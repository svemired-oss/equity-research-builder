"""Assemble a GD run only when its independent sources cover the model."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, timedelta

from .gd_evidence import GDHistoryEvidence, _validate_filing_reference
from .gd_history_checks import HistoricalCheck, reconcile_gd_history
from .gd_inputs import (
    ANNUAL_ASSUMPTION_ROWS,
    DATED_ASSUMPTION_PERIODS,
    GUIDED_SCALARS,
    SELECTORS,
    SOURCE_KINDS,
    Cell,
    GDInputs,
    InputSource,
    _cell,
    _choice,
    _number,
    _source,
    _source_json,
    cell_address,
)
from .gd_projection import run_projection
from .gd_run_inputs import ForecastAssumptions, MarketInputs, _public_url

# The standalone model checks reported totals in each year and uses the latest
# history to open the forecast. Verify this roster against the model's read trace.
_EVERY_HISTORICAL_YEAR = {
    "Balance sheet": (9, 11, 16, 22, 26, 27, 33, 45, 47, 68, 77),
    "Equity": (52,),
    "Income statement": (9, 17),
    "Revenue model": (60, 61, 62, 63, 69, 70, 71, 72, 73),
    "Schedules": (50, 90, 120),
}
_LATEST_HISTORICAL_YEAR = {
    "Balance sheet": (8, 10, 12, 17, 18, 19, 28, 29, 34, 40, 41, 44),
    "Equity": (13, 20, 21, 22, 32, 41),
    "Income statement": (7, 8, 12, 13, 14, 30),
    "Revenue model": (9, 13, 15, 23, 24, 25, 36, 37, 38, 49, 50),
    "Schedules": (33, 42, 75, 93),
}
_MARKET_CELL = "Equity!F20"
_SHARE_COUNT_CELL = "Equity!F21"
_HISTORICAL_SOURCE_KINDS = {"sec_filing", "issuer_release", "derived"}
_FORECAST_SOURCE_KINDS = SOURCE_KINDS - {"archived_workbook"}


@dataclass(frozen=True)
class PreparationResult:
    inputs: GDInputs | None
    missing: dict[str, str]

    @property
    def ready(self) -> bool:
        return self.inputs is not None


@dataclass(frozen=True)
class GDHistoryAudit:
    as_of: date
    historical_years: tuple[int, ...]
    required_filing_inputs: int
    sourced_filing_inputs: int
    year_end_market_close_sourced: bool
    checks: tuple[HistoricalCheck, ...]
    missing: dict[str, str]
    history_ready: bool


def required_gd_history_cells() -> frozenset[str]:
    """List the model's historical cells, including the separately sourced price."""
    return frozenset(
        f"{sheet}!{column}{row}"
        for groups, columns in ((_EVERY_HISTORICAL_YEAR, "BCDEF"), (_LATEST_HISTORICAL_YEAR, "F"))
        for sheet, rows in groups.items()
        for row in rows
        for column in columns
    )


def _required_forecast_cells() -> set[Cell]:
    annual = {(sheet, row, period) for sheet, rows in ANNUAL_ASSUMPTION_ROWS.items()
              for row in rows for period in range(6)}
    dated = {(sheet, row, period) for (sheet, row), periods in DATED_ASSUMPTION_PERIODS.items()
             for period in periods}
    return annual | dated


def _checked_source(
    source: InputSource | None, key: str, as_of: date, allowed: set[str],
) -> InputSource:
    if not isinstance(source, InputSource):
        raise ValueError(f"No dated source is available for {key}.")
    if source.kind not in allowed:
        raise ValueError(f"Source kind {source.kind!r} cannot supply {key} in a fresh run.")
    return _source(_source_json(source), f"source for {key}", as_of)


def _add_history(
    evidence: GDHistoryEvidence, missing: dict[str, str], provenance: dict[str, InputSource],
) -> dict[str, float]:
    history = {}
    for key, value in evidence.history.items():
        if key == _MARKET_CELL:
            continue
        try:
            if key in evidence.missing:
                raise ValueError(evidence.missing[key])
            _, _, period = _cell(key, forecast=False)
            source = _checked_source(evidence.provenance.get(key), key, evidence.as_of, _HISTORICAL_SOURCE_KINDS)
            if source.available_on < date(evidence.as_of.year + period, 12, 31):
                raise ValueError(f"The source for {key} predates its fiscal year-end.")
            history[key] = _number(value, key)
            provenance[key] = source
        except ValueError as exc:
            missing[key] = str(exc)
    return history


def _add_market_close(
    market: MarketInputs, year_end: date, history: dict[str, float],
    missing: dict[str, str], provenance: dict[str, InputSource],
) -> None:
    try:
        close = _number(market.year_end_close, _MARKET_CELL)
        source = _checked_source(market.source, _MARKET_CELL, market.as_of, {"market_data"})
        observed = market.year_end_close_date
        if close <= 0:
            raise ValueError("The year-end closing share price must be positive.")
        if not _public_url(source.url):
            raise ValueError("The market source must have a public HTTP(S) URL.")
        if not year_end - timedelta(days=7) <= observed <= year_end or observed.weekday() >= 5:
            raise ValueError("Use a trading day in the seven days through the latest fiscal year-end.")
        if observed > source.available_on:
            raise ValueError("The market source date precedes the price observation.")
        history[_MARKET_CELL] = close
        provenance[_MARKET_CELL] = replace(
            source, citation=f"{source.citation} Unadjusted closing price observed {observed.isoformat()}.",
        )
    except ValueError as exc:
        missing[_MARKET_CELL] = str(exc)


def _validate_history(
    evidence: GDHistoryEvidence, market: MarketInputs, missing: dict[str, str], provenance: dict[str, InputSource],
    *, audit_annual_cash: bool = False,
) -> tuple[dict[str, float], tuple[HistoricalCheck, ...]]:
    history = _add_history(evidence, missing, provenance)
    _add_market_close(market, date(evidence.as_of.year - 1, 12, 31), history, missing, provenance)
    for key in sorted(required_gd_history_cells() - history.keys()):
        missing.setdefault(key, evidence.missing.get(key, "No source covers this required GD historical input."))
    shares = history.get(_SHARE_COUNT_CELL)
    if shares is not None and shares <= 0:
        missing[_SHARE_COUNT_CELL] = "Year-end shares outstanding must be positive."

    annual_cash = evidence.cash_flow_actuals
    if audit_annual_cash and annual_cash is None:
        annual_cash = {}
    checks = reconcile_gd_history(history, evidence.historical_years, cash_flow_actuals=annual_cash)
    for check in checks:
        if check.status == "failed":
            missing[f"FY{check.year} {check.name}"] = f"Sourced values differ by {check.difference_millions:g} USD millions."
        elif audit_annual_cash and check.status == "unavailable":
            missing[f"FY{check.year} {check.name}"] = f"Missing check inputs: {', '.join(check.missing)}."
    return history, checks


def audit_gd_history(evidence: GDHistoryEvidence, market: MarketInputs) -> GDHistoryAudit:
    """Verify the filing-derived inputs and market close separately from valuation readiness."""
    if evidence.as_of != market.as_of:
        raise ValueError("Market inputs must use the historical evidence cutoff.")
    as_of = evidence.as_of
    if as_of.year < 6 or evidence.historical_years != tuple(range(as_of.year - 5, as_of.year)):
        raise ValueError("Supply the five fiscal years ending before the forecast start year.")
    missing: dict[str, str] = {}
    history, checks = _validate_history(evidence, market, missing, {}, audit_annual_cash=True)
    required_filing = required_gd_history_cells() - {_MARKET_CELL}
    sourced_filing = (required_filing & history.keys()) - missing.keys()
    return GDHistoryAudit(
        as_of, evidence.historical_years, len(required_filing), len(sourced_filing),
        _MARKET_CELL in history, checks, missing, not missing,
    )


def _check_forecast_inputs(
    assumptions: ForecastAssumptions, missing: dict[str, str], provenance: dict[str, InputSource],
) -> None:
    expected = _required_forecast_cells()
    sources = set(SELECTORS) | GUIDED_SCALARS
    for cell in expected - assumptions.assumptions.keys():
        missing[cell_address(cell)] = "Supply an explicit forecast assumption."
    for cell, value in assumptions.assumptions.items():
        try:
            key = cell_address(cell)
        except ValueError as exc:
            missing[f"forecast assumption {cell!r}"] = str(exc)
            continue
        if cell not in expected:
            missing[key] = "This cell is outside the GD forecast assumption roster."
            continue
        sources.add(key)
        try:
            _number(value, key)
        except ValueError as exc:
            missing[key] = str(exc)

    try:
        default = _checked_source(assumptions.source, "forecast assumptions", assumptions.as_of, {"model_assumption"})
    except ValueError as exc:
        missing["assumptions.source"] = str(exc)
    else:
        provenance.update(dict.fromkeys(sources - assumptions.provenance.keys(), default))
    for key, source in assumptions.provenance.items():
        if key not in sources:
            missing[key] = "This source does not correspond to a supplied forecast input."
            continue
        try:
            provenance[key] = _checked_source(source, key, assumptions.as_of, _FORECAST_SOURCE_KINDS)
        except ValueError as exc:
            missing[key] = str(exc)
    for key, row in SELECTORS.items():
        try:
            _choice(assumptions.scenarios.get(row), key)
        except ValueError as exc:
            missing[key] = str(exc)
    for row in assumptions.scenarios.keys() - SELECTORS.values():
        missing[f"scenario row {row}"] = "This row is outside the six GD scenario selectors."

    numeric = {}
    for key in GUIDED_SCALARS:
        try:
            numeric[key] = _number(getattr(assumptions, key), key)
        except ValueError as exc:
            missing[key] = str(exc)
    if numeric.keys() == GUIDED_SCALARS:
        if numeric["cost_of_equity"] <= 0:
            missing["cost_of_equity"] = "Cost of equity must be positive."
        if not -1 < numeric["terminal_growth"] < numeric["cost_of_equity"]:
            missing["terminal_growth"] = "Terminal growth must exceed -100% and stay below cost of equity."
        if not 0 <= numeric["dividend_payout_ratio"] <= 1:
            missing["dividend_payout_ratio"] = "Dividend payout must be between 0% and 100%."


def prepare_gd_inputs(
    evidence: GDHistoryEvidence, assumptions: ForecastAssumptions, market: MarketInputs,
) -> PreparationResult:
    """Return a complete sourced GD run or the gaps that prevent its construction."""
    as_of = evidence.as_of
    missing: dict[str, str] = {}
    if assumptions.as_of != as_of:
        missing["assumptions.as_of"] = "Forecast assumptions must use the historical evidence cutoff."
    if market.as_of != as_of:
        missing["market.as_of"] = "Market inputs must use the historical evidence cutoff."
    expected_years = tuple(range(as_of.year - 5, as_of.year))
    if not 6 <= as_of.year <= 9994 or evidence.historical_years != expected_years:
        missing["historical_years"] = "Supply the five fiscal years ending before the forecast start year."
    if missing:
        return PreparationResult(None, missing)

    interim = evidence.latest_interim
    if interim is not None:
        try:
            _validate_filing_reference(interim, as_of)
            if interim.form != "10-Q":
                raise ValueError("Latest interim evidence must identify a 10-Q.")
        except ValueError as exc:
            missing["latest_interim"] = str(exc)
        else:
            if interim.report_date.year == as_of.year:
                missing["latest_interim"] = (
                    f"The {interim.report_date.isoformat()} interim period was reported on "
                    f"{interim.filing_date.isoformat()}. A fresh valuation requires an interim "
                    "roll-forward and an explicit forecast for the remaining period."
                )

    provenance: dict[str, InputSource] = {}
    history, _ = _validate_history(evidence, market, missing, provenance)
    _check_forecast_inputs(assumptions, missing, provenance)
    if missing:
        return PreparationResult(None, missing)

    provenance["opening_shares_millions"] = provenance[_SHARE_COUNT_CELL]
    inputs = GDInputs(
        history={_cell(key, forecast=False): value for key, value in history.items()},
        assumptions=assumptions.assumptions.copy(),
        scenarios=assumptions.scenarios.copy(),
        cost_of_equity=assumptions.cost_of_equity,
        dividend_payout_ratio=assumptions.dividend_payout_ratio,
        as_of=as_of,
        forecast_ends=tuple(date(year, 12, 31) for year in range(as_of.year, as_of.year + 6)),
        twelve_month_date=as_of + timedelta(days=365),
        terminal_growth=assumptions.terminal_growth,
        opening_shares_millions=history[_SHARE_COUNT_CELL],
        source=InputSource("derived", "GD inputs assembled from dated history, a year-end share price, and forecast assumptions.", as_of),
        provenance=provenance,
    )
    try:
        run_projection(inputs)
    except (ValueError, ArithmeticError) as exc:
        return PreparationResult(None, {"projection": str(exc)})
    return PreparationResult(inputs, {})

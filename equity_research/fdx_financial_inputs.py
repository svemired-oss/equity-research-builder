"""Read and validate the private FedEx archive's May fiscal-year financial inputs."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
from io import BytesIO
from pathlib import Path

from openpyxl import load_workbook

from .fdx_replay import BASELINE_SHA256, SERVICE_ROWS, _compare, _literal_terms, _number
from .fdx_revenue import (
    LEGACY_FISCAL_END,
    RevenueAssumptions,
    RevenueForecast,
    ServiceAssumptions,
    forecast_archived_fdx_revenue,
)


INCOME_ROWS = (5, *range(7, 18), *range(19, 31), *range(32, 37))
BALANCE_ROWS = (
    *range(6, 11), *range(12, 20), *range(21, 26),
    *range(28, 35), *range(36, 42), *range(43, 50),
)
CAPEX_ROWS = (*range(10, 16), *range(18, 22))
SELECTOR_ROWS = {105: 11, 110: 12, 117: 13, 121: 14}
FIRST_YEAR_AVERAGES = {128: (62, "DEFGH"), 129: (63, "DEFGH"), 133: (67, "CDEFGH")}


@dataclass(frozen=True)
class FDXHistoricalPeriod:
    """Archive statement values keyed by original sheet row, in USD millions except EPS."""

    fiscal_end: date
    statement_column: str
    revenue_column: str
    income_statement: Mapping[int, float]
    balance_sheet: Mapping[int, float]
    capex: Mapping[int, float] | None
    segment_revenue_millions: Mapping[str, float]
    driver_revenue_millions: float


@dataclass(frozen=True)
class FDXRevenuePeriod:
    """Independent calculations for both archived views of legacy revenue."""

    fiscal_end: date
    source_column: str
    assumptions: RevenueAssumptions
    reported_revenue: RevenueForecast
    express_segment_revenue_millions: float
    segment_revenue_millions: float
    ground_segment_days: int
    other_revenue_growth: float | None

    @property
    def reported_revenue_millions(self) -> float:
        return self.reported_revenue.total_revenue_millions

    @property
    def segment_gap_millions(self) -> float:
        return self.segment_revenue_millions - self.reported_revenue_millions


@dataclass(frozen=True)
class FDXFinancialInputs:
    workbook_sha256: str
    historical: tuple[FDXHistoricalPeriod, ...]
    revenue_path: tuple[FDXRevenuePeriod, ...]
    cells_matched: int
    known_issues: tuple[str, ...]


def _read_cells(contents: bytes, *, data_only: bool) -> dict[str, object]:
    ranges = {"Revenue": "C1:N154", "Cover": "I11:I14", "DCF": "C14:C14"}
    if data_only:
        ranges.update({
            "Income statement": "B4:L36",
            "Balance sheet": "B3:F49",
            "CapEx": "B8:D21",
        })
    workbook = load_workbook(
        BytesIO(contents), data_only=data_only, read_only=True, keep_links=False,
    )
    try:
        return {
            f"{sheet}!{cell.coordinate}": cell.value
            for sheet, address in ranges.items()
            for row in workbook[sheet][address]
            for cell in row
            if cell.value is not None
        }
    finally:
        workbook.close()


def _historical_periods(saved: dict[str, object]) -> tuple[FDXHistoricalPeriod, ...]:
    periods = []
    for year, statement, revenue, capex in zip(
        range(2021, 2026), "BCDEF", "CDEFG", (None, None, "B", "C", "D"), strict=True,
    ):
        for address in (
            f"Income statement!{statement}4", f"Balance sheet!{statement}3", f"Revenue!{revenue}1",
        ):
            if saved.get(address) != year:
                raise ValueError(f"Expected FedEx FY{year} in {address}.")
        income = {
            row: _number(saved, f"Income statement!{statement}{row}")
            for row in INCOME_ROWS if row != 33 or f"Income statement!{statement}{row}" in saved
        }
        balance = {row: _number(saved, f"Balance sheet!{statement}{row}") for row in BALANCE_ROWS}
        if capex is None:
            capital_spending = None
        else:
            if saved.get(f"CapEx!{capex}8") != f"FY {year}":
                raise ValueError(f"Expected FedEx FY{year} in CapEx!{capex}8.")
            capital_spending = {row: _number(saved, f"CapEx!{capex}{row}") for row in CAPEX_ROWS}
            _compare(
                f"FY{year} CapEx total",
                sum(capital_spending[row] for row in range(10, 15)), capital_spending[15],
            )
            _compare(
                f"FY{year} CapEx by segment",
                sum(capital_spending[row] for row in range(18, 21)), capital_spending[21],
            )
            _compare(f"FY{year} CapEx reconciliation", capital_spending[15], capital_spending[21])
        segments = {
            "federal_express": _number(saved, f"Revenue!{revenue}4"),
            "fedex_freight": _number(saved, f"Revenue!{revenue}5"),
            "other_and_eliminations": _number(saved, f"Revenue!{revenue}6"),
        }
        for row in (7, 36):
            _compare(f"Revenue!{revenue}{row}", _number(saved, f"Revenue!{revenue}{row}"), income[5])
        _compare(f"FY{year} segment revenue", sum(segments.values()), income[5])
        _compare(f"FY{year} operating expenses", sum(income[row] for row in range(7, 16)), income[16])
        _compare(f"FY{year} operating income", income[5] - income[16], income[17])
        _compare(f"FY{year} net income", income[25] - income[26], income[27])
        _compare(f"FY{year} balance sheet", balance[25], balance[49])
        periods.append(FDXHistoricalPeriod(
            date(year, 5, 31), statement, revenue, income, balance, capital_spending, segments,
            _number(saved, f"Revenue!{revenue}89"),
        ))
    return tuple(periods)


def _initial_service_levels(source: dict[str, object]) -> dict[str, tuple[float, float]]:
    return {
        service: (
            sum(_literal_terms(source, f"Revenue!G{volume}", r"=(\d+)\+(\d+)"))
            if volume in (54, 70) else _number(source, f"Revenue!G{volume}"),
            _number(source, f"Revenue!G{yield_}"),
        )
        for service, (volume, yield_, _, _, _) in SERVICE_ROWS.items()
    }


def _growth(
    source: dict[str, object],
    saved: dict[str, object],
    column: str,
    row: int,
) -> float:
    if row in SELECTOR_ROWS:
        address = f"Cover!I{SELECTOR_ROWS[row]}"
        selector = _number(source, address)
        if selector not in (1, 2, 3):
            raise ValueError(f"Invalid scenario selector in {address}.")
        return _number(source, f"Revenue!{column}{row + int(selector)}")
    if column == "J" and row in FIRST_YEAR_AVERAGES:
        driver_row, historical_columns = FIRST_YEAR_AVERAGES[row]
        levels = [_number(source, f"Revenue!{prior}{driver_row}") for prior in historical_columns]
        rates = []
        for prior, level, growth_column in zip(levels[:-1], levels[1:], historical_columns[1:], strict=True):
            rate = (level - prior) / prior
            address = f"Revenue!{growth_column}{row}"
            _compare(address, rate, _number(saved, address))
            rates.append(rate)
        return sum(rates) / len(rates)
    return _number(source, f"Revenue!{column}{row}")


def _revenue_path(
    source: dict[str, object],
    saved: dict[str, object],
) -> tuple[tuple[FDXRevenuePeriod, ...], int]:
    anchor = source.get("DCF!C14")
    if not isinstance(anchor, datetime) or anchor.date() != LEGACY_FISCAL_END:
        raise ValueError("DCF!C14 disagrees with the May FY2026 FedEx archive.")
    prior_levels = _initial_service_levels(source)
    quarterly_other, quarters = _literal_terms(source, "Revenue!J88", r"=(\d+)\*(\d+)")
    other = float(quarterly_other * quarters)
    periods = []
    compared = 0
    for year, column, income_column in zip(range(2026, 2031), "JKLMN", "HIJKL", strict=True):
        label = f"{year}E"
        if (
            source.get(f"Revenue!{column}1") != label
            or saved.get(f"Income statement!{income_column}4") != label
        ):
            raise ValueError(f"FedEx archive forecast columns disagree with FY{year}.")
        days = _number(source, f"Revenue!{column}43")
        if not days.is_integer():
            raise ValueError(f"Expected whole operating days in Revenue!{column}43.")
        ground_days, = _literal_terms(
            source, f"Revenue!{column}14", rf"={column}56\*(\d+)/1000",
        )
        other_growth = None
        if column != "J":
            other_growth = _number(source, f"Revenue!{column}154")
            other *= 1 + other_growth
        services = {
            name: ServiceAssumptions(
                *prior_levels[name],
                _growth(source, saved, column, volume_growth),
                _growth(source, saved, column, yield_growth),
            )
            for name, (_, _, _, volume_growth, yield_growth) in SERVICE_ROWS.items()
        }
        assumptions = RevenueAssumptions(
            date(year, 5, 31), int(days), services, _number(source, f"Revenue!{column}74"), other,
        )
        forecast = forecast_archived_fdx_revenue(assumptions)
        ground = forecast.services["us_ground"]
        ground_segment_revenue = ground.daily_revenue_thousands * ground_days / 1000
        ground_gap = ground_segment_revenue - ground.revenue_millions
        express_segment = forecast.express_revenue_millions + ground_gap
        segment_total = express_segment + forecast.freight_segment_revenue_millions + other
        modeled = {
            4: express_segment, 5: forecast.freight_segment_revenue_millions,
            6: other, 7: segment_total, 14: ground_segment_revenue,
            28: assumptions.express_other_millions, 30: express_segment,
            32: forecast.freight_segment_revenue_millions, 34: other,
            36: forecast.total_revenue_millions, 86: forecast.daily_revenue_thousands,
            87: forecast.annualized_service_revenue_millions, 88: other,
            89: forecast.total_revenue_millions,
        }
        for service, (volume, yield_, daily, volume_growth, yield_growth) in SERVICE_ROWS.items():
            line = forecast.services[service]
            modeled[volume] = line.daily_volume_thousands
            modeled[yield_] = line.yield_usd
            modeled[daily] = line.daily_revenue_thousands
            modeled[volume_growth] = services[service].volume_growth
            modeled[yield_growth] = services[service].yield_growth
        for row, value in modeled.items():
            address = f"Revenue!{column}{row}"
            _compare(address, value, _number(saved, address))
        income_address = f"Income statement!{income_column}5"
        _compare(income_address, forecast.total_revenue_millions, _number(saved, income_address))
        compared += len(modeled) + 1
        periods.append(FDXRevenuePeriod(
            assumptions.period_end, column, assumptions, forecast, express_segment,
            segment_total, ground_days, other_growth,
        ))
        prior_levels = {
            name: (line.daily_volume_thousands, line.yield_usd)
            for name, line in forecast.services.items()
        }
    return tuple(periods), compared


def load_fdx_financial_inputs(workbook: bytes | Path | str) -> FDXFinancialInputs:
    """Load the fingerprinted archive, then reconcile history and the five-year revenue path."""
    contents = workbook if isinstance(workbook, bytes) else Path(workbook).read_bytes()
    digest = sha256(contents).hexdigest()
    if digest != BASELINE_SHA256:
        raise ValueError(
            "The workbook does not match the private FedEx portfolio copy "
            f"(SHA-256 {digest}). Use the original portfolio workbook for this replay."
        )
    saved = _read_cells(contents, data_only=True)
    source = _read_cells(contents, data_only=False)
    historical = _historical_periods(saved)
    revenue_path, compared = _revenue_path(source, saved)
    return FDXFinancialInputs(
        digest, historical, revenue_path, compared,
        (
            "FY2021–22 Federal Express segment history aggregates the predecessor Express and Ground segments.",
            "Revenue!C89:G89 is a volume-and-yield estimate. Revenue!C36:G36 reconciles to historical reported sales.",
            "Income statement!G7 links full-year FY2026 salaries into the Q1 FY2026 column and distorts G16.",
            "Balance sheet!E20 is a change in accumulated depreciation excluded from historical asset totals.",
            "Revenue!J128, J129 and J133 average historical growth including Q1 FY2026 versus full-year FY2025.",
            "Revenue!J88 annualizes the workbook's Q1 FY2026 Other and Eliminations figure across four quarters.",
            "The segment view annualizes Ground using 260 days. Consolidated revenue uses 256 days across all services.",
            "FY2027–30 reflect an archived legacy-basis counterfactual including FedEx Freight.",
        ),
    )

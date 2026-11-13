"""Estimate GD's remaining operating cash flow and capex from dated guidance."""

from dataclasses import dataclass, replace
from datetime import date
from math import isclose
from pathlib import Path
from statistics import mean

from .gd_cash_bridge import CitedAmount, GDCashInputs, GDCashWindow
from .gd_inputs import InputSource, _fields, _number, _object, _read, _source, _text
from .gd_interim import GDInterimEvidence, _range
from .gd_run_inputs import _header
from .reconciliation import TOLERANCE_MILLIONS


@dataclass(frozen=True)
class GDCashTiming:
    operating_cash_share_shift: float
    capex_share_shift: float
    rationale: str


@dataclass(frozen=True)
class GDCashOutlook:
    as_of: date
    capex_share_of_sales: tuple[float, float]
    fcf_conversion: float
    source: InputSource
    timing_cases: dict[str, GDCashTiming]


@dataclass(frozen=True)
class CashEstimate:
    cash_from_operations_millions: float
    capital_expenditures_millions: float
    cfo_less_capex_millions: float


@dataclass(frozen=True)
class GDCashCase:
    annual_revenue_millions: float
    annual_diluted_eps: float
    annual_net_earnings_millions: float
    annual: CashEstimate
    q4: CashEstimate


@dataclass(frozen=True)
class GDCashTimingResult:
    operating_cash_share_after_cutoff: float
    capex_share_after_cutoff: float
    rationale: str
    elapsed: CashEstimate
    remaining: CashEstimate
    cash_inputs: GDCashInputs


@dataclass(frozen=True)
class GDQ4CashForecast:
    as_of: date
    interim_period_end: date
    full_quarter_days: int
    days_after_cutoff: int
    diluted_shares_proxy_millions: float
    outlook: GDCashOutlook
    fiscal_cases: dict[str, GDCashCase]
    timing_cases: dict[str, GDCashTimingResult]
    notes: tuple[str, ...]


def load_gd_cash_outlook(path: Path) -> GDCashOutlook:
    """Load documented cash guidance and explicit timing sensitivities."""
    item = _fields(_read(path), "GD cash outlook", {
        "schema_version", "ticker", "currency", "as_of", "capex_share_of_sales",
        "fcf_conversion", "source", "timing_cases",
    })
    as_of = _header(item)
    capex = _range(item["capex_share_of_sales"], "FY capex share of sales")
    conversion = _number(item["fcf_conversion"], "FY free cash flow conversion")
    source = _source(item["source"], "GD cash outlook", as_of)
    if not 0 < capex[0] <= capex[1] <= 1 or conversion <= 0:
        raise ValueError("Cash guidance needs positive conversion and capex as a fraction of revenue.")
    if source.kind != "issuer_call":
        raise ValueError("Cash outlook needs a dated issuer_call source.")

    timing = {}
    for name, raw in _object(item["timing_cases"], "timing cases").items():
        label = _text(name, "timing case name")
        if label != name:
            raise ValueError("Timing case names cannot include leading or trailing spaces.")
        case = _fields(raw, label, {"operating_cash_share_shift", "capex_share_shift", "rationale"})
        timing[label] = GDCashTiming(
            _number(case["operating_cash_share_shift"], f"{label} operating cash shift"),
            _number(case["capex_share_shift"], f"{label} capex shift"),
            _text(case["rationale"], f"{label} rationale"),
        )
    if "base" not in timing:
        raise ValueError("Supply a base timing case.")
    return GDCashOutlook(as_of, capex, conversion, source, timing)


def _cash_estimate(operating_cash: float, capex: float) -> CashEstimate:
    return CashEstimate(operating_cash, capex, _number(operating_cash - capex, "operating cash less capex"))


def _forecast_case(
    revenue: float, eps: float, capex_share: float, shares: float, conversion: float, ytd: dict[str, float],
) -> GDCashCase:
    net_earnings = _number(eps * shares, "FY net earnings estimate")
    capex = revenue * capex_share
    fcf = _number(net_earnings * conversion, "FY issuer free cash flow estimate")
    operating_cash = _number(capex + fcf, "FY operating cash estimate")
    q4_capex = capex - ytd["capital_expenditures"]
    if q4_capex < 0:
        raise ValueError("Estimated full-year cash capex cannot be below the reported nine-month amount.")
    return GDCashCase(
        revenue, eps, net_earnings, _cash_estimate(operating_cash, capex),
        _cash_estimate(operating_cash - ytd["cash_from_operations"], q4_capex),
    )


def _timing_case(
    interim: GDInterimEvidence, outlook: GDCashOutlook, timing: GDCashTiming,
    q4: CashEstimate, year_end: date, calendar_share: float,
) -> GDCashTimingResult:
    cfo_share = calendar_share + timing.operating_cash_share_shift
    capex_share = calendar_share + timing.capex_share_shift
    if not 0 <= cfo_share <= 1 or not 0 <= capex_share <= 1:
        raise ValueError("Timing case must allocate operating cash and capex shares between zero and one.")
    remaining = _cash_estimate(q4.cash_from_operations_millions * cfo_share, q4.capital_expenditures_millions * capex_share)
    elapsed = _cash_estimate(
        q4.cash_from_operations_millions - remaining.cash_from_operations_millions,
        q4.capital_expenditures_millions - remaining.capital_expenditures_millions,
    )

    def window(start: date, end: date, amounts: CashEstimate, cfo_fraction: float, capex_fraction: float) -> GDCashWindow:
        source = InputSource(
            "model_assumption",
            f"FY guidance midpoint case less nine-month actuals, allocated to this window. {timing.rationale} "
            f"The allocation uses {cfo_fraction:.2%} of Q4 operating cash and {capex_fraction:.2%} of Q4 cash capex. "
            f"Uses the cash outlook from the {outlook.source.available_on.isoformat()} call and actuals from {interim.filing.url}.",
            interim.as_of, url=outlook.source.url,
        )
        return GDCashWindow(start, end, {
            "cash_from_operations_millions": CitedAmount(amounts.cash_from_operations_millions, source),
            "capital_expenditures_millions": CitedAmount(amounts.capital_expenditures_millions, source),
        })

    cash_inputs = GDCashInputs(
        interim.as_of,
        window(interim.period_end, interim.as_of, elapsed, 1 - cfo_share, 1 - capex_share),
        window(interim.as_of, year_end, remaining, cfo_share, capex_share),
    )
    return GDCashTimingResult(cfo_share, capex_share, timing.rationale, elapsed, remaining, cash_inputs)


def forecast_gd_q4_cash(interim: GDInterimEvidence, outlook: GDCashOutlook) -> GDQ4CashForecast:
    """Keep the full-quarter estimates and their uncertain intra-quarter timing separate."""
    if interim.as_of != outlook.as_of or not interim.period_end <= outlook.source.available_on <= interim.as_of:
        raise ValueError("The cash outlook must follow the reported quarter and use the same cutoff.")
    shares = interim.ytd_diluted_shares_millions
    if shares is None or interim.guidance.diluted_eps[0] <= 0:
        raise ValueError("Cash estimation requires filed nine-month diluted shares and positive full-year EPS guidance.")
    ytd = interim.ytd
    ytd_cfo_less_capex = ytd["cash_from_operations"] - ytd["capital_expenditures"]
    if not isclose(ytd_cfo_less_capex, ytd["issuer_free_cash_flow"], rel_tol=0, abs_tol=TOLERANCE_MILLIONS):
        raise ValueError("Reported nine-month operating cash less capex must reconcile to issuer free cash flow.")
    year_end = date(interim.as_of.year, 12, 31)
    quarter_days = (year_end - interim.period_end).days
    remaining_days = (year_end - interim.as_of).days
    if not 0 < remaining_days < quarter_days:
        raise ValueError("The cutoff must fall inside the fourth quarter.")

    inputs = (
        interim.guidance.revenue, interim.guidance.diluted_eps, outlook.capex_share_of_sales,
    )
    fiscal_cases = {
        "low": _forecast_case(*(bounds[0] for bounds in inputs), shares, outlook.fcf_conversion, ytd),
        "base": _forecast_case(*(mean(bounds) for bounds in inputs), shares, outlook.fcf_conversion, ytd),
        "high": _forecast_case(*(bounds[1] for bounds in inputs), shares, outlook.fcf_conversion, ytd),
    }
    timing_cases = {
        name: _timing_case(interim, outlook, timing, fiscal_cases["base"].q4, year_end, remaining_days / quarter_days)
        for name, timing in outlook.timing_cases.items()
    }
    return GDQ4CashForecast(
        interim.as_of, interim.period_end, quarter_days, remaining_days, shares, outlook, fiscal_cases, timing_cases,
        (
            "FY diluted EPS guidance multiplied by reported nine-month diluted weighted-average shares estimates FY net earnings. "
            "The full-year diluted share count remains an estimate.",
            "The fiscal cases pair lower, midpoint or upper revenue, EPS and capex guidance values. All hold approximate FCF conversion fixed.",
            "Issuer free cash flow is operating cash flow less cash capex. Add estimated capex to estimated issuer FCF for CFO, "
            "then subtract the respective reported nine-month amounts. Full-quarter FCF conversion can differ from the full-year target.",
            "Timing cases use the same base Q4 totals. The calendar allocation and timing shifts are analyst scenarios. "
            "No intra-quarter cash settlement data is assumed.",
            "Debt, other FCFE adjustments, shareholder distributions and share movements require separate inputs. "
            "The modeled operating cash less capex cannot be used as the DCF cash flow to equity.",
        ),
    )


def cash_inputs_for_fiscal_case(forecast: GDQ4CashForecast, fiscal_case: str) -> GDCashInputs:
    """Use a common elapsed cash estimate and allocate the selected case's balance to the future."""
    if fiscal_case not in forecast.fiscal_cases:
        raise ValueError(f"Unknown GD fiscal cash case {fiscal_case!r}.")
    base = forecast.timing_cases["base"].cash_inputs
    if fiscal_case == "base":
        return base
    quarter = forecast.fiscal_cases[fiscal_case].q4
    amounts = {
        "cash_from_operations_millions": quarter.cash_from_operations_millions,
        "capital_expenditures_millions": quarter.capital_expenditures_millions,
    }
    source = InputSource(
        "model_assumption",
        f"Use the {fiscal_case} FY guidance pairing less nine-month actuals and the common elapsed Q4 cash estimate. "
        "Allocate the residual operating cash and capex to the period after the valuation cutoff.",
        forecast.as_of, url=forecast.outlook.source.url,
    )
    remaining = {key: CitedAmount(total - base.elapsed.amounts[key].amount, source) for key, total in amounts.items()}
    if remaining["capital_expenditures_millions"].amount < 0:
        raise ValueError("Fiscal case capex cannot be below the common elapsed Q4 capex estimate.")
    return replace(base, remaining=replace(base.remaining, amounts=remaining))

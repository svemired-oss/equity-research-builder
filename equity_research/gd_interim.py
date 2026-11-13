"""Validate sourced nine-month GD evidence and bridge full-year guidance to Q4."""

from dataclasses import dataclass
from datetime import date
from math import isclose
from pathlib import Path
from urllib.parse import urlsplit

from .gd_cash_bridge import FCFE_FIELDS, GDCashBalances, GDCashBridge, GDCashInputs, build_gd_cash_bridge
from .gd_evidence import GDHistoryEvidence
from .gd_fresh import _required_forecast_cells, audit_gd_history, required_gd_history_cells
from .gd_inputs import InputSource, _day, _fields, _number, _read, _source
from .gd_outlook import SEGMENTS
from .gd_run_inputs import MarketInputs, _header
from .reconciliation import TOLERANCE_MILLIONS


YTD_FIELDS = {
    "revenue", "operating_earnings", "net_earnings", "diluted_eps", "net_interest_expense",
    "cash_from_operations", "capital_expenditures", "issuer_free_cash_flow", "deliveries",
    "corporate_operating_costs",
}
BALANCE_FIELDS = {"cash", "total_debt", "shares_outstanding", "backlog", "funded_backlog", "unfunded_backlog"}
SEGMENT_FIELDS = {"revenue", "operating_earnings", "backlog"}


@dataclass(frozen=True)
class GDGuidance:
    revenue: tuple[float, float]
    operating_margin: float
    net_interest_expense: float
    diluted_eps: tuple[float, float]


@dataclass(frozen=True)
class GDScheduledDividend:
    record_date: date
    payment_date: date
    usd_per_share: float
    period_end_dividends_payable_millions: float
    declaration: InputSource


@dataclass(frozen=True)
class GDInterimEvidence:
    as_of: date
    period_end: date
    filing: InputSource
    release: InputSource
    ytd: dict[str, float]
    period_end_balances: dict[str, float]
    segments: dict[str, dict[str, float]]
    guidance: GDGuidance
    scheduled_dividend: GDScheduledDividend | None = None
    ytd_diluted_shares_millions: float | None = None


@dataclass(frozen=True)
class GDQ4Bridge:
    revenue_range_millions: tuple[float, float]
    operating_earnings_at_guided_margin_millions: tuple[float, float]
    net_interest_at_guidance_millions: float
    full_quarter_days: int
    days_after_cutoff: int
    method: str


@dataclass(frozen=True)
class GDRefreshReadiness:
    as_of: date
    interim: GDInterimEvidence
    market: MarketInputs
    q4_bridge: GDQ4Bridge
    reconciliation_checks: tuple[str, ...]
    net_debt_millions: float
    annual_history_inputs_required: int
    forecast_inputs_required: int
    blockers: tuple[str, ...]
    cash_bridge: GDCashBridge
    valuation_ready: bool = False
    annual_history_ready: bool = False


def _amounts(value: object, label: str, keys: set[str]) -> dict[str, float]:
    fields = _fields(value, label, keys)
    return {key: _number(amount, f"{label}.{key}") for key, amount in fields.items()}


def _range(value: object, label: str) -> tuple[float, float]:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(f"{label} must have a low and high estimate.")
    low, high = (_number(item, label) for item in value)
    if low > high:
        raise ValueError(f"{label} must be in ascending order.")
    return low, high


def load_gd_interim(path: Path) -> GDInterimEvidence:
    """Read a cited Q3 packet without substituting archival model values."""
    item = _fields(_read(path), "GD interim evidence", {
        "schema_version", "ticker", "currency", "as_of", "period_end", "sources",
        "ytd", "period_end_balances", "segments", "guidance",
    }, {"scheduled_dividend", "ytd_diluted_shares_millions"})
    as_of = _header(item)
    period_end = _day(item["period_end"], "interim period end")
    if period_end.year != as_of.year or period_end.month not in (9, 10) or period_end > as_of:
        raise ValueError("Use a current-year Q3 period ending in September or October by the cutoff.")
    sources = _fields(item["sources"], "interim sources", {"filing", "release"})
    filing = _source(sources["filing"], "interim filing", as_of)
    release = _source(sources["release"], "interim earnings release", as_of)
    for source, kind in ((filing, "sec_filing"), (release, "issuer_release")):
        url = urlsplit(source.url or "")
        if (source.kind != kind or source.available_on < period_end or url.scheme != "https"
                or url.netloc != "www.sec.gov" or not url.path.startswith("/Archives/edgar/data/40533/")):
            raise ValueError("Interim evidence needs dated GD filings and exhibits on SEC EDGAR.")

    ytd = _amounts(item["ytd"], "nine-month actuals", YTD_FIELDS)
    balances = _amounts(item["period_end_balances"], "period-end balances", BALANCE_FIELDS)
    segments = {
        key: _amounts(values, key, SEGMENT_FIELDS)
        for key, values in _fields(item["segments"], "segments", set(SEGMENTS.values())).items()
    }
    if not ytd["deliveries"].is_integer() or ytd["deliveries"] < 0:
        raise ValueError("Aircraft deliveries must be a nonnegative whole count.")
    if not balances["shares_outstanding"].is_integer() or balances["shares_outstanding"] <= 0:
        raise ValueError("Shares outstanding must be a positive whole count.")
    if min(balances.values()) < 0 or ytd["capital_expenditures"] < 0 or ytd["corporate_operating_costs"] < 0:
        raise ValueError("Balance amounts, capex and corporate costs must be nonnegative.")
    if ytd["revenue"] <= 0 or any(min(segment["revenue"], segment["backlog"]) < 0 for segment in segments.values()):
        raise ValueError("Consolidated revenue must be positive and segment revenue and backlog must be nonnegative.")
    guide = _fields(item["guidance"], "FY guidance", {"revenue", "operating_margin", "net_interest_expense", "diluted_eps"})
    guidance = GDGuidance(
        _range(guide["revenue"], "FY revenue guidance"),
        _number(guide["operating_margin"], "FY operating margin guidance"),
        _number(guide["net_interest_expense"], "FY net interest guidance"),
        _range(guide["diluted_eps"], "FY diluted EPS guidance"),
    )
    if guidance.revenue[0] < ytd["revenue"] or not -1 <= guidance.operating_margin <= 1:
        raise ValueError("FY revenue guidance must cover nine-month revenue and margin must be a ratio.")

    dividend = None
    if "scheduled_dividend" in item:
        row = _fields(item["scheduled_dividend"], "scheduled dividend", {
            "record_date", "payment_date", "usd_per_share", "period_end_dividends_payable_millions", "declaration",
        })
        record = _day(row["record_date"], "dividend record_date")
        payment = _day(row["payment_date"], "dividend payment_date")
        per_share = _number(row["usd_per_share"], "dividend per share")
        payable = _number(row["period_end_dividends_payable_millions"], "period-end dividends payable")
        declaration = _source(row["declaration"], "dividend declaration", as_of)
        if (declaration.kind != "issuer_release" or not declaration.available_on <= record <= payment
                or payment <= period_end or per_share <= 0 or payable < 0):
            raise ValueError("A scheduled dividend requires a dated issuer declaration, valid dates and positive per-share cash.")
        dividend = GDScheduledDividend(record, payment, per_share, payable, declaration)
    diluted_shares = None
    if "ytd_diluted_shares_millions" in item:
        diluted_shares = _number(item["ytd_diluted_shares_millions"], "nine-month diluted weighted-average shares in millions")
        # Net earnings and EPS are both rounded in the filing.
        if diluted_shares <= 0 or abs(ytd["net_earnings"] / diluted_shares - ytd["diluted_eps"]) > 0.005 + 0.5 / diluted_shares:
            raise ValueError("Nine-month diluted shares must reconcile to the reported diluted EPS.")
    return GDInterimEvidence(as_of, period_end, filing, release, ytd, balances, segments, guidance, dividend, diluted_shares)


def assess_gd_refresh(
    interim: GDInterimEvidence, market: MarketInputs, cash_inputs: GDCashInputs | None = None,
    *, annual_history: GDHistoryEvidence | None = None,
) -> GDRefreshReadiness:
    """Tie current evidence and expose the inputs still needed for a valuation."""
    if market.as_of != interim.as_of or market.valuation_close is None:
        raise ValueError("A dated valuation close is required for the same cutoff as the interim evidence.")
    if market.valuation_close.observed_on < interim.period_end:
        raise ValueError("The valuation close must follow the reported quarter.")
    ytd, balances, guide = interim.ytd, interim.period_end_balances, interim.guidance
    segments = interim.segments.values()
    checks = (
        ("Segment revenue", sum(row["revenue"] for row in segments), ytd["revenue"]),
        ("Segment operating earnings less corporate costs", sum(row["operating_earnings"] for row in segments) - ytd["corporate_operating_costs"], ytd["operating_earnings"]),
        ("Segment backlog", sum(row["backlog"] for row in segments), balances["backlog"]),
        ("Funded and unfunded backlog", balances["funded_backlog"] + balances["unfunded_backlog"], balances["backlog"]),
        ("Issuer free cash flow", ytd["cash_from_operations"] - ytd["capital_expenditures"], ytd["issuer_free_cash_flow"]),
    )
    for label, calculated, reported in checks:
        if not isclose(calculated, reported, rel_tol=0, abs_tol=TOLERANCE_MILLIONS):
            raise ValueError(f"{label} does not reconcile. Calculated {calculated:g}, reported {reported:g} USD millions.")

    low, high = guide.revenue
    year_end = date(interim.as_of.year, 12, 31)
    earnings = sorted(revenue * guide.operating_margin - ytd["operating_earnings"] for revenue in (low, high))
    bridge = GDQ4Bridge(
        (low - ytd["revenue"], high - ytd["revenue"]),
        (earnings[0], earnings[1]),
        guide.net_interest_expense - ytd["net_interest_expense"],
        (year_end - interim.period_end).days,
        (year_end - interim.as_of).days,
        "Subtract reported nine-month totals from full-year guidance. Operating earnings use the approximate guided margin. "
        "The result covers the full fourth quarter. A valuation needs cash flows after the cutoff. EPS guidance is retained at full-year scope.",
    )
    cash_bridge = build_gd_cash_bridge(GDCashBalances(
        interim.period_end, balances["cash"], balances["total_debt"], int(balances["shares_outstanding"]),
    ), interim.as_of, cash_inputs)
    history_ready = annual_history is not None and audit_gd_history(annual_history, market).history_ready
    blockers = []
    if cash_bridge.cutoff is None:
        blockers.append("Supply sourced or explicitly estimated cash, debt and share movements through the valuation date.")
    if cash_bridge.remaining_fcfe is None and interim.as_of < year_end:
        missing_fcfe = FCFE_FIELDS - cash_bridge.remaining.amounts.keys()
        names = ", ".join(name.removesuffix("_millions").replace("_", " ") for name in sorted(missing_fcfe))
        blockers.append(f"Complete the remaining-period FCFE inputs ({names}).")
    if cash_bridge.year_end is None:
        blockers.append("Complete the cash, debt and share roll-forward through fiscal year-end.")
    if not history_ready:
        blockers.append("Load and reconcile the five fiscal years of annual source evidence independently of the archive.")
    blockers.append("Supply sourced forecast drivers, case selections, cost of equity, terminal growth and payout assumptions.")
    return GDRefreshReadiness(
        interim.as_of, interim, market, bridge, tuple(label for label, _, _ in checks),
        balances["total_debt"] - balances["cash"], len(required_gd_history_cells()), len(_required_forecast_cells()),
        tuple(blockers), cash_bridge, annual_history_ready=history_ready,
    )

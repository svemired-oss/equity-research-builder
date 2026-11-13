"""FedEx Q4 2026 operating cash outlook and dated cutoff bridge in USD millions."""

from dataclasses import dataclass
from datetime import date, timedelta
from math import isclose
from urllib.parse import urlsplit

from .gd_inputs import _day, _fields, _number, _object, _text


Q4_START = date(2026, 10, 1)
Q4_END = date(2026, 12, 31)
SEPTEMBER_END = Q4_START - timedelta(days=1)
TIMING_KEYS = (
    "operating_cfo_share_before_cutoff", "capex_share_before_cutoff",
    "pension_share_before_cutoff", "tariff_cash_share_before_cutoff",
)


@dataclass(frozen=True)
class FDXCashWindow:
    period_start: date
    period_end: date
    calendar_days: int
    operating_cfo_before_voluntary_pension_millions: float
    voluntary_pension_contribution_millions: float
    operating_cfo_excluding_tariffs_millions: float
    customer_tariff_cash_change_millions: float
    modeled_cfo_including_tariffs_millions: float
    continuing_capex_millions: float
    operating_cash_after_capex_millions: float
    supply_chain_sale_cash_millions: float


@dataclass(frozen=True)
class FDXCashBridge:
    as_of: date
    fourth_quarter: FDXCashWindow
    through_cutoff: FDXCashWindow
    after_cutoff: FDXCashWindow
    reported_nine_month_cfo_millions: float
    reported_nine_month_continuing_cfo_millions: float | None
    reported_nine_month_capex_millions: float
    continuing_nine_month_capex_millions: float
    implied_january_may_consolidated_cfo_millions: float
    june_september_2025_continuing_cfo_millions: float
    calendar_2026_guidance_cfo_millions: float
    calendar_2026_guidance_adjusted_fcf_millions: float
    calendar_2026_presentation_capex_millions: float
    calendar_2026_guidance_continuing_capex_millions: float
    calendar_2026_guidance_voluntary_pension_millions: float
    legacy_may_2026_voluntary_pension_paid_millions: float
    june_september_2026_voluntary_pension_paid_millions: float
    september_customer_refund_obligation_millions: float
    cutoff_customer_refund_obligation_millions: float
    year_end_customer_refund_obligation_millions: float
    september_cash_net_of_customer_refunds_millions: float
    available_cash_before_financing_at_cutoff_millions: float
    available_cash_before_financing_at_year_end_millions: float
    timing_shares: dict[str, float]
    limitations: tuple[str, ...]


def _record(record, key):
    return _object(record.get(key), key)


def _amount(record, key, *, signed=False):
    number = _number(record.get(key), key)
    if not signed and number < 0:
        raise ValueError(f"{key} must be nonnegative.")
    return number


def _cash_period(cash_flows, sources, key, period_start, period_end):
    row = _record(cash_flows, key)
    if (row.get("basis") != "reported_consolidated_unrecast"
            or _day(row.get("period_start"), f"{key} start") != period_start
            or _day(row.get("period_end"), f"{key} end") != period_end):
        raise ValueError(f"{key} must be the specified reported consolidated cash-flow period.")
    _actual_source(sources, row, period_end, key)
    return row


def _check_sources(assumptions, actuals, as_of):
    for data, date_key in ((assumptions, "available_on"), (actuals, "published_on")):
        for key, source in _record(data, "sources").items():
            source = _object(source, key)
            available = _day(source.get(date_key), f"{key}.{date_key}")
            url = urlsplit(_text(source.get("url"), f"{key} URL"))
            if available > as_of or url.scheme != "https" or not url.hostname or url.username or url.password:
                raise ValueError(f"{key} must cite a public HTTPS source available by {as_of}.")

    for section, key in (("calendar_2026_outlook", "source_id"),
                         ("calendar_2026_outlook", "continuing_capex_source_id"),
                         ("pension", "source_id"), ("pension", "legacy_may_fy2026_source_id"),
                         ("tariffs", "source_id")):
        source = _text(_record(assumptions, section).get(key), f"{section}.{key}")
        if source not in assumptions["sources"]:
            raise ValueError(f"Unknown cash outlook source {source!r}.")


def _actual_source(sources, row, observed_on, label):
    source = _record(sources, _text(row.get("source_id"), f"{label} source"))
    if _day(source.get("published_on"), f"{label} source date") < observed_on:
        raise ValueError(f"The source for {label} predates the observation.")


def _check_outlook(outlook):
    raw_cfo = _amount(outlook, "gaap_cash_from_operations_millions")
    deck_capex = _amount(outlook, "presentation_capex_millions")
    disposition = _amount(outlook, "asset_disposition_proceeds_millions")
    reported_fcf = _amount(outlook, "free_cash_flow_millions")
    pension = _amount(outlook, "voluntary_us_pension_contributions_millions")
    tariffs = _amount(outlook, "customer_tariff_refunds_due_adjustment_millions")
    adjusted_fcf = _amount(outlook, "adjusted_free_cash_flow_millions")
    if not isclose(raw_cfo - deck_capex + disposition, reported_fcf, abs_tol=1):
        raise ValueError("The issuer's CY2026 free cash flow outlook does not reconcile.")
    if not isclose(reported_fcf + pension - tariffs, adjusted_fcf, abs_tol=1):
        raise ValueError("The issuer's CY2026 adjusted free cash flow outlook does not reconcile.")


def _timing_shares(record, elapsed_share):
    record = _fields(record, "cash timing", set(TIMING_KEYS), {"method"})
    shares = {}
    for key in TIMING_KEYS:
        value = elapsed_share if record[key] is None else _number(record[key], key)
        if not 0 <= value <= 1:
            raise ValueError(f"{key} must be between zero and one.")
        shares[key] = value
    return shares


def _window(start, end, cfo_before_pension, capex, pension, tariffs, sale):
    clean_cfo = cfo_before_pension - pension
    return FDXCashWindow(
        period_start=start, period_end=end, calendar_days=(end - start).days + 1,
        operating_cfo_before_voluntary_pension_millions=cfo_before_pension,
        voluntary_pension_contribution_millions=pension,
        operating_cfo_excluding_tariffs_millions=clean_cfo,
        customer_tariff_cash_change_millions=tariffs,
        modeled_cfo_including_tariffs_millions=clean_cfo + tariffs,
        continuing_capex_millions=capex,
        operating_cash_after_capex_millions=clean_cfo - capex,
        supply_chain_sale_cash_millions=sale,
    )


def build_fdx_cash(actuals: dict, assumptions: dict) -> FDXCashBridge:
    """Estimate the Q4 residual and allocate it across a pre-open valuation cutoff."""
    as_of = _day(assumptions.get("as_of"), "cash cutoff")
    if (as_of != _day(actuals.get("as_of"), "FedEx actuals cutoff")
            or not Q4_START < as_of <= Q4_END or assumptions.get("cutoff_basis") != "pre_open"):
        raise ValueError("FedEx cash requires a matching Q4 pre-open cutoff with days on both sides.")
    _check_sources(assumptions, actuals, as_of)
    outlook = _record(assumptions, "calendar_2026_outlook")
    _check_outlook(outlook)
    cash_flows = _record(actuals, "cash_flows")
    sources = _record(actuals, "sources")
    calendar_ytd = _cash_period(cash_flows, sources, "2026_ytd", date(2026, 1, 1), SEPTEMBER_END)
    transition = _cash_period(cash_flows, sources, "2026_transition_through_q3", date(2026, 6, 1), SEPTEMBER_END)
    prior_transition = _cash_period(cash_flows, sources, "2025_transition_comparable", date(2025, 6, 1), date(2025, 9, 30))
    reported_ytd_cfo = _amount(calendar_ytd, "cash_from_operations", signed=True)
    reported_ytd_capex = -_amount(calendar_ytd, "capital_expenditures", signed=True)
    capex_actuals = _record(actuals, "capital_spending")
    if capex_actuals.get("basis") != "continuing_operations_positive_spend":
        raise ValueError("The capex outlook must use continuing operations and positive spend.")
    capex_ytd = _record(capex_actuals, "2026_ytd")
    _actual_source(sources, capex_ytd, SEPTEMBER_END, "calendar nine-month capex")
    ytd_capex = _amount(capex_ytd, "total")
    capex_guidance = _amount(outlook, "continuing_capex_guidance_approximate_millions")
    quarter_capex = capex_guidance - ytd_capex
    if not 0 <= ytd_capex <= reported_ytd_capex or quarter_capex < 0:
        raise ValueError("Continuing capex is inconsistent with reported capex or the full-year guidance.")

    pension = _record(assumptions, "pension")
    pension_quarter = _amount(pension, "voluntary_us_pension_guidance_october_december_millions")
    pension_transition = _amount(pension, "voluntary_us_pension_paid_june_september_millions")
    pension_annual = _amount(outlook, "voluntary_us_pension_contributions_millions")
    if pension_quarter + pension_transition > pension_annual:
        raise ValueError("June–September and Q4 voluntary pension payments exceed the calendar-year outlook.")

    balance = _record(_record(actuals, "balance_sheets"), SEPTEMBER_END.isoformat())
    _actual_source(sources, balance, SEPTEMBER_END, "September balance sheet")
    cash = _amount(balance, "cash")
    september_refunds = _amount(balance, "tariff_refund_customer_obligation")
    refund_claims_start = _day(_record(assumptions, "tariffs").get("refund_claims_started_on"), "tariff claims start")
    if not date(2026, 1, 1) <= refund_claims_start <= SEPTEMBER_END or september_refunds > cash:
        raise ValueError("September customer refund cash does not match the disclosed refund program period or balance.")
    if any(_amount(row, "closing_cash") != cash for row in (calendar_ytd, transition)):
        raise ValueError("September cash must match both reported cash-flow periods.")

    december_refunds = _amount(outlook, "customer_tariff_refunds_due_adjustment_millions")
    quarter_refunds = december_refunds - september_refunds
    annual_cfo = _amount(outlook, "gaap_cash_from_operations_millions")
    raw_quarter_cfo = annual_cfo - reported_ytd_cfo
    quarter_cfo_before_pension = raw_quarter_cfo - quarter_refunds + pension_quarter

    sale = _record(_record(actuals, "transactions"), "fedex_supply_chain_sale")
    sale_date = _day(sale.get("date"), "Supply Chain sale date")
    if not Q4_START <= sale_date < as_of:
        raise ValueError("The known Supply Chain sale must be within the pre-cutoff Q4 window.")
    _actual_source(sources, sale, sale_date, "Supply Chain sale")
    sale_cash = _amount(sale, "net_cash_proceeds_approximate")

    quarter_days = (Q4_END - Q4_START).days + 1
    shares = _timing_shares(assumptions.get("timing"), (as_of - Q4_START).days / quarter_days)
    amounts = (quarter_cfo_before_pension, quarter_capex, pension_quarter, quarter_refunds)
    before = tuple(value * shares[key] for value, key in zip(amounts, TIMING_KEYS, strict=True))
    remaining = tuple(value - used for value, used in zip(amounts, before, strict=True))
    quarter = _window(Q4_START, Q4_END, *amounts, sale_cash)
    through_cutoff = _window(Q4_START, as_of - timedelta(days=1), *before, sale_cash)
    after_cutoff = _window(as_of, Q4_END, *remaining, 0)
    opening_available_cash = cash - september_refunds
    return FDXCashBridge(
        as_of=as_of, fourth_quarter=quarter, through_cutoff=through_cutoff, after_cutoff=after_cutoff,
        reported_nine_month_cfo_millions=reported_ytd_cfo,
        reported_nine_month_continuing_cfo_millions=None,
        reported_nine_month_capex_millions=reported_ytd_capex,
        continuing_nine_month_capex_millions=ytd_capex,
        implied_january_may_consolidated_cfo_millions=reported_ytd_cfo - _amount(transition, "cash_from_operations", signed=True),
        june_september_2025_continuing_cfo_millions=(
            _amount(prior_transition, "cash_from_operations", signed=True)
            - _amount(prior_transition, "cash_from_discontinued_operations", signed=True)
        ),
        calendar_2026_guidance_cfo_millions=annual_cfo,
        calendar_2026_guidance_adjusted_fcf_millions=_amount(outlook, "adjusted_free_cash_flow_millions"),
        calendar_2026_presentation_capex_millions=_amount(outlook, "presentation_capex_millions"),
        calendar_2026_guidance_continuing_capex_millions=capex_guidance,
        calendar_2026_guidance_voluntary_pension_millions=pension_annual,
        legacy_may_2026_voluntary_pension_paid_millions=_amount(pension, "legacy_may_fy2026_voluntary_us_pension_paid_millions"),
        june_september_2026_voluntary_pension_paid_millions=pension_transition,
        september_customer_refund_obligation_millions=september_refunds,
        cutoff_customer_refund_obligation_millions=september_refunds + through_cutoff.customer_tariff_cash_change_millions,
        year_end_customer_refund_obligation_millions=december_refunds,
        september_cash_net_of_customer_refunds_millions=opening_available_cash,
        available_cash_before_financing_at_cutoff_millions=(
            opening_available_cash + through_cutoff.operating_cash_after_capex_millions + sale_cash
        ),
        available_cash_before_financing_at_year_end_millions=(
            opening_available_cash + quarter.operating_cash_after_capex_millions + sale_cash
        ),
        timing_shares=shares,
        limitations=(
            "The issuer's Q4 CFO residual assumes that calendar-year CFO guidance and the nine-month reported cash flow use a consistent consolidated basis. The resulting Q4 period is after the Freight separation. Exact calendar nine-month continuing-only CFO is unavailable.",
            "The $2 billion guidance adjustment is modeled as the year-end net customer tariff refund liability. Cash attributable to FedEx's own recoveries is not separately measurable. The actual refund timing and year-end balance remain uncertain.",
            "The voluntary pension funding forecast is already included in modeled CFO. The May 2026 fiscal-year contribution covers a different period and cannot be subtracted from the calendar-year forecast to estimate Q4 funding.",
            "Q4 capex uses continuing-operations year-to-date spend and the approximate continuing-operations outlook. The appendix's $3,945 million reconciling figure is retained for comparison.",
            "The default timing estimate allocates operating cash, capex, voluntary pension and customer refund cash in proportion to calendar days. Only the Supply Chain sale proceeds have a confirmed October date.",
            "The cash estimate excludes dividends, buybacks, debt, foreign exchange and other investing cash flows. It does not establish a reported cutoff or year-end balance, an operating cash reserve, or a cash amount available for distribution.",
        ),
    )

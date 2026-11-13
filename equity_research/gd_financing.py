"""Extend GD's operating cash estimates with a cited financing scenario."""

from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from .gd_cash_bridge import CitedAmount, GDCashInputs, GDCashWindow
from .gd_inputs import InputSource, _day, _fields, _number, _read, _source, _text
from .gd_interim import GDInterimEvidence
from .gd_run_inputs import _header


DEBT_FIELDS = {"debt_proceeds_millions", "debt_repayments_millions", "other_debt_carrying_change_millions"}


@dataclass(frozen=True)
class GDFinancing:
    as_of: date
    q2_end: date
    q2_shares: int
    q3_program_shares: int
    q3_program_shares_remaining: int
    q3_repurchases_millions: float
    q3_other_cash_millions: float
    annual_lease_principal_proxy_millions: float
    q2_source: InputSource
    q3_source: InputSource
    lease_source: InputSource
    repurchase_pace: float
    non_program_share_pace: float
    debt: dict[str, dict[str, CitedAmount]]
    q3_nonfinancing_cash_millions: float = 0.0


def _count(value: object, label: str) -> int:
    number = _number(value, label)
    if number < 0 or not number.is_integer():
        raise ValueError(f"{label} must be a nonnegative whole share count.")
    return int(number)


def load_gd_financing(path: Path) -> GDFinancing:
    item = _fields(_read(path), "GD financing", {
        "schema_version", "ticker", "currency", "as_of", "sources", "benchmarks", "assumptions",
    })
    as_of = _header(item)
    sources = _fields(item["sources"], "financing sources", {"q2", "q3", "lease"})
    q2, q3, lease = (_source(sources[key], key, as_of) for key in ("q2", "q3", "lease"))
    for source in (q2, q3, lease):
        url = urlsplit(source.url or "")
        if source.kind != "sec_filing" or url.scheme != "https" or url.netloc != "www.sec.gov" or not url.path.startswith("/Archives/edgar/data/40533/"):
            raise ValueError("Financing benchmarks require GD filings on SEC EDGAR.")

    facts = _fields(item["benchmarks"], "financing benchmarks", {
        "q2_end", "q2_shares", "q3_program_shares", "q3_program_shares_remaining", "q3_repurchases_millions",
        "q3_other_financing_millions", "q3_other_investing_millions", "q3_discontinued_cash_millions",
        "annual_lease_principal_proxy_millions",
    })
    q2_end = _day(facts["q2_end"], "Q2 period end")
    if (q2_end.year != as_of.year or q2_end.month not in (6, 7)
            or not lease.available_on < q2_end <= q2.available_on < q3.available_on):
        raise ValueError("Use current-year Q2 and Q3 filings and a lease benchmark available before Q2 end.")
    opening_shares, bought, authorized = (
        _count(facts[name], name) for name in ("q2_shares", "q3_program_shares", "q3_program_shares_remaining")
    )
    repurchases, principal = (_number(facts[name], name) for name in ("q3_repurchases_millions", "annual_lease_principal_proxy_millions"))
    if min(opening_shares, bought, repurchases) <= 0 or principal < 0:
        raise ValueError("Use positive share and buyback benchmarks and nonnegative lease principal.")
    nonfinancing_cash = sum(_number(facts[name], name) for name in (
        "q3_other_investing_millions", "q3_discontinued_cash_millions",
    ))
    other_cash = _number(facts["q3_other_financing_millions"], "Q3 other financing") + nonfinancing_cash

    assumptions = _fields(item["assumptions"], "financing assumptions", {"repurchase_pace", "non_program_share_pace", "debt"})
    buyback_pace, share_pace = (_number(assumptions[name], name) for name in ("repurchase_pace", "non_program_share_pace"))
    if min(buyback_pace, share_pace) < 0:
        raise ValueError("Share pace multipliers must be nonnegative.")
    windows = _fields(assumptions["debt"], "debt assumptions", {"elapsed", "remaining"})
    debt = {}
    for window, raw in windows.items():
        row = _fields(raw, f"{window} debt assumptions", DEBT_FIELDS | {"rationale"})
        source = InputSource("model_assumption", _text(row["rationale"], f"{window} rationale"), as_of, url=q3.url)
        amounts = {name: _number(row[name], name) for name in sorted(DEBT_FIELDS)}
        if min(amounts["debt_proceeds_millions"], amounts["debt_repayments_millions"]) < 0:
            raise ValueError("Debt proceeds and repayments must be nonnegative magnitudes.")
        debt[window] = {name: CitedAmount(amount, source) for name, amount in amounts.items()}
    return GDFinancing(as_of, q2_end, opening_shares, bought, authorized, repurchases, other_cash, principal,
                       q2, q3, lease, buyback_pace, share_pace, debt, nonfinancing_cash)


def apply_gd_financing(interim: GDInterimEvidence, cash_inputs: GDCashInputs, financing: GDFinancing) -> GDCashInputs:
    """Populate only financing fields and leave the two operating cash windows intact."""
    as_of = interim.as_of
    quarter_end = interim.period_end
    year_end = date(as_of.year, 12, 31)
    if not (as_of == cash_inputs.as_of == financing.as_of and financing.q3_source.url == interim.filing.url
            and financing.q3_source.available_on == interim.filing.available_on and financing.q2_end < quarter_end):
        raise ValueError("Financing and cash inputs must match the cutoff and GD's reported quarter.")
    if ((cash_inputs.elapsed.start_exclusive, cash_inputs.elapsed.end_inclusive,
         cash_inputs.remaining.start_exclusive, cash_inputs.remaining.end_inclusive) != (quarter_end, as_of, as_of, year_end)
            or not quarter_end < as_of < year_end):
        raise ValueError("Financing requires two cash windows split at the cutoff inside Q4.")
    dividend = interim.scheduled_dividend
    if dividend is None or not quarter_end < dividend.payment_date <= year_end:
        raise ValueError("Financing requires the declared quarterly dividend schedule.")

    q3_days = (quarter_end - financing.q2_end).days
    q4_days = (year_end - quarter_end).days
    year_days = (year_end - date(as_of.year - 1, 12, 31)).days
    q3_non_program_change = int(interim.period_end_balances["shares_outstanding"]) - financing.q2_shares + financing.q3_program_shares
    repurchase_cash_per_share_millions = financing.q3_repurchases_millions / financing.q3_program_shares
    buybacks_per_day = _number(financing.q3_program_shares * financing.repurchase_pace / q3_days, "daily buyback shares")
    non_program_per_day = _number(q3_non_program_change * financing.non_program_share_pace / q3_days, "daily non-program share change")
    if round(buybacks_per_day * q4_days) > financing.q3_program_shares_remaining:
        raise ValueError("Projected purchases exceed the reported remaining buyback authorization.")

    def estimate(amount: float, rationale: str, source: InputSource) -> CitedAmount:
        return CitedAmount(_number(amount, "financing estimate"), InputSource("model_assumption", rationale, as_of, url=source.url))

    def complete(key: str, window: GDCashWindow) -> GDCashWindow:
        start = (window.start_exclusive - quarter_end).days
        end = (window.end_inclusive - quarter_end).days
        days = end - start
        bought = round(buybacks_per_day * end) - round(buybacks_per_day * start)
        non_program = round(non_program_per_day * end) - round(non_program_per_day * start)
        principal = financing.annual_lease_principal_proxy_millions * days / year_days
        pay_dividend = window.start_exclusive < dividend.payment_date <= window.end_inclusive
        estimated = {
            **financing.debt[key],
            "other_fcfe_cash_flows_millions": estimate(
                -principal, "Allocate the FY2025 current finance lease principal proxy by days. Interest is in operating cash flow. "
                "Lease principal is a cash outflow separate from Note H carrying debt. No additional adjustments in this scenario.", financing.lease_source,
            ),
            "dividends_paid_millions": estimate(
                dividend.period_end_dividends_payable_millions if pay_dividend else 0,
                f"Use the quarter-end dividend payable as a cash proxy solely in the window containing the announced {dividend.payment_date} payment. "
                f"Dividend announcement: {dividend.declaration.url}", interim.filing,
            ),
            "share_repurchases_millions": estimate(
                bought * repurchase_cash_per_share_millions,
                f"Model {bought:,} program shares using Q3 daily repurchase pace times {financing.repurchase_pace:g}. "
                "Cash uses the Q3 purchase cash per program share as a blended proxy.", interim.filing,
            ),
            "share_issuance_proceeds_millions": estimate(
                0, "The Q3 cash flow statement bundles option proceeds with other financing. The scenario retains bundled cash in other cash movements.", interim.filing,
            ),
            "other_cash_movements_millions": estimate(
                financing.q3_other_cash_millions * days / q3_days + principal,
                "Continue Q3 net other financing, other investing and discontinued cash at its daily rate. Add back the same modeled lease principal "
                f"already deducted in FCFE to keep it from being counted twice. Q2 comparative inputs: {financing.q2_source.url}", interim.filing,
            ),
            "net_share_change": estimate(
                non_program - bought, f"Model {non_program:+,} non-program shares and {bought:,} program shares purchased. "
                f"Use Q3 daily paces and infer non-program movement from the change between the Q2 and Q3 outstanding share counts plus Q3 buybacks. "
                f"Non-program pace multiplier {financing.non_program_share_pace:g}. Q2 filing: {financing.q2_source.url}", interim.filing,
            ),
        }
        if window.amounts.keys() & estimated.keys():
            raise ValueError(f"The {key} cash window already contains financing inputs.")
        return replace(window, amounts={**window.amounts, **estimated})

    return GDCashInputs(as_of, complete("elapsed", cash_inputs.elapsed), complete("remaining", cash_inputs.remaining))

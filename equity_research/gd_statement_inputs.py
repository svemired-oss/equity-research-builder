"""Read dated GD statement evidence and forward schedule assumptions."""

from dataclasses import dataclass
from datetime import date
from math import isclose
from pathlib import Path
from urllib.parse import urlsplit

from .gd_inputs import InputSource, _array, _day, _fields, _number, _read, _source
from .gd_interim import _amounts
from .gd_run_inputs import _header
from .reconciliation import TOLERANCE_MILLIONS


_HEADER = {"schema_version", "ticker", "currency", "as_of"}
_ASSETS = {
    "cash", "receivables", "unbilled", "inventory", "other_current_assets", "ppe_gross",
    "intangibles", "goodwill", "other_assets",
}
_LIABILITIES = {
    "accounts_payable", "customer_advances", "other_current_liabilities", "other_liabilities", "debt_carrying",
}
_EQUITY = {"paid_in_capital", "retained_earnings", "treasury_stock", "aoci"}
_BALANCE_FIELDS = _ASSETS | _LIABILITIES | _EQUITY | {
    "ppe_accumulated_depreciation", "dividends_payable", "equity", "total_assets", "total_liabilities",
}
_SIGNED_BALANCES = {"retained_earnings", "treasury_stock", "aoci", "equity"}
_NONNEGATIVE_YTD = {"depreciation", "intangible_amortization", "lease_amortization", "sbc", "g_and_a"}
_YTD_FIELDS = _NONNEGATIVE_YTD | {"other_income", "taxes", "pretax"}
_YTD_INVESTING = ("cash_capex", "other_investing")
_YTD_FINANCING = ("note_draws", "note_repayments", "dividends_paid", "share_repurchases", "other_financing")
_YTD_CASH_FIELDS = {
    *_YTD_INVESTING, *_YTD_FINANCING, "cash_from_operations", "cash_from_investing", "cash_from_financing",
    "cash_from_discontinued", "cash_change", "cash_beginning", "cash_ending",
}
_ASSUMPTION_RATIOS = {"depreciation_rate", "cash_yield", "other_debt_coupon"}
_ASSUMPTION_AMOUNTS = {
    "annual_finance_rou_amortization", "final_intangible_amortization", "final_lease_payment",
    "annual_debt_cost_amortization",
}
_ASSUMPTION_GROWTH = {"dividend_growth", "buyback_price_growth"}
_ASSUMPTION_SOURCES = {"capex", "tax", "assets", "interest", "capital_return", "q4"}


@dataclass(frozen=True)
class DebtNote:
    maturity: date
    principal: float
    coupon: float


@dataclass(frozen=True)
class GDStatementEvidence:
    as_of: date
    period_end: date
    balance: dict[str, float]
    ytd: dict[str, float]
    other_principal: float
    unamortized_costs: float
    notes: tuple[DebtNote, ...]
    lease_rou_asset: float
    lease_liability: float
    lease_rate: float
    lease_payments: dict[int, float]
    intangible_amortization: dict[int, float]
    sources: dict[str, InputSource]
    ytd_cash_flow: dict[str, float] | None = None


@dataclass(frozen=True)
class GDStatementAssumptions:
    as_of: date
    capex_share: tuple[float, ...]
    book_tax_rate: tuple[float, ...]
    depreciation_rate: float
    annual_finance_rou_amortization: float
    final_intangible_amortization: float
    final_lease_payment: float
    cash_yield: float
    other_debt_coupon: float
    annual_debt_cost_amortization: float
    other_income: float
    dividend_growth: float
    buyback_price_growth: float
    sources: dict[str, InputSource]


def _sources(raw: object, keys: set[str], kind: str, as_of: date) -> dict[str, InputSource]:
    fields = _fields(raw, "statement sources", keys)
    sources = {key: _source(value, f"statement source {key}", as_of) for key, value in fields.items()}
    if any(source.kind != kind for source in sources.values()):
        raise ValueError(f"Statement sources must have kind {kind}.")
    return sources


def _verify_balance(balance: dict[str, float]) -> None:
    if any(balance[key] < 0 for key in _BALANCE_FIELDS - _SIGNED_BALANCES) or balance["treasury_stock"] > 0:
        raise ValueError("Statement balances must use nonnegative magnitudes and signed contra-equity.")
    if balance["ppe_accumulated_depreciation"] > balance["ppe_gross"]:
        raise ValueError("Accumulated depreciation cannot exceed gross PP&E.")
    if balance["dividends_payable"] > balance["other_current_liabilities"]:
        raise ValueError("Dividends payable must fit within other current liabilities.")
    reconciliations = (
        ("Total assets", sum(balance[key] for key in _ASSETS) - balance["ppe_accumulated_depreciation"], balance["total_assets"]),
        ("Total liabilities", sum(balance[key] for key in _LIABILITIES), balance["total_liabilities"]),
        ("Shareholders’ equity", sum(balance[key] for key in _EQUITY), balance["equity"]),
        ("Assets versus liabilities and equity", balance["total_assets"], balance["total_liabilities"] + balance["equity"]),
    )
    for label, calculated, reported in reconciliations:
        if not isclose(calculated, reported, rel_tol=0, abs_tol=TOLERANCE_MILLIONS):
            raise ValueError(f"{label} does not reconcile in the statement evidence.")


def _verify_ytd_cash(cash: dict[str, float], balance: dict[str, float]) -> None:
    if (min(cash["note_draws"], cash["cash_beginning"], cash["cash_ending"]) < 0
            or max(cash[key] for key in ("cash_capex", "note_repayments", "dividends_paid", "share_repurchases")) > 0):
        raise ValueError("YTD cash-flow evidence must preserve the signs of proceeds, cash balances and payments.")
    reconciliations = (
        ("Investing components", sum(cash[key] for key in _YTD_INVESTING), cash["cash_from_investing"]),
        ("Financing components", sum(cash[key] for key in _YTD_FINANCING), cash["cash_from_financing"]),
        ("Cash-flow sections", sum(cash[key] for key in (
            "cash_from_operations", "cash_from_investing", "cash_from_financing", "cash_from_discontinued",
        )), cash["cash_change"]),
        ("Opening cash and cash change", cash["cash_beginning"] + cash["cash_change"], cash["cash_ending"]),
        ("Closing cash and balance-sheet cash", cash["cash_ending"], balance["cash"]),
    )
    for label, calculated, reported in reconciliations:
        if not isclose(calculated, reported, rel_tol=0, abs_tol=TOLERANCE_MILLIONS):
            raise ValueError(f"{label} do not reconcile in the YTD cash-flow evidence.")


def _debt_note(raw: object, period_end: date) -> DebtNote:
    fields = _fields(raw, "debt note", {"maturity", "principal", "coupon"})
    month = fields["maturity"]
    if not isinstance(month, str) or len(month) != 7:
        raise ValueError("Debt note maturity must use YYYY-MM.")
    maturity = _day(f"{month}-01", "debt note maturity month")
    principal = _number(fields["principal"], "debt note principal")
    coupon = _number(fields["coupon"], "debt note coupon")
    if maturity < period_end.replace(day=1) or principal <= 0 or not 0 <= coupon <= 1:
        raise ValueError("Debt notes require an outstanding maturity, positive principal and coupon between zero and one.")
    return DebtNote(maturity, principal, coupon)


def _annual_amounts(raw: object, label: str, years: range) -> dict[int, float]:
    amounts = _amounts(raw, label, {str(year) for year in years})
    if any(amount < 0 for amount in amounts.values()):
        raise ValueError(f"{label} must be nonnegative.")
    return {int(year): amount for year, amount in amounts.items()}


def load_gd_statement_evidence(path: Path) -> GDStatementEvidence:
    """Load filed opening statements and supporting debt and asset schedules."""
    item = _fields(_read(path), "statement evidence", _HEADER | {
        "period_end", "balance", "ytd", "debt", "annual_notes", "sources",
    }, {"ytd_cash_flow"})
    as_of = _header(item)
    period_end = _day(item["period_end"], "statement period end")
    sources = _sources(item["sources"], {"interim", "annual"}, "sec_filing", as_of)
    if (period_end.year != as_of.year or period_end.month not in (9, 10)
            or not date(as_of.year - 1, 12, 31) <= sources["annual"].available_on < period_end <= sources["interim"].available_on):
        raise ValueError("Statement evidence requires a current-year Q3 filing and prior-year annual filing.")
    for source in sources.values():
        url = urlsplit(source.url or "")
        if url.scheme != "https" or url.netloc != "www.sec.gov" or not url.path.startswith("/Archives/edgar/data/40533/"):
            raise ValueError("Statement evidence requires GD filings on SEC EDGAR.")

    balance = _amounts(item["balance"], "statement balance", _BALANCE_FIELDS)
    _verify_balance(balance)
    ytd = _amounts(item["ytd"], "statement YTD", _YTD_FIELDS)
    if any(ytd[key] < 0 for key in _NONNEGATIVE_YTD):
        raise ValueError("Depreciation, amortization, share compensation and G&A must be nonnegative.")
    ytd_cash_flow = None
    if "ytd_cash_flow" in item:
        ytd_cash_flow = _amounts(item["ytd_cash_flow"], "statement YTD cash flow", _YTD_CASH_FIELDS)
        _verify_ytd_cash(ytd_cash_flow, balance)

    debt = _fields(item["debt"], "statement debt", {"other_principal", "unamortized_costs", "notes"})
    other_principal = _number(debt["other_principal"], "other debt principal")
    unamortized_costs = _number(debt["unamortized_costs"], "unamortized debt costs")
    notes = tuple(_debt_note(note, period_end) for note in _array(debt["notes"], "debt notes"))
    if min(other_principal, unamortized_costs) < 0:
        raise ValueError("Other principal and unamortized debt costs must be nonnegative.")
    carrying = sum(note.principal for note in notes) + other_principal - unamortized_costs
    if not isclose(carrying, balance["debt_carrying"], rel_tol=0, abs_tol=TOLERANCE_MILLIONS):
        raise ValueError("Debt principal less unamortized costs does not reconcile to reported carrying debt.")

    annual = _fields(item["annual_notes"], "annual notes", {
        "lease_rou_asset", "lease_liability", "lease_rate", "lease_payments", "intangible_amortization",
    })
    rou_asset = _number(annual["lease_rou_asset"], "finance lease ROU asset")
    lease_liability = _number(annual["lease_liability"], "finance lease liability")
    lease_rate = _number(annual["lease_rate"], "finance lease rate")
    if min(rou_asset, lease_liability) < 0 or not 0 <= lease_rate <= 1:
        raise ValueError("Finance lease balances must be nonnegative and the rate between zero and one.")
    lease_payments = _annual_amounts(annual["lease_payments"], "finance lease payments", range(as_of.year + 1, as_of.year + 5))
    intangible_amortization = _annual_amounts(
        annual["intangible_amortization"], "intangible amortization", range(as_of.year, as_of.year + 5),
    )
    return GDStatementEvidence(
        as_of, period_end, balance, ytd, other_principal, unamortized_costs, notes,
        rou_asset, lease_liability, lease_rate, lease_payments, intangible_amortization, sources, ytd_cash_flow=ytd_cash_flow,
    )


def _ratio_path(raw: object, label: str) -> tuple[float, ...]:
    rates = tuple(_number(value, label) for value in _array(raw, label))
    if len(rates) != 5 or any(not 0 <= rate <= 1 for rate in rates):
        raise ValueError(f"{label} must supply five annual ratios between zero and one.")
    return rates


def load_gd_statement_assumptions(path: Path) -> GDStatementAssumptions:
    """Load cited FY2027–31 schedule paths and current-year model assumptions."""
    scalar_fields = _ASSUMPTION_RATIOS | _ASSUMPTION_AMOUNTS | _ASSUMPTION_GROWTH | {"other_income"}
    item = _fields(_read(path), "statement assumptions", _HEADER | scalar_fields | {"capex_share", "book_tax_rate", "sources"})
    as_of = _header(item)
    scalars = {key: _number(item[key], key) for key in scalar_fields}
    if any(not 0 <= scalars[key] <= 1 for key in _ASSUMPTION_RATIOS):
        raise ValueError("Depreciation, cash yield and coupon rates must be between zero and one.")
    if any(scalars[key] < 0 for key in _ASSUMPTION_AMOUNTS):
        raise ValueError("Statement amortization and lease payment assumptions must be nonnegative.")
    if any(scalars[key] <= -1 for key in _ASSUMPTION_GROWTH):
        raise ValueError("Dividend and buyback price growth must exceed -100%.")
    return GDStatementAssumptions(
        as_of, _ratio_path(item["capex_share"], "capex_share"), _ratio_path(item["book_tax_rate"], "book_tax_rate"),
        sources=_sources(item["sources"], _ASSUMPTION_SOURCES, "model_assumption", as_of), **scalars,
    )

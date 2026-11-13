"""Link fresh GD operating cases to condensed financial statements and annual FCFE."""

from dataclasses import dataclass
from datetime import date
from math import fsum, isclose
from pathlib import Path

from .gd_cash_bridge import GDCashBalances, build_gd_cash_bridge
from .gd_cash_forecast import GDQ4CashForecast, cash_inputs_for_fiscal_case, forecast_gd_q4_cash, load_gd_cash_outlook
from .gd_evidence import load_gd_history_evidence
from .gd_financing import GDFinancing, apply_gd_financing, load_gd_financing
from .gd_inputs import InputSource
from .gd_interim import GDInterimEvidence, load_gd_interim
from .gd_operating_forecast import (
    CASES, GDOperatingForecast, OperatingPeriod, forecast_gd_operations, load_gd_operating_assumptions, load_gd_segment_evidence,
)
from .gd_statement_inputs import (
    GDStatementAssumptions, GDStatementEvidence, load_gd_statement_assumptions, load_gd_statement_evidence,
)
from .reconciliation import TOLERANCE_MILLIONS
from .valuation import FcfeStub


WORKING_ASSETS = ("receivables", "unbilled", "inventory", "other_current_assets")
WORKING_LIABILITIES = ("accounts_payable", "customer_advances", "other_current_liabilities")
FISCAL_CASH_CASE = {"bear": "low", "base": "base", "bull": "high"}


@dataclass(frozen=True)
class BalanceSheet:
    assets: dict[str, float]
    liabilities: dict[str, float]
    equity: dict[str, float]
    total_assets: float
    total_liabilities: float
    total_equity: float
    net_operating_working_capital: float
    shares_outstanding: int


@dataclass(frozen=True)
class CashFlowStatement:
    operating: dict[str, float]
    investing: dict[str, float]
    financing: dict[str, float]
    cash_from_operations: float
    cash_from_investing: float
    cash_from_financing: float
    other_cash_movements: float
    net_change_in_cash: float
    fcfe_millions: float


@dataclass(frozen=True)
class StatementPeriod:
    period_end: date
    income: dict[str, float]
    balance_sheet: BalanceSheet
    cash_flow: CashFlowStatement
    schedules: dict[str, float]


@dataclass(frozen=True)
class GDForwardStatements:
    as_of: date
    case: str
    reported_balance_date: date
    october_opening_balance: BalanceSheet
    fy2026_income: dict[str, float]
    q4_2026: StatementPeriod
    cutoff_shares: int
    cutoff_cash_millions: float
    cutoff_debt_millions: float
    cutoff_dividend_payable_millions: float
    remaining_fcfe: FcfeStub
    annual: tuple[StatementPeriod, ...]
    evidence: GDStatementEvidence
    assumptions: GDStatementAssumptions
    cash_outlook_source: InputSource
    financing_source: InputSource
    notes: tuple[str, ...]


def run_gd_statement_cases(
    as_of: date, inputs_dir: Path, *, cases: tuple[str, ...] = tuple(CASES),
) -> dict[str, GDForwardStatements]:
    """Build each requested scenario from the same dated evidence and schedules."""
    if not cases or len(set(cases)) != len(cases) or any(case not in CASES for case in cases):
        raise ValueError("Select distinct GD operating cases from bear, base and bull.")

    def dated_input(name: str) -> Path:
        return inputs_dir / f"gd-{name}-{as_of.isoformat()}.json"

    interim = load_gd_interim(dated_input("interim"))
    if interim.as_of != as_of:
        raise ValueError("The statement run must match the requested cutoff.")
    operations = forecast_gd_operations(
        interim, load_gd_history_evidence(dated_input("history")), load_gd_segment_evidence(dated_input("segment-evidence")),
        load_gd_operating_assumptions(dated_input("operating-assumptions")),
    )
    cash = forecast_gd_q4_cash(interim, load_gd_cash_outlook(dated_input("cash-outlook")))
    financing = load_gd_financing(dated_input("financing"))
    evidence = load_gd_statement_evidence(dated_input("statement-evidence"))
    assumptions = load_gd_statement_assumptions(dated_input("statement-assumptions"))
    return {
        case: forecast_gd_statements(interim, operations, cash, financing, evidence, assumptions, case=case)
        for case in cases
    }


def run_gd_statements(as_of: date, inputs_dir: Path, *, case: str = "base") -> GDForwardStatements:
    """Build one selected case, with the base case as the default."""
    return run_gd_statement_cases(as_of, inputs_dir, cases=(case,))[case]


def _tie(left: float, right: float, label: str) -> None:
    if not isclose(left, right, rel_tol=0, abs_tol=TOLERANCE_MILLIONS):
        raise ValueError(f"{label} does not reconcile. Calculated {left:g}, expected {right:g}.")


def _balance(assets: dict[str, float], liabilities: dict[str, float], equity: dict[str, float], shares: int) -> BalanceSheet:
    if min(assets["cash"], assets["ppe_gross"] + assets["accumulated_depreciation"], assets["intangibles"],
           assets["finance_lease_rou"], liabilities["finance_lease_liability"], liabilities["customer_advances"],
           liabilities["dividends_payable"], liabilities["debt_principal"] + liabilities["debt_costs"]) < 0 or shares <= 0:
        raise ValueError("Projected cash, capital assets, advances, debt and shares must have valid balances.")
    total_assets = fsum(assets.values())
    total_liabilities = fsum(liabilities.values())
    total_equity = fsum(equity.values())
    _tie(total_assets, total_liabilities + total_equity, "Assets and liabilities plus equity")
    working_capital = fsum(assets[key] for key in WORKING_ASSETS) - fsum(liabilities[key] for key in WORKING_LIABILITIES)
    return BalanceSheet(assets, liabilities, equity, total_assets, total_liabilities, total_equity, working_capital, shares)


def _income(operating: OperatingPeriod, other_income: float, net_interest: float, taxes: float) -> dict[str, float]:
    revenue = operating.consolidated.revenue_millions
    ebit = operating.consolidated.operating_earnings_millions
    pretax = ebit + other_income - net_interest
    return {
        "revenue": revenue, "operating_expenses": ebit - revenue, "operating_earnings": ebit,
        "other_income": other_income, "net_interest": -net_interest, "pretax": pretax,
        "tax_provision": -taxes, "net_earnings": pretax - taxes,
    }


def _cash_flow(operating: dict[str, float], investing: dict[str, float], financing: dict[str, float],
               *, other_cash: float = 0) -> CashFlowStatement:
    cfo = fsum(operating.values())
    cfi = fsum(investing.values())
    cff = fsum(financing.values())
    fcfe = cfo + investing["cash_capex"] + financing["note_draws"] + financing["note_repayments"] + financing["lease_principal"]
    return CashFlowStatement(operating, investing, financing, cfo, cfi, cff, other_cash, cfo + cfi + cff + other_cash, fcfe)


def _reported_balance(interim: GDInterimEvidence, evidence: GDStatementEvidence, financing: GDFinancing) -> BalanceSheet:
    b = evidence.balance
    start = date(interim.as_of.year - 1, 12, 31)
    ytd_fraction = (interim.period_end - start).days / (date(interim.as_of.year, 12, 31) - start).days
    lease_principal = financing.annual_lease_principal_proxy_millions
    lease_liability = evidence.lease_liability - lease_principal * ytd_fraction
    lease_rou = evidence.lease_rou_asset - evidence.ytd["lease_amortization"]
    assets = {
        **{key: b[key] for key in ("cash", *WORKING_ASSETS, "ppe_gross", "intangibles", "goodwill")},
        "accumulated_depreciation": -b["ppe_accumulated_depreciation"],
        "finance_lease_rou": lease_rou, "other_assets": b["other_assets"] - lease_rou,
    }
    liabilities = {
        "accounts_payable": b["accounts_payable"], "customer_advances": b["customer_advances"],
        "other_current_liabilities": b["other_current_liabilities"] - b["dividends_payable"] - lease_principal,
        "dividends_payable": b["dividends_payable"],
        "other_liabilities": b["other_liabilities"] - (lease_liability - lease_principal),
        "debt_principal": b["debt_carrying"] + evidence.unamortized_costs, "debt_costs": -evidence.unamortized_costs,
        "finance_lease_liability": lease_liability,
    }
    equity = {key: b[key] for key in ("paid_in_capital", "retained_earnings", "treasury_stock", "aoci")}
    opening = _balance(assets, liabilities, equity, int(interim.period_end_balances["shares_outstanding"]))
    for name in ("total_assets", "total_liabilities", "total_equity"):
        _tie(getattr(opening, name), b["equity" if name == "total_equity" else name], f"October reported {name}")
    _tie(b["cash"], interim.period_end_balances["cash"], "October reported cash")
    _tie(b["debt_carrying"], interim.period_end_balances["total_debt"], "October carrying debt")
    _tie(fsum(note.principal for note in evidence.notes) + evidence.other_principal, liabilities["debt_principal"],
         "October debt principal")
    return opening


def _roll_equity(opening: BalanceSheet, earnings: float, sbc: float, equity_cash: float,
                 dividends_declared: float, repurchases: float) -> dict[str, float]:
    return {
        **opening.equity,
        "paid_in_capital": opening.equity["paid_in_capital"] + sbc + equity_cash,
        "retained_earnings": opening.equity["retained_earnings"] + earnings - dividends_declared,
        "treasury_stock": opening.equity["treasury_stock"] - repurchases,
    }


def forecast_gd_statements(
    interim: GDInterimEvidence, operations: GDOperatingForecast, cash_forecast: GDQ4CashForecast,
    financing: GDFinancing, evidence: GDStatementEvidence, assumptions: GDStatementAssumptions, *, case: str = "base",
) -> GDForwardStatements:
    as_of = interim.as_of
    if (not as_of == operations.as_of == cash_forecast.as_of == financing.as_of == evidence.as_of == assumptions.as_of
            or not interim.period_end == operations.interim_period_end == cash_forecast.interim_period_end == evidence.period_end
            or (evidence.sources["interim"].url, evidence.sources["interim"].available_on) != (interim.filing.url, interim.filing.available_on)):
        raise ValueError("Statement inputs must share the cutoff, reported quarter and filing.")
    if case not in FISCAL_CASH_CASE or case not in operations.cases:
        raise ValueError(f"Unknown GD operating case {case!r}.")
    fiscal_cash = FISCAL_CASH_CASE[case]
    opening = _reported_balance(interim, evidence, financing)
    cash_inputs = apply_gd_financing(interim, cash_inputs_for_fiscal_case(cash_forecast, fiscal_cash), financing)
    bridge = build_gd_cash_bridge(GDCashBalances(interim.period_end, opening.assets["cash"],
                                  evidence.balance["debt_carrying"], opening.shares_outstanding), as_of, cash_inputs)
    if bridge.year_end is None or bridge.cutoff is None or bridge.remaining_fcfe is None:
        raise ValueError("The statements need a complete Q4 cash bridge.")

    q4_cash = {key: entry.amount + cash_inputs.remaining.amounts[key].amount for key, entry in cash_inputs.elapsed.amounts.items()}
    for window in (cash_inputs.elapsed, cash_inputs.remaining):
        if any(window.amounts[key].amount != 0 for key in (
            "debt_proceeds_millions", "debt_repayments_millions", "other_debt_carrying_change_millions",
        )):
            raise ValueError("The filed forward debt schedule requires no Q4 note borrowing or repayments.")

    year = as_of.year
    year_end = bridge.year_end.as_of
    operating_case = operations.cases[case]
    fiscal_case = cash_forecast.fiscal_cases[fiscal_cash]
    annual_earnings = fiscal_case.annual_net_earnings_millions
    _tie(operating_case.annual[year].consolidated.revenue_millions,
         fiscal_case.annual_revenue_millions, "FY2026 operating and cash cases")
    annual_pretax = operating_case.annual[year].consolidated.operating_earnings_millions + assumptions.other_income - interim.guidance.net_interest_expense
    fy_income = _income(operating_case.annual[year], assumptions.other_income, interim.guidance.net_interest_expense,
                        annual_pretax - annual_earnings)
    ytd = evidence.ytd
    _tie(interim.ytd["operating_earnings"] + ytd["other_income"] - interim.ytd["net_interest_expense"],
         ytd["pretax"], "Nine-month pretax earnings")
    _tie(ytd["pretax"] - ytd["taxes"], interim.ytd["net_earnings"], "Nine-month net earnings")
    q4_income = _income(operating_case.q4, assumptions.other_income - ytd["other_income"],
                        interim.guidance.net_interest_expense - interim.ytd["net_interest_expense"],
                        annual_pretax - annual_earnings - ytd["taxes"])
    _tie(q4_income["net_earnings"] + interim.ytd["net_earnings"], annual_earnings, "FY2026 earnings")

    q4_days = (year_end - interim.period_end).days
    ytd_days = (interim.period_end - date(year - 1, 12, 31)).days
    q3_days = (interim.period_end - financing.q2_end).days
    capex = q4_cash["capital_expenditures_millions"]
    depreciation = ytd["depreciation"] * q4_days / ytd_days
    intangible_amortization = evidence.intangible_amortization[year] - ytd["intangible_amortization"]
    lease_amortization = ytd["lease_amortization"] * q4_days / ytd_days
    sbc = ytd["sbc"] * q4_days / ytd_days
    q4_non_cash = depreciation + intangible_amortization + lease_amortization + sbc
    q4_earnings = q4_income["net_earnings"]
    q4_wc_change = q4_earnings + q4_non_cash - q4_cash["cash_from_operations_millions"]
    lease_principal = -q4_cash["other_fcfe_cash_flows_millions"]
    dividends_paid = q4_cash["dividends_paid_millions"]
    dividend_per_quarter = interim.scheduled_dividend.usd_per_share
    dividends_declared = dividend_per_quarter * bridge.year_end.shares_outstanding / 1_000_000
    repurchases = q4_cash["share_repurchases_millions"]
    other_investing_cash = financing.q3_nonfinancing_cash_millions * q4_days / q3_days
    equity_cash = q4_cash["other_cash_movements_millions"] + q4_cash["share_issuance_proceeds_millions"] - other_investing_cash

    q4_flow = _cash_flow(
        {"net_earnings": q4_earnings, "depreciation": depreciation, "intangible_amortization": intangible_amortization,
         "lease_amortization": lease_amortization, "stock_compensation": sbc, "change_in_operating_working_capital": -q4_wc_change},
        {"cash_capex": -capex},
        {"note_draws": 0, "note_repayments": 0, "lease_principal": -lease_principal,
         "dividends_paid": -dividends_paid, "share_repurchases": -repurchases, "other_net_equity_cash": equity_cash},
        other_cash=other_investing_cash,
    )
    q4_balance = _balance(
        {**opening.assets, "cash": opening.assets["cash"] + q4_flow.net_change_in_cash,
         "ppe_gross": opening.assets["ppe_gross"] + capex, "accumulated_depreciation": opening.assets["accumulated_depreciation"] - depreciation,
         "intangibles": opening.assets["intangibles"] - intangible_amortization,
         "finance_lease_rou": opening.assets["finance_lease_rou"] - lease_amortization,
         "other_assets": opening.assets["other_assets"] - other_investing_cash},
        {**opening.liabilities, "customer_advances": opening.liabilities["customer_advances"] - q4_wc_change,
         "finance_lease_liability": opening.liabilities["finance_lease_liability"] - lease_principal,
         "dividends_payable": opening.liabilities["dividends_payable"] + dividends_declared - dividends_paid},
        _roll_equity(opening, q4_earnings, sbc, equity_cash, dividends_declared, repurchases), bridge.year_end.shares_outstanding,
    )
    _tie(q4_flow.cash_from_operations, q4_cash["cash_from_operations_millions"], "Q4 operating cash")
    _tie(q4_flow.cash_from_operations, fiscal_case.q4.cash_from_operations_millions, "Q4 fiscal-case operating cash")
    _tie(capex, fiscal_case.q4.capital_expenditures_millions, "Q4 fiscal-case capex")
    _tie(q4_balance.assets["cash"], bridge.year_end.cash_millions, "Q4 closing cash")
    _tie(q4_balance.net_operating_working_capital - opening.net_operating_working_capital, q4_wc_change, "Q4 working capital")
    q4_shares_bought = round(financing.q3_program_shares * financing.repurchase_pace / q3_days * q4_days)
    authorization = financing.q3_program_shares_remaining - q4_shares_bought
    q4 = StatementPeriod(year_end, q4_income, q4_balance, q4_flow, {
        "gulfstream_deliveries": operating_case.q4.deliveries,
        "dividends_declared": dividends_declared, "program_shares_purchased": q4_shares_bought,
        "net_non_program_shares": q4_balance.shares_outstanding - opening.shares_outstanding + q4_shares_bought,
        "remaining_repurchase_authorization": authorization,
    })

    annual = []
    previous = q4_balance
    prior_revenue = fy_income["revenue"]
    q3_share_additions = opening.shares_outstanding - financing.q2_shares + financing.q3_program_shares
    purchase_price = financing.q3_repurchases_millions * 1_000_000 / financing.q3_program_shares
    for offset in range(1, 6):
        next_year = year + offset
        period = operating_case.annual[next_year]
        projected = _forecast_year(
            next_year, offset, period, prior_revenue, previous, evidence, assumptions,
            interim, q3_share_additions / q3_days * financing.non_program_share_pace,
            dividend_per_quarter, purchase_price, authorization,
        )
        annual.append(projected)
        previous = projected.balance_sheet
        prior_revenue = projected.income["revenue"]
        authorization = int(projected.schedules["remaining_repurchase_authorization"])

    dividend = interim.scheduled_dividend
    payable_to_prior_holders = dividends_paid if dividend.record_date < as_of < dividend.payment_date else 0
    return GDForwardStatements(as_of, case, interim.period_end, opening, fy_income, q4, bridge.cutoff.shares_outstanding,
                               bridge.cutoff.cash_millions, bridge.cutoff.carrying_debt_millions, payable_to_prior_holders,
                               bridge.remaining_fcfe, tuple(annual), evidence, assumptions, cash_forecast.outlook.source,
                               financing.q3_source, (
        "October consolidated totals are filed. The finance lease split uses prior-year balances and estimated runoff.",
        f"Q4 uses the {case} operating case and {fiscal_cash} fiscal cash pairing, with a shared elapsed cash estimate. "
        "The operating cash residual is allocated to customer advances as a modeling estimate. "
        "Q4 equity cash follows the Q3 other financing run rate with modeled lease principal separated. "
        "Other investing and discontinued cash changes other assets.",
        "The November dividend clears the October payable. A December declaration is estimated at the same quarterly rate.",
        "Future statements use the reported note maturities, no refinancing, and a cash coupon proxy for book interest before debt cost amortization. "
        "Cash taxes equal modeled book taxes. Other balances are held flat where no schedule is modeled.",
        "Annual repurchases offset estimated net share additions up to the existing authorization. "
        "FCFE includes finance lease principal, and shareholder distributions change closing cash separately. "
        "Operating cash includes the stock compensation add-back, with share movements tracked in the equity schedule.",
    ))


def _forecast_year(
    year: int, offset: int, operating: OperatingPeriod, prior_revenue: float, opening: BalanceSheet,
    evidence: GDStatementEvidence, assumptions: GDStatementAssumptions, interim: GDInterimEvidence,
    daily_share_additions: float, quarterly_dividend: float, share_price: float, authorization: int,
) -> StatementPeriod:
    end = date(year, 12, 31)
    days = (end - date(year - 1, 12, 31)).days
    sales = operating.consolidated.revenue_millions
    capex = sales * assumptions.capex_share[offset - 1]
    gross_ppe = opening.assets["ppe_gross"] + capex
    depreciation = (opening.assets["ppe_gross"] + gross_ppe) / 2 * assumptions.depreciation_rate
    intangible = min(opening.assets["intangibles"], evidence.intangible_amortization.get(year, assumptions.final_intangible_amortization))
    rou_amortization = min(opening.assets["finance_lease_rou"], assumptions.annual_finance_rou_amortization)
    sbc = sales * evidence.ytd["sbc"] / interim.ytd["revenue"]
    principal_repaid = fsum(note.principal for note in evidence.notes if note.maturity.year == year)
    coupons = fsum(note.principal * note.coupon * (0.5 if note.maturity.year == year and note.maturity.month <= 6 else 1)
                   for note in evidence.notes if note.maturity.year >= year)
    _tie(opening.liabilities["debt_principal"], evidence.other_principal + fsum(
        note.principal for note in evidence.notes if note.maturity.year >= year), f"FY{year} opening debt principal")
    debt_cost_amortization = min(-opening.liabilities["debt_costs"], assumptions.annual_debt_cost_amortization)
    lease_balance = opening.liabilities["finance_lease_liability"]
    lease_interest = lease_balance * evidence.lease_rate
    lease_payment = min(lease_balance + lease_interest, evidence.lease_payments.get(year, assumptions.final_lease_payment))
    lease_principal = lease_payment - lease_interest
    if lease_principal < 0:
        raise ValueError(f"FY{year} lease payments must cover modeled interest.")
    interest_income = opening.assets["cash"] * assumptions.cash_yield
    net_interest = coupons + evidence.other_principal * assumptions.other_debt_coupon + lease_interest + debt_cost_amortization - interest_income
    pretax = operating.consolidated.operating_earnings_millions + assumptions.other_income - net_interest
    income = _income(operating, assumptions.other_income, net_interest, pretax * assumptions.book_tax_rate[offset - 1])

    assets = {**opening.assets, "ppe_gross": gross_ppe, "accumulated_depreciation": opening.assets["accumulated_depreciation"] - depreciation,
              "intangibles": opening.assets["intangibles"] - intangible, "finance_lease_rou": opening.assets["finance_lease_rou"] - rou_amortization}
    liabilities = {**opening.liabilities, "debt_principal": opening.liabilities["debt_principal"] - principal_repaid,
                   "debt_costs": opening.liabilities["debt_costs"] + debt_cost_amortization,
                   "finance_lease_liability": lease_balance - lease_principal}
    scale = sales / prior_revenue
    for key in ("receivables", "unbilled", "inventory"):
        assets[key] *= scale
    for key in ("accounts_payable", "customer_advances"):
        liabilities[key] *= scale
    nwc = fsum(assets[key] for key in WORKING_ASSETS) - fsum(liabilities[key] for key in WORKING_LIABILITIES)
    wc_change = nwc - opening.net_operating_working_capital
    share_additions = round(daily_share_additions * days)
    shares_bought = min(max(0, share_additions), authorization)
    shares = opening.shares_outstanding + share_additions - shares_bought
    repurchase_price = share_price * (1 + assumptions.buyback_price_growth) ** offset
    buyback = shares_bought * repurchase_price / 1_000_000
    dps = quarterly_dividend * (1 + assumptions.dividend_growth) ** offset
    dividends_payable = dps * shares / 1_000_000
    dividends_paid = opening.liabilities["dividends_payable"] + 3 * dps * (opening.shares_outstanding + shares) / 2_000_000
    dividends_declared = dividends_paid - opening.liabilities["dividends_payable"] + dividends_payable
    liabilities["dividends_payable"] = dividends_payable
    cash = _cash_flow(
        {"net_earnings": income["net_earnings"], "depreciation": depreciation, "intangible_amortization": intangible,
         "lease_amortization": rou_amortization, "stock_compensation": sbc, "debt_cost_amortization": debt_cost_amortization,
         "change_in_operating_working_capital": -wc_change},
        {"cash_capex": -capex},
        {"note_draws": 0, "note_repayments": -principal_repaid, "lease_principal": -lease_principal,
         "dividends_paid": -dividends_paid, "share_repurchases": -buyback},
    )
    assets["cash"] = opening.assets["cash"] + cash.net_change_in_cash
    balance = _balance(assets, liabilities, _roll_equity(opening, income["net_earnings"], sbc, 0, dividends_declared, buyback), shares)
    return StatementPeriod(end, income, balance, cash, {
        "gulfstream_deliveries": operating.deliveries,
        "capex_share_of_sales": assumptions.capex_share[offset - 1], "working_capital_investment": wc_change,
        "note_cash_coupons": coupons, "interest_income": interest_income, "lease_interest": lease_interest,
        "dividends_declared": dividends_declared, "dividend_per_share_quarterly": dps,
        "net_non_program_shares": share_additions, "program_shares_purchased": shares_bought,
        "repurchase_price_per_share": repurchase_price, "remaining_repurchase_authorization": authorization - shares_bought,
    })

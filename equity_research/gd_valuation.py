"""Value a dated GD operating case using distributable equity cash flow."""

from dataclasses import dataclass
from datetime import date
from math import fsum
from pathlib import Path
from typing import Callable

from .gd_forward_statements import (
    GDForwardStatements, WORKING_ASSETS, WORKING_LIABILITIES, run_gd_statements,
)
from .gd_inputs import InputSource, _day, _fields, _number, _read, _source
from .gd_run_inputs import DatedClose, MarketInputs, _header, _public_url, load_market_inputs
from .valuation import FcfeDiscount, FcfeStub, ForecastCashFlow, discount_fcfe_with_stub


@dataclass(frozen=True)
class ObservedInput:
    value: float
    observed_on: date
    source: InputSource


@dataclass(frozen=True)
class GDValuationInputs:
    as_of: date
    risk_free_rate: ObservedInput
    equity_risk_premium: ObservedInput
    unlevered_beta: ObservedInput
    relevering_tax_rate: float
    terminal_growth: float
    operating_cash_share: float
    cost_of_equity_step: float
    terminal_growth_step: float
    sources: dict[str, InputSource]


@dataclass(frozen=True)
class CashFlowAdjustment:
    period_end: date
    statement_fcfe_millions: float
    stock_compensation_millions: float
    after_tax_cash_interest_adjustment_millions: float
    operating_cash_investment_millions: float

    @property
    def adjusted_fcfe_millions(self) -> float:
        return (self.statement_fcfe_millions - self.stock_compensation_millions
                - self.after_tax_cash_interest_adjustment_millions - self.operating_cash_investment_millions)


@dataclass(frozen=True)
class TerminalCashFlow:
    period_end: date
    sales_millions: float
    operating_earnings_millions: float
    note_interest_millions: float
    finance_lease_interest_millions: float
    operating_cash_interest_millions: float
    tax_millions: float
    net_ppe_investment_millions: float
    working_capital_investment_millions: float
    operating_cash_investment_millions: float
    net_borrowing_millions: float
    fcfe_millions: float


@dataclass(frozen=True)
class GDValuation:
    as_of: date
    case: str
    close: DatedClose
    shares_millions: float
    market_cap_millions: float
    cutoff_debt_millions: float
    debt_to_market_equity: float
    levered_beta: float
    cost_of_equity: float
    cutoff_cash_millions: float
    opening_operating_cash_millions: float
    dividend_payable_to_prior_holders_millions: float
    net_nonoperating_cash_millions: float
    adjustments: tuple[CashFlowAdjustment, ...]
    terminal: TerminalCashFlow
    discount: FcfeDiscount
    common_equity_value_millions: float
    intrinsic_value_per_share: float
    premium_to_reference_close: float
    terminal_share_of_equity_value: float
    sensitivity_cost_of_equity: tuple[float, ...]
    sensitivity_terminal_growth: tuple[float, ...]
    sensitivity_value_per_share: tuple[tuple[float | None, ...], ...]
    inputs: GDValuationInputs
    statement_sources: dict[str, InputSource]
    notes: tuple[str, ...]


def _observation(raw: object, name: str, as_of: date) -> ObservedInput:
    item = _fields(raw, name, {"value", "observed_on", "source"})
    observed_on = _day(item["observed_on"], f"{name} observed_on")
    source = _source(item["source"], f"{name} source", as_of)
    if not observed_on <= source.available_on <= as_of:
        raise ValueError(f"{name} must have been observed and available by the cutoff.")
    if source.kind != "market_data" or not _public_url(source.url):
        raise ValueError(f"{name} requires a public market data source.")
    return ObservedInput(_number(item["value"], name), observed_on, source)


def load_gd_valuation_inputs(path: Path) -> GDValuationInputs:
    """Load the sourced CAPM observations and explicit valuation assumptions."""
    observations = ("risk_free_rate", "equity_risk_premium", "unlevered_beta")
    ratios = ("relevering_tax_rate", "terminal_growth", "operating_cash_share",
              "cost_of_equity_step", "terminal_growth_step")
    item = _fields(_read(path), "GD valuation inputs",
                   {"schema_version", "ticker", "currency", "as_of", "sources", *observations, *ratios})
    as_of = _header(item)
    risk_free, premium, beta = (_observation(item[name], name, as_of) for name in observations)
    tax, growth, cash_share, rate_step, growth_step = (_number(item[name], name) for name in ratios)
    if not (0 < risk_free.value < 1 and 0 < premium.value < 1 and beta.value > 0):
        raise ValueError("The risk-free rate, equity risk premium and beta must be positive. Supply rates as decimals.")
    if not (0 <= tax < 1 and 0 <= growth < risk_free.value and 0 <= cash_share < 1):
        raise ValueError("Tax and operating cash ratios must be in [0, 1). Stable growth must be below the risk-free rate.")
    if not (0 < rate_step < 1 and 0 < growth_step < 1):
        raise ValueError("Sensitivity steps must be positive decimal rates below 1.")
    source_items = _fields(item["sources"], "valuation sources",
                           {"relevering_tax_rate", "terminal_growth", "operating_cash_share", "normalization", "equity_awards"})
    sources = {key: _source(raw, f"{key} source", as_of) for key, raw in source_items.items()}
    if any(not _public_url(source.url) or source.kind not in {"model_assumption", "sec_filing"}
           for source in sources.values()):
        raise ValueError("Valuation policies require public source links.")
    return GDValuationInputs(as_of, risk_free, premium, beta, tax, growth, cash_share, rate_step, growth_step, sources)


def _cash_flow_adjustments(
    statements: GDForwardStatements, operating_cash_share: float,
) -> tuple[CashFlowAdjustment, ...]:
    q4 = statements.q4_2026
    stub = statements.remaining_fcfe
    stub_days = (stub.period_end - stub.as_of).days
    q4_days = (stub.period_end - statements.reported_balance_date).days
    cash_reserve = statements.fy2026_income["revenue"] * operating_cash_share
    cash_yield = statements.assumptions.cash_yield
    cutoff_surplus = statements.cutoff_cash_millions - cash_reserve
    q4_after_tax = (q4.income["net_earnings"] / q4.income["pretax"] if q4.income["pretax"] > 0
                    else 1 - statements.assumptions.book_tax_rate[0])
    adjustments = [CashFlowAdjustment(
        stub.period_end, stub.fcfe_millions, q4.cash_flow.operating["stock_compensation"] * stub_days / q4_days,
        cutoff_surplus * cash_yield * stub_days / 365 * q4_after_tax, 0,
    )]
    opening_cash = q4.balance_sheet.assets["cash"]
    for period, tax_rate in zip(statements.annual, statements.assumptions.book_tax_rate, strict=True):
        new_reserve = period.income["revenue"] * operating_cash_share
        surplus_interest = (opening_cash - cash_reserve) * cash_yield
        adjustments.append(CashFlowAdjustment(
            period.period_end, period.cash_flow.fcfe_millions, period.cash_flow.operating["stock_compensation"],
            surplus_interest * (1 - tax_rate), new_reserve - cash_reserve,
        ))
        opening_cash = period.balance_sheet.assets["cash"]
        cash_reserve = new_reserve
    return tuple(adjustments)


def _terminal_cash_flow(statements: GDForwardStatements, growth: float, operating_cash_share: float) -> TerminalCashFlow:
    final = statements.annual[-1]
    assets, liabilities = final.balance_sheet.assets, final.balance_sheet.liabilities
    # Maintain the FY2031 operating margin and net operating asset intensity in the stable period.
    sales = final.income["revenue"] * (1 + growth)
    ebit = final.income["operating_earnings"] * (1 + growth)
    ppe = (assets["ppe_gross"] + assets["accumulated_depreciation"]) * growth
    scalable_wc = (fsum(assets[key] for key in WORKING_ASSETS if key != "other_current_assets")
                   - fsum(liabilities[key] for key in WORKING_LIABILITIES if key != "other_current_liabilities"))
    wc = scalable_wc * growth
    operating_cash = final.income["revenue"] * operating_cash_share
    # Refinance maturities at the surviving book's blended coupon and grow principal with sales.
    notes = statements.evidence.notes
    current_coupon = fsum(note.principal * note.coupon for note in notes if note.maturity > final.period_end)
    current_coupon += statements.evidence.other_principal * statements.assumptions.other_debt_coupon
    interest = current_coupon * (1 + growth / 2)
    lease_interest = liabilities["finance_lease_liability"] * statements.evidence.lease_rate
    cash_interest = operating_cash * statements.assumptions.cash_yield
    pretax = ebit - interest - lease_interest + cash_interest
    tax = pretax * statements.assumptions.book_tax_rate[-1]
    net_borrowing = liabilities["debt_principal"] * growth
    reserve_investment = operating_cash * growth
    fcfe = pretax - tax - ppe - wc - reserve_investment + net_borrowing
    if fcfe <= 0:
        raise ValueError("The normalized first-year terminal cash flow must be positive.")
    return TerminalCashFlow(
        date(final.period_end.year + 1, final.period_end.month, final.period_end.day), sales, ebit, interest,
        lease_interest, cash_interest, tax, ppe, wc, reserve_investment, net_borrowing, fcfe,
    )


def gd_sensitivity_grid(
    statements: GDForwardStatements, inputs: GDValuationInputs, rates: tuple[float, ...], growth_rates: tuple[float, ...],
    price_at: Callable[[float, float, float], float],
) -> tuple[tuple[float | None, ...], ...]:
    """Rebuild terminal cash flow across the growth axis and revalue each valid cell."""
    terminal_by_growth = {
        g: _terminal_cash_flow(statements, g, inputs.operating_cash_share).fcfe_millions
        for g in growth_rates if 0 <= g < inputs.risk_free_rate.value and g < max(rates)
    }
    return tuple(tuple(
        price_at(rate, g, terminal_by_growth[g]) if g in terminal_by_growth and g < rate else None
        for g in growth_rates
    ) for rate in rates)


def value_gd(statements: GDForwardStatements, market: MarketInputs, inputs: GDValuationInputs) -> GDValuation:
    """Discount the selected case's adjusted FCFE and calculate the rate-by-growth sensitivity."""
    as_of = statements.as_of
    close = market.valuation_close
    if inputs.as_of != as_of or market.as_of != as_of or close is None:
        raise ValueError("The statements, assumptions and cited share price must share the valuation cutoff.")
    if not statements.reported_balance_date <= close.observed_on == inputs.risk_free_rate.observed_on < as_of:
        raise ValueError("A pre-open valuation requires the Treasury yield and share price on the same prior trading date.")
    shares = statements.cutoff_shares / 1_000_000
    market_cap = close.price * shares
    debt_equity = statements.cutoff_debt_millions / market_cap
    beta = inputs.unlevered_beta.value * (1 + (1 - inputs.relevering_tax_rate) * debt_equity)
    cost_of_equity = inputs.risk_free_rate.value + beta * inputs.equity_risk_premium.value
    growth = inputs.terminal_growth
    cash_share = inputs.operating_cash_share
    reserve = statements.fy2026_income["revenue"] * cash_share
    dividend = statements.cutoff_dividend_payable_millions
    net_cash = statements.cutoff_cash_millions - reserve - dividend
    adjustments = _cash_flow_adjustments(statements, cash_share)
    stub = FcfeStub(as_of, adjustments[0].period_end, adjustments[0].adjusted_fcfe_millions)
    annual = tuple(ForecastCashFlow(row.period_end, row.adjusted_fcfe_millions) for row in adjustments[1:])
    terminal = _terminal_cash_flow(statements, growth, cash_share)
    dcf = discount_fcfe_with_stub(stub, annual, as_of, cost_of_equity, growth, shares,
                                 terminal_fcfe_millions=terminal.fcfe_millions)
    equity = dcf.equity_present_value_millions + net_cash
    per_share = equity / shares

    rates = tuple(cost_of_equity + step * inputs.cost_of_equity_step for step in range(-3, 4))
    growth_rates = tuple(growth + step * inputs.terminal_growth_step for step in range(-3, 4))

    def price_at(rate: float, g: float, terminal_fcfe: float) -> float:
        pv = discount_fcfe_with_stub(stub, annual, as_of, rate, g, shares, terminal_fcfe_millions=terminal_fcfe)
        return (pv.equity_present_value_millions + net_cash) / shares

    grid = gd_sensitivity_grid(statements, inputs, rates, growth_rates, price_at)
    if grid[3][3] != per_share:
        raise ValueError("The center sensitivity cell must reproduce the selected valuation.")
    return GDValuation(
        as_of, statements.case, close, shares, market_cap, statements.cutoff_debt_millions, debt_equity, beta,
        cost_of_equity, statements.cutoff_cash_millions, reserve, dividend, net_cash, adjustments, terminal,
        dcf, equity, per_share, per_share / close.price - 1, dcf.terminal_present_value_millions / equity,
        rates, growth_rates, grid, inputs, {
            **statements.evidence.sources,
            "cash_outlook": statements.cash_outlook_source,
            "cutoff_financing": statements.financing_source,
        }, (
            "ACT/365 period-end discounting. The remaining 2026 cash flow uses the modeled cutoff through December 31.",
            "Stock compensation is a cash-equivalent expense. The denominator is modeled cutoff common shares. "
            "Separate cutoff option overhang could not be estimated from a current option roll-forward.",
            "Cash required for operations grows with sales. Strip interest on surplus cash and value the cutoff cash "
            "net of the operating reserve and dividend owed to prior holders. Charge any reserve shortfall at cutoff "
            "and credit the modeled yield on the funded reserve. Q4 cash interest and SBC use time estimates.",
            "The stable year preserves the FY2031 EBIT margin with SBC, intangible amortization and finance lease ROU "
            "amortization as economic costs. This assumes cash replacement of amortizing assets and leases. "
            "Exclude noninterest other income. Scale net PP&E, core working capital and the cash reserve with sales.",
            "The annual forecast honors filed maturities. In the stable period, refinance remaining Note H debt at the "
            "surviving blended coupon and grow principal with sales. Include finance lease interest at its modeled run rate.",
            "The sensitivity recalculates stable-year reinvestment and net borrowing for each growth assumption. "
            "Blank cells fall outside the stable-growth assumptions or have growth at or above the cost of equity.",
            "The price comparison uses the preceding session close. Use target-gd for the separately timed one-year "
            "valuation and compare-gd for trailing spot peer cross-checks.",
        ),
    )


def run_gd_valuation(as_of: date, inputs_dir: Path, *, case: str = "base") -> GDValuation:
    """Rebuild the statements and value the same dated source snapshot."""
    inputs = load_gd_valuation_inputs(inputs_dir / f"gd-valuation-{as_of.isoformat()}.json")
    market = load_market_inputs(inputs_dir / f"gd-market-{as_of.isoformat()}.json")
    return value_gd(run_gd_statements(as_of, inputs_dir, case=case), market, inputs)

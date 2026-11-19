"""Value a captive lender's common equity from projected distributable earnings."""

from dataclasses import dataclass
from math import isfinite

from .distributable_equity import discount_common_equity
from .operating_research import ResearchGap


@dataclass(frozen=True)
class FinanceYear:
    year: int
    net_receivables: float
    leased_vehicles: float
    common_earnings: float
    opening_common_capital: float
    closing_common_capital: float
    distributable_earnings: float
    residual_income: float


@dataclass(frozen=True)
class FinanceValuation:
    forecast: tuple[FinanceYear, ...]
    common_equity_value: float
    residual_income_value: float
    common_earnings_yield: float
    common_capital_ratio: float
    receivables_growth: float
    leased_vehicles_growth: float
    cost_of_equity: float
    terminal_growth: float


def value_captive_finance(evidence, *, cost_of_equity, terminal_growth):
    """Forecast common distributions and reconcile dividend and residual-income values.

    The model uses the filed balance date as its anchor. Opening common capital is
    total equity less the preferred liquidation claim, an economic capital proxy.
    New capital is retained to maintain that ratio relative to earning assets.
    Currency values are in millions.
    """
    if not 0 <= terminal_growth < cost_of_equity < 1:
        raise ResearchGap("The finance terminal growth rate must be below the cost of common equity.")
    opening = evidence["earning_assets"]
    prior = evidence["prior_earning_assets"]
    # Preserve distinct drivers for the loan book and leased vehicles.
    loans, vehicles = opening["net_receivables_millions"], opening["leased_vehicles_millions"]
    prior_loans, prior_vehicles = prior["net_receivables_millions"], prior["leased_vehicles_millions"]
    common_capital = evidence["common_capital_proxy_millions"]
    earnings = evidence["trailing_common_earnings_millions"]
    if any(not isfinite(value) or value <= 0 for value in (
        loans, vehicles, prior_loans, prior_vehicles, common_capital, earnings,
    )):
        raise ResearchGap("The finance valuation needs positive capital, earnings, and earning-asset balances.")
    starting_capital = common_capital
    starting_assets = loans + vehicles
    capital_ratio = common_capital / starting_assets
    yield_on_assets = earnings / ((starting_assets + prior_loans + prior_vehicles) / 2)
    loan_growth = loans / prior_loans - 1
    lease_growth = vehicles / prior_vehicles - 1
    forecasts = []
    for year in range(1, 6):
        old_assets = loans + vehicles
        loans *= 1 + terminal_growth + (loan_growth - terminal_growth) * .5 ** year
        vehicles *= 1 + terminal_growth + (lease_growth - terminal_growth) * .5 ** year
        earning_assets = loans + vehicles
        common_income = (old_assets + earning_assets) / 2 * yield_on_assets
        closing_capital = earning_assets * capital_ratio
        distributions = common_income - (closing_capital - common_capital)
        if distributions < 0:
            raise ResearchGap("The finance forecast needs additional common capital at the assumed growth rate.")
        forecasts.append(FinanceYear(year, loans, vehicles, common_income, common_capital, closing_capital,
                                     distributions, common_income - common_capital * cost_of_equity))
        common_capital = closing_capital
    terminal_income = (loans + vehicles) * (1 + terminal_growth / 2) * yield_on_assets
    terminal_distributions = terminal_income - common_capital * terminal_growth
    terminal_residual = terminal_income - common_capital * cost_of_equity
    if terminal_distributions <= 0:
        raise ResearchGap("The finance terminal case does not support positive common distributions.")
    dividend_value, residual_value = discount_common_equity(
        starting_capital, tuple(row.distributable_earnings for row in forecasts),
        tuple(row.residual_income for row in forecasts), terminal_distributions,
        terminal_residual, cost_of_equity, terminal_growth,
    )
    return FinanceValuation(tuple(forecasts), dividend_value, residual_value, yield_on_assets, capital_ratio,
                            loan_growth, lease_growth, cost_of_equity, terminal_growth)

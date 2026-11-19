"""Value a linked operating forecast using explicit cutoff and terminal assumptions."""

from dataclasses import dataclass
from datetime import date
from math import fsum, isclose, isfinite
from typing import Sequence

from .fcff_valuation import (
    DatedFCFF, EquityBridge, FCFFValuation, TerminalAssumptions, discount_fcff, fcff_sensitivity,
)
from .linked_forecast import ForecastBalance, ForecastPeriod, PeriodAssumptions, forecast_periods


@dataclass(frozen=True)
class CostOfCapitalInputs:
    """Rates are decimals. Market capital values are in USD millions."""

    as_of: date
    risk_free_rate: float
    equity_risk_premium: float
    levered_beta: float
    common_equity_market_value_millions: float
    pretax_cost_of_debt: float
    debt_market_value_millions: float
    marginal_tax_rate: float
    preferred_cost: float
    preferred_market_value_millions: float


@dataclass(frozen=True)
class CostOfCapital:
    inputs: CostOfCapitalInputs
    cost_of_equity: float
    after_tax_cost_of_debt: float
    equity_weight: float
    debt_weight: float
    preferred_weight: float
    wacc: float


@dataclass(frozen=True)
class OperatingValuation:
    periods: tuple[ForecastPeriod, ...]
    cost_of_capital: CostOfCapital
    valuation: FCFFValuation
    sensitivity: tuple[tuple[float | None, ...], ...]
    funding_gap_periods: tuple[date, ...]


def calculate_cost_of_capital(inputs: CostOfCapitalInputs) -> CostOfCapital:
    """Use market-value weights and retain the preferred class when applicable."""
    if type(inputs.as_of) is not date:
        raise ValueError("Cost of capital requires a valuation date.")
    for field, value in vars(inputs).items():
        if field == "as_of":
            continue
        if type(value) not in (float, int) or not isfinite(value):
            raise ValueError(f"{field} must be finite.")
        if value < 0 and field not in {"risk_free_rate", "levered_beta"}:
            raise ValueError(f"{field} must be nonnegative.")
    if inputs.common_equity_market_value_millions <= 0:
        raise ValueError("Common equity market capitalization must be positive.")
    if inputs.marginal_tax_rate > 1 or inputs.pretax_cost_of_debt >= 1 or inputs.preferred_cost >= 1:
        raise ValueError("Enter financing and tax rates as decimal proportions.")
    if inputs.debt_market_value_millions and not inputs.pretax_cost_of_debt:
        raise ValueError("Outstanding debt requires a positive pretax cost of debt.")
    if inputs.preferred_market_value_millions and not inputs.preferred_cost:
        raise ValueError("A preferred claim requires its own cost of capital.")

    equity_cost = inputs.risk_free_rate + inputs.levered_beta * inputs.equity_risk_premium
    debt_cost = inputs.pretax_cost_of_debt * (1 - inputs.marginal_tax_rate)
    total = fsum((inputs.common_equity_market_value_millions, inputs.debt_market_value_millions,
                  inputs.preferred_market_value_millions))
    equity = inputs.common_equity_market_value_millions / total
    debt = inputs.debt_market_value_millions / total
    preferred = inputs.preferred_market_value_millions / total
    wacc = fsum((equity * equity_cost, debt * debt_cost, preferred * inputs.preferred_cost))
    if not 0 < equity_cost < 1 or not 0 < wacc < 1:
        raise ValueError("The implied cost of equity and WACC must be positive and below 100%.")
    return CostOfCapital(inputs, equity_cost, debt_cost, equity, debt, preferred, wacc)


def value_linked_forecast(
    opening: ForecastBalance,
    assumptions: Sequence[PeriodAssumptions],
    capital: CostOfCapitalInputs,
    bridge: EquityBridge,
    terminal: TerminalAssumptions,
    *,
    wacc_rates: Sequence[float],
    growth_rates: Sequence[float],
) -> OperatingValuation:
    """Link statements, discount future FCFF, and expose any modeled funding gap.

    Opening balances are already reconciled to the valuation cutoff. Period
    assumptions begin there, with the first fiscal year supplied as a stub
    when applicable. The caller documents the separate normalized terminal
    assumptions and obtains consistent market-value capital inputs.
    """
    if opening.period_end != capital.as_of or opening.period_end != bridge.as_of:
        raise ValueError("Opening balances, WACC and equity bridge must use the same cutoff.")
    if not isclose(capital.preferred_market_value_millions, bridge.preferred_equity_millions,
                   rel_tol=1e-9, abs_tol=1e-6):
        raise ValueError("WACC and the equity bridge must use the same preferred claim.")
    if not isclose(capital.debt_market_value_millions, bridge.debt_and_finance_leases_millions,
                   rel_tol=1e-9, abs_tol=1e-6):
        raise ValueError("WACC and the equity bridge must use the same market-value debt basis.")
    if bridge.diluted_shares_millions < opening.basic_shares_millions:
        raise ValueError("Cutoff diluted shares are below the opening basic share count.")
    cost = calculate_cost_of_capital(capital)
    periods = forecast_periods(opening, assumptions)
    cash_flows = tuple(DatedFCFF(period.closing.period_end, period.cash_flow["unlevered_free_cash_flow"])
                       for period in periods)
    valuation = discount_fcff(cash_flows, opening.period_end, cost.wacc, terminal, bridge)
    sensitivity = fcff_sensitivity(cash_flows, opening.period_end, terminal, bridge,
                                   wacc_rates=wacc_rates, growth_rates=growth_rates)
    for wacc, row in zip(wacc_rates, sensitivity, strict=True):
        for growth, value in zip(growth_rates, row, strict=True):
            if wacc == cost.wacc and growth == terminal.growth_rate and value != valuation.value_per_share:
                raise ValueError("The base sensitivity cell disagrees with the calculated DCF.")
    gaps = tuple(period.closing.period_end for period in periods if period.funding_gap_millions > 0)
    return OperatingValuation(periods, cost, valuation, sensitivity, gaps)

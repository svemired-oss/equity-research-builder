"""Free cash flow to equity valuation for annual and partial periods."""

from calendar import monthrange
from dataclasses import dataclass
from datetime import date
from math import fsum, isfinite
from typing import Sequence


@dataclass(frozen=True)
class ForecastCashFlow:
    period_end: date
    fcfe_millions: float


@dataclass(frozen=True)
class FcfeStub:
    """Cash flow after as_of and through the fiscal year-end."""

    as_of: date
    period_end: date
    fcfe_millions: float


@dataclass(frozen=True)
class DiscountedCashFlow:
    period_end: date
    fcfe_millions: float
    discount_years: float
    present_value_millions: float


@dataclass(frozen=True)
class FcfeDiscount:
    periods: tuple[DiscountedCashFlow, ...]
    terminal_value_millions: float
    terminal_present_value_millions: float
    equity_present_value_millions: float
    value_per_share: float


def _check_inputs(
    cash_flows: Sequence[ForecastCashFlow],
    cost_of_equity: float,
    terminal_growth: float,
    shares_millions: float,
) -> None:
    if not isfinite(cost_of_equity) or not isfinite(terminal_growth):
        raise ValueError("Cost of equity and terminal growth must be finite.")
    if not -1 < terminal_growth < cost_of_equity:
        raise ValueError("Terminal growth must exceed -100% and be below the cost of equity.")
    if not isfinite(shares_millions) or shares_millions <= 0:
        raise ValueError("Share count must be finite and positive.")
    if not cash_flows:
        raise ValueError("At least one forecast cash flow is required.")
    if any(not isfinite(flow.fcfe_millions) for flow in cash_flows):
        raise ValueError("Forecast cash flows must be finite.")
    for prior, current in zip(cash_flows, cash_flows[1:]):
        if (current.period_end - prior.period_end).days not in (365, 366):
            raise ValueError("Forecast period ends must be consecutive annual dates.")


def value_fcfe(
    cash_flows: Sequence[ForecastCashFlow],
    valuation_date: date,
    cost_of_equity: float,
    terminal_growth: float,
    shares_millions: float,
) -> float:
    """Return equity value per share, prorating the first remaining fiscal year."""
    _check_inputs(cash_flows, cost_of_equity, terminal_growth, shares_millions)
    remaining = [flow for flow in cash_flows if flow.period_end > valuation_date]
    if not remaining:
        raise ValueError("The valuation date must precede the final forecast period end.")
    first_weight = min((remaining[0].period_end - valuation_date).days / 365, 1)
    return _discount(remaining, valuation_date, cost_of_equity, terminal_growth, shares_millions, first_weight).value_per_share


def value_fcfe_with_stub(
    stub: FcfeStub,
    annual_cash_flows: Sequence[ForecastCashFlow],
    valuation_date: date,
    cost_of_equity: float,
    terminal_growth: float,
    shares_millions: float,
) -> float:
    """Discount an explicit remaining-period FCFE followed by full fiscal years."""
    return discount_fcfe_with_stub(
        stub, annual_cash_flows, valuation_date, cost_of_equity, terminal_growth, shares_millions,
    ).value_per_share


def discount_fcfe_with_stub(
    stub: FcfeStub,
    annual_cash_flows: Sequence[ForecastCashFlow],
    valuation_date: date,
    cost_of_equity: float,
    terminal_growth: float,
    shares_millions: float,
    *,
    terminal_fcfe_millions: float | None = None,
    dated_stub_flows: Sequence[ForecastCashFlow] = (),
) -> FcfeDiscount:
    """Discount full years and a stub, optionally timing cash within the stub."""
    _check_inputs(annual_cash_flows, cost_of_equity, terminal_growth, shares_millions)
    if stub.as_of != valuation_date:
        raise ValueError("The FCFE stub must start on the valuation date.")

    def year_end(year: int) -> date:
        month, day = stub.period_end.month, stub.period_end.day
        return date(year, month, min(day, monthrange(year, month)[1]))

    if stub.period_end.year < 2 or not year_end(stub.period_end.year - 1) < valuation_date < stub.period_end:
        raise ValueError("The FCFE stub must cover a positive interval shorter than a fiscal year.")
    if type(stub.fcfe_millions) not in (int, float) or not isfinite(stub.fcfe_millions):
        raise ValueError("The FCFE stub amount must be a finite number.")
    for year, flow in enumerate(annual_cash_flows, start=stub.period_end.year + 1):
        if flow.period_end.year != year or flow.period_end != year_end(year):
            raise ValueError("Full forecast years must start after the stub and follow the same fiscal year-end.")
    dated = sorted(dated_stub_flows, key=lambda flow: flow.period_end)
    if any(not valuation_date < flow.period_end < stub.period_end or not isfinite(flow.fcfe_millions) for flow in dated):
        raise ValueError("Dated stub cash flows must be finite and fall strictly within the stub.")
    if len({flow.period_end for flow in dated}) != len(dated):
        raise ValueError("Combine dated stub cash flows falling on the same date.")
    remaining_stub = stub.fcfe_millions - fsum(flow.fcfe_millions for flow in dated)
    flows = [*dated, ForecastCashFlow(stub.period_end, remaining_stub), *annual_cash_flows]
    return _discount(flows, valuation_date, cost_of_equity, terminal_growth, shares_millions, 1, terminal_fcfe_millions)


def _discount(
    remaining: Sequence[ForecastCashFlow],
    valuation_date: date,
    cost_of_equity: float,
    terminal_growth: float,
    shares_millions: float,
    first_weight: float,
    terminal_fcfe_millions: float | None = None,
) -> FcfeDiscount:
    years = [(flow.period_end - valuation_date).days / 365 for flow in remaining]
    discount_base = 1 + cost_of_equity
    discounted_flows = [
        flow.fcfe_millions / discount_base**year
        for flow, year in zip(remaining, years)
    ]
    discounted_flows[0] *= first_weight

    if terminal_fcfe_millions is None:
        terminal_fcfe_millions = remaining[-1].fcfe_millions * (1 + terminal_growth)
    elif type(terminal_fcfe_millions) not in (float, int) or not isfinite(terminal_fcfe_millions):
        raise ValueError("First-year terminal FCFE must be a finite number.")
    terminal_value = terminal_fcfe_millions / (cost_of_equity - terminal_growth)
    discounted_terminal_value = terminal_value / discount_base**years[-1]
    equity_value = fsum(discounted_flows) + discounted_terminal_value
    value_per_share = equity_value / shares_millions
    if not isfinite(value_per_share):
        raise ValueError("The resulting equity value per share must be finite.")
    periods = tuple(
        DiscountedCashFlow(flow.period_end, flow.fcfe_millions, year, pv)
        for flow, year, pv in zip(remaining, years, discounted_flows, strict=True)
    )
    return FcfeDiscount(periods, terminal_value, discounted_terminal_value, equity_value, value_per_share)

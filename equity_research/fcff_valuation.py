"""Discount dated operating cash flows and bridge enterprise value to common equity."""

from dataclasses import dataclass, replace
from datetime import date
from math import fsum, isfinite
from typing import Sequence


@dataclass(frozen=True)
class DatedFCFF:
    period_end: date
    amount_millions: float


@dataclass(frozen=True)
class TerminalAssumptions:
    """Normalized full-year NOPAT at the end of the explicit forecast."""

    normalized_nopat_millions: float
    growth_rate: float
    marginal_roic: float


@dataclass(frozen=True)
class EquityBridge:
    """Positive asset and claim amounts measured at the valuation date."""

    as_of: date
    excess_cash_millions: float
    nonoperating_assets_millions: float
    debt_and_finance_leases_millions: float
    preferred_equity_millions: float
    minority_interests_millions: float
    other_noncommon_claims_millions: float
    diluted_shares_millions: float
    # A filed negative noncontrolling interest below 0.5% of total equity is
    # deducted at book value ("immaterial NCI at book"); larger deficits stop.
    immaterial_minority_deficit: bool = False

    @property
    def adjustment_millions(self) -> float:
        return fsum((
            self.excess_cash_millions,
            self.nonoperating_assets_millions,
            -self.debt_and_finance_leases_millions,
            -self.preferred_equity_millions,
            -self.minority_interests_millions,
            -self.other_noncommon_claims_millions,
        ))


@dataclass(frozen=True)
class DiscountedFCFF:
    cash_flow: DatedFCFF
    discount_years: float
    present_value_millions: float


@dataclass(frozen=True)
class FCFFValuation:
    as_of: date
    wacc: float
    periods: tuple[DiscountedFCFF, ...]
    terminal: TerminalAssumptions
    terminal_nopat_millions: float
    terminal_net_reinvestment_millions: float
    terminal_fcff_millions: float
    terminal_value_millions: float
    terminal_present_value_millions: float
    enterprise_value_millions: float
    bridge: EquityBridge
    equity_value_millions: float
    value_per_share: float


def _finite(value: float, label: str) -> None:
    if type(value) not in (float, int) or not isfinite(value):
        raise ValueError(f"{label} must be a finite number.")


def _check_wacc(wacc: float) -> None:
    _finite(wacc, "WACC")
    if wacc <= 0:
        raise ValueError("WACC must be positive.")


def _check_growth(growth: float) -> None:
    _finite(growth, "Terminal growth")
    if growth <= -1:
        raise ValueError("Terminal growth must exceed -100%.")


def _check_model(
    cash_flows: Sequence[DatedFCFF], as_of: date, terminal: TerminalAssumptions, bridge: EquityBridge,
) -> None:
    if type(as_of) is not date or type(bridge.as_of) is not date or bridge.as_of != as_of:
        raise ValueError("The equity bridge must use the valuation date.")
    if not cash_flows:
        raise ValueError("At least one dated forecast cash flow is required.")
    previous = as_of
    for flow in cash_flows:
        if type(flow.period_end) is not date or flow.period_end <= previous:
            raise ValueError("Cash flow dates must be strictly increasing and after the valuation date.")
        _finite(flow.amount_millions, "Forecast FCFF")
        previous = flow.period_end
    _finite(terminal.normalized_nopat_millions, "Normalized terminal NOPAT")
    _finite(terminal.marginal_roic, "Terminal marginal ROIC")
    _check_growth(terminal.growth_rate)
    if terminal.normalized_nopat_millions <= 0 or terminal.marginal_roic <= 0:
        raise ValueError("Normalized terminal NOPAT and marginal ROIC must be positive.")

    for label, amount in (
        ("Excess cash", bridge.excess_cash_millions),
        ("Nonoperating assets", bridge.nonoperating_assets_millions),
        ("Debt and finance leases", bridge.debt_and_finance_leases_millions),
        ("Preferred equity", bridge.preferred_equity_millions),
        ("Minority interests", bridge.minority_interests_millions),
        ("Other noncommon claims", bridge.other_noncommon_claims_millions),
    ):
        _finite(amount, label)
        if amount < 0 and not (label == "Minority interests" and bridge.immaterial_minority_deficit):
            raise ValueError(f"{label} must be nonnegative.")
    _finite(bridge.diluted_shares_millions, "Diluted shares")
    if bridge.diluted_shares_millions <= 0:
        raise ValueError("Diluted shares must be positive.")


def _discount(
    cash_flows: Sequence[DatedFCFF], as_of: date, wacc: float, terminal: TerminalAssumptions, bridge: EquityBridge,
) -> FCFFValuation:
    periods = []
    for flow in cash_flows:
        years = (flow.period_end - as_of).days / 365
        periods.append(DiscountedFCFF(flow, years, flow.amount_millions / (1 + wacc) ** years))

    growth = terminal.growth_rate
    nopat = terminal.normalized_nopat_millions * (1 + growth)
    reinvestment = nopat * growth / terminal.marginal_roic
    terminal_fcff = nopat - reinvestment
    terminal_value = terminal_fcff / (wacc - growth)
    terminal_pv = terminal_value / (1 + wacc) ** periods[-1].discount_years
    enterprise_value = fsum((terminal_pv, *(period.present_value_millions for period in periods)))
    equity_value = fsum((enterprise_value, bridge.adjustment_millions))
    value_per_share = equity_value / bridge.diluted_shares_millions
    _finite(value_per_share, "Common equity value per share")
    return FCFFValuation(
        as_of, wacc, tuple(periods), terminal, nopat, reinvestment, terminal_fcff,
        terminal_value, terminal_pv, enterprise_value, bridge, equity_value, value_per_share,
    )


def discount_fcff(
    cash_flows: Sequence[DatedFCFF], as_of: date, wacc: float, terminal: TerminalAssumptions, bridge: EquityBridge,
) -> FCFFValuation:
    """Discount each supplied amount once using ACT/365 and period-end timing.

    An initial stub must contain only the cash flow remaining after as_of.
    Terminal cash flow uses a full next year and the growth-implied net
    reinvestment rate. Negative terminal growth implies net disinvestment.
    """
    _check_model(cash_flows, as_of, terminal, bridge)
    _check_wacc(wacc)
    if terminal.growth_rate >= min(wacc, terminal.marginal_roic):
        raise ValueError("Terminal growth must be below WACC and terminal marginal ROIC.")
    return _discount(cash_flows, as_of, wacc, terminal, bridge)


def fcff_sensitivity(
    cash_flows: Sequence[DatedFCFF],
    as_of: date,
    terminal: TerminalAssumptions,
    bridge: EquityBridge,
    *,
    wacc_rates: Sequence[float],
    growth_rates: Sequence[float],
) -> tuple[tuple[float | None, ...], ...]:
    """Return per-share values by WACC row and terminal growth column."""
    _check_model(cash_flows, as_of, terminal, bridge)
    if not wacc_rates or not growth_rates:
        raise ValueError("Both sensitivity axes need at least one rate.")
    for wacc in wacc_rates:
        _check_wacc(wacc)
    for growth in growth_rates:
        _check_growth(growth)

    return tuple(
        tuple(
            _discount(cash_flows, as_of, wacc, replace(terminal, growth_rate=growth), bridge).value_per_share
            if growth < min(wacc, terminal.marginal_roic) else None
            for growth in growth_rates
        )
        for wacc in wacc_rates
    )

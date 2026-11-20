"""Roll forward a complete, non-overlapping set of company revenue lines."""

from dataclasses import dataclass
from datetime import date
from math import fsum, isclose, isfinite
from typing import Mapping, Sequence


@dataclass(frozen=True)
class RevenueBase:
    """Caller-classified lines for a reported fiscal year, in millions."""

    period_end: date
    reported_total_millions: float
    amounts_millions: Mapping[str, float]


@dataclass(frozen=True)
class RevenueGrowth:
    period_end: date
    growth_rates: Mapping[str, float]


@dataclass(frozen=True)
class RevenueForecastYear:
    period_end: date
    amounts_millions: Mapping[str, float]
    consolidated_millions: float


@dataclass(frozen=True)
class RevenueForecast:
    base: RevenueBase
    years: tuple[RevenueForecastYear, ...]


def _finite(value: float, label: str) -> None:
    if type(value) not in (int, float) or not isfinite(value):
        raise ValueError(f"{label} must be a finite number.")


def forecast_revenue(base: RevenueBase, assumptions: Sequence[RevenueGrowth]) -> RevenueForecast:
    """Project five full fiscal years using explicit annual growth by line.

    The caller establishes that lines have the same currency and do not
    overlap. The historical total is a control, with no balancing line added.
    """
    if type(base.period_end) is not date or len(assumptions) != 5:
        raise ValueError("Supply a dated historical base and five forecast years.")
    prior_amounts = dict(base.amounts_millions)
    if not prior_amounts or any(
        not isinstance(key, str) or not key.strip() or key != key.strip() for key in prior_amounts
    ):
        raise ValueError("Supply at least one nonblank revenue line identifier.")
    _finite(base.reported_total_millions, "Reported consolidated revenue")
    for key, amount in prior_amounts.items():
        _finite(amount, f"Historical revenue for {key}")
    historical_total = fsum(prior_amounts.values())
    if not isclose(historical_total, base.reported_total_millions, rel_tol=1e-15, abs_tol=1e-12):
        raise ValueError("Historical revenue lines do not reconcile to reported consolidated revenue.")

    historical = RevenueBase(base.period_end, base.reported_total_millions, prior_amounts)
    previous_end = base.period_end
    years = []
    for year in assumptions:
        if type(year.period_end) is not date or (year.period_end - previous_end).days not in (364, 365, 366, 371):
            raise ValueError("Forecast dates must be consecutive full fiscal year-ends.")
        if year.growth_rates.keys() != prior_amounts.keys():
            raise ValueError(f"Growth rates for {year.period_end} must cover exactly the historical revenue lines.")
        projected = {}
        for key, prior_amount in prior_amounts.items():
            growth = year.growth_rates[key]
            _finite(growth, f"Revenue growth for {key} in {year.period_end}")
            if growth < -1:
                raise ValueError(f"Revenue growth for {key} in {year.period_end} is below -100%.")
            amount = prior_amount * (1 + growth)
            _finite(amount, f"Projected revenue for {key} in {year.period_end}")
            projected[key] = amount
        consolidated = fsum(projected.values())
        years.append(RevenueForecastYear(year.period_end, projected, consolidated))
        prior_amounts, previous_end = projected, year.period_end
    return RevenueForecast(historical, tuple(years))

"""Newmont commodity revenue and production costs with explicit quantity units."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from math import isfinite


# Unit of each volume input, then conversion of volume times USD/unit to USDm.
NEM_COMMODITIES = {
    "gold": ("thousand_troy_ounces", 0.001),
    "silver": ("million_troy_ounces", 1.0),
    "copper": ("million_pounds", 1.0),
    "zinc": ("million_pounds", 1.0),
    "lead": ("million_pounds", 1.0),
}


@dataclass(frozen=True)
class CommodityAssumptions:
    production_volume: float
    sold_volume: float
    price_usd_per_unit: float
    production_cost_usd_per_unit: float


@dataclass(frozen=True)
class RevenueAssumptions:
    period_end: date
    commodities: Mapping[str, CommodityAssumptions]


@dataclass(frozen=True)
class CommodityForecast:
    unit: str
    production_volume: float
    sold_volume: float
    production_less_sales_volume: float
    price_usd_per_unit: float
    production_cost_usd_per_unit: float
    revenue_millions: float
    production_cost_millions: float


@dataclass(frozen=True)
class RevenueForecast:
    period_end: date
    commodities: dict[str, CommodityForecast]
    total_revenue_millions: float
    total_production_cost_millions: float


def _nonnegative_number(name: str, value: float) -> None:
    if type(value) not in (int, float) or not isfinite(value) or value < 0:
        raise ValueError(f"{name} must be a finite nonnegative number.")


def forecast_nem_revenue(inputs: RevenueAssumptions) -> RevenueForecast:
    """Calculate annual revenue from sales and costs from production.

    Physical volumes use NEM_COMMODITIES units, and prices and unit costs use
    USD per ounce or pound. Production and sold volume must have a comparable
    ownership and consolidation basis. Their difference is a diagnostic that
    needs an inventory and ownership bridge before use in financial statements.
    """
    period_end = inputs.period_end
    if type(period_end) is not date or (period_end.month, period_end.day) != (12, 31):
        raise ValueError("Newmont annual revenue requires a December 31 fiscal year-end.")
    missing = NEM_COMMODITIES.keys() - inputs.commodities.keys()
    unexpected = inputs.commodities.keys() - NEM_COMMODITIES.keys()
    if missing or unexpected:
        raise ValueError(f"Newmont commodities mismatch. Missing: {sorted(missing)}. Unexpected: {sorted(unexpected)}.")

    commodities = {}
    for commodity, (unit, conversion) in NEM_COMMODITIES.items():
        assumptions = inputs.commodities[commodity]
        _nonnegative_number(f"{commodity} production volume", assumptions.production_volume)
        _nonnegative_number(f"{commodity} sold volume", assumptions.sold_volume)
        _nonnegative_number(f"{commodity} price", assumptions.price_usd_per_unit)
        _nonnegative_number(f"{commodity} unit production cost", assumptions.production_cost_usd_per_unit)
        revenue = (assumptions.sold_volume * conversion) * assumptions.price_usd_per_unit
        production_cost = (assumptions.production_volume * conversion) * assumptions.production_cost_usd_per_unit
        _nonnegative_number(f"{commodity} forecast revenue", revenue)
        _nonnegative_number(f"{commodity} forecast production cost", production_cost)
        commodities[commodity] = CommodityForecast(
            unit=unit,
            production_volume=assumptions.production_volume,
            sold_volume=assumptions.sold_volume,
            production_less_sales_volume=assumptions.production_volume - assumptions.sold_volume,
            price_usd_per_unit=assumptions.price_usd_per_unit,
            production_cost_usd_per_unit=assumptions.production_cost_usd_per_unit,
            revenue_millions=revenue,
            production_cost_millions=production_cost,
        )

    total_revenue = sum(commodity.revenue_millions for commodity in commodities.values())
    total_production_cost = sum(commodity.production_cost_millions for commodity in commodities.values())
    _nonnegative_number("Total forecast revenue", total_revenue)
    _nonnegative_number("Total forecast production cost", total_production_cost)
    return RevenueForecast(period_end, commodities, total_revenue, total_production_cost)

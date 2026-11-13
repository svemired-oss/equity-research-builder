"""Build FedEx revenue on the legacy May fiscal-year reporting basis."""

from calendar import isleap
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from math import isfinite


FDX_SERVICES = {
    "us_priority": "package",
    "us_deferred": "package",
    "us_ground": "package",
    "international_export": "package",
    "international_domestic": "package",
    "express_us_freight": "pound",
    "express_international_freight": "pound",
    "ltl_priority": "shipment",
    "ltl_economy": "shipment",
}
FREIGHT_SEGMENT_SERVICES = ("ltl_priority", "ltl_economy")
LEGACY_FISCAL_END = date(2026, 5, 31)
ARCHIVE_FORECAST_END = date(2030, 5, 31)


@dataclass(frozen=True)
class ServiceAssumptions:
    prior_daily_volume_thousands: float
    prior_yield_usd: float
    volume_growth: float
    yield_growth: float


@dataclass(frozen=True)
class RevenueAssumptions:
    period_end: date
    operating_days: int
    services: Mapping[str, ServiceAssumptions]
    express_other_millions: float
    other_and_eliminations_millions: float


@dataclass(frozen=True)
class ServiceRevenue:
    daily_volume_thousands: float
    yield_usd: float
    daily_revenue_thousands: float
    revenue_millions: float


@dataclass(frozen=True)
class RevenueForecast:
    period_end: date
    operating_days: int
    services: dict[str, ServiceRevenue]
    daily_revenue_thousands: float
    annualized_service_revenue_millions: float
    express_revenue_millions: float
    freight_segment_revenue_millions: float
    total_revenue_millions: float


def _finite_number(name: str, value: float, *, minimum: float | None = None) -> None:
    if type(value) not in (int, float) or not isfinite(value):
        raise ValueError(f"{name} must be a finite number.")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be at least {minimum:g}.")


def forecast_fdx_revenue(inputs: RevenueAssumptions) -> RevenueForecast:
    """Project nine services with daily volume in thousands and USD per unit.

    Each service uses the package, pound, or shipment unit in FDX_SERVICES.
    The two other revenue inputs are already annual USD millions, and the
    other and eliminations line may be negative. FY2026 is the last supported
    period on this legacy consolidated reporting basis.
    """
    period_end = inputs.period_end
    if (
        type(period_end) is not date
        or (period_end.month, period_end.day) != (5, 31)
        or period_end > LEGACY_FISCAL_END
    ):
        raise ValueError("FedEx legacy revenue requires a May 31 fiscal year-end through FY2026.")
    return _forecast_revenue(inputs)


def forecast_archived_fdx_revenue(inputs: RevenueAssumptions) -> RevenueForecast:
    """Replay the portfolio workbook's counterfactual legacy FY2026–30 periods."""
    period_end = inputs.period_end
    if (
        type(period_end) is not date
        or (period_end.month, period_end.day) != (5, 31)
        or not LEGACY_FISCAL_END <= period_end <= ARCHIVE_FORECAST_END
    ):
        raise ValueError("FedEx archive revenue requires a May 31 fiscal year-end from FY2026 through FY2030.")
    return _forecast_revenue(inputs)


def _forecast_revenue(inputs: RevenueAssumptions) -> RevenueForecast:
    period_end = inputs.period_end
    days = inputs.operating_days
    calendar_days = 366 if isleap(period_end.year) else 365
    if type(days) is not int or not 1 <= days <= calendar_days:
        raise ValueError(f"Operating days must be an integer between 1 and {calendar_days} for this fiscal year.")
    missing = FDX_SERVICES.keys() - inputs.services.keys()
    unexpected = inputs.services.keys() - FDX_SERVICES.keys()
    if missing or unexpected:
        raise ValueError(f"FedEx services mismatch. Missing: {sorted(missing)}. Unexpected: {sorted(unexpected)}.")
    _finite_number("Express other revenue", inputs.express_other_millions, minimum=0)
    _finite_number("Other revenue and eliminations", inputs.other_and_eliminations_millions)

    services = {}
    for service in FDX_SERVICES:
        drivers = inputs.services[service]
        _finite_number(f"{service} prior daily volume", drivers.prior_daily_volume_thousands, minimum=0)
        _finite_number(f"{service} prior yield", drivers.prior_yield_usd, minimum=0)
        _finite_number(f"{service} volume growth", drivers.volume_growth, minimum=-1)
        _finite_number(f"{service} yield growth", drivers.yield_growth, minimum=-1)
        daily_volume = drivers.prior_daily_volume_thousands * (1 + drivers.volume_growth)
        yield_usd = drivers.prior_yield_usd * (1 + drivers.yield_growth)
        daily_revenue = daily_volume * yield_usd
        annual_revenue = daily_revenue * (days / 1000)
        _finite_number(f"{service} forecast daily volume", daily_volume)
        _finite_number(f"{service} forecast yield", yield_usd)
        _finite_number(f"{service} forecast annual revenue", annual_revenue)
        services[service] = ServiceRevenue(daily_volume, yield_usd, daily_revenue, annual_revenue)

    daily_revenue = sum(line.daily_revenue_thousands for line in services.values())
    annualized_service_revenue = daily_revenue * (days / 1000)
    express_revenue = inputs.express_other_millions + sum(
        line.revenue_millions for service, line in services.items() if service not in FREIGHT_SEGMENT_SERVICES
    )
    freight_revenue = sum(services[service].revenue_millions for service in FREIGHT_SEGMENT_SERVICES)
    total_revenue = express_revenue + freight_revenue + inputs.other_and_eliminations_millions
    _finite_number("Forecast daily service revenue", daily_revenue)
    _finite_number("Forecast total revenue", total_revenue)
    return RevenueForecast(
        period_end=period_end,
        operating_days=days,
        services=services,
        daily_revenue_thousands=daily_revenue,
        annualized_service_revenue_millions=annualized_service_revenue,
        express_revenue_millions=express_revenue,
        freight_segment_revenue_millions=freight_revenue,
        total_revenue_millions=total_revenue,
    )

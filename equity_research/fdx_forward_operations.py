"""Forecast FedEx's December-year-end operations from the continuing-operations bridge."""

from dataclasses import dataclass
from datetime import date
from math import fsum, isfinite
from pathlib import Path

from .fdx_current import (
    DOMESTIC,
    INTERNATIONAL,
    SEGMENTS,
    SERVICES,
    ServiceProjection,
    _dated_source,
    _forecast_service,
    _item,
    _load,
    _num,
    _reconcile,
    _sources,
    run_current_fdx,
)
from .gd_inputs import _array, _day, _fields, _number, _object, _text


YEARS = tuple(range(2027, 2032))
GROUPS = dict(zip(SEGMENTS[:2], (DOMESTIC, INTERNATIONAL), strict=True))
SERVICE_SEGMENTS = {service: segment for segment, services in GROUPS.items() for service in services}


@dataclass(frozen=True)
class FDXServiceBase:
    daily_volume_thousands: float
    yield_usd: float
    revenue_millions: float
    incremental_fuel_surcharge_removed_millions: float
    normalized_yield_usd: float
    normalized_revenue_millions: float


@dataclass(frozen=True)
class FDXOperatingBase:
    period_end: date
    operating_days: int
    revenue_millions: float
    normalized_revenue_millions: float
    fuel_surcharge_removed_millions: float
    corporate_other_annualization_share: float
    segment_revenue_millions: dict[str, float]
    normalized_other_revenue_millions: dict[str, float]
    services: dict[str, FDXServiceBase]


@dataclass(frozen=True)
class FDXOperatingYear:
    period_end: date
    operating_days: int
    revenue_millions: float
    operating_income_millions: float
    operating_margin: float
    segment_revenue_millions: dict[str, float]
    segment_operating_income_millions: dict[str, float]
    segment_operating_margin: dict[str, float | None]
    other_revenue_millions: dict[str, float]
    services: dict[str, ServiceProjection]


@dataclass(frozen=True)
class FDXForwardOperations:
    as_of: date
    case: str
    case_rationale: str
    opening: FDXOperatingBase
    years: tuple[FDXOperatingYear, ...]
    targets_2029: dict[str, float | str]
    target_comparison_2029: dict[str, float]
    sources: dict
    limitations: tuple[str, ...]


def _series(value, label):
    values = _array(value, label)
    if len(values) != len(YEARS):
        raise ValueError(f"{label} requires one numeric assumption for each year from CY2027 through CY2031.")
    return tuple(_number(item, label) for item in values)


def _segment_series(row, field, segments):
    values = _fields(_item(row, field), field, set(segments))
    return {key: _series(path, f"{field}.{key}") for key, path in values.items()}


def _year_days(document, source_dates, operations):
    record = _item(document, "operating_days")
    _dated_source(_item(record, "source"), source_dates, "Forward operating days")
    days = _series(_item(record, "days"), "operating_days.days")
    guidance = operations["guidance"]["calendar_2027"]["operating_days_by_quarter"]
    if days[0] != sum(guidance):
        raise ValueError("CY2027 operating days do not match FedEx's published schedule.")
    for year, count in zip(YEARS, days, strict=True):
        calendar_days = (date(year + 1, 1, 1) - date(year, 1, 1)).days
        if not count.is_integer() or not 1 <= count <= calendar_days:
            raise ValueError(f"CY{year} operating days must be a whole count within the year.")
    return tuple(int(count) for count in days)


def _opening_base(document, source_dates, operations, current):
    checks = []
    q4 = current["q4_revenue"]
    prior = operations["periods"]
    quarters = tuple(prior[f"2026q{quarter}"] for quarter in (1, 2, 3))
    days_by_quarter = tuple(_num(quarter, "operating_days", minimum=1) for quarter in (*quarters, q4))
    if not all(count.is_integer() for count in days_by_quarter):
        raise ValueError("CY2026 operating days must be whole counts.")
    days = int(fsum(days_by_quarter))
    last_year = tuple(prior[f"2025q{quarter}"] for quarter in (1, 2, 3, 4))
    prior_revenue = {segment: fsum(quarter["segment_revenue_millions"][segment] for quarter in last_year)
                     for segment in SEGMENTS}
    annual_revenue = {segment: fsum(quarter["segment_revenue_millions"][segment] for quarter in quarters)
                      + _num(q4["segment_revenue_millions"], segment) for segment in SEGMENTS}
    _reconcile(checks, "CY2026 continuing revenue", fsum(annual_revenue.values()),
               current["calendar_year"]["forecast_revenue_millions"])
    _reconcile(checks, "CY2025 fuel surcharge reference revenue", fsum(prior_revenue.values()),
               operations["q4_bridge"]["cy25_revenue_millions"])

    fuel = _item(document, "fuel_normalization")
    _dated_source(_item(fuel, "source"), source_dates, "Fuel normalization")
    contribution = _num(fuel, "cy25_sales_growth_contribution", minimum=0)
    _reconcile(checks, "CY2026 fuel growth contribution", contribution, operations["guidance"]["revenue_growth_fuel_surcharge_contribution"])
    fraction = _num(fuel, "fraction_removed_from_2027_base", minimum=0)
    if fraction > 1:
        raise ValueError("The fraction of CY2026 incremental fuel revenue removed must be between zero and one.")
    segment_weights = _fields(_item(fuel, "segment_weights_by_cy25_revenue"), "fuel segment weights", set(GROUPS))
    weights = {segment: _num(segment_weights, segment, minimum=0) * prior_revenue[segment] for segment in GROUPS}
    weight_total = fsum(weights.values())
    if weight_total <= 0:
        raise ValueError("Fuel revenue allocation requires positive segment weights.")
    fuel_removed = contribution * fsum(prior_revenue.values()) * fraction
    segment_surcharge = {segment: fuel_removed * weight / weight_total for segment, weight in weights.items()}

    service_base = {}
    for service in SERVICES:
        final = q4["services"][service]
        historical = tuple(quarter["services"][service] for quarter in quarters)
        volumes = tuple(row["daily_volume_thousands"] for row in historical) + (final.daily_volume_thousands,)
        yields = tuple(row["yield_usd"] for row in historical) + (final.yield_usd,)
        units = tuple(volume * count for volume, count in zip(volumes, days_by_quarter, strict=True))
        units_total = fsum(units)
        if units_total <= 0:
            raise ValueError(f"{service} has no CY2026 units to project.")
        annual_yield = fsum(units_sold * yield_usd for units_sold, yield_usd in zip(units, yields, strict=True)) / units_total
        revenue = fsum(row["revenue_millions"] for row in historical) + final.revenue_millions
        service_base[service] = (units_total / days, annual_yield, revenue)

    services = {}
    normalized_other = {}
    for segment, group in GROUPS.items():
        booked = fsum(service_base[service][2] for service in group)
        normalized_other[segment] = fsum(quarter["other_revenue_millions"][segment] for quarter in quarters) + _num(
            q4["domestic_and_international_other_revenue_millions"], segment,
        )
        _reconcile(checks, f"CY2026 {segment} service and other revenue", booked + normalized_other[segment], annual_revenue[segment])
        if booked <= segment_surcharge[segment]:
            raise ValueError(f"The fuel normalization would exhaust {segment} service revenue.")
        reduction = segment_surcharge[segment] / booked
        for service in group:
            volume, yield_usd, revenue = service_base[service]
            services[service] = FDXServiceBase(volume, yield_usd, revenue, revenue * reduction,
                                              yield_usd * (1 - reduction), revenue * (1 - reduction))

    corporate = SEGMENTS[2]
    corporate_record = _item(document, "corporate_revenue")
    _dated_source(_item(corporate_record, "source"), source_dates, "Supply Chain disposal")
    fourth_quarter_share = last_year[-1]["segment_revenue_millions"][corporate] / prior_revenue[corporate]
    if not 0 < fourth_quarter_share <= 1:
        raise ValueError("CY2025 corporate revenue does not provide a positive Q4 seasonality ratio.")
    normalized_other[corporate] = _num(q4["segment_revenue_millions"], corporate) / fourth_quarter_share
    normalized_revenue = fsum(service.normalized_revenue_millions for service in services.values()) + fsum(normalized_other.values())
    _reconcile(checks, "Incremental fuel surcharge removal", fsum(row.incremental_fuel_surcharge_removed_millions for row in services.values()),
               fuel_removed)
    if not isfinite(normalized_revenue) or normalized_revenue <= 0:
        raise ValueError("The CY2026 operating base must retain positive normalized consolidated revenue.")
    return FDXOperatingBase(date(2026, 12, 31), days, fsum(annual_revenue.values()), normalized_revenue, fuel_removed,
                            fourth_quarter_share, annual_revenue, normalized_other, services)


def _forecast_years(document, selected, day_counts, opening):
    raw_assumptions = _fields(_item(document, "base_service_assumptions"), "base_service_assumptions", set(SERVICES))
    base = {}
    for key, row in raw_assumptions.items():
        row = _fields(row, key, {"volume_growth", "yield_growth"})
        base[key] = (_series(row["volume_growth"], f"{key} volume"), _series(row["yield_growth"], f"{key} yield"))
    volume_case = _segment_series(selected, "volume_growth_adjustments", GROUPS)
    yield_case = _segment_series(selected, "yield_growth_adjustments", GROUPS)
    other_growth = _segment_series(selected, "other_revenue_growth", SEGMENTS)
    margins = _segment_series(selected, "operating_margins", GROUPS)
    corporate_ebit = _series(_item(selected, "corporate_operating_income_millions"), "corporate operating income")

    prior_services = {key: {"daily_volume_thousands": row.daily_volume_thousands, "yield_usd": row.normalized_yield_usd,
                            "revenue_millions": row.normalized_revenue_millions}
                      for key, row in opening.services.items()}
    prior_other = opening.normalized_other_revenue_millions
    prior_days = opening.operating_days
    years = []
    for index, (year, days) in enumerate(zip(YEARS, day_counts, strict=True)):
        services = {}
        for key in SERVICES:
            segment = SERVICE_SEGMENTS[key]
            volume_growth = base[key][0][index] + volume_case[segment][index]
            yield_growth = base[key][1][index] + yield_case[segment][index]
            if min(volume_growth, yield_growth) <= -1:
                raise ValueError(f"CY{year} {key} volume and yield growth must exceed -100%.")
            services[key] = _forecast_service(prior_services[key], days, prior_days, volume_growth, yield_growth)
        other = {}
        for segment, rates in other_growth.items():
            growth = rates[index]
            if growth <= -1:
                raise ValueError(f"CY{year} {segment} other revenue growth must exceed -100%.")
            other[segment] = prior_other[segment] * (1 + growth)
        corporate = SEGMENTS[2]
        sales = {segment: fsum(services[service].revenue_millions for service in group) + other[segment]
                 for segment, group in GROUPS.items()}
        sales[corporate] = other[corporate]
        segment_margin = {segment: rates[index] for segment, rates in margins.items()}
        if any(margin > 1 for margin in segment_margin.values()):
            raise ValueError(f"CY{year} segment margin cannot exceed 100%.")
        ebit = {segment: sales[segment] * segment_margin[segment] for segment in GROUPS}
        ebit[corporate] = corporate_ebit[index]
        segment_margin[corporate] = ebit[corporate] / sales[corporate] if sales[corporate] > 0 else None
        revenue_total = fsum(sales.values())
        ebit_total = fsum(ebit.values())
        if not isfinite(revenue_total) or not isfinite(ebit_total) or min(revenue_total, *(sales[s] for s in GROUPS)) <= 0:
            raise ValueError(f"CY{year} operating estimates must be finite with positive transportation and consolidated revenue.")
        years.append(FDXOperatingYear(date(year, 12, 31), days, revenue_total, ebit_total, ebit_total / revenue_total,
                                      sales, ebit, segment_margin, other, services))
        prior_services = {key: {"daily_volume_thousands": row.daily_volume_thousands, "yield_usd": row.yield_usd,
                                "revenue_millions": row.revenue_millions} for key, row in services.items()}
        prior_other = other
        prior_days = days
    return tuple(years)


def _target_comparison(document, source_dates, forecast):
    record = _item(document, "targets_2029")
    _dated_source(_item(record, "source"), source_dates, "CY2029 management targets")
    metrics = {
        "revenue_millions": forecast.revenue_millions,
        "operating_income_millions": forecast.operating_income_millions,
        "operating_margin": forecast.operating_margin,
        "express_us_domestic_operating_margin": forecast.segment_operating_margin[SEGMENTS[0]],
        "express_international_operating_margin": forecast.segment_operating_margin[SEGMENTS[1]],
    }
    targets = {key: _num(record, key, "CY2029 management target", minimum=0)
               for key in (*metrics, "capex_to_revenue", "adjusted_fcf_millions")}
    return {**targets, "source": record["source"]}, {key: value - targets[key] for key, value in metrics.items()}


def run_fdx_forward_operations(
    as_of: date, inputs_dir: Path, *, case: str = "base", current: dict | None = None,
) -> FDXForwardOperations:
    """Build a selected five-year operating case on the December fiscal calendar."""
    document = _load(inputs_dir / f"fdx-forward-operations-{as_of.isoformat()}.json")
    operations = _load(inputs_dir / f"fdx-operations-{as_of.isoformat()}.json")
    for name, snapshot in (("forward operations", document), ("operating history", operations)):
        if _day(_item(snapshot, "as_of"), f"{name}.as_of") != as_of:
            raise ValueError(f"FedEx {name} does not match the requested cutoff.")
    if (document.get("ticker"), document.get("currency"), document.get("monetary_units")) != ("FDX", "USD", "millions"):
        raise ValueError("The forward snapshot must contain FedEx amounts in USD millions.")
    if tuple(_array(_item(document, "years"), "years")) != YEARS:
        raise ValueError("FedEx forward assumptions must use CY2027 through CY2031 in order.")
    dates = _sources(document, as_of)
    for source_id, note in _object(_item(document, "service_assumption_notes"), "service assumption notes").items():
        _dated_source(source_id, dates, "Service assumptions")
        _text(note, source_id)
    cases = _fields(_item(document, "cases"), "forward cases", {"bear", "base", "bull"})
    if case not in cases:
        raise ValueError(f"Unsupported FedEx case {case!r}. Use bear, base or bull.")
    selected = _object(cases[case], f"{case} case")
    if current is None:
        current = run_current_fdx(as_of, inputs_dir)
    if current.get("as_of") != as_of:
        raise ValueError("The FedEx CY2026 operating bridge must match the forward forecast cutoff.")
    day_counts = _year_days(document, dates, operations)
    opening = _opening_base(document, dates, operations, current)
    forecast = _forecast_years(document, selected, day_counts, opening)
    targets, differences = _target_comparison(document, dates, forecast[YEARS.index(2029)])
    limitations = tuple(_text(item, "forward limitation")
                        for item in _array(_item(document, "limitations"), "forward limitations"))
    return FDXForwardOperations(as_of, case, _text(_item(selected, "rationale"), "case rationale"), opening, forecast,
                                targets, differences, document["sources"], limitations)

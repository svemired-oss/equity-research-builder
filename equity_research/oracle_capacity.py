"""Translate explicit Oracle data center cohorts into incremental PP&E and revenue."""

from calendar import monthrange
from dataclasses import dataclass, replace
from datetime import date
from math import fsum, isclose, isfinite
from typing import Sequence
from urllib.parse import urlsplit

from .forecast_schedules import PPEBalance, PPEMovements, roll_ppe

@dataclass(frozen=True)
class CapacityEconomics:
    """Approximate construction cost and mature annual revenue per GW in USD millions."""

    owned_build_cost_millions_per_gw: float
    customer_hardware_build_cost_millions_per_gw: float
    owned_mature_annual_revenue_millions_per_gw: float
    customer_hardware_mature_annual_revenue_millions_per_gw: float
    available_on: date
    source_url: str


INVESTOR_DAY_2026_ECONOMICS = CapacityEconomics(
    37_000,
    6_000,
    16_000,
    11_000,
    date(2026, 10, 28),
    "https://s23.q4cdn.com/440135859/files/doc_downloads/2026/10/"
    "Oracle-Investor-Day-Meeting/vFinal/2026-Investor-Day-CFO-presentation.pdf#page=18",
)


@dataclass(frozen=True)
class QuarterFraction:
    quarter_end: date
    fraction: float


@dataclass(frozen=True)
class CapacityCohort:
    """Modeled contract timing, capacity and funding assumptions with provenance.

    The vendor share applies to the modeled Oracle-owned total build cost.
    Management's 70% vendor example applies to hardware acquisition capex.
    Commissioning occurs at quarter close. Revenue starts in a later quarter.
    """

    name: str
    assumed_on: date
    source_url: str
    contract_signed_on: date
    capacity_gw: float
    customer_hardware_share: float
    vendor_share_of_owned_build_cost: float
    construction_weights: tuple[QuarterFraction, ...]
    commissioned_on: date
    revenue_ramp: tuple[QuarterFraction, ...]
    final_revenue_quarter: date


@dataclass(frozen=True)
class CapacityQuarter:
    """Quarterly flows and balances for modeled cohorts.

    Installed capacity is cumulative. Ending a modeled revenue term does
    not assume disposal of the equipment or a replacement contract.
    """

    period_end: date
    economic_additions_millions: float
    vendor_financed_additions_millions: float
    additions_without_vendor_financing_millions: float
    gross_property_commissioned_millions: float
    closing_construction_in_progress_millions: float
    commissioned_gw: float
    installed_gw: float
    revenue_weighted_gw: float
    incremental_revenue_millions: float


@dataclass(frozen=True)
class CapacityForecast:
    as_of: date
    economics: CapacityEconomics
    cohorts: tuple[CapacityCohort, ...]
    quarters: tuple[CapacityQuarter, ...]


def _quarter_end(day: date) -> bool:
    return (type(day) is date and day.month in (2, 5, 8, 11)
            and day.day == monthrange(day.year, day.month)[1])


def _next_quarter(day: date) -> date:
    year, month = (day.year + 1, 2) if day.month == 11 else (day.year, day.month + 3)
    return date(year, month, monthrange(year, month)[1])


def _number(value: float) -> bool:
    return type(value) in (int, float) and isfinite(value)


def _check_source(on: date, url: str, as_of: date) -> None:
    if type(on) is not date or on > as_of or not isinstance(url, str):
        raise ValueError("Capacity assumptions and unit economics need dated source evidence by the valuation cutoff.")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Capacity evidence must reference an HTTPS source.")


def _quarter_fractions(rows: tuple[QuarterFraction, ...], label: str) -> dict[date, float]:
    if not rows:
        raise ValueError(f"{label} needs at least one explicit quarter.")
    quarters = {}
    for row in rows:
        if not _quarter_end(row.quarter_end) or not _number(row.fraction) or not 0 <= row.fraction <= 1:
            raise ValueError(f"{label} needs Oracle fiscal quarter-ends and fractions from zero to one.")
        if quarters and row.quarter_end <= next(reversed(quarters)):
            raise ValueError(f"{label} quarter-ends must be unique and increasing.")
        quarters[row.quarter_end] = row.fraction
    return quarters


def _cohort_quarters(
    cohort: CapacityCohort, economics: CapacityEconomics, as_of: date,
) -> tuple[CapacityQuarter, ...]:
    _check_source(cohort.assumed_on, cohort.source_url, as_of)
    if type(cohort.contract_signed_on) is not date or not _number(cohort.capacity_gw) or cohort.capacity_gw <= 0:
        raise ValueError(f"{cohort.name} requires a contract date and positive capacity.")
    if not all(_number(share) and 0 <= share <= 1 for share in (
        cohort.customer_hardware_share, cohort.vendor_share_of_owned_build_cost,
    )):
        raise ValueError(f"{cohort.name} requires hardware and financing shares from zero to one.")
    if not all(map(_quarter_end, (cohort.commissioned_on, cohort.final_revenue_quarter))):
        raise ValueError(f"{cohort.name} commissioning and final revenue dates must be Oracle fiscal quarter-ends.")

    construction = _quarter_fractions(cohort.construction_weights, f"{cohort.name} construction weights")
    if min(construction) < cohort.contract_signed_on or max(construction) > cohort.commissioned_on:
        raise ValueError(f"{cohort.name} construction must occur between contract signing and commissioning.")
    if not isclose(fsum(construction.values()), 1, rel_tol=0, abs_tol=1e-12):
        raise ValueError(f"{cohort.name} construction weights must allocate the total build cost exactly once.")
    ramp = _quarter_fractions(cohort.revenue_ramp, f"{cohort.name} revenue ramp")
    if min(ramp) <= cohort.commissioned_on or max(ramp) > cohort.final_revenue_quarter:
        raise ValueError(f"{cohort.name} revenue ramp must follow commissioning and finish by the final service quarter.")
    previous_date, previous_fraction = cohort.commissioned_on, 0
    for day, fraction in ramp.items():
        if day != _next_quarter(previous_date) or fraction < previous_fraction:
            raise ValueError(f"{cohort.name} revenue ramp must cover consecutive quarters and cannot decline.")
        previous_date, previous_fraction = day, fraction
    if previous_fraction != 1:
        raise ValueError(f"{cohort.name} revenue ramp must end at full utilization.")

    customer_gw = cohort.capacity_gw * cohort.customer_hardware_share
    owned_gw = cohort.capacity_gw - customer_gw
    owned_cost = owned_gw * economics.owned_build_cost_millions_per_gw
    total_cost = owned_cost + customer_gw * economics.customer_hardware_build_cost_millions_per_gw
    vendor_cost = owned_cost * cohort.vendor_share_of_owned_build_cost
    mature_quarter_revenue = fsum((
        owned_gw * economics.owned_mature_annual_revenue_millions_per_gw,
        customer_gw * economics.customer_hardware_mature_annual_revenue_millions_per_gw,
    )) / 4
    if not all(map(isfinite, (total_cost, vendor_cost, mature_quarter_revenue))):
        raise ValueError(f"{cohort.name} capacity and economics exceed a finite schedule.")

    rows, additions = [], []
    last_ramp_quarter = max(ramp)
    day = min(construction)
    while day <= cohort.final_revenue_quarter:
        weight = construction.get(day, 0)
        spend, vendor_funding = total_cost * weight, vendor_cost * weight
        additions.append(spend)
        utilization = ramp.get(day, 1 if day > last_ramp_quarter else 0)
        rows.append(CapacityQuarter(
            day, spend, vendor_funding, spend - vendor_funding,
            total_cost if day == cohort.commissioned_on else 0,
            fsum(additions) if day < cohort.commissioned_on else 0,
            cohort.capacity_gw if day == cohort.commissioned_on else 0,
            cohort.capacity_gw if day >= cohort.commissioned_on else 0,
            utilization * cohort.capacity_gw,
            utilization * mature_quarter_revenue,
        ))
        if day == cohort.final_revenue_quarter:
            break
        day = _next_quarter(day)
    if not isclose(fsum(additions), total_cost, rel_tol=1e-12, abs_tol=1e-6):
        raise ValueError(f"{cohort.name} construction spending does not reconcile to its gross commissioned property.")
    return tuple(rows)


def forecast_oracle_capacity(
    cohorts: Sequence[CapacityCohort], economics: CapacityEconomics, *, as_of: date,
) -> CapacityForecast:
    """Build a dated incremental scenario from explicitly supplied cohort assumptions.

    Spending is recognized economic PP&E additions, including financed assets.
    Remaining additions after vendor financing do not represent cash capex.
    Payable settlement, leases and customer financing are separate schedules.
    Equipment supplied and owned by customers never enters Oracle's PP&E.
    """
    if type(as_of) is not date or not cohorts:
        raise ValueError("Supply an as-of date and at least one explicit capacity cohort.")
    _check_source(economics.available_on, economics.source_url, as_of)
    unit_values = (economics.owned_build_cost_millions_per_gw, economics.customer_hardware_build_cost_millions_per_gw,
                   economics.owned_mature_annual_revenue_millions_per_gw,
                   economics.customer_hardware_mature_annual_revenue_millions_per_gw)
    if not all(_number(value) and value > 0 for value in unit_values):
        raise ValueError("Unit economics must use finite, positive cost and revenue.")
    names, by_date = set(), {}
    for cohort in cohorts:
        if not isinstance(cohort.name, str) or not cohort.name.strip() or cohort.name in names:
            raise ValueError("Capacity cohort names must be nonblank and unique.")
        names.add(cohort.name)
        for row in _cohort_quarters(cohort, economics, as_of):
            by_date.setdefault(row.period_end, []).append(row)

    quarters = []
    day, final_day = min(by_date), max(by_date)
    installed_gw = 0
    while day <= final_day:
        rows = by_date.get(day, ())
        commissioned_gw = fsum(row.commissioned_gw for row in rows)
        installed_gw = fsum((installed_gw, commissioned_gw))
        quarters.append(CapacityQuarter(
            day,
            fsum(row.economic_additions_millions for row in rows),
            fsum(row.vendor_financed_additions_millions for row in rows),
            fsum(row.additions_without_vendor_financing_millions for row in rows),
            fsum(row.gross_property_commissioned_millions for row in rows),
            fsum(row.closing_construction_in_progress_millions for row in rows),
            commissioned_gw,
            installed_gw,
            fsum(row.revenue_weighted_gw for row in rows),
            fsum(row.incremental_revenue_millions for row in rows),
        ))
        if day == final_day:
            break
        day = _next_quarter(day)
    return CapacityForecast(as_of, economics, tuple(cohorts), tuple(quarters))


def _incremental_property(quarters: Sequence[CapacityQuarter], life_quarters: int) -> dict:
    """Roll model-only additions forward, starting depreciation after commissioning."""
    if type(life_quarters) is not int or life_quarters <= 0:
        raise ValueError("The illustrative useful life must be a positive number of quarters.")
    balance = PPEBalance(0, 0, 0)
    commissioned = []
    result = {}
    for row in quarters:
        active = commissioned[-life_quarters:]
        depreciation = fsum(active) / life_quarters
        movements = PPEMovements(
            cash_additions_to_construction_millions=row.additions_without_vendor_financing_millions,
            noncash_additions_to_construction_millions=row.vendor_financed_additions_millions,
            cash_additions_to_in_service_millions=0,
            noncash_additions_to_in_service_millions=0,
            construction_commissioned_millions=row.gross_property_commissioned_millions,
            depreciation_millions=depreciation, gross_disposed_millions=0,
            accumulated_depreciation_disposed_millions=0,
        )
        balance = roll_ppe(balance, movements).closing
        if not isclose(balance.construction_in_progress_millions, row.closing_construction_in_progress_millions,
                       rel_tol=0, abs_tol=1e-6):
            raise ValueError("Construction in progress differs between the capacity and PP&E schedules.")
        commissioned.append(row.gross_property_commissioned_millions)
        result[row.period_end] = (depreciation, balance)
    return result


def compare_oracle_buildout(as_of: date, *, life_quarters: int = 24) -> dict:
    """Compare ownership and delivery assumptions for the October 2026 illustration."""
    economics = INVESTOR_DAY_2026_ECONOMICS
    if as_of < economics.available_on:
        return {"status": "unavailable", "cases": {}}
    source = economics.source_url.replace("#page=18", "#page=24")
    weights = (0.25, 0.50, 0.25)
    groups = []
    for index, commissioning in enumerate((date(2027, 8, 31), date(2028, 5, 31))):
        construction = (
            (date(2027, 2, 28), date(2027, 5, 31), date(2027, 8, 31))
            if index == 0 else (date(2027, 11, 30), date(2028, 2, 29), date(2028, 5, 31))
        )
        service = commissioning
        ramp = []
        for fraction in (0.2, 0.4, 0.6, 0.8, 1.0):
            service = _next_quarter(service)
            ramp.append(QuarterFraction(service, fraction))
        final = ramp[0].quarter_end
        for _ in range(19):
            final = _next_quarter(final)
        groups.append(CapacityCohort(
            name=f"Illustrative group {index + 1}", assumed_on=economics.available_on, source_url=source,
            contract_signed_on=date(2026, 10, 28), capacity_gw=3, customer_hardware_share=0,
            vendor_share_of_owned_build_cost=0, commissioned_on=commissioning,
            construction_weights=tuple(QuarterFraction(day, weight) for day, weight in zip(construction, weights)),
            revenue_ramp=tuple(ramp), final_revenue_quarter=final,
        ))

    delayed = groups[-1]
    delayed = replace(
        delayed, commissioned_on=_next_quarter(delayed.commissioned_on),
        revenue_ramp=tuple(QuarterFraction(_next_quarter(row.quarter_end), row.fraction) for row in delayed.revenue_ramp),
        final_revenue_quarter=_next_quarter(delayed.final_revenue_quarter),
    )
    cases = {}
    for name, cohorts, customer_share, vendor_share in (
        ("oracle_owned", groups, 0, 0),
        ("half_customer_hardware", groups, 0.5, 0),
        ("half_customer_hardware_delayed", [groups[0], delayed], 0.5, 0),
        ("half_customer_hardware_vendor_funded", groups, 0.5, 0.5),
    ):
        modeled = forecast_oracle_capacity(
            tuple(replace(cohort, customer_hardware_share=customer_share,
                          vendor_share_of_owned_build_cost=vendor_share) for cohort in cohorts),
            economics, as_of=as_of,
        )
        property_schedule = _incremental_property(modeled.quarters, life_quarters)
        years = {}
        for year in range(2027, 2032):
            rows = [row for row in modeled.quarters if row.period_end.year + (row.period_end.month > 5) == year]
            closing = property_schedule[rows[-1].period_end][1]
            years[year] = {
                "economic_additions_millions": fsum(row.economic_additions_millions for row in rows),
                "vendor_financed_additions_millions": fsum(row.vendor_financed_additions_millions for row in rows),
                "incremental_revenue_millions": fsum(row.incremental_revenue_millions for row in rows),
                "commissioned_gw": fsum(row.commissioned_gw for row in rows),
                "incremental_depreciation_millions": fsum(property_schedule[row.period_end][0] for row in rows),
                "closing_construction_in_progress_millions": closing.construction_in_progress_millions,
                "closing_incremental_net_ppe_millions": closing.net_millions,
            }
        cases[name] = {
            "customer_hardware_share": customer_share,
            "vendor_share_of_owned_build_cost": vendor_share,
            "commissioning_dates": [cohort.commissioned_on.isoformat() for cohort in modeled.cohorts],
            "fiscal_years": years,
            "quarters": [
                {
                    "period_end": row.period_end.isoformat(),
                    "economic_additions_millions": row.economic_additions_millions,
                    "vendor_financed_additions_millions": row.vendor_financed_additions_millions,
                    "gross_property_commissioned_millions": row.gross_property_commissioned_millions,
                    "incremental_depreciation_millions": property_schedule[row.period_end][0],
                    "closing_incremental_net_ppe_millions": property_schedule[row.period_end][1].net_millions,
                }
                for row in modeled.quarters if row.period_end <= date(2031, 5, 31)
            ],
        }
    return {
        "status": "illustrative", "source": source, "cases": cases,
        "assumed_useful_life_quarters": life_quarters,
        "useful_life_reference": "https://www.sec.gov/Archives/edgar/data/1341439/000119312526389274/orcl-20260831.htm",
        "limitations": [
            "The 6 GW buildout and per-GW economics come from Oracle's illustrative disclosures.",
            "The two equal groups, commissioning dates, ownership split, spend weights and ramp fractions are scenario assumptions.",
            "Incremental cohort revenue may already be reflected in consolidated guidance. Do not add it to that guidance.",
            "Economic additions exclude lease rent and are distinct from reported cash capex.",
            "Vendor financing covers an illustrative share of Oracle-owned build cost. Payment timing and interest are separate inputs.",
            "The incremental PP&E schedule uses straight-line depreciation starting the quarter after commissioning. "
            "It applies one illustrative useful life to the modeled asset mix and excludes existing assets, disposals and replacement spending.",
        ],
    }

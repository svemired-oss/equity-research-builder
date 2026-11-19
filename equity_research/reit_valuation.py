"""Value a net-lease REIT from property cash flow and a matching NAV bridge."""

from dataclasses import dataclass
from math import fsum, isfinite
from statistics import mean

from .operating_research import ResearchGap


@dataclass(frozen=True)
class NetLeaseBasis:
    annualized_in_place_cash_rent: float
    cash_noi_margin: float
    annual_recurring_capex_and_leasing_costs: float
    annual_cash_overhead: float
    annual_property_rent_growth: float
    annual_expiring_rent_share: tuple[float, ...]
    renewal_fraction: float
    renewal_rent_ratio: float
    vacancy_years: float
    cash: float
    net_nonproperty_investments: float
    gross_debt: float
    preferred_and_other_claims: float
    shares_millions: float
    scope: str


@dataclass(frozen=True)
class PropertyYear:
    year: int
    contracted_rent: float
    cash_rent: float
    cash_noi: float
    maintenance_and_leasing: float
    overhead: float
    unlevered_cash_flow: float


@dataclass(frozen=True)
class NetLeaseValuation:
    years: tuple[PropertyYear, ...]
    property_enterprise_value: float
    common_value: float
    value_per_share: float
    common_nav: float
    nav_per_share: float


def value_net_lease(basis: NetLeaseBasis, *, discount_rate, terminal_growth, nav_cap_rate) -> NetLeaseValuation:
    """Value an existing portfolio. All assets and debt must use one ownership basis."""
    b = basis
    if not 0 <= terminal_growth < min(discount_rate, nav_cap_rate) < .4:
        raise ResearchGap("REIT discount, capitalization and growth assumptions are out of range.")
    if (not b.scope or len(b.annual_expiring_rent_share) != 5
            or not all(isfinite(number) for number in (b.annualized_in_place_cash_rent, b.cash, b.gross_debt,
                                                       b.net_nonproperty_investments, b.preferred_and_other_claims,
                                                       b.shares_millions, b.annual_recurring_capex_and_leasing_costs,
                                                       b.annual_cash_overhead, *b.annual_expiring_rent_share))
            or min(b.annualized_in_place_cash_rent, b.shares_millions) <= 0
            or min(b.cash, b.gross_debt, b.preferred_and_other_claims, b.annual_cash_overhead,
                   b.annual_recurring_capex_and_leasing_costs, *b.annual_expiring_rent_share) < 0
            or not 0 < b.cash_noi_margin <= 1
            or not -.10 < b.annual_property_rent_growth < .15
            or not 0 <= b.renewal_fraction <= 1 or not .5 <= b.renewal_rent_ratio <= 1.5
            or not 0 <= b.vacancy_years <= 1 or sum(b.annual_expiring_rent_share) > 1):
        raise ResearchGap("A validated rent, ownership and debt basis is required for the REIT valuation.")

    initial_rent = b.annualized_in_place_cash_rent
    contracted_rent = initial_rent
    forecast = []
    for year, expiry_share in enumerate(b.annual_expiring_rent_share, 1):
        contracted_rent *= 1+b.annual_property_rent_growth
        expiring = initial_rent*(1+b.annual_property_rent_growth)**year*expiry_share
        # Model one-time vacancy on non-renewals and a contractual change on
        # extensions. Current in-place rent already reflects current occupancy.
        contracted_rent += expiring*b.renewal_fraction*(b.renewal_rent_ratio-1)
        cash_rent = contracted_rent-expiring*(1-b.renewal_fraction)*b.vacancy_years
        noi = cash_rent*b.cash_noi_margin
        scale = cash_rent/initial_rent
        maintenance = b.annual_recurring_capex_and_leasing_costs*scale
        overhead = b.annual_cash_overhead*scale
        forecast.append(PropertyYear(year, contracted_rent, cash_rent, noi, maintenance, overhead,
                                     noi-maintenance-overhead))
    if any(row.unlevered_cash_flow <= 0 for row in forecast):
        raise ResearchGap("The property cash flow is not sustainable under the specified lease assumptions.")
    vacancy = mean(b.annual_expiring_rent_share)*(1-b.renewal_fraction)*b.vacancy_years
    next_rent = contracted_rent*(1+terminal_growth)*(1-vacancy)
    stabilized_margin = b.cash_noi_margin-(b.annual_recurring_capex_and_leasing_costs+b.annual_cash_overhead)/initial_rent
    terminal = next_rent*stabilized_margin/(discount_rate-terminal_growth)
    property_value = fsum(row.unlevered_cash_flow/(1+discount_rate)**row.year for row in forecast)
    property_value += terminal/(1+discount_rate)**len(forecast)
    bridge = b.cash+b.net_nonproperty_investments-b.gross_debt-b.preferred_and_other_claims
    common_value = property_value+bridge
    common_nav = forecast[0].cash_noi/nav_cap_rate+bridge
    if min(common_value, common_nav) <= 0:
        raise ResearchGap("The equity valuation needs a reviewed debt and nonproperty bridge.")
    return NetLeaseValuation(tuple(forecast), property_value, common_value, common_value/b.shares_millions,
                            common_nav, common_nav/b.shares_millions)

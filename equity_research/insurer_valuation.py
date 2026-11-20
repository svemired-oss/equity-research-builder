"""Forecast a property-and-casualty insurer from premiums and required surplus."""

from dataclasses import dataclass
from math import isfinite

from .distributable_equity import discount_common_equity
from .operating_research import ResearchGap


@dataclass(frozen=True)
class UnderwritingBasis:
    earned_premiums: float
    prior_earned_premiums: float
    underwriting_profit: float
    investment_income: float
    financing_and_other_costs: float
    common_taxes: float
    statutory_surplus: float
    documented_combined_ratio_target: float


@dataclass(frozen=True)
class UnderwritingYear:
    year: int
    premiums: float
    combined_ratio: float
    underwriting_income: float
    investment_income: float
    common_income: float
    closing_surplus: float
    distributions: float
    residual_income: float


@dataclass(frozen=True)
class UnderwritingValuation:
    years: tuple[UnderwritingYear, ...]
    terminal: UnderwritingYear
    common_equity: float
    residual_income_equity: float


def value_property_casualty(basis: UnderwritingBasis, *, cost_of_equity, terminal_growth):
    """Use premium-to-surplus leverage as the capital retention policy.

    This estimates underlying common distribution capacity. Subsidiary-level
    regulatory and upstream dividend restrictions require separate evidence.
    """
    if not 0 <= terminal_growth < cost_of_equity < .4:
        raise ResearchGap("Insurer terminal growth must stay below the cost of common equity.")
    b = basis
    values = (b.earned_premiums, b.prior_earned_premiums, b.investment_income, b.statutory_surplus,
              b.financing_and_other_costs, b.common_taxes, b.underwriting_profit)
    if (any(not isfinite(value) for value in values)
            or min(b.earned_premiums, b.prior_earned_premiums, b.statutory_surplus) <= 0
            or min(b.investment_income, b.financing_and_other_costs, b.common_taxes) < 0
            or not .7 <= b.documented_combined_ratio_target < 1.05):
        raise ResearchGap("The insurer needs valid earned premium, investment yield and capital inputs.")
    growth = b.earned_premiums/b.prior_earned_premiums - 1
    reported_ratio = 1 - b.underwriting_profit/b.earned_premiums
    pretax = b.underwriting_profit + b.investment_income - b.financing_and_other_costs
    if pretax <= 0 or b.common_taxes/pretax >= .5 or not .7 <= reported_ratio <= 1.3:
        raise ResearchGap("The insurer's earnings need normalization before valuation.")
    tax = b.common_taxes/pretax
    surplus_ratio = b.statutory_surplus/b.earned_premiums
    investment_ratio = b.investment_income/b.earned_premiums
    corporate_cost_ratio = b.financing_and_other_costs/b.earned_premiums
    premiums, opening = b.earned_premiums, b.statutory_surplus
    results = []
    for year in range(1, 7):
        fade = .5**year if year <= 5 else 0
        premiums *= 1+terminal_growth+(growth-terminal_growth)*fade
        # Set the final explicit year to the sustainable target so all
        # Year 6 income, capital and distributions can grow at one rate.
        ratio_fade = .5**year if year < 5 else 0
        combined = b.documented_combined_ratio_target+(reported_ratio-b.documented_combined_ratio_target)*ratio_fade
        underwriting = premiums*(1-combined)
        investment = premiums*investment_ratio
        income = (underwriting+investment-premiums*corporate_cost_ratio)*(1-tax)
        closing = premiums*surplus_ratio
        distribution = income+opening-closing
        if distribution < 0:
            raise ResearchGap("The insurer would need additional capital to sustain this premium growth.")
        results.append(UnderwritingYear(year, premiums, combined, underwriting, investment, income, closing,
                                        distribution, income-opening*cost_of_equity))
        opening = closing
    *years, terminal = results
    value, residual = discount_common_equity(
        b.statutory_surplus, tuple(row.distributions for row in years), tuple(row.residual_income for row in years),
        terminal.distributions, terminal.residual_income, cost_of_equity, terminal_growth,
    )
    return UnderwritingValuation(tuple(years), terminal, value, residual)

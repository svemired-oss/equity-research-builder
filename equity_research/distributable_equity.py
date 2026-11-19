"""Discount common distributions and independently reconcile residual income."""

from math import fsum, isclose, isfinite

from .operating_research import ResearchGap


def discount_common_equity(capital, distributions, residual_income, next_distribution, next_residual_income,
                           cost_of_equity, growth):
    """Use annual year-end timing and a stable-growth next-year cash flow."""
    if (not distributions or len(distributions) != len(residual_income)
            or not 0 <= growth < cost_of_equity
            or not all(isfinite(number) for number in (
                capital, next_distribution, next_residual_income, *distributions, *residual_income))
            or capital <= 0 or next_distribution <= 0):
        raise ResearchGap("The common-equity valuation needs a positive capital base and sustainable distributions.")
    spread = cost_of_equity-growth
    terminal_discount = (1+cost_of_equity)**len(distributions)

    def pv(series, following):
        return fsum(amount/(1+cost_of_equity)**year for year, amount in enumerate(series, 1)) + (
            following/spread/terminal_discount)

    dividends = pv(distributions, next_distribution)
    residual = capital + pv(residual_income, next_residual_income)
    if not isclose(dividends, residual, rel_tol=1e-10, abs_tol=1e-6):
        raise ResearchGap("The dividend discount and residual-income valuations did not reconcile.")
    return dividends, residual

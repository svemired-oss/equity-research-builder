"""Project bank earnings and value common distributions after regulatory retention."""

from dataclasses import dataclass
from math import isclose, isfinite

from .distributable_equity import discount_common_equity
from .operating_research import ResearchGap


@dataclass(frozen=True)
class BankBalance:
    assets: float
    liabilities: float
    deposits: float
    gross_loans: float
    risk_weighted_assets: float
    cet1: float
    equity: float


@dataclass(frozen=True)
class BankIncome:
    interest_income: float
    deposit_interest: float
    other_interest: float
    noninterest_income: float
    noninterest_expense: float
    credit_provision: float
    taxes: float
    noncommon_earnings: float

    @property
    def pretax(self):
        return (self.interest_income + self.noninterest_income - self.deposit_interest
                - self.other_interest - self.noninterest_expense - self.credit_provision)

    @property
    def common_earnings(self):
        return self.pretax - self.taxes - self.noncommon_earnings


@dataclass(frozen=True)
class BankYear:
    year: int
    assets: float
    deposits: float
    gross_loans: float
    risk_weighted_assets: float
    opening_cet1: float
    closing_cet1: float
    income: BankIncome
    common_distributions: float
    residual_income: float


@dataclass(frozen=True)
class BankValuation:
    years: tuple[BankYear, ...]
    terminal: BankYear
    common_equity: float
    residual_income_equity: float
    cet1_target: float
    cost_of_equity: float
    terminal_growth: float


def value_bank(current: BankBalance, previous: BankBalance, trailing: BankIncome, *, minimum_cet1: float,
               cost_of_equity: float, terminal_growth: float, capital_buffer: float = .015,
               forecast_minimum_cet1: float | None = None) -> BankValuation:
    """Use consolidated CET1 as the capital proxy and annual period-end cash flows.

    Deposits and other funding support interest-earning activity. Preferred and
    participating claims are represented by the reported income allocation.
    Currency inputs and outputs use millions. The date is the balance-sheet anchor.
    """
    forecast_floor = minimum_cet1 if forecast_minimum_cet1 is None else forecast_minimum_cet1
    if not (0 < minimum_cet1 < .35 and 0 < forecast_floor < .35 and 0 <= capital_buffer < .1
            and 0 <= terminal_growth < cost_of_equity < .4):
        raise ResearchGap("Bank growth, discount, or regulatory capital assumptions are out of range.")
    for balance in (current, previous):
        values = (balance.assets, balance.liabilities, balance.deposits, balance.gross_loans,
                  balance.risk_weighted_assets, balance.cet1, balance.equity)
        if (any(not isfinite(value) or value <= 0 for value in values)
                or not balance.deposits < balance.liabilities
                or not balance.gross_loans < balance.assets
                or not isclose(balance.assets - balance.liabilities, balance.equity, rel_tol=0, abs_tol=1)):
            raise ResearchGap("The bank balance sheet or funding inputs did not reconcile.")
    income_values = (trailing.interest_income, trailing.deposit_interest, trailing.other_interest,
                     trailing.noninterest_income, trailing.noninterest_expense, trailing.credit_provision,
                     trailing.taxes, trailing.noncommon_earnings)
    if any(not isfinite(value) or value < 0 for value in income_values) or trailing.common_earnings <= 0:
        raise ResearchGap("The bank forecast needs a positive, reconciled trailing common earnings base.")
    opening_ratio = current.cet1/current.risk_weighted_assets
    target = max(opening_ratio, forecast_floor + capital_buffer)
    if opening_ratio < minimum_cet1:
        raise ResearchGap("The latest CET1 ratio is below the filed consolidated requirement.")

    average_assets = (current.assets + previous.assets)/2
    average_deposits = (current.deposits + previous.deposits)/2
    average_other_funding = ((current.liabilities-current.deposits) + (previous.liabilities-previous.deposits))/2
    average_loans = (current.gross_loans + previous.gross_loans)/2
    gross_yield = trailing.interest_income/average_assets
    deposit_cost = trailing.deposit_interest/average_deposits
    other_cost = trailing.other_interest/average_other_funding
    noninterest_rate = trailing.noninterest_income/average_assets
    provision_rate = trailing.credit_provision/average_loans
    operating_revenue = trailing.interest_income-trailing.deposit_interest-trailing.other_interest+trailing.noninterest_income
    expense_ratio = trailing.noninterest_expense/operating_revenue
    tax_rate = trailing.taxes/trailing.pretax
    if not 0 < expense_ratio < 1 or not 0 <= tax_rate < .5:
        raise ResearchGap("The bank's reported efficiency ratio or effective tax rate needs normalization.")
    rwa_density = current.risk_weighted_assets/current.gross_loans
    equity_to_assets = current.equity/current.assets

    growth_rates = (current.assets/previous.assets-1, current.deposits/previous.deposits-1,
                    current.gross_loans/previous.gross_loans-1)
    assets, deposits, loans = current.assets, current.deposits, current.gross_loans
    other = current.liabilities-current.deposits
    capital = current.cet1
    forecasts = []
    for year in range(1, 7):
        fade = .5**year if year <= 5 else 0
        rates = [terminal_growth+(rate-terminal_growth)*fade for rate in growth_rates]
        new_assets, new_deposits, new_loans = (value*(1+rate) for value, rate in zip(
            (assets, deposits, loans), rates, strict=True))
        new_other = new_assets*(1-equity_to_assets)-new_deposits
        if min(new_assets, new_deposits, new_loans, new_other) <= 0 or new_loans >= new_assets:
            raise ResearchGap("The bank forecast has a funding or loan mix that needs a different model.")
        interest = (assets+new_assets)/2*gross_yield
        deposit_interest = (deposits+new_deposits)/2*deposit_cost
        other_interest = (other+new_other)/2*other_cost
        fees = (assets+new_assets)/2*noninterest_rate
        operating_expense = (interest-deposit_interest-other_interest+fees)*expense_ratio
        provisions = (loans+new_loans)/2*provision_rate
        pretax = interest-deposit_interest-other_interest+fees-operating_expense-provisions
        income = BankIncome(interest, deposit_interest, other_interest, fees, operating_expense, provisions,
                            pretax*tax_rate, trailing.noncommon_earnings*((1+terminal_growth)**year))
        rwa = new_loans*rwa_density
        closing = rwa*target
        distributions = income.common_earnings + capital - closing
        if distributions < 0 or closing/rwa < forecast_floor:
            raise ResearchGap("Forecast earnings cannot sustain the bank's modeled capital retention.")
        forecasts.append(BankYear(year, new_assets, new_deposits, new_loans, rwa, capital, closing, income,
                                  distributions, income.common_earnings-capital*cost_of_equity))
        assets, deposits, loans, other, capital = new_assets, new_deposits, new_loans, new_other, closing

    *explicit, terminal = forecasts
    dcf, residual = discount_common_equity(
        current.cet1, tuple(row.common_distributions for row in explicit),
        tuple(row.residual_income for row in explicit), terminal.common_distributions,
        terminal.residual_income, cost_of_equity, terminal_growth,
    )
    return BankValuation(tuple(explicit), terminal, dcf, residual, target, cost_of_equity, terminal_growth)

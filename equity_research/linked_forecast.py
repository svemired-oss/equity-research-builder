"""Link an operating forecast to financial statements and FCFF in USD millions."""

from dataclasses import dataclass
from datetime import date
from math import fsum, isclose, isfinite
from typing import Sequence

from .forecast_schedules import PPEBalance, PPEMovements, PPERollForward, roll_ppe
from .reconciliation import TOLERANCE_MILLIONS


@dataclass(frozen=True)
class WorkingCapital:
    """Operating balances; excludes restricted cash and all financing liabilities."""

    receivables_millions: float
    inventory_millions: float
    other_operating_assets_millions: float
    trade_payables_millions: float
    ordinary_deferred_revenue_millions: float
    other_operating_liabilities_millions: float

    @property
    def assets_millions(self) -> float:
        return fsum((self.receivables_millions, self.inventory_millions, self.other_operating_assets_millions))

    @property
    def liabilities_millions(self) -> float:
        return fsum((self.trade_payables_millions, self.ordinary_deferred_revenue_millions,
                     self.other_operating_liabilities_millions))

    @property
    def net_millions(self) -> float:
        return self.assets_millions - self.liabilities_millions


@dataclass(frozen=True)
class ForecastBalance:
    period_end: date
    cash_millions: float
    working_capital: WorkingCapital
    ppe: PPEBalance
    intangibles_millions: float
    operating_lease_assets_millions: float
    operating_lease_liabilities_millions: float
    notes_millions: float
    vendor_debt_millions: float
    finance_leases_millions: float
    capital_payables_millions: float
    customer_financing_millions: float
    dividends_payable_millions: float
    other_assets_millions: float
    other_liabilities_millions: float
    retained_earnings_millions: float
    other_equity_millions: float
    noncontrolling_interest_millions: float
    basic_shares_millions: float

    @property
    def assets_millions(self) -> float:
        return fsum((self.cash_millions, self.working_capital.assets_millions, self.ppe.net_millions,
                     self.intangibles_millions, self.operating_lease_assets_millions, self.other_assets_millions))

    @property
    def liabilities_millions(self) -> float:
        return fsum((self.working_capital.liabilities_millions, self.operating_lease_liabilities_millions,
                     self.notes_millions, self.vendor_debt_millions, self.finance_leases_millions,
                     self.capital_payables_millions, self.customer_financing_millions,
                     self.dividends_payable_millions, self.other_liabilities_millions))

    @property
    def parent_equity_millions(self) -> float:
        return self.retained_earnings_millions + self.other_equity_millions

    @property
    def equity_millions(self) -> float:
        return self.parent_equity_millions + self.noncontrolling_interest_millions


@dataclass(frozen=True)
class CapexFunding:
    """Sources of noncash PP&E additions and cash settlement of capital payables."""

    vendor_financed_additions_millions: float
    finance_lease_additions_millions: float
    capital_payables_incurred_millions: float
    capital_payables_paid_millions: float
    asset_disposal_proceeds_millions: float


@dataclass(frozen=True)
class OperatingLeaseFlows:
    """Model operating rent inside EBIT and recognize new leases at equal asset and liability."""

    lease_inceptions_millions: float
    rent_expense_millions: float
    cash_rent_millions: float
    liability_accretion_millions: float


@dataclass(frozen=True)
class DebtFlows:
    """Debt issuance proceeds are net cash and initial carrying value."""

    note_proceeds_millions: float
    note_repayments_millions: float
    note_discount_accretion_millions: float
    vendor_principal_paid_millions: float
    finance_lease_principal_paid_millions: float
    cash_interest_millions: float


@dataclass(frozen=True)
class CustomerFinancingFlows:
    """Changes to contract liabilities with a significant financing component."""

    received_millions: float
    refunded_millions: float
    interest_accretion_millions: float
    applied_to_revenue_millions: float


@dataclass(frozen=True)
class EquityFlows:
    """Parent cash flows and dividends exclude NCI distributions.

    Preferred dividends for earnings attribution can accrue before declaration.
    The stock compensation supplied here must already be an expense in EBITDA.
    """

    stock_compensation_millions: float
    dividends_declared_millions: float
    preferred_dividends_for_income_attribution_millions: float
    dividends_paid_millions: float
    stock_issuance_proceeds_millions: float
    stock_repurchase_cost_millions: float
    shares_sold_millions: float
    shares_vested_millions: float
    shares_repurchased_millions: float
    dilutive_shares_millions: float
    net_income_attributable_to_nci_millions: float
    nci_distributions_paid_millions: float


@dataclass(frozen=True)
class PeriodAssumptions:
    """One dated period. All flow inputs cover the entire period exactly once.

    Flow amounts are nonnegative magnitudes, except EBITDA and NCI income.
    The model expenses operating rent and equity-settled stock compensation
    in EBITDA. Other asset/liability balances carry forward. Taxes follow
    positive pretax profit at the supplied rate, with no loss benefit or
    deferred-tax movements.
    """

    period_end: date
    revenue_millions: float
    ebitda_after_sbc_and_operating_rent_millions: float
    tax_rate: float
    working_capital: WorkingCapital
    property: PPEMovements
    intangible_purchases_millions: float
    intangible_amortization_millions: float
    capex_funding: CapexFunding
    operating_lease: OperatingLeaseFlows
    debt: DebtFlows
    customer_financing: CustomerFinancingFlows
    equity: EquityFlows


@dataclass(frozen=True)
class ForecastPeriod:
    opening: ForecastBalance
    closing: ForecastBalance
    assumptions: PeriodAssumptions
    property: PPERollForward
    income_statement: dict[str, float]
    cash_flow: dict[str, float]
    funding_gap_millions: float


def _amount(value: float, name: str, *, signed: bool = False) -> None:
    if type(value) not in (int, float) or not isfinite(value):
        raise ValueError(f"{name} must be a finite number.")
    if not signed and value < 0:
        raise ValueError(f"{name} must be nonnegative.")


def _check(actual: float, expected: float, name: str) -> None:
    if not isclose(actual, expected, rel_tol=0.0, abs_tol=TOLERANCE_MILLIONS):
        raise ValueError(f"{name} does not reconcile by {actual - expected:,.6f} million.")


def _closing_balance(value: float, name: str) -> float:
    if value < -TOLERANCE_MILLIONS:
        raise ValueError(f"{name} cannot close below zero.")
    return max(0.0, value)


def _validate(opening: ForecastBalance, inputs: PeriodAssumptions) -> None:
    if type(opening.period_end) is not date or type(inputs.period_end) is not date or inputs.period_end <= opening.period_end:
        raise ValueError("Forecast period must end after the dated opening balance.")
    for field, value in vars(opening).items():
        if field not in {"period_end", "working_capital", "ppe"}:
            _amount(value, field, signed=field in {
                "cash_millions", "retained_earnings_millions", "other_equity_millions", "noncontrolling_interest_millions",
            })
    for group in (opening.ppe, opening.working_capital, inputs.working_capital, inputs.capex_funding,
                  inputs.operating_lease, inputs.debt, inputs.customer_financing, inputs.equity):
        for field, value in vars(group).items():
            _amount(value, field, signed=field == "net_income_attributable_to_nci_millions")
    _amount(inputs.revenue_millions, "Revenue")
    _amount(inputs.ebitda_after_sbc_and_operating_rent_millions, "EBITDA", signed=True)
    _amount(inputs.intangible_purchases_millions, "Intangible purchases")
    _amount(inputs.intangible_amortization_millions, "Intangible amortization")
    _amount(inputs.tax_rate, "Tax rate")
    if inputs.tax_rate > 1:
        raise ValueError("Tax rate must be a decimal proportion from zero to one.")
    if opening.basic_shares_millions <= 0:
        raise ValueError("Opening basic shares must be positive.")
    if inputs.customer_financing.applied_to_revenue_millions > inputs.revenue_millions:
        raise ValueError("Customer financing applied to revenue exceeds reported revenue.")
    if inputs.operating_lease.liability_accretion_millions > inputs.operating_lease.rent_expense_millions:
        raise ValueError("Operating lease accretion exceeds rent expense.")
    _check(opening.assets_millions, opening.liabilities_millions + opening.equity_millions, "Opening balance")


def forecast_period(opening: ForecastBalance, inputs: PeriodAssumptions) -> ForecastPeriod:
    """Project statements from the supplied operating, investing and financing changes.

    Economic FCFF treats owned capex at recognition, including leased and
    vendor-funded equipment. Significant customer financing cash movements
    stay in book CFO and are stripped from FCFF. A valuation on that basis
    separately accounts for outstanding capital payables and the economic
    value of outstanding customer financing. Operating lease rent remains
    an operating cost, so the valuation cannot also deduct its liability.
    A negative projected cash balance quantifies an unfunded scenario.
    """
    _validate(opening, inputs)
    ppe = roll_ppe(opening.ppe, inputs.property)
    capex = inputs.capex_funding
    lease = inputs.operating_lease
    debt = inputs.debt
    customer = inputs.customer_financing
    equity = inputs.equity
    _check(ppe.noncash_additions_millions,
           fsum((capex.vendor_financed_additions_millions, capex.finance_lease_additions_millions,
                 capex.capital_payables_incurred_millions)), "Noncash PP&E funding")
    cash_capex = ppe.cash_funded_additions_millions + capex.capital_payables_paid_millions
    da = inputs.property.depreciation_millions + inputs.intangible_amortization_millions
    ebit = inputs.ebitda_after_sbc_and_operating_rent_millions - da
    disposal_gain = capex.asset_disposal_proceeds_millions - ppe.disposal_net_book_value_millions
    interest = fsum((debt.cash_interest_millions, debt.note_discount_accretion_millions, customer.interest_accretion_millions))
    pretax = ebit + disposal_gain - interest
    taxes = max(0.0, pretax) * inputs.tax_rate
    income = pretax - taxes
    parent_income = income - equity.net_income_attributable_to_nci_millions
    wc_release = opening.working_capital.net_millions - inputs.working_capital.net_millions
    rent_adjustment = lease.rent_expense_millions - lease.cash_rent_millions
    customer_change = fsum((customer.received_millions, -customer.refunded_millions,
                            customer.interest_accretion_millions, -customer.applied_to_revenue_millions))
    cfo = fsum((income, da, equity.stock_compensation_millions, -disposal_gain, wc_release, rent_adjustment,
                customer_change, debt.note_discount_accretion_millions))
    cfi = fsum((-cash_capex, -inputs.intangible_purchases_millions, capex.asset_disposal_proceeds_millions))
    cff = fsum((debt.note_proceeds_millions, -debt.note_repayments_millions, -debt.vendor_principal_paid_millions,
                -debt.finance_lease_principal_paid_millions, equity.stock_issuance_proceeds_millions,
                -equity.stock_repurchase_cost_millions, -equity.dividends_paid_millions, -equity.nci_distributions_paid_millions))
    cash_change = fsum((cfo, cfi, cff))

    closing = ForecastBalance(
        period_end=inputs.period_end,
        cash_millions=opening.cash_millions + cash_change,
        working_capital=inputs.working_capital,
        ppe=ppe.closing,
        intangibles_millions=_closing_balance(
            opening.intangibles_millions + inputs.intangible_purchases_millions - inputs.intangible_amortization_millions,
            "Intangibles",
        ),
        operating_lease_assets_millions=_closing_balance(
            opening.operating_lease_assets_millions + lease.lease_inceptions_millions
            - lease.rent_expense_millions + lease.liability_accretion_millions, "Operating lease assets",
        ),
        operating_lease_liabilities_millions=_closing_balance(
            opening.operating_lease_liabilities_millions + lease.lease_inceptions_millions
            + lease.liability_accretion_millions - lease.cash_rent_millions, "Operating lease liabilities",
        ),
        notes_millions=_closing_balance(
            opening.notes_millions + debt.note_proceeds_millions - debt.note_repayments_millions
            + debt.note_discount_accretion_millions, "Notes",
        ),
        vendor_debt_millions=_closing_balance(
            opening.vendor_debt_millions + capex.vendor_financed_additions_millions - debt.vendor_principal_paid_millions,
            "Vendor debt",
        ),
        finance_leases_millions=_closing_balance(
            opening.finance_leases_millions + capex.finance_lease_additions_millions - debt.finance_lease_principal_paid_millions,
            "Finance leases",
        ),
        capital_payables_millions=_closing_balance(
            opening.capital_payables_millions + capex.capital_payables_incurred_millions - capex.capital_payables_paid_millions,
            "Capital payables",
        ),
        customer_financing_millions=_closing_balance(opening.customer_financing_millions + customer_change, "Customer financing"),
        dividends_payable_millions=_closing_balance(
            opening.dividends_payable_millions + equity.dividends_declared_millions - equity.dividends_paid_millions,
            "Dividends payable",
        ),
        other_assets_millions=opening.other_assets_millions,
        other_liabilities_millions=opening.other_liabilities_millions,
        retained_earnings_millions=opening.retained_earnings_millions + parent_income - equity.dividends_declared_millions,
        other_equity_millions=fsum((opening.other_equity_millions, equity.stock_compensation_millions,
                                  equity.stock_issuance_proceeds_millions, -equity.stock_repurchase_cost_millions)),
        noncontrolling_interest_millions=fsum((opening.noncontrolling_interest_millions,
                                              equity.net_income_attributable_to_nci_millions,
                                              -equity.nci_distributions_paid_millions)),
        basic_shares_millions=fsum((opening.basic_shares_millions, equity.shares_sold_millions,
                                  equity.shares_vested_millions, -equity.shares_repurchased_millions)),
    )
    _check(closing.assets_millions, closing.liabilities_millions + closing.equity_millions, "Closing balance")
    if closing.basic_shares_millions <= 0:
        raise ValueError("Closing basic shares must be positive.")

    operating_taxes = max(0.0, ebit) * inputs.tax_rate
    incremental_disposal_taxes = max(0.0, ebit + disposal_gain) * inputs.tax_rate - operating_taxes
    nopat = ebit - operating_taxes
    fcff = fsum((nopat, da, wc_release, rent_adjustment, -ppe.total_additions_millions,
                 -inputs.intangible_purchases_millions, capex.asset_disposal_proceeds_millions, -incremental_disposal_taxes))
    _check(fcff, fsum((
        cfo, cfi, nopat, -income, -equity.stock_compensation_millions, disposal_gain,
        -customer_change, -debt.note_discount_accretion_millions, cash_capex, -ppe.total_additions_millions,
        -incremental_disposal_taxes,
    )), "FCFF to statement cash flow")

    return ForecastPeriod(
        opening, closing, inputs, ppe,
        {
            "revenue": inputs.revenue_millions,
            "operating_expenses_excluding_da": inputs.revenue_millions - inputs.ebitda_after_sbc_and_operating_rent_millions,
            "ebitda": inputs.ebitda_after_sbc_and_operating_rent_millions,
            "depreciation_and_amortization": da,
            "operating_income": ebit,
            "asset_disposal_gain": disposal_gain,
            "interest_expense": interest,
            "income_before_taxes": pretax,
            "income_taxes": taxes,
            "consolidated_net_income": income,
            "net_income_attributable_to_nci": equity.net_income_attributable_to_nci_millions,
            "net_income_attributable_to_parent": parent_income,
            "net_income_available_to_common": parent_income - equity.preferred_dividends_for_income_attribution_millions,
            "ending_basic_shares": closing.basic_shares_millions,
            "ending_diluted_shares": closing.basic_shares_millions + equity.dilutive_shares_millions,
        },
        {
            "cash_from_operations": cfo,
            "cash_from_investing": cfi,
            "cash_from_financing": cff,
            "net_cash_change": cash_change,
            "working_capital_cash_release": wc_release,
            "operating_lease_cash_adjustment": rent_adjustment,
            "change_in_customer_financing": customer_change,
            "cash_capex": cash_capex,
            "economic_ppe_additions": ppe.total_additions_millions,
            "nopat": nopat,
            "unlevered_free_cash_flow": fcff,
        },
        funding_gap_millions=max(0.0, -closing.cash_millions),
    )


def forecast_periods(opening: ForecastBalance, assumptions: Sequence[PeriodAssumptions]) -> tuple[ForecastPeriod, ...]:
    """Run consecutive modeled intervals using each closing balance as the next opening."""
    periods = []
    for inputs in assumptions:
        period = forecast_period(opening, inputs)
        periods.append(period)
        opening = period.closing
    return tuple(periods)

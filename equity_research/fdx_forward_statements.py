"""FedEx's continuing-operations statement forecasts and cash for a later FCFF valuation."""

from dataclasses import dataclass, replace
from datetime import date
from math import fsum
from pathlib import Path

from .fdx_current import Q3_END, Q4_END, _dated_source, _item, _load, _num, _reconcile, _sources, run_current_fdx
from .fdx_current_cash import build_fdx_cash
from .fdx_forward_operations import run_fdx_forward_operations
from .gd_inputs import _array, _day


@dataclass(frozen=True)
class WorkingCapital:
    receivables: float
    spare_parts_and_supplies: float
    operating_prepaids: float
    trade_and_accrued_liabilities: float

    @property
    def net(self) -> float:
        return self.receivables + self.spare_parts_and_supplies + self.operating_prepaids - self.trade_and_accrued_liabilities

    def scale(self, factor: float) -> "WorkingCapital":
        return WorkingCapital(self.receivables * factor, self.spare_parts_and_supplies * factor,
                              self.operating_prepaids * factor, self.trade_and_accrued_liabilities * factor)


@dataclass(frozen=True)
class Property:
    gross: float
    accumulated_depreciation: float
    intangibles: float

    @property
    def net_property(self) -> float:
        return self.gross - self.accumulated_depreciation


@dataclass(frozen=True)
class Debt:
    opening_notes: float
    opening_finance_leases: float
    note_principal_paid: float
    new_notes: float
    note_interest: float
    finance_lease_principal_paid: float
    finance_lease_interest: float
    new_finance_leases: float
    closing_notes: float
    closing_finance_leases: float

    @property
    def interest(self) -> float:
        return self.note_interest + self.finance_lease_interest


@dataclass(frozen=True)
class Balance:
    cash_before_reserve: float
    working_capital: WorkingCapital
    property: Property
    other_assets: dict[str, float]
    other_liabilities: dict[str, float]
    notes: float
    finance_leases: float
    dividends_payable: float
    equity: dict[str, float]

    @property
    def assets(self) -> float:
        wc = self.working_capital
        return fsum((self.cash_before_reserve, wc.receivables, wc.spare_parts_and_supplies, wc.operating_prepaids,
                     self.property.net_property, self.property.intangibles, *self.other_assets.values()))

    @property
    def liabilities(self) -> float:
        return fsum((self.working_capital.trade_and_accrued_liabilities, self.notes, self.finance_leases,
                     self.dividends_payable, *self.other_liabilities.values()))


@dataclass(frozen=True)
class ModelPeriod:
    period_end: date
    income_statement: dict[str, float]
    balance_sheet: Balance
    cash_flow: dict[str, float]
    debt_schedule: Debt
    supporting_schedules: dict[str, float | None]


def _dated_inputs(inputs_dir, name, as_of):
    document = _load(inputs_dir / f"fdx-{name}-{as_of.isoformat()}.json")
    if document.get("as_of") != as_of.isoformat():
        raise ValueError(f"FedEx {name} uses a different evidence cutoff.")
    return document


def _statement_parameters(document, as_of):
    sources = _sources(document, as_of)

    def input_value(key):
        entry = _item(document, key, "statement assumptions")
        _dated_source(_item(entry, "source", key), sources, key)
        return _num(entry, "value", key, minimum=0)

    values = {name: input_value(name) for name in (
        "q3_stock_compensation_millions", "dilutive_stock_awards_millions", "june_september_finance_lease_additions_millions",
        "may_net_intangibles_millions", "normalized_tax_rate", "annual_pension_contribution_millions", "capex_target_share_of_revenue",
    )}
    for name in ("normalized_tax_rate", "capex_target_share_of_revenue"):
        if not 0 < values[name] < 1:
            raise ValueError(f"{name} must be a proportion between zero and one.")
    schedule = _item(document, "may_fiscal_amortization_millions")
    _dated_source(_item(schedule, "source"), sources, "May fiscal-year amortization")
    rates = _item(schedule, "values")
    amort = {year: _num(rates, str(year), "May fiscal amortization", minimum=0) for year in range(2027, 2032)}
    return values, amort


def _financing_parameters(document, actuals, capital, as_of, checks):
    sources = _sources(document, as_of)
    debt = _item(document, "opening_debt")
    if debt["as_of"] != Q3_END.isoformat():
        raise ValueError("The opening debt must come from the September 30 balance sheet.")
    gross_debt = _num(debt, "total_carrying_millions", minimum=0)
    lease = _num(debt, "finance_lease_carrying_millions", minimum=0)
    notes = _num(debt, "notes_carrying_millions", minimum=0)
    _dated_source(debt["source"], sources, "Opening debt", observed_on=Q3_END)
    _reconcile(checks, "Financing input to September carrying debt", gross_debt, capital["reported_debt_including_finance_leases_millions"])
    _reconcile(checks, "Financing input to September finance lease liability", lease, capital["finance_leases_included_millions"])
    _reconcile(checks, "Notes plus finance leases to carrying debt", notes + lease, gross_debt)
    rates = (_num(debt, "reported_notes_weighted_coupon", minimum=0),
             _num(document, "finance_lease_discount_rate_proxy", minimum=0))
    if max(rates) >= 1:
        raise ValueError("Interest rates must be decimal annual rates below 100%.")
    note_maturities = _item(document, "note_principal_millions")
    _verify_note_maturities(document, note_maturities, sources, checks)
    lease_payments = _item(document, "finance_lease_cash_payments_millions")
    maturities = {year: (_num(note_maturities, str(year), "note maturity", minimum=0),
                         _num(lease_payments, str(year), "finance lease payment", minimum=0)) for year in range(2026, 2032)}
    reported_leases = actuals["debt_and_lease_disclosures"]["finance_lease_undiscounted_payments"]
    for year in range(2026, 2031):
        _reconcile(checks, f"{year} finance lease payments to filing", maturities[year][1],
                   _num(reported_leases, "2026_remainder" if year == 2026 else str(year), minimum=0))
    dividends = _item(document, "dividends")
    paid = _num(dividends, "october_1_paid_cash_millions", minimum=0)
    shares = _num(dividends, "forecast_opening_shares", minimum=1)
    quarterly = _num(dividends, "forecast_quarterly_per_share", minimum=0)
    if _day(dividends["october_1_paid_date"], "Dividend payment date") != date(2026, 10, 1):
        raise ValueError("The October dividend payment date differs from the modeled cash window.")
    _dated_source(dividends["october_1_paid_source"], sources, "October dividend payment", observed_on=date(2026, 10, 1))
    _reconcile(checks, "October share count across snapshots", shares, capital["fdx_shares"], 0)
    _reconcile(checks, "Annual and quarterly dividend policy", quarterly * 4, _num(dividends, "forecast_annual_per_share", minimum=0))
    return notes, lease, rates, maturities, shares / 1e6, paid, quarterly


def _verify_note_maturities(document, maturities, sources, checks):
    basis = _item(document, "note_principal_basis")
    fx = _num(basis, "usd_per_eur", minimum=0.01)
    _dated_source(basis["fx_source"], sources, "Debt schedule EUR/USD", observed_on=Q3_END)
    if _day(basis["fx_observed_on"], "FX observation date") != Q3_END:
        raise ValueError("Translate note principal at the September balance-sheet reference rate.")
    certificates = _item(basis, "aircraft_certificates")
    _dated_source(certificates["source"], sources, "Aircraft certificates")
    payments = _array(certificates["distribution_month_days"], "Certificate distribution dates")
    principal = _num(certificates, "scheduled_principal_per_distribution_millions", minimum=0)
    totals = {year: principal * sum(date.fromisoformat(f"{year}-{day}") > Q3_END for day in payments) for year in range(2026, 2033)}
    for note in _array(basis["senior_note_maturities"], "Dated notes"):
        maturity = _day(_item(note, "matures_on"), "Note maturity date")
        currency = _item(note, "currency")
        if currency not in ("EUR", "USD") or not Q3_END < maturity <= date(2032, 12, 31):
            raise ValueError("Expected a USD or EUR note maturing between the balance sheet and 2032.")
        for source in _array(note["source_ids"], "Note sources"):
            _dated_source(source, sources, f"Note maturing on {maturity}")
        totals[maturity.year] += _num(note, "principal_millions", minimum=0) * (fx if currency == "EUR" else 1)
    for year, value in totals.items():
        _reconcile(checks, f"{year} note and aircraft certificate principal", value, _num(maturities, str(year), minimum=0))


def _opening_balance(actuals, capital, notes, finance_leases, payable, intangible, checks):
    reported = actuals["balance_sheets"][Q3_END.isoformat()]
    sale = actuals["transactions"]["fedex_supply_chain_sale"]["held_for_sale_balances_2026_09_30"]
    refunds = capital["tariff_cash_for_customers_millions"]
    debt_securities = capital["other_debt_securities_millions"]
    equity_securities = capital["other_equity_securities_millions"]
    working_capital = WorkingCapital(
        reported["receivables"], reported["spare_parts_supplies_and_fuel"],
        reported["prepaids_and_other"] - debt_securities - sale["current_assets"],
        reported["payables"] + reported["other_accrued_liabilities"] + reported["accrued_salaries_and_benefits"]
        - refunds - payable - sale["current_liabilities"],
    )
    balance = Balance(
        cash_before_reserve=capital["cash_after_customer_obligation_before_operating_reserve_millions"],
        working_capital=working_capital,
        property=Property(reported["gross_ppe"], -reported["accumulated_depreciation"], intangible),
        other_assets={
            "operating_lease_assets": reported["operating_lease_rou_assets"],
            "goodwill_and_other_operating_assets": reported["goodwill_and_other_assets"] - equity_securities - sale["noncurrent_assets"] - intangible,
            "incremental_pension_funding": 0.0,
            "retained_freight_investment": reported["short_term_investment"],
            "inpost_investment": reported["equity_method_investment"],
            "debt_securities": debt_securities, "other_equity_securities": equity_securities,
            "supply_chain_held_for_sale": sale["current_assets"] + sale["noncurrent_assets"],
        },
        other_liabilities={
            "operating_leases": capital["operating_leases_separate_millions"],
            "self_insurance": reported["self_insurance_accruals"], "deferred_tax": reported["deferred_income_taxes"],
            "other_noncurrent_liabilities": reported["other_noncurrent_liabilities"] - sale["noncurrent_liabilities"],
            "supply_chain_held_for_sale": sale["current_liabilities"] + sale["noncurrent_liabilities"],
        },
        notes=notes, finance_leases=finance_leases, dividends_payable=payable,
        equity={line: reported[line] for line in ("common_stock", "additional_paid_in_capital", "retained_earnings",
                                                  "accumulated_other_comprehensive_loss", "treasury_stock")},
    )
    _reconcile(checks, "September assets after refund cash", balance.assets, reported["total_assets"] - refunds)
    _reconcile(checks, "September liability and equity classification", balance.liabilities + fsum(balance.equity.values()), balance.assets)
    return balance


def _debt(opening, maturities, rates, portion, *, new_notes=0.0, new_leases=0.0):
    notes_paid, lease_payment = maturities
    closing_notes = opening.notes - notes_paid + new_notes
    notes_interest = (opening.notes + closing_notes) / 2 * rates[0] * portion
    lease_rate = rates[1] * portion
    # Interest on the average liability, with payment equal to interest plus principal.
    lease_interest = lease_rate * (opening.finance_leases + (new_leases - lease_payment) / 2) / (1 - lease_rate / 2)
    lease_principal = lease_payment - lease_interest
    closing_lease = opening.finance_leases + new_leases - lease_principal
    if min(closing_notes, closing_lease, lease_principal, lease_interest) < 0:
        raise ValueError("Contracted financing cash flows produce an invalid balance or lease payment.")
    return Debt(opening.notes, opening.finance_leases, notes_paid, new_notes, notes_interest, lease_principal,
                lease_interest, new_leases, closing_notes, closing_lease)


def _property(opening, capex, depreciation, amortization, gross_retired, debt):
    updated = Property(
        opening.gross + capex + debt.new_finance_leases - gross_retired,
        opening.accumulated_depreciation + depreciation - gross_retired,
        opening.intangibles - amortization,
    )
    if min(depreciation, amortization, updated.gross, updated.net_property, updated.accumulated_depreciation, updated.intangibles) < 0:
        raise ValueError("Projected capex, depreciation or amortization produces a negative asset balance.")
    return updated


def _close_period(opening, *, end, revenue, ebit, working_capital, property_schedule, debt, tax_rate, sbc, capex, pension,
                  shares, diluted_shares, dividends_declared, dividends_paid, sale_proceeds, nonoperating_income, checks):
    property_close, depreciation, amortization, gross_retired = property_schedule
    da = depreciation + amortization
    pretax = ebit - debt.interest + nonoperating_income
    taxes = pretax * tax_rate
    income = pretax - taxes
    wc_release = opening.working_capital.net - working_capital.net
    cfo = income + da + sbc + wc_release - pension
    if sale_proceeds:
        sale_equity_adjustment = sale_proceeds - opening.other_assets["supply_chain_held_for_sale"] + opening.other_liabilities["supply_chain_held_for_sale"]
    else:
        sale_equity_adjustment = 0.0
    investing = -capex + sale_proceeds
    net_borrowing = debt.new_notes - debt.note_principal_paid - debt.finance_lease_principal_paid
    financing = net_borrowing - dividends_paid
    end_cash = fsum((opening.cash_before_reserve, cfo, investing, financing))
    other_assets = {**opening.other_assets, "incremental_pension_funding": opening.other_assets["incremental_pension_funding"] + pension}
    other_liabilities = dict(opening.other_liabilities)
    if sale_proceeds:
        other_assets["supply_chain_held_for_sale"] = 0.0
        other_liabilities["supply_chain_held_for_sale"] = 0.0
    equity = {**opening.equity,
              "additional_paid_in_capital": opening.equity["additional_paid_in_capital"] + sbc,
              "retained_earnings": opening.equity["retained_earnings"] + income - dividends_declared + sale_equity_adjustment}
    closing = Balance(end_cash, working_capital, property_close, other_assets, other_liabilities,
                      debt.closing_notes, debt.closing_finance_leases,
                      opening.dividends_payable + dividends_declared - dividends_paid, equity)
    if closing.cash_before_reserve < 0 or closing.dividends_payable < 0 or revenue <= 0 or diluted_shares <= 0:
        raise ValueError(f"{end} requires a financing, dividend or operating assumption to close the forecast.")
    _reconcile(checks, f"{end} assets to liabilities and equity", closing.assets, closing.liabilities + fsum(closing.equity.values()))
    _reconcile(checks, f"{end} net PP&E roll-forward", closing.property.net_property,
               opening.property.net_property + capex + debt.new_finance_leases - depreciation)
    _reconcile(checks, f"{end} intangible roll-forward", opening.property.intangibles - amortization, closing.property.intangibles)
    _reconcile(checks, f"{end} debt cash and noncash roll-forward", closing.notes + closing.finance_leases,
               opening.notes + opening.finance_leases + net_borrowing + debt.new_finance_leases)
    _reconcile(checks, f"{end} retained earnings roll-forward", equity["retained_earnings"] - opening.equity["retained_earnings"],
               income - dividends_declared + sale_equity_adjustment)
    nopat = ebit * (1 - tax_rate)
    # Treat share compensation as an economic expense and lease-financed purchases as reinvestment.
    fcff = nopat + da + wc_release - capex - pension - debt.new_finance_leases
    _reconcile(checks, f"{end} FCFF to book cash flow", fcff, cfo - capex + nopat - income - sbc - debt.new_finance_leases)
    return ModelPeriod(
        end,
        {"revenue": revenue, "operating_expenses_excluding_da": revenue - ebit - da, "depreciation_and_amortization": da,
         "operating_income": ebit, "interest_expense": -debt.interest, "other_core_nonoperating_income": nonoperating_income,
         "income_before_taxes": pretax, "income_taxes": taxes, "net_income": income,
         "basic_shares_millions": shares, "diluted_shares_millions": diluted_shares, "diluted_eps_proxy": income / diluted_shares},
        closing,
        {"net_income": income, "depreciation_and_amortization": da, "stock_compensation": sbc,
         "working_capital_cash_release": wc_release, "voluntary_pension_contribution": -pension, "cash_from_operations": cfo,
         "capex": -capex, "supply_chain_sale_proceeds": sale_proceeds, "cash_from_investing": investing,
         "note_principal_paid": -debt.note_principal_paid, "finance_lease_principal_paid": -debt.finance_lease_principal_paid,
         "note_issuance_proceeds": debt.new_notes, "dividends_paid": -dividends_paid, "cash_from_financing": financing,
         "opening_cash": opening.cash_before_reserve, "closing_cash": end_cash, "net_cash_change": end_cash - opening.cash_before_reserve,
         "operating_cash_after_capex": cfo - capex, "nopat": nopat, "unlevered_free_cash_flow": fcff},
        debt,
        {"net_operating_working_capital": working_capital.net, "gross_property_retired": gross_retired,
         "property_depreciation": depreciation, "intangible_amortization": amortization, "dividends_declared": dividends_declared,
         "provisional_pretax_sale_equity_adjustment": sale_equity_adjustment,
         "noncash_finance_lease_additions": debt.new_finance_leases, "net_property": closing.property.net_property,
         "total_assets": closing.assets, "total_liabilities": closing.liabilities, "total_equity": fsum(closing.equity.values()),
         "total_financing_debt": closing.notes + closing.finance_leases},
    )


def _fourth_quarter(actuals, current, opening, cash, debt, params, amortization, shares, dividend, paid, retirement_rate, checks):
    income_rows = actuals["income_statements"]
    historical = actuals["balance_sheets"]["2025-12-31"]
    q4 = cash.fourth_quarter
    capex = q4.continuing_capex_millions
    pension = q4.voluntary_pension_contribution_millions
    sbc = params["q3_stock_compensation_millions"]
    tax_rate = params["normalized_tax_rate"]
    revenue = current["q4_revenue"]["revenue_millions"]
    guide = current["guidance_bridge"]
    ebit = guide["q4_implied_adjusted_operating_income_millions"]
    target_income = fsum(guide["q4_implied_adjusted_net_income_range_millions"]) / 2
    diluted = guide["q4_average_diluted_shares_guidance_millions"]
    q4_depreciation = (income_rows["2026_q3"]["depreciation_amortization"] * income_rows["2025_q4"]["depreciation_amortization"]
                       / income_rows["2025_q3"]["depreciation_amortization"] - amortization)
    retired = opening.property.gross * retirement_rate * q4.calendar_days / 365
    prop = _property(opening.property, capex, q4_depreciation, amortization, retired, debt)
    implied_wc_release = q4.operating_cfo_excluding_tariffs_millions - (target_income + q4_depreciation + amortization + sbc - pension)
    closing_nwc = opening.working_capital.net - implied_wc_release
    seasonal_factor = revenue / income_rows["2025_q4"]["revenue"]
    # Receivables and other current assets follow the latest comparable holiday quarter.
    # Operating payables and accruals carry the cash conversion implied by the issuer cash bridge.
    receivables = historical["receivables"] * seasonal_factor
    supplies = historical["spare_parts_supplies_and_fuel"] * seasonal_factor
    prepaids = historical["prepaids_and_other"] * seasonal_factor
    accruals = receivables + supplies + prepaids - closing_nwc
    if accruals < 0:
        raise ValueError("The issuer cash bridge implies negative Q4 operating payables and accruals.")
    wc = WorkingCapital(receivables, supplies, prepaids, accruals)
    net_other = target_income / (1 - tax_rate) - ebit + debt.interest
    modeled = _close_period(
        opening, end=Q4_END, revenue=revenue, ebit=ebit, working_capital=wc,
        property_schedule=(prop, q4_depreciation, amortization, retired), debt=debt, tax_rate=tax_rate, sbc=sbc,
        capex=capex, pension=pension, shares=shares, diluted_shares=diluted, dividends_declared=shares * dividend,
        dividends_paid=paid, sale_proceeds=q4.supply_chain_sale_cash_millions, nonoperating_income=net_other, checks=checks,
    )
    _reconcile(checks, "Q4 estimated operating cash excluding tariff pass-throughs", modeled.cash_flow["cash_from_operations"],
               q4.operating_cfo_excluding_tariffs_millions)
    _reconcile(checks, "Q4 adjusted earnings midpoint", modeled.income_statement["net_income"], target_income)
    _reconcile(checks, "Q4 continuing capex guidance bridge", capex, guide["q4_capex_implied_by_rounded_guidance_millions"])
    _reconcile(checks, "Supply Chain proceeds appear once", modeled.cash_flow["supply_chain_sale_proceeds"],
               current["capital_inputs"]["supply_chain_net_cash_proceeds_millions"])
    return modeled


def _remaining_cash(cash, q4, paid, checks):
    before = cash.through_cutoff.calendar_days / cash.fourth_quarter.calendar_days
    borrowing = q4.debt_schedule.new_notes - q4.debt_schedule.note_principal_paid - q4.debt_schedule.finance_lease_principal_paid
    cutoff_cash = cash.available_cash_before_financing_at_cutoff_millions - paid + borrowing * before
    remaining = cash.after_cutoff
    post_cash_change = remaining.operating_cash_after_capex_millions + remaining.supply_chain_sale_cash_millions + borrowing * (1 - before)
    _reconcile(checks, "November 12 cutoff and remaining cash to Q4 ending cash", cutoff_cash + post_cash_change, q4.balance_sheet.cash_before_reserve)
    conversion = q4.cash_flow["unlevered_free_cash_flow"] - q4.cash_flow["operating_cash_after_capex"]
    remaining_fcff = remaining.operating_cash_after_capex_millions + conversion * (1 - before)
    return {
        "as_of": cash.as_of, "period_end": q4.period_end,
        "cash_before_operating_reserve_millions": cutoff_cash,
        "remaining_operating_cash_after_capex_millions": remaining.operating_cash_after_capex_millions,
        "remaining_unlevered_free_cash_flow_millions": remaining_fcff,
        "customer_refund_obligation_millions": cash.cutoff_customer_refund_obligation_millions,
        "estimated_financing_debt_millions": q4.debt_schedule.opening_notes + q4.debt_schedule.opening_finance_leases
        + (borrowing + q4.debt_schedule.new_finance_leases) * before,
        "method": "Uses the operating cash bridge through November 11. Assigns the October 1 dividend and Supply Chain proceeds to their observed date. Prorates modeled borrowing and the conversion from levered operating cash to FCFF over calendar days.",
    }


def _forecast_year(opening, prior_revenue, operating, maturities, rates, funding, depreciation_rate, retirement_rate,
                   amortization, capex_rate, params, sbc_rate, shares, quarterly_dividend, checks):
    revenue = operating.revenue_millions
    cash_capex = revenue * capex_rate
    wc = opening.working_capital.scale(revenue / prior_revenue)
    debt = _debt(opening, maturities, rates, 1.0, new_notes=funding[0], new_leases=funding[1])
    retired = opening.property.gross * retirement_rate
    ending_gross = opening.property.gross + cash_capex + debt.new_finance_leases - retired
    depreciation = (opening.property.gross + ending_gross) / 2 * depreciation_rate
    prop = _property(opening.property, cash_capex, depreciation, amortization, retired, debt)
    return _close_period(
        opening, end=operating.period_end, revenue=revenue, ebit=operating.operating_income_millions, working_capital=wc,
        property_schedule=(prop, depreciation, amortization, retired), debt=debt, tax_rate=params["normalized_tax_rate"],
        sbc=revenue * sbc_rate, capex=cash_capex, pension=params["annual_pension_contribution_millions"],
        shares=shares, diluted_shares=shares + params["dilutive_stock_awards_millions"],
        dividends_declared=shares * quarterly_dividend * 4, dividends_paid=opening.dividends_payable + shares * quarterly_dividend * 3,
        sale_proceeds=0, nonoperating_income=0, checks=checks,
    )


def _classify_debt(period, next_notes, next_lease_payment, rates):
    opening = period.balance_sheet
    lease_principal = None
    current = None
    if next_lease_payment is not None:
        projected = _debt(opening, (next_notes, next_lease_payment), rates, 1)
        lease_principal = projected.finance_lease_principal_paid
        current = next_notes + lease_principal
    return replace(period, supporting_schedules={
        **period.supporting_schedules, "scheduled_note_principal_next_year": next_notes,
        "estimated_finance_lease_principal_next_year": lease_principal,
        "estimated_current_financing_debt": current,
        "estimated_noncurrent_financing_debt": None if current is None else opening.notes + opening.finance_leases - current,
    })


def run_fdx_statements(as_of: date, inputs_dir: Path, *, case: str = "base", guided: Path | None = None) -> dict:
    """Return Q4 cash, cutoff cash and debt, and linked December fiscal-year statements."""
    current = run_current_fdx(as_of, inputs_dir, guided=guided)
    actuals = _dated_inputs(inputs_dir, "actuals", as_of)
    cash_inputs = _dated_inputs(inputs_dir, "cash", as_of)
    financing_inputs = _dated_inputs(inputs_dir, "financing", as_of)
    statement_inputs = _dated_inputs(inputs_dir, "statement-assumptions", as_of)
    cash = build_fdx_cash(actuals, cash_inputs)
    operating = run_fdx_forward_operations(as_of, inputs_dir, case=case, current=current)
    params, may_amort = _statement_parameters(statement_inputs, as_of)
    checks = []
    capital = current["capital_inputs"]
    notes, lease, rates, maturities, shares, paid, dividend = _financing_parameters(financing_inputs, actuals, capital, as_of, checks)
    funding = _item(financing_inputs, "funding_assumptions")
    new_notes = _num(funding, "new_note_proceeds_millions_per_period", minimum=0)
    new_leases = _num(funding, "new_finance_lease_funding_millions_per_period", minimum=0)
    for field in ("stock_issuance_proceeds_millions_per_period", "share_repurchase_cash_millions_per_period"):
        if _num(funding, field, minimum=0) != 0:
            raise ValueError(f"{field} requires a dated share-count bridge and is unavailable in the current model.")
    opening_intangibles = params["may_net_intangibles_millions"] - may_amort[2027] * 4 / 12
    opening = _opening_balance(actuals, capital, notes, lease, paid, opening_intangibles, checks)
    may_gross = actuals["balance_sheets"]["2026-05-31"]["gross_ppe"]
    actual_capex = actuals["capital_spending"]["2026_transition_through_q3"]["total"]
    gross_retirement_proxy = may_gross + actual_capex + params["june_september_finance_lease_additions_millions"] - opening.property.gross
    if gross_retirement_proxy < 0:
        raise ValueError("The PP&E roll-forward requires a source for other gross additions.")
    retirement_rate = gross_retirement_proxy / may_gross * 3
    q4_debt = _debt(opening, maturities[2026], rates, cash.fourth_quarter.calendar_days / 365,
                    new_notes=new_notes, new_leases=new_leases)
    q4 = _fourth_quarter(actuals, current, opening, cash, q4_debt, params, may_amort[2027] * 3 / 12,
                         shares, dividend, paid, retirement_rate, checks)
    q4 = _classify_debt(q4, *maturities[2027], rates)
    cutoff = _remaining_cash(cash, q4, paid, checks)
    revenue_26 = current["calendar_year"]["forecast_revenue_millions"]
    capex_26 = cash.continuing_nine_month_capex_millions + cash.fourth_quarter.continuing_capex_millions
    capex_rate_26 = capex_26 / revenue_26
    capex_target = params["capex_target_share_of_revenue"]
    da_26 = actuals["income_statements"]["2026_ytd"]["depreciation_amortization"] + q4.income_statement["depreciation_and_amortization"]
    average_gross = (actuals["balance_sheets"]["2025-12-31"]["gross_ppe"] + q4.balance_sheet.property.gross) / 2
    # May-year amortization is a proxy for the small intangible share of CY2026 D&A.
    depreciation_rate = (da_26 - may_amort[2027]) / average_gross
    sbc_rate = params["q3_stock_compensation_millions"] / actuals["income_statements"]["2026_q3"]["revenue"]
    yearly = []
    balance = q4.balance_sheet
    previous_revenue = revenue_26
    for forecast, capex_rate in zip(operating.years, (capex_rate_26, (capex_rate_26 + capex_target) / 2, *([capex_target] * 3)), strict=True):
        year = forecast.period_end.year
        amortization = (5 * may_amort[year] + 7 * may_amort[min(year + 1, 2031)]) / 12
        modeled = _forecast_year(balance, previous_revenue, forecast, maturities[year], rates, (new_notes, new_leases),
                                 depreciation_rate, retirement_rate, amortization, capex_rate, params, sbc_rate, shares, dividend, checks)
        next_maturities = maturities.get(year + 1)
        if next_maturities is None:
            next_maturities = (_num(financing_inputs["note_principal_millions"], str(year + 1), minimum=0), None)
        modeled = _classify_debt(modeled, *next_maturities, rates)
        yearly.append(modeled)
        balance = modeled.balance_sheet
        previous_revenue = forecast.revenue_millions
    return {
        "as_of": as_of, "case": case, "opening_september_balance": opening, "q4_operating_cash_bridge": cash,
        "cutoff": cutoff, "q4": q4, "operating_forecast": operating, "years": tuple(yearly),
        "depreciation_rate_on_average_gross_property": depreciation_rate,
        "estimated_gross_property_retirement_rate": retirement_rate, "checks": tuple(checks),
        "source_register": {**current["source_register"], "cash": cash_inputs["sources"], "financing": financing_inputs["sources"],
                            "forward_operations": operating.sources,
                            "statement_assumptions": statement_inputs["sources"]},
        "limitations": (*statement_inputs["limitations"], *cash.limitations, *operating.limitations),
    }

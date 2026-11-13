"""Newmont's dated opening balances and linked Q4 2026 and 2027 statements."""

from dataclasses import dataclass
from datetime import date
from math import fsum
from pathlib import Path

from .fdx_current import _dated_source, _load, _num, _reconcile, _sources
from .nem_capital import build_nem_capital_schedule
from .nem_current import _evidence, run_current_nem


Q4 = "q4_2026"
FY27 = "fy_2027"
SEPTEMBER = "2026-09-30"
DECEMBER = "2025-12-31"
CURRENT_ASSETS = (
    "cash", "trade_receivables", "current_investments", "inventories", "current_stockpiles_and_ore_on_leach_pads",
    "other_receivables", "other_current_assets",
)
NONCURRENT_ASSETS = (
    "net_property_plant_mine_development", "noncurrent_investments", "noncurrent_stockpiles_and_ore_on_leach_pads",
    "deferred_tax_assets", "goodwill", "other_noncurrent_assets",
)
CURRENT_LIABILITIES = (
    "accounts_payable", "current_employee_benefits", "income_mining_taxes_payable", "current_lease_financing_obligations",
    "other_current_liabilities",
)
NONCURRENT_LIABILITIES = (
    "long_term_debt", "noncurrent_lease_financing_obligations", "noncurrent_reclamation_remediation",
    "deferred_tax_liabilities", "noncurrent_employee_benefits", "noncurrent_silver_stream", "other_noncurrent_liabilities",
)
PARENT_EQUITY = (
    "common_stock", "treasury_stock", "additional_paid_in_capital", "accumulated_other_comprehensive_income", "retained_earnings",
)
OPERATING_EXPENSES = (
    "costs_applicable_to_sales", "depreciation_amortization", "reclamation_remediation", "exploration", "advanced_projects",
    "general_administrative", "impairment_charges", "gain_loss_on_assets_held_for_sale", "other_expense_net",
)
CASH_FLOW_ITEMS = {
    "cash_from_operations": (
        "net_income", "depreciation_amortization", "impairment_charges", "gain_loss_on_assets_held_for_sale_adjustment",
        "discontinued_net_income_adjustment", "reclamation_remediation_noncash", "deferred_income_taxes",
        "investment_fair_value_change_adjustment", "debt_extinguishment_adjustment", "stock_based_compensation",
        "other_noncash_adjustments", "change_trade_and_other_receivables", "change_inventories_stockpiles_ore_on_leach_pads",
        "change_other_assets", "change_accounts_payable", "reclamation_remediation_payments", "change_accrued_tax_liabilities",
        "change_other_accrued_liabilities", "cash_from_discontinued_operations",
    ),
    "cash_from_investing": (
        "capital_expenditures", "proceeds_sales_of_investments", "return_of_investment_equity_method",
        "proceeds_sales_of_mining_operations_and_other_assets", "contributions_to_equity_method_investees",
        "purchases_of_investments", "maturities_of_investments", "acquisitions_net", "other_investing", "cash_from_discontinued_investing",
    ),
    "cash_from_financing": (
        "repurchases_of_common_stock", "dividends_paid_to_common_stockholders", "distributions_to_noncontrolling_interests",
        "funding_from_noncontrolling_interests", "payments_on_lease_financing_obligations", "debt_repayments",
        "employee_tax_withholding_cash", "proceeds_from_debt_issuance", "other_financing",
    ),
}


@dataclass(frozen=True)
class Inventory:
    materials_and_supplies: float
    in_process: float
    concentrate: float
    precious_metals: float
    current_stockpiles_and_ore_on_leach_pads: float
    noncurrent_stockpiles_and_ore_on_leach_pads: float

    @property
    def manufactured_and_supplies(self) -> float:
        return fsum((self.materials_and_supplies, self.in_process, self.concentrate, self.precious_metals))

    @property
    def total(self) -> float:
        return fsum((self.manufactured_and_supplies, self.current_stockpiles_and_ore_on_leach_pads,
                     self.noncurrent_stockpiles_and_ore_on_leach_pads))


@dataclass(frozen=True)
class WorkingCapital:
    trade_receivables: float
    accounts_payable: float
    accrued_operating_costs: float
    accrued_royalties: float
    payables_to_ngm: float
    inventory: Inventory

    @property
    def operating_accruals(self) -> float:
        return fsum((self.accrued_operating_costs, self.accrued_royalties, self.payables_to_ngm))

    @property
    def net(self) -> float:
        return self.trade_receivables + self.inventory.total - self.accounts_payable - self.operating_accruals


@dataclass(frozen=True)
class ForecastFinancing:
    interest_expense: float
    interest_income: float
    note_principal: float
    lease_principal: float
    closing_notes: float
    closing_lease_current: float
    closing_lease_noncurrent: float
    buybacks: float
    common_dividends: float
    average_basic_shares_millions: float
    diluted_shares_millions: float
    closing_shares_millions: float
    ngm_cash_contribution: float


@dataclass(frozen=True)
class ForecastPeriod:
    period_start: date
    period_end: date
    income_statement: dict
    balance_sheet: dict
    cash_flow: dict
    inventory_and_working_capital: dict
    financing: ForecastFinancing


def _check_balance(row, label, checks):
    for subtotal, fields in (
        ("current_assets", CURRENT_ASSETS), ("current_liabilities", CURRENT_LIABILITIES), ("parent_equity", PARENT_EQUITY),
        ("total_assets", ("current_assets", *NONCURRENT_ASSETS, "provisional_ngm_project_asset")),
        ("total_liabilities", ("current_liabilities", *NONCURRENT_LIABILITIES)),
        ("total_equity", ("parent_equity", "noncontrolling_interests")),
        ("total_liabilities_and_equity", ("total_liabilities", "total_equity")),
    ):
        values = (row.get(field, 0) if field == "provisional_ngm_project_asset" else row[field] for field in fields)
        _reconcile(checks, f"{label} {subtotal}", fsum(values), row[subtotal])
    _reconcile(checks, f"{label} balance sheet equation", row["total_assets"], row["total_liabilities_and_equity"])


def _balance_totals(row):
    row["current_assets"] = fsum(row[field] for field in CURRENT_ASSETS)
    row["total_assets"] = fsum((row["current_assets"], *(row[field] for field in NONCURRENT_ASSETS), row["provisional_ngm_project_asset"]))
    row["current_liabilities"] = fsum(row[field] for field in CURRENT_LIABILITIES)
    row["total_liabilities"] = fsum((row["current_liabilities"], *(row[field] for field in NONCURRENT_LIABILITIES)))
    row["parent_equity"] = fsum(row[field] for field in PARENT_EQUITY)
    row["total_equity"] = row["parent_equity"] + row["noncontrolling_interests"]
    row["total_liabilities_and_equity"] = row["total_liabilities"] + row["total_equity"]


def _opening_working_capital(actuals, checks):
    row = actuals["balance_sheets"][SEPTEMBER]
    inventories = actuals["schedules"]["inventories"][SEPTEMBER]
    accruals = actuals["schedules"]["other_liabilities"][SEPTEMBER]
    inventory = Inventory(*(_num(inventories, key, minimum=0) for key in ("materials_and_supplies", "in_process", "concentrate", "precious_metals")),
                          row["current_stockpiles_and_ore_on_leach_pads"], row["noncurrent_stockpiles_and_ore_on_leach_pads"])
    _reconcile(checks, "September inventory categories to filed balance sheet", inventory.manufactured_and_supplies, row["inventories"])
    working = WorkingCapital(row["trade_receivables"], row["accounts_payable"], accruals["accrued_operating_costs"],
                             accruals["accrued_royalties"], accruals["payables_to_ngm"], inventory)
    return working


def _stockpile_rate(actuals):
    opening, latest = (actuals["balance_sheets"][key] for key in (DECEMBER, SEPTEMBER))
    keys = ("current_stockpiles_and_ore_on_leach_pads", "noncurrent_stockpiles_and_ore_on_leach_pads")
    change = fsum(latest[key] - opening[key] for key in keys)
    return change / -actuals["income_statements"]["ytd_2026"]["costs_applicable_to_sales"]


def _working_capital(opening, calibration, quarter_sales, quarter_cas, period, days, stockpile_rate, checks):
    inventory = opening.inventory
    finished_by_metal = {}
    for metal, row in period["metals"].items():
        sold = row["volume"].sold
        if sold <= 0:
            raise ValueError(f"Forecast Newmont {metal} needs sold units to estimate inventory carrying cost.")
        finished_by_metal[metal] = row["economics"].cas_millions * row["production_less_sold"] / sold
    precious_change = finished_by_metal["gold"]
    concentrate_change = fsum(amount for metal, amount in finished_by_metal.items() if metal != "gold")
    # Mine-level ore-placement and extraction schedules are unavailable. Carry the historical book build per dollar of CAS.
    ore_build = stockpile_rate * period["cas_millions"]
    opening_ore = inventory.current_stockpiles_and_ore_on_leach_pads + inventory.noncurrent_stockpiles_and_ore_on_leach_pads
    current_build = ore_build * inventory.current_stockpiles_and_ore_on_leach_pads / opening_ore
    closing_inventory = Inventory(inventory.materials_and_supplies, inventory.in_process,
                                  inventory.concentrate + concentrate_change, inventory.precious_metals + precious_change,
                                  inventory.current_stockpiles_and_ore_on_leach_pads + current_build,
                                  inventory.noncurrent_stockpiles_and_ore_on_leach_pads + ore_build - current_build)
    if min(closing_inventory.concentrate, closing_inventory.precious_metals,
           closing_inventory.current_stockpiles_and_ore_on_leach_pads, closing_inventory.noncurrent_stockpiles_and_ore_on_leach_pads) < 0:
        raise ValueError("Newmont modeled shipments require more inventory than the carrying value supports.")
    cash_sales = period["sales_millions"] - period["noncash_stream_amortization_millions"]
    sales_ratio = (cash_sales / days) / (quarter_sales / 92)
    cost_ratio = (period["cas_millions"] / days) / (quarter_cas / 92)
    closing = WorkingCapital(calibration.trade_receivables * sales_ratio, calibration.accounts_payable * cost_ratio,
                             calibration.accrued_operating_costs * cost_ratio, calibration.accrued_royalties * cost_ratio,
                             calibration.payables_to_ngm * cost_ratio, closing_inventory)
    _reconcile(checks, "Inventory cost bridge", closing_inventory.total - inventory.total, fsum(finished_by_metal.values()) + ore_build)
    return closing, finished_by_metal, ore_build


def _lease_principal(opening, payment, rate):
    # Total payment = principal + interest on the average balance.
    principal = (payment - opening * rate) / (1 - rate / 2)
    if not 0 <= principal <= opening:
        raise ValueError("Newmont's lease payment proxy is inconsistent with the opening obligation.")
    return principal


def _financing(opening, packet, period, start, end, checks):
    q4 = period == Q4
    prefix = "q4" if q4 else period
    notes, leases = packet["notes_debt"], packet["lease_financing"]
    interest, dividends = packet["interest"], packet["dividends"]
    note_principal = notes["scheduled_principal_millions"][str(end.year)]
    if any(notes[f"{prefix}_{key}"] for key in ("new_note_proceeds_millions", "discretionary_note_redemptions_millions")) or leases[f"{prefix}_new_finance_leases_millions"]:
        raise ValueError("New financing requires a separate dated debt, interest and PP&E bridge.")
    opening_leases = opening["current_lease_financing_obligations"] + opening["noncurrent_lease_financing_obligations"]
    lease_rate = leases["discount_rate_proxy"]
    lease_principal = leases["q4_principal_millions"] if q4 else _lease_principal(opening_leases, leases["fy_2027_gross_payment_millions"], lease_rate)
    lease_balance = opening_leases - lease_principal
    if not q4:
        estimated_lease_interest = (opening_leases + lease_balance) / 2 * lease_rate
        _reconcile(checks, "FY2027 finance lease principal and interest to gross payment", lease_principal + estimated_lease_interest,
                   leases["fy_2027_gross_payment_millions"])
    next_payment = leases["fy_2025_finance_lease_gross_payment_schedule_millions"][str(end.year + 1)]
    lease_current = _lease_principal(lease_balance, next_payment, lease_rate)
    days = (end - start).days + 1
    if q4:
        shares = packet["opening"]["latest_shares_outstanding"] / 1e6
        buybacks = packet["repurchases"]["known_q4_cash_millions"]
        if packet["repurchases"]["additional_q4_cash_millions"]:
            raise ValueError("Additional repurchases require an execution-price and share-count bridge.")
        known_days = (date.fromisoformat(packet["repurchases"]["known_q4_through"]) - start).days + 1
        if not 0 <= known_days <= days:
            raise ValueError("The disclosed Newmont buybacks must fall inside Q4.")
        average = (opening["shares_outstanding_millions"] * known_days + shares * (days - known_days)) / days
        common_dividend = dividends["declared_per_share_usd"] * shares
        ngm = packet["ngm_settlement"]["modeled_q4_cash_millions"]
        interest_expense = interest["fy_2026_net_interest_expense_guidance_millions"] - packet["actuals"]["ytd_2026"]["interest_expense_net_millions"]
        _reconcile(checks, "Rounded September shares less disclosed buybacks to latest share proxy",
                   opening["shares_outstanding_millions"] - packet["repurchases"]["known_q4_shares_millions"], shares, tolerance=1.1)
    else:
        if packet["repurchases"]["fy_2027_cash_millions"]:
            raise ValueError("Additional repurchases require an execution-price and share-count bridge.")
        average = shares = opening["shares_outstanding_millions"]
        common_dividend = dividends["fy_2027_quarterly_per_share_usd"] * dividends["fy_2027_payments"] * shares
        buybacks = ngm = 0.0
        interest_expense = interest["fy_2027_net_interest_expense_millions"]
    interest_income = interest["q3_2026_interest_income_millions"] * opening["cash"] / packet["opening"]["cash_millions"] * days / 92
    diluted = average + packet["actuals"]["q3_2026"]["dilutive_awards_millions"]
    _reconcile(checks, f"{period} modeled lease principal and classifications", lease_current + (lease_balance - lease_current),
               opening_leases - lease_principal)
    return ForecastFinancing(interest_expense, interest_income, note_principal, lease_principal,
                             opening["long_term_debt"] - note_principal, lease_current, lease_balance - lease_current,
                             buybacks, common_dividend, average, diluted, shares, ngm)


def _affiliate_forecast(evidence, days):
    history = evidence["schedules"]["affiliate_cash"]["ytd_2026"]
    scale = days / 273
    earnings = (history["pueblo_viejo_equity_income"] + history["lundin_gold_equity_income"]) * scale
    gross_distribution = (history["pueblo_viejo_cash_distributions"] + history["lundin_gold_cash_distributions"]) * scale
    return_of_capital = history["return_of_investment"] * scale
    operating_distribution = gross_distribution - return_of_capital
    cash_contributions = -history["pueblo_viejo_expansion_contributions"] * scale
    if operating_distribution < 0:
        raise ValueError("Newmont affiliate return-of-capital proxy exceeds named cash distributions.")
    return earnings, operating_distribution, return_of_capital, cash_contributions


def _operating_expenses(actuals, operating, cost, period, days):
    guidance = operating["cost_guidance"]["full_year"]
    history = actuals["income_statements"]["ytd_2026"]
    exploration_mix = history["exploration"] / (history["exploration"] + history["advanced_projects"])
    exploration_total = guidance["advanced_projects_research_and_exploration_millions"]
    ga = guidance["general_and_administrative_millions"]
    if period == Q4:
        exploration_total += history["exploration"] + history["advanced_projects"]
        ga += history["general_administrative"]
    recurring_other_quarter = actuals["schedules"]["other_expense_detail"]["q3_2026"]["other"]
    return {
        "sales": operating["periods"][period]["sales_millions"],
        "costs_applicable_to_sales": -operating["periods"][period]["cas_millions"],
        "depreciation_amortization": -cost.depreciation_millions,
        "reclamation_remediation": -cost.closure_expense_millions,
        "exploration": -exploration_total * exploration_mix,
        "advanced_projects": -exploration_total * (1 - exploration_mix),
        "general_administrative": -ga,
        "other_expense_net": -recurring_other_quarter * days / 92,
    }


def _income_statement(actuals, operating, cost, financing, period, days, affiliate_earnings):
    ops = _operating_expenses(actuals, operating, cost, period, days)
    operating_income = fsum(ops.values())
    tax_rate = operating["cost_guidance"]["full_year"]["adjusted_income_and_mining_tax_rate"]
    pretax = operating_income - financing.interest_expense + financing.interest_income
    tax = max(0, pretax) * tax_rate
    earnings = pretax - tax + affiliate_earnings
    merian = operating["periods"][period]["mines"]["merian"]["economics"]["gold"]
    merian_da = cost.depreciation_by_site_and_metal["merian"]["gold"]
    nci_share = 1 - operating["mine_register"]["merian"]["ownership"]
    nci_income = (merian.sales_less_cas_millions - merian_da) * (1 - tax_rate) * nci_share
    parent_earnings = earnings - nci_income
    return {
        **ops, "impairment_charges": 0.0, "gain_loss_on_assets_held_for_sale": 0.0,
        "total_costs_expenses": operating_income - ops["sales"], "operating_income": operating_income,
        "interest_expense_net": -financing.interest_expense, "other_income_net": financing.interest_income,
        "investment_fair_value_change": 0.0, "total_other_income_expense": financing.interest_income - financing.interest_expense,
        "pretax_income": pretax, "income_mining_taxes": -tax, "effective_tax_rate": tax_rate,
        "equity_income_affiliates": affiliate_earnings, "net_income": earnings, "continuing_net_income": earnings,
        "discontinued_net_income": 0.0, "noncontrolling_net_income": -nci_income,
        "net_income_attributable_to_newmont": parent_earnings,
        "basic_weighted_average_shares": financing.average_basic_shares_millions,
        "diluted_weighted_average_shares": financing.diluted_shares_millions,
        "basic_eps": parent_earnings / financing.average_basic_shares_millions, "diluted_eps": parent_earnings / financing.diluted_shares_millions,
    }


def _forecast_period(actuals, operating, capital, funding, opening, working_open, calibration, period, start, end, checks):
    days = (end - start).days + 1
    operations = operating["periods"][period]
    q3_silver = operating["historical_realized_sales"]["q3_2026"]["silver"]
    q3 = actuals["income_statements"]["q3_2026"]
    working, finished, ore_build = _working_capital(
        working_open, calibration, q3["sales"] - q3_silver["streaming_millions"], -q3["costs_applicable_to_sales"],
        operations, days, _stockpile_rate(actuals), checks,
    )
    finance = _financing(opening, funding, period, start, end, checks)
    affiliate_earnings, affiliate_cfo, affiliate_cfi, affiliate_funding = _affiliate_forecast(actuals, days)
    earnings = _income_statement(actuals, operating, capital, finance, period, days, affiliate_earnings)
    history = actuals["cash_flows"]["ytd_2026"]
    equity_history = actuals["schedules"]["equity_changes"]["ytd_2026"]
    sbc = equity_history["stock_awards_equity_total"] * days / 273
    nci_distributions = -history["distributions_to_noncontrolling_interests"] * days / 273
    nci_funding = history["funding_from_noncontrolling_interests"] * days / 273
    noncash_stream = operations["noncash_stream_amortization_millions"]
    working_release = working_open.net - working.net
    operating_flows = {
        "net_income": earnings["net_income"], "depreciation_amortization": capital.depreciation_millions,
        "noncash_closure_accretion": capital.closure_expense_millions, "cash_closure_payments": -capital.closure_cash_millions,
        "noncash_silver_stream_amortization": -noncash_stream, "stock_awards_proxy": sbc,
        "equity_income_affiliates": -affiliate_earnings, "affiliate_operating_distributions_proxy": affiliate_cfo,
        "working_capital_cash_release": working_release,
    }
    investing_flows = {
        "capital_expenditures": -capital.cash_capex_millions, "ngm_cash_contribution": -finance.ngm_cash_contribution,
        "affiliate_return_of_capital_proxy": affiliate_cfi, "affiliate_expansion_contributions": -affiliate_funding,
    }
    financing_flows = {
        "note_principal": -finance.note_principal, "lease_principal": -finance.lease_principal,
        "share_repurchases": -finance.buybacks, "common_dividends_paid": -finance.common_dividends,
        "distributions_to_noncontrolling_interests": -nci_distributions, "funding_from_noncontrolling_interests": nci_funding,
    }
    cfo, cfi, cff = (fsum(flows.values()) for flows in (operating_flows, investing_flows, financing_flows))
    cash = fsum((opening["cash"], cfo, cfi, cff))
    if cash < 0:
        raise ValueError(f"{end} needs additional liquidity to close the forecast.")
    nopat = earnings["operating_income"] - max(0, earnings["operating_income"]) * earnings["effective_tax_rate"]
    fcff = fsum((nopat, capital.depreciation_millions, capital.closure_expense_millions, -capital.closure_cash_millions,
                 -noncash_stream, working_release, -capital.unlevered_cash_capex_millions))
    cash_flow = {
        "operating_items": operating_flows, "investing_items": investing_flows, "financing_items": financing_flows,
        "cash_from_operations": cfo, "cash_from_investing": cfi, "cash_from_financing": cff,
        "opening_cash": opening["cash"], "closing_cash": cash, "net_cash_change": fsum((cfo, cfi, cff)),
        "cash_capex": capital.cash_capex_millions, "cash_from_operations_less_capex": cfo - capital.cash_capex_millions,
        "unlevered_cash_capex": capital.unlevered_cash_capex_millions, "nopat": nopat,
        "core_consolidated_unlevered_free_cash_flow": fcff,
    }
    balance = {**opening}
    balance.pop("source_id", None)
    balance["period_end"] = end
    balance_detail = actuals["schedules"]["other_liabilities"][SEPTEMBER]
    before_closure_current = opening.get("current_reclamation_remediation", balance_detail["current_reclamation_remediation"])
    # Current stream amount is a fixed embedded proxy. Matching amortization releases the aggregate liability.
    closing_stream = opening["noncurrent_silver_stream"] - noncash_stream
    if closing_stream < 0:
        raise ValueError("Newmont silver streaming amortization exceeds the modeled noncurrent balance.")
    balance.update(
        cash=cash, trade_receivables=working.trade_receivables, accounts_payable=working.accounts_payable,
        inventories=working.inventory.manufactured_and_supplies,
        current_stockpiles_and_ore_on_leach_pads=working.inventory.current_stockpiles_and_ore_on_leach_pads,
        noncurrent_stockpiles_and_ore_on_leach_pads=working.inventory.noncurrent_stockpiles_and_ore_on_leach_pads,
        other_current_liabilities=opening["other_current_liabilities"] + working.operating_accruals - working_open.operating_accruals
        + capital.closing_current_closure_liability_millions - before_closure_current,
        current_silver_stream_estimate_embedded_in_other_liabilities=balance_detail["implied_current_silver_stream"],
        current_reclamation_remediation=capital.closing_current_closure_liability_millions,
        noncurrent_reclamation_remediation=capital.closing_noncurrent_closure_liability_millions,
        net_property_plant_mine_development=capital.closing_ppem_millions,
        provisional_ngm_project_asset=opening.get("provisional_ngm_project_asset", 0) + finance.ngm_cash_contribution,
        noncurrent_investments=opening["noncurrent_investments"] + affiliate_earnings - affiliate_cfo - affiliate_cfi + affiliate_funding,
        long_term_debt=finance.closing_notes, current_lease_financing_obligations=finance.closing_lease_current,
        noncurrent_lease_financing_obligations=finance.closing_lease_noncurrent, noncurrent_silver_stream=closing_stream,
        shares_outstanding_millions=finance.closing_shares_millions,
    )
    # Estimate the retirement journal from historical book allocation, with total cash cost fixed.
    retirement = {key: equity_history[f"repurchase_{key}"] for key in ("common_stock", "additional_paid_in_capital", "retained_earnings")}
    retire_total = fsum(retirement.values())
    for key, change in retirement.items():
        balance[key] -= finance.buybacks * change / retire_total
    balance["additional_paid_in_capital"] += sbc
    balance["retained_earnings"] += earnings["net_income_attributable_to_newmont"] - finance.common_dividends
    balance["noncontrolling_interests"] += -earnings["noncontrolling_net_income"] - nci_distributions + nci_funding
    balance["shares_issued_millions"] = finance.closing_shares_millions + opening["treasury_shares_millions"]
    _balance_totals(balance)
    _check_balance(balance, str(end), checks)
    _reconcile(checks, f"{end} ending cash", cash - opening["cash"], cash_flow["net_cash_change"])
    _reconcile(checks, f"{end} consolidated to parent income", earnings["net_income"] + earnings["noncontrolling_net_income"],
               earnings["net_income_attributable_to_newmont"])
    _reconcile(checks, f"{end} parent equity roll", balance["parent_equity"] - opening["parent_equity"],
               earnings["net_income_attributable_to_newmont"] + sbc - finance.buybacks - finance.common_dividends)
    _reconcile(checks, f"{end} affiliate investments roll", balance["noncurrent_investments"] - opening["noncurrent_investments"],
               affiliate_earnings - affiliate_cfo - affiliate_cfi + affiliate_funding)
    _reconcile(checks, f"{end} silver stream release", opening["noncurrent_silver_stream"] - balance["noncurrent_silver_stream"], noncash_stream)
    _reconcile(checks, f"{end} accrual capex to cash capex with no modeled capital payable change", capital.accrual_capex_millions,
               capital.cash_capex_millions + capital.closing_accrued_capex_millions - capital.opening_accrued_capex_millions)
    # Convert booked operating cash to core FCFF by removing noncore cash and cash compensation treatment.
    expected_fcff = cfo - earnings["net_income"] + nopat + affiliate_earnings - affiliate_cfo - sbc - capital.unlevered_cash_capex_millions
    _reconcile(checks, f"{end} unlevered free cash flow to operating cash", fcff, expected_fcff)
    inventory = {
        "opening": working_open, "closing": working,
        "finished_inventory_change_by_metal_millions": finished, "ore_and_leach_pad_build_millions": ore_build,
        "inventory_build_millions": working.inventory.total - working_open.inventory.total,
        "cash_production_cost_proxy_millions": operations["cas_millions"] + working.inventory.total - working_open.inventory.total,
        "working_capital_cash_release_millions": working_release,
    }
    return ForecastPeriod(start, end, earnings, balance, cash_flow, inventory, finance), working


def _cutoff(q4, as_of, funding, checks):
    days = (q4.period_end - q4.period_start).days + 1
    elapsed = (as_of - q4.period_start).days
    if not 0 < elapsed < days:
        raise ValueError("Newmont pre-open cutoff must fall inside the modeled fourth quarter.")
    after = days - elapsed
    fraction = elapsed / days
    dividend_day = date.fromisoformat(funding["dividends"]["pay_on"])
    if not as_of <= dividend_day <= q4.period_end:
        raise ValueError("The modeled December dividend must be after the cutoff.")
    observed_through = date.fromisoformat(funding["repurchases"]["known_q4_through"])
    if observed_through >= as_of:
        raise ValueError("Post-quarter repurchases must have been reported before the cutoff.")
    ngm_paid = _num(funding["ngm_settlement"], "modeled_pre_cutoff_cash_millions", minimum=0)
    if ngm_paid > q4.financing.ngm_cash_contribution:
        raise ValueError("The cutoff cannot include more than the modeled NGM transaction payment.")
    cf = q4.cash_flow
    timed_cash = -(q4.financing.buybacks + q4.financing.common_dividends + q4.financing.ngm_cash_contribution)
    spread = cf["net_cash_change"] - timed_cash
    estimated_cash = cf["opening_cash"] + spread * fraction - q4.financing.buybacks - ngm_paid
    remaining_ngm = q4.financing.ngm_cash_contribution - ngm_paid
    remaining_change = spread * (after / days) - q4.financing.common_dividends - remaining_ngm
    _reconcile(checks, "Pre-open cash plus remaining Q4 cash change", estimated_cash + remaining_change, cf["closing_cash"])
    return {
        "as_of": as_of, "calendar_days_before_cutoff": elapsed, "calendar_days_remaining": after,
        "cash_before_operating_reserve_millions": estimated_cash,
        "cash_if_ngm_already_paid_millions": estimated_cash - remaining_ngm,
        "note_and_finance_lease_debt_millions": q4.financing.closing_notes + q4.financing.note_principal
        + q4.financing.closing_lease_current + q4.financing.closing_lease_noncurrent + q4.financing.lease_principal
        - (q4.financing.note_principal + q4.financing.lease_principal) * fraction,
        "share_count_proxy_millions": q4.financing.closing_shares_millions,
        "ngm_payment_modeled_after_cutoff_millions": remaining_ngm,
        "remaining_cash_change_millions": remaining_change,
        "remaining_core_consolidated_fcff_millions": cf["core_consolidated_unlevered_free_cash_flow"] * after / days,
    }


def _historical(actuals, as_of, operating, capital, financing, checks):
    sources = _sources(actuals, as_of, date_field="date")
    for section in ("income_statements", "balance_sheets", "cash_flows"):
        for period, row in actuals[section].items():
            observed = date.fromisoformat(row.get("period_end", period) if section != "balance_sheets" else period)
            _dated_source(row["source_id"], sources, f"{period} {section}", observed_on=observed)
    for key, period in (("inventories", SEPTEMBER), ("other_liabilities", SEPTEMBER), ("equity_changes", "ytd_2026"),
                        ("other_expense_detail", "q3_2026"), ("affiliate_cash", "ytd_2026")):
        evidence = actuals["schedules"][key][period]
        for source in evidence["source_ids"] if "source_ids" in evidence else (evidence["source_id"],):
            _dated_source(source, sources, f"{key} {period}")
    for period, row in actuals["balance_sheets"].items():
        _check_balance(row, period, checks)
        _reconcile(checks, f"{period} outstanding shares", row["shares_issued_millions"] - row["treasury_shares_millions"],
                   row["shares_outstanding_millions"])
    for period, row in actuals["income_statements"].items():
        _reconcile(checks, f"{period} filed costs and expenses", fsum(row[key] for key in OPERATING_EXPENSES), row["total_costs_expenses"])
        other_income = fsum(row[key] for key in ("investment_fair_value_change", "other_income_net", "interest_expense_net"))
        _reconcile(checks, f"{period} filed other income and expense", other_income, row["total_other_income_expense"])
        _reconcile(checks, f"{period} filed pretax income", row["sales"] + row["total_costs_expenses"] + other_income, row["pretax_income"])
        _reconcile(checks, f"{period} filed net income", row["pretax_income"] + row["income_mining_taxes"] + row["equity_income_affiliates"]
                   + row["discontinued_net_income"], row["net_income"])
        _reconcile(checks, f"{period} filed parent income", row["net_income"] + row["noncontrolling_net_income"], row["net_income_attributable_to_newmont"])
        _reconcile(checks, f"{period} filed diluted EPS", row["net_income_attributable_to_newmont"] / row["diluted_weighted_average_shares"],
                   row["diluted_eps"], tolerance=0.012)
    for period, row in actuals["cash_flows"].items():
        for subtotal, fields in CASH_FLOW_ITEMS.items():
            # Interim disclosures report certain components inside larger filed categories.
            _reconcile(checks, f"{period} filed {subtotal}", fsum(row[key] for key in fields if row[key] is not None), row[subtotal])
        change = fsum((*(row[key] for key in CASH_FLOW_ITEMS), row["effect_of_exchange_rates"], row["held_for_sale_cash_reclassification"]))
        _reconcile(checks, f"{period} filed cash change", change, row["net_change_in_cash"])
        _reconcile(checks, f"{period} filed restricted cash roll", row["opening_cash"] + change, row["closing_cash"])
        _reconcile(checks, f"{period} closing cash split", fsum(row[key] for key in (
            "closing_unrestricted_cash", "closing_current_restricted_cash", "closing_noncurrent_restricted_cash")), row["closing_cash"])
        _reconcile(checks, f"{period} cash flow net income to filed earnings", row["net_income"], actuals["income_statements"][period]["net_income"])

    latest = actuals["balance_sheets"][SEPTEMBER]
    filed = actuals["income_statements"]["ytd_2026"]
    historical_cash = actuals["cash_flows"]["ytd_2026"]
    historic_capital = capital["historical"]
    funding_open = financing["opening"]
    for label, computed, reported in (
        ("Nine-month modeled sales baseline", operating["periods"]["ytd_2026"]["sales_millions"], filed["sales"]),
        ("Nine-month modeled CAS baseline", operating["periods"]["ytd_2026"]["cas_millions"], -filed["costs_applicable_to_sales"]),
        ("Nine-month capex evidence", historic_capital["cash_capex_millions"], -historical_cash["capital_expenditures"]),
        ("September net PP&E", historic_capital["closing_ppem_millions"], latest["net_property_plant_mine_development"]),
        ("September total closure liabilities", historic_capital["closing_closure_liability_millions"],
         latest["noncurrent_reclamation_remediation"] + actuals["schedules"]["other_liabilities"][SEPTEMBER]["current_reclamation_remediation"]),
        ("Nine-month closure payments", historic_capital["closure_cash_millions"], -historical_cash["reclamation_remediation_payments"]),
        ("September unrestricted cash", funding_open["cash_millions"], latest["cash"]),
        ("September restricted cash", funding_open["restricted_cash_millions"], historical_cash["closing_cash"] - latest["cash"]),
        ("September debt carrying value", funding_open["debt_carrying_millions"], latest["long_term_debt"]),
        ("September debt face to carrying", funding_open["debt_face_millions"] - funding_open["unamortized_debt_discount_millions"],
         latest["long_term_debt"]),
        ("September lease financing obligations", funding_open["finance_leases_current_millions"] + funding_open["finance_leases_noncurrent_millions"],
         latest["current_lease_financing_obligations"] + latest["noncurrent_lease_financing_obligations"]),
        ("Nine-month net interest expense", financing["actuals"]["ytd_2026"]["interest_expense_net_millions"], -filed["interest_expense_net"]),
        ("Nine-month lease principal", financing["actuals"]["ytd_2026"]["lease_principal_paid_millions"],
         -historical_cash["payments_on_lease_financing_obligations"]),
        ("Nine-month debt cash paid", financing["actuals"]["ytd_2026"]["note_cash_repaid_millions"], -historical_cash["debt_repayments"]),
        ("Nine-month paid common dividends", financing["actuals"]["ytd_2026"]["dividends_paid_millions"],
         -historical_cash["dividends_paid_to_common_stockholders"]),
        ("Nine-month share repurchases", financing["actuals"]["ytd_2026"]["repurchases_cash_millions"],
         -historical_cash["repurchases_of_common_stock"]),
    ):
        _reconcile(checks, label, computed, reported)


def _annual_income(ytd, q4, checks):
    ytd_days = (date.fromisoformat(ytd["period_end"]) - date.fromisoformat(ytd["period_start"])).days + 1
    q4_days = (q4.period_end - q4.period_start).days + 1
    row = q4.income_statement
    combined = {key: ytd[key] + row.get(key, 0) for key in (
        "sales", *OPERATING_EXPENSES, "total_costs_expenses", "investment_fair_value_change", "other_income_net", "interest_expense_net",
        "total_other_income_expense", "pretax_income", "income_mining_taxes", "equity_income_affiliates", "net_income",
        "continuing_net_income", "discontinued_net_income", "noncontrolling_net_income",
        "net_income_attributable_to_newmont",
    )}
    combined["operating_income"] = combined["sales"] + combined["total_costs_expenses"]
    for basis in ("basic", "diluted"):
        key = f"{basis}_weighted_average_shares"
        combined[key] = (ytd[key] * ytd_days + row[key] * q4_days) / (ytd_days + q4_days)
        combined[f"{basis}_eps"] = combined["net_income_attributable_to_newmont"] / combined[key]
    _reconcile(checks, "FY2026 net income to parent income", combined["net_income"] + combined["noncontrolling_net_income"],
               combined["net_income_attributable_to_newmont"])
    return combined


def _annual_cash(ytd, q4, checks):
    row = q4.cash_flow
    totals = {key: ytd[key] + row[key] for key in CASH_FLOW_ITEMS}
    totals["cash_capex"] = -ytd["capital_expenditures"] + row["cash_capex"]
    totals["cash_from_operations_less_capex"] = totals["cash_from_operations"] - totals["cash_capex"]
    totals["net_cash_change"] = fsum((*(totals[key] for key in CASH_FLOW_ITEMS), ytd["effect_of_exchange_rates"],
                                     ytd["held_for_sale_cash_reclassification"]))
    restricted = ytd["closing_current_restricted_cash"] + ytd["closing_noncurrent_restricted_cash"]
    totals["opening_cash_including_restricted"] = ytd["opening_cash"]
    totals["closing_cash_including_restricted"] = row["closing_cash"] + restricted
    totals["closing_unrestricted_cash"] = row["closing_cash"]
    _reconcile(checks, "FY2026 cash including restricted cash", totals["opening_cash_including_restricted"] + totals["net_cash_change"],
               totals["closing_cash_including_restricted"])
    return totals


def run_nem_statements(as_of: date, inputs_dir: Path, *, guided: Path | None = None) -> dict:
    """Link reported Newmont actuals to its short-term operating and cash forecast."""
    operating = run_current_nem(as_of, inputs_dir, guided=guided)
    actuals = _load(inputs_dir / f"nem-actuals-{as_of.isoformat()}.json")
    if actuals["ticker"] != "NEM" or actuals["currency"] != "USD" or date.fromisoformat(actuals["cutoff"][:10]) != as_of:
        raise ValueError("Newmont actuals use a different evidence cutoff, ticker or currency.")
    funding = _evidence(inputs_dir, "financing", as_of)
    funding_sources = _sources(funding, as_of)
    for key in ("opening", "notes_debt", "lease_financing", "interest", "repurchases", "dividends", "ngm_settlement"):
        _dated_source(funding[key]["source"], funding_sources, key)
        for source in funding[key].get("source_ids", ()):
            _dated_source(source, funding_sources, key)
    reference = funding["reference_close"]
    quote_day = date.fromisoformat(reference["observed_on"])
    if quote_day >= as_of:
        raise ValueError("Use a Newmont reference close from before the pre-open cutoff.")
    _dated_source(reference["source"], funding_sources, "reference close", observed_on=quote_day)
    capital = build_nem_capital_schedule(as_of, inputs_dir, operating)
    checks = list(capital["checks"])
    _historical(actuals, as_of, operating, capital, funding, checks)
    calibration = _opening_working_capital(actuals, checks)
    first = actuals["balance_sheets"][SEPTEMBER]
    q4, q4_working = _forecast_period(actuals, operating, capital["periods"][Q4], funding, first,
                                      calibration, calibration, Q4, date(2026, 10, 1), date(2026, 12, 31), checks)
    fy27, _ = _forecast_period(actuals, operating, capital["periods"][FY27], funding, q4.balance_sheet, q4_working,
                               calibration, FY27, date(2027, 1, 1), date(2027, 12, 31), checks)
    annual_income = _annual_income(actuals["income_statements"]["ytd_2026"], q4, checks)
    annual_cash = _annual_cash(actuals["cash_flows"]["ytd_2026"], q4, checks)
    _reconcile(checks, "FY2026 sales to mine operating forecast", annual_income["sales"], operating["periods"]["fy_2026"]["sales_millions"])
    _reconcile(checks, "FY2026 CAS to mine operating forecast", -annual_income["costs_applicable_to_sales"], operating["periods"]["fy_2026"]["cas_millions"])
    cutoff = _cutoff(q4, as_of, funding, checks)
    return {
        "as_of": as_of, "mode": operating["mode"], "units": "USD millions except per-share values, ratios and specified volumes",
        "operating_forecast": operating, "historical_statements": actuals, "capital_schedule": capital,
        "financing_evidence": funding, "cutoff": cutoff,
        "periods": {Q4: q4, "fy_2026": {"income_statement": annual_income, "cash_flow": annual_cash, "balance_sheet": q4.balance_sheet}, FY27: fy27},
        "checks": checks, "source_register": {**operating["source_register"], "historical_statements": actuals["sources"],
                                            "capital": capital["sources"], "financing": funding["sources"]},
        "limitations": (
            "Finished inventory uses production less sales for each metal valued at that metal's forecast CAS per unit sold. The ore and leach-pad build uses the historical book change per dollar of CAS. Materials and in-process stay flat. Mine-level assays, payability, inventory recovery and stockpile impairments are unavailable.",
            "Cash receivables scale with daily revenue excluding silver stream amortization. Trade payables and selected operating accruals scale with daily CAS. Tax, capex and other accrual balances carry forward. Income and mining taxes are modeled as cash expense at the 33% adjusted rate. Previously accrued tax settlement timing is unavailable.",
            "Affiliate income and cash estimates use Pueblo Viejo and Lundin Gold's nine-month earnings and named distributions. Total named cash is split using the aggregate reported investing return as a proxy. Exact operating versus investing classification is unavailable. The core FCFF excludes affiliate cash and earnings.",
            "Noncontrolling income uses the 25% minority share of modeled Merian earnings after a consolidated tax proxy. NCI distributions and funding use the filed nine-month run rates. Merian's future local tax and distribution schedules are unavailable.",
            "The cash flow uses the nine-month stock awards equity credit as a noncash add-back proxy. It remains an economic cost in FCFF. Other financing, hedge, purchase-accounting and deferred-tax changes carry no forecast cash impact. Historical balances remain in the balance sheet.",
            "The implied current silver streaming liability remains embedded in other current liabilities. The forecast releases the combined obligation through the noncurrent balance as amortization revenue is recognized.",
            "Financing uses the last declared dividend, scheduled note maturities and a prior-year contractual lease payment proxy. The model carries no discretionary share repurchases after the disclosed October activity or new debt issuance.",
            "The model includes the $1.95 billion Nevada Gold Mines cash contribution once in Q4 as separate investing cash and provisional project basis. Closing date, gain, asset exchanges, tax and liabilities are unconfirmed. The cutoff cash estimate shows the timing effect separately.",
            "The Q4 cutoff prorates recurring cash and FCFF through November 11, includes the disclosed $451 million repurchases, and assigns the declared common dividend and modeled NGM contribution to the remainder. Restricted cash stays outside cutoff liquidity.",
            "Core consolidated FCFF includes Merian at 100%, uses recurring project capex excluding the capitalized interest proxy, and includes modeled closure cash. The later equity bridge must address noncontrolling interests, affiliate interests and transaction treatment. Mine-life and post-2027 closure schedules remain open.",
            *capital["limitations"],
        ),
    }

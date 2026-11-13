"""Build the linked Newmont three statements and supporting Excel schedules."""

from datetime import date

from openpyxl.utils.datetime import to_excel

from .fdx_statements import PCT, USD
from .nem_current import METALS
from .nem_forward_statements import CURRENT_ASSETS, NONCURRENT_ASSETS, CURRENT_LIABILITIES, NONCURRENT_LIABILITIES, PARENT_EQUITY
from .nem_fresh_excel_operations import labels, periods, sheet_title
from .nem_nav import _sustaining_allocations


INCOME = {
    6: "sales", 7: "costs_applicable_to_sales", 8: "depreciation_amortization", 9: "reclamation_remediation",
    11: "exploration", 12: "advanced_projects", 13: "general_administrative", 14: "other_expense_net",
    15: "impairment_charges", 16: "gain_loss_on_assets_held_for_sale", 17: "total_costs_expenses", 18: "operating_income",
    20: "interest_expense_net", 21: "other_income_net", 22: "investment_fair_value_change", 23: "total_other_income_expense",
    24: "pretax_income", 25: "income_mining_taxes", 26: "equity_income_affiliates", 27: "continuing_net_income",
    28: "discontinued_net_income", 29: "net_income", 30: "noncontrolling_net_income", 31: "net_income_attributable_to_newmont",
    33: "basic_weighted_average_shares", 34: "diluted_weighted_average_shares", 35: "basic_eps", 36: "diluted_eps",
}
BALANCE_KEYS = (*CURRENT_ASSETS, "current_assets", *NONCURRENT_ASSETS, "provisional_ngm_project_asset", "total_assets",
                *CURRENT_LIABILITIES, "current_debt", "current_liabilities", *NONCURRENT_LIABILITIES, "total_liabilities",
                *PARENT_EQUITY, "parent_equity", "noncontrolling_interests", "total_equity", "total_liabilities_and_equity")
BALANCE = {key: 6 + index for index, key in enumerate(BALANCE_KEYS)}
CASH_HISTORY = {6: "net_income", 7: "depreciation_amortization", 10: "reclamation_remediation_payments",
                13: "cash_from_operations", 15: "capital_expenditures", 17: "return_of_investment_equity_method",
                18: "contributions_to_equity_method_investees", 19: "cash_from_investing", 20: "repurchases_of_common_stock",
                21: "dividends_paid_to_common_stockholders", 23: "cash_from_financing", 27: "closing_unrestricted_cash",
                28: "net_change_in_cash", 42: "effect_of_exchange_rates", 43: "held_for_sale_cash_reclassification",
                44: "opening_cash", 45: "closing_cash", 46: "closing_current_restricted_cash", 47: "closing_noncurrent_restricted_cash"}
DATE_FORMAT = "mmm d, yyyy"


def _label(key):
    return key.replace("_", " ").replace("ppe", "PP&E").replace("eps", "EPS").capitalize()


def _value(plan, sheet, address):
    if address in plan.expected.get(sheet, {}):
        return plan.expected[sheet][address]
    return plan.cells[sheet][address][0]


def _history(plan, actuals):
    sheet = "Actuals"
    sheet_title(plan, sheet, "Filed financial statements", "USD millions except per-share data. Cash flow includes restricted cash where marked.")
    plan.set(sheet, "B4", "FY2025A", "header")
    plan.set(sheet, "D4", "9M2026A / September", "header")
    for data_key, roster, start, tab, keys in (
        ("income_statements", INCOME, 0, "Income statement", ("fy_2025", "ytd_2026")),
        ("balance_sheets", {row: key for key, row in BALANCE.items()}, 50, "Balance sheet", ("2025-12-31", "2026-09-30")),
        ("cash_flows", CASH_HISTORY, 105, "Cash flow", ("fy_2025", "ytd_2026")),
    ):
        plan.set(sheet, f"A{start+5}", tab, "section")
        for row, field in roster.items():
            plan.set(sheet, f"A{start+row}", _label(field))
            for col, period in zip("BD", keys, strict=True):
                amount = actuals[data_key][period].get(field)
                if amount is None:
                    continue
                fmt = USD if field.endswith("_eps") else None
                plan.set(sheet, f"{col}{start+row}", amount, "actual", fmt)
                plan.calc(tab, f"{col}{row}", f"Actuals!{col}{start+row}", amount, fmt=fmt)
    for row, label, amount in ((155, "Q3 sales including stream amortization", actuals["income_statements"]["q3_2026"]["sales"]),
                               (156, "Q3 costs applicable to sales", -actuals["income_statements"]["q3_2026"]["costs_applicable_to_sales"]),
                               (157, "Q3 other operating expenses", actuals["schedules"]["other_expense_detail"]["q3_2026"]["other"])):
        plan.set(sheet, f"A{row}", label)
        plan.set(sheet, f"D{row}", amount, "actual")
    for col, period in (("B", "fy_2025"), ("D", "ytd_2026")):
        historical = actuals["income_statements"][period]
        plan.calc("Income statement", f"{col}18", f"{col}6+{col}17", historical["sales"]+historical["total_costs_expenses"])


def _capital(plan, statements, raw, costs, col, prev, period):
    sheet = "PP&E"
    c = statements["capital_schedule"]["periods"][period]
    h = statements["capital_schedule"]["historical"]
    guidance = costs["guidance_2026"]["full_year"]
    quarter = statements["operating_forecast"]["cost_guidance"]["fourth_quarter"]
    later = costs["indications_2027"]
    q4 = col == "E"
    if q4:
        for address, amount in {"I6": guidance["attributable_sustaining_capital_millions"], "I7": guidance["attributable_development_capital_millions"],
                                "I8": later["indicative_development_capital_increase_millions"], "I10": guidance["capitalized_interest_millions"],
                                "I13": guidance["reclamation_cash_millions"], "I14": guidance["reclamation_and_remediation_accretion_millions"],
                                "I15": later["indicative_annual_reclamation_cash_reduction_millions"],
                                "I16": raw["assumptions"]["fy_2027_accretion_growth"],
                                "I17": raw["assumptions"]["fy_2027_capitalized_interest_growth"],
                                "I20": quarter["attributable_sustaining_capital_millions"],
                                "I21": quarter["attributable_development_capital_millions"]}.items():
            plan.set(sheet, address, amount, "input")
        for row, field in {7: "attributable_sustaining_capex_millions", 8: "attributable_development_capex_millions", 9: "cash_capex_millions",
                           11: "closing_ppem_millions", 13: "closing_closure_liability_millions", 14: "closure_cash_millions",
                           15: "closure_accretion_millions", 17: "closing_current_closure_liability_millions"}.items():
            plan.set(sheet, f"D{row}", h[field], "actual")
        plan.calc(sheet, "I9", "D9/SUM(D7:D8)", h["consolidated_cash_to_attributable_capex_ratio"], fmt=PCT)
    fields = {
        6: (f"{prev}11", c.opening_ppem_millions),
        7: (("I20" if q4 else "I6") + "*$I$9", c.sustaining_cash_capex_millions),
        8: (("I21" if q4 else "(I7+I8)") + "*$I$9", c.development_cash_capex_millions),
        9: (f"SUM({col}7:{col}8)", c.cash_capex_millions),
        10: (f"Operations!{col}11", c.depreciation_millions),
        11: (f"{col}6+{col}9-{col}10", c.closing_ppem_millions),
        12: (f"{col}9-{col}19", c.unlevered_cash_capex_millions),
        13: (f"{prev}{13 if q4 else 16}", c.opening_closure_liability_millions),
        14: ("I13-D14" if q4 else "I13-I15", c.closure_cash_millions),
        15: ("I14-D15" if q4 else "I14*(1+I16)", c.closure_expense_millions),
        16: (f"{col}13-{col}14+{col}15", c.closing_closure_liability_millions),
        17: ("I13-I15" if q4 else "I13-2*I15", c.closing_current_closure_liability_millions),
        18: (f"{col}16-{col}17", c.closing_noncurrent_closure_liability_millions),
        19: ("I10*92/365" if q4 else "I10*(1+I17)", c.capitalized_interest_millions),
    }
    for row, (expr, amount) in fields.items():
        plan.calc(sheet, f"{col}{row}", expr, amount, kind="total" if row in (9, 11, 12, 16) else "")


def _debt(plan, statements, col, prev, period):
    sheet = "Debt schedule"
    p = statements["periods"][period]
    f = p.financing
    raw = statements["financing_evidence"]
    lease = raw["lease_financing"]
    hist = statements["historical_statements"]["balance_sheets"]["2026-09-30"]
    q4 = col == "E"
    if q4:
        for row, amount in {8: hist["long_term_debt"], 12: hist["current_lease_financing_obligations"],
                            13: hist["noncurrent_lease_financing_obligations"], 20: hist["shares_outstanding_millions"]}.items():
            plan.set(sheet, f"D{row}", amount, "actual")
        for row, amount in {7: raw["notes_debt"]["scheduled_principal_millions"]["2026"], 8: raw["notes_debt"]["scheduled_principal_millions"]["2027"],
                            10: lease["q4_principal_millions"], 11: lease["fy_2027_gross_payment_millions"], 12: lease["discount_rate_proxy"],
                            13: lease["fy_2025_finance_lease_gross_payment_schedule_millions"]["2028"],
                            15: raw["interest"]["fy_2026_net_interest_expense_guidance_millions"],
                            16: raw["actuals"]["ytd_2026"]["interest_expense_net_millions"], 17: raw["interest"]["fy_2027_net_interest_expense_millions"],
                            18: raw["interest"]["q3_2026_interest_income_millions"], 20: raw["opening"]["latest_shares_outstanding"]/1e6,
                            21: raw["actuals"]["q3_2026"]["dilutive_awards_millions"], 22: raw["repurchases"]["known_q4_cash_millions"],
                            23: raw["dividends"]["declared_per_share_usd"], 24: raw["dividends"]["fy_2027_quarterly_per_share_usd"],
                            25: raw["dividends"]["fy_2027_payments"], 26: raw["ngm_settlement"]["modeled_q4_cash_millions"]}.items():
            plan.set(sheet, f"I{row}", amount, "input")
        plan.set(sheet, "I27", to_excel(date.fromisoformat(raw["repurchases"]["known_q4_through"])), "actual", DATE_FORMAT)
    fields = {
        6: (f"{prev}8", f.closing_notes + f.note_principal), 7: ("I7" if q4 else "I8", f.note_principal),
        8: (f"{col}6-{col}7", f.closing_notes), 9: (f"SUM({prev}12:{prev}13)", f.closing_lease_current + f.closing_lease_noncurrent + f.lease_principal),
        10: ("I10" if q4 else f"(I11-{col}9*I12)/(1-I12/2)", f.lease_principal),
        11: (f"{col}9-{col}10", f.closing_lease_current + f.closing_lease_noncurrent),
        12: (f"({'I11' if q4 else 'I13'}-{col}11*I12)/(1-I12/2)", f.closing_lease_current),
        13: (f"{col}11-{col}12", f.closing_lease_noncurrent),
        14: (f"{col}8+{col}11", f.closing_notes + f.closing_lease_current + f.closing_lease_noncurrent),
        15: ("I15-I16" if q4 else "I17", f.interest_expense),
        16: ("I18" if q4 else f"I18*('Cash flow'!E27-'Cash flow'!E37)/'Balance sheet'!D6*365/92", f.interest_income),
        18: ("I22" if q4 else "0", f.buybacks), 19: (f"I23*{col}20" if q4 else f"I24*I25*{col}20", f.common_dividends),
        20: ("I20" if q4 else "E20", f.closing_shares_millions), 21: (f"{col}22+I21", f.diluted_shares_millions),
        22: ("(D20*(I27-DATE(2026,10,1)+1)+E20*(DATE(2026,12,31)-I27))/92" if q4 else "G20", f.average_basic_shares_millions),
        23: ("I26" if q4 else "0", f.ngm_cash_contribution),
    }
    for row, (expr, amount) in fields.items():
        plan.calc(sheet, f"{col}{row}", expr, amount)


def _working_capital(plan, statements, operations_rows, col, prev, period):
    sheet = "Working capital"
    hist = statements["historical_statements"]
    current = statements["operating_forecast"]
    wc = statements["periods"][period].inventory_and_working_capital
    start, end = wc["opening"], wc["closing"]
    fields = {6: "trade_receivables", 7: "accounts_payable", 8: "accrued_operating_costs", 9: "accrued_royalties", 10: "payables_to_ngm"}
    inventory = {17: "materials_and_supplies", 18: "in_process", 19: "concentrate", 20: "precious_metals",
                 21: "current_stockpiles_and_ore_on_leach_pads", 22: "noncurrent_stockpiles_and_ore_on_leach_pads"}
    days = 92 if col == "E" else 365
    if col == "E":
        for row, field in fields.items():
            plan.set(sheet, f"D{row}", getattr(start, field), "actual")
        for row, field in inventory.items():
            plan.set(sheet, f"D{row}", getattr(start.inventory, field), "actual")
        plan.calc(sheet, "D12", "SUM(D8:D10)", start.operating_accruals)
        plan.calc(sheet, "D13", "SUM(D17:D22)", start.inventory.total)
        plan.calc(sheet, "D14", "D6-D7-D12+D13", start.net)
        for row, label, value in (
            (6, "Q3 silver stream amortization", current["historical_realized_sales"]["q3_2026"]["silver"]["streaming_millions"]),
            (7, "FY2025 stockpiles and leach pads", sum(hist["balance_sheets"]["2025-12-31"][key] for key in inventory.values() if "stockpiles" in key)),
        ):
            plan.set(sheet, f"I{row}", label, "note")
            plan.set(sheet, f"J{row}", value, "actual")
        rate = (start.inventory.current_stockpiles_and_ore_on_leach_pads + start.inventory.noncurrent_stockpiles_and_ore_on_leach_pads
                - plan.cells[sheet]["J7"][0]) / -hist["income_statements"]["ytd_2026"]["costs_applicable_to_sales"]
        plan.calc(sheet, "J8", "(SUM(D21:D22)-J7)/(-Actuals!D7)", rate, fmt=PCT)
        plan.set(sheet, "I8", "Historical stockpile build / CAS", "note")
    op = current["periods"][period]
    quarter_sales = hist["income_statements"]["q3_2026"]["sales"] - current["historical_realized_sales"]["q3_2026"]["silver"]["streaming_millions"]
    base_sales, base_cost = op["sales_millions"], op["cas_millions"]
    sales_factor = (base_sales-op["noncash_stream_amortization_millions"])/days/(quarter_sales/92)
    cas_factor = base_cost/days/(-hist["income_statements"]["q3_2026"]["costs_applicable_to_sales"]/92)
    plan.calc(sheet, f"{col}24", f"(Operations!{col}13-Operations!{col}12)*92/{days}/(Actuals!D155-$J$6)", sales_factor, fmt=PCT)
    plan.calc(sheet, f"{col}25", f"Operations!{col}14*92/{days}/Actuals!D156", cas_factor, fmt=PCT)
    for row, field in fields.items():
        plan.calc(sheet, f"{col}{row}", f"D{row}*{col}{24 if row==6 else 25}", getattr(end, field))
    for index, metal in enumerate(METALS):
        matched = [row for (site, key), row in operations_rows.items()
                   if key == metal and key in op["mines"][site]["economics"]]
        quantities = [f"Operations!{col}{row}" for row in matched]
        sales = [f"Operations!{col}{row+1}" for row in matched]
        costs = [f"Operations!{col}{row+4}" for row in matched]
        expr = f"SUM({','.join(costs)})*(SUM({','.join(quantities)})/SUM({','.join(sales)})-1)"
        plan.calc(sheet, f"{col}{28+index}", expr, wc["finished_inventory_change_by_metal_millions"][metal])
        plan.set(sheet, f"A{28+index}", f"{metal.title()} finished inventory build")
    plan.calc(sheet, f"{col}33", f"Operations!{col}14*$J$8", wc["ore_and_leach_pad_build_millions"])
    for row, field in inventory.items():
        expr = {19: f"+SUM({col}29:{col}32)", 20: f"+{col}28",
                21: f"+{col}33*{prev}21/SUM({prev}21:{prev}22)",
                22: f"+{col}33*{prev}22/SUM({prev}21:{prev}22)"}.get(row, "")
        plan.calc(sheet, f"{col}{row}", f"{prev}{row}{expr}", getattr(end.inventory, field))
    for row, expr, amount in (
        (12, f"SUM({col}8:{col}10)", end.operating_accruals), (13, f"SUM({col}17:{col}22)", end.inventory.total),
        (14, f"{col}6-{col}7-{col}12+{col}13", end.net), (15, f"{col}14-{col}13", end.net-end.inventory.total),
        (16, f"{prev}14-{col}14", wc["working_capital_cash_release_millions"]),
        (34, f"{col}13-{prev}13", wc["inventory_build_millions"]),
    ):
        plan.calc(sheet, f"{col}{row}", expr, amount, kind="total" if row in (14, 16) else "")


def _income_and_cash(plan, statements, nav, rows, col, prev, period):
    p = statements["periods"][period]
    income, cash, finance = p.income_statement, p.cash_flow, p.financing
    days = 92 if col == "E" else 365
    op = statements["operating_forecast"]["periods"][period]
    tax = income["effective_tax_rate"]
    mo = rows["merian", "gold"]
    ownership = statements["operating_forecast"]["mine_register"]["merian"]["ownership"]
    cap = statements["capital_schedule"]["periods"][period]
    sales_delta = _value(plan, "Operations", f"{col}9") - income["sales"]
    cas_delta = _value(plan, "Operations", f"{col}10") + income["costs_applicable_to_sales"]
    delta = (sales_delta-cas_delta)*(1-tax)
    merian = op["mines"]["merian"]["economics"]["gold"]
    merian_delta = (_value(plan,"Operations",f"{col}{mo+5}")-_value(plan,"Operations",f"{col}{mo+6}")-merian.sales_less_cas_millions)*(1-tax)*(1-ownership)
    sust = _sustaining_allocations(nav, statements, [site for site, mine in op["mines"].items() if mine["economics"]], [])
    merian_capex_ratio = sust["merian"]/cap.sustaining_cash_capex_millions if col == "G" else sust["merian"]/statements["capital_schedule"]["periods"]["fy_2027"].sustaining_cash_capex_millions
    merian_da = cap.depreciation_by_site_and_metal["merian"]["gold"]
    merian_cash = ((merian.sales_less_cas_millions-merian_da)*(1-tax)+merian_da-cap.sustaining_cash_capex_millions*merian_capex_ratio)*(1-ownership)
    plan.set("Cash flow", "I38", merian_capex_ratio, "input", PCT)
    earnings = income["net_income"]+delta
    adjustments = {6: sales_delta, 7: -cas_delta, 17: -cas_delta, 18: sales_delta-cas_delta, 24: sales_delta-cas_delta,
                   25: -(sales_delta-cas_delta)*tax, 27: delta, 29: delta, 30: -merian_delta, 31: delta-merian_delta,
                   35: (delta-merian_delta)/finance.average_basic_shares_millions, 36: (delta-merian_delta)/finance.diluted_shares_millions}
    c=col
    actuals = statements["historical_statements"]
    guide = statements["operating_forecast"]["cost_guidance"]["full_year"]
    if col == "E":
        for row, value in ((11, guide["advanced_projects_research_and_exploration_millions"]), (13, guide["general_and_administrative_millions"])):
            plan.set("Income statement", f"I{row}", value, "input")
        plan.calc("Income statement", "I12", "Actuals!D11/SUM(Actuals!D11:D12)",
                  actuals["income_statements"]["ytd_2026"]["exploration"]/(actuals["income_statements"]["ytd_2026"]["exploration"]+actuals["income_statements"]["ytd_2026"]["advanced_projects"]), fmt=PCT)
        history = actuals["schedules"]["affiliate_cash"]["ytd_2026"]
        eq = actuals["schedules"]["equity_changes"]["ytd_2026"]
        cf = actuals["cash_flows"]["ytd_2026"]
        values = {6: history["pueblo_viejo_equity_income"]+history["lundin_gold_equity_income"],
                  7: history["pueblo_viejo_cash_distributions"]+history["lundin_gold_cash_distributions"],
                  8: history["return_of_investment"], 9: history["pueblo_viejo_expansion_contributions"],
                  11: eq["stock_awards_equity_total"], 12: cf["distributions_to_noncontrolling_interests"],
                  13: cf["funding_from_noncontrolling_interests"], 14: 1-ownership}
        for row, value in values.items():
            plan.set("Cash flow", f"I{row}", value, "actual")
        plan.set("Cash flow", "I41", statements["financing_evidence"]["ngm_settlement"]["modeled_pre_cutoff_cash_millions"], "input")
    q4 = col == "E"
    total_exploration = "($I$11+SUM(Actuals!D11:D12))" if q4 else "$I$11"
    total_ga = "-($I$13+Actuals!D13)" if q4 else "-$I$13"
    income_formulas = {
        6: f"Operations!{c}9", 7: f"-Operations!{c}10", 8: f"-'PP&E'!{c}10", 9: f"-'PP&E'!{c}15",
        11: f"-{total_exploration}*$I$12", 12: f"-{total_exploration}*(1-$I$12)", 13: total_ga,
        14: f"-Actuals!D157*{days}/92", 15: "0", 16: "0", 17: f"SUM({c}7:{c}16)", 18: f"{c}6+{c}17",
        20: f"-'Debt schedule'!{c}15", 21: f"'Debt schedule'!{c}16", 22: "0", 23: f"SUM({c}20:{c}22)",
        24: f"{c}18+{c}23", 25: f"-MAX(0,{c}24)*'Cash flow'!{c}36", 26: f"'Cash flow'!$I$6*{days}/273",
        27: f"SUM({c}24:{c}26)", 28: "0", 29: f"{c}27+{c}28",
        30: f"-(Operations!{c}{mo+5}-Operations!{c}{mo+6}-Operations!{c}{mo+7})*(1-'Cash flow'!{c}36)*'Cash flow'!$I$14",
        31: f"{c}29+{c}30", 33: f"'Debt schedule'!{c}22", 34: f"'Debt schedule'!{c}21", 35: f"{c}31/{c}33", 36: f"{c}31/{c}34",
    }
    for row, field in INCOME.items():
        plan.calc("Income statement", f"{c}{row}", income_formulas[row], income[field]+adjustments.get(row,0), fmt=USD if row in (35,36) else None)
    cfo, cfi, cff = (cash[key] for key in ("cash_from_operations", "cash_from_investing", "cash_from_financing"))
    op_items, inv_items, fin_items = (cash[key] for key in ("operating_items", "investing_items", "financing_items"))
    previous_delta = 0 if q4 else _value(plan,"Cash flow","E37")
    prices = statements["operating_forecast"]["modeled_prices"][period]
    gold_slope = prices["gold"].gross_usd/prices["gold"].benchmark_usd*(1-plan.cells["Assumptions"]["B25"][0])*(1-tax)/1000
    cash_fields = {
        6: (f"'Income statement'!{c}29", earnings), 7: (f"'PP&E'!{c}10", cap.depreciation_millions),
        8: (f"'PP&E'!{c}15", cap.closure_expense_millions), 9: (f"-Operations!{c}12", op_items["noncash_silver_stream_amortization"]),
        10: (f"-{c}26", -cap.closure_cash_millions), 11: (f"'Working capital'!{c}16", op_items["working_capital_cash_release"]),
        12: (f"$I$7*{days}/273-{c}17-'Income statement'!{c}26", op_items["affiliate_operating_distributions_proxy"]-income["equity_income_affiliates"]),
        13: (f"SUM({c}6:{c}12)+{c}34", cfo+delta), 15: (f"-'PP&E'!{c}9", -cap.cash_capex_millions),
        16: (f"-'Debt schedule'!{c}23", -finance.ngm_cash_contribution),
        17: (f"$I$8*{days}/273", inv_items["affiliate_return_of_capital_proxy"]),
        18: (f"$I$9*{days}/273", inv_items["affiliate_expansion_contributions"]),
        19: (f"SUM({c}15:{c}18)", cfi), 20: (f"-'Debt schedule'!{c}18", -finance.buybacks),
        21: (f"-{c}29", -finance.common_dividends), 22: (f"-{c}31", -finance.note_principal-finance.lease_principal),
        23: (f"SUM({c}20:{c}22)+{c}33", cff), 24: (f"'Balance sheet'!{prev}6", cash["opening_cash"]+previous_delta),
        25: (f"'Income statement'!{c}18-MAX(0,'Income statement'!{c}18)*{c}36+SUM({c}7:{c}11)-'PP&E'!{c}12", cash["core_consolidated_unlevered_free_cash_flow"]+delta),
        26: (f"'PP&E'!{c}14", cap.closure_cash_millions), 27: (f"{c}24+{c}28", cash["closing_cash"]+previous_delta+delta),
        28: (f"SUM({c}13,{c}19,{c}23)", cash["net_cash_change"]+delta),
        29: (f"'Debt schedule'!{c}19", finance.common_dividends), 30: (f"'Debt schedule'!{c}14", finance.closing_notes+finance.closing_lease_current+finance.closing_lease_noncurrent),
        31: (f"SUM('Debt schedule'!{c}7,'Debt schedule'!{c}10)", finance.note_principal+finance.lease_principal),
        32: (f"$I$7*{days}/273+{c}18", op_items["affiliate_operating_distributions_proxy"]+inv_items["affiliate_return_of_capital_proxy"]+inv_items["affiliate_expansion_contributions"]),
        33: (f"SUM($I$12:$I$13)*{days}/273", fin_items["distributions_to_noncontrolling_interests"]+fin_items["funding_from_noncontrolling_interests"]),
        34: (f"$I$11*{days}/273", op_items["stock_awards_proxy"]),
        35: (f"{c}38+((Operations!{c}{mo+5}-Operations!{c}{mo+3})-(Operations!{c}{mo+6}-Operations!{c}{mo+4}))*(1-{c}36)*$I$14", merian_cash+merian_delta),
        36: ("Assumptions!B32", tax),
        37: (f"((Operations!{c}9-Operations!{c}13)-(Operations!{c}10-Operations!{c}14))*(1-{c}36)", delta),
        38: (f"((Operations!{c}{mo+3}-Operations!{c}{mo+4}-Operations!{c}{mo+7})*(1-{c}36)+Operations!{c}{mo+7}-'PP&E'!{c}7*$I$38)*$I$14", merian_cash),
        39: (f"Prices!{c}14/Prices!{c}21*(1-Assumptions!$B$25)*(1-{c}36)*Operations!{c}8/1000", gold_slope*op["metals"]["gold"]["volume"].sold),
        40: (f"Prices!{c}14/Prices!{c}21*(1-Assumptions!$B$25)*(1-{c}36)*Operations!{c}{mo+1}/1000*$I$14", gold_slope*op["mines"]["merian"]["volumes"]["gold"].sold*(1-ownership)),
    }
    if q4:
        cash_fields[41] = ("E24+(E28-E37-E20+E29-E16)*(1-Assumptions!$B$17)+E20-$I$41", statements["cutoff"]["cash_before_operating_reserve_millions"])
    for row, (expr, amount) in cash_fields.items():
        plan.calc("Cash flow", f"{c}{row}", expr, amount, fmt=PCT if row==36 else None)
    return delta, merian_delta


def _balance(plan, statements, col, prev, period):
    sheet = "Balance sheet"
    base = statements["periods"][period].balance_sheet
    c = col
    wc = {"trade_receivables": 6, "accounts_payable": 7, "current_stockpiles_and_ore_on_leach_pads": 21,
          "noncurrent_stockpiles_and_ore_on_leach_pads": 22}
    if c == "E":
        history = statements["historical_statements"]["schedules"]["equity_changes"]["ytd_2026"]
        retirement = {key: history[f"repurchase_{key}"] for key in ("common_stock", "additional_paid_in_capital", "retained_earnings")}
        for row, (key, value) in enumerate(retirement.items(), 7):
            plan.set(sheet, f"I{row}", f"Filed repurchase allocation · {_label(key)}", "note")
            plan.set(sheet, f"J{row}", value/sum(retirement.values()), "actual", PCT)
    delta = _value(plan,"Cash flow",f"{c}37")
    previous_delta = 0 if c == "E" else _value(plan,"Cash flow","E37")
    def cell(key, column=c):
        return f"{column}{BALANCE[key]}"
    def total(keys):
        return "SUM("+",".join(cell(key) for key in keys)+")"
    def update(key, expression, amount=None):
        plan.calc(sheet, cell(key), expression, base[key] if amount is None else amount)
    for key in BALANCE:
        if key in ("current_assets", "total_assets", "current_liabilities", "total_liabilities", "parent_equity", "total_equity", "total_liabilities_and_equity"):
            continue
        if key in wc:
            update(key, f"'Working capital'!{c}{wc[key]}")
            continue
        if key == "inventories":
            update(key, f"SUM('Working capital'!{c}17:{c}20)")
            continue
        if key == "cash":
            update(key, f"'Cash flow'!{c}27", base[key]+delta+previous_delta)
            continue
        if key in ("net_property_plant_mine_development", "noncurrent_reclamation_remediation", "long_term_debt",
                   "current_lease_financing_obligations", "noncurrent_lease_financing_obligations"):
            tab, row = {"net_property_plant_mine_development": ("PP&E", 11), "noncurrent_reclamation_remediation": ("PP&E", 18),
                        "long_term_debt": ("Debt schedule", 8), "current_lease_financing_obligations": ("Debt schedule", 12),
                        "noncurrent_lease_financing_obligations": ("Debt schedule", 13)}[key]
            update(key, f"'{tab}'!{c}{row}")
            continue
        old = cell(key, prev)
        if key == "provisional_ngm_project_asset":
            update(key, f"{old}-'Cash flow'!{c}16")
        elif key == "noncurrent_investments":
            update(key, f"{old}+'Income statement'!{c}26-'Cash flow'!{c}32")
        elif key == "other_current_liabilities":
            update(key, f"{old}+'Working capital'!{c}12-'Working capital'!{prev}12+'PP&E'!{c}17-'PP&E'!{prev}17")
        elif key == "noncurrent_silver_stream":
            update(key, f"{old}+'Cash flow'!{c}9")
        elif key in ("common_stock", "additional_paid_in_capital", "retained_earnings"):
            row = {"common_stock":7, "additional_paid_in_capital":8, "retained_earnings":9}[key]
            expr = f"{old}+'Cash flow'!{c}20*$J${row}"
            amount = base[key]
            if key == "additional_paid_in_capital":
                expr += f"+'Cash flow'!{c}34"
            if key == "retained_earnings":
                expr += f"+'Income statement'!{c}31-'Cash flow'!{c}29"
                amount += _value(plan,"Income statement",f"{c}31")-statements["periods"][period].income_statement["net_income_attributable_to_newmont"]
                if c == "G":
                    amount += _value(plan,"Income statement","E31")-statements["periods"]["q4_2026"].income_statement["net_income_attributable_to_newmont"]
            update(key, expr, amount)
        elif key == "noncontrolling_interests":
            change = statements["periods"][period].income_statement["noncontrolling_net_income"] - _value(plan,"Income statement",f"{c}30")
            if c == "G":
                change += statements["periods"]["q4_2026"].income_statement["noncontrolling_net_income"]-_value(plan,"Income statement","E30")
            update(key, f"{old}-'Income statement'!{c}30+'Cash flow'!{c}33", base[key]+change)
        else:
            update(key, old)
    for key, children in (
        ("current_assets", CURRENT_ASSETS), ("total_assets", ("current_assets", *NONCURRENT_ASSETS, "provisional_ngm_project_asset")),
        ("current_liabilities", (*CURRENT_LIABILITIES, "current_debt")),
        ("total_liabilities", ("current_liabilities", *NONCURRENT_LIABILITIES)),
        ("parent_equity", PARENT_EQUITY), ("total_equity", ("parent_equity", "noncontrolling_interests")),
        ("total_liabilities_and_equity", ("total_liabilities", "total_equity")),
    ):
        update(key, total(children), sum(_value(plan,sheet,cell(child)) for child in children))


def add_nem_statements(plan, statements, nav_capital, raw_capital, costs, rows, *, case):
    actuals = statements["historical_statements"]
    for sheet, title in {"Income statement":"Consolidated income statement", "Balance sheet":"Consolidated balance sheet",
                         "Cash flow":"Consolidated cash flow", "Working capital":"Inventory and working capital",
                         "PP&E":"Property, plant and mine development", "Debt schedule":"Debt, finance leases and share count"}.items():
        sheet_title(plan, sheet, title, "USD millions except per-share data, shares and percentages.")
        periods(plan, sheet, case)
    labels(plan, "Income statement", {row: _label(field) for row, field in INCOME.items()}, totals=(6,18,24,29,31,36))
    labels(plan, "Balance sheet", {row: _label(field) for field, row in BALANCE.items()},
           totals=tuple(row for field,row in BALANCE.items() if field.startswith("total")))
    labels(plan, "Cash flow", {6:"Net income",7:"Depreciation and amortization",8:"Noncash closure expense",9:"Silver stream amortization",
           10:"Closure cash payments",11:"Working capital release",12:"Affiliate cash distributions less equity income",13:"Cash from operations",
           15:"Capital expenditures",16:"Nevada Gold Mines transaction",17:"Affiliate return of capital",18:"Affiliate development contributions",
           19:"Cash from investing",20:"Share repurchases",21:"Common dividends paid",22:"Debt and finance lease principal paid",23:"Cash from financing",
           24:"Opening unrestricted cash",25:"Core unlevered free cash flow",26:"Closure cash payments (positive)",27:"Closing unrestricted cash",
           28:"Net cash change",29:"Common dividends (positive)",30:"Closing debt and finance leases",31:"Debt and finance lease principal (positive)",
           32:"Affiliate operating and investing cash",33:"NCI distributions and funding",34:"Stock award cash add-back",35:"Merian minority free cash flow",
           36:"Income and mining tax rate",37:"Scenario free cash flow change",38:"Merian minority free cash flow before scenario",
           39:"FCFF change per $1 gold move",40:"Minority FCFF change per $1 gold move",41:"Cutoff unrestricted cash before scenario",
           42:"Exchange-rate effect",43:"Held-for-sale cash reclassification",44:"Opening cash including restricted",
           45:"Closing cash including restricted",46:"Current restricted cash",47:"Noncurrent restricted cash"}, totals=(13,19,23,25,27))
    labels(plan, "PP&E", {6:"Opening net PP&E",7:"Sustaining cash capex",8:"Development cash capex",9:"Total cash capex",10:"D&A",11:"Closing net PP&E",
                          12:"Unlevered cash capex",13:"Opening closure liability",14:"Closure cash paid",15:"Closure accretion",16:"Closing closure liability",
                          17:"Current closure liability",18:"Noncurrent closure liability",19:"Capitalized interest"}, totals=(9,11,12,16))
    labels(plan, "Debt schedule", {6:"Opening notes",7:"Scheduled note principal",8:"Closing notes",9:"Opening finance leases",10:"Finance lease principal",
                                  11:"Closing finance leases",12:"Current finance leases",13:"Noncurrent finance leases",14:"Notes and finance leases",
                                  15:"Interest expense, net",16:"Interest income",18:"Buybacks",19:"Common dividends",20:"Closing shares (millions)",
                                  21:"Weighted diluted shares (millions)",22:"Weighted basic shares (millions)",23:"NGM cash contribution"}, totals=(14,20,21))
    labels(plan, "Working capital", {6:"Trade receivables",7:"Accounts payable",8:"Accrued operating costs",9:"Accrued royalties",10:"Payables to NGM",
                                     12:"Operating accruals",13:"Total inventories and stockpiles",14:"Net operating working capital",15:"Trade working capital only",
                                     16:"Working capital cash release",17:"Materials and supplies",18:"Work in process",19:"Concentrate",20:"Precious metals",
                                     21:"Current stockpiles and leach pads",22:"Noncurrent stockpiles and leach pads",24:"Receivables turnover scale",25:"Payables turnover scale",
                                     33:"Stockpile and leach-pad build",34:"Net inventory build"}, totals=(13,14,16,34))
    for sheet, names in {
        "PP&E":{6:"FY2026 attributable sustaining",7:"FY2026 attributable development",8:"FY2027 development increase",9:"Cash capex conversion",
                 10:"FY2026 capitalized interest",13:"FY2026 closure cash",14:"FY2026 accretion",15:"Annual closure spending reduction",16:"FY2027 accretion growth",17:"FY2027 capitalized interest growth",
                 20:"Q4 attributable sustaining guidance",21:"Q4 attributable development guidance"},
        "Debt schedule":{7:"FY2026 note principal",8:"FY2027 note principal",10:"Q4 lease principal",11:"FY2027 gross lease cash",12:"Lease discount rate",13:"FY2028 gross lease cash",
                         15:"FY2026 interest guide",16:"9M actual interest",17:"FY2027 net interest",18:"Q3 interest income",20:"Latest shares outstanding (millions)",21:"Dilutive awards (millions)",
                         22:"Reported Q4 buybacks",23:"Q4 dividend per share",24:"FY2027 dividend per quarter",25:"FY2027 dividend payments",26:"NGM transaction payment",27:"Latest buyback observation"},
        "Cash flow":{6:"9M affiliate income",7:"9M affiliate cash distributions",8:"9M affiliate capital return",9:"9M affiliate expansion cash",11:"9M share awards equity credit",
                     12:"9M minority distributions",13:"9M minority funding",14:"Merian minority share",38:"Merian sustaining capex allocation",41:"NGM cash already paid at cutoff"},
        "Income statement":{11:"FY2026 research and exploration guidance",12:"Historical exploration share",13:"FY2026 corporate G&A guidance"},
    }.items():
        for row, name in names.items():
            plan.set(sheet, f"J{row}", name, "note")
    _history(plan, actuals)
    # The NGM provisional asset did not yet appear in the filed balance sheet.
    plan.set("Actuals", f"D{50+BALANCE['provisional_ngm_project_asset']}", 0, "actual")
    plan.calc("Balance sheet", f"D{BALANCE['provisional_ngm_project_asset']}", f"Actuals!D{50+BALANCE['provisional_ngm_project_asset']}", 0)
    for col, prior, period in (("E","D","q4_2026"),("G","E","fy_2027")):
        _capital(plan, statements, raw_capital, costs, col, prior, period)
        _debt(plan, statements, col, prior, period)
        _working_capital(plan, statements, rows, col, prior, period)
        _income_and_cash(plan, statements, nav_capital, rows, col, prior, period)
        _balance(plan, statements, col, prior, period)
    for field, row in BALANCE.items():
        plan.calc("Balance sheet", f"F{row}", f"E{row}", _value(plan,"Balance sheet",f"E{row}"))
    for row in INCOME:
        if row in (33,34):
            expression = f"(D{row}*273+E{row}*92)/365"
            amount = (_value(plan,"Income statement",f"D{row}")*273+_value(plan,"Income statement",f"E{row}")*92)/365
        elif row in (35,36):
            expression = f"F31/F{row-2}"
            amount = _value(plan,"Income statement","F31")/_value(plan,"Income statement",f"F{row-2}")
        else:
            expression = f"SUM(D{row}:E{row})"
            amount = _value(plan,"Income statement",f"D{row}")+_value(plan,"Income statement",f"E{row}")
        plan.calc("Income statement", f"F{row}", expression, amount, fmt=USD if row in (35,36) else None)
    for row in (6,7,10,13,15,17,18,19,20,21,23):
        amount = _value(plan,"Cash flow",f"D{row}") + _value(plan,"Cash flow",f"E{row}")
        plan.calc("Cash flow", f"F{row}", f"SUM(D{row}:E{row})", amount)
    plan.calc("Cash flow", "F27", "E27", _value(plan,"Cash flow","E27"))
    plan.calc("Cash flow", "F28", "D28+E28", _value(plan,"Cash flow","D28")+_value(plan,"Cash flow","E28"))
    plan.calc("Cash flow", "F44", "D44", _value(plan,"Cash flow","D44"))
    plan.calc("Cash flow", "F45", "F27+SUM(D46:D47)", _value(plan,"Cash flow","F27")+sum(_value(plan,"Cash flow",f"D{row}") for row in (46,47)))

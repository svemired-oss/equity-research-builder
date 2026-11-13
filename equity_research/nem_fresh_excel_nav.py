"""Editable finite-life Newmont NAV, equity bridges and valuation sensitivities."""

from datetime import date
from math import fsum, log

from openpyxl.utils.cell import get_column_letter
from openpyxl.utils.datetime import to_excel

from .fdx_fresh_excel_statements import DAY, M, PCT, USD, _labels, _sheet


FLOW_START = 75
PERIOD_COLUMNS = ("E", "G")
MINE_CASH = "'Mine cash'"
CF = "'Cash flow'"
DEBT = "'Debt schedule'"


def _dcf_summary(plan, nav, valuation, case):
    sheet = "DCF"
    active = nav["scenarios"][case]
    as_of, horizon = nav["as_of"], nav["horizon"]
    close, wacc = nav["reference_close"]["price_usd"], nav["cost_of_capital"]
    structure = valuation["capital_structure"]
    _sheet(plan, sheet, "Net asset value", "Finite mine-life cash flows | USD millions, except per-share values and rates")
    _labels(plan, sheet, {
        5: "Valuation cutoff (pre-open)", 6: "12-month target date", 7: "Reference close", 8: "Active discount rate",
        9: "Spot NAV per share", 10: "12-month target per share", 11: "12-month total return", 12: "Model rating",
        13: "Spot equity NAV", 14: "12-month equity NAV", 15: "Forward dividends per share", 16: "Buy / sell return threshold",
        18: "Cost of capital", 19: "10-year Treasury yield", 20: "Equity risk premium", 21: "Unlevered industry beta",
        22: "Pretax borrowing cost", 23: "Marginal corporate tax rate", 24: "Relevered equity beta", 25: "Cost of equity",
        26: "Market-weighted WACC", 27: "After-tax borrowing cost", 29: "Enterprise-to-equity bridge",
        30: "Mine and corporate cash PV", 31: "Closure cash PV", 32: "Consolidated operating NAV",
        33: "Merian minority cash PV", 34: "Quoted and technical investment marks", 35: "Porcupine installment PV",
        36: "Gross unrestricted cash", 37: "Net cash adjustment", 38: "Notes and finance lease debt",
        39: "Unpaid Nevada Gold Mines contribution", 40: "Equity NAV", 41: "Diluted shares (millions)",
        42: "NAV per diluted share", 44: "Elapsed FY2027 cash share at target", 45: "Q4 cash share after cutoff",
        46: "Q4/FY2027 stock award cash adjustment", 47: "Q4/FY2027 affiliate cash adjustment",
        48: "Q4/FY2027 minority cash adjustment", 49: "FY2027 cash dividend payments by target",
    }, totals=(9, 10, 11, 12, 13, 14, 26, 32, 40, 42), sections=(18, 29))
    plan.set(sheet, "B29", "Spot", "header")
    plan.set(sheet, "C29", "12-month target", "header")
    for row, name in ((19, "risk_free_rate"), (20, "equity_risk_premium"), (21, "unlevered_beta"),
                      (22, "pretax_cost_of_debt"), (23, "tax_rate")):
        plan.set(sheet, f"B{row}", valuation[name]["value"], "input", "0.000" if row == 21 else PCT)
    plan.set(sheet, "B16", valuation["scenarios"]["rating_threshold"], "input", PCT)
    for row, label, value in (
        (19, "Quoted shares (millions)", structure["common_shares_millions"]),
        (21, "September note carrying debt", structure["debt_millions"]),
        (22, "September finance leases", structure["finance_leases_millions"]),
    ):
        plan.set(sheet, f"D{row}", label)
        plan.set(sheet, f"E{row}", value, fmt=M)
    plan.set(sheet, "D20", "Quoted market equity")
    plan.set(sheet, "D23", "Total carrying debt")
    plan.calc(sheet, "E20", "E19*B7", wacc["market_equity_millions"], fmt=M)
    plan.calc(sheet, "E23", "SUM(E21:E22)", wacc["gross_debt_millions"], fmt=M)
    for row, expression, expected, fmt in (
        (5, "Assumptions!B3", to_excel(as_of), DAY), (6, "Assumptions!B14", to_excel(horizon), DAY),
        (7, "Assumptions!B4", close, USD), (8, "Assumptions!B23", wacc["wacc"], PCT),
        (9, "B42", active["spot"]["value_per_share"], USD), (10, "C42", active["target"]["value_per_share"], USD),
        (11, "(B10+B15)/B7-1", active["total_return"], PCT),
        (12, 'IF(B11>=B16,"Buy",IF(B11<=-B16,"Sell","Hold"))', active["rating"], None),
        (13, "B40", active["spot"]["equity_nav_millions"], M), (14, "C40", active["target"]["equity_nav_millions"], M),
        (15, f"({CF}!E29+C49)/{DEBT}!G20", active["dividends_per_share"], USD),
        (24, "B21*(1+(1-B23)*E23/E20)", wacc["levered_beta"], "0.000"),
        (25, "B19+B20*B24", wacc["cost_of_equity"], PCT),
        (26, "(B25*E20+B27*E23)/SUM(E20,E23)", wacc["wacc"], PCT),
        (27, "B22*(1-B23)", wacc["after_tax_cost_of_debt"], PCT),
    ):
        plan.calc(sheet, f"B{row}", expression, expected, kind="total" if row in (9, 10, 11, 12, 13, 14, 26) else "", fmt=fmt)


def _affiliate_and_closure_inputs(plan, nav, statements, capital, valuation):
    sheet = "DCF"
    affiliates = valuation["affiliates"]
    marks = nav["investment_marks_millions"]
    plan.set(sheet, "D25", "Investments and contractual receivable", "section")
    for offset, key in enumerate(("lundin_gold", "lunr_royalties"), 27):
        line = affiliates[key]
        plan.set(sheet, f"D{offset}", "Lundin Gold" if offset == 27 else "LunR Royalties")
        for column, label, amount, fmt in (
            ("E", "Shares held", line["shares"], "#,##0"), ("F", "CAD close", line["price_cad"], USD),
            ("G", "CAD per USD", line["cad_per_usd"], "0.0000"),
        ):
            plan.set(sheet, f"{column}26", label, "header")
            plan.set(sheet, f"{column}{offset}", amount, "input", fmt)
        plan.calc(sheet, f"H{offset}", f"E{offset}*F{offset}/G{offset}/1000000", marks[key], fmt=M)
    plan.set(sheet, "H26", "USD millions", "header")
    pv, other, lunr = (affiliates[key] for key in ("pueblo_viejo", "other_investments", "lunr_royalties"))
    for row, label, e, f, g in (
        (29, "Pueblo Viejo: ownership × technical NPV", pv["ownership"], pv["mine_npv_100pct_millions"], pv["shareholder_loan_net_adjustment_millions"]),
        (30, "September LunR proxy for securities bridge", lunr["shares"], lunr["september_price_cad"], lunr["september_cad_per_usd"]),
        (31, "Other investments: book less September LunR", other["marketable_and_other_carrying_millions"], None, None),
        (32, "Development-project asset marks in base", other["development_projects_base_millions"], None, None),
        (35, "Porcupine face amount and installments", other["porcupine_deferred_consideration_face_millions"],
         other["porcupine_deferred_consideration_annual_payments"], None),
    ):
        plan.set(sheet, f"D{row}", label)
        for column, value in zip("EFG", (e, f, g), strict=True):
            if value is not None:
                plan.set(sheet, f"{column}{row}", value, "input", PCT if (row, column) == (29, "E") else M)
    plan.calc(sheet, "H29", "E29*F29+G29", marks["pueblo_viejo"], fmt=M)
    plan.calc(sheet, "H30", "E30*F30/G30/1000000", other["marketable_and_other_carrying_millions"]
              - marks["other_investment_securities"], fmt=M)
    plan.calc(sheet, "H31", "E31-H30", marks["other_investment_securities"], fmt=M)
    plan.calc(sheet, "H32", "E32", marks["nueva_union_and_norte_abierto"], fmt=M)
    plan.set(sheet, "D33", "Total portfolio investment marks", "total")
    plan.calc(sheet, "H33", "SUM(H27:H29,H31:H32)", fsum(marks.values()), kind="total", fmt=M)
    for column, label in zip("DEFG", ("Porcupine payment date", "Scheduled cash", "Spot PV", "Target PV"), strict=True):
        plan.set(sheet, f"{column}37", label, "header")
    for row, (day_string, amount) in enumerate(nav["porcupine_receivable_payments_millions"].items(), 38):
        payment = date.fromisoformat(day_string)
        if row == 38:
            plan.set(sheet, "D38", to_excel(payment), fmt=DAY)
        else:
            prior = f"D{row - 1}"
            plan.calc(sheet, f"D{row}", f"DATE(YEAR({prior})+1,MONTH({prior}),DAY({prior}))", to_excel(payment), fmt=DAY)
        plan.calc(sheet, f"E{row}", "$E$35/$F$35", amount, fmt=M)
        for column, anchor, day in (("F", "B5", nav["as_of"]), ("G", "B6", nav["horizon"])):
            value = amount / (1 + nav["cost_of_capital"]["wacc"]) ** ((payment - day).days / 365)
            plan.calc(sheet, f"{column}{row}", f"E{row}/(1+$B$8)^((D{row}-${anchor[0]}${anchor[1:]})/365)", value, fmt=M)
    plan.set(sheet, "D43", "Unpaid NGM cash contribution at cutoff")
    plan.set(sheet, "E43", statements["cutoff"]["ngm_payment_modeled_after_cutoff_millions"], fmt=M)
    plan.set(sheet, "D44", "FY2027 conditional Red Chris cash removed")
    plan.set(sheet, "E44", capital["development"]["fy_2027_conditional_red_chris_cash_millions"], "input", M)
    plan.set(sheet, "D45", "FY2027 dividend payments by target / annual")
    plan.set(sheet, "E45", 3, fmt="0")
    plan.set(sheet, "F45", statements["financing_evidence"]["dividends"]["fy_2027_payments"], fmt="0")
    closure = capital["closure"]
    schedule = nav["closure_payments_from_september"]
    balance_day = date.fromisoformat(closure["calibration_date"])
    rate = closure["calibration_accretion_rate"]
    explicit = [schedule[year] for year in (2026, 2027, 2028)]
    explicit_pv = fsum(amount / (1 + rate) ** ((date(year, 12, 31) - balance_day).days / 365)
                       for amount, year in zip(explicit, (2026, 2027, 2028), strict=True))
    residual = closure["undiscounted_residual_cash_millions"]
    book = closure["reported_september_2026_book_millions"]
    tenor = log(residual / (book - explicit_pv)) / log(1 + rate)
    late = max(schedule)
    _labels(plan, sheet, {51: "Closure calibration", 52: "September 2026 balance date", 53: "FY2025 undiscounted closure",
                          54: "9M 2026 payments", 55: "September 2026 book provision", 56: "Accretion rate proxy",
                          57: "Residual nominal payments after 2028", 58: "PV remaining after explicit payments",
                          59: "Effective tenor from balance date (years)", 60: "Effective payment date",
                          62: "Scheduled payment date", 64: "Discounted at accretion proxy",
                          65: "Book provision reconciliation", 66: "Undiscounted cash reconciliation"},
            sections=(51,), totals=(65, 66))
    for row, value, fmt in ((52, to_excel(balance_day), DAY), (53, closure["reported_fy_2025_undiscounted_millions"], M),
                            (54, closure["actual_9m_2026_cash_millions"], M), (55, book, M), (56, rate, PCT)):
        plan.set(sheet, f"B{row}", value, "input" if row == 56 else "", fmt)
    for row, formula, value, fmt in (
        (57, "B53-B54-SUM(B63:D63)", residual, M), (58, "B55-SUM(B64:D64)", book - explicit_pv, M),
        (59, "LN(B57/B58)/LN(1+B56)", tenor, "0.00"), (60, "B52+B59*365", to_excel(balance_day) + tenor * 365, DAY),
        (65, "SUM(B64:F64)-B55", 0, M), (66, "SUM(B63:F63)+B54-B53", 0, M),
    ):
        plan.calc(sheet, f"B{row}", formula, value, fmt=fmt)
    plan.set(sheet, "A63", "Nominal closure cash")
    for index, year in enumerate((2026, 2027, 2028, late - 1, late), 2):
        column = get_column_letter(index)
        day = date(year, 12, 31)
        if index <= 4:
            plan.set(sheet, f"{column}62", to_excel(day), fmt=DAY)
        else:
            formula = "DATE(YEAR(B60)-1,12,31)" if index == 5 else "DATE(YEAR(B60),12,31)"
            plan.calc(sheet, f"{column}62", formula, to_excel(day), fmt=DAY)
        if index in (2, 3):
            plan.calc(sheet, f"{column}63", f"{CF}!{PERIOD_COLUMNS[index - 2]}26", schedule[year], fmt=M)
        elif index == 4:
            plan.set(sheet, "D63", schedule[year], "input", M)
        else:
            early = "(1+$B$56)^(-($E$62-$B$52)/365)"
            later = "(1+$B$56)^(-($F$62-$B$52)/365)"
            formula = f"($B$58-$B$57*{later})/({early}-{later})" if index == 5 else "B57-E63"
            plan.calc(sheet, f"{column}63", formula, schedule[year], fmt=M)
        amount = schedule[year] / (1 + rate) ** ((day - balance_day).days / 365)
        plan.calc(sheet, f"{column}64", f"{column}63/(1+$B$56)^(({column}62-$B$52)/365)", amount, fmt=M)


def _dated_cash_and_equity(plan, nav, statements, capital, case):
    sheet = "DCF"
    active = nav["scenarios"][case]
    spot, target = active["spot"], active["target"]
    wacc = nav["cost_of_capital"]["wacc"]
    fraction = target["bridge"]["cash_fraction_2027"]
    q4_fraction = statements["cutoff"]["calendar_days_remaining"] / 92
    q4, fy27 = (statements["periods"][key] for key in ("q4_2026", "fy_2027"))
    capex_adjustment = capital["development"]["fy_2027_conditional_red_chris_cash_millions"]
    near = plan.expected["Cash flow"]
    mine = plan.expected["Mine cash"]
    for column, label in zip("ABCDEFGHIJKLMNO", (
        "Cash-flow date", "Mine cash before corporate and closure", "Corporate cash costs", "Closure cash",
        "Consolidated FCFF", "Merian minority cash", "Parent FCFF", "Target retained cash share", "Spot years", "Target years",
        "Spot mine and corporate PV", "Spot closure PV", "Spot minority PV", "Target mine and corporate PV", "Target closure PV",
    ), strict=True):
        plan.set(sheet, f"{column}{FLOW_START - 1}", label, "header")
    plan.set(sheet, f"P{FLOW_START - 1}", "Target minority PV", "header")
    flows = nav["base_dated_cash_flows"]
    last = FLOW_START + len(flows) - 1
    for i, flow in enumerate(flows):
        row, day = FLOW_START + i, flow["date"]
        plan.set(sheet, f"A{row}", to_excel(day), fmt=DAY)
        if i < 2:
            column = PERIOD_COLUMNS[i]
            share = q4_fraction if i == 0 else 1
            factor = "*$B$45" if i == 0 else ""
            base_cash = near[f"{column}25"]
            closure = near[f"{column}26"] * share
            minority = near[f"{column}35"] * share
            operating = (base_cash + near[f"{column}26"] + (capex_adjustment if i == 1 else 0)) * share
            adjustment = "+$E$44" if i == 1 else ""
            plan.calc(sheet, f"B{row}", f"({CF}!{column}25+{CF}!{column}26{adjustment}){factor}", operating, fmt=M)
            plan.set(sheet, f"C{row}", 0, fmt=M)
            plan.calc(sheet, f"D{row}", f"{CF}!{column}26{factor}", closure, fmt=M)
            plan.calc(sheet, f"F{row}", f"{CF}!{column}35{factor}", minority, fmt=M)
            corporate = 0
        else:
            column = get_column_letter(i)
            operating, minority, corporate = (mine[f"{column}{line}"] for line in (15, 17, 18))
            closure = flow["closure_cash_millions"]
            for col, line, value in (("B", 15, operating), ("C", 18, corporate), ("F", 17, minority)):
                plan.calc(sheet, f"{col}{row}", f"{MINE_CASH}!{column}{line}", value, fmt=M)
            plan.calc(sheet, f"D{row}", f"SUMIF($D$62:$F$62,A{row},$D$63:$F$63)", closure, fmt=M)
        target_share = (0, 1 - fraction)[i] if i < 2 else 1
        spot_time, target_time = ((day - valuation_day).days / 365 for valuation_day in (nav["as_of"], nav["horizon"]))
        spot_factor, target_factor = (1 + wacc) ** -spot_time, (1 + wacc) ** -target_time
        for col, formula, value, fmt in (
            ("E", f"B{row}-C{row}-D{row}", operating - corporate - closure, M),
            ("G", f"E{row}-F{row}", operating - corporate - closure - minority, M),
            ("H", f"IF(A{row}<=$B$6,0,IF(YEAR(A{row})=YEAR($B$6),1-$C$44,1))", target_share, PCT),
            ("I", f"(A{row}-$B$5)/365", spot_time, "0.000"), ("J", f"(A{row}-$B$6)/365", target_time, "0.000"),
            ("K", f"(B{row}-C{row})/(1+$B$8)^I{row}", (operating - corporate) * spot_factor, M),
            ("L", f"D{row}/(1+$B$8)^I{row}", closure * spot_factor, M),
            ("M", f"F{row}/(1+$B$8)^I{row}", minority * spot_factor, M),
            ("N", f"(B{row}-C{row})*H{row}/(1+$B$8)^J{row}", (operating - corporate) * target_share * target_factor, M),
            ("O", f"D{row}*H{row}/(1+$B$8)^J{row}", closure * target_share * target_factor, M),
            ("P", f"F{row}*H{row}/(1+$B$8)^J{row}", minority * target_share * target_factor, M),
        ):
            plan.calc(sheet, f"{col}{row}", formula, value, fmt=fmt)
    for column, result, stream_cols, receipt in (("B", spot, "KLM", "F"), ("C", target, "NOP", "G")):
        bridge = result["bridge"]
        op_col, closure_col, minority_col = stream_cols
        for row, formula, value in (
            (30, f"SUM({op_col}{FLOW_START}:{op_col}{last})", result["mine_and_corporate_pv_millions"]),
            (31, f"SUM({closure_col}{FLOW_START}:{closure_col}{last})", result["closure_pv_millions"]),
            (32, f"{column}30-{column}31", result["consolidated_operating_nav_millions"]),
            (33, f"SUM({minority_col}{FLOW_START}:{minority_col}{last})", result["merian_minority_nav_millions"]),
            (34, "$H$33", fsum(nav["investment_marks_millions"].values())),
            (35, f"SUM({receipt}38:{receipt}41)", result["porcupine_receivable_pv_millions"]),
            (40, f"{column}32-{column}33+SUM({column}34:{column}37)-SUM({column}38:{column}39)", result["equity_nav_millions"]),
            (41, f"{DEBT}!G21", bridge["shares_millions"]),
            (42, f"{column}40/{column}41", result["value_per_share"]),
        ):
            plan.calc(sheet, f"{column}{row}", formula, value, kind="total" if row in (32, 40, 42) else "",
                      fmt=USD if row == 42 else M)
    target_bridge, spot_bridge = target["bridge"], spot["bridge"]
    for address, formula, value, fmt in (
        ("B36", f"{CF}!E41", spot_bridge["gross_cash_millions"], M),
        ("B38", f"{CF}!E30+{CF}!E31*B45", spot_bridge["debt_and_finance_leases_millions"], M),
        ("B39", "E43", spot_bridge["ngm_obligation_millions"], M),
        ("B45", "Assumptions!B17", q4_fraction, PCT),
        ("C36", f"{CF}!E27-{CF}!E37*(1-$B$45)+({CF}!G28+{CF}!G29+$E$44)*C44-C49", target_bridge["gross_cash_millions"], M),
        ("C37", "-SUM(C46:C48)", target_bridge["net_cash_adjustment_millions"], M),
        ("C38", f"{CF}!E30-{CF}!G31*C44", target_bridge["debt_and_finance_leases_millions"], M),
        ("C44", "(B6-DATE(YEAR(B6),1,1))/(DATE(YEAR(B6)+1,1,1)-DATE(YEAR(B6),1,1))", fraction, PCT),
        ("C46", f"{CF}!E34*$B$45+{CF}!G34*C44", target_bridge["stock_awards_cash_adjustment_millions"], M),
        ("C47", f"{CF}!E32*$B$45+{CF}!G32*C44", target_bridge["affiliate_cash_adjustment_millions"], M),
        ("C48", f"F{FLOW_START}+F{FLOW_START + 1}*C44+{CF}!E33*$B$45+{CF}!G33*C44",
         target_bridge["merian_cash_adjustment_millions"], M),
        ("C49", f"{CF}!G29*$E$45/$F$45", fy27.financing.common_dividends * 3 / 4, M),
    ):
        plan.calc(sheet, address, formula, value, fmt=fmt)
    plan.set(sheet, "B37", 0, fmt=M)
    plan.set(sheet, "C39", 0, fmt=M)
    plan.set(sheet, "A68", "Closure cash is pooled and deducted once. The calibrated effective date represents payments that may span decades.", "note")
    plan.set(sheet, "A69", "The target cash balance includes the modeled Nevada Gold Mines payment. The spot bridge shows the remaining obligation separately.", "note")
    plan.set(sheet, "A70", "Quoted holdings and Pueblo Viejo use cutoff marks. Porcupine installments are re-discounted at the target date.", "note")
    plan.set(sheet, "A72", "Dated operating cash flows and present values", "section")
    plan.set(sheet, "A73", "Quarterly cash after the cutoff and annual year-end cash | Actual days / 365", "note")
    plan.set(sheet, "D47", "Q4 common dividend in return bridge")
    plan.calc(sheet, "E47", f"{CF}!E29", q4.financing.common_dividends, fmt=M)
    plan.set(sheet, "A67", "Dated cash flow coverage of remaining closure")
    plan.calc(sheet, "B67", f"SUM(D{FLOW_START}:D{last})-SUM(B63*$B$45,C63:F63)", 0, kind="check", fmt=M)


def _sensitivity(plan, nav, statements, valuation, case):
    from .nem_fresh_excel_mines import mine_cash_rows

    sheet = "Sensitivity"
    scenario = valuation["scenarios"]
    active = nav["scenarios"][case]
    base = nav["scenarios"]["base"]
    benchmark_grid = nav["sensitivity"]
    flows = nav["base_dated_cash_flows"]
    near = plan.expected["Cash flow"]
    mine_rows = mine_cash_rows(nav)
    tax = statements["periods"]["fy_2027"].income_statement["effective_tax_rate"]
    prices = statements["operating_forecast"]["modeled_prices"]["fy_2027"]
    gold_ratio = prices["gold"].gross_usd / prices["gold"].benchmark_usd
    royalty = plan.expected["Mine plan"]["B6"]
    inflation = valuation["inflation"]["value"]
    minority = valuation["merian"]["nci_share"]
    q4_fraction = statements["cutoff"]["calendar_days_remaining"] / 92
    target_fraction = active["target"]["bridge"]["cash_fraction_2027"]
    _sheet(plan, sheet, "Gold price and WACC sensitivity", "USD per diluted share | Other metal and cost case shocks held at zero")
    _labels(plan, sheet, {4: "Gold price step ($/oz)", 5: "Gold shock at grid center ($/oz)", 6: "WACC step", 7: "Active WACC",
                          9: "Spot NAV per share", 18: "12-month NAV per share", 29: "Sensitivity cash and discount factors"},
            sections=(9, 18, 29))
    for row, amount, fmt in ((4, scenario["gold_price_step_usd"], USD), (5, 0, USD), (6, scenario["wacc_step"], PCT)):
        plan.set(sheet, f"B{row}", amount, "input", fmt)
    plan.calc(sheet, "B7", "DCF!B8", nav["cost_of_capital"]["wacc"], fmt=PCT)
    plan.set(sheet, "A26", "Column headers show the change to the dated gold-price path. The center uses neutral co-product and CAS case shocks.", "note")
    plan.set(sheet, "A27", "Each path adjusts royalties and site cash tax, including the floor on taxable profit. WACC re-discounts operating cash and Porcupine installments.", "note")
    cash_start = 34
    cash_end = cash_start + len(flows) - 1
    receipt_start, receipt_end = cash_end + 2, cash_end + 5
    bridge_row = receipt_end + 3
    mine_support_start = bridge_row + 5
    plan.set(sheet, f"A{mine_support_start - 2}", "Mine after-tax cash by gold shock", "section")
    plan.set(sheet, "A32", "Cash-flow date", "header")
    for offset, label in enumerate(("Spot parent FCFF by gold shock", "Target parent FCFF by gold shock",
                                    "Spot discount factors by WACC", "Target discount factors by WACC")):
        plan.set(sheet, f"{get_column_letter(2 + offset * 5)}31", label, "header")
    for index, shift in enumerate(benchmark_grid["gold_shift_usd"]):
        column = get_column_letter(index + 2)
        target_col = get_column_letter(index + 7)
        delta = index - 2
        for row in (10, 19):
            plan.calc(sheet, f"{column}{row}", f"$B$5+({delta})*$B$4", shift, kind="header", fmt=USD)
        for col in (column, target_col):
            plan.calc(sheet, f"{col}32", f"{column}10", shift, kind="header", fmt=USD)
        plan.calc(sheet, f"{column}{mine_support_start - 1}", f"{column}10", shift, kind="header", fmt=USD)
    for index, rate in enumerate(benchmark_grid["wacc"]):
        spot_col, target_col = (get_column_letter(index + offset) for offset in (12, 17))
        for row in (11 + index, 20 + index):
            plan.calc(sheet, f"A{row}", f"$B$7+({index - 2})*$B$6", rate, kind="header", fmt=PCT)
        for col in (spot_col, target_col):
            plan.calc(sheet, f"{col}32", f"A{11 + index}", rate, kind="header", fmt=PCT)
    plan.set(sheet, f"A{bridge_row}", "Target cash plus bridge adjustments", "total")
    for i, flow in enumerate(flows):
        row, dcf_row = cash_start + i, FLOW_START + i
        day = flow["date"]
        plan.calc(sheet, f"A{row}", f"DCF!A{dcf_row}", to_excel(day), fmt=DAY)
        target_share = 0 if i == 0 else 1 - target_fraction if i == 1 else 1
        if i >= 2:
            first_site_row = mine_support_start + (i - 2) * (len(mine_rows) + 1)
            plan.set(sheet, f"A{first_site_row}", day.year, "section")
            for site_index, site in enumerate(mine_rows, 1):
                name = statements["operating_forecast"]["mine_register"][site]["name"]
                plan.set(sheet, f"A{first_site_row + site_index}", name)
        for index, shift in enumerate(benchmark_grid["gold_shift_usd"]):
            column, target_col = (get_column_letter(index + offset) for offset in (2, 7))
            gold_shock = f"{column}$32"
            if i < 2:
                forecast = PERIOD_COLUMNS[i]
                fraction = q4_fraction if i == 0 else 1
                scenario_delta = near[f"{forecast}37"]
                gold_slope, minority_slope = (near[f"{forecast}{line}"] for line in (39, 40))
                adj = f"+{CF}!{forecast}25-{CF}!{forecast}37+({CF}!{forecast}39-{CF}!{forecast}40)*{gold_shock}-{CF}!{forecast}38"
                if i == 1:
                    adj += "+DCF!$E$44"
                expression = f"({adj.lstrip('+')})" + ("*DCF!$B$45" if i == 0 else "")
                cash = (near[f"{forecast}25"] - scenario_delta - near[f"{forecast}38"] + (gold_slope - minority_slope) * shift) * fraction
                if i == 1:
                    cash += plan.cells["DCF"]["E44"][0]
            else:
                year = day.year
                source_col = get_column_letter(i)
                factor = (1 + inflation) ** (year - 2028)
                site_cash = []
                for site_index, (site, labels) in enumerate(mine_rows.items(), 1):
                    amounts = nav["base_mine_cash"][site][year]
                    gold_qty = amounts["production_and_sales"].get("gold", 0)
                    base_ebit = amounts["cash_sales_millions"] - amounts["cas_millions"] - amounts["depreciation_proxy_millions"]
                    incremental = gold_qty * shift * gold_ratio * factor * (1 - royalty) / 1000
                    ownership = 1 - minority if site == "merian" else 1
                    neutral_cash = amounts["core_cash_millions"]
                    stressed_cash = (neutral_cash + incremental - (max(0, base_ebit + incremental) - max(0, base_ebit)) * tax) * ownership
                    site_cash.append(stressed_cash)
                    cell = {key: f"{MINE_CASH}!{source_col}{labels[key]}" for key in ("gold_quantity", "base_ebit", "ebit", "fcff")}
                    step = (f"{cell['gold_quantity']}*{gold_shock}*'Mine plan'!$B$12*{MINE_CASH}!{source_col}$20"
                            f"/(1+Assumptions!$B$16)*(1-'Mine plan'!$B$6)/1000")
                    stressed = f"({cell['base_ebit']}+{step})"
                    owner = "*(1-'Mine plan'!$B$18)" if site == "merian" else ""
                    formula = f"({cell['fcff']}+{stressed}-{cell['ebit']}-(MAX(0,{stressed})-MAX(0,{cell['ebit']}))*'Mine plan'!$B$7){owner}"
                    plan.calc(sheet, f"{column}{first_site_row + site_index}", formula, stressed_cash, fmt=M)
                first, last = first_site_row + 1, first_site_row + len(mine_rows)
                cash = fsum(site_cash) - flow["corporate_cash_millions"] - flow["closure_cash_millions"]
                expression = f"SUM({column}{first}:{column}{last})-DCF!C{dcf_row}-DCF!D{dcf_row}"
            plan.calc(sheet, f"{column}{row}", expression, cash, fmt=M)
            plan.calc(sheet, f"{target_col}{row}", f"{column}{row}*DCF!H{dcf_row}", cash * target_share, fmt=M)
    for row, dcf_row in zip(range(receipt_start, receipt_end + 1), range(38, 42), strict=True):
        payment_iso, amount = tuple(nav["porcupine_receivable_payments_millions"].items())[row - receipt_start]
        plan.calc(sheet, f"A{row}", f"DCF!D{dcf_row}", to_excel(date.fromisoformat(payment_iso)), fmt=DAY)
        for index in range(2, 12):
            plan.calc(sheet, f"{get_column_letter(index)}{row}", f"DCF!E{dcf_row}", amount, fmt=M)
    for row in (*range(cash_start, cash_end + 1), *range(receipt_start, receipt_end + 1)):
        serial = plan.expected[sheet][f"A{row}"]
        for index, rate in enumerate(benchmark_grid["wacc"]):
            for offset, anchor, start in ((12, "B5", nav["as_of"]), (17, "B6", nav["horizon"])):
                col = get_column_letter(index + offset)
                exponent = (serial - to_excel(start)) / 365
                plan.calc(sheet, f"{col}{row}", f"1/(1+{col}$32)^(($A{row}-DCF!${anchor[0]}${anchor[1:]})/365)",
                          (1 + rate) ** -exponent, fmt="0.0000")
    for index, shift in enumerate(benchmark_grid["gold_shift_usd"]):
        spot_col, target_col = (get_column_letter(index + offset) for offset in (2, 7))
        full_gold_delta = fsum((near[f"{col}39"] - near[f"{col}40"]) * period_share for col, period_share in
                               zip(PERIOD_COLUMNS, (q4_fraction, target_fraction), strict=True))
        bridge_value = (base["target"]["bridge"]["gross_cash_millions"] + base["target"]["bridge"]["net_cash_adjustment_millions"]
                        + full_gold_delta * shift)
        adjustments = []
        for col, fraction in zip(PERIOD_COLUMNS, ("DCF!$B$45", "DCF!$C$44"), strict=True):
            adjustments.append(f"(({CF}!{col}39-{CF}!{col}40)*{spot_col}$32-{CF}!{col}37+{CF}!{col}35-{CF}!{col}38)*{fraction}")
        plan.calc(sheet, f"{target_col}{bridge_row}", f"SUM(DCF!C36:C37)+{'+'.join(adjustments)}", bridge_value, fmt=M)
        for rate_index in range(5):
            for name, row, cash_col, factor_col, bridge in (
                ("spot", 11 + rate_index, spot_col, get_column_letter(12 + rate_index), "SUM(DCF!B36:B37)-SUM(DCF!B38:B39)"),
                ("target", 20 + rate_index, target_col, get_column_letter(17 + rate_index), f"{target_col}{bridge_row}-SUM(DCF!C38:C39)"),
            ):
                formula = (f"(SUMPRODUCT({cash_col}{cash_start}:{cash_col}{cash_end},{factor_col}{cash_start}:{factor_col}{cash_end})"
                           f"+SUMPRODUCT({cash_col}{receipt_start}:{cash_col}{receipt_end},{factor_col}{receipt_start}:{factor_col}{receipt_end})"
                           f"+DCF!$H$33+{bridge})/DCF!B41")
                plan.calc(sheet, f"{spot_col}{row}", formula, benchmark_grid[name][rate_index][index],
                          kind="total" if index == rate_index == 2 else "", fmt=USD)


def add_nem_nav(plan, nav, statements, capital, valuation, *, case):
    """Link the live mine and statement builds to the dated operating and equity NAV."""
    _dcf_summary(plan, nav, valuation, case)
    _affiliate_and_closure_inputs(plan, nav, statements, capital, valuation)
    _dated_cash_and_equity(plan, nav, statements, capital, case)
    _sensitivity(plan, nav, statements, valuation, case)

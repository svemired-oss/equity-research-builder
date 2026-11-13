"""Dated FedEx workbook DCF, sensitivities, and recommendation summary."""

from openpyxl.utils.datetime import to_excel

from .fdx_forward_statements import _dated_inputs
from .fdx_fresh_excel_statements import CASES, DAY, M, PCT, USD, _labels, _sheet


def _cover(plan, valuation, selected):
    sheet = "Cover"
    _sheet(plan, sheet, "FedEx Corporation", "Continuing operations | Equity valuation and financial model")
    case = valuation["scenarios"][selected]
    _labels(plan, sheet, {
        3: "Valuation cutoff (pre-open)", 4: "Reference close", 5: "Active operating case", 6: "Forecast mode",
        8: "Active valuation", 9: "Spot DCF", 10: "12-month target", 11: "12-month total return", 12: "Model rating",
        13: "Weighted average cost of capital", 14: "Target date", 16: "Dated operating case comparison",
    }, totals=(9, 10, 11, 12), sections=(8, 16))
    plan.set(sheet, "B3", to_excel(valuation["as_of"]), fmt=DAY)
    plan.set(sheet, "B4", valuation["reference_close_usd"], fmt=USD)
    plan.set(sheet, "B5", selected.title(), "input")
    plan.set(sheet, "B6", valuation["mode"].title())
    for row, address, value, fmt in (
        (9, "B46", case.spot.value_per_share, USD), (10, "C46", case.target.value_per_share, USD),
        (11, "C48", case.total_return, PCT), (12, "C49", case.recommendation, None),
        (13, "B27", valuation["cost_of_capital"].wacc, PCT), (14, "B5", to_excel(valuation["horizon"]), DAY),
    ):
        plan.calc(sheet, f"B{row}", f"DCF!{address}", value, kind="total", fmt=fmt)
    for col, header in zip("ABCDE", ("Operating case", "Spot DCF", "12-month target", "Total return", "Rating"), strict=True):
        plan.set(sheet, f"{col}17", header, "header")
    for row, name in enumerate(CASES, 18):
        item = valuation["scenarios"][name]
        plan.set(sheet, f"A{row}", name.title())
        for col, value, fmt in (
            ("B", item.spot.value_per_share, USD), ("C", item.target.value_per_share, USD),
            ("D", item.total_return, PCT), ("E", item.recommendation, None),
        ):
            plan.set(sheet, f"{col}{row}", value, fmt=fmt)
    plan.set(sheet, "A22", "The case comparison captures the dated inputs at export. Use the active valuation above for workbook edits.", "note")
    plan.set(sheet, "A23", "Change the case in B5. Blue figures are editable assumptions.", "note")


def _inputs(plan, as_of, inputs_dir, valuation, statements, selected):
    sheet = "DCF"
    case = valuation["scenarios"][selected]
    cap = valuation["cost_of_capital"]
    bridge = case.spot.bridge
    inputs = _dated_inputs(inputs_dir, "discount", as_of)
    assets = _dated_inputs(inputs_dir, "valuation-bridge", as_of)
    _sheet(plan, sheet, "Discounted cash flow", "Continuing operations. USD millions except per-share values and rates. ACT/365 discounting.")
    _labels(plan, sheet, {
        3: "Valuation date", 4: "Reference close date", 5: "12-month target date", 6: "Case selected",
        7: "10-year U.S. Treasury yield", 8: "Equity risk premium", 9: "Cash-adjusted unlevered sector beta",
        10: "Pretax cost of debt (BBB index proxy)", 11: "Tax rate", 12: "September carrying debt and finance leases",
        13: "Terminal growth", 14: "Marginal return on new capital", 15: "WACC sensitivity step",
        16: "Terminal growth sensitivity step", 17: "Operating cash reserve / CY2026 revenue",
        18: "Freight retained stake at quoted price", 19: "InPost transaction cost proxy",
        20: "Nonoperating debt securities", 21: "Other equity securities", 22: "Reference market capitalization",
        23: "Re-levered equity beta", 24: "Cost of equity", 25: "After-tax cost of debt",
        26: "Market equity weight", 27: "Weighted average cost of capital", 28: "Buy total return threshold",
        29: "Sell total return threshold", 30: "Q4 cash-flow share through cutoff", 31: "Estimated debt at cutoff",
        32: "CY2027 elapsed share at target", 33: "Scheduled note principal paid by target",
        34: "Remaining Q4 unlevered free cash flow", 35: "Book cash before reserve",
        36: "CY2027 dividends paid by target", 37: "Operating cash reserve", 38: "Excess cash after reserve and stock comp",
        39: "Notes and finance lease obligations", 40: "Total nonoperating investments",
        41: "Enterprise to equity adjustment", 42: "Diluted shares (millions)", 43: "Basic shares (millions)",
        44: "Equity value", 45: "Post-cutoff stock compensation adjustment", 46: "DCF value per share",
        47: "Forward cash dividends per share", 48: "12-month total return", 49: "12-month model rating",
        50: "First terminal year NOPAT", 51: "Terminal reinvestment", 52: "Terminal pension funding",
        53: "Terminal FCFF", 54: "Terminal value", 55: "Present value of terminal cash flows",
        56: "Enterprise value", 57: "Terminal share of enterprise value",
        59: "Spot sensitivity | Value per share", 71: "12-month sensitivity | Target price",
        83: "Calculation basis",
    }, totals=(27, 38, 41, 44, 46, 48, 49, 53, 56), sections=(59, 71, 83))
    plan.calc(sheet, "B3", "Cover!B3", to_excel(as_of), fmt=DAY)
    plan.set(sheet, "B4", to_excel(valuation["quote_date"]), fmt=DAY)
    plan.calc(sheet, "B5", "DATE(YEAR(B3)+1,MONTH(B3),DAY(B3))", to_excel(valuation["horizon"]), fmt=DAY)
    plan.calc(sheet, "B6", "Cover!B5", selected.title())
    plan.set(sheet, "C34", "12-month target", "header")
    plan.set(sheet, "E18", "FDXF retained shares (millions)")
    plan.set(sheet, "F18", assets["fdxf"]["retained_shares"] / 1e6, fmt="0.000")
    plan.set(sheet, "E19", "FDXF last close")
    plan.set(sheet, "F19", assets["fdxf"]["close_usd"], fmt=USD)
    for row, value, fmt in (
        (7, cap.risk_free_rate, PCT), (8, cap.equity_risk_premium, PCT), (9, cap.unlevered_beta, "0.000"),
        (10, cap.pretax_cost_of_debt, PCT), (13, case.spot.terminal.growth, PCT),
        (14, case.spot.terminal.return_on_new_invested_capital, PCT), (15, inputs["sensitivity_steps"]["wacc"], PCT),
        (16, inputs["sensitivity_steps"]["terminal_growth"], PCT),
        (17, assets["operating_cash_reserve"]["base_share_of_revenue"], PCT),
        (19, bridge.inpost_millions, M), (28, valuation["rating_policy"].buy_at_or_above, PCT),
        (29, valuation["rating_policy"].sell_at_or_below, PCT),
    ):
        plan.set(sheet, f"B{row}", value, "input", fmt)
    cash = statements["q4_operating_cash_bridge"]
    spot = case.spot
    cutoff = statements["cutoff"]
    share = cash.through_cutoff.calendar_days / cash.fourth_quarter.calendar_days
    for row, formula, value, fmt in (
        (11, "Assumptions!B10", cap.tax_rate, PCT), (12, "SUM('Debt schedule'!C10,'Debt schedule'!C18)", cap.carrying_debt_millions, M),
        (18, "F18*F19", bridge.fdxf_millions, M), (20, "'Balance sheet'!C23", bridge.debt_securities_millions, M),
        (21, "'Balance sheet'!C24", bridge.other_equity_securities_millions, M),
        (22, "Cover!B4*B43", cap.market_equity_millions, M), (23, "B9*(1+(1-B11)*B12/B22)", cap.levered_beta, "0.000"),
        (24, "B7+B8*B23", cap.cost_of_equity, PCT), (25, "B10*(1-B11)", cap.after_tax_cost_of_debt, PCT),
        (26, "B22/(B22+B12)", cap.equity_weight, PCT), (27, "B26*B24+(1-B26)*B25", cap.wacc, PCT),
        (30, "Assumptions!B31/Assumptions!B30", share, PCT),
        (31, "B12+(SUM('Cash flow'!D20:D22)+'Debt schedule'!D17)*B30", cutoff["estimated_financing_debt_millions"], M),
        (32, "(B5-DATE(YEAR(B5),1,1))/(DATE(YEAR(B5)+1,1,1)-DATE(YEAR(B5),1,1))",
         case.horizon_cash_roll.elapsed_calendar_days / case.horizon_cash_roll.days_in_year, PCT),
        (33, "Assumptions!E41", case.horizon_cash_roll.scheduled_note_principal_paid_millions, M),
        (34, "'Cash flow'!D34*(1-B30)", cutoff["remaining_unlevered_free_cash_flow_millions"], M),
        (35, "'Balance sheet'!C7+('Cash flow'!D31+SUM('Cash flow'!D20:D22))*B30+'Cash flow'!D16+'Cash flow'!D23",
         cutoff["cash_before_operating_reserve_millions"], M),
        (37, "B17*Revenue!D13", bridge.reserve_millions, M),
        (39, "B31", bridge.debt_including_finance_leases_millions, M),
        (40, "SUM(B18:B21)", bridge.nonoperating_assets_millions, M),
        (42, "'Income statement'!E20", bridge.diluted_shares_millions, M),
        (43, "'Income statement'!E19", bridge.basic_shares_millions, M),
        (45, "0", bridge.post_cutoff_stock_compensation_millions, M),
    ):
        plan.calc(sheet, f"B{row}", formula, value, fmt=fmt)

    target = case.target.bridge
    plan.calc(sheet, "C35", "'Cash flow'!D28+('Cash flow'!E31+'Cash flow'!E22+'Cash flow'!E21)*B32-B33-C36",
              target.book_cash_before_reserve_millions)
    plan.calc(sheet, "C36", "-'Cash flow'!E23", case.horizon_cash_roll.dividends_paid_millions)
    plan.calc(sheet, "C37", "B37", target.reserve_millions)
    plan.calc(sheet, "C39", "'Debt schedule'!D20+('Cash flow'!E22+'Cash flow'!E21+'Debt schedule'!E17)*B32-B33",
              target.debt_including_finance_leases_millions)
    plan.calc(sheet, "C45", "'Cash flow'!D9*(1-B30)+'Cash flow'!E9*B32", target.post_cutoff_stock_compensation_millions)
    plan.calc(sheet, "C40", "B40", target.nonoperating_assets_millions)
    plan.calc(sheet, "C42", "B42", target.diluted_shares_millions)
    plan.calc(sheet, "C43", "B43", target.basic_shares_millions)
    plan.calc(sheet, "C47", "C36/C43", case.modeled_dividends_per_share, fmt=USD)
    plan.calc(sheet, "C48", "(C46+C47)/Cover!B4-1", case.total_return, fmt=PCT, kind="total")
    plan.calc(sheet, "C49", 'IF(C48>=B28,"Buy",IF(C48<=B29,"Sell","Hold"))', case.recommendation, kind="total")
    for col, result in (("B", spot), ("C", case.target)):
        date_ref = "$B$3" if col == "B" else "$B$5"
        dcf_bridge = result.bridge
        for row, formula, value, fmt in (
            (38, f"{col}35-{col}37-{col}45", dcf_bridge.excess_cash_millions, M),
            (41, f"{col}38-{col}39+{col}40", dcf_bridge.enterprise_to_equity_millions, M),
            (44, f"{col}56+{col}41", result.equity_value_millions, M),
            (46, f"{col}44/{col}42", result.value_per_share, USD),
            (50, "'Cash flow'!$I$32*(1+$B$13)", result.terminal.nopat_millions, M),
            (51, f"{col}50*$B$13/$B$14", result.terminal.reinvestment_millions, M),
            (52, "-'Cash flow'!$I$11*(1+$B$13)", result.terminal.pension_funding_millions, M),
            (53, f"{col}50-{col}51-{col}52", result.terminal.unlevered_cash_flow_millions, M),
            (54, f"{col}53/($B$27-$B$13)", result.terminal_value_millions, M),
            (55, f"{col}54/(1+$B$27)^(($I$8-{date_ref})/365)", result.terminal_present_value_millions, M),
            (56, f"SUM({col}55,{('D12:I12' if col == 'B' else 'E16:I16')})", result.enterprise_value_millions, M),
            (57, f"{col}55/{col}56", result.terminal_share_of_enterprise_value, PCT),
        ):
            plan.calc(sheet, f"{col}{row}", formula, value, fmt=fmt)
    return case


def _forecast_discounting(plan, case):
    sheet = "DCF"
    for row, title in ((8, "Cash flow date"), (10, "Spot projected FCFF"), (11, "Spot years to cash flow"),
                       (12, "Spot present value"), (14, "Target projected FCFF"), (15, "Target years to cash flow"),
                       (16, "Target present value")):
        plan.set(sheet, f"C{row}", title, "header" if row == 8 else "")
    for col, flow, pv in zip("DEFGHI", case.spot.projected_cash_flows, case.spot.present_values, strict=True):
        plan.set(sheet, f"{col}8", to_excel(flow.date), "header", "mmm-yy")
        amount = "$B$34" if col == "D" else f"'Cash flow'!{col}34"
        plan.calc(sheet, f"{col}10", amount, flow.amount_millions)
        plan.calc(sheet, f"{col}11", f"({col}$8-$B$3)/365", (flow.date - case.spot.date).days / 365, fmt="0.000")
        plan.calc(sheet, f"{col}12", f"{col}10/(1+$B$27)^{col}11", pv.amount_millions, kind="total")
    for col, flow, pv in zip("EFGHI", case.target.projected_cash_flows, case.target.present_values, strict=True):
        amount = "E10*(1-$B$32)" if col == "E" else f"{col}10"
        plan.calc(sheet, f"{col}14", amount, flow.amount_millions)
        plan.calc(sheet, f"{col}15", f"({col}$8-$B$5)/365", (flow.date - case.target.date).days / 365, fmt="0.000")
        plan.calc(sheet, f"{col}16", f"{col}14/(1+$B$27)^{col}15", pv.amount_millions, kind="total")


def _sensitivity(plan, result, grid, *, target):
    sheet = "DCF"
    top = 73 if target else 61
    value_col = "C" if target else "B"
    flows = "EFGHI" if target else "DEFGHI"
    fcff, years = (14, 15) if target else (10, 11)
    helper = top - 1
    plan.set(sheet, f"A{top}", "WACC / terminal growth", "header")
    plan.set(sheet, f"A{helper}", "Terminal FCFF by growth rate")
    plan.set(sheet, f"J{top}", "PV of forecast FCFF", "header")
    plan.set(sheet, f"K{top}", "Terminal discount factor", "header")
    plan.calc(sheet, f"B{top}", f"{value_col}46", result.value_per_share, fmt=USD)
    base = result.terminal
    last_nopat = base.nopat_millions / (1 + base.growth)
    last_pension = base.pension_funding_millions / (1 + base.growth)
    for col, shift, growth in zip("CDEFGHI", range(-3, 4), grid.growth, strict=True):
        plan.calc(sheet, f"{col}{top}", f"$B$13+({shift})*$B$16", growth, kind="header", fmt=PCT)
        growth_fcf = (last_nopat * (1 - growth / base.return_on_new_invested_capital) - last_pension) * (1 + growth)
        plan.calc(sheet, f"{col}{helper}", f"('Cash flow'!$I$32*(1-{col}{top}/$B$14)+'Cash flow'!$I$11)*(1+{col}{top})",
                  growth_fcf)
    for index, (wacc, values) in enumerate(zip(grid.wacc, grid.value_per_share, strict=True)):
        r = top + 1 + index
        shift = index - 3
        plan.calc(sheet, f"B{r}", f"$B$27+({shift})*$B$15", wacc, kind="header", fmt=PCT)
        factors = [(1 + wacc) ** ((flow.date - result.date).days / 365) for flow in result.projected_cash_flows]
        pv_fcf = sum(flow.amount_millions / factor for flow, factor in zip(result.projected_cash_flows, factors, strict=True))
        pv_terms = ",".join(f"{col}${fcff}/(1+$B{r})^{col}${years}" for col in flows)
        plan.calc(sheet, f"J{r}", f"SUM({pv_terms})", pv_fcf)
        plan.calc(sheet, f"K{r}", f"(1+$B{r})^$I${years}", factors[-1], fmt="0.000")
        for col, price in zip("CDEFGHI", values, strict=True):
            formula = f"($J{r}+{col}${helper}/($B{r}-{col}${top})/$K{r}+${value_col}$41)/${value_col}$42"
            plan.calc(sheet, f"{col}{r}", formula, price, fmt=USD)
    plan.calc("Checks", f"D{19 if target else 18}", f"DCF!F{top + 4}-DCF!{value_col}46", 0, kind="check", fmt="0.00")


def add_fdx_valuation(plan, inputs_dir, as_of, valuation, statements_by_case, selected):
    """Build the active-case DCF and the dated case comparison."""
    _cover(plan, valuation, selected)
    case = _inputs(plan, as_of, inputs_dir, valuation, statements_by_case[selected], selected)
    _forecast_discounting(plan, case)
    _sensitivity(plan, case.spot, case.spot_sensitivity, target=False)
    _sensitivity(plan, case.target, case.target_sensitivity, target=True)
    for row, note in enumerate((
        "Terminal reinvestment equals terminal NOPAT × growth ÷ marginal return on new capital.",
        "The November 2027 horizon falls after all scheduled CY2027 note principal and modeled dividend payments.",
        "The target holds the Freight gross market mark and InPost transaction cost proxy constant.",
        "Disposal timing, incremental tax and a private InPost market value are unavailable.",
    ), 84):
        plan.set("DCF", f"A{row}", note, "note")

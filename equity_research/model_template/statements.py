"""Integrated statements using filed line items and explicit reported groupings."""

from ..operating_research import ResearchGap, _balance

INCOME = "Income Statement"
BALANCE = "Balance Sheet"
CASH = "Cash Flow Statement"

INCOME_ROWS = {
    4: "Revenue", 5: "Operating income", 6: "Interest expense", 7: "Pretax income",
    8: "Income tax", 9: "Net income", 11: "Cost of revenue", 12: "Gross profit",
    13: "Operating expenses", 14: "Other pretax income / (expense)",
    15: "Other net income adjustments", 16: "Operating margin",
}
BALANCE_ROWS = {
    4: "Cash and equivalents", 5: "Short-term investments", 6: "Receivables",
    7: "Inventory", 8: "Other / unclassified operating current assets", 9: "Total current assets",
    10: "Net PP&E", 11: "Operating lease right-of-use assets", 12: "Goodwill",
    13: "Net intangibles", 14: "Other / unclassified noncurrent assets", 15: "Total assets",
    17: "Accounts payable", 18: "Current deferred revenue", 19: "Current operating leases",
    20: "Other / unclassified operating current liabilities", 21: "Current debt and finance leases",
    22: "Total current liabilities", 23: "Noncurrent debt and finance leases",
    24: "Noncurrent operating leases", 25: "Noncurrent deferred revenue",
    26: "Other / unclassified noncurrent liabilities", 27: "Total liabilities",
    29: "Retained earnings", 30: "Other / unclassified parent equity",
    31: "Noncontrolling interests", 32: "Total equity",
    33: "Total liabilities and equity",
}
CASH_ROWS = {
    4: "Net income", 5: "Depreciation and amortization", 6: "Increase in operating working capital",
    7: "Cash from operations", 8: "Capital expenditures paid", 9: "Cash from financing",
    10: "Change in cash", 11: "Full-year FCFF", 12: "Post-cutoff FCFF",
    14: "Stock compensation", 15: "Other / unclassified operating adjustments",
    16: "Other investing cash flows", 17: "Cash from investing", 18: "Debt issued",
    19: "Debt repaid", 20: "Dividends paid", 21: "Buybacks paid",
    22: "Other / unclassified financing flows", 23: "Cash FX effect",
    24: "Opening cash (cash plus restricted cash for historical periods)",
    25: "Closing cash (cash plus restricted cash for historical periods)",
}
DETAIL = {
    6: "receivables", 7: "inventory", 11: "operating_lease_right_of_use_assets",
    12: "goodwill", 13: "net_intangibles", 17: "accounts_payable",
    18: "deferred_revenue_current", 19: "operating_lease_current",
    24: "operating_lease_noncurrent", 25: "deferred_revenue_noncurrent",
    29: "retained_earnings", 31: "noncontrolling_interest",
}
SCHEDULE_ROWS = {
    4: "Opening net PP&E", 5: "Capex", 6: "D&A (combined, charged to PP&E as proxy)",
    7: "Closing net PP&E", 9: "Receivables days", 10: "Inventory days",
    11: "Payables days", 12: "Operating working capital", 13: "Change in operating working capital",
    15: "Opening funded debt", 16: "Debt issued", 17: "Debt repaid",
    18: "Closing funded debt", 19: "Interest on opening debt", 21: "Opening equity",
    22: "Net income", 23: "Dividends", 24: "Buybacks", 25: "Closing equity",
    26: "Closing common shares (millions)", 28: "Pretax income", 29: "Book and cash tax rate",
    30: "Tax expense", 31: "Unlevered tax on EBIT",
}
CHECK_ROWS = {
    4: "Balance sheet difference", 5: "Cash flow movement difference",
    6: "PP&E roll-forward difference", 7: "Debt roll-forward difference",
    8: "Equity roll-forward difference", 9: "Revenue build difference",
    10: "DCF cash flow difference", 11: "DCF sensitivity center difference",
}


def _labels(book):
    for tab, labels in ((INCOME, INCOME_ROWS), (BALANCE, BALANCE_ROWS), (CASH, CASH_ROWS),
                        ("Schedules", SCHEDULE_ROWS), ("Checks", CHECK_ROWS)):
        book.cells[tab] = {}
        book.put(tab, "A1", tab, "title")
        book.put(tab, "A2", "USD millions unless indicated")
        for row, label in labels.items():
            book.put(tab, f"A{row}", label)


def _historical(book, model, period, col, prior):
    raw = period["metrics"]
    date = period["period_end"]
    filing = period["filing"]["url"]

    def fact(tab, row, key):
        value = raw.get(key)
        if value is None:
            book.put(tab, f"{col}{row}", "Unavailable")
        else:
            source = period.get("sources", {}).get(key, {})
            book.input(tab, f"{col}{row}", value / 1e6,
                       source.get("url") or filing, f"{date} | {source.get('tag', key)}")

    for tab in (INCOME, BALANCE, CASH, "Schedules", "Checks"):
        book.put(tab, f"{col}3", f"{date} actual", "header")
    for row, key in {4: "revenue", 5: "operating_income", 6: "interest_expense",
                     7: "income_before_taxes", 8: "income_taxes", 9: "net_income",
                     11: "cost_of_revenue", 12: "gross_profit"}.items():
        fact(INCOME, row, key)
    book.formulas(INCOME, col, {
        13: f'IF(ISNUMBER({col}12),{col}12-{col}5,"Unavailable")',
        14: f'IF(COUNT({col}5:{col}7)=3,{col}7-{col}5+{col}6,"Unavailable")',
        15: f'IF(COUNT({col}7:{col}9)=3,{col}9-({col}7-{col}8),"Unavailable")',
        16: f'IF(COUNT({col}4:{col}5)=2,IF({col}4>0,{col}5/{col}4,"Unavailable"),"Unavailable")',
    })
    for row, key in {**DETAIL, 4: "cash_and_equivalents", 9: "current_assets", 10: "net_ppe",
                     15: "total_assets", 22: "current_liabilities", 27: "total_liabilities"}.items():
        fact(BALANCE, row, key)
    for row, key in {4: "net_income", 5: "depreciation_amortization", 7: "operating_cash_flow",
                     8: "capex", 9: "financing_cash_flow", 10: "change_in_cash_and_restricted_cash",
                     14: "stock_based_compensation", 17: "investing_cash_flow", 20: "dividends_paid",
                     21: "share_repurchases", 23: "exchange_effect_on_cash_and_restricted_cash",
                     25: "cash_and_restricted_cash"}.items():
        fact(CASH, row, key)
    if prior:
        book.formulas(CASH, col, {24: f"{prior}25"})
    book.formulas(CASH, col, {
        15: f'IF(COUNT({col}4:{col}5,{col}7)=3,{col}7-SUM({col}4:{col}5,{col}14),"Unavailable")',
        16: f'IF(COUNT({col}8,{col}17)=2,{col}17+{col}8,"Unavailable")',
        22: f'IF(ISNUMBER({col}9),SUM({col}9,{col}20:{col}21),"Unavailable")',
    })
    # Keep historical balance validation independent of the optional debt detail.
    fact(BALANCE, 32, "total_equity" if raw.get("total_equity") is not None else "equity_attributable_to_parent")
    if raw.get("total_liabilities") is None and raw.get("liabilities_and_equity") is not None:
        fact(BALANCE, 33, "liabilities_and_equity")
        book.formulas(BALANCE, col, {27: f"{col}33-{col}32"})
    else:
        book.formulas(BALANCE, col, {
            33: f'IF(COUNT({col}27,{col}32)=2,{col}27+{col}32,"Unavailable")',
        })
    book.formulas("Checks", col, {
        4: f"""IF(COUNT('{BALANCE}'!{col}15,'{BALANCE}'!{col}33)=2,'{BALANCE}'!{col}15-'{BALANCE}'!{col}33,"Unavailable")""",
        5: f"""IF(COUNT('{CASH}'!{col}24:{col}25,'{CASH}'!{col}10)=3,'{CASH}'!{col}25-'{CASH}'!{col}24-'{CASH}'!{col}10,"Unavailable")""",
    }, kind="check")
    try:
        # _balance adds provenance and notes to its period. Do not mutate loaded evidence.
        from copy import deepcopy
        _, cash, investments, debt, equity, wc_assets, wc_liabs = _balance(deepcopy(period))
    except ResearchGap:
        if period is model["history"][-1]:
            raise
        book.put(BALANCE, f"{col}2", "Some filed debt or working capital detail is unavailable", "note")
        return
    for row, value, basis in (
        (4, cash, "Cash from reconciled filing bridge"), (5, investments, "Short-term investments from reconciled filing bridge"),
        (21, raw["current_liabilities"] / 1e6 - wc_liabs, "Current funded debt and finance leases from filing bridge"),
    ):
        key = {4: "cash_and_equivalents", 5: "short_term_investments", 21: "current_debt"}[row]
        src = period.get("sources", {}).get(key, {})
        book.input(BALANCE, f"{col}{row}", value, src.get("url") or filing,
                   f"{date} | {src.get('basis', basis)}")
    book.input("Schedules", f"{col}18", debt, period.get("operating_bridge_source") or filing,
               f"{date} | Funded debt reconciled by the filing bridge")
    book.formulas(BALANCE, col, {
        8: f"{col}9-SUM({col}4:{col}7)",
        14: f"{col}15-{col}9-SUM({col}10:{col}13)",
        20: f"{col}22-SUM({col}17:{col}19,{col}21)",
        23: f"Schedules!{col}18-{col}21",
        26: f"{col}27-{col}22-SUM({col}23:{col}25)",
        30: f"{col}32-SUM({col}29,{col}31)",
    })
    book.formulas("Schedules", col, {
        7: f"'{BALANCE}'!{col}10",
        12: f"SUM('{BALANCE}'!{col}6:{col}8)-SUM('{BALANCE}'!{col}17:{col}20)",
        25: f"'{BALANCE}'!{col}32",
    })
    assets, liabs = raw.get("total_assets"), raw.get("total_liabilities")
    if assets is not None and (liabs is not None or raw.get("liabilities_and_equity") is not None):
        difference = (assets - liabs - equity * 1e6 if liabs is not None
                      else assets - raw["liabilities_and_equity"]) / 1e6
        book.expected["Checks"][f"{col}4"] = difference


def build_statements(book, model, revenue_total):
    _labels(book)
    history = model["history"][-5:]
    historical = "HIJKL"[-len(history):]
    for i, (period, col) in enumerate(zip(history, historical, strict=True)):
        _historical(book, model, period, col, historical[i-1] if i else None)
    base = "L"
    from .equity import case_block_start
    case_total = case_block_start(revenue_total) + 1 + len(model["lines"])
    book.put(INCOME, "A18", "Forecast other pretax income is the filed three-year median of other nonoperating and "
             "equity-method income. Historical values are all other pretax items, including interest income.")
    book.put("Assumptions", "A18", "Forecast days convention")
    book.input("Assumptions", "B18", 365, basis="Analyst assumption | 365-day working-capital convention")
    for row,label in ((30,"Operating current asset intensity"),(31,"Operating current liability intensity")):
        book.put("Assumptions",f"A{row}",label)
        book.input("Assumptions",f"B{row}",1,basis="Analyst multiplier on the filed operating balance-to-sales ratio",fmt="0.00%")
    book.put("Assumptions", "A19", "Cost of revenue / sales")
    book.formulas("Assumptions", "B", {19: f"""IF(COUNT('{INCOME}'!{base}11)=1,'{INCOME}'!{base}11/'{INCOME}'!{base}4,"Unavailable")"""})
    for row, label in ((20, "Annual debt issuance"), (21, "Annual debt repayment"), (22, "Annual buybacks")):
        book.put("Assumptions", f"A{row}", label)
        book.input("Assumptions", f"B{row}", 0, basis="Analyst assumption | no future capital transaction modeled")
    start = model.get("financing_start") or {}
    book.put("Schedules", "A40", "Year-1 debt change to the latest 10-Q")
    book.put("Schedules", "A41", "Year-1 repurchases already in the latest 10-Q share count")
    if start:
        book.input("Schedules", "B40", start["debt_change"], start["url"],
                   f"{start['period_end']} | 10-Q funded debt less annual funded debt; {start['basis']}")
        book.input("Schedules", "B41", start["repurchases"], start["repurchases_url"],
                   f"{start['period_end']} | Fiscal YTD PaymentsForRepurchaseOfCommonStock in the 10-Q")
    else:
        book.input("Schedules", "B40", 0, basis="No material post-year-end financing change; forecast debt opens at the annual balance")
        book.input("Schedules", "B41", 0, basis="No material post-year-end financing change")
    book.put("Assumptions", "A23", "Repurchase price")
    book.formulas("Assumptions", "B", {23: "Cover!B6"})
    book.put("Assumptions", "C23", "Analyst proxy | dated quote held constant for forecast repurchases")
    book.put(BALANCE, "A35", "Unclassified amounts are filed subtotals less separately identified lines.")
    book.put(BALANCE, "A36", "Undisclosed components stay grouped. Nonoperating balances are held flat.")
    book.put(CASH, "A27", "Forecast cash is opening cash plus operating, investing and financing cash flows.")
    book.put(CASH, "A28", "Historical cash groupings follow available reported totals. Unavailable detail stays labeled.")
    book.expected[CASH] = book.expected.pop("Cash flow")
    for i, col in enumerate("BCDEF"):
        prev = "LBCDE"[i]
        inc, bs, cf, sc = (f"'{tab}'!{col}" for tab in (INCOME, BALANCE, CASH, "Schedules"))
        pb = f"'{BALANCE}'!{prev}"
        hb = f"'{BALANCE}'!$L"
        for tab in (INCOME, BALANCE, CASH, "Schedules", "Checks"):
            book.put(tab, f"{col}3", str(model["dates"][i]) + " forecast", "header")
        book.formulas(INCOME, col, {
            4: f"'Revenue Build'!{col}{case_total}",
            5: f"{col}4*SUM(Assumptions!$B$3,Assumptions!$B$11)",
            6: f"{sc}19", 7: f"{col}5-{col}6+{col}14", 8: f"{sc}30",
            14: "Assumptions!$B$33",
            9: f"{col}7-{col}8",
            11: f'IF(ISNUMBER(Assumptions!$B$19),{col}4*Assumptions!$B$19,"Unavailable")',
            12: f'IF(ISNUMBER({col}11),{col}4-{col}11,"Unavailable")',
            13: f'IF(ISNUMBER({col}12),{col}12-{col}5,"Unavailable")',
            16: f"{col}5/{col}4",
        })
        cost = f"IF(ISNUMBER({inc}11),{inc}11,{inc}4)"
        hist_cost = f"IF(ISNUMBER('{INCOME}'!$L$11),'{INCOME}'!$L$11,'{INCOME}'!$L$4)"
        annual_days = "Assumptions!$B$18"
        for sr, br, flow, hflow in ((9, 6, f"{inc}4", f"'{INCOME}'!$L$4"), (10, 7, cost, hist_cost),
                                    (11, 17, cost, hist_cost)):
            intensity = 31 if br == 17 else 30
            book.formulas("Schedules", col, {
                sr: f'IF(ISNUMBER({hb}{br}),{hb}{br}/{hflow}*{annual_days}*Assumptions!$B${intensity},"Unavailable")',
            })
            book.formulas(BALANCE, col, {
                br: f'IF(ISNUMBER({sc}{sr}),{sc}{sr}/{annual_days}*{flow},"Grouped")',
            })
        book.formulas("Schedules", col, {
            4: f"{pb}10", 5: f"{inc}4*(Assumptions!$B$6+(Assumptions!$B$14-Assumptions!$B$6)*{i}/4)",
            6: f"{inc}4*Assumptions!$B$5", 7: f"SUM({col}4:{col}5)-{col}6",
            12: f"SUM({bs}6:{col}8)-SUM({bs}17:{col}20)", 13: f"{col}12-{prev}12",
            15: f"{pb}21+{pb}23",
            # Year 1 books the post-year-end move to the 10-Q balance, so interest accrues on 10-Q debt.
            16: "Assumptions!$B$20" + ("+MAX(0,Schedules!$B$40)" if not i else ""),
            17: "Assumptions!$B$21" + ("+MAX(0,-Schedules!$B$40)" if not i else ""),
            18: f"SUM({col}15:{col}16)-{col}17",
            19: f"({col}15+Schedules!$B$40)*WACC!$B$6" if not i else f"{col}15*WACC!$B$6",
            21: f"{pb}32", 22: f"{inc}9",
            23: f"MAX(0,{col}22)*Assumptions!$B$16",
            24: "Assumptions!$B$22" + ("+Schedules!$B$41" if not i else ""),
            25: f"SUM({col}21:{col}22)-SUM({col}23:{col}24)",
            # The opening count is the 10-Q bridge count, which already reflects the year-1 repurchases.
            26: f"{'DCF!$B$12' if not i else prev+'26'}-Assumptions!$B$22/Assumptions!$B$23",
            28: f"{inc}7", 29: "Assumptions!$B$4", 30: f"{col}28*{col}29", 31: f"{inc}5*{col}29",
        })
        fixed = {r: f'{pb}{r}' for r in (5, 11, 12, 13, 14, 24, 25, 26, 31)}
        scaled = {r: f'IF(ISNUMBER({hb}{r}),{hb}{r}*{inc}4/\'{INCOME}\'!$L$4*Assumptions!$B$31,"Grouped")'
                  for r in (18, 19)}
        book.formulas(BALANCE, col, {
            **fixed, **scaled,
            4: f"{cf}25", 8: f"{hb}8*{inc}4/'{INCOME}'!$L$4*Assumptions!$B$30", 9: f"SUM({col}4:{col}8)",
            10: f"{sc}7", 15: f"SUM({col}9:{col}14)",
            20: f"{hb}20*{inc}4/'{INCOME}'!$L$4*Assumptions!$B$31",
            21: f"MAX(0,{pb}21-{sc}17)", 22: f"SUM({col}17:{col}21)",
            23: f"{sc}18-{col}21", 27: f"SUM({col}22:{col}26)",
            29: f'IF(ISNUMBER({pb}29),{pb}29+{inc}9-{sc}23,"Grouped")',
            30: f"{pb}30+IF(ISNUMBER({pb}29),0,{inc}9-{sc}23)-{sc}24",
            32: f"SUM({col}29:{col}31)", 33: f"{col}27+{col}32",
        })
        book.formulas(CASH, col, {
            4: f"{inc}9", 5: f"{sc}6", 6: f"{sc}13", 7: f"SUM({col}4:{col}5)-{col}6",
            8: f"{sc}5", 9: f"{col}18-SUM({col}19:{col}21)", 10: f"SUM({col}7,{col}17,{col}9)",
            11: f"{inc}5-{sc}31+{col}5-{col}6-{col}8",
            12: f"{col}11" + ("*Assumptions!$B$12" if i == 0 else ""),
            17: f"-{col}8", 18: f"{sc}16", 19: f"{sc}17", 20: f"{sc}23", 21: f"{sc}24",
            24: f"{pb}4", 25: f"SUM({col}24,{col}10)",
        })
        book.formulas("Checks", col, {
            4: f"{bs}15-{bs}33", 5: f"{cf}25-{cf}24-{cf}10", 6: f"{bs}10-{sc}4-{sc}5+{sc}6",
            7: f"{bs}21+{bs}23-{sc}15-{sc}16+{sc}17", 8: f"{bs}32-{sc}25",
            9: f"{inc}4-'Revenue Build'!{col}{case_total}", 10: f"DCF!{col}21-{cf}12",
        }, kind="check")
        book.expected["Checks"].update({f"{col}{r}": 0 for r in range(4, 11)})


"""Write the fresh GD statement formulas and the schedules that drive them."""

from datetime import date


NUMBER = '#,##0.00;(#,##0.00);"-"'
COUNT = '#,##0;(#,##0);"-"'
RATE = '0.00%;(0.00%);"-"'
DAY = "mm/dd/yy"
INPUT = "'Statement inputs'!"
INCOME = "Income statement"
BALANCE = "Balance sheet"
CASH = "Cash flow"
EQUITY = "Equity"
PPE = "PP&E"
DEBT = "Debt"
NWC = "NWC"
FUTURE = "HIJKL"
HISTORICAL = "BCDEF"
YTD_CASH_ROWS = {
    12: "cash_from_operations", 15: "other_investing", 16: "cash_capex", 17: "cash_from_investing",
    26: "note_draws", 27: "note_repayments", 29: "dividends_paid", 30: "share_repurchases", 31: "other_financing",
    32: "cash_from_financing", 33: "cash_from_discontinued", 53: "cash_change", 55: "cash_beginning", 56: "cash_ending",
}


LABELS = {
    INCOME: {
        7: "Product revenue (reported)", 8: "Service revenue (reported)", 9: "Total revenue",
        12: "Product operating costs (reported)", 13: "Service operating costs (reported)",
        14: "General and administrative expense (reported)", 15: "Total operating costs and expenses",
        17: "Operating earnings", 19: "Other income, net", 20: "Net interest expense",
        22: "Earnings before income tax", 24: "Income tax provision", 26: "Net earnings",
        29: "Operating margin", 30: "Effective tax rate", 31: "Net margin",
    },
    BALANCE: {
        8: "Cash and equivalents", 9: "Accounts receivable", 10: "Unbilled receivables", 11: "Inventories",
        12: "Other current assets", 13: "Total current assets", 16: "Property, plant and equipment, net",
        17: "Intangible assets, net", 18: "Goodwill", 19: "Other noncurrent assets",
        20: "Total noncurrent assets", 21: "Finance lease right-of-use asset", 22: "Total assets",
        26: "Current debt (reported historical split)", 27: "Accounts payable",
        28: "Customer advances and deposits", 29: "Other current liabilities",
        30: "Current liabilities before separately shown debt and leases",
        31: "Dividends payable (separately shown from Q3 2026)",
        33: "Long-term debt historically and total carrying debt from Q3 2026",
        34: "Other noncurrent liabilities", 35: "Noncurrent and separately shown liabilities",
        37: "Total liabilities", 38: "Finance lease liability (separately shown from Q3 2026)",
        40: "Common stock (separately shown historically)", 41: "Paid-in capital (includes common stock from Q3 2026)",
        42: "Retained earnings", 43: "Treasury stock", 44: "Accumulated other comprehensive income (loss)",
        45: "Total shareholders' equity", 47: "Total liabilities and shareholders' equity",
        49: "Assets less liabilities and equity",
    },
    CASH: {
        3: "Net earnings", 4: "Depreciation", 5: "Intangible amortization", 6: "Finance lease ROU amortization",
        7: "Stock compensation", 8: "Debt cost amortization", 9: "Change in operating working capital",
        10: "Other operating cash adjustments and unallocated YTD working capital", 12: "Cash from operations",
        15: "Other investing cash", 16: "Cash capital expenditures", 17: "Cash from investing", 26: "Note borrowings",
        27: "Note principal repayments", 28: "Finance lease principal (modeled)", 29: "Dividends paid",
        30: "Share repurchases", 31: "Other financing cash", 32: "Cash from financing",
        33: "Other cash flows, including discontinued operations",
        47: "Operating cash less cash capex", 48: "Cash flow to equity",
        53: "Change in cash and equivalents", 55: "Opening cash and equivalents",
        56: "Closing cash and equivalents", 60: "FCFE after the valuation cutoff",
        61: "Stock compensation after the cutoff (time estimate)",
    },
    PPE: {
        8: "Machinery and equipment (reported)", 9: "Buildings and improvements (reported)",
        10: "Construction in process (reported)", 11: "Land and improvements (reported)",
        13: "Gross PP&E", 14: "Accumulated depreciation", 16: "Net PP&E",
        20: "Cash capital expenditures", 21: "Depreciation", 24: "Capex as a share of sales",
        25: "Depreciation rate on average gross PP&E", 29: "Opening gross PP&E",
        34: "Closing intangible assets", 35: "Intangible amortization",
        39: "Closing finance lease right-of-use asset", 40: "Finance lease ROU amortization",
    },
    NWC: {
        110: "Revenue", 114: "Revenue relative to prior fiscal year",
        116: "Accounts receivable", 117: "Inventories", 118: "Accounts payable",
        119: "Unbilled receivables", 120: "Net operating working capital", 121: "Working capital investment",
        122: "Operating working capital scaled with sales", 123: "Customer advances and deposits",
        124: "Other current assets", 125: "Other current liabilities excluding dividends and finance leases when separately shown",
    },
    DEBT: {
        75: "Opening Note H principal", 79: "Cash note borrowings", 80: "Cash note principal repayments",
        81: "Closing Note H principal", 82: "Unamortized debt costs and discounts (contra balance)",
        83: "Debt cost amortization", 86: "Finance lease liability", 87: "Finance lease principal payment",
        88: "Total finance lease payment", 90: "Closing Note H carrying debt",
        93: "Surviving fixed-note annual coupons", 94: "Fixed-note cash coupons",
        95: "Net interest expense", 96: "Interest income from opening cash", 97: "Finance lease interest",
    },
    EQUITY: {
        8: "Opening retained earnings", 9: "Net earnings", 10: "Dividends declared (equity reduction)",
        13: "Closing retained earnings", 17: "Opening treasury stock", 18: "Share repurchases",
        20: "Estimated repurchase execution price per share", 21: "Closing common shares outstanding (millions)",
        22: "Closing treasury stock", 25: "Quarterly dividend per share",
        27: "Net non-program shares added (whole shares)", 28: "Program shares purchased (whole shares)",
        29: "Remaining repurchase authorization (whole shares)", 30: "Cash dividends paid",
        31: "Closing dividends payable", 32: "Paid-in capital including common stock",
        33: "Other net equity cash", 34: "Stock compensation", 41: "Accumulated other comprehensive income (loss)",
        52: "Total shareholders' equity",
    },
}


def _rows(book, sheet, column, values):
    for row, value in values.items():
        fmt = RATE if (sheet, row) in {
            (INCOME, 29), (INCOME, 30), (INCOME, 31), (PPE, 24), (PPE, 25), (NWC, 114),
        } else COUNT if sheet == EQUITY and row in (27, 28, 29) else NUMBER
        if sheet == BALANCE and row == 49:
            fmt = "0.00;(0.00);0.00"
        book.put(sheet, f"{column}{row}", value, fmt=fmt)


def _round_count(expression):
    # Python round and Excel ROUND differ on an exact half with an even lower integer.
    return f"IF(MOD(ABS({expression}),2)=0.5,SIGN({expression})*ROUNDDOWN(ABS({expression}),0),ROUND({expression},0))"


def _layout(book, data):
    end = date(data.interim.as_of.year, 12, 31)
    for sheet, labels in LABELS.items():
        book.put(sheet, "A1", f"General Dynamics Corporation | {sheet}")
        header = 2 if sheet == CASH else 5
        book.put(sheet, f"A{header}", "USD millions except where indicated")
        if sheet != CASH:
            book.put(sheet, "A3", "Selected case")
            book.put(sheet, "B3", "='Cover'!E13")
        for row, label in labels.items():
            book.put(sheet, f"A{row}", label)
        for col, year in zip("BCDEFGHIJKL", range(end.year - 5, end.year + 6), strict=True):
            book.put(sheet, f"{col}{header}", date(year, 12, 31), fmt="yyyy")
        book.put(sheet, f"O{header}", data.interim.period_end, fmt='"Reported "mm/dd/yy')
        book.put(sheet, f"P{header}", end, fmt='"Q4E "mm/dd/yy')
        if sheet != CASH:
            book.put(sheet, "G4", "FY estimates")
        else:
            book.put(sheet, "A58", "Selected case")
            book.put(sheet, "B58", "='Cover'!E13")
    book.put(BALANCE, "A51", "The October finance lease split and forecast debt classification follow the statement model.")
    if data.statements["base"].evidence.ytd_cash_flow is None:
        book.put(CASH, "A35", "Other investing and financing cash when not separately supplied")
        book.put(CASH, "A63", "FY2026 combines reported nine-month data and modeled Q4. Unavailable reported detail is grouped or left blank.")
    else:
        book.put(CASH, "A63", "FY2026 combines reported nine-month cash and modeled Q4. Reported lease principal remains in other financing.")
        book.put(CASH, "A64", "The Q4 projection groups other investing and discontinued cash in other cash flows.")
    book.put(NWC, "A127", "Q4 customer advances absorb the modeled operating cash residual. Earlier current liabilities retain the filed presentation.")


def _inputs(book, data):
    sheet = "Statement inputs"
    base = data.statements["base"]
    ev, a, fin, cash, interim = base.evidence, base.assumptions, data.financing, data.cash_forecast, data.interim
    dividend = interim.scheduled_dividend
    book.put(sheet, "A1", "General Dynamics Corporation | Statement inputs")
    book.put(sheet, "A3", "USD millions except rates, dates and share counts as labeled")
    fields = {
        6: ("Valuation cutoff", interim.as_of), 7: ("Reported October balance date", interim.period_end),
        8: ("Modeled cutoff cash", "='Balance sheet'!O8+B49-B50+B41*B14/B17-B51*B56/1000000-IF(AND(B36>B7,B36<=B6),'Balance sheet'!O31,0)"),
        9: ("Modeled cutoff carrying debt", "='Debt'!O90"),
        10: ("Modeled cutoff common shares (millions)", "='Equity'!O21+(B52-B51)/1000000"),
        11: ("Cutoff dividend owed to prior holders", "=IF(AND(B35<B6,B6<B36),'Balance sheet'!O31,0)"),
        12: ("Fiscal year end", "=DATE(YEAR(B6),12,31)"), 13: ("Days in Q4", "=B12-B7"),
        14: ("Q4 days through cutoff", "=B6-B7"), 15: ("Q4 days after cutoff", "=B12-B6"),
        16: ("Elapsed fiscal days through the filed quarter", "=B7-DATE(YEAR(B6)-1,12,31)"),
        17: ("Days in Q3", "=B7-B18"), 18: ("Reported Q2 end", fin.q2_end),
        19: ("Annual finance lease principal proxy", fin.annual_lease_principal_proxy_millions),
        20: ("FY2025 finance lease ROU asset", ev.lease_rou_asset), 21: ("FY2025 finance lease liability", ev.lease_liability),
        22: ("Modeled yield on cash", a.cash_yield), 23: ("Finance lease discount rate", ev.lease_rate),
        24: ("Other debt principal", ev.other_principal), 25: ("Other debt coupon", a.other_debt_coupon),
        26: ("Depreciation rate", a.depreciation_rate), 27: ("Annual finance lease ROU amortization", a.annual_finance_rou_amortization),
        28: ("Final-year intangible amortization", a.final_intangible_amortization),
        29: ("Final-year total finance lease payment", a.final_lease_payment),
        30: ("Annual debt cost amortization", a.annual_debt_cost_amortization), 31: ("Noninterest other income", a.other_income),
        32: ("Annual dividend growth", a.dividend_growth), 33: ("Annual buyback price growth", a.buyback_price_growth),
        34: ("Declared quarterly dividend per share", dividend.usd_per_share),
        35: ("Dividend record date", dividend.record_date), 36: ("Dividend payment date", dividend.payment_date),
        37: ("Q2 shares outstanding (whole shares)", fin.q2_shares),
        38: ("Q3 program purchases (whole shares)", fin.q3_program_shares),
        39: ("Q3 closing buyback authorization (whole shares)", fin.q3_program_shares_remaining),
        40: ("Q3 cash repurchases", fin.q3_repurchases_millions),
        41: ("Q3 other financing, investing and discontinued cash", fin.q3_other_cash_millions),
        42: ("Q3 other investing and discontinued cash", fin.q3_nonfinancing_cash_millions),
        43: ("Q4 program repurchase pace multiplier", fin.repurchase_pace),
        44: ("Non-program share pace multiplier", fin.non_program_share_pace),
        45: ("October unamortized debt costs", ev.unamortized_costs),
        46: ("FY2026 issuer FCF conversion", cash.outlook.fcf_conversion),
        47: ("FY2026 diluted share count proxy (millions)", cash.diluted_shares_proxy_millions),
        48: ("FY2026 guided net interest expense", interim.guidance.net_interest_expense),
        49: ("Common elapsed operating cash estimate", "=((G55*B47*B46+G56*G54)-'Cash flow'!O12)*B14/B13"),
        50: ("Common elapsed cash capex estimate", "=(G56*G54+'Cash flow'!O16)*B14/B13"),
        51: ("Common elapsed repurchases (whole shares)", f"={_round_count('B55*B14')}"),
        52: ("Common elapsed net non-program additions (whole shares)", f"={_round_count('B54*B14')}"),
        54: ("Net non-program share additions per day", "=(ROUND('Equity'!O21*1000000,0)-B37+B38)*B44/B17"),
        55: ("Program repurchases per day", "=B38*B43/B17"), 56: ("Q3 execution price proxy per share", "=B40*1000000/B38"),
        57: ("Selected FY2026 diluted EPS", "=INDEX(F55:H55,1,MATCH('Cover'!$E$13,F53:H53,0))"),
        58: ("Selected FY2026 cash capex share of sales", "=INDEX(F54:H54,1,MATCH('Cover'!$E$13,F53:H53,0))"),
    }
    for row, (label, value) in fields.items():
        book.put(sheet, f"A{row}", label)
        fmt = DAY if row in (6, 7, 12, 18, 35, 36) else RATE if row in (22, 23, 25, 26, 32, 33, 46, 58) else (
            COUNT if row in (13, 14, 15, 16, 17, 37, 38, 39, 51, 52) else NUMBER
        )
        editable = row in (22, 25, 26, 27, 28, 29, 30, 31, 32, 33, 43, 44, 46)
        book.put(sheet, f"B{row}", value, fmt=fmt, kind="input" if editable else None)
    for offset, col in enumerate(FUTURE):
        year = interim.as_of.year + offset + 1
        book.put(sheet, f"{col}46", date(year, 12, 31), fmt="yyyy")
        book.put(sheet, f"{col}47", a.capex_share[offset], kind="input", fmt=RATE)
        book.put(sheet, f"{col}48", a.book_tax_rate[offset], kind="input", fmt=RATE)
        book.put(sheet, f"{col}49", ev.intangible_amortization.get(year, "=$B$28"), fmt=NUMBER)
        book.put(sheet, f"{col}50", ev.lease_payments.get(year, "=$B$29"), fmt=NUMBER)
    book.put(sheet, "G49", ev.intangible_amortization[interim.as_of.year], fmt=NUMBER)
    for row, label in ((47, "Cash capex / sales"), (48, "Book tax"), (49, "Intangible amortization"), (50, "Finance lease payments")):
        book.put(sheet, f"E{row}", label)
    for row, label in ((53, "FY2026 cash guidance"), (54, "Cash capex / sales"), (55, "Diluted EPS"), (56, "FY revenue")):
        book.put(sheet, f"E{row}", label)
    for col, case, capex in zip("FGH", ("low", "base", "high"),
                                (cash.outlook.capex_share_of_sales[0], sum(cash.outlook.capex_share_of_sales) / 2,
                                 cash.outlook.capex_share_of_sales[1]), strict=True):
        book.put(sheet, f"{col}53", {"low": "Bear", "base": "Base", "high": "Bull"}[case])
        book.put(sheet, f"{col}54", capex, kind="input", fmt=RATE)
        book.put(sheet, f"{col}55", cash.fiscal_cases[case].annual_diluted_eps, fmt=NUMBER)
        revenue_row = {"low": 14, "base": 11, "high": 15}[case]
        book.put(sheet, f"{col}56", f"='Operating inputs'!B{revenue_row}", fmt=NUMBER)
    for col, label in zip("EFGHI", ("Note maturity", "Principal", "Coupon", "Annual coupon cash", "Maturity-year coupon cash"), strict=True):
        book.put(sheet, f"{col}59", label)
    for row, note in enumerate(ev.notes, 60):
        book.put(sheet, f"E{row}", note.maturity, fmt="mmm-yy")
        book.put(sheet, f"F{row}", note.principal, fmt=NUMBER)
        book.put(sheet, f"G{row}", note.coupon, fmt=RATE)
        book.put(sheet, f"H{row}", f"=F{row}*G{row}", fmt=NUMBER)
        book.put(sheet, f"I{row}", f"=IF(MONTH(E{row})<=6,H{row}/2,H{row})", fmt=NUMBER)
    for row, (label, source) in enumerate((
        ("October balance and YTD", ev.sources["interim"]), ("Annual lease/intangibles", ev.sources["annual"]),
        ("Q3 cash outlook", cash.outlook.source), ("Q2 share baseline", fin.q2_source),
        ("Q3 financing baseline", fin.q3_source), ("Dividend declaration", dividend.declaration),
        *((f"Model {name}", source) for name, source in a.sources.items()),
    ), 72):
        book.put(sheet, f"A{row}", label)
        book.put(sheet, f"B{row}", source.available_on, fmt=DAY)
        book.put(sheet, f"C{row}", source.url or "")
        book.put(sheet, f"D{row}", source.citation)


def _historical(book, data):
    history = data.history.history
    cash_history = data.history.cash_flow_actuals
    if cash_history is None or set(data.history.historical_years) - cash_history.keys():
        raise ValueError("The fresh statement workbook requires cash-flow evidence for each historical fiscal year.")
    for col, year in zip(HISTORICAL, data.history.historical_years, strict=True):
        def source(sheet, rows):
            return {row: history[f"{sheet}!{col}{row}"] for row in rows if f"{sheet}!{col}{row}" in history}

        _rows(book, INCOME, col, source(INCOME, (7, 8, 9, 12, 13, 14, 15, 17, 19, 20, 22, 24, 26)))
        _rows(book, BALANCE, col, source(BALANCE, (8, 9, 10, 11, 12, 13, 16, 17, 18, 19, 20, 22, 26, 27, 28, 29,
                                                  30, 33, 34, 35, 40, 41, 42, 43, 44, 45, 47)))
        _rows(book, BALANCE, col, {37: f"={col}30+{col}35", 49: f"={col}22-{col}47"})
        _rows(book, PPE, col, source("Schedules", (8, 9, 10, 11, 13, 14, 16, 20, 21)))
        _rows(book, EQUITY, col, source(EQUITY, (9, 13, 21, 22, 41, 52)))
        _rows(book, EQUITY, col, {32: f"=SUM('Balance sheet'!{col}40:{col}41)"})
        _rows(book, DEBT, col, source("Schedules", (90, 95)))
        _nwc_links(book, col, None)
        cash = cash_history[year].amounts
        _rows(book, CASH, col, {
            3: f"='Income statement'!{col}26", 4: f"='PP&E'!{col}21", 12: cash["cash_from_operations"],
            16: -cash["capital_expenditures"], 17: cash["cash_from_investing"], 32: cash["cash_from_financing"],
            33: cash["cash_from_discontinued"], 53: cash["cash_change"], 55: cash["cash_beginning"], 56: cash["cash_ending"],
        })


def _nwc_links(book, col, prev):
    keys = {116: 9, 117: 11, 118: 27, 119: 10, 123: 28, 124: 12, 125: 29}
    rows = {110: f"='Income statement'!{col}9",
            120: f"={col}122+{col}124-{col}125",
            122: f"={col}116+{col}117+{col}119-{col}118-{col}123"}
    if prev is None:
        rows.update({row: f"='Balance sheet'!{col}{bs_row}" for row, bs_row in keys.items()})
    elif col == "P":
        rows.update({row: f"=O{row}" for row in keys})
        rows[123] = "=O123-P121"
        rows[121] = "=SUM('Cash flow'!P3:P8)-('Income statement'!G26*'Statement inputs'!$B$46+'PP&E'!G20-'Cash flow'!O12)"
    else:
        rows[114] = f"={col}110/{prev}110"
        rows.update({row: f"={prev}{row}*{col}114" if row in (116, 117, 118, 119, 123) else f"={prev}{row}" for row in keys})
        rows[121] = f"={col}120-{prev}120"
    _rows(book, NWC, col, rows)


def _reported(book, data):
    evidence = data.statements["base"].evidence
    b, ytd = evidence.balance, evidence.ytd
    interim = data.interim.ytd
    _rows(book, INCOME, "O", {14: -ytd["g_and_a"], 19: ytd["other_income"], 20: -interim["net_interest_expense"], 24: -ytd["taxes"]})
    _rows(book, BALANCE, "O", {
        8: b["cash"], 9: b["receivables"], 10: b["unbilled"], 11: b["inventory"],
        12: b["other_current_assets"], 16: "='PP&E'!O16", 17: "='PP&E'!O34", 18: b["goodwill"],
        19: "=O52-O21", 21: "='PP&E'!O39", 27: b["accounts_payable"], 28: b["customer_advances"],
        29: f"=O53-O31-{INPUT}$B$19", 31: b["dividends_payable"], 33: b["debt_carrying"],
        34: f"=O54-O38+{INPUT}$B$19", 38: "='Debt'!O86", 41: b["paid_in_capital"],
        42: b["retained_earnings"], 43: b["treasury_stock"], 44: b["aoci"],
        52: b["other_assets"], 53: b["other_current_liabilities"], 54: b["other_liabilities"],
    })
    for row, label in ((52, "Reported other noncurrent assets"), (53, "Reported other current liabilities"), (54, "Reported other noncurrent liabilities")):
        book.put(BALANCE, f"A{row}", label)
    _rows(book, PPE, "O", {
        13: b["ppe_gross"], 14: -b["ppe_accumulated_depreciation"], 16: "=SUM(O13:O14)",
        20: interim["capital_expenditures"], 21: ytd["depreciation"], 34: b["intangibles"],
        35: ytd["intangible_amortization"], 39: f"={INPUT}$B$20-O40", 40: ytd["lease_amortization"],
    })
    _rows(book, DEBT, "O", {
        81: f"='Balance sheet'!O33+{INPUT}$B$45", 82: f"=-{INPUT}$B$45", 86: f"={INPUT}$B$21-{INPUT}$B$19*{INPUT}$B$16/({INPUT}$B$16+{INPUT}$B$13)",
        90: "=SUM(O81:O82)", 95: "=-'Income statement'!O20",
    })
    _rows(book, EQUITY, "O", {
        9: "='Income statement'!O26", 13: "='Balance sheet'!O42",
        21: data.interim.period_end_balances["shares_outstanding"] / 1_000_000, 22: "='Balance sheet'!O43",
        29: f"={INPUT}$B$39", 31: "='Balance sheet'!O31", 32: "='Balance sheet'!O41",
        34: ytd["sbc"], 41: "='Balance sheet'!O44", 52: "=O13+O22+O32+O41",
    })
    cash_rows = {
        3: "='Income statement'!O26", 4: "='PP&E'!O21", 5: "='PP&E'!O35", 6: "='PP&E'!O40", 7: "='Equity'!O34",
        10: "=O12-SUM(O3:O8)", 12: interim["cash_from_operations"], 16: "=-'PP&E'!O20",
    }
    filed_cash = evidence.ytd_cash_flow
    if filed_cash is None:
        cash_rows.update({35: "=O53-O12-O16", 53: "=O56-O55", 55: "='Balance sheet'!F8", 56: "='Balance sheet'!O8"})
    else:
        if (filed_cash["cash_from_operations"] != interim["cash_from_operations"]
                or filed_cash["cash_capex"] != -interim["capital_expenditures"]
                or filed_cash["cash_beginning"] != data.history.history["Balance sheet!F8"]):
            raise ValueError("Filed YTD cash flow disagrees with interim evidence or the prior-year closing cash.")
        cash_rows.update({
            **{row: filed_cash[YTD_CASH_ROWS[row]] for row in (15, 26, 27, 29, 30, 31, 33, 55)},
            17: "=SUM(O15:O16)", 32: "=SUM(O26:O31)", 53: "=SUM(O12,O17,O32:O33)", 56: "=O55+O53",
        })
    _rows(book, CASH, "O", cash_rows)
    _nwc_links(book, "O", None)


def _income_formulas(book):
    for col in "G" + FUTURE + "OP":
        rows = {9: f"='Revenue model'!{col}64", 15: f"={col}17-{col}9", 17: f"='Revenue model'!{col}74",
                22: f"=SUM({col}17,{col}19:{col}20)", 26: f"={col}22+{col}24"}
        if col == "G":
            rows.update({19: f"={INPUT}$B$31", 20: f"=-{INPUT}$B$48", 24: f"={INPUT}$B$57*{INPUT}$B$47-G22"})
        elif col == "P":
            rows.update({row: f"=G{row}-O{row}" for row in (19, 20, 24)})
        elif col != "O":
            rows.update({19: f"={INPUT}$B$31", 20: f"=-'Debt'!{col}95", 24: f"=-{col}22*{INPUT}{col}48"})
        _rows(book, INCOME, col, rows)
    for col in HISTORICAL + "G" + FUTURE + "OP":
        _rows(book, INCOME, col, {29: f"={col}17/{col}9", 30: f'=IF({col}22=0,"n.a.",-{col}24/{col}22)', 31: f"={col}26/{col}9"})


def _asset_schedules(book):
    _rows(book, PPE, "P", {
        13: "=O13+P20", 14: "=O14-P21", 16: "=SUM(P13:P14)",
        20: f"='Income statement'!G9*{INPUT}$B$58-O20", 21: f"=O21*{INPUT}$B$13/{INPUT}$B$16",
        29: "=O13", 34: "=O34-P35", 35: f"={INPUT}G49-O35", 39: "=O39-P40",
        40: f"=O40*{INPUT}$B$13/{INPUT}$B$16",
    })
    _rows(book, PPE, "G", {
        **{row: f"=P{row}" for row in (13, 14, 16, 34, 39)},
        **{row: f"=O{row}+P{row}" for row in (20, 21, 35, 40)},
        24: f"={INPUT}$B$58", 29: "=F13",
    })
    for prev, col in zip("GHIJK", FUTURE, strict=True):
        _rows(book, PPE, col, {
            13: f"={col}29+{col}20", 14: f"={prev}14-{col}21", 16: f"=SUM({col}13:{col}14)",
            20: f"='Income statement'!{col}9*{col}24", 21: f"=AVERAGE({col}29,{col}13)*{col}25",
            24: f"={INPUT}{col}47", 25: f"={INPUT}$B$26", 29: f"={prev}13",
            34: f"={prev}34-{col}35", 35: f"=MIN({prev}34,{INPUT}{col}49)",
            39: f"={prev}39-{col}40", 40: f"=MIN({prev}39,{INPUT}$B$27)",
        })
    _nwc_links(book, "P", "O")
    for row in (110, 116, 117, 118, 119, 120, 122, 123, 124, 125):
        formula = "='Income statement'!G9" if row == 110 else f"=P{row}"
        _rows(book, NWC, "G", {row: formula})
    for prev, col in zip("GHIJK", FUTURE, strict=True):
        _nwc_links(book, col, prev)


def _debt_schedule(book, data):
    last_note_row = 59 + len(data.statements["base"].evidence.notes)
    maturities = f"{INPUT}$E$60:$E${last_note_row}"
    principals = f"{INPUT}$F$60:$F${last_note_row}"
    coupons = f"{INPUT}$H$60:$H${last_note_row}"
    maturity_coupons = f"{INPUT}$I$60:$I${last_note_row}"
    _rows(book, DEBT, "P", {
        75: "=O81", 79: "=0", 80: "=0", 81: "=P75+P79-P80", 82: "=O82", 83: "=0",
        86: "=O86-P87", 87: f"={INPUT}$B$19*{INPUT}$B$13/({INPUT}$B$16+{INPUT}$B$13)",
        90: "=SUM(P81:P82)", 93: f'=SUMIF({maturities},">"&P5,{coupons})',
        95: "=-'Income statement'!P20",
    })
    _rows(book, DEBT, "G", {**{row: f"=P{row}" for row in (81, 82, 86, 90, 93)}, 95: "=-'Income statement'!G20"})
    for prev, col in zip("GHIJK", FUTURE, strict=True):
        in_year = f'{maturities},">"&{prev}5,{maturities},"<="&{col}5'
        _rows(book, DEBT, col, {
            75: f"={prev}81", 79: "=0",
            80: f"=SUMIFS({principals},{in_year})",
            81: f"={col}75+{col}79-{col}80", 82: f"={prev}82+{col}83",
            83: f"=MIN(-{prev}82,{INPUT}$B$30)",
            86: f"={prev}86-{col}87", 87: f"={col}88-{col}97",
            88: f"=MIN({prev}86+{col}97,{INPUT}{col}50)", 90: f"=SUM({col}81:{col}82)",
            93: f'=SUMIF({maturities},">"&{col}5,{coupons})',
            94: f"={col}93+SUMIFS({maturity_coupons},{in_year})",
            95: f"={col}94+{INPUT}$B$24*{INPUT}$B$25+{col}97+{col}83-{col}96",
            96: f"='Balance sheet'!{prev}8*{INPUT}$B$22", 97: f"={prev}86*{INPUT}$B$23",
        })


def _equity_schedule(book):
    _rows(book, EQUITY, "P", {
        8: "=O13", 9: "='Income statement'!P26", 10: "=-P25*P21", 13: "=SUM(P8:P10)",
        17: "=O22", 18: "=-P20*P28/1000000", 20: f"={INPUT}$B$56",
        21: "=O21+(P27-P28)/1000000", 22: "=P17+P18", 25: f"={INPUT}$B$34",
        27: f"={_round_count(f'{INPUT}$B$54*{INPUT}$B$13')}",
        28: f"={_round_count(f'{INPUT}$B$55*{INPUT}$B$13')}",
        29: "=O29-P28",
        30: f"=IF(AND({INPUT}$B$36>{INPUT}$B$7,{INPUT}$B$36<={INPUT}$B$12),O31,0)",
        31: "=O31-P30-P10", 32: "=O32+P33+P34",
        33: f"=({INPUT}$B$41-{INPUT}$B$42)*{INPUT}$B$13/{INPUT}$B$17+'Debt'!P87",
        34: f"=O34*{INPUT}$B$13/{INPUT}$B$16", 41: "=O41", 52: "=P13+P22+P32+P41",
    })
    _rows(book, EQUITY, "G", {
        **{row: f"=P{row}" for row in (13, 21, 22, 25, 29, 31, 32, 41, 52)},
        8: "=F13", 9: "='Income statement'!G26", 17: "=F22", 34: "=O34+P34",
    })
    for prev, col in zip("GHIJK", FUTURE, strict=True):
        offset = f"(YEAR({col}5)-YEAR(G5))"
        _rows(book, EQUITY, col, {
            8: f"={prev}13", 9: f"='Income statement'!{col}26", 10: f"={prev}31-{col}30-{col}31", 13: f"=SUM({col}8:{col}10)",
            17: f"={prev}22", 18: f"=-{col}20*{col}28/1000000",
            20: f"={INPUT}$B$56*(1+{INPUT}$B$33)^{offset}",
            21: f"={prev}21+({col}27-{col}28)/1000000", 22: f"={col}17+{col}18",
            25: f"={INPUT}$B$34*(1+{INPUT}$B$32)^{offset}",
            27: f"={_round_count(f'{INPUT}$B$54*({col}5-{prev}5)')}",
            28: f"=MIN(MAX(0,{col}27),{prev}29)", 29: f"={prev}29-{col}28",
            30: f"={prev}31+3*{col}25*AVERAGE({prev}21,{col}21)", 31: f"={col}25*{col}21",
            32: f"={prev}32+{col}34", 33: "=0", 34: f"='Income statement'!{col}9*O34/'Income statement'!O9",
            41: f"={prev}41", 52: f"={col}13+{col}22+{col}32+{col}41",
        })


def _balance_formulas(book):
    for col in "PG" + FUTURE:
        prev = "O" if col == "P" else chr(ord(col) - 1)
        rows = {
            8: f"='Cash flow'!{col}56", 9: f"='NWC'!{col}116", 10: f"='NWC'!{col}119",
            11: f"='NWC'!{col}117", 12: f"='NWC'!{col}124", 16: f"='PP&E'!{col}16",
            17: f"='PP&E'!{col}34", 18: f"={prev}18", 19: f"={prev}19",
            21: f"='PP&E'!{col}39", 27: f"='NWC'!{col}118", 28: f"='NWC'!{col}123",
            29: f"='NWC'!{col}125", 31: f"='Equity'!{col}31", 33: f"='Debt'!{col}90",
            34: f"={prev}34", 38: f"='Debt'!{col}86", 41: f"='Equity'!{col}32",
            42: f"='Equity'!{col}13", 43: f"='Equity'!{col}22", 44: f"='Equity'!{col}41",
        }
        if col == "P":
            rows[19] = "=O19-'Cash flow'!P33"
        elif col == "G":
            rows.update({row: f"=P{row}" for row in (18, 19, 34)})
        _rows(book, BALANCE, col, rows)
    for col in "OPG" + FUTURE:
        _rows(book, BALANCE, col, {
            13: f"=SUM({col}8:{col}12)", 20: f"=SUM({col}16:{col}19,{col}21)", 22: f"={col}13+{col}20",
            30: f"=SUM({col}26:{col}29,{col}31)", 35: f"=SUM({col}33:{col}34,{col}38)",
            37: f"={col}30+{col}35", 45: f"=SUM({col}40:{col}44)", 47: f"={col}37+{col}45",
            49: f"=ROUND({col}22-{col}47,8)",
        })


def _cash_formulas(book, data):
    for col in "P" + FUTURE:
        prev = "O" if col == "P" else chr(ord(col) - 1)
        _rows(book, CASH, col, {
            3: f"='Income statement'!{col}26", 4: f"='PP&E'!{col}21",
            5: f"='PP&E'!{col}35", 6: f"='PP&E'!{col}40", 7: f"='Equity'!{col}34",
            8: f"='Debt'!{col}83", 9: f"=-'NWC'!{col}121", 12: f"=SUM({col}3:{col}9)",
            16: f"=-'PP&E'!{col}20", 17: f"={col}16", 26: f"='Debt'!{col}79", 27: f"=-'Debt'!{col}80",
            28: f"=-'Debt'!{col}87", 29: f"=-'Equity'!{col}30", 30: f"='Equity'!{col}18", 31: f"='Equity'!{col}33",
            32: f"=SUM({col}26:{col}31)",
            33: f"={INPUT}$B$42*{INPUT}$B$13/{INPUT}$B$17" if col == "P" else "=0",
            48: f"=SUM({col}12,{col}16,{col}26:{col}28)",
            53: f"=SUM({col}12,{col}17,{col}32:{col}33)", 55: f"={prev}56", 56: f"={col}55+{col}53",
        })
    _rows(book, CASH, "P", {
        60: f"=P48-{INPUT}$B$49+{INPUT}$B$50+{INPUT}$B$19*{INPUT}$B$14/({INPUT}$B$16+{INPUT}$B$13)",
        61: f"=P7*{INPUT}$B$15/{INPUT}$B$13",
    })
    annual_rows = {
        **{row: f"=O{row}+P{row}" for row in (3, 4, 5, 6, 7, 12, 16)},
        9: "=P9", 10: "=O10", 55: "=O55", 56: "=G55+G53",
    }
    if data.statements["base"].evidence.ytd_cash_flow is None:
        annual_rows.update({35: "=O35+P32+P33", 53: "=SUM(G12,G16,G35)"})
    else:
        annual_rows.update({
            **{row: f"=O{row}+P{row}" for row in (26, 27, 29, 30, 31, 33)},
            15: "=O15", 17: "=SUM(G15:G16)", 28: "=P28", 32: "=SUM(G26:G31)", 53: "=SUM(G12,G17,G32:G33)",
        })
    _rows(book, CASH, "G", annual_rows)
    for col in HISTORICAL + "G" + FUTURE + "OP":
        _rows(book, CASH, col, {47: f"={col}12+{col}16"})


def _expected(book, data):
    for case, report in data.statements.items():
        def check(sheet, col, values):
            for row, expected in values.items():
                book.expect(sheet, f"{col}{row}", expected, case=case)

        book.expect("Statement inputs", "B8", report.cutoff_cash_millions, case=case)
        book.expect("Statement inputs", "B9", report.cutoff_debt_millions, case=case)
        book.expect("Statement inputs", "B10", report.cutoff_shares / 1_000_000, case=case)
        book.expect("Statement inputs", "B11", report.cutoff_dividend_payable_millions, case=case)
        book.expect(CASH, "P60", report.remaining_fcfe.fcfe_millions, case=case)
        book.expect(CASH, "P61", report.q4_2026.cash_flow.operating["stock_compensation"]
                    * data.cash_forecast.days_after_cutoff / data.cash_forecast.full_quarter_days, case=case)
        opening = report.october_opening_balance
        check(BALANCE, "O", {8: opening.assets["cash"], 22: opening.total_assets, 37: opening.total_liabilities,
                             45: opening.total_equity, 49: 0})
        check(INCOME, "O", {22: report.evidence.ytd["pretax"], 26: data.interim.ytd["net_earnings"]})
        filed_cash = report.evidence.ytd_cash_flow
        if filed_cash is not None:
            check(CASH, "O", {row: filed_cash[key] for row, key in YTD_CASH_ROWS.items()})
            q4_flow = report.q4_2026.cash_flow
            check(CASH, "G", {
                12: filed_cash["cash_from_operations"] + q4_flow.cash_from_operations,
                15: filed_cash["other_investing"],
                16: filed_cash["cash_capex"] + q4_flow.investing["cash_capex"],
                17: filed_cash["cash_from_investing"] + q4_flow.cash_from_investing,
                **{row: filed_cash[YTD_CASH_ROWS[row]] + q4_flow.financing[YTD_CASH_ROWS[row]] for row in (26, 27, 29, 30)},
                28: q4_flow.financing["lease_principal"],
                31: filed_cash["other_financing"] + q4_flow.financing["other_net_equity_cash"],
                32: filed_cash["cash_from_financing"] + q4_flow.cash_from_financing,
                33: filed_cash["cash_from_discontinued"] + q4_flow.other_cash_movements,
                53: filed_cash["cash_change"] + q4_flow.net_change_in_cash,
                55: filed_cash["cash_beginning"],
                56: report.q4_2026.balance_sheet.assets["cash"],
            })
        income_rows = {
            9: "revenue", 15: "operating_expenses", 17: "operating_earnings", 19: "other_income",
            20: "net_interest", 22: "pretax", 24: "tax_provision", 26: "net_earnings",
        }
        check(INCOME, "G", {row: report.fy2026_income[key] for row, key in income_rows.items()})
        for col, period in zip("P" + FUTURE, (report.q4_2026, *report.annual), strict=True):
            cf, bs, schedules = period.cash_flow, period.balance_sheet, period.schedules
            check(INCOME, col, {row: period.income[key] for row, key in income_rows.items()})
            check(BALANCE, col, {8: bs.assets["cash"], 16: bs.assets["ppe_gross"] + bs.assets["accumulated_depreciation"],
                                 22: bs.total_assets, 31: bs.liabilities["dividends_payable"],
                                 37: bs.total_liabilities, 45: bs.total_equity, 47: bs.total_equity + bs.total_liabilities, 49: 0})
            check(CASH, col, {
                7: cf.operating["stock_compensation"], 12: cf.cash_from_operations, 16: cf.investing["cash_capex"],
                17: cf.cash_from_investing, 26: cf.financing["note_draws"], 27: cf.financing["note_repayments"],
                28: cf.financing["lease_principal"], 29: cf.financing["dividends_paid"], 30: cf.financing["share_repurchases"],
                32: cf.cash_from_financing, 33: cf.other_cash_movements, 48: cf.fcfe_millions,
                53: cf.net_change_in_cash, 56: bs.assets["cash"],
            })
            check(PPE, col, {13: bs.assets["ppe_gross"], 14: bs.assets["accumulated_depreciation"],
                              34: bs.assets["intangibles"], 39: bs.assets["finance_lease_rou"]})
            check(DEBT, col, {81: bs.liabilities["debt_principal"], 82: bs.liabilities["debt_costs"],
                               86: bs.liabilities["finance_lease_liability"],
                               90: bs.liabilities["debt_principal"] + bs.liabilities["debt_costs"]})
            check(NWC, col, {120: bs.net_operating_working_capital,
                              121: -cf.operating["change_in_operating_working_capital"]})
            check(EQUITY, col, {21: bs.shares_outstanding / 1_000_000, 27: schedules["net_non_program_shares"],
                                 28: schedules["program_shares_purchased"], 29: schedules["remaining_repurchase_authorization"],
                                 52: bs.total_equity})
            if col != "P":
                check(DEBT, col, {94: schedules["note_cash_coupons"], 95: -period.income["net_interest"],
                                   96: schedules["interest_income"], 97: schedules["lease_interest"]})
        q4 = report.q4_2026.balance_sheet
        check(BALANCE, "G", {8: q4.assets["cash"], 22: q4.total_assets, 45: q4.total_equity,
                             47: q4.total_liabilities + q4.total_equity, 49: 0})
        check(EQUITY, "G", {21: q4.shares_outstanding / 1_000_000})


def build_statements(book, data):
    """Write one set of case-driven, linked financial statements."""
    _layout(book, data)
    _inputs(book, data)
    _historical(book, data)
    _reported(book, data)
    _income_formulas(book)
    _asset_schedules(book)
    _debt_schedule(book, data)
    _equity_schedule(book)
    _balance_formulas(book)
    _cash_formulas(book, data)
    _expected(book, data)

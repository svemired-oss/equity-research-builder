"""FCFE at the cost of equity: the workbook's primary valuation, cost of equity and live scenarios."""

from .statements import BALANCE, CASH, INCOME

PCT = "0.00%"
PRICE = '"$"0.00'
COE = "Cost of Equity"
SCENARIOS = "Scenarios"
# DCF sheet rows for the primary FCFE block.
KE, G, CASH_ROW, INVEST, AFFILIATE, NCI, PREF, SHARES = 101, 102, 103, 104, 105, 106, 107, 108
YEARS, FLOW, PV = 111, 112, 113
T_NOPAT, T_INTEREST, T_OTHER, T_BORROW, T_FCFE, T_VALUE, T_PV, EQUITY, VALUE = range(115, 124)
# Displayed checks below the FCFE value: the ROIC-based terminal and the final-year capex flag.
CHECK_FCFE, CHECK_VALUE, CAPEX_FLAG = 124, 125, 126
CASE_ROWS = {"Bear": -1, "Base": 0, "Bull": 1}
TAX = "Assumptions!$B$4"
# docs/method-notes.md: interest and equity-method income are in FCFE, so cash and investments are not added.
BRIDGE = f"-DCF!$B${NCI}-DCF!$B${PREF}"


def case_block_start(revenue_total):
    return 2 * revenue_total + 16


def case_revenue(book, model, revenue_total):
    """Add the selected case's growth shift to every revenue line's annual growth rate."""
    tab = "Revenue Build"
    start = case_block_start(revenue_total)
    lines = len(model["lines"])
    book.put(tab, f"A{start}", "Selected case revenue | case growth shift added to every line's annual growth", "section")
    for col in "BCDEF":
        book.put(tab, f"{col}{start}", f"={col}3", "header")
    for k in range(lines):
        row, base = start + 1 + k, 4 + k
        book.put(tab, f"A{row}", f"=A{base}")
        book.put(tab, f"B{row}", f"=B{base}+$H{base}*Assumptions!$B$34")
        for col, prev in zip("CDEF", "BCDE", strict=True):
            book.put(tab, f"{col}{row}", f"={prev}{row}*({col}{base}/{prev}{base}+Assumptions!$B$34)")
    total = start + 1 + lines
    book.put(tab, f"A{total}", "Selected case total revenue", "total")
    for i, col in enumerate("BCDEF"):
        book.put(tab, f"{col}{total}", f"=SUM({col}{start+1}:{col}{total-1})", "total")
        book.expected.setdefault(tab, {})[f"{col}{total}"] = model["revenue"][i]
    return total


def assumptions(book, model):
    annual = model["sources"]["Annual financials and revenue lines"]
    tab = "Assumptions"
    book.put(tab, "A33", "Other nonoperating and equity-method income, USD millions")
    book.input(tab, "B33", model["other_income"], annual,
               "Three-year median of filed OtherNonoperatingIncomeExpense plus IncomeLossFromEquityMethodInvestments")
    book.put(tab, "A34", "Case revenue growth shift")
    book.put(tab, "B34", '=IF(Cover!B5="Bear",-B35,IF(Cover!B5="Bull",B35,0))', fmt=PCT)
    book.put(tab, "A35", "Scenario revenue growth step")
    book.input(tab, "B35", model["growth_step"], basis="Analyst assumption | bull and bear add or subtract this to every line's annual growth", fmt=PCT)


def cash_flow(book):
    book.put(CASH, "A30", "Full-year FCFE")
    book.put(CASH, "A31", "Post-cutoff FCFE")
    for i, col in enumerate("BCDEF"):
        book.put(CASH, f"{col}30", f"={col}7-{col}8+{col}18-{col}19")
        if i == 0:
            # Debt moved before the cutoff is already in the bridge cash, so only later borrowing counts.
            book.put(CASH, f"{col}31", f"=({col}7-{col}8)*Assumptions!$B$12+{col}18-{col}19-Schedules!$B$40")
        else:
            book.put(CASH, f"{col}31", f"={col}30")
    book.put(CASH, "A32", "FCFE is cash from operations less capex plus net borrowing from the debt schedule.")


def cost_of_equity(book, model):
    """CAPM with the historical ERP; five-year monthly historical and relevered peer-mean comparable betas, 50/50."""
    from ..fcfe_valuation import HISTORICAL_WEIGHT, MIN_BETA_PEERS, REGRESSION_MIN_MONTHS
    tab = COE
    evidence = model["beta"]
    regression, comparable = evidence["regression"], evidence["comparable"]
    book.cells[tab] = {}
    book.put(tab, "A1", "Cost of equity | CAPM", "title")
    book.put(tab, "A2", "Cost of equity = risk-free rate + weighted beta × historical equity risk premium")
    for row, label in ((3, "Risk-free rate"), (4, "Equity risk premium (Damodaran historical)"),
                       (5, "Implied equity risk premium (Damodaran, reference only)"),
                       (6, "Historical beta | five-year monthly regression against the S&P 500"),
                       (7, "Raw regression beta (OLS slope, no adjustment)"), (8, "Matched monthly returns"),
                       (9, "First month-end in the regression"), (10, "Minimum monthly returns"),
                       (11, "Historical beta"), (12, "Cost of equity at the historical beta"), (14, "Forward beta"),
                       (16, "Comparable beta | each peer unlevered at its own D/E and tax rate"),
                       (24, "Mean unlevered peer beta"), (25, "Subject funded debt, USD millions"),
                       (26, "Subject market equity, USD millions"), (27, "Subject tax rate"),
                       (28, "Subject debt / equity"), (29, "Relevered comparable beta"),
                       (30, "Cost of equity at the comparable beta"),
                       (31, "Weighted beta"), (32, "Beta weighting basis"), (33, "Cost of equity"),
                       (34, "Weight on the historical beta"), (35, "Weight on the comparable beta")):
        book.put(tab, f"A{row}", label, "section" if row in (6, 16) else "text")
    book.put(tab, "B3", "=WACC!B3", fmt=PCT)
    book.put(tab, "B4", "=WACC!B4", fmt=PCT)
    implied = evidence.get("implied_erp") or {}
    if implied.get("value") is not None:
        book.input(tab, "B5", implied["value"], implied["url"], f"{implied['date']} | {implied['basis']} | reference only", PCT)
    else:
        book.put(tab, "B5", "Unavailable")
        book.put(tab, "C5", implied.get("unavailable", "No dated implied ERP was collected."), "note")
    if regression.get("raw") is not None:
        book.input(tab, "B7", regression["raw"], regression["url"],
                   f"{regression['start']} to {regression['date']} | Monthly regression vs "
                   f"{regression.get('index_basis', regression.get('index_url'))}", "0.000")
        book.put(tab, "B9", regression["start"], "evidence")
    else:
        book.put(tab, "B7", "Unavailable")
        book.put(tab, "C7", regression.get("unavailable", "Subject regression unavailable."), "note")
    book.input(tab, "B8", regression.get("months") or 0, regression.get("index_url") or regression.get("url"),
               "Matched completed monthly returns")
    book.input(tab, "B10", REGRESSION_MIN_MONTHS, basis="Analyst rule | the historical beta needs 36 monthly returns")
    book.put(tab, "B11", '=IF(AND(ISNUMBER(B7),B8>=B10),B7,"Unavailable")', fmt="0.000")
    book.put(tab, "B12", '=IF(ISNUMBER(B11),B3+B11*B4,"Unavailable")', fmt=PCT)
    book.put(tab, "B14", evidence["forward"])
    book.put(tab, "C14", "Forward beta needs consensus estimates; no consensus source is connected.", "note")
    for col, label in zip("ABCDEFGH", ("Peer", "SIC tier", "Monthly beta", "Funded debt", "Market equity",
                                      "Debt / equity", "Tax rate", "Unlevered beta"), strict=True):
        book.put(tab, f"{col}17", label, "header")
    tiers = {row["ticker"]: row["tier"] for row in (model.get("peer_selection") or {}).get("peers", ())}
    for row, item in enumerate(comparable["rows"][:5], 18):
        book.put(tab, f"A{row}", item["ticker"])
        book.put(tab, f"B{row}", tiers.get(item["ticker"], "SIC tier not recorded"))
        book.input(tab, f"C{row}", item["value"], item["url"],
                   f"{item['start']} to {item['date']} | {item['basis']}, {item['months']} months", "0.000")
        book.input(tab, f"D{row}", item["debt"], item["balance_url"], f"{item['balance_date']} | {item.get('debt_basis', 'Funded debt from the filed balance sheet')}")
        book.input(tab, f"E{row}", item["market_cap"], item["quote_url"],
                   f"{item['shares']:,.1f} million shares × ${item['close']:.2f} close")
        book.put(tab, f"F{row}", f"=D{row}/E{row}", fmt="0.000")
        book.input(tab, f"G{row}", item["tax_rate"], item["tax_url"], f"Median filed effective tax rate, {item['tax_years']} years", PCT)
        book.put(tab, f"H{row}", f"=C{row}/(1+(1-G{row})*F{row})", fmt="0.000")
    book.put(tab, "B24", f'=IF(COUNT(H18:H22)>={MIN_BETA_PEERS},AVERAGE(H18:H22),"Unavailable")', fmt="0.000")
    book.put(tab, "B25", "=DCF!B8")
    book.put(tab, "B26", "=Cover!B6*DCF!B12")
    book.put(tab, "B27", f"={TAX}", fmt=PCT)
    book.put(tab, "B28", "=B25/B26", fmt="0.000")
    book.put(tab, "B29", '=IF(ISNUMBER(B24),B24*(1+(1-B27)*B28),"Unavailable")', fmt="0.000")
    book.put(tab, "B30", '=IF(ISNUMBER(B29),B3+B29*B4,"Unavailable")', fmt=PCT)
    book.input(tab, "B34", HISTORICAL_WEIGHT, basis="docs/method-notes.md rule 2 | forward beta unavailable, so historical and comparable are weighted 50/50", fmt=PCT)
    book.put(tab, "B35", "=1-B34", fmt=PCT)
    book.put(tab, "B31", "=IF(AND(ISNUMBER(B11),ISNUMBER(B29)),B34*B11+B35*B29,IF(ISNUMBER(B11),B11,B29))", "total", "0.000")
    book.put(tab, "B32", '=IF(AND(ISNUMBER(B11),ISNUMBER(B29)),"Weighted historical and comparable",'
                         'IF(ISNUMBER(B11),"Historical only: comparable unavailable","Comparable only: historical unavailable"))')
    # WACC!B5 is this weighted beta unless a Guided override replaces it there.
    book.put(tab, "B33", "=B3+WACC!B5*B4", "total", PCT)
    row = 39
    book.put(tab, "A38", "Peers excluded from the comparable beta", "section")
    for ticker, reason in comparable["excluded"]:
        book.put(tab, f"A{row}", ticker)
        book.put(tab, f"B{row}", reason, "note")
        row += 1
    historical = evidence.get("historical")
    book.expected[tab] = {"B31": model["cost"].levered_beta, "B33": model["cost_of_equity"],
                          "B11": historical if historical is not None else "Unavailable",
                          "B29": comparable["value"] if comparable["value"] is not None else "Unavailable"}
    # WACC (the FCFF cross-check) uses the same weighted beta.
    book.put("WACC", "B5", f"='{COE}'!B31")


def dcf_block(book, model):
    """Primary FCFE valuation below the FCFF cross-check on the DCF sheet."""
    tab = "DCF"
    book.put(tab, "A15", "FCFF at WACC cross-check value per share")
    book.put(tab, "A99", "Primary valuation | FCFE at the cost of equity", "section")
    rows = {KE: ("Cost of equity (CAPM on the Cost of Equity tab)", "=WACC!B3+WACC!B5*WACC!B4", PCT), G: ("Terminal growth (analyst assumption)", "=Assumptions!B9", PCT),
            CASH_ROW: ("Memo | cash, not added: its interest income is in FCFE", "=B6", None),
            INVEST: ("Memo | marketable investments, not added: their income is in FCFE", f"=B7-B{AFFILIATE}", None),
            NCI: ("Noncontrolling interests (book proxy)", "=B9", None),
            PREF: ("Preferred equity (book proxy)", "=B10", None), SHARES: ("Ending common shares (millions)", "=B12", None)}
    for row, (label, formula, fmt) in rows.items():
        book.put(tab, f"A{row}", label)
        book.put(tab, f"B{row}", formula, fmt=fmt)
    book.put(tab, f"A{AFFILIATE}", "Equity-method investments at book, excluded because their income is in FCFE")
    book.input(tab, f"B{AFFILIATE}", model["bridge_affiliate_book_value"],
               model["sources"]["Latest balance sheet"], "Latest filed equity-method investment balance")
    book.put(tab, f"A{YEARS}", "Discount years")
    book.put(tab, f"A{FLOW}", "Post-cutoff FCFE")
    book.put(tab, f"A{PV}", "PV of FCFE at the cost of equity")
    base = model["fcfe"]["Base"]
    for i, col in enumerate("BCDEF"):
        book.put(tab, f"{col}110", f"={col}24", "header", "yyyy-mm-dd")
        book.put(tab, f"{col}{YEARS}", f"={col}20")
        book.put(tab, f"{col}{FLOW}", f"='{CASH}'!{col}31")
        book.put(tab, f"{col}{PV}", f"={col}{FLOW}/(1+$B${KE})^{col}{YEARS}")
        book.expected[tab][f"{col}{FLOW}"] = base.post_cutoff_fcfe[i]
        book.expected.setdefault(CASH, {})[f"{col}31"] = base.post_cutoff_fcfe[i]
    terminal = {
        T_NOPAT: ("Check | terminal NOPAT after reinvestment at the incremental ROIC",
                  f"='{INCOME}'!F5*(1-{TAX})*(1+B{G})*(1-B{G}/Assumptions!B10)"),
        T_INTEREST: ("Check | terminal after-tax interest on closing debt", f"=Schedules!F18*WACC!B6*(1-{TAX})"),
        T_OTHER: ("Check | terminal after-tax other and equity-method income", f"='{INCOME}'!F14*(1+B{G})*(1-{TAX})"),
        T_BORROW: ("Check | terminal net borrowing (debt grows at terminal growth)", f"=B{G}*Schedules!F18"),
        T_FCFE: ("Terminal FCFE | final-year FCFE × (1 + g), Gordon growth", f"=F{FLOW}*(1+B{G})"),
        T_VALUE: ("Terminal equity value | terminal FCFE / (cost of equity − g)", f"=B{T_FCFE}/(B{KE}-B{G})"),
        T_PV: ("PV of terminal equity value", f"=B{T_VALUE}/(1+B{KE})^F{YEARS}"),
        EQUITY: ("Common equity value | PV of FCFE + PV of terminal value − NCI − preferred", f"=SUM(B{PV}:F{PV})+B{T_PV}-B{NCI}-B{PREF}"),
        VALUE: ("FCFE value per share", f"=B{EQUITY}/B{SHARES}"),
        CHECK_FCFE: ("Check | ROIC-based terminal FCFE (not used in the value)",
                     f"=B{T_NOPAT}-B{T_INTEREST}+B{T_OTHER}+B{T_BORROW}"),
        CHECK_VALUE: ("Check | value per share with the ROIC-based terminal value",
                      f"=(SUM(B{PV}:F{PV})+B{CHECK_FCFE}/(B{KE}-B{G})/(1+B{KE})^F{YEARS}"
                      f"-B{NCI}-B{PREF})/B{SHARES}"),
        CAPEX_FLAG: ("Flag | final forecast year capex below D&A (flagged, not changed)",
                     '=IF(Schedules!F5<Schedules!F6,"Flag: capex below D&A","No")'),
    }
    for row, (label, formula) in terminal.items():
        book.put(tab, f"A{row}", label, "total" if row == VALUE else "text")
        book.put(tab, f"B{row}", formula, "total" if row == VALUE else "text",
                 PRICE if row in (VALUE, CHECK_VALUE) else None)
    book.put(tab, f"A{T_NOPAT - 1}", "Terminal value | Gordon growth on final-year FCFE; ROIC-based rows are a check", "section")
    book.expected[tab].update({f"B{T_FCFE}": base.terminal_fcfe, f"B{T_PV}": base.terminal_present_value,
                               f"B{EQUITY}": base.equity_value, f"B{VALUE}": base.value_per_share,
                               f"B{CHECK_FCFE}": base.roic_terminal_fcfe, f"B{CHECK_VALUE}": base.roic_value_per_share,
                               f"B{CAPEX_FLAG}": "Flag: capex below D&A" if model["capex_below_da"] else "No"})
    book.put("Cover", "A7", "DCF fair value | FCFE at cost of equity")
    book.put("Cover", "B7", f"=DCF!B{VALUE}", "total", PRICE)
    book.put("Checks", "A13", "FCFE cash flow difference")
    for col in "BCDEF":
        book.put("Checks", f"{col}13", f"=DCF!{col}{FLOW}-'{CASH}'!{col}31", "check")
        book.expected["Checks"][f"{col}13"] = 0


def scenarios(book, model, revenue_total):
    """Bear, base and bull FCFE values as live formulas that do not depend on the case selector."""
    tab = SCENARIOS
    book.cells[tab] = {}
    book.put(tab, "A1", "Scenario FCFE values | USD per share", "title")
    book.put(tab, "A2", "Bull and bear shift every revenue line's annual growth by the scenario step and the EBIT margin by the case shift.")
    lines = len(model["lines"])
    height = lines + 12
    results = {}
    for index, (case, sign) in enumerate(CASE_ROWS.items()):
        top = 4 + index * height
        book.put(tab, f"A{top}", f"{case} case", "section")
        for col in "BCDEF":
            book.put(tab, f"{col}{top}", f"='Revenue Build'!{col}3", "header")
        book.put(tab, f"A{top+1}", "EBIT margin")
        book.put(tab, f"B{top+1}", f"=Assumptions!$B$3+({sign})*Assumptions!$B$17", fmt=PCT)
        book.put(tab, f"A{top+2}", "Revenue growth shift")
        book.put(tab, f"B{top+2}", f"=({sign})*Assumptions!$B$35", fmt=PCT)
        for k in range(lines):
            row, base = top + 3 + k, 4 + k
            book.put(tab, f"A{row}", f"='Revenue Build'!A{base}")
            book.put(tab, f"B{row}", f"='Revenue Build'!B{base}+'Revenue Build'!$H{base}*$B${top+2}")
            for col, prev in zip("CDEF", "BCDE", strict=True):
                book.put(tab, f"{col}{row}", f"={prev}{row}*('Revenue Build'!{col}{base}/'Revenue Build'!{prev}{base}+$B${top+2})")
        total, flow, pv = top + 3 + lines, top + 4 + lines, top + 5 + lines
        debt, borrow, interest = top + 8 + lines, top + 9 + lines, top + 10 + lines
        book.put(tab, f"A{total}", "Revenue", "total")
        book.put(tab, f"A{flow}", "Post-cutoff FCFE")
        book.put(tab, f"A{pv}", "PV of FCFE")
        book.put(tab, f"A{debt}", "Closing funded debt (debt / EBITDA held)")
        book.put(tab, f"A{borrow}", "Net borrowing")
        book.put(tab, f"A{interest}", "Interest on opening debt")
        for i, col in enumerate("BCDEF"):
            book.put(tab, f"{col}{total}", f"=SUM({col}{top+3}:{col}{total-1})", "total")
            book.put(tab, f"{col}{debt}", f"=Assumptions!$B$45*{col}{total}*($B${top+1}+Assumptions!$B$5)"
                                          f"+{i + 1}*(Assumptions!$B$20-Assumptions!$B$21)")
            prior_debt = "(Schedules!$B$15+Schedules!$B$40)" if i == 0 else f"{'BCDE'[i-1]}{debt}"
            book.put(tab, f"{col}{borrow}", f"={col}{debt}-{prior_debt}")
            book.put(tab, f"{col}{interest}", f"={prior_debt}*WACC!$B$6")
            opening = "Schedules!$L$12" if i == 0 else f"{'BCDE'[i-1]}{total}*Assumptions!$B$32"
            capex = f"Schedules!{col}5/'{INCOME}'!{col}4"
            operating = (f"{col}{total}*($B${top+1}*(1-{TAX})+Assumptions!$B$5-{capex})"
                         f"-({col}{total}*Assumptions!$B$32-{opening})"
                         f"+('{INCOME}'!{col}14-{col}{interest})*(1-{TAX})")
            borrowing = f"{col}{borrow}"
            stub = "*Assumptions!$B$12" if i == 0 else ""
            book.put(tab, f"{col}{flow}", f"=({operating}){stub}+{borrowing}")
            book.put(tab, f"{col}{pv}", f"={col}{flow}/(1+DCF!$B${KE})^DCF!{col}{YEARS}")
        # Gordon growth on the case's final-year FCFE; F{pv} already carries the year-5 discount.
        terminal = f"F{pv}*(1+DCF!$B${G})/(DCF!$B${KE}-DCF!$B${G})"
        book.put(tab, f"A{top+6+lines}", "PV of terminal equity value (Gordon growth on final-year FCFE)")
        book.put(tab, f"B{top+6+lines}", "=" + terminal)
        value = top + 7 + lines
        book.put(tab, f"A{value}", f"{case} FCFE value per share", "total")
        book.put(tab, f"B{value}", f"=(SUM(B{pv}:F{pv})+B{top+6+lines}+{BRIDGE})/DCF!$B${SHARES}", "total", PRICE)
        book.expected.setdefault(tab, {})[f"B{value}"] = model["fcfe"][case].value_per_share
        results[case] = f"{tab}!B{value}"
    for row, case in ((20, "Bear"), (21, "Base"), (22, "Bull")):
        book.put("Cover", f"A{row}", f"{case} case value per share")
        book.put("Cover", f"B{row}", "=" + results[case], fmt=PRICE)
        book.expected.setdefault("Cover", {})[f"B{row}"] = model["fcfe"][case].value_per_share
    book.put("Cover", "A23", "Cost of equity")
    book.put("Cover", "B23", f"=DCF!B{KE}", fmt=PCT)
    book.put("Checks", "A14", "Selected case FCFE difference to its scenario value")
    book.put("Checks", "B14", f'=DCF!B{VALUE}-IF(Cover!B5="Bear",{results["Bear"]},IF(Cover!B5="Bull",{results["Bull"]},{results["Base"]}))', "check")
    book.expected["Checks"]["B14"] = 0
    return results


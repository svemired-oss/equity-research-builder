"""Write editable GD valuation, dated inputs and trading-comparable formulas."""

from typing import TYPE_CHECKING

from .gd_fresh_excel_statements import _round_count

if TYPE_CHECKING:
    from .gd_fresh_excel import FreshWorkbookData, WorkbookPlan


DCF = "DCF"
INPUTS = "Valuation inputs"
COMPS = "Comps"
MONEY = '#,##0.00;(#,##0.00);"–"'
PERCENT = "0.00%"
PRICE = "$0.00"
MULTIPLE = '0.00"x"'
DATE = "mmm d, yyyy"
YEARS = "0.0000"


def _ref(sheet, address):
    return f"'{sheet}'!{address}"


def _headers(book, sheet, row, labels, columns="ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
    for column, label in zip(columns, labels):
        book.put(sheet, f"{column}{row}", label, kind="header")


def _rows(book, sheet, start, rows, *, totals=()):
    for row, (label, formula, fmt) in enumerate(rows, start):
        book.put(sheet, f"A{row}", label)
        book.put(sheet, f"B{row}", f"={formula}", kind="total" if row in totals else None, fmt=fmt)


def _source(book, sheet, row, source, columns="DEF"):
    date_column, url_column, citation_column = columns
    book.put(sheet, f"{date_column}{row}", source.available_on, fmt=DATE)
    book.put(sheet, f"{url_column}{row}", source.url)
    book.put(sheet, f"{citation_column}{row}", source.citation, kind="note")


def _inputs(book, data):
    inputs, market, horizon = data.valuation_inputs, data.market, data.horizon_inputs
    close = market.valuation_close
    policy = data.research.inputs.rating_policy
    book.put(INPUTS, "A1", "Valuation inputs and sources", kind="title")
    _headers(book, INPUTS, 2, ("USD unless indicated", "Value", "Observed", "Available", "Source URL", "Basis"))
    book.put(INPUTS, "A3", "Valuation cutoff")
    book.put(INPUTS, "B3", f"={_ref('Statement inputs', 'B6')}", fmt=DATE)
    book.put(INPUTS, "A4", "Reference close date")
    book.put(INPUTS, "B4", "=C5", fmt=DATE)
    observed = (
        ("Reference close", close.price, close.observed_on, close.source, PRICE),
        ("10-year U.S. Treasury", inputs.risk_free_rate.value, inputs.risk_free_rate.observed_on,
         inputs.risk_free_rate.source, PERCENT),
        ("U.S. implied equity risk premium", inputs.equity_risk_premium.value,
         inputs.equity_risk_premium.observed_on, inputs.equity_risk_premium.source, PERCENT),
        ("Cash-adjusted unlevered industry beta", inputs.unlevered_beta.value, inputs.unlevered_beta.observed_on,
         inputs.unlevered_beta.source, "0.00"),
    )
    for row, (label, value, observation, source, fmt) in enumerate(observed, 5):
        book.put(INPUTS, f"A{row}", label)
        book.put(INPUTS, f"B{row}", value, kind="actual", fmt=fmt)
        book.put(INPUTS, f"C{row}", observation, fmt=DATE)
        _source(book, INPUTS, row, source)

    policies = (
        ("Marginal tax rate for beta", inputs.relevering_tax_rate, "relevering_tax_rate"),
        ("Stable revenue growth", inputs.terminal_growth, "terminal_growth"),
        ("Operating cash / sales", inputs.operating_cash_share, "operating_cash_share"),
        ("Cost of equity grid step", inputs.cost_of_equity_step, None),
        ("Stable growth grid step", inputs.terminal_growth_step, None),
        ("Buy total return threshold", policy.buy_at_or_above, None),
        ("Sell total return threshold", policy.sell_at_or_below, None),
    )
    for row, (label, value, source_key) in enumerate(policies, 9):
        book.put(INPUTS, f"A{row}", label)
        book.put(INPUTS, f"B{row}", value, kind="input", fmt=PERCENT)
        if source_key is not None:
            _source(book, INPUTS, row, inputs.sources[source_key])
    book.put(INPUTS, "F14", policy.rationale, kind="note")
    book.put(INPUTS, "A16", "Terminal normalization")
    _source(book, INPUTS, 16, inputs.sources["normalization"])
    book.put(INPUTS, "A17", "Equity award treatment")
    _source(book, INPUTS, 17, inputs.sources["equity_awards"])

    book.put(INPUTS, "A19", "Reported cash seasonality (USD millions)", kind="section")
    _headers(book, INPUTS, 20, ("Year", "Annual CFO", "Q4 CFO", "Annual capex", "Q4 capex", "Available", "Source URL", "Basis"))
    season_rows = []
    for row, season in enumerate(horizon.seasonality, 21):
        season_rows.append(row)
        book.put(INPUTS, f"A{row}", season.year, kind="actual", fmt="0")
        for column, value in zip("BCDE", (season.annual_cfo, season.q4_cfo, season.annual_capex, season.q4_capex), strict=True):
            book.put(INPUTS, f"{column}{row}", value, kind="actual", fmt=MONEY)
        _source(book, INPUTS, row, season.source, "FGH")

    book.put(INPUTS, "A25", "Contractual note maturities (USD millions)", kind="section")
    _headers(book, INPUTS, 26, ("Date", "Principal", "Available", "Source URL", "Basis"))
    maturity_rows = []
    for row, note in enumerate(horizon.maturities, 27):
        maturity_rows.append(row)
        book.put(INPUTS, f"A{row}", note.date, kind="actual", fmt=DATE)
        book.put(INPUTS, f"B{row}", note.principal_millions, kind="actual", fmt=MONEY)
        _source(book, INPUTS, row, note.source, "CDE")

    book.put(INPUTS, "A32", "Modeled 2027 dividend calendar", kind="section")
    _headers(book, INPUTS, 33, ("Record date", "Payment date", "Available", "Source URL", "Basis"))
    dividend_rows = []
    for row, dividend in enumerate(horizon.dividend_dates, 34):
        dividend_rows.append(row)
        book.put(INPUTS, f"A{row}", dividend.record_on, kind="input", fmt=DATE)
        book.put(INPUTS, f"B{row}", dividend.paid_on, kind="input", fmt=DATE)
    _source(book, INPUTS, 34, horizon.dividend_source, "CDE")
    return season_rows, maturity_rows, dividend_rows


def _spot(book):
    book.put(DCF, "A1", "General Dynamics | FCFE valuation", kind="title")
    book.put(DCF, "A2", "USD millions except per-share figures and ratios", kind="note")
    statements = "Statement inputs"
    _rows(book, DCF, 3, (
        ("Valuation cutoff", _ref(statements, "B6"), DATE),
        ("Reference close date", _ref(INPUTS, "B4"), DATE),
        ("Reference close", _ref(INPUTS, "B5"), PRICE),
        ("Cost of equity", f"{_ref(INPUTS, 'B6')}+B15*{_ref(INPUTS, 'B7')}", PERCENT),
        ("Stable revenue growth", _ref(INPUTS, "B10"), PERCENT),
        ("Operating case", _ref("Cover", "$E$13"), None),
        ("Cutoff common shares (millions)", _ref(statements, "B10"), "#,##0.000"),
        ("Cutoff cash", _ref(statements, "B8"), MONEY),
        ("Cutoff carrying debt", _ref(statements, "B9"), MONEY),
        ("Dividend due to prior holders", _ref(statements, "B11"), MONEY),
        ("Market capitalization", "B5*B9", MONEY),
        ("Debt / market equity", "B11/B13", PERCENT),
        ("Relevered equity beta", f"{_ref(INPUTS, 'B8')}*(1+(1-{_ref(INPUTS, 'B9')})*B14)", "0.00"),
        ("Required operating cash", f"{_ref('Income statement', 'G9')}*{_ref(INPUTS, 'B11')}", MONEY),
        ("Net nonoperating cash", "B10-B12-B16", MONEY),
        ("Present value of forecast FCFE", "SUM(G35:L35)", MONEY),
        ("First-year stable FCFE", "F107", MONEY),
        ("Terminal value", f'IF(AND(B6>B7,B7>=0,B7<{_ref(INPUTS, "B6")},B19>0),B19/(B6-B7),"")', MONEY),
        ("Present value of terminal FCFE", 'IF(ISNUMBER(B20),B20/(1+B6)^L34,"")', MONEY),
        ("Common equity value", 'IF(ISNUMBER(B21),B18+B21+B17,"")', MONEY),
        ("Terminal present value / common equity", 'IF(ISNUMBER(B22),B21/B22,"")', PERCENT),
        ("Spot intrinsic value / share", 'IF(ISNUMBER(B22),B22/B9,"")', PRICE),
        ("Spot premium to reference close", 'IF(ISNUMBER(B24),B24/B5-1,"")', PERCENT),
    ), totals=(6, 19, 22, 24))
    book.put(DCF, "A27", "Spot distributable cash flow", kind="section")
    _headers(book, DCF, 27, ("Remaining 2026", "FY2027", "FY2028", "FY2029", "FY2030", "FY2031"), "GHIJKL")
    for row, label in enumerate(("Cash flow date", "Statement FCFE", "Stock compensation", "After-tax surplus cash interest",
                                 "Operating cash investment", "Valuation FCFE", "ACT/365 discount years",
                                 "Present value", "Closing operating cash reserve"), 28):
        book.put(DCF, f"A{row}", label)
    for previous, col, year in zip("FGHIJK", "GHIJKL", range(2026, 2032), strict=True):
        stub = col == "G"
        formulas = {
            28: (f"DATE(YEAR($B$3)+{year - 2026},12,31)", DATE),
            29: (_ref("Cash flow", "P60" if stub else f"{col}48"), MONEY),
            30: (_ref("Cash flow", "P61" if stub else f"{col}7"), MONEY),
            31: (f"($B$10-$B$16)*{_ref(statements, 'B22')}*G34*"
                 f"IF({_ref('Income statement', 'P22')}>0,{_ref('Income statement', 'P26')}/{_ref('Income statement', 'P22')},"
                 f"1-{_ref(statements, 'H48')})" if stub else
                 f"({_ref('Balance sheet', f'{previous}8')}-{previous}36)*{_ref(statements, 'B22')}*(1-{_ref(statements, f'{col}48')})", MONEY),
            32: ("G36-$B$16" if stub else f"{col}36-{previous}36", MONEY),
            33: (f"{col}29-SUM({col}30:{col}32)", MONEY),
            34: (f"({col}28-$B$3)/365", YEARS),
            35: (f"{col}33/(1+$B$6)^{col}34", MONEY),
            36: (f"{_ref('Income statement', f'{col}9')}*{_ref(INPUTS, '$B$11')}", MONEY),
        }
        for row, (formula, fmt) in formulas.items():
            book.put(DCF, f"{col}{row}", f"={formula}", fmt=fmt)


def _horizon(book, season_rows, maturity_rows, dividend_rows):
    book.put(DCF, "A38", "12-month target", kind="section")
    _rows(book, DCF, 39, (
        ("Target date", "DATE(YEAR(B3)+1,MONTH(B3),DAY(B3))", DATE),
        ("Projected common shares (millions)", f"{_ref('Equity', 'H21')}+(B71-B73)/1000000", "#,##0.000"),
        ("Projected target-date cash", "B74-B65+B70+B72", MONEY),
        ("Target-date operating cash reserve", "H36-B68", MONEY),
        ("Dividends payable to holders of record", "SUM(F87:F90)", MONEY),
        ("Target-date net nonoperating cash", "B41-B42-B43", MONEY),
        ("Target-date common equity value", 'IF(ISNUMBER(B77),B78+B77+B44,"")', MONEY),
        ("12-month target / share", 'IF(ISNUMBER(B45),B45/B40,"")', PRICE),
        ("Modeled price return", 'IF(ISNUMBER(B46),B46/B5-1,"")', PERCENT),
        ("Modeled total return", 'IF(ISNUMBER(B46),(B46+B49+B50)/B5-1,"")', PERCENT),
        ("Paid dividend entitlement / share", "SUM(G87:G90)", PRICE),
        ("Unpaid dividend entitlement / share", "SUM(H87:H90)", PRICE),
    ), totals=(45, 46, 48))
    book.put(DCF, "A53", "Target-date cash and share bridge", kind="section")
    _headers(book, DCF, 54, ("Note maturity", "Principal", "Dated outflow", "ACT/365", "Present value"), "DEFGH")
    note_rows = []
    for row, source in enumerate(maturity_rows, 55):
        note_rows.append(row)
        for column, formula, fmt in (
            ("D", _ref(INPUTS, f"A{source}"), DATE), ("E", _ref(INPUTS, f"B{source}"), MONEY),
            ("F", f"IF(D{row}>$B$39,-E{row},0)", MONEY),
            ("G", f"MAX(0,(D{row}-$B$39)/365)", YEARS),
            ("H", f"F{row}/(1+$B$6)^G{row}", MONEY),
        ):
            book.put(DCF, f"{column}{row}", f"={formula}", fmt=fmt)
    _headers(book, DCF, 59, ("Historical FY", "Q4 / FY CFO", "Q4 / FY capex"), "DEF")
    for row, source in enumerate(season_rows, 60):
        book.put(DCF, f"D{row}", f"={_ref(INPUTS, f'A{source}')}", fmt="0")
        book.put(DCF, f"E{row}", f"={_ref(INPUTS, f'C{source}')}/{_ref(INPUTS, f'B{source}')}", fmt=PERCENT)
        book.put(DCF, f"F{row}", f"={_ref(INPUTS, f'E{source}')}/{_ref(INPUTS, f'D{source}')}", fmt=PERCENT)
    _rows(book, DCF, 55, (
        ("Fiscal year-end", "H28", DATE),
        ("Days remaining", "B55-B39", "0"),
        ("Share of annual calendar days", "B56/(B55-G28)", PERCENT),
        ("Share of Q4 calendar days", "B56/(B55-DATE(YEAR(B55),9,30))", PERCENT),
        ("Share of annual CFO after target", f"MEDIAN(E60:E{59+len(season_rows)})*B58", PERCENT),
        ("Share of annual capex after target", f"MEDIAN(F60:F{59+len(season_rows)})*B58", PERCENT),
        ("Remaining operating cash", f"{_ref('Cash flow', 'H12')}*B59", MONEY),
        ("Remaining cash capex", f"-{_ref('Cash flow', 'H16')}*B60", MONEY),
        ("Remaining note principal", f"-SUM(F55:F{note_rows[-1]})" if note_rows else "0", MONEY),
        ("Remaining finance lease principal", f"-{_ref('Cash flow', 'H28')}*B57", MONEY),
        ("Remaining statement FCFE", "B61-SUM(B62:B64)", MONEY),
        ("Remaining stock compensation", "H30*B57", MONEY),
        ("Remaining after-tax surplus cash interest", "H31*B57", MONEY),
        ("Remaining operating cash investment", "H32*B57", MONEY),
        ("Remaining valuation FCFE", "B65-SUM(B66:B68)", MONEY),
        ("Remaining dividends paid", "SUM(E87:E90)", MONEY),
        ("Remaining buyback shares", _round_count(f"{_ref('Equity', 'H28')}*B57"), "#,##0"),
        ("Remaining buyback cash", f"B71*{_ref('Equity', 'H20')}/1000000", MONEY),
        ("Remaining net non-program shares", _round_count(f"{_ref('Equity', 'H27')}*B57"), "#,##0"),
        ("Projected fiscal year-end cash", _ref("Balance sheet", "H8"), MONEY),
    ))
    _rows(book, DCF, 77, (
        ("Target-date PV of terminal cash flow", 'IF(ISNUMBER(B20),B20/(1+B6)^L81,"")', MONEY),
        ("Target-date PV of forecast FCFE", f"SUM(H82:L82,H55:H{note_rows[-1]})" if note_rows else "SUM(H82:L82)", MONEY),
    ))
    _headers(book, DCF, 78, ("Remaining FY2027", "FY2028", "FY2029", "FY2030", "FY2031"), "HIJKL")
    for row, label in enumerate(("Cash flow date", "Valuation FCFE excluding dated note payment",
                                 "ACT/365 discount years", "Present value"), 79):
        book.put(DCF, f"A{row}", label)
    for col in "HIJKL":
        book.put(DCF, f"{col}79", f"={col}28", fmt=DATE)
        book.put(DCF, f"{col}80", "=$B$69+$B$63" if col == "H" else f"={col}33", fmt=MONEY)
        book.put(DCF, f"{col}81", f"=({col}79-$B$39)/365", fmt=YEARS)
        book.put(DCF, f"{col}82", f"={col}80/(1+$B$6)^{col}81", fmt=MONEY)

    book.put(DCF, "A85", "Modeled dividend entitlement", kind="section")
    _headers(book, DCF, 86, ("Record date", "Payment date", "Dividend / share", "Total dividend", "After target",
                             "Payable at target", "Paid / share", "Unpaid / share"))
    for row, source in enumerate(dividend_rows, 87):
        for column, formula, fmt in (
            ("A", _ref(INPUTS, f"A{source}"), DATE), ("B", _ref(INPUTS, f"B{source}"), DATE),
            ("C", f"D{row}/{_ref('Equity', 'G21')}" if row == 87 else _ref("Equity", "H25"), PRICE),
            ("D", _ref("Balance sheet", "G31") if row == 87 else
             f"C{row}*AVERAGE({_ref('Equity', 'G21')},{_ref('Equity', 'H21')})", MONEY),
            ("E", f"IF(B{row}>$B$39,D{row},0)", MONEY),
            ("F", f"IF(AND(A{row}<=$B$39,$B$39<B{row}),D{row},0)", MONEY),
            ("G", f"IF(AND($B$3<A{row},A{row}<=$B$39,B{row}<=$B$39),C{row},0)", PRICE),
            ("H", f"IF(AND($B$3<A{row},A{row}<=$B$39,$B$39<B{row}),C{row},0)", PRICE),
        ):
            book.put(DCF, f"{column}{row}", f"={formula}", fmt=fmt)
    return note_rows


def _terminal_and_sensitivity(book, note_rows):
    statements = "Statement inputs"
    book.put(DCF, "A93", "Stable-year reinvestment by growth rate", kind="section")
    book.put(DCF, "A94", "Stable year ending")
    book.put(DCF, "B94", "=DATE(YEAR(L28)+1,MONTH(L28),DAY(L28))", fmt=DATE)
    for row, label in enumerate(("Stable sales growth", "Sales", "Operating earnings", "Normalized note and other debt interest",
                                 "Finance lease interest", "Operating cash interest income", "Pretax earnings", "Cash taxes",
                                 "Net PP&E investment", "Core working capital investment", "Operating cash investment",
                                 "Net borrowing", "First-year stable FCFE"), 95):
        book.put(DCF, f"A{row}", label)
    for col, offset in zip("CDEFGHI", range(-3, 4), strict=True):
        terminal = {
            95: f"$B$7+({offset})*{_ref(INPUTS, '$B$13')}",
            96: f"{_ref('Income statement', '$L$9')}*(1+{col}95)",
            97: f"{_ref('Income statement', '$L$17')}*(1+{col}95)",
            98: f"({_ref('Debt', '$L$93')}+{_ref(statements, '$B$24')}*{_ref(statements, '$B$25')})*(1+{col}95/2)",
            99: f"{_ref('Debt', '$L$86')}*{_ref(statements, '$B$23')}",
            100: f"$L$36*{_ref(statements, '$B$22')}",
            101: f"{col}97-{col}98-{col}99+{col}100",
            102: f"{col}101*{_ref(statements, '$L$48')}",
            103: f"{_ref('PP&E', '$L$16')}*{col}95",
            104: f"{_ref('NWC', '$L$122')}*{col}95",
            105: f"$L$36*{col}95",
            106: f"{_ref('Debt', '$L$81')}*{col}95",
            107: f"{col}101-SUM({col}102:{col}105)+{col}106",
        }
        for row, formula in terminal.items():
            book.put(DCF, f"{col}{row}", f"={formula}", fmt=PERCENT if row == 95 else MONEY)

    for heading, title, cash_columns, flow_row, years_row, net_cash, shares in (
        (111, "Spot sensitivity | value per share", "GHIJKL", 33, 34, "$B$17", "$B$9"),
        (122, "12-month sensitivity | target per share", "HIJKL", 80, 81, "$B$44", "$B$40"),
    ):
        book.put(DCF, f"A{heading-1}", title, kind="section")
        book.put(DCF, f"B{heading}", "Ke / growth", kind="header")
        book.put(DCF, f"J{heading}", "PV of forecast FCFE", kind="header")
        for col in "CDEFGHI":
            book.put(DCF, f"{col}{heading}", f"={col}95", kind="header", fmt=PERCENT)
        for row, offset in enumerate(range(-3, 4), heading + 1):
            rate = f"$B{row}"
            book.put(DCF, f"B{row}", f"=$B$6+({offset})*{_ref(INPUTS, '$B$12')}", kind="header", fmt=PERCENT)
            terms = [f"${col}${flow_row}/(1+{rate})^${col}${years_row}" for col in cash_columns]
            if heading == 122:
                terms.extend(f"$F${note}/(1+{rate})^$G${note}" for note in note_rows)
            book.put(DCF, f"J{row}", f'=IF({rate}>0,SUM({",".join(terms)}),"")', fmt=MONEY)
            for col in "CDEFGHI":
                growth, stable = f"{col}${heading}", f"{col}$107"
                valid = f"AND({growth}>=0,{growth}<{_ref(INPUTS, '$B$6')},{growth}<{rate},{stable}>0)"
                terminal_pv = f"{stable}/({rate}-{growth})/(1+{rate})^$L${years_row}"
                book.put(DCF, f"{col}{row}", f'=IF({valid},($J{row}+{terminal_pv}+{net_cash})/{shares},"")', fmt=PRICE)


def _comparables(book, comps):
    book.put(COMPS, "A1", "Trailing trading comparables", kind="title")
    book.put(COMPS, "A2", "USD millions except prices and whole common shares. Medians exclude GD.", kind="note")
    companies = (comps.subject, *comps.peers)
    last = 11 + len(companies)
    _rows(book, COMPS, 4, (
        ("Peer median EV / operating earnings", f'IF(COUNT(N13:N{last})>=2,MEDIAN(N13:N{last}),"")', MULTIPLE),
        ("Peer median P / common earnings", f'IF(COUNT(O13:O{last})>=2,MEDIAN(O13:O{last}),"")', MULTIPLE),
        ("EV-based GD implied spot / share", 'IF(AND(ISNUMBER(B4),ISNUMBER(N12),B4*L12-K12+J12>0),(B4*L12-K12+J12)*1000000/D12,"")', PRICE),
        ("P/E-based GD implied spot / share", 'IF(AND(ISNUMBER(B5),ISNUMBER(O12)),B5*M12*1000000/D12,"")', PRICE),
        ("Valid EV / operating earnings peers", f"COUNT(N13:N{last})", "0"),
        ("Valid P / common earnings peers", f"COUNT(O13:O{last})", "0"),
    ), totals=(4, 5, 6, 7))
    _headers(book, COMPS, 11, ("Ticker", "Close", "Quote date", "Cover shares", "Shares date", "Cash", "Carrying debt",
                              "Noncontrolling", "Preferred", "Market equity", "Enterprise value", "LTM EBIT", "LTM common earnings",
                              "EV / EBIT", "P/E"))
    book.put(COMPS, "A19", "Reported earnings bridge", kind="section")
    _headers(book, COMPS, 20, ("Ticker", "FY end", "FY EBIT", "FY common earnings", "Prior 9M end", "Prior 9M EBIT",
                              "Prior 9M earnings", "Current 9M end", "Current 9M EBIT", "Current 9M earnings"))
    book.put(COMPS, "A28", "Dated market and filing sources", kind="section")
    _headers(book, COMPS, 29, ("Ticker", "Selection", "Quote available", "Market URL", "FY filing date", "FY SEC URL",
                              "9M filing date", "9M SEC URL"))
    for index, metrics in enumerate(companies):
        row, reported, cited = 12 + index, 21 + index, 30 + index
        evidence = metrics.evidence
        book.put(COMPS, f"A{row}", evidence.ticker)
        for column, value, fmt in (
            ("B", f"={_ref(INPUTS, 'B5')}" if index == 0 else evidence.close.price, PRICE),
            ("C", f"={_ref(INPUTS, 'B4')}" if index == 0 else evidence.close.observed_on, DATE),
            ("D", evidence.shares, "#,##0"), ("E", evidence.shares_on, DATE), ("F", evidence.cash_millions, MONEY),
            ("G", evidence.carrying_debt_millions, MONEY), ("H", evidence.noncontrolling_interest_millions, MONEY),
            ("I", evidence.preferred_stock_millions, MONEY),
        ):
            book.put(COMPS, f"{column}{row}", value, kind="actual", fmt=fmt)
        for column, formula, expected in (
            ("J", f"B{row}*D{row}/1000000", metrics.market_cap_millions),
            ("K", f"J{row}+SUM(G{row}:I{row})-F{row}", metrics.enterprise_value_millions),
            ("L", f"C{reported}-F{reported}+I{reported}", metrics.ltm_ebit_millions),
            ("M", f"D{reported}-G{reported}+J{reported}", metrics.ltm_common_earnings_millions),
            ("N", f'IF(AND(K{row}>0,L{row}>0),K{row}/L{row},"")', metrics.ev_to_ebit),
            ("O", f'IF(M{row}>0,J{row}/M{row},"")', metrics.price_to_earnings),
        ):
            book.put(COMPS, f"{column}{row}", f"={formula}", fmt=MULTIPLE if column in "NO" else MONEY)
            for case in ("bear", "base", "bull"):
                book.expect(COMPS, f"{column}{row}", expected, case=case)
        book.put(COMPS, f"A{reported}", f"=A{row}")
        for columns, period in zip(("BCD", "EFG", "HIJ"), (evidence.annual, evidence.prior_interim, evidence.interim), strict=True):
            book.put(COMPS, f"{columns[0]}{reported}", period.period_end, kind="actual", fmt=DATE)
            book.put(COMPS, f"{columns[1]}{reported}", period.operating_earnings_millions, kind="actual", fmt=MONEY)
            book.put(COMPS, f"{columns[2]}{reported}", period.common_net_earnings_millions, kind="actual", fmt=MONEY)
        book.put(COMPS, f"A{cited}", f"=A{row}")
        book.put(COMPS, f"B{cited}", evidence.selection_reason, kind="note")
        for date_col, url_col, source in (("C", "D", evidence.close.source), ("E", "F", evidence.annual_source),
                                         ("G", "H", evidence.interim_source)):
            book.put(COMPS, f"{date_col}{cited}", source.available_on, fmt=DATE)
            book.put(COMPS, f"{url_col}{cited}", source.url)
    for row, expected in enumerate((comps.median_ev_to_ebit, comps.median_price_to_earnings,
                                    comps.implied_price_ev_to_ebit, comps.implied_price_price_to_earnings,
                                    comps.peer_count_ev_to_ebit, comps.peer_count_price_to_earnings), 4):
        for case in ("bear", "base", "bull"):
            book.expect(COMPS, f"B{row}", expected, case=case)


def _expect_valuation(book, data, note_rows):
    for case, valuation in data.valuations.items():
        target = data.targets[case]
        bridge = target.bridge
        for row, value in {
            3: valuation.as_of, 4: valuation.close.observed_on, 5: valuation.close.price, 6: valuation.cost_of_equity,
            7: valuation.inputs.terminal_growth, 8: case.title(), 9: valuation.shares_millions, 10: valuation.cutoff_cash_millions,
            11: valuation.cutoff_debt_millions, 12: valuation.dividend_payable_to_prior_holders_millions,
            13: valuation.market_cap_millions, 14: valuation.debt_to_market_equity, 15: valuation.levered_beta,
            16: valuation.opening_operating_cash_millions, 17: valuation.net_nonoperating_cash_millions,
            18: valuation.discount.equity_present_value_millions - valuation.discount.terminal_present_value_millions,
            19: valuation.terminal.fcfe_millions, 20: valuation.discount.terminal_value_millions,
            21: valuation.discount.terminal_present_value_millions, 22: valuation.common_equity_value_millions,
            23: valuation.terminal_share_of_equity_value, 24: valuation.intrinsic_value_per_share,
            25: valuation.premium_to_reference_close, 39: target.horizon,
            40: bridge.projected_horizon_common_shares / 1_000_000, 41: bridge.projected_horizon_cash_millions,
            42: bridge.operating_cash_reserve_millions, 43: bridge.dividends_payable_to_prior_holders_millions,
            44: bridge.net_nonoperating_cash_millions,
            45: target.discount.equity_present_value_millions + bridge.net_nonoperating_cash_millions,
            46: target.price_target, 47: target.price_return, 48: target.total_return,
            49: target.projected_paid_dividends_per_share, 50: target.projected_unpaid_dividend_entitlement_per_share,
            55: bridge.fiscal_year_end, 56: bridge.remaining_days, 59: bridge.remaining_share_of_annual_cfo,
            60: bridge.remaining_share_of_annual_capex, 61: bridge.remaining_cash_from_operations_millions,
            62: bridge.remaining_capex_millions, 63: bridge.remaining_note_principal_millions,
            64: bridge.remaining_lease_principal_millions, 65: bridge.remaining_statement_fcfe_millions,
            66: bridge.remaining_stock_compensation_millions, 67: bridge.remaining_after_tax_cash_interest_adjustment_millions,
            68: bridge.remaining_operating_cash_investment_millions, 69: bridge.remaining_valuation_fcfe_millions,
            70: bridge.remaining_dividends_millions, 72: bridge.remaining_buybacks_millions,
            74: bridge.projected_year_end_cash_millions, 77: target.discount.terminal_present_value_millions,
            78: target.discount.equity_present_value_millions - target.discount.terminal_present_value_millions,
            94: valuation.terminal.period_end,
        }.items():
            book.expect(DCF, f"B{row}", value, case=case)
        for col, adjustment, discount in zip("GHIJKL", valuation.adjustments, valuation.discount.periods, strict=True):
            for row, value in enumerate((adjustment.period_end, adjustment.statement_fcfe_millions, adjustment.stock_compensation_millions,
                                         adjustment.after_tax_cash_interest_adjustment_millions, adjustment.operating_cash_investment_millions,
                                         adjustment.adjusted_fcfe_millions, discount.discount_years, discount.present_value_millions), 28):
                book.expect(DCF, f"{col}{row}", value, case=case)
        by_date = {discount.period_end: discount for discount in target.discount.periods}
        for row, note in zip(note_rows, data.horizon_inputs.maturities, strict=True):
            discount = by_date.get(note.date)
            book.expect(DCF, f"F{row}", discount.fcfe_millions if discount else 0, case=case)
            book.expect(DCF, f"H{row}", discount.present_value_millions if discount else 0, case=case)
        for col, year in zip("HIJKL", range(2027, 2032), strict=True):
            discount = next(discount for day, discount in by_date.items() if day.year == year and day.month == 12 and day.day == 31)
            for row, value in enumerate((discount.period_end, discount.fcfe_millions, discount.discount_years, discount.present_value_millions), 79):
                book.expect(DCF, f"{col}{row}", value, case=case)
        for row, value in enumerate((valuation.terminal.sales_millions, valuation.terminal.operating_earnings_millions,
                                     valuation.terminal.note_interest_millions, valuation.terminal.finance_lease_interest_millions,
                                     valuation.terminal.operating_cash_interest_millions), 96):
            book.expect(DCF, f"F{row}", value, case=case)
        for row, value in enumerate((valuation.terminal.tax_millions, valuation.terminal.net_ppe_investment_millions,
                                     valuation.terminal.working_capital_investment_millions, valuation.terminal.operating_cash_investment_millions,
                                     valuation.terminal.net_borrowing_millions, valuation.terminal.fcfe_millions), 102):
            book.expect(DCF, f"F{row}", value, case=case)
        for start, grid in ((112, valuation.sensitivity_value_per_share), (123, target.sensitivity_value_per_share)):
            for row, values in enumerate(grid, start):
                for col, value in zip("CDEFGHI", values, strict=True):
                    book.expect(DCF, f"{col}{row}", value, case=case)


def build_valuation(book: "WorkbookPlan", data: "FreshWorkbookData") -> None:
    """Write the selected-case spot and horizon DCFs plus the fixed peer snapshot."""
    season_rows, maturity_rows, dividend_rows = _inputs(book, data)
    _spot(book)
    note_rows = _horizon(book, season_rows, maturity_rows, dividend_rows)
    _terminal_and_sensitivity(book, note_rows)
    _comparables(book, data.comps)
    _expect_valuation(book, data, note_rows)

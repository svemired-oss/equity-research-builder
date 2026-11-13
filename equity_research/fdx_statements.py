"""Linked FedEx archive statements, in USD millions except share data and rates."""

from dataclasses import dataclass, field
from math import isclose

from .fdx_revenue import RevenueForecast

PCT = '0.0%;(0.0%);"-"'
USD = '$#,##0.00;($#,##0.00);"-"'

# FY2023 and FY2025 10-K consolidated cash flow statements and FY2026 Q1 10-Q.
# Opening cash, CFO, capex, CFI, debt proceeds, principal paid, stock proceeds,
# dividends, buybacks, CFF, FX, ending cash, SBC, net operating balance changes.
HISTORICAL_CASH = (
    (4881, 10135, -5884, -6010, 4212, -6318, 740, -686, 0, -2090, 171, 7087, 200, -1872),
    (7087, 9832, -6763, -6816, 0, -161, 184, -793, -2248, -3019, -187, 6897, 190, -3119),
    (6897, 8848, -6174, -6174, 0, -152, 231, -1177, -1500, -2597, -118, 6856, 182, -3140),
    (6856, 8312, -5176, -5200, 0, -147, 491, -1259, -2500, -3426, -41, 6501, 163, -3431),
    (6501, 7036, -4055, -4092, 0, -157, 524, -1339, -3017, -4019, 76, 5502, 154, -4700),
    (5502, 1716, -623, -619, 997, -625, 18, -345, -500, -460, 27, 6166, 56, -1110),
)

# Interest is reported net in the interim release. Other includes separation costs.
Q1_INCOME = {5: 22244, 7: 8062, 8: 5488, 9: 1192, 10: 1092, 11: 873,
             12: 843, 13: 0, 14: 67, 15: 3441, 16: 21058, 17: 1186, 19: -119,
             21: 60, 23: 7, 24: -52, 25: 1134, 26: 310, 27: 824, 28: 3.48, 29: 3.46}

IS_TOTALS = {16: "SUM({c}7:{c}15)", 17: "{c}5-{c}16", 24: "SUM({c}19:{c}23)",
             25: "{c}17+{c}24", 27: "{c}25-{c}26"}
BS_TOTALS = {10: "SUM({c}6:{c}9)", 17: "SUM({c}12:{c}16)", 19: "{c}17+{c}18",
             24: "SUM({c}21:{c}23)", 25: "{c}10+{c}19+{c}24", 33: "SUM({c}28:{c}32)",
             41: "SUM({c}36:{c}40)", 48: "SUM({c}43:{c}47)",
             49: "{c}33+{c}34+{c}41+{c}48"}
BS_INPUTS = {66: "Receivable days", 67: "Spare parts / maintenance", 68: "Prepaids / operating expenses",
             82: "Other assets / revenue", 88: "Accrued payroll / compensation",
             89: "Payables / operating expenses", 90: "Current share of operating lease liabilities",
             91: "Accrued expenses / operating expenses", 95: "Deferred tax liability / net PP&E",
             96: "Pension and related obligations", 97: "Self-insurance accrual / operating expenses",
             99: "Other liabilities / revenue", 103: "Stock-based compensation proxy"}
CF_LABELS = {2: "Net income", 3: "Depreciation and amortization", 4: "Stock-based compensation",
             5: "Other noncash items (historical)", 7: "Receivables", 8: "Spare parts and prepaids",
             9: "Payables and current accruals", 10: "Change in net operating working capital",
             11: "Other operating assets and liabilities", 12: "Reported operating balance changes",
             13: "Cash from operations", 16: "Capital expenditures", 17: "Other investing cash flows",
             18: "Cash from investing", 21: "Debt proceeds", 22: "Notes and finance lease principal paid",
             23: "Stock issuance proceeds", 24: "Other financing cash flows", 25: "Net cash borrowing",
             51: "Share repurchases", 52: "Dividends paid", 53: "Cash from financing", 54: "Net change in cash",
             55: "Effect of exchange rates on cash", 56: "Opening cash", 57: "Closing cash", 59: "FCFE before distributions"}
CF_TOTALS = {10: "SUM({c}7:{c}9)", 13: "SUM({c}2:{c}5,{c}10:{c}12)", 18: "SUM({c}16:{c}17)",
             25: "SUM({c}21:{c}22)", 53: "SUM({c}23:{c}25,{c}51:{c}52)",
             54: "SUM({c}13,{c}18,{c}53,{c}55)", 57: "SUM({c}54,{c}56)", 59: "SUM({c}13,{c}16,{c}25)"}


@dataclass
class WorkbookPlan:
    cells: dict = field(default_factory=dict)
    expected: dict = field(default_factory=dict)

    def set(self, sheet, address, value, kind="", fmt=None):
        self.cells.setdefault(sheet, {})[address] = (value, kind, fmt)

    def calc(self, sheet, address, expression, expected, *, kind="", fmt=None):
        self.set(sheet, address, "=" + expression, kind, fmt)
        self.expected.setdefault(sheet, {})[address] = expected


@dataclass(frozen=True)
class StatementForecast:
    fcfe: float
    borrowing: float
    depreciation: float
    capex: float
    shares: float
    net_income: float
    cash: float


def _require_close(label, actual, expected):
    if not isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-6):
        raise ValueError(f"{label} fails to reconcile: {actual} versus {expected}.")


def _income_totals(values):
    values[16] = sum(values.get(row, 0) for row in range(7, 16))
    values[17] = values[5] - values[16]
    values[24] = sum(values.get(row, 0) for row in range(19, 24))
    values[25] = values[17] + values[24]


def _balance_totals(values):
    values[10] = sum(values[row] for row in range(6, 10))
    values[17] = sum(values[row] for row in range(12, 17))
    values[19] = values[17] + values[18]
    values[24] = sum(values[row] for row in range(21, 24))
    values[25] = values[10] + values[19] + values[24]
    values[33] = sum(values.get(row, 0) for row in range(28, 33))
    values[41] = sum(values[row] for row in range(36, 41))
    values[48] = sum(values[row] for row in range(43, 48))
    values[49] = values[33] + values[34] + values[41] + values[48]


def _historical_statements(plan, inputs, cached):
    for name, title, note, header in (
        ("Income statement", "CONSOLIDATED STATEMENTS OF INCOME", "USD millions except per-share data", 4),
        ("Balance sheet", "CONSOLIDATED BALANCE SHEETS", "USD millions except share data", 3),
    ):
        plan.set(name, "A1", title, "title")
        plan.set(name, "A2", note, "subtitle")
        final = 29 if name == "Income statement" else 49
        sections = {6, 18} if final == 29 else {4, 5, 11, 20, 26, 27, 35, 42}
        totals = IS_TOTALS if final == 29 else BS_TOTALS
        for row in range(header + 1, final + 1):
            label = cached.value(name, f"A{row}")
            if isinstance(label, str):
                plan.set(name, f"A{row}", label.strip().replace("Maintanence", "Maintenance"),
                         "section" if row in sections else "total" if row in totals else "")
    plan.set("Income statement", "A19", "Interest expense (net in Q1 FY26)")
    plan.set("Income statement", "A15", "Other operating expenses (includes Q1 separation)")
    plan.set("Balance sheet", "A34", "Long-term debt / FY30 unclassified total")
    for index, (col, period) in enumerate(zip("BCDEF", inputs.historical, strict=True)):
        year = 2021 + index
        plan.set("Income statement", f"{col}4", f"FY{year}A", "header")
        plan.set("Balance sheet", f"{col}3", f"FY{year}A", "header")
        for name, values, formulas in (("Income statement", period.income_statement, IS_TOTALS),
                                        ("Balance sheet", period.balance_sheet, BS_TOTALS)):
            for row, value in values.items():
                if row > (29 if name == "Income statement" else 49):
                    continue
                if row in formulas:
                    plan.calc(name, f"{col}{row}", formulas[row].format(c=col), value, kind="total")
                else:
                    plan.set(name, f"{col}{row}", value, fmt=USD if name == "Income statement" and row in (28, 29) else None)
    plan.set("Income statement", "G4", "Q1 FY26A", "header")
    for row, value in Q1_INCOME.items():
        if row in IS_TOTALS:
            plan.calc("Income statement", f"G{row}", IS_TOTALS[row].format(c="G"), value, kind="total")
        else:
            plan.set("Income statement", f"G{row}", value, fmt=USD if row in (28, 29) else None)


def _cash_history(plan, inputs):
    sheet = "Derived cash flow"
    plan.set(sheet, "A1", "CASH FLOW AND FCFE", "title")
    for row, label in CF_LABELS.items():
        plan.set(sheet, f"A{row}", label, "total" if row in CF_TOTALS else "")
    for row, label in ((6, "Operating working capital"), (15, "Investing activities"), (20, "Financing activities")):
        plan.set(sheet, f"A{row}", label, "section")
    # Keep archive C:G forecast positions. Historical disclosures sit to the left on the statements
    # and in a dedicated chronological block below the cash flow forecast.
    plan.set(sheet, "A63", "REPORTED CASH FLOWS  |  USD MILLIONS", "section")
    for row, label in CF_LABELS.items():
        if row not in (7, 8, 9, 10, 11):
            plan.set(sheet, f"A{row + 63}", label, "total" if row in CF_TOTALS else "")
    for index, (c, cash) in enumerate(zip("BCDEFG", HISTORICAL_CASH, strict=True)):
        opening, cfo, capex, cfi, debt, principal, stock, dividends, repurchase, cff, fx, close, sbc, changes = cash
        income = inputs.historical[index].income_statement if index < 5 else Q1_INCOME
        _require_close(f"FY{2021 + index} reported cash", opening + cfo + cfi + cff + fx, close)
        if index < 5:
            _require_close(f"FY{2021 + index} balance sheet cash", inputs.historical[index].balance_sheet[6], close)
        plan.set(sheet, f"{c}64", f"FY{2021 + index}A" if index < 5 else "Q1 FY26A", "header")
        line_items = {4: sbc, 5: cfo - income[27] - income[10] - sbc - changes,
                      12: changes, 16: capex, 17: cfi - capex, 21: debt, 22: principal, 23: stock,
                      24: cff - debt - principal - stock - dividends - repurchase, 51: repurchase,
                      52: dividends, 55: fx, 56: opening}
        for row, value in line_items.items():
            plan.set(sheet, f"{c}{row + 63}", value)
        for row, source_row in ((2, 27), (3, 10)):
            plan.calc(sheet, f"{c}{row + 63}", f"'Income statement'!{c}{source_row}", income[source_row])
        formulas = {
            13: (f"SUM({c}65:{c}68,{c}75)", cfo), 18: (f"SUM({c}79:{c}80)", cfi),
            25: (f"SUM({c}84:{c}85)", debt + principal), 53: (f"SUM({c}86:{c}88,{c}114:{c}115)", cff),
            54: (f"SUM({c}76,{c}81,{c}116,{c}118)", close - opening),
            57: (f"SUM({c}117,{c}119)", close), 59: (f"SUM({c}76,{c}79,{c}88)", cfo + capex + debt + principal),
        }
        for row, (expression, expected) in formulas.items():
            plan.calc(sheet, f"{c}{row + 63}", expression, expected, kind="total")


def _operating_expenses(plan, cached, revenues):
    name = "Operating expenses"
    plan.set(name, "A1", "OPERATING EXPENSES BY SEGMENT", "title")
    plan.set(name, "A2", "USD millions  |  Rate inputs from the portfolio model", "subtitle")
    for row in (3, 32, 55, 78):
        plan.set(name, f"A{row}", {3: "Federal Express", 32: "FedEx Freight", 55: "Other and eliminations",
                                  78: "Consolidated operating expenses"}[row], "section")
    sales = (4, 33, 56)
    detail = ((5, 10, 15, 17, 19, 21, 23, 24, 27), (34, 36, 38, 40, 42, 44, 46, 47, 50),
              (57, 59, 61, 63, 65, 67, 69, 70, 73))
    rate_rows = {5: 6, 10: 11, 15: 16, 17: 18, 19: 20, 21: 22, 27: 28, 34: 35, 36: 37, 38: 39,
                 40: 41, 42: 43, 44: 45, 50: 51, 57: 58, 59: 60, 61: 62, 63: 64, 65: 66, 67: 68, 73: 74}
    groups = [*sales, *(row for rows in detail for row in rows), *range(79, 88)]
    for row in groups:
        plan.set(name, f"A{row}", "Segment revenue" if row in sales else str(cached.value(name, f"A{row}") or "Asset impairment").strip())
    for row, driver_row in rate_rows.items():
        growth = row in (61, 63, 65, 67, 73)
        plan.set(name, f"A{driver_row}", "Annual expense growth" if growth else "Expense / segment revenue", "note")
    for row, label in ((29, "Federal Express operating expenses"), (52, "FedEx Freight operating expenses"),
                       (75, "Other and eliminations operating expenses"), (90, "Consolidated operating expenses")):
        plan.set(name, f"A{row}", label, "total")
    for col, rev_col, year in zip("BCD", "EFG", range(2023, 2026), strict=True):
        plan.set(name, f"{col}3", f"FY{year}A", "header")
        for row, segment in zip(sales, (4, 5, 6), strict=True):
            plan.calc(name, f"{col}{row}", f"Revenue!{rev_col}{segment}", cached.number("Revenue", f"{rev_col}{segment}"))
        for row in (row for rows in detail for row in rows):
            value = cached.value(name, f"{col}{row}")
            if type(value) in (int, float):
                plan.set(name, f"{col}{row}", value)
    prior = {row: cached.number(name, f"D{row}") for row in (15, 61, 63, 65, 67, 73)}
    outputs = []
    for index, (col, previous_col, rev_col, rev) in enumerate(zip("EFGHI", "DEFGH", "JKLMN", revenues, strict=True)):
        plan.set(name, f"{col}3", f"FY{2026 + index}E", "header")
        values = {}
        for segment, sale_row, rows, revenue in zip((4, 5, 6), sales, detail,
                (rev.express_revenue_millions, rev.freight_segment_revenue_millions,
                 rev.total_revenue_millions - rev.express_revenue_millions - rev.freight_segment_revenue_millions), strict=True):
            plan.calc(name, f"{col}{sale_row}", f"Revenue!{rev_col}{segment}", revenue)
            for row in rows:
                if row not in rate_rows:
                    # Impairment is event-driven. The archive does not document forecast impairments.
                    value = (cached.value(name, f"{col}{row}") or 0) if row in (24, 47, 70) else 0
                    plan.set(name, f"{col}{row}", value, "input")
                else:
                    rate_row = rate_rows[row]
                    rate = cached.number(name, f"{col}{rate_row}")
                    growth = row in (61, 63, 65, 67, 73) or (row == 15 and index > 0)
                    plan.set(name, f"{col}{rate_row}", rate, "input", PCT)
                    expr = f"{previous_col}{row}*(1+{col}{rate_row})" if growth else f"{col}{sale_row}*{col}{rate_row}"
                    value = prior[row] * (1 + rate) if growth else revenue * rate
                    plan.calc(name, f"{col}{row}", expr, value)
                values[row] = value
        for total, rows in zip((29, 52, 75), detail, strict=True):
            plan.calc(name, f"{col}{total}", "+".join(f"{col}{row}" for row in rows), sum(values[row] for row in rows), kind="total")
        consolidated = {}
        for row, parts in enumerate(zip(*detail, strict=True), 79):
            consolidated[row] = sum(values[part] for part in parts)
            plan.calc(name, f"{col}{row}", "+".join(f"{col}{part}" for part in parts), consolidated[row])
        plan.calc(name, f"{col}90", f"SUM({col}79:{col}87)", sum(consolidated.values()), kind="total")
        prior = values
        outputs.append(consolidated)
    return outputs


def _schedule_layout(plan, inputs, cached):
    labels = {
        "CapEx": {1: "CAPITAL EXPENDITURES", 8: "Asset class", 10: "Aircraft and related equipment",
                  11: "Package handling and ground support equipment", 12: "Information technology",
                  13: "Vehicles and trailers", 14: "Facilities and other", 15: "Total capex",
                  18: "Opening net PP&E", 19: "Property depreciation", 20: "Closing net PP&E",
                  23: "Intangible amortization", 24: "Closing intangible assets"},
        "Debt schedule": {1: "DEBT AND FINANCING", 6: "Fiscal year", 7: "Opening debt (carrying value)",
                          8: "Total principal paid", 9: "New note proceeds", 10: "Closing debt (carrying value)",
                          11: "Average notes carrying value", 12: "Interest expense", 13: "Estimated note interest rate",
                          15: "Contractual note principal payments", 16: "Contractual finance lease payments",
                          17: "Opening finance lease debt", 18: "Estimated finance lease interest",
                          19: "Estimated finance lease principal", 20: "Closing finance lease debt",
                          21: "Closing notes carrying value", 22: "Current portion of debt"},
    }
    totals = {"CapEx": {15, 20, 24}, "Debt schedule": {8, 10, 12, 20, 21}}
    for sheet, rows in labels.items():
        for row, label in rows.items():
            plan.set(sheet, f"A{row}", label, "title" if row == 1 else "total" if row in totals[sheet] else "")
        plan.set(sheet, "A2", "USD millions except rates", "subtitle")
    for col, period in zip("BCD", inputs.historical[2:], strict=True):
        plan.set("CapEx", f"{col}8", f"FY{period.fiscal_end.year}A", "header")
        for row in range(10, 15):
            plan.set("CapEx", f"{col}{row}", period.capex[row])
        plan.calc("CapEx", f"{col}15", f"SUM({col}10:{col}14)", period.capex[15], kind="total")
    plan.set("CapEx", "E8", "Q1 FY26A", "header")
    for row in range(10, 15):
        plan.set("CapEx", f"E{row}", cached.number("CapEx", f"E{row}"))
    plan.calc("CapEx", "E15", "SUM(E10:E14)", 623, kind="total")
    plan.set("CapEx", "D24", 205)
    for c in "BCDEF":
        plan.set("Debt schedule", f"{c}6", f"FY{2021 + ord(c) - ord('B')}A", "header")
        history = inputs.historical[ord(c) - ord('B')].balance_sheet
        plan.calc("Debt schedule", f"{c}10", f"SUM('Balance sheet'!{c}28,'Balance sheet'!{c}34)", history[28] + history[34])
    plan.set("Debt schedule", "F20", 680)
    plan.set("Debt schedule", "B24", 0.039, "input", PCT)
    plan.set("Debt schedule", "A24", "Finance lease rate proxy (FY25 filing)")
    for row, label in BS_INPUTS.items():
        plan.set("Balance sheet", f"A{row}", label)
    plan.set("Balance sheet", "A61", "BALANCE SHEET ASSUMPTIONS", "section")
    for row, label in {145: "Operating lease additions", 146: "Operating lease amortization / principal",
                       147: "Closing operating lease liability", 155: "Opening retained earnings", 156: "Net income",
                       158: "Cash dividends", 159: "Annual dividend per share", 160: "Dividend growth",
                       161: "Closing retained earnings", 163: "New SBC shares (millions)",
                       165: "Share repurchases", 166: "Shares repurchased (millions)", 167: "Repurchase price per share",
                       168: "Repurchase price growth", 169: "Shares outstanding proxy (millions)",
                       170: "Average share count proxy (millions)"}.items():
        plan.set("Balance sheet", f"A{row}", label, "total" if row in (147, 161, 169) else "")
    plan.set("Balance sheet", "A143", "OPERATING LEASES", "section")
    plan.set("Balance sheet", "A154", "EQUITY AND SHARE COUNT", "section")
    plan.set("Balance sheet", "F159", 5.52, fmt=USD)
    plan.set("Balance sheet", "F167", cached.number("Balance sheet", "F167"), fmt=USD)
    plan.set("Balance sheet", "F169", 235.955461)
    plan.set("Income statement", "A38", "INCOME STATEMENT ASSUMPTIONS", "section")
    plan.set("Income statement", "A60", "Interest income / other assets (archive proxy)")
    plan.set("Income statement", "A61", "Retirement plan income excluding mark-to-market")
    plan.set("Income statement", "A63", "Other nonoperating income (expense)")
    plan.set("Income statement", "A66", "Effective tax rate")
    plan.set("Balance sheet", "A171", "FY25 year-end shares (rounded filing data)")
    plan.set("Balance sheet", "F171", 238)


def _debt_and_capex(plan, cached, index, col, prior, pp, dep):
    """Advance note and lease principal separately while preserving source capex categories."""
    n = "Debt schedule"
    p = "CapEx"
    c = col
    d = "FGHIJ"[index]
    previous = "FGHIJ"[index]
    forecast_capex = {row: cached.number(p, f"{d}{row}") for row in range(10, 15)}
    capex = sum(forecast_capex.values())
    amort = (54, 53, 50, 11, 10)[index]
    depreciation = dep - amort
    intangible = prior["intangible"] - amort
    net_ppe = pp + capex - depreciation
    plan.set(p, f"{d}8", f"FY{2026 + index}E", "header")
    for row, value in forecast_capex.items():
        plan.set(p, f"{d}{row}", value, "input")
    plan.set(p, f"{d}23", amort, "input")
    prior_intangible = "DFGHI"[index]
    income_col = "HIJKL"[index]
    for row, expr, value in (
        (15, f"SUM({d}10:{d}14)", capex), (18, f"'Balance sheet'!{previous}19", pp),
        (19, f"'Income statement'!{income_col}10-{d}23", depreciation),
        (20, f"SUM({d}18,{d}15)-{d}19", net_ppe), (24, f"{prior_intangible}24-{d}23", intangible),
    ):
        plan.calc(p, f"{d}{row}", expr, value, kind="total" if row in (15, 20, 24) else "")
    plan.set(n, f"{c}6", f"FY{2026 + index}E", "header")
    maturities = cached.number(n, f"{c}8")
    lease_payment = (84, 85, 84, 82, 74)[index]
    issuance = cached.number(n, f"{c}9")
    rate = cached.number(n, f"{c}13")
    lease_interest = prior["finance_lease"] * 0.039
    lease_principal = lease_payment - lease_interest
    lease_close = prior["finance_lease"] - lease_principal
    paid = maturities + lease_principal
    closing = prior["debt"] + issuance - paid
    average_notes = (prior["debt"] - prior["finance_lease"] + closing - lease_close) / 2
    interest = average_notes * rate + lease_interest
    for row, value in {9: issuance, 13: rate, 15: maturities, 16: lease_payment}.items():
        plan.set(n, f"{c}{row}", value, "input", PCT if row == 13 else None)
    debt_rows = {
        7: (f"{previous}10", prior["debt"]), 8: (f"SUM({c}15,{c}19)", paid),
        10: (f"{c}7+{c}9-{c}8", closing), 11: (f"AVERAGE({c}7-{c}17,{c}21)", average_notes),
        12: (f"{c}11*{c}13+{c}18", interest), 17: (f"{previous}20", prior["finance_lease"]),
        18: (f"{c}17*$B$24", lease_interest), 19: (f"{c}16-{c}18", lease_principal),
        20: (f"{c}17-{c}19", lease_close), 21: (f"{c}10-{c}20", closing - lease_close),
    }
    current = None
    if index < 4:
        following = "HIJK"[index]
        next_payment = (85, 84, 82, 74)[index]
        current = cached.number(n, f"{following}8") + next_payment - lease_close * 0.039
        debt_rows[22] = (f"{following}8", current)
    for row, (expr, value) in debt_rows.items():
        plan.calc(n, f"{c}{row}", expr, value, kind="total" if row in (8, 10, 12, 20, 21) else "")
    return forecast_capex, capex, depreciation, intangible, closing, current, interest, issuance, paid, lease_close


def _forecast_statements(plan, cached, inputs, revenues):
    income_name, balance_name, flow_name = "Income statement", "Balance sheet", "Derived cash flow"
    expenses = _operating_expenses(plan, cached, revenues)
    _schedule_layout(plan, inputs, cached)
    prior = dict(inputs.historical[-1].balance_sheet)
    state = {"debt": prior[28] + prior[34], "finance_lease": 680, "intangible": 205,
             "dividend": 5.52, "buyback_price": cached.number(balance_name, "F167"), "shares": 238}
    forecasts = []
    for i, (b, prev, e, o, f, p, r, rev, expense) in enumerate(zip(
        "GHIJK", "FGHIJ", "HIJKL", "EFGHI", "CDEFG", "FGHIJ", "JKLMN", revenues, expenses, strict=True,
    )):
        year = f"FY{2026 + i}E"
        plan.set(income_name, f"{e}4", year, "header")
        plan.set(balance_name, f"{b}3", year, "header")
        plan.set(flow_name, f"{f}1", year, "header")
        revenue = rev.total_revenue_millions
        income = {5: revenue, **{row: expense[row + 72] for row in range(7, 16)}}
        ie = {5: f"Revenue!{r}36", **{row: f"'Operating expenses'!{o}{row + 72}" for row in range(7, 16)}}
        capex_by_asset, capex, depreciation, intangible, debt, current, interest, issuance, paid, finance_lease = _debt_and_capex(
            plan, cached, i, b, state, prior[19], income[10],
        )
        ratios = {row: cached.number(balance_name, f"{b}{row}") for row in BS_INPUTS}
        for row, ratio in ratios.items():
            plan.set(balance_name, f"{b}{row}", ratio, "input", None if row in (66, 96, 103) else PCT)
        other_assets = ratios[82] * revenue + intangible - 205
        is_inputs = {row: cached.number(income_name, f"{e}{row}") for row in (60, 63, 66)}
        is_inputs[61] = (404 + 161 + 198) / 3  # FY23–25 other retirement income less noncash MTM gains.
        for row, value in is_inputs.items():
            plan.set(income_name, f"{e}{row}", value, "input", PCT if row in (60, 66) else None)
        income.update({19: -interest, 20: is_inputs[60] * other_assets, 21: is_inputs[61], 23: is_inputs[63]})
        _income_totals(income)
        income[26] = income[25] * is_inputs[66]
        income[27] = income[25] - income[26]
        ie.update({19: f"-'Debt schedule'!{b}12", 20: f"{e}60*'Balance sheet'!{b}23", 21: f"{e}61",
                   23: f"{e}63", 26: f"{e}25*{e}66", 29: f"{e}27/'Balance sheet'!{b}170"})
        ie.update({row: formula.format(c=e) for row, formula in IS_TOTALS.items()})
        dividend_growth = cached.number(balance_name, f"{b}160")
        dividend = state["dividend"] * (1 + dividend_growth)
        buyback = cached.number(balance_name, f"{b}165")
        buyback_price = state["buyback_price"] * 1.1
        grant = cached.number(balance_name, f"{b}163")
        shares_bought = buyback / buyback_price
        shares = state["shares"] + grant - shares_bought
        average_shares = (state["shares"] + shares) / 2
        if shares <= 0 or debt < 0 or intangible < 0:
            raise ValueError(f"{year} forecast has an invalid closing balance.")
        dividends = dividend * shares
        income[29] = income[27] / average_shares
        for row, formula in ie.items():
            plan.calc(income_name, f"{e}{row}", formula, income[row], kind="total" if row in IS_TOTALS else "", fmt=USD if row == 29 else None)

        lease_in = cached.number(balance_name, f"{f}145")
        lease_out = cached.number(balance_name, f"{f}146")
        lease_liab = prior[31] + prior[39] + lease_in + lease_out
        lease_assets = prior[21] + lease_in + lease_out
        net_ppe = prior[19] + capex - depreciation
        opex = income[16]
        balance = {7: ratios[66] * revenue / 365, 8: ratios[67] * income[12], 9: ratios[68] * opex,
                   **{row: prior[row] + capex_by_asset[row - 2] for row in range(12, 17)},
                   18: prior[18] - depreciation, 21: lease_assets, 22: prior[22], 23: other_assets,
                   29: income[7] * ratios[88], 30: opex * ratios[89], 31: lease_liab * ratios[90],
                   32: opex * ratios[91], 34: debt - (current or 0), 36: net_ppe * ratios[95],
                   37: ratios[96], 38: opex * ratios[97], 39: lease_liab * (1 - ratios[90]),
                   40: revenue * ratios[99], 43: prior[43], 44: prior[44] + ratios[103],
                   45: prior[45] + income[27] - dividends, 46: prior[46], 47: prior[47] - buyback}
        bref = f"'Balance sheet'!{b}"
        prevref = f"'Balance sheet'!{prev}"
        ref = f"'Income statement'!{e}"
        be = {6: f"'Derived cash flow'!{f}57", 7: f"{b}66*{ref}5/365", 8: f"{b}67*{ref}12", 9: f"{b}68*{ref}16",
              **{row: f"{prev}{row}+CapEx!{p}{row - 2}" for row in range(12, 17)},
              18: f"{prev}18-CapEx!{p}19", 21: f"SUM({prev}21,{b}145:{b}146)", 22: f"{prev}22",
              23: f"{b}82*{ref}5+CapEx!{p}24-CapEx!$D$24", 29: f"{ref}7*{b}88", 30: f"{ref}16*{b}89",
              31: f"{b}147*{b}90", 32: f"{ref}16*{b}91", 34: f"'Debt schedule'!{b}10-SUM({b}28)",
              36: f"{b}19*{b}95", 37: f"{b}96", 38: f"{ref}16*{b}97", 39: f"{b}147-{b}31",
              40: f"{ref}5*{b}99", 43: f"{prev}43", 44: f"SUM({prev}44,{b}103)", 45: f"{b}161",
              46: f"{prev}46", 47: f"{prev}47-{b}165"}
        if current is not None:
            balance[28] = current
            be[28] = f"'Debt schedule'!{b}22"
        for row, formula in BS_TOTALS.items():
            be[row] = formula.format(c=b)
        for row, value in {145: lease_in, 146: lease_out, 160: dividend_growth, 163: grant, 165: buyback, 168: 0.1}.items():
            plan.set(balance_name, f"{b}{row}", value, "input", PCT if row in (160, 168) else None)
        share_open = "F171" if i == 0 else f"{prev}169"
        equity = {
            147: (f"SUM({prev}31,{prev}39,{b}145:{b}146)", lease_liab),
            155: (f"{prev}45", prior[45]), 156: (f"{ref}27", income[27]), 158: (f"{b}159*{b}169", dividends),
            159: (f"{prev}159*(1+{b}160)", dividend), 161: (f"SUM({b}155:{b}156)-{b}158", balance[45]),
            166: (f"{b}165/{b}167", shares_bought), 167: (f"{prev}167*(1+{b}168)", buyback_price),
            169: (f"SUM({share_open},{b}163)-{b}166", shares), 170: (f"AVERAGE({share_open},{b}169)", average_shares),
        }
        for row, (formula, value) in equity.items():
            plan.calc(balance_name, f"{b}{row}", formula, value, fmt=USD if row in (159, 167) else None)

        def change(rows):
            return sum(balance[row] - prior[row] for row in rows)

        flow = {2: income[27], 3: income[10], 4: ratios[103], 7: -change((7,)), 8: -change((8, 9)),
                9: change((29, 30, 32)),
                11: change((31, 36, 37, 38, 39, 40)) - change((21, 23)) + intangible - state["intangible"],
                16: -capex, 21: issuance, 22: -paid, 51: -buyback, 52: -dividends, 56: prior[6]}
        flow[10] = flow[7] + flow[8] + flow[9]
        flow[13] = sum(flow[row] for row in (2, 3, 4, 10, 11))
        flow[18] = flow[16]
        flow[25] = issuance - paid
        flow[53] = flow[25] - buyback - dividends
        flow[54] = flow[13] + flow[18] + flow[53]
        flow[57] = prior[6] + flow[54]
        flow[59] = flow[13] - capex + flow[25]
        balance[6] = flow[57]
        _balance_totals(balance)
        _require_close(f"{year} balance sheet", balance[25], balance[49])
        _require_close(f"{year} net PP&E", balance[19], net_ppe)
        for row, formula in be.items():
            plan.calc(balance_name, f"{b}{row}", formula, balance[row], kind="total" if row in BS_TOTALS else "")
        cf = {2: f"{ref}27", 3: f"{ref}10", 4: f"{bref}103", 7: f"{prevref}7-{bref}7",
              8: f"SUM({prevref}8:{prev}9)-SUM({bref}8:{b}9)",
              9: f"SUM({bref}29:{b}30,{bref}32)-SUM({prevref}29:{prev}30,{prevref}32)",
              11: f"SUM({bref}31,{bref}36:{b}40,{prevref}21,{prevref}23)-SUM({prevref}31,{prevref}36:{prev}40,{bref}21,{bref}23)-CapEx!{p}23",
              16: f"-CapEx!{p}15", 21: f"'Debt schedule'!{b}9", 22: f"-'Debt schedule'!{b}8", 51: f"-{bref}165",
              52: f"-{bref}158", 56: f"{prevref}6"}
        cf.update({row: formula.format(c=f) for row, formula in CF_TOTALS.items()})
        for row, formula in cf.items():
            plan.calc(flow_name, f"{f}{row}", formula, flow[row], kind="total" if row in CF_TOTALS else "")
        forecast = StatementForecast(flow[59], flow[25], income[10], capex, shares, income[27], flow[57])
        forecasts.append(forecast)
        prior = balance
        state = {"debt": debt, "finance_lease": finance_lease, "intangible": intangible, "dividend": dividend,
                 "buyback_price": buyback_price, "shares": shares}
    return tuple(forecasts)


def add_fdx_statements(plan, inputs, cached, revenues):
    _historical_statements(plan, inputs, cached)
    _cash_history(plan, inputs)
    return _forecast_statements(plan, cached, inputs, revenues)

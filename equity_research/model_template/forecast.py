"""Stage 2d forecast normalization rows (docs/method-notes.md, Rules for the automated model 5-8).

Normalized margin table, the year 2-5 revenue growth fade, each nonoperating line at its three-year
median, and funded debt held at the last actual year's debt / EBITDA.
"""

from .statements import BALANCE, CASH, INCOME

PCT = "0.00%"
TAB = "Assumptions"


def _evidence(book, tab, address, value, url, basis):
    """A filed value: cited, shown as evidence rather than an editable analyst input."""
    book.put(tab, address, value, "evidence")
    book.cite(tab, address, url, basis)
# Assumptions rows.
CAGR, LONG_RUN, FADE_FIRST = 36, 37, 38          # B38:B41 are the year 2-5 growth rates
INTEREST_INCOME, EQUITY_INCOME, OTHER_INCOME = 42, 43, 44
LEVERAGE, LEVERAGE_DEBT, LEVERAGE_EBITDA = 45, 46, 47
NORM_TOP = 50                                    # header row; table rows 51-57, medians 58-59
NORMALIZED_MEDIAN, REPORTED_MEDIAN = 58, 59
HISTORY_COLS = "HIJKL"


def _history_columns(model):
    """Income statement history columns, latest in L."""
    periods = model["history"][-5:]
    return dict(zip((p["period_end"] for p in periods), HISTORY_COLS[-len(periods):], strict=True))


def normalized_margin(book, model):
    rows = model["normalization"]
    book.put(TAB, f"A{NORM_TOP}", "Normalized operating margin | disclosed unusual items removed (docs/method-notes.md)", "section")
    labels = {51: "Reported operating income", 52: "Disclosed unusual charges added back", 53: "Asset-sale gains removed",
              54: "Normalized operating income", 55: "Revenue", 56: "Reported operating margin",
              57: "Normalized operating margin"}
    for row, label in labels.items():
        book.put(TAB, f"A{row}", label)
    for col, item in zip("BCD", rows, strict=False):
        book.put(TAB, f"{col}{NORM_TOP}", f"FY {item['period_end']}", "header")
        url = item["url"]
        _evidence(book, TAB, f"{col}51", item["operating_income"], url, f"{item['period_end']} | filed OperatingIncomeLoss")
        _evidence(book, TAB, f"{col}52", item["charges"], url,
                   f"{item['period_end']} | broadest disclosed aggregate of OtherCostAndExpenseOperating, "
                   "RestructuringSettlementAndImpairmentProvisions, or restructuring + impairments + LitigationSettlementExpense")
        _evidence(book, TAB, f"{col}53", item["gain"], url,
                   f"{item['period_end']} | filed gain (loss) on asset sales, assumed within operating income")
        book.put(TAB, f"{col}54", f"={col}51+{col}52-{col}53")
        _evidence(book, TAB, f"{col}55", item["revenue"], url, f"{item['period_end']} | filed revenue")
        book.put(TAB, f"{col}56", f"={col}51/{col}55", fmt=PCT)
        book.put(TAB, f"{col}57", f"={col}54/{col}55", fmt=PCT)
    last = "BCD"[len(rows) - 1]
    book.put(TAB, f"A{NORMALIZED_MEDIAN}", "Three-year median normalized operating margin (base EBIT margin, B3)", "total")
    book.put(TAB, f"B{NORMALIZED_MEDIAN}", f"=MEDIAN(B57:{last}57)", "total", PCT)
    book.put(TAB, f"A{REPORTED_MEDIAN}", "Three-year median reported operating margin (for comparison)")
    book.put(TAB, f"B{REPORTED_MEDIAN}", f"=MEDIAN(B56:{last}56)", fmt=PCT)
    expected = book.expected.setdefault(TAB, {})
    expected[f"B{NORMALIZED_MEDIAN}"] = model["guided_defaults"]["margin"]
    expected[f"B{REPORTED_MEDIAN}"] = model["reported_margin"]


def growth_fade(book, model, revenue_total):
    fade = model["revenue_fade"]
    cols = _history_columns(model)
    latest = cols[model["history"][-1]["period_end"]]
    earlier = cols[model["history"][-1 - fade["years"]]["period_end"]]
    book.put(TAB, f"A{CAGR}", f"{fade['years']}-year consolidated revenue CAGR")
    book.put(TAB, f"B{CAGR}", f"=('{INCOME}'!${latest}$4/'{INCOME}'!${earlier}$4)^(1/{fade['years']})-1", fmt=PCT)
    book.put(TAB, f"A{LONG_RUN}", "Long-run nominal revenue growth reached in year 5")
    book.input(TAB, f"B{LONG_RUN}", fade["long_run"],
               basis="docs/method-notes.md | analyst assumption: years 2-5 fade linearly from the CAGR to 4% nominal", fmt=PCT)
    expected = book.expected.setdefault(TAB, {})
    expected[f"B{CAGR}"] = fade["cagr"]
    for k in range(4):
        row = FADE_FIRST + k
        book.sources.pop((TAB, f"B{row}"), None)
        book.put(TAB, f"A{row}", f"Year {k + 2} revenue growth (linear fade)")
        book.put(TAB, f"B{row}", f"=$B${CAGR}+($B${LONG_RUN}-$B${CAGR})*{k}/3", fmt=PCT)
        expected[f"B{row}"] = fade["path"][k]
    if model["driver"]["type"] != "segment_growth":
        book.put(TAB, f"C{CAGR}", "The fade does not apply to operating-driver revenue paths.", "note")
        return
    rb = book.cells["Revenue Build"]
    for k in range(len(model["lines"])):
        row = 4 + k
        for i, (col, prev) in enumerate(zip("CDEF", "BCDE", strict=True)):
            rb[f"{col}{row}"] = [f"={prev}{row}*(1+Assumptions!$B${FADE_FIRST + i})", "text", None]
    book.put("Revenue Build", f"A{revenue_total + 1}",
             "Year 1 fades each line's recent growth halfway to terminal growth, then any filed guidance anchor applies. "
             "Years 2-5 apply the consolidated growth fade on Assumptions rows 36-41 to every line.", "note")


def nonoperating(book, model):
    annual = model["sources"]["Annual financials and revenue lines"]
    from ..fcfe_valuation import NONOPERATING
    cols = _history_columns(model)
    for (key, label), row, is_row in zip(NONOPERATING, (INTEREST_INCOME, EQUITY_INCOME, OTHER_INCOME), (19, 20, 21),
                                         strict=True):
        book.put(TAB, f"A{row}", f"{label}, USD millions (three-year filed median)")
        book.input(TAB, f"B{row}", model["nonoperating"][key], annual,
                   f"Median of the last three filed annual values; a year without the fact counts as zero")
        book.put(INCOME, f"A{is_row}", f"{label} (forecast: three-year median)")
        for col in "BCDEF":
            book.put(INCOME, f"{col}{is_row}", f"=Assumptions!$B${row}")
        for period in model["history"][-5:]:
            value = period["metrics"].get(key)
            book.put(INCOME, f"{cols[period['period_end']]}{is_row}",
                     value / 1e6 if type(value) in (int, float) else "Not filed", "evidence")
    book.put(TAB, "A33", "Total nonoperating income carried forward, USD millions")
    book.put(TAB, "B33", f"=SUM(B{INTEREST_INCOME}:B{OTHER_INCOME})")
    book.sources.pop((TAB, "B33"), None)
    book.expected.setdefault(TAB, {})["B33"] = model["other_income"]
    book.put(INCOME, "A14", "Nonoperating income (rows 19-21)")
    book.put(INCOME, "A18", "Forecast nonoperating income is interest, equity-method and other income, each at its three-year "
             "filed median (rows 19-21). Historical row 14 is all other pretax items.")


def debt_schedule(book, model):
    leverage = model["leverage"]
    book.put(TAB, f"A{LEVERAGE}", "Debt / EBITDA held from the last actual year")
    book.put(TAB, f"B{LEVERAGE}", f"=B{LEVERAGE_DEBT}/B{LEVERAGE_EBITDA}", fmt="0.00")
    book.put(TAB, f"A{LEVERAGE_DEBT}", f"Funded debt at {leverage['period_end']}, USD millions")
    _evidence(book, TAB, f"B{LEVERAGE_DEBT}", leverage["debt"], leverage["url"], "Reconciled annual funded debt on the valuation bridge basis")
    book.put(TAB, f"A{LEVERAGE_EBITDA}", f"EBITDA (reported operating income + D&A) for FY {leverage['period_end']}, USD millions")
    _evidence(book, TAB, f"B{LEVERAGE_EBITDA}", leverage["ebitda"], leverage["url"], "Filed OperatingIncomeLoss plus filed D&A")
    book.expected.setdefault(TAB, {})[f"B{LEVERAGE}"] = leverage["ratio"]
    book.put(TAB, "A20", "Analyst annual debt addition on top of the leverage target (cumulative)")
    book.put(TAB, "A21", "Analyst annual debt reduction on top of the leverage target (cumulative)")
    for i, col in enumerate("BCDEF"):
        opening = f"{col}15" + ("+Schedules!$B$40" if i == 0 else "")
        target = (f"(Assumptions!$B${LEVERAGE}*('{INCOME}'!{col}5+Schedules!{col}6)"
                  f"+{i + 1}*(Assumptions!$B$20-Assumptions!$B$21))")
        book.put("Schedules", f"{col}16", f"=MAX(0,{target}-({opening}))" + ("+MAX(0,Schedules!$B$40)" if i == 0 else ""))
        book.put("Schedules", f"{col}17", f"=MAX(0,({opening})-{target})" + ("+MAX(0,-Schedules!$B$40)" if i == 0 else ""))
    book.put("Schedules", "A16", "Debt issued (to hold debt / EBITDA)")
    book.put("Schedules", "A17", "Debt repaid (to hold debt / EBITDA)")
    book.put("Schedules", "A42", "Closing funded debt = Assumptions B45 debt / EBITDA × forecast EBITDA; the change is net "
             "borrowing in FCFE.", "note")
    # Base case (the selector default): final closing debt matches the FCFE engine.
    book.expected.setdefault("Schedules", {})["F18"] = model["fcfe"]["Base"].closing_debt


def build(book, model, revenue_total):
    normalized_margin(book, model)
    growth_fade(book, model, revenue_total)
    nonoperating(book, model)
    debt_schedule(book, model)

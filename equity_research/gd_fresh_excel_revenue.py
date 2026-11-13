"""Write the sourced GD operating forecast and its editable inputs to Excel."""

from datetime import date

from .gd_operating_forecast import PRODUCTS, PRODUCT_LABELS


REVENUE = "Revenue model"
INPUTS = "Operating inputs"
INPUT_REF = "'Operating inputs'!"
REVENUE_REF = "'Revenue model'!"
MONEY = '#,##0;(#,##0);"-"'
PERCENT = "0.0%"
PRODUCT_NAMES = {row: label.capitalize() for row, label in PRODUCT_LABELS.items()}
PRODUCT_NAMES.update({"49": "Information technology (IT) services", "50": "C5ISR solutions"})

# The original model's segment sections, subtotals, and operating margin rows.
SEGMENTS = (
    ("aerospace", "Aerospace", 7, 10, 19, 20),
    ("marine_systems", "Marine Systems", 22, 26, 31, 33),
    ("combat_systems", "Combat Systems", 35, 39, 45, 46),
    ("technologies", "Technologies", 48, 51, 56, 57),
)
GROWTH_ROWS = {
    "9": (18, "Aircraft services revenue growth"),
    "15": (16, "Revenue per delivery growth"),
    "23": (29, "Submarine revenue growth"),
    "24": (30, "Surface ship revenue growth"),
    "25": (32, "Repair and service revenue growth"),
    "36": (42, "Military vehicle revenue growth"),
    "37": (43, "Weapons and munitions revenue growth"),
    "38": (44, "Engineering and service revenue growth"),
    "49": (54, "IT services revenue growth"),
    "50": (55, "C5ISR revenue growth"),
}
PATH_ROWS = {driver: row for row, driver in enumerate(GROWTH_ROWS, 46)}
PRIOR_MIX_ROWS = {product: row for row, product in enumerate(PRODUCT_LABELS, 25)}


def _source(book, row, label, source):
    book.put(INPUTS, f"A{row}", label, kind="note")
    book.put(INPUTS, f"B{row}", source.available_on, kind="note", fmt="yyyy-mm-dd")
    book.put(INPUTS, f"C{row}", source.citation, kind="note")
    if source.url:
        book.put(INPUTS, f"D{row}", source.url, kind="note")


def _operating_inputs(book, data):
    operations = data.operations
    evidence, assumptions = operations.evidence, operations.assumptions
    interim = data.interim

    def put(address, value, *, kind=None, fmt=None):
        book.put(INPUTS, address, value, kind=kind, fmt=fmt)

    put("A1", "Operating inputs and forecast bridge", kind="title")
    put("A2", "Evidence cutoff", kind="header")
    put("B2", operations.as_of, kind="actual", fmt="yyyy-mm-dd")
    put("A3", "FY guidance and Q4 allocation | USD millions", kind="section")
    for column, header in zip("ABCDEFGH", (
        "Segment", "July FY sales", "July margin", "Q3 sales", "9M sales",
        "July Q4 sales", "Positive gap", "Allocation weight",
    ), strict=True):
        put(f"{column}4", header, kind="header")
    for row, (segment, label, _, revenue_row, _, _) in enumerate(SEGMENTS, 5):
        put(f"A{row}", label)
        put(f"B{row}", evidence.july_revenue[segment], kind="actual", fmt=MONEY)
        put(f"C{row}", evidence.july_margins[segment], kind="actual", fmt=PERCENT)
        put(f"D{row}", evidence.q3_segment_revenue[segment], kind="actual", fmt=MONEY)
        put(f"E{row}", f"={REVENUE_REF}O{revenue_row}", fmt=MONEY)
        put(f"F{row}", f"=B{row}-E{row}", fmt=MONEY)
        if segment != "aerospace":
            put(f"G{row}", f"=MAX(0,D{row}-F{row})", fmt=MONEY)
            put(f"H{row}", f"=G{row}/SUM($G$6:$G$8)", fmt=PERCENT)
            book.expect(INPUTS, f"H{row}", operations.defense_uplift_weights[segment])

    low, high = interim.guidance.revenue
    guidance = (
        (12, "July FY delivery outlook (aircraft)", evidence.july_deliveries, MONEY),
        (13, "July FY corporate costs", evidence.july_corporate_costs, MONEY),
        (14, "October FY sales outlook, low", low, MONEY),
        (15, "October FY sales outlook, high", high, MONEY),
        (16, "October FY consolidated margin", interim.guidance.operating_margin, PERCENT),
    )
    for row, label, value, fmt in guidance:
        put(f"A{row}", label)
        put(f"B{row}", value, kind="actual", fmt=fmt)

    calculations = (
        (11, "October FY sales outlook, midpoint", '=AVERAGE(B14:B15)', MONEY),
        (17, "Case direction", '=MATCH(Cover!$E$13,$A$40:$A$42,0)-2', '0'),
        (18, "Selected FY sales target", '=CHOOSE(B17+2,B14,B11,B15)', MONEY),
        (19, "Implied Q4 sales", f"=B18-{REVENUE_REF}O64", MONEY),
        (20, "Residual sales allocated to defense", f"=B19-SUM({REVENUE_REF}P8:P9)-SUM(F6:F8)", MONEY),
        (21, "Uniform FY segment margin adjustment",
         f"=(B18*B16+B13-SUMPRODUCT({REVENUE_REF}G60:G63,C5:C8))/B18", PERCENT),
    )
    for row, label, formula, fmt in calculations:
        put(f"A{row}", label)
        put(f"B{row}", formula, kind="total", fmt=fmt)
    put("A22", "Forward corporate cost growth")
    put("B22", assumptions.corporate_cost_growth, kind="input", fmt=PERCENT)

    put("A23", "Prior-year Q4 product mix | USD millions", kind="section")
    for column, header in zip("ABCDE", ("Product", "Prior 9M", "Prior FY", "Prior Q4", "Segment mix"), strict=True):
        put(f"{column}24", header, kind="header")
    for segment, products in PRODUCTS.items():
        first, last = PRIOR_MIX_ROWS[products[0]], PRIOR_MIX_ROWS[products[-1]]
        for product in products:
            row = PRIOR_MIX_ROWS[product]
            put(f"A{row}", PRODUCT_NAMES[product])
            put(f"B{row}", evidence.prior_products_ytd[product], kind="actual", fmt=MONEY)
            put(f"C{row}", f"={REVENUE_REF}F{product}", fmt=MONEY)
            put(f"D{row}", f"=C{row}-B{row}", fmt=MONEY)
            if segment != "aerospace":
                put(f"E{row}", f"=D{row}/SUM($D${first}:$D${last})", fmt=PERCENT)

    put("A38", "Forecast Gulfstream deliveries | aircraft", kind="section")
    for column, year in zip("BCDEF", range(operations.as_of.year + 1, operations.as_of.year + 6), strict=True):
        put(f"{column}39", year, kind="header", fmt='0"E"')
    for row, case in enumerate(("bear", "base", "bull"), 40):
        put(f"A{row}", case.title(), kind="header")
        for column, deliveries in zip("BCDEF", assumptions.deliveries[case], strict=True):
            put(f"{column}{row}", deliveries, kind="input", fmt=MONEY)

    put("A44", "Forward product growth assumptions", kind="section")
    for column, label in zip("ABCD", ("Driver", "First year", "Final year", "Case spread"), strict=True):
        put(f"{column}45", label, kind="header")
    for settings in assumptions.segments.values():
        for driver, growth in settings.growth.items():
            row = PATH_ROWS[driver]
            put(f"A{row}", GROWTH_ROWS[driver][1])
            for column, amount in zip("BCD", (growth.first, growth.last, growth.case_spread), strict=True):
                put(f"{column}{row}", amount, kind="input", fmt=PERCENT)

    put("A57", "Forward segment margin assumptions", kind="section")
    for column, label in zip("ABC", ("Segment", "Final margin", "Case spread"), strict=True):
        put(f"{column}58", label, kind="header")
    for row, (segment, label, _, _, _, _) in enumerate(SEGMENTS, 59):
        settings = assumptions.segments[segment]
        put(f"A{row}", label)
        put(f"B{row}", settings.final_margin, kind="input", fmt=PERCENT)
        put(f"C{row}", settings.margin_spread, kind="input", fmt=PERCENT)

    put("A64", "Sources and assumption rationale", kind="section")
    for column, label in zip("ABCD", ("Input group", "Available on", "Citation and rationale", "Source URL"), strict=True):
        put(f"{column}65", label, kind="header")
    _source(book, 66, "Nine-month and Q3 operating facts", evidence.sources["products"])
    _source(book, 67, "July guidance", evidence.sources["july_guidance"])
    _source(book, 68, "October guidance", interim.filing)
    _source(book, 69, "Forward delivery paths", assumptions.delivery_source)
    _source(book, 70, "Forward corporate cost growth", assumptions.corporate_source)
    for row, (segment, label, _, _, _, _) in enumerate(SEGMENTS, 71):
        _source(book, row, f"{label} growth and margins", assumptions.segments[segment].source)


def _revenue_labels(book, operations):
    book.put(REVENUE, "A1", "General Dynamics Corporation", kind="title")
    book.put(REVENUE, "A2", "Bottom-up revenue and operating earnings", kind="section")
    book.put(REVENUE, "A3", "USD millions except aircraft, per-delivery revenue and percentages", kind="note")
    book.put(REVENUE, "A5", "Fiscal period", kind="header")
    book.put(REVENUE, "O4", operations.interim_period_end, kind="actual", fmt="yyyy-mm-dd")
    book.put(REVENUE, "P4", date(operations.as_of.year, 12, 31), kind="header", fmt="yyyy-mm-dd")
    book.put(REVENUE, "O5", "9M actual", kind="header")
    book.put(REVENUE, "P5", "Q4 estimate", kind="header")
    for product, label in PRODUCT_NAMES.items():
        book.put(REVENUE, f"A{product}", label)
    for _, label, section, revenue_row, earnings_row, margin_row in SEGMENTS:
        book.put(REVENUE, f"A{section}", label, kind="section")
        book.put(REVENUE, f"A{revenue_row}", f"Total {label} revenue", kind="total")
        book.put(REVENUE, f"A{earnings_row}", "Operating earnings", kind="total")
        book.put(REVENUE, f"A{margin_row}", "Operating margin")
    for row, label in GROWTH_ROWS.values():
        book.put(REVENUE, f"A{row}", label)
    for row, label in (
        (13, "Gulfstream deliveries"), (14, "Delivery growth"),
        (15, "Blended manufacturing revenue per delivery"), (17, "Aircraft manufacturing revenue growth"),
        (66, "Revenue growth"), (73, "Corporate operating costs"), (75, "Consolidated operating margin"),
    ):
        book.put(REVENUE, f"A{row}", label)
    for row, label in ((59, "Consolidated revenue"), (68, "Consolidated operating earnings"), (80, "Backlog")):
        book.put(REVENUE, f"A{row}", label, kind="section")
    for index, (_, label, _, _, _, _) in enumerate(SEGMENTS):
        for start in (60, 69, 81):
            book.put(REVENUE, f"A{start + index}", label)
    for row, label in ((64, "Total consolidated revenue"), (74, "Total operating earnings"), (85, "Reported backlog")):
        book.put(REVENUE, f"A{row}", label, kind="total")
    book.put(REVENUE, "A87", "Backlog is reported at the interim date. The workbook carries no forecast backlog balance.", kind="note")


def _rollup(book, column, prior=None):
    def formula(row, expression, fmt=MONEY, kind=None):
        book.put(REVENUE, f"{column}{row}", f"={expression}", kind=kind, fmt=fmt)

    for index, (segment, _, _, revenue_row, earnings_row, margin_row) in enumerate(SEGMENTS):
        products = ",".join(f"{column}{product}" for product in PRODUCTS[segment])
        formula(revenue_row, f"SUM({products})", kind="total")
        if column in "BCDEFOP":
            formula(margin_row, f"{column}{earnings_row}/{column}{revenue_row}", PERCENT)
        formula(60 + index, f"{column}{revenue_row}")
        formula(69 + index, f"{column}{earnings_row}")
    formula(64, f"SUM({column}60:{column}63)", kind="total")
    formula(74, f"SUM({column}69:{column}73)", kind="total")
    formula(75, f"{column}74/{column}64", PERCENT)
    if column in "BCDEFGOP":
        formula(15, f"{column}8/{column}13", "0.00")
    if prior:
        for row, source_row in ((14, 13), (17, 8), (66, 64)):
            formula(row, f"{column}{source_row}/{prior}{source_row}-1", PERCENT)
        if column in "CDEFG":
            for driver, (row, _) in GROWTH_ROWS.items():
                formula(row, f"{column}{driver}/{prior}{driver}-1", PERCENT)


def build_revenue(book, data):
    """Build one active case from sourced historical data and editable forecast assumptions."""
    operations, interim, history = data.operations, data.interim, data.history
    _operating_inputs(book, data)
    _revenue_labels(book, operations)

    def put(column, row, value, *, kind=None, fmt=MONEY):
        book.put(REVENUE, f"{column}{row}", value, kind=kind, fmt=fmt)

    historical_rows = (*PRODUCT_LABELS, 13, 19, 31, 45, 56, 73)
    for index, (column, year) in enumerate(zip("BCDEF", history.historical_years, strict=True)):
        put(column, 5, year, kind="header", fmt='0"A"')
        for row in historical_rows:
            address = f"{REVENUE}!{column}{row}"
            put(column, row, history.history[address], kind="actual")
        _rollup(book, column, "BCDEF"[index - 1] if index else None)

    for product, value in operations.evidence.products_ytd.items():
        put("O", product, value, kind="actual")
    put("O", 13, interim.ytd["deliveries"], kind="actual")
    put("O", 73, -interim.ytd["corporate_operating_costs"], kind="actual")
    for index, (segment, _, _, _, earnings_row, _) in enumerate(SEGMENTS):
        put("O", earnings_row, interim.segments[segment]["operating_earnings"], kind="actual")
        put("O", 81 + index, interim.segments[segment]["backlog"], kind="actual")
    put("O", 85, "=SUM(O81:O84)", kind="total")
    _rollup(book, "O")
    book.expect(REVENUE, "O64", interim.ytd["revenue"])
    book.expect(REVENUE, "O74", interim.ytd["operating_earnings"])
    book.expect(REVENUE, "O85", interim.period_end_balances["backlog"])

    put("P", 13, f"={INPUT_REF}$B$12-O13")
    put("P", 8, "=P13*O15")
    services = PRIOR_MIX_ROWS["9"]
    put("P", 9, f"={INPUT_REF}$D${services}*O9/{INPUT_REF}$B${services}")
    for row, (segment, _, _, _, _, _) in enumerate(SEGMENTS[1:], 6):
        for product in PRODUCTS[segment]:
            mix = PRIOR_MIX_ROWS[product]
            put("P", product, f"=({INPUT_REF}$F${row}+{INPUT_REF}$H${row}*{INPUT_REF}$B$20)*{INPUT_REF}$E${mix}")
    for _, _, _, _, earnings_row, _ in SEGMENTS:
        put("P", earnings_row, f"=G{earnings_row}-O{earnings_row}", kind="total")
    put("P", 73, "=G73-O73")
    _rollup(book, "P")

    for offset, column in enumerate("GHIJKL"):
        prior = "FGHIJK"[offset]
        put(column, 5, operations.as_of.year + offset, kind="header", fmt='0"E"')
        if offset == 0:
            for row in (*PRODUCT_LABELS, 13):
                put(column, row, f"=O{row}+P{row}")
            put(column, 73, f"=-{INPUT_REF}$B$13")
        else:
            delivery_col = "BCDEF"[offset - 1]
            put(column, 13, f"=INDEX({INPUT_REF}{delivery_col}$40:{delivery_col}$42,{INPUT_REF}$B$17+2)")
            for driver, (growth_row, _) in GROWTH_ROWS.items():
                row = PATH_ROWS[driver]
                put(column, growth_row,
                    f"={INPUT_REF}$B${row}+({INPUT_REF}$C${row}-{INPUT_REF}$B${row})*({offset - 1}/4)"
                    f"+{INPUT_REF}$B$17*{INPUT_REF}$D${row}", fmt=PERCENT)
                put(column, driver, f"={prior}{driver}*(1+{column}{growth_row})",
                    fmt="0.00" if driver == "15" else MONEY)
            put(column, 8, f"={column}13*{column}15")
            put(column, 73, f"={prior}73*(1+{INPUT_REF}$B$22)")

        for index, (_, _, _, revenue_row, earnings_row, margin_row) in enumerate(SEGMENTS):
            if offset == 0:
                margin = f"={INPUT_REF}$C${5 + index}+{INPUT_REF}$B$21"
            else:
                row = 59 + index
                margin = (f"=$G{margin_row}+({INPUT_REF}$B${row}-$G{margin_row})*({offset}/5)"
                          f"+{INPUT_REF}$B$17*{INPUT_REF}$C${row}")
            put(column, margin_row, margin, fmt=PERCENT)
            put(column, earnings_row, f"={column}{revenue_row}*{column}{margin_row}", kind="total")
        _rollup(book, column, prior)

    for case, result in operations.cases.items():
        book.expect(INPUTS, "B21", result.margin_adjustment, case=case)
        annual = (result.annual[operations.as_of.year + offset] for offset in range(6))
        for column, period in zip("PGHIJKL", (result.q4, *annual), strict=True):
            values = {
                **period.products_millions, 13: period.deliveries,
                15: period.manufacturing_revenue_per_delivery_millions,
                64: period.consolidated.revenue_millions, 73: -period.corporate_costs_millions,
                74: period.consolidated.operating_earnings_millions, 75: period.consolidated.operating_margin,
            }
            for segment, _, _, revenue_row, earnings_row, margin_row in SEGMENTS:
                forecast = period.segments[segment]
                values.update({revenue_row: forecast.revenue_millions,
                               earnings_row: forecast.operating_earnings_millions,
                               margin_row: forecast.operating_margin})
            for row, value in values.items():
                book.expect(REVENUE, f"{column}{row}", value, case=case)

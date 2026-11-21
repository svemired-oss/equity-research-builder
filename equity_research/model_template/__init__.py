"""The shared formula workbook for U.S. operating-company research."""

from datetime import date
import re

from ..apple_export import _sources, workbook_plan as legacy_plan
from ..operating_research import ResearchGap
from ..workbook_renderer import render_workbook
from .statements import BALANCE, CASH, INCOME, build_statements

SHEETS = ("Cover", "Assumptions", "Revenue Build", INCOME, BALANCE, CASH, "Schedules",
          "Cost of Equity", "DCF", "Scenarios", "Sensitivity", "WACC", "Comps", "Football Field", "Checks", "Sources")
PCT = "0.00%"
PRICE = '"$"0.00'


def ordered_sheets(cells):
    """Place common presentation tabs before issuer detail tabs."""
    return {name:cells[name] for name in (*SHEETS,*(tab for tab in cells if tab not in SHEETS)) if name in cells}


class Template:
    def __init__(self, plan):
        self.cells = plan["cells"]
        self.expected = plan["expected"]
        self.sources = {}
        self.cells["Sources"] = {}
        self.put("Sources", "A1", "Input source register", "title")
        for col, label in zip("ABCD", ("Input or reference", "Value", "Source URL", "Period / basis"), strict=True):
            self.put("Sources", f"{col}3", label, "header")

    def put(self, tab, address, value, kind="text", fmt=None):
        self.cells.setdefault(tab, {})[address] = [value, kind, fmt]

    def formulas(self, tab, col, rows, *, kind="text"):
        for row, formula in rows.items():
            self.put(tab, f"{col}{row}", "=" + formula, kind)

    def cite(self, tab, address, url, basis):
        if not (url or basis):
            raise ValueError(f"Missing source basis for {tab}!{address}.")
        self.sources[(tab, address)] = (url, basis)

    def input(self, tab, address, value, url=None, basis="", fmt=None):
        self.put(tab, address, value, "input", fmt)
        self.cite(tab, address, url, basis)

    def write_sources(self, model):
        # Store hyperlinks separately from input values so every input remains editable.
        for index, ((tab, address), (url, basis)) in enumerate(self.sources.items(), 4):
            self.put("Sources", f"A{index}", f"{tab}!{address}")
            self.put("Sources", f"B{index}", f"='{tab}'!{address}", fmt=self.cells[tab][address][2])
            self.put("Sources", f"C{index}", url or "")
            self.put("Sources", f"D{index}", basis, "note")
        row = 6 + len(self.sources)
        for name, url in _sources(model).items():
            self.put("Sources", f"A{row}", name)
            self.put("Sources", f"C{row}", url)
            row += 1
        for note in model.get("notes", ()):
            if note:
                self.put("Sources", f"D{row}", note, "note")
                row += 1
        return {tab: {address: f"A{row}" for row, (target, address) in enumerate(self.sources, 4)
                      if target == tab} for tab in self.cells}


def _legacy_sources(book, model, revenue_total):
    sources = model["sources"]
    annual = sources["Annual financials and revenue lines"]
    bridge = sources["Latest balance sheet"]
    price = next(url for name, url in sources.items() if name.startswith("Closing price"))
    treasury = next(url for name, url in sources.items() if name.startswith("Treasury yield"))
    book.cite("Cover", "B5", None, "Analyst scenario selector")
    book.cite("Cover", "B6", price, f"Dated market close | {model['price_date']}")
    assumptions = {
        3: (annual, "Three-year median normalized operating margin | calculation on Assumptions rows 50-59"),
        4: (annual, "Three-year median effective tax rate"),
        5: (annual, "Three-year median D&A / revenue"),
        6: (annual, "Historical capex ratio with D&A floor and available interim evidence"),
        9: (None, "Analyst assumption | terminal growth"), 10: (None, "Analyst assumption | incremental ROIC for forecast reinvestment and the ROIC-based terminal check"),
        13: (None, "Analyst assumption | annual growth convergence"),
        15: (next((url for label, url in sources.items()
                   if label.startswith(("FY revenue guidance", "Reported current YTD sales"))), None),
             "Filed full-year guidance or comparable interim anchor. See source notes."
             if model["revenue_anchor"] is not None else "No filed revenue anchor applied"),
        16: (annual, "Median of three filed annual payouts or explicit zero payout assumption"),
        17: (None, "Analyst assumption | margin shift capped at 25% of base margin or three points"),
    }
    overrides = {item["target"]: item["reason"] for item in model.get("overrides", ())}
    for row, (url, basis) in assumptions.items():
        target = {3: "margin", 6: "capex", 9: "growth", 10: "roic"}.get(row)
        if target in overrides:
            url, basis = None, f"Guided analyst assumption | {overrides[target]}"
        if row == 16 and model["payout_ratio"] is None:
            url, basis = None, "Analyst assumption | no modeled dividends when payout evidence is incomplete"
        book.cite("Assumptions", f"B{row}", url, basis)
    for row, url, basis in ((3, treasury, "Dated Treasury rate"), (4, None, "Analyst equity risk premium"),
                            (5, None, "Analyst beta proxy"), (10, None, "Analyst borrowing spread proxy")):
        book.cite("WACC", f"B{row}", url, basis)
    for row, claim in ((6, "Cash"), (7, "Investments"), (8, "Funded debt"), (9, "Noncontrolling interest"),
                       (10, "Preferred equity"), (12, "Common shares")):
        value = book.cells["DCF"][f"B{row}"][0]
        if isinstance(value, (int, float)):
            book.cite("DCF", f"B{row}", bridge, f"Latest reconciled bridge | {claim}; see balance evidence")
    for col in "GHIJ":
        if col in "IJ" and not model.get("comparable_revenue_interim"):
            continue
        url = (model["comparable_revenue_interim"]["source"] if col in "IJ"
               else sources.get("Full-year acquisition pro forma sales", annual))
        for row in range(4, revenue_total):
            book.cite("Revenue Build", f"{col}{row}", url, "Selected reconciled revenue group")
    if model.get("capital_movements"):
        for col in "BCD":
            book.cite("Equity bridge", f"{col}5", bridge, "Opening filed balance")
        for row, event in enumerate(model["capital_movements"], 6):
            for col in "BCD":
                book.cite("Equity bridge", f"{col}{row}", event["url"], f"Completed {event['kind']} | {event['closed_on']}")
    comps = model.get("comparables")
    if comps is not None:
        for row, peer in enumerate(comps["rows"], 5):
            book.cite("Comps", f"C{row}", peer["quote_source"], f"USD close | {comps['quote_date']}")
            book.cite("Comps", f"D{row}", peer["filings"][0], f"Earnings ending {peer['end']}. See all peer filings below.")
        book.cite("Comps", "B13", comps["subject_filings"][0], "Subject diluted EPS. See all subject filings below.")
    for row, entry in enumerate(model.get("balance_evidence", ()), 5):
        evidence = entry["source"]
        book.cite("Balance evidence", f"C{row}", evidence.get("url") or bridge,
                  evidence.get("basis") or evidence.get("kind") or entry["metric"])
    for row, change in enumerate(model.get("overrides", ()), 4):
        book.cite("Guided changes", f"C{row}", None, f"Guided analyst assumption | {change['reason']}")
        book.cite("Guided changes", f"B{row}", annual, "Previous model default before Guided change")


def _sensitivity(book, model):
    from ..fcfe_valuation import value_fcfe
    from .equity import BRIDGE, G, KE, SHARES, VALUE, YEARS, FLOW
    tab = "Sensitivity"
    book.put(tab, "A1", "FCFE DCF sensitivity | USD per share", "title")
    book.input(tab, "H3", .005, basis="docs/method-notes.md rule 4 | cost of equity step", fmt=PCT)
    book.put(tab, "G3", "Cost of equity step")
    book.input(tab, "H5", .0025, basis="docs/method-notes.md rule 4 | terminal growth step", fmt=PCT)
    book.put(tab, "G5", "Terminal growth step")
    book.input(tab, "H4", .01, basis="Analyst assumption | sensitivity EBIT margin and revenue growth step", fmt=PCT)
    book.put(tab, "G4", "Margin / annual revenue growth step")
    for row in range(26, 33):
        for col in "ABCDEF":
            book.cells["DCF"].pop(f"{col}{row}", None)
            book.expected["DCF"].pop(f"{col}{row}", None)
    tax = "Assumptions!$B$4"
    book.put(tab, "A26", "Cost of equity × terminal growth", "section")
    inputs, revenues, margin = model["fcfe_inputs"], model["revenue"], model["margin"]
    book.expected[tab] = {}
    for offset, col in enumerate("BCDEF", -2):
        book.put(tab, f"{col}27", f"=DCF!$B${G}+({offset})*$H$5", "header", PCT)
    for row, offset in enumerate(range(-2, 3), 28):
        book.put(tab, f"A{row}", f"=DCF!$B${KE}+({offset})*$H$3", "header", PCT)
        for step, col in enumerate("BCDEF", -2):
            pv = "+".join(f"DCF!${year}${FLOW}/(1+$A{row})^DCF!${year}${YEARS}" for year in "BCDEF")
            terminal = f"DCF!$F${FLOW}*(1+{col}$27)/($A{row}-{col}$27)/(1+$A{row})^DCF!$F${YEARS}"
            book.put(tab, f"{col}{row}", f"=({pv}+{terminal}+{BRIDGE})/DCF!$B${SHARES}", fmt=PRICE)
            book.expected[tab][f"{col}{row}"] = value_fcfe(
                inputs, revenues, margin, model["cost_of_equity"] + offset*.005, model["growth"] + step*.0025).value_per_share
    book.put("DCF", "A30", "Cost of equity × terminal growth sensitivity center")
    book.put("DCF", "D30", "=Sensitivity!D30", "total", PRICE)
    book.expected["DCF"]["D30"] = book.expected[tab]["D30"]

    book.put(tab, "A6", "EBIT margin × annual revenue growth adjustment", "section")
    book.put(tab, "A7", "Capex ratios, working capital ratios, cost of equity and terminal growth are held at the selected case.")
    for offset, col in enumerate("BCDEF", -2):
        book.put(tab, f"{col}9", f"={offset}*$H$4", "header", PCT)
    for row, offset in enumerate(range(-2, 3), 10):
        book.put(tab, f"A{row}", f"=SUM(Assumptions!$B$3,Assumptions!$B$11)+({offset})*$H$4", "header", PCT)
        for col in "BCDEF":
            # Each grid result uses a visible five-year scenario row below the grids.
            start = 36 + (row-10)*5 + "BCDEF".index(col)
            book.put(tab, f"A{start}", f"=A{row}", fmt=PCT)
            book.put(tab, f"B{start}", f"={col}$9", fmt=PCT)
            for i, out in enumerate("CDEFG"):
                case_col = "BCDEF"[i]
                last = f"'{INCOME}'!$L$4" if i == 0 else f"'{INCOME}'!{'ABCDE'[i]}4"
                # Apply a percentage-point growth shock to the base year-on-year rate.
                sales = f"'{INCOME}'!{case_col}4"
                previous = f"'{INCOME}'!$L$4" if i == 0 else f"{'CDEF'[i-1]}{start}"
                book.put(tab, f"{out}{start}", f"={previous}*({sales}/{last}+$B{start})")
            for i, out in enumerate("HIJKL"):
                case_col = "BCDEF"[i]
                sales = f"{'CDEFG'[i]}{start}"
                previous = f"'{INCOME}'!$L$4" if i == 0 else f"{'CDEF'[i-1]}{start}"
                capex = f"Schedules!{case_col}5/'{INCOME}'!{case_col}4"
                formula = (f"{sales}*($A{start}*(1-{tax})+Assumptions!$B$5-{capex})"
                           f"-({sales}*Assumptions!$B$32-"
                           + ("Schedules!$L$12" if i==0 else f"{previous}*Assumptions!$B$32") + ")"
                           f"+('{INCOME}'!{case_col}14-'{INCOME}'!{case_col}6)*(1-{tax})")
                stub = "*Assumptions!$B$12" if not i else ""
                borrowing = f"'{CASH}'!{case_col}18-'{CASH}'!{case_col}19" + ("-Schedules!$B$40" if not i else "")
                book.put(tab, f"{out}{start}", f"=(({formula}){stub}+{borrowing})/(1+DCF!$B${KE})^DCF!{case_col}20")
            # Gordon growth on the grid's final-year FCFE; L is already discounted by the same factor.
            terminal = f"L{start}*(1+DCF!$B${G})/(DCF!$B${KE}-DCF!$B${G})"
            book.put(tab, f"M{start}", f"={terminal}")
            book.put(tab, f"{col}{row}", f"=(SUM(H{start}:M{start})+{BRIDGE})/DCF!$B${SHARES}", fmt=PRICE)
    for col, label in zip("ABCDEFGHIJKLM", (
        "EBIT margin", "Growth shock", *(f"Revenue Y{i}" for i in range(1, 6)),
        *(f"PV of FCFE Y{i}" for i in range(1, 6)), "PV of terminal value",
    ), strict=True):
        book.put(tab, f"{col}35", label, "header")
    book.expected[tab]["D12"] = model["fcfe"]["Base"].value_per_share
    book.put("Checks", "B11", f"=Sensitivity!D12-DCF!B{VALUE}", "check")
    book.put("Checks", "B12", f"=Sensitivity!D30-DCF!B{VALUE}", "check")
    book.put("Checks", "A11", "Margin × growth sensitivity center difference")
    book.put("Checks", "A12", "Cost of equity × terminal growth sensitivity center difference")
    book.expected["Checks"].update(B11=0, B12=0)


def _football_field(book):
    tab = "Football Field"
    book.put(tab, "A1", "Valuation ranges | USD per share", "title")
    for col, label in zip("ABCD", ("Method", "Low", "High", "Reference"), strict=True):
        book.put(tab, f"{col}4", label, "header")
    for row, label, low, high, reference in (
        (5, "FCFE DCF | cost of equity × terminal growth", "MIN(Sensitivity!B28:F32)", "MAX(Sensitivity!B28:F32)", "DCF!B123"),
        (6, "FCFE DCF | margin × growth", "MIN(Sensitivity!B10:F14)", "MAX(Sensitivity!B10:F14)", "DCF!B123"),
    ):
        book.put(tab, f"A{row}", label)
        for col, formula in zip("BCD", (low, high, reference), strict=True):
            book.put(tab, f"{col}{row}", "=" + formula, fmt=PRICE)
    book.put(tab, "A7", "Peer-implied P/E range")
    if "B14" in book.cells.get("Comps", {}):
        for col, formula in (("B", "MIN(Comps!E5:E9)*Comps!B13"),
                             ("C", "MAX(Comps!E5:E9)*Comps!B13"), ("D", "Comps!B14")):
            book.put(tab, f"{col}7", f'=IF(ISNUMBER(Comps!B14),{formula},"Unavailable")', fmt=PRICE)
    else:
        book.put(tab, "B7", "Unavailable")
    # In-cell bars stay formula driven in both Excel and Calc, including negative ranges.
    book.put(tab, "A10", "Range chart")
    book.put(tab, "G4", "Reference close")
    book.put(tab, "G5", "=Cover!B6", fmt=PRICE)
    for index, col in enumerate("EFGHIJKLMNOPQRSTUVW"):
        book.put(tab, f"{col}12", f"=MIN(B5:B7)+(MAX(C5:C7)-MIN(B5:B7))*{index}/18", "header", PRICE)
        for chart_row, source in enumerate((5, 6, 7), 13):
            book.put(tab, f"A{chart_row}", f"=A{source}")
            book.put(tab, f"{col}{chart_row}",
                     f'=IF(AND(ISNUMBER($B{source}),{col}$12>=$B{source},{col}$12<=$C{source}),"■","")')


def _implied_margin(book, model):
    """Constant EBIT margin at which the FCFE value equals the close, other inputs fixed."""
    from .equity import G, KE, SHARES, VALUE, YEARS
    pv = "+".join(f"'{INCOME}'!{c}4/(1+DCF!B{KE})^DCF!{c}{YEARS}" + ("*Assumptions!B12" if c == "B" else "")
                  for c in "BCDEF")
    terminal = f"'{INCOME}'!F4*(1+DCF!B{G})/(DCF!B{KE}-DCF!B{G})/(1+DCF!B{KE})^DCF!F{YEARS}"
    book.put("DCF", "A36", "Market-implied operating margin (FCFE)")
    book.put("DCF", "B36", f"=Assumptions!B3+Assumptions!B11+(Cover!B6-DCF!B{VALUE})*DCF!B{SHARES}"
                           f"/((1-Assumptions!B4)*({pv}+{terminal}))", "total", PCT)
    book.expected["DCF"]["B36"] = model["implied_margin"]


def workbook_plan(model):
    if not model.get("normalize_capex") or not 3 <= len(model["history"][-5:]) <= 5:
        raise ResearchGap("The operating template needs three to five annual periods and reconciled forecast inputs.")
    plan = legacy_plan(model)
    cells = plan["cells"]
    cells.pop("Actuals")
    cells["Revenue Build"] = cells.pop("Revenue")
    plan["expected"]["Revenue Build"] = plan["expected"].pop("Revenue")
    for tab in ("Income statement", "Balance sheet", "Cash flow"):
        cells.pop(tab)
    for tab in cells.values():
        for record in tab.values():
            value = record[0]
            if isinstance(value, str) and value.startswith("="):
                record[0] = value.replace("'Income statement'!", f"'{INCOME}'!").replace("'Cash flow'!", f"'{CASH}'!")
    book = Template(plan)
    revenue_total = 4 + len(model["lines"])
    book.put("Cover", "A11", "Shared operating model | Phase 2")
    book.put("Assumptions", "A17", "Case margin shift")
    book.input("Assumptions", "B17", model["margin_step"], basis="Analyst case range", fmt=PCT)
    book.put("Assumptions", "B11", '=IF(Cover!B5="Bear",-B17,IF(Cover!B5="Bull",B17,0))', fmt=PCT)
    if model["payout_ratio"] is None:
        book.put("Assumptions", "B16", 0, "input", PCT)
    if model["revenue_anchor"] is None:
        book.put("Assumptions", "B15", "Historical forecast", "evidence")
    book.put("Assumptions", "B12", "=(DCF!B18-DCF!B17)/(DCF!B18-DCF!B19)", fmt=PCT)
    book.put("DCF", "A24", "Forecast fiscal close")
    book.put("DCF", "A25", "Discount day count convention")
    book.input("DCF", "B25", 365, basis="Analyst convention | ACT/365")
    annual_url = model["sources"]["Annual financials and revenue lines"]
    excel_epoch = date(1899, 12, 30)
    for row, label, when in ((17, "Valuation cutoff", model["as_of"]), (18, "First forecast close", model["dates"][0]),
                             (19, "Annual opening close", model["history"][-1]["period_end"])):
        book.put("DCF", f"A{row}", label)
        iso = str(when)
        if row == 18:
            book.put("DCF", "B18", "=B24", fmt="yyyy-mm-dd")
        else:
            book.input("DCF", f"B{row}", (date.fromisoformat(iso)-excel_epoch).days,
                       annual_url if row == 19 else None,
                       f"{iso} | {'Valuation cutoff' if row == 17 else 'Filed annual fiscal close'}", "yyyy-mm-dd")
    for col, when in zip("BCDEF", model["dates"], strict=True):
        book.input("DCF", f"{col}24", (when-excel_epoch).days, annual_url,
                   f"{when} | forecast extension of the filed fiscal calendar", "yyyy-mm-dd")
        book.put("DCF", f"{col}20", f"=({col}24-$B$17)/$B$25")
    book.put("WACC", "A10", "Borrowing spread")
    book.put("WACC", "B10", model["cost"].pretax_cost_of_debt-model["cost"].risk_free_rate, "input", PCT)
    book.put("WACC", "B6", "=SUM(B3,B10)", fmt=PCT)
    build_statements(book, model, revenue_total)
    from . import equity
    equity.assumptions(book, model)
    equity.case_revenue(book, model, revenue_total)
    equity.cash_flow(book)
    book.put("Assumptions", "B7", "=SUM('Balance Sheet'!L6:L8)/'Income Statement'!L4", fmt=PCT)
    book.put("Assumptions", "B8", "=SUM('Balance Sheet'!L17:L20)/'Income Statement'!L4", fmt=PCT)
    book.put("Assumptions", "A32", "Forecast operating working capital / sales")
    book.put("Assumptions", "B32", "=B7*B30-B8*B31", fmt=PCT)
    book.put("Assumptions", "B14", "=B5+B3*(1-B4)*B9/B10-B9*B32", fmt=PCT)
    _legacy_sources(book, model, revenue_total)
    from ..drivers import formula_block
    formula_block(book, model)
    from . import forecast
    forecast.build(book, model, revenue_total)
    for address, record in cells["Revenue Build"].items():
        if address[0] in "GHIJ" and type(record[0]) in (int,float):
            record[1] = "evidence"
    equity.dcf_block(book, model)
    if model.get("beta"):
        equity.cost_of_equity(book, model)
    equity.scenarios(book, model, revenue_total)
    _implied_margin(book, model)
    _sensitivity(book, model)
    if "Comps" not in cells:
        book.put("Comps", "A1", "Comparable valuation", "title")
        book.put("Comps", "A3", "Verified comparable evidence is unavailable.")
    _football_field(book)
    if "valuation_summary" in model:
        from .valuation import add_valuation
        add_valuation(book, model)
    from .report_inputs import add_report_inputs
    add_report_inputs(book, model, revenue_total)
    plan["inputSources"] = book.write_sources(model)
    plan["formulaColor"] = "000000"
    plan["cells"] = ordered_sheets(cells)
    plan["recalculationProbe"]["unchanged"] = [{"sheet": INCOME, "address": "L4"}]
    plan["recalculationProbe"]["changed"] = [{"sheet": "DCF", "address": "B123"}]
    for name, expectations in plan["scenarioExpected"].items():
        expectations["DCF"] = {"B15": model["scenarios"][name].value_per_share,
                               "B123": model["fcfe"][name].value_per_share}
        expectations["Cover"] = {"B7": model["fcfe"][name].value_per_share}
    plan["recalculationProbe"]["expected"] = {"Checks": {key: 0 for key in book.expected["Checks"]
                                                       if key[0] in "BCDEF"}}
    for expectations in plan["scenarioExpected"].values():
        expectations["Checks"] = plan["recalculationProbe"]["expected"]["Checks"]
    return plan


def export_operating(model, folder, node=None, modules=None, *, report_writer=None, overrides=(), plan=None):
    """Use the portable LibreOffice workflow, including scenario and assumption-edit validation."""
    workbook = folder / f"{model['ticker']}-{model['as_of']}.xlsx"
    from .guided import apply, fields
    plan = workbook_plan(model) if plan is None else plan
    plan["guidedBounds"] = fields(plan, model)
    validator = apply(plan, model, overrides) if overrides else None
    render_workbook(plan, workbook, validate=validator)
    pdf = workbook.with_suffix(".pdf")
    from ..operating_report import export_report
    export_report(workbook, pdf, report_writer)
    return workbook, pdf


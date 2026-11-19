"""Create and verify the paired private Apple deliverables."""

from dataclasses import replace
from hashlib import sha256

from .apple_pilot import AS_OF, CLOSE, LINES, MARGIN_SHIFT
from .fcff_valuation import discount_fcff


PCT = "0.00%"
PRICE = '"$"0.00'
NOTES = (
    "Each revenue line starts with its filed FY2026 sales and historical year-over-year growth. Growth moves halfway toward the terminal rate each year.",
    "Base EBIT margin uses the median of three fiscal years. Bear and bull shift it by three percentage points each year.",
    "The model uses a 17% tax rate, 1.15 beta, 5.50% pretax debt cost, 2.50% terminal growth, and 25% terminal return on incremental capital.",
    "The first DCF period prorates full-year FCFF by remaining days. The equity bridge uses the September balance sheet and holds cash, investments, debt and share count through the valuation date.",
    "The annual statements assume constant book debt and no forecast financing flows. Buybacks, dividends, acquisitions and new funding are outside this forecast. Depreciation and amortization are modeled together.",
    "The workbook uses publicly cited dated prices. The headline valuation has no peer-multiple cross-check.",
)


def workbook_plan(model):
    cells, expected = {}, {"Checks": {}, "Cash flow": {}, "DCF": {}, "Revenue": {}}
    lines = model.get("lines", LINES)
    as_of = model.get("as_of", AS_OF)
    close = model.get("close", CLOSE)
    total_row = 4 + len(lines)
    notes = model.get("notes", NOTES)
    normalize_capex = model.get("normalize_capex", False)
    margin_step = model.get("margin_step", .03)
    rating = 'IF(B8>=0.15,"Buy",IF(B8<=-0.15,"Sell","Hold"))'
    if "implied_margin" in model:
        rating = f'IF(ABS(B8)>=0.5,"Review",{rating})'

    def sheet(name, rows, col="A", start=1):
        tab = cells.setdefault(name, {})
        for index, value in enumerate(rows, start):
            if isinstance(value, tuple):
                value, style, number = value
            else:
                style, number = ("title" if col == "A" and index == 1 else "text"), None
            tab[f"{col}{index}"] = [value, style, number]

    base = model["scenarios"]["Base"]
    sheet("Cover", [model.get("company", "Apple Inc."), f"{model.get('ticker', 'AAPL')} | {as_of:%B %d, %Y} | Private prototype"])
    sheet("Cover", ["Scenario", "Reference close", "DCF fair value", "Upside to close", "Illustrative rating"], start=5)
    sheet("Cover", [("Base", "input", None), (close, "input", PRICE), ("=DCF!B15", "total", PRICE),
                    ("=B7/B6-1", "total", PCT), ("=" + rating, "total", None)], "B", 5)
    metrics = ("revenue", "operating_income", "net_income", "operating_cash_flow", "investing_cash_flow",
               "financing_cash_flow", "total_assets", "total_liabilities", "equity_attributable_to_parent")
    sheet("Actuals", ["Historical statements", "USD millions"])
    sheet("Actuals", [key.replace("_", " ").title() for key in metrics], start=4)
    for col, row in zip("BCDEF", model["history"][-5:]):
        sheet("Actuals", [(row["period_end"], "header", None)] + [
            row["metrics"][key] / 1e6 if row["metrics"].get(key) is not None else "Unavailable" for key in metrics
        ], col, 3)
    sheet("Revenue", ["Revenue by product and service", "USD millions"])
    sheet("Revenue", [*lines.values(), ("Total revenue", "total", None)], start=4)
    for col, values in (("G", model["old_sales"]), ("H", model["sales"])):
        sheet("Revenue", [("Prior FY" if col == "G" else "Latest FY", "header", None),
                          *[values[key] for key in lines], f"=SUM({col}4:{col}{total_row-1})"], col, 3)
    comparable_interim = model.get("comparable_revenue_interim")
    growth_base, growth_recent = ("I", "J") if comparable_interim else ("G", "H")
    if comparable_interim:
        for column, key, label in (("I", "prior", "Prior comparable YTD"), ("J", "current", "Latest comparable YTD")):
            sheet("Revenue", [(label, "header", None), *[comparable_interim[key][line] for line in lines],
                              f"=SUM({column}4:{column}{total_row-1})"], column, 3)
    sheet("Assumptions", ["Forecast assumptions", "Historical ratios and analyst inputs"])
    sheet("Assumptions", ["Base EBIT margin", "Cash and book tax rate", "D&A / revenue", "Capex / revenue",
                          "Operating current assets / sales", "Operating current liabilities / sales",
                          "Terminal growth", "Terminal incremental ROIC", "Case margin shift",
                          "First-period cash flow fraction", "Growth convergence factor"], start=3)
    sheet("Assumptions", [(item, "input", PCT) for item in (
        model["margin"], model["cost"].marginal_tax_rate, model["da"], model["capex"], model["wc_assets"], model["wc_liabilities"],
        model["growth"], model["roic"],
    )] + [(f'=IF(Cover!B5="Bear",-{margin_step},IF(Cover!B5="Bull",{margin_step},0))', "text", PCT),
          (model["stub"], "input", PCT), (.5, "input", PCT)], "B", 3)
    if normalize_capex:
        sheet("Assumptions", ["Normalized capex / revenue"], start=14)
        sheet("Assumptions", [("=B5+B3*(1-B4)*B9/B10-B9*(B7-B8)", "text", PCT)], "B", 14)
        sheet("Assumptions", ["First-year consolidated revenue anchor", "Dividend payout ratio"], start=15)
        sheet("Assumptions", [(model["revenue_anchor"] if model["revenue_anchor"] is not None else "Historical forecast", "input", None),
                              (model["payout_ratio"] if model["payout_ratio"] is not None else "Unavailable", "input", PCT)], "B", 15)
        sheet("Revenue", ["Unanchored first-year sales"], start=total_row + 2)
        sheet("Revenue", [*lines.values(), "Unanchored total"], start=total_row + 3)
        for row in range(4, total_row):
            sheet("Revenue", [f"=H{row}*(1+Assumptions!$B$9+({growth_recent}{row}/{growth_base}{row}-1-Assumptions!$B$9)*Assumptions!$B$13)"],
                  "B", total_row + row - 1)
        sheet("Revenue", [f"=SUM(B{total_row+3}:B{2*total_row-2})"], "B", 2*total_row-1)
        sheet("Schedules", ["Forecast schedules", "USD millions", "",
                            "Operating current assets", "Operating current liabilities", "Net working capital",
                            "Opening net investment assets", "Cash capital expenditure", "D&A",
                            "Closing net investment assets", "Opening funded debt", "Net debt raised",
                            "Closing funded debt", "Dividends paid", "Closing shares (millions)"])
    sheet("Balance sheet", ["Projected balance sheet", "USD millions"])
    sheet("Balance sheet", ["Cash", "Marketable investments", "Operating current assets", "Net investment assets",
                            "Other assets", "Total assets", "Funded debt and finance leases", "Operating current liabilities",
                            "Other liabilities", "Equity", "Total liabilities and equity"], start=4)
    m = model["metrics"]
    sheet("Balance sheet", ["Annual filed basis", model["cash"], model["investments"], model["opening_wc_assets"],
                            m["net_ppe"], model["other_assets"], "=SUM(H4:H8)", model["debt"], model["opening_wc_liabilities"],
                            model["other_liabilities"], m["equity_attributable_to_parent"], "=SUM(H10:H13)"], "H", 3)
    sheet("Income statement", ["Projected income statement", "USD millions"])
    sheet("Income statement", ["Revenue", "Operating income", "Interest expense", "Pretax income", "Tax", "Net income"], start=4)
    sheet("Cash flow", ["Projected cash flow", "USD millions"])
    sheet("Cash flow", ["Net income", "Depreciation and amortization", "Operating working capital change", "Operating cash flow",
                        "Capital expenditures", "Financing cash flow", "Net change in cash", "Full-year FCFF", "Post-cutoff FCFF"], start=4)
    sheet("Checks", ["Reconciliation", "USD millions", "", "Assets less liabilities and equity"])
    for i, col in enumerate("BCDEF"):
        prev = "HBCDE"[i]
        header = (f"FY{model['dates'][i].year}", "header", None)
        sheet("Revenue", [header, *[
            (f"=B{total_row+row-1}*IF(ISNUMBER(Assumptions!$B$15),Assumptions!$B$15/B{2*total_row-1},1)" if not i and normalize_capex else
             f"={prev}{row}*(1+Assumptions!$B$9+({growth_recent}{row}/{growth_base}{row}-1-Assumptions!$B$9)*Assumptions!$B$13^{i+1})")
            for row in range(4, total_row)], f"=SUM({col}4:{col}{total_row-1})"], col, 3)
        inc = f"'Income statement'!{col}"
        bs = f"'Balance sheet'!{col}"
        prior = f"'Balance sheet'!{prev}"
        cf = f"'Cash flow'!{col}"
        capex_ratio = f"Assumptions!$B$6+(Assumptions!$B$14-Assumptions!$B$6)*{i}/4" if normalize_capex else "Assumptions!$B$6"
        if normalize_capex:
            sheet("Schedules", [header, f"={inc}4*Assumptions!$B$7", f"={inc}4*Assumptions!$B$8", f"={col}4-{col}5",
                                f"={prior}7", f"={inc}4*({capex_ratio})", f"={inc}4*Assumptions!$B$5",
                                f"={col}7+{col}8-{col}9", f"={prior}10", 0, f"=SUM({col}11:{col}12)",
                                f'=IF(ISNUMBER(Assumptions!$B$16),MAX(0,{inc}9)*Assumptions!$B$16,0)',
                                "=DCF!$B$12"], col, 3)
        schedule = f"Schedules!{col}"
        sheet("Income statement", [header, f"=Revenue!{col}{total_row}", f"={col}4*SUM(Assumptions!$B$3,Assumptions!$B$11)",
                                   f"={bs}10*WACC!$B$6", f"={col}5-{col}6", f"={col}7*Assumptions!$B$4",
                                   f"={col}7-{col}8"], col, 3)
        sheet("Balance sheet", [header, f"={prev}4+{cf}10", f"={prev}5",
                                f"={schedule}4" if normalize_capex else f"={inc}4*Assumptions!$B$7",
                                f"={schedule}10" if normalize_capex else f"={prev}7+{cf}8-{cf}5", f"={prev}8",
                                f"=SUM({col}4:{col}8)", f"={schedule}13" if normalize_capex else f"={prev}10",
                                f"={schedule}5" if normalize_capex else f"={inc}4*Assumptions!$B$8", f"={prev}12",
                                f"={prev}13+{inc}9-{schedule}14" if normalize_capex else f"={prev}13+{inc}9",
                                f"=SUM({col}10:{col}13)"], col, 3)
        sheet("Cash flow", [header, f"={inc}9", f"={schedule}9" if normalize_capex else f"={inc}4*Assumptions!$B$5",
                            f"={bs}6-{bs}11-({prior}6-{prior}11)",
                            f"=SUM({col}4:{col}5)-{col}6", f"={schedule}8" if normalize_capex else f"={inc}4*({capex_ratio})",
                            f"={schedule}12-{schedule}14" if normalize_capex else 0,
                            f"={col}7-{col}8+{col}9", f"={inc}5*(1-Assumptions!$B$4)+{col}5-{col}6-{col}8",
                            f"={col}11*Assumptions!$B$12" if not i else f"={col}11"], col, 3)
        sheet("Checks", [header, (f"={bs}9-{bs}14", "check", None)], col, 3)
        expected["Checks"][f"{col}4"] = 0
        expected["Cash flow"][f"{col}12"] = base.periods[i].cash_flow.amount_millions
        expected["Revenue"][f"{col}{total_row}"] = model["revenue"][i]
        sheet("DCF", [base.periods[i].discount_years, f"={cf}12", f"={col}21/(1+$B$3)^{col}20"], col, 20)
    sheet("WACC", ["Weighted average cost of capital", "", "Treasury yield", "Equity risk premium", "Beta",
                   "Pretax cost of debt", "Common equity market value", "Debt carrying value", "WACC"])
    c = model["cost"]
    sheet("WACC", [(c.risk_free_rate, "input", PCT), (c.equity_risk_premium, "input", PCT),
                   (c.levered_beta, "input", None), (c.pretax_cost_of_debt, "input", PCT),
                   "=Cover!B6*DCF!B12", "=DCF!B8",
                   ("=(B7*(B3+B4*B5)+B8*B6*(1-Assumptions!B4))/SUM(B7:B8)", "total", PCT)], "B", 3)
    sheet("DCF", ["Discounted cash flow", "USD millions except per share"])
    investments_label = ("Marketable and equity-method investments" if model.get("bridge_affiliate_book_value")
                         else "Marketable investments")
    sheet("DCF", ["WACC", "Terminal growth", "Terminal incremental ROIC", "Cash", investments_label,
                  "Debt and finance leases", "Noncontrolling interests", "", "", "Ending common shares (millions)", "Enterprise value",
                  "Equity value", "Fair value per share", "PV of terminal value"], start=3)
    sheet("DCF", [("=WACC!B9", "total", PCT), ("=Assumptions!B9", "text", PCT), ("=Assumptions!B10", "text", PCT),
                  model.get("bridge_cash", "='Balance sheet'!H4"), model.get("bridge_investments", "='Balance sheet'!H5"),
                  model.get("bridge_debt", "='Balance sheet'!H10"), model.get("bridge_nci", 0), "", "", (model["shares"], "input", None),
                  "=SUM(B22:F22)+B16", "=B13+B6+B7-SUM(B8:B9)", ("=B14/B12", "total", PRICE),
                  "=B23*(1+B4)*(1-B4/B5)/(B3-B4)/(1+B3)^F20"], "B", 3)
    sheet("DCF", ["Discount years", "Post-cutoff FCFF", "PV of FCFF", "Terminal normalized NOPAT"], start=20)
    sheet("DCF", ["='Income statement'!F5*(1-Assumptions!B4)"], "B", 23)
    sheet("DCF", [("WACC and terminal growth", "section", None)], start=26)
    for offset, col in enumerate("BCDEF", -2):
        sheet("DCF", [(f"=$B$4+({offset}*0.005)", "header", PCT)], col, 27)
    flows = tuple(p.cash_flow for p in base.periods)
    for row, offset in enumerate(range(-2, 3), 28):
        sheet("DCF", [(f"=$B$3+({offset}*0.005)", "header", PCT)], start=row)
        for step, col in enumerate("BCDEF", -2):
            pv = "+".join(f"${year}$21/(1+$A{row})^${year}$20" for year in "BCDEF")
            formula = (f"=({pv}+$B$23*(1+{col}$27)*(1-{col}$27/$B$5)/($A{row}-{col}$27)"
                       f"/(1+$A{row})^$F$20+$B$6+$B$7-$B$8-$B$9)/$B$12")
            sheet("DCF", [(formula, "text", PRICE)], col, row)
            terminal = replace(base.terminal, growth_rate=model["growth"] + step*.005)
            expected["DCF"][f"{col}{row}"] = discount_fcff(flows, as_of, model["wacc"] + offset*.005, terminal, base.bridge).value_per_share
    expected["DCF"]["B15"] = base.value_per_share
    if "implied_margin" in model:
        sheet("DCF", [("Reverse DCF", "section", None), "Market-implied operating margin"], start=35)
        pv_margin = "+".join(f"'Income statement'!{c}4/(1+B3)^{c}20" +
                             ("*Assumptions!B12" if c == "B" else "") for c in "BCDEF")
        terminal_margin = "'Income statement'!F4*(1+B4)*(1-B4/B5)/(B3-B4)/(1+B3)^F20"
        sheet("DCF", [(f"=Assumptions!B3+Assumptions!B11+(Cover!B6-B15)*B12/((1-Assumptions!B4)*({pv_margin}+{terminal_margin}))",
                       "total", PCT)], "B", 36)
        expected["DCF"]["B36"] = model["implied_margin"]
    comps = model.get("comparables")
    if comps is not None:
        sheet("Comps", [f"{comps['basis']} diluted P/E", f"USD closes from {comps['quote_date']}"])
        for col, value in zip("ABCDE", ("Ticker", "Earnings period end", "USD close", "Diluted EPS", "P/E"), strict=True):
            sheet("Comps", [(value, "header", None)], col, 4)
        expected["Comps"] = {}
        for row, peer in enumerate(comps["rows"], 5):
            for col, value in zip("ABCDE", (peer["ticker"], str(peer["end"]), (peer["close"], "input", PRICE),
                                          (peer["eps"], "input", PRICE), f"=C{row}/D{row}"), strict=True):
                sheet("Comps", [value], col, row)
            expected["Comps"][f"E{row}"] = peer["multiple"]
        sheet("Comps", ["Peer median P/E", "Subject diluted EPS", "Implied price per share"], start=12)
        sheet("Comps", [("=MEDIAN(E5:E9)" if comps["median"] is not None else "Unavailable", "total", None),
                        (comps["subject_eps"], "input", PRICE),
                        ('=IF(ISNUMBER(B12),B12*B13,"Unavailable")', "total", PRICE)], "B", 12)
        expected["Comps"]["B14"] = comps["value"] if comps["value"] is not None else "Unavailable"
        sheet("Comps", [(comps["basis_note"] + " At least two eligible peers are required.", "note", None)], start=16)
        for row, (ticker, reason) in enumerate(comps["excluded"], 18):
            sheet("Comps", [(f"{ticker} excluded. {reason}", "note", None)], start=row)
    sheet("Sources", ["Sources and assumptions"])
    sources = _sources(model)
    for row, (name, url) in enumerate(sources.items(), 3):
        sheet("Sources", [name], start=row)
        sheet("Sources", [url], "C", row)
    sheet("Sources", [(note, "note", None) for note in notes], start=4+len(sources))
    if model.get("overrides"):
        from .operating_research import OPERATING_GUIDES

        sheet("Guided changes", ["Guided overrides", "The DCF uses the revised assumptions."])
        for col, label in zip("ABCD", ("Assumption", "Original", "Revised", "Reason"), strict=True):
            sheet("Guided changes", [(label, "header", None)], col, 3)
        for row, change in enumerate(model["overrides"], 4):
            target = change["target"]
            sheet("Guided changes", [OPERATING_GUIDES[target][0]], start=row)
            sheet("Guided changes", [(model["guided_defaults"][target], "text", PCT)], "B", row)
            sheet("Guided changes", [(change["value"], "input", PCT)], "C", row)
            sheet("Guided changes", [change["reason"]], "D", row)
    return {"cells": cells, "case": "Base", "expected": expected,
            "scenarioControls": {key: key for key in MARGIN_SHIFT},
            "scenarioExpected": {name: {"DCF": {"B15": item.value_per_share}}
                                 for name, item in model["scenarios"].items() if name != "Base"},
            "recalculationProbe": {"input": {"sheet": "Assumptions", "address": "B3", "delta": .01},
                                   "changed": [{"sheet": "DCF", "address": "B15"}],
                                   "unchanged": [{"sheet": "Actuals", "address": "F4"}]}}


def _sources(model):
    sources = dict(model["sources"])
    comps = model.get("comparables")
    if comps is not None:
        for index, url in enumerate(comps["subject_filings"], 1):
            sources[f"Subject earnings filing {index}"] = url
        for peer in comps["rows"]:
            for index, url in enumerate(peer["filings"], 1):
                sources[f"{peer['ticker']} earnings filing {index}"] = url
            sources[f"{peer['ticker']} market close"] = peer["quote_source"]
    return sources


def export_apple(model, folder, node, modules, *, previews=None):
    from .workbook_renderer import render_workbook

    workbook = folder / f"{model.get('ticker', 'AAPL')}-{model.get('as_of', AS_OF)}.xlsx"
    render_workbook(workbook_plan(model), workbook, node, modules, previews=previews)
    pdf = workbook.with_suffix(".pdf")
    _write_pdf(model, workbook, pdf)
    return workbook, pdf


def _write_pdf(model, workbook, pdf):
    from openpyxl import load_workbook
    from pypdf import PdfReader
    from reportlab.lib import colors
    from reportlab.graphics.shapes import Drawing, Rect, String
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from xml.sax.saxutils import escape

    base = model["scenarios"]["Base"].value_per_share
    company = model.get("company", "Apple Inc.")
    ticker = model.get("ticker", "AAPL")
    as_of = model.get("as_of", AS_OF)
    close = model.get("close", CLOSE)
    comps = model.get("comparables")
    cached = load_workbook(workbook, read_only=True, data_only=True)
    try:
        if abs(cached["Cover"]["B7"].value - base) > 1e-6:
            raise ValueError("PDF valuation differs from the saved workbook.")
        rating = cached["Cover"]["B9"].value
        if comps is not None:
            comp_value = cached["Comps"]["B14"].value
            expected = comps["value"]
            if (expected is None and comp_value != "Unavailable"
                    or expected is not None and (not isinstance(comp_value, (float, int))
                                                 or abs(comp_value - expected) > 1e-6)):
                raise ValueError("PDF comparable valuation differs from the saved workbook.")
    finally:
        cached.close()
    stamp = sha256(workbook.read_bytes()).hexdigest()
    styles = getSampleStyleSheet()
    story = []
    def paragraph(text, style="BodyText"):
        story.append(Paragraph(escape(text), styles[style]))

    paragraph(f"{company} | Equity research", "Title")
    paragraph(f"{ticker} | {as_of:%B %d, %Y} | Private pilot")
    paragraph(f"DCF fair value ${base:.2f} | Reference close ${close:.2f} | {base/close-1:+.1%} | {rating}", "Heading2")
    paragraph("Investment thesis", "Heading2")
    paragraph(model.get("thesis", "The model prices Apple as a hardware and services business. iPhone is the largest source of sales, while Services grows from a smaller base. A three-point margin change tests how mix, demand and cost pressure could affect valuation."))
    table = Table([["Case", "Fair value", "EBIT margin shift"]] + [
        [name, f"${result.value_per_share:.2f}", f"{MARGIN_SHIFT[name] / .03 * model.get('margin_step', .03):+.1%}"]
        for name, result in model["scenarios"].items()
    ], colWidths=[130, 130, 130])
    table.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,0), colors.HexColor("#17365D")),
                               ("TEXTCOLOR", (0,0), (-1,0), colors.white),
                               ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.HexColor("#EEF4FA"), colors.white])]))
    story.append(table)
    if "implied_margin" in model:
        paragraph(f"At the reference close, the model implies a constant EBIT margin of {model['implied_margin']:.2%}. "
                  f"The base case uses {model['margin']:.2%}. Other base-case inputs are held fixed.")
    paragraph("Revenue forecast by fiscal year", "Heading2")
    chart = Drawing(410, 128)
    maximum = max(model["revenue"])
    blue = colors.HexColor("#17365D")
    for index, (year, amount) in enumerate(zip(model["dates"], model["revenue"], strict=True)):
        x, height = 34 + 77 * index, 88 * amount / maximum
        chart.add(Rect(x, 22, 40, height, fillColor=blue, strokeColor=None))
        chart.add(String(x + 20, height + 27, f"${amount/1000:.0f}B", textAnchor="middle", fontName="Helvetica", fontSize=9))
        chart.add(String(x + 20, 7, str(year.year), textAnchor="middle", fontName="Helvetica", fontSize=9))
    story.append(chart)
    paragraph("Catalysts and risks", "Heading2")
    paragraph(model.get("risks", "Watch iPhone upgrade demand, Services growth, product mix and gross margins. Hardware competition, supply-chain exposure and regulatory changes to platform economics can alter growth and margins."))
    if "implied_margin" in model:
        paragraph("The report values the company at the cutoff. A 12-month target requires a separately verified cash, debt, and share-count forecast at the target date.")
    story.append(PageBreak())
    if comps is not None:
        paragraph("Industry context and comparable-company valuation", "Heading2")
        if comps["rows"]:
            peer_table = Table([["Ticker", "Period end", "Close", "Diluted EPS", "P/E"]] + [
                [peer["ticker"], str(peer["end"]), f"${peer['close']:.2f}", f"${peer['eps']:.2f}", f"{peer['multiple']:.1f}x"]
                for peer in comps["rows"]
            ], colWidths=[75, 100, 75, 80, 60])
            peer_table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#17365D")),
                                            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white)]))
            story.append(peer_table)
        if comps["value"] is None:
            paragraph("Peer valuation unavailable. At least two eligible peers with verified earnings and prices are required.")
        else:
            paragraph(f"Peer-implied value ${comps['value']:.2f} using median diluted P/E of {comps['median']:.1f}x.",
                      "Heading3")
        paragraph(comps["basis_note"] + " Earnings include any one-time items. The rating uses the DCF.")
        for peer in comps["rows"]:
            paragraph(f"{peer['ticker']} selection rationale. {peer['reason']}")
        for ticker, reason in comps["excluded"]:
            paragraph(f"{ticker} excluded. {reason}")
        story.append(PageBreak())
    paragraph("Assumptions and sources", "Heading2")
    for note in model.get("notes", NOTES):
        paragraph(note)
    if model.get("overrides"):
        from .operating_research import OPERATING_GUIDES

        paragraph("Guided overrides", "Heading2")
        for change in model["overrides"]:
            target = change["target"]
            paragraph(f"{OPERATING_GUIDES[target][0]} changed from {model['guided_defaults'][target]:.2%} "
                      f"to {change['value']:.2%}. {change['reason']}")
    story.append(Spacer(1, 6))
    for name, url in _sources(model).items():
        story.append(Paragraph(f'<link href="{escape(url)}" color="#17365D">{escape(name)}</link>', styles["BodyText"]))
    paragraph(f"Workbook SHA-256 {stamp}")
    SimpleDocTemplate(str(pdf), title=f"{company} | Equity Research").build(story)
    text = " ".join(page.extract_text() for page in PdfReader(pdf).pages)
    if f"${base:.2f}" not in text or rating not in text or stamp not in "".join(text.split()):
        raise ValueError("PDF failed its valuation or workbook fingerprint check.")
    if comps is not None:
        marker = ("Peer valuation unavailable." if comps["value"] is None
                  else f"Peer-implied value ${comps['value']:.2f}")
        if marker not in text:
            raise ValueError("PDF failed its comparable valuation check.")

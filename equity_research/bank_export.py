"""Export and verify the bank-model workbook and accompanying research brief."""

from dataclasses import astuple
from hashlib import sha256
from math import isclose
from xml.sax.saxutils import escape

from .bank_valuation import value_bank


_PCT = "0.00%"
_NOTICE = ("The valuation uses the filed balance date, projected aggregate common distributions and reported "
           "regulatory capital. Prospective leverage and liquidity requirements, upstream restrictions, "
           "current market price, and peer valuation still require separate review.")


def bank_workbook_plan(evidence, result, *, company, capital_buffer, comparables=None, overrides=()):
    cells = {}
    expected = {"Cover": {"B7": result.common_equity, "B8": result.residual_income_equity}, "Forecast": {}, "Checks": {}}

    def put(tab, col, row, values):
        sheet = cells.setdefault(tab, {})
        for index, value in enumerate(values, row):
            if not isinstance(value, tuple):
                value = (value, "title" if col == "A" and index == 1 else "text", None)
            sheet[f"{col}{index}"] = list(value)

    put("Cover", "A", 1, (company, f"Bank valuation | Balance date {evidence.balance_date}", "USD millions",
                           "", "Case", "", "Common equity from distributions", "Common equity from residual income",
                           "Modeled CET1 ratio"))
    scope = ("The valuation uses the filed balance date. Prospective funding and leverage requirements and upstream "
             "distributions still require review." if comparables else _NOTICE)
    put("Cover", "A", 18, ((scope, "note", None),))
    put("Cover", "B", 5, ("Base", "", "=SUM(Forecast!B26:F26)+Forecast!G24/(Assumptions!B5-Assumptions!B4)/(1+Assumptions!B5)^5",
                           "=Actuals!C9+SUM(Forecast!B27:F27)+Forecast!G25/(Assumptions!B5-Assumptions!B4)/(1+Assumptions!B5)^5",
                           ("=Assumptions!B8", "total", _PCT)))
    put("Cover", "A", 13, (("Cost of equity", "header", None),))
    put("Cover", "B", 13, (("Common equity", "header", None),))
    for index, delta in enumerate((-.01, 0, .01), 14):
        put("Cover", "A", index, ((f"=Assumptions!$B$5+({delta})", "text", _PCT),))
        terms = "+".join(f"Forecast!{col}24/(1+A{index})^{year}" for year, col in enumerate("BCDEF", 1))
        put("Cover", "B", index, (f"={terms}+Forecast!G24/(A{index}-Assumptions!$B$4)/(1+A{index})^5",))
    expected["Cover"]["B15"] = result.common_equity
    if evidence.common_shares_millions:
        put("Cover", "A", 10, ("Distribution value per common share",))
        put("Cover", "B", 10, (("=B7/Actuals!C32", "total", '"$"0.00'),))
        expected["Cover"]["B10"] = result.common_equity / evidence.common_shares_millions
        put("Actuals", "A", 32, ("Filed quarter-end common shares (millions)",))
        put("Actuals", "C", 32, (evidence.common_shares_millions,))

    put("Assumptions", "A", 1, ("Bank assumptions", "Rates shown as percentages", "",
                                "Terminal growth", "Cost of common equity", "Capital buffer", "Filed CET1 requirement",
                                "Modeled CET1 target", "Interest income / average total assets",
                                "Deposit interest / average deposits", "Other interest / other funding",
                                "Noninterest income / average total assets", "Efficiency ratio",
                                "Credit provision / average gross loans", "Effective tax rate",
                                "Risk-weighted assets / gross loans", "GAAP equity / total assets",
                                "Historical asset growth", "Historical deposit growth", "Historical loan growth"))
    assumption_formulas = (
        "=MAX(B21+B6,Actuals!C9/Actuals!C8)", "=Actuals!C13/AVERAGE(Actuals!B4:C4)",
        "=Actuals!C14/AVERAGE(Actuals!B6:C6)", "=Actuals!C15/AVERAGE(Actuals!B11:C11)",
        "=Actuals!C16/AVERAGE(Actuals!B4:C4)", "=Actuals!C17/(Actuals!C13-Actuals!C14-Actuals!C15+Actuals!C16)",
        "=Actuals!C18/AVERAGE(Actuals!B7:C7)", "=Actuals!C19/Actuals!C21",
        "=Actuals!C8/Actuals!C7", "=Actuals!C10/Actuals!C4",
        "=Actuals!C4/Actuals!B4-1", "=Actuals!C6/Actuals!B6-1", "=Actuals!C7/Actuals!B7-1",
    )
    put("Assumptions", "B", 4, [(value, "input" if i < 3 else "text", _PCT) for i, value in enumerate(
        (result.terminal_growth, result.cost_of_equity, capital_buffer, evidence.minimum_cet1, *assumption_formulas))])
    forecast_floor = evidence.forecast_minimum_cet1
    put("Assumptions", "A", 21, ("Disclosed CET1 floor used in the forecast",))
    put("Assumptions", "B", 21, (((forecast_floor if forecast_floor is not None else "=B7"), "text", _PCT),))
    for index, change in enumerate(overrides, 23):
        label = change["target"].replace("_", " ").capitalize()
        put("Assumptions", "A", index, ((f"{label} set to {change['value']:.2%}. {change['reason']}", "note", None),))

    put("Forecast", "A", 1, ("Bank forecast", "USD millions", "Forecast period", "Assets", "Deposits", "Gross loans",
                             "Other liabilities and funding", "Modeled GAAP equity", "Risk-weighted assets",
                             "Opening CET1", "Closing CET1", "CET1 ratio", "Gross interest income",
                             "Deposit interest expense", "Other interest expense", "Noninterest income",
                             "Net revenue", "Noninterest expense", "Credit provision", "Pretax income",
                             "Taxes", "Noncommon earnings", "Common earnings", "Distributable common earnings",
                             "Residual income", "PV of distributions", "PV of residual income"))
    put("Checks", "A", 1, ("Bank reconciliation", "USD millions", "Forecast period", "Capital rollforward",
                           "Assets less funding and equity", "CET1 shortfall",
                           "Distribution vs. residual-income valuation"))
    put("Checks", "B", 7, (("=Cover!B7-Cover!B8", "check", None),))
    expected["Checks"]["B7"] = 0
    for i, (col, row) in enumerate(zip("BCDEFG", (*result.years, result.terminal), strict=True)):
        prev = "ABCDEF"[i]
        base = i == 0
        prev_assets = "Actuals!$C$4" if base else f"{prev}4"
        prev_deposits = "Actuals!$C$6" if base else f"{prev}5"
        prev_loans = "Actuals!$C$7" if base else f"{prev}6"
        prev_funding = "Actuals!$C$11" if base else f"{prev}7"
        prev_capital = "Actuals!$C$9" if base else f"{prev}11"
        fade = "0" if i == 5 else f"0.5^{i+1}"
        growth = "Assumptions!$B$4"
        ke = "Assumptions!$B$5"
        period = ("Terminal year" if i == 5 else f"Year {i+1}", "header", None)
        put("Forecast", col, 3, (period,
            f"={prev_assets}*(1+{growth}+(Assumptions!$B$18-{growth})*{fade})",
            f"={prev_deposits}*(1+{growth}+(Assumptions!$B$19-{growth})*{fade})",
            f"={prev_loans}*(1+{growth}+(Assumptions!$B$20-{growth})*{fade})",
            f"={col}4-{col}5-{col}8", f"={col}4*Assumptions!$B$17", f"={col}6*Assumptions!$B$16",
            f"={prev_capital}", f"={col}9*Assumptions!$B$8", (f"={col}11/{col}9", "text", _PCT),
            f"=AVERAGE({prev_assets},{col}4)*Assumptions!$B$9",
            f"=AVERAGE({prev_deposits},{col}5)*Assumptions!$B$10",
            f"=AVERAGE({prev_funding},{col}7)*Assumptions!$B$11",
            f"=AVERAGE({prev_assets},{col}4)*Assumptions!$B$12",
            f"={col}13-{col}14-{col}15+{col}16", f"={col}17*Assumptions!$B$13",
            f"=AVERAGE({prev_loans},{col}6)*Assumptions!$B$14", f"={col}17-{col}18-{col}19",
            f"={col}20*Assumptions!$B$15", f"=Actuals!$C$20*(1+{growth})^{i+1}",
            f"={col}20-{col}21-{col}22", f"={col}23+{col}10-{col}11", f"={col}23-{col}10*{ke}",
            f"={col}24/(1+{ke})^{i+1}", f"={col}25/(1+{ke})^{i+1}"))
        put("Checks", col, 3, (period,
            (f"=Forecast!{col}10+Forecast!{col}23-Forecast!{col}24-Forecast!{col}11", "check", None),
            (f"=Forecast!{col}4-SUM(Forecast!{col}5,Forecast!{col}7,Forecast!{col}8)", "check", None),
            (f"=MAX(0,Assumptions!$B$21-Forecast!{col}12)", "check", None)))
        expected["Forecast"].update({f"{col}4": row.assets, f"{col}9": row.risk_weighted_assets,
                                     f"{col}11": row.closing_cet1, f"{col}23": row.income.common_earnings,
                                     f"{col}24": row.common_distributions})
        expected["Checks"].update({f"{col}{r}": 0 for r in (4, 5, 6)})

    put("Actuals", "A", 1, ("Filed benchmark inputs", "USD millions", "", "Assets", "Liabilities", "Deposits", "Gross loans",
                            "Risk-weighted assets", "CET1", "GAAP equity", "Other liabilities and funding",
                            "", "Gross interest income", "Deposit interest expense", "Other interest expense",
                            "Noninterest income", "Noninterest expense", "Credit provision", "Taxes", "Noncommon earnings",
                            "Pretax income", "Common earnings"))
    for col, balance, label in (("B", evidence.previous, "Prior-year interim"),
                                ("C", evidence.current, str(evidence.balance_date))):
        put("Actuals", col, 3, ((label, "header", None), *astuple(balance), f"={col}5-{col}6"))
    put("Actuals", "C", 12, (("Trailing 12 months", "header", None), *astuple(evidence.trailing),
                             "=C13-C14-C15+C16-C17-C18", "=C21-C19-C20"))
    regulatory = evidence.regulatory_checks
    if regulatory:
        put("Actuals", "A", 24, ("Tier 1 capital", "Total leverage exposure", "SLR from filed capital and leverage",
                                "Filed SLR (rounded)", "Filed SLR requirement", "Firm quarterly average LCR"))
        put("Actuals", "C", 24, (regulatory.tier1_capital, regulatory.leverage_exposure,
                                 ("=C24/C25", "total", _PCT), (regulatory.reported_slr, "text", _PCT),
                                 (regulatory.slr_requirement, "text", _PCT), (regulatory.average_lcr, "text", _PCT)))
        put("Checks", "A", 10, ("Filed SLR residual over rounding tolerance", "Filed SLR shortfall"))
        put("Checks", "B", 10, (("=MAX(0,ABS(Actuals!C26-Actuals!C27)-0.0006)", "check", _PCT),
                                 ("=MAX(0,Actuals!C28-Actuals!C26)", "check", _PCT)))
        expected["Checks"].update(B10=0, B11=0)
    put("Sources", "A", 1, ("Bank filing sources", "", "Annual source", "Current interim source", "Prior interim source",
                             "Regulatory capital method"))
    method = evidence.regulatory_method.removeprefix("BaselIII").removesuffix("Member").removesuffix("Approach")
    put("Sources", "C", 3, (*evidence.sources, f"Basel III {method.lower()} approach"))
    if comparables:
        subject = comparables["subject"]
        put("Comps", "A", 1, ("Comparable banks", f"US closes on {comparables['quote_date']}",
                              "Ticker", *[row["ticker"] for row in comparables["rows"]]))
        put("Comps", "B", 3, ("USD close", *[row["close"] for row in comparables["rows"]]))
        put("Comps", "C", 3, ("Tangible book per share", *[row["book"] for row in comparables["rows"]]))
        put("Comps", "D", 3, ("Price / tangible book", "=B4/C4", "=B5/C5"))
        put("Comps", "A", 8, ("Median peer P/TBV", f"{subject['ticker']} tangible book per share",
                              "Peer-implied value per common share", f"{subject['ticker']} closing price"))
        put("Comps", "B", 8, ("=MEDIAN(D4:D5)", subject["book"], "=B8*B9", subject["close"]))
        put("Sources", "A", 8, ("Peer tangible book filings", *[row["ticker"] for row in comparables["rows"]],
                                "Market data source"))
        put("Sources", "C", 9, (*[row["source"] for row in comparables["rows"]], subject["quote_source"]))
        expected["Comps"] = {"B8":comparables["median"], "B10":comparables["value"]}
    return {"cells": cells, "expected": expected, "case": "Base", "scenarioControls": {"Base": "Base"},
            "scenarioExpected": {}, "recalculationProbe": {
                "input": {"sheet": "Assumptions", "address": "B5", "delta": .005},
                "changed": [{"sheet": "Cover", "address": "B7"}],
                "unchanged": [{"sheet": "Actuals", "address": "C9"}]}}


def export_bank(evidence, folder, node, modules, *, company, ticker, cost_of_equity,
                terminal_growth=.025, capital_buffer=.015, previews=None, comparables=None, overrides=()):
    from .workbook_renderer import render_workbook

    result = value_bank(evidence.current, evidence.previous, evidence.trailing, minimum_cet1=evidence.minimum_cet1,
                        cost_of_equity=cost_of_equity, terminal_growth=terminal_growth, capital_buffer=capital_buffer,
                        forecast_minimum_cet1=evidence.forecast_minimum_cet1)
    filename = "bank-guided" if overrides else "bank"
    workbook = folder / f"{ticker}-{evidence.balance_date}-{filename}.xlsx"
    plan = bank_workbook_plan(evidence, result, company=company, capital_buffer=capital_buffer,
                              comparables=comparables, overrides=overrides)
    render_workbook(plan, workbook, node, modules, previews=previews)
    pdf = workbook.with_suffix(".pdf")
    _write_pdf(evidence, result, company, workbook, pdf, comparables=comparables, overrides=overrides)
    return (workbook, pdf), result


def _write_pdf(evidence, result, company, workbook, pdf, *, comparables=None, overrides=()):
    from openpyxl import load_workbook
    from pypdf import PdfReader
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    share_value = result.common_equity/evidence.common_shares_millions if evidence.common_shares_millions else None
    cached = load_workbook(workbook, read_only=True, data_only=True, keep_links=False)
    try:
        for address in ("B7", "B8"):
            if not isclose(cached["Cover"][address].value, result.common_equity, abs_tol=1e-6):
                raise ValueError("The bank PDF and saved workbook valuations differ.")
        if comparables and not isclose(cached["Comps"]["B10"].value, comparables["value"], abs_tol=1e-6):
            raise ValueError("The bank PDF and comparable-bank worksheet values differ.")
        if share_value is not None and not isclose(cached["Cover"]["B10"].value, share_value, abs_tol=1e-6):
            raise ValueError("The bank PDF and workbook per-share values differ.")
    finally:
        cached.close()
    stamp = sha256(workbook.read_bytes()).hexdigest()
    styles = getSampleStyleSheet()
    styles["BodyText"].spaceBefore = 3
    styles["BodyText"].leading = 11
    styles["Heading2"].spaceBefore = 7
    story = []

    def add(message, style="BodyText"):
        story.append(Paragraph(escape(message), styles[style]))

    add(company, "Title")
    add(f"Bank research pilot | Balance date {evidence.balance_date} | USD billions")
    value = f"${result.common_equity/1000:,.1f}B"
    add(f"Modeled common equity {value}", "Heading2")
    share_label = f"Distribution value ${share_value:,.2f} per common share" if share_value is not None else None
    if share_label:
        add(share_label)
    add(f"The distribution and residual-income methods agree. The model uses a {result.cost_of_equity:.2%} cost "
        f"of common equity and {result.terminal_growth:.2%} terminal growth as analyst assumptions.")
    for change in overrides:
        add(f"Guided change. {change['target'].replace('_', ' ').capitalize()} set to {change['value']:.2%}. {change['reason']}")
    add("Funding and credit", "Heading2")
    add(f"The filed balance sheet reports ${evidence.current.deposits/1000:,.1f}B in deposits and "
        f"${evidence.current.gross_loans/1000:,.1f}B in gross loans. The model applies separate historical rates "
        "to deposits and other funding and bases future credit provisions on projected loans.")
    rows = [["USD billions", *(f"Year {r.year}" for r in result.years)]]
    for label, getter in (("Gross loans", lambda r: r.gross_loans), ("Common earnings", lambda r: r.income.common_earnings),
                          ("Closing CET1", lambda r: r.closing_cet1), ("Common distributions", lambda r: r.common_distributions)):
        rows.append([label, *(f"{getter(row)/1000:,.1f}" for row in result.years)])
    table = Table(rows, colWidths=(140, 62, 62, 62, 62, 62))
    table.setStyle(TableStyle([("BACKGROUND", (0,0), (-1,0), colors.HexColor("#17365D")),
                               ("TEXTCOLOR", (0,0), (-1,0), colors.white),
                               ("ALIGN", (1,0), (-1,-1), "RIGHT"),
                               ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.HexColor("#EEF4FA"), colors.white])]))
    story.extend([Spacer(1, 9), table])
    add("Capital and valuation risks", "Heading2")
    add(f"Modeled CET1 stays at {result.cet1_target:.2%} against the filed {evidence.minimum_cet1:.2%} requirement "
        "under the approach with the least CET1 headroom. Deposit repricing, higher credit losses and a higher capital requirement can "
        "reduce common distributions.")
    if evidence.forecast_minimum_cet1 is not None:
        add(f"The forecast incorporates the announced change to a {evidence.forecast_minimum_cet1:.2%} CET1 requirement.")
    if evidence.regulatory_checks:
        checks = evidence.regulatory_checks
        add(f"The filed holding-company supplementary leverage ratio was {checks.reported_slr:.2%}, "
            f"against the disclosed {checks.slr_requirement:.2%} requirement.")
        if checks.average_lcr is not None:
            add(f"The firm reported a {checks.average_lcr:.0%} average quarterly liquidity coverage ratio.")
    peer_headline = None
    if comparables:
        peer_headline = f"Peer-implied value ${comparables['value']:.2f} per common share"
        peers = " and ".join(row["ticker"] for row in comparables["rows"])
        add("Trading comparables", "Heading2")
        add(f"{peer_headline}. {peers} trade at a median {comparables['median']:.2f}x tangible book "
            f"using {comparables['quote_date']} closes. {comparables['subject']['ticker']} closed at "
            f"${comparables['subject']['close']:.2f}. "
            "Differences in profitability, fees and capital requirements limit the comparison.")
        add("Prospective leverage, funding and upstream distribution constraints still require review.")
    else:
        add(_NOTICE)
    add("SEC filings", "Heading2")
    for label, url in zip(("Annual report", "Current interim report", "Prior interim report"), evidence.sources, strict=True):
        story.append(Paragraph(f'<link href="{escape(url)}" color="#17365D">{label}</link>', styles["BodyText"]))
    if comparables:
        for row in comparables["rows"]:
            story.append(Paragraph(f'<link href="{escape(row["source"])}" color="#17365D">{row["ticker"]} tangible book</link>',
                                   styles["BodyText"]))
    add(f"Workbook SHA-256 {stamp}")
    SimpleDocTemplate(str(pdf), title=f"{company} | Bank research pilot", topMargin=44, bottomMargin=44,
                      leftMargin=62, rightMargin=62).build(story)
    extracted = " ".join(page.extract_text() for page in PdfReader(pdf).pages)
    regulatory = evidence.regulatory_checks
    ratios = [f"{regulatory.reported_slr:.2%}"] if regulatory else []
    if regulatory and regulatory.average_lcr is not None:
        ratios.append(f"{regulatory.average_lcr:.0%}")
    if (value not in extracted or stamp not in "".join(extracted.split())
            or any(ratio not in extracted for ratio in ratios) or
            (share_label and share_label not in " ".join(extracted.split())) or
            (peer_headline and peer_headline not in " ".join(extracted.split()))):
        raise ValueError("The bank PDF failed the value or workbook fingerprint check.")

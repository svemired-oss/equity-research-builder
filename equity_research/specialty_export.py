"""Formula-linked workbooks and matching reports for specialty valuation pilots."""

from hashlib import sha256
from math import isclose
from xml.sax.saxutils import escape

from .insurer_valuation import value_property_casualty
from .reit_valuation import value_net_lease


PCT = "0.00%"
PRICE = '"$"0.00'


class _Workbook:
    def __init__(self, company, date, method, unit):
        self.cells = {}
        self.expected = {"Cover": {}, "Forecast": {}, "Checks": {}}
        self.column("Cover", "A", 1, (company, f"{method} | Balance date {date}", unit, "", "Case"))
        self.column("Cover", "B", 5, ("Base",))

    def column(self, sheet, column, start, values):
        cells = self.cells.setdefault(sheet, {})
        for row, value in enumerate(values, start):
            if not isinstance(value, tuple):
                value = (value, "title" if column == "A" and row == 1 else "text", None)
            cells[f"{column}{row}"] = list(value)

    def check(self, sheet, **values):
        self.expected.setdefault(sheet, {}).update(values)

    def plan(self, probe):
        return {"cells": self.cells, "expected": self.expected, "case": "Base", "scenarioControls": {"Base": "Base"},
                "scenarioExpected": {}, "recalculationProbe": {
                    "input": {"sheet": "Assumptions", "address": "B4", "delta": probe},
                    "changed": [{"sheet": "Cover", "address": "B7"}],
                    "unchanged": [{"sheet": "Actuals", "address": "B4"}],
                }}


def _export(workbook, folder, node, modules, ticker, balance_date, report, *, previews=None, suffix="research-pilot"):
    from .workbook_renderer import render_workbook

    xlsx = folder / f"{ticker}-{balance_date}-{suffix}.xlsx"
    render_workbook(workbook, xlsx, node, modules, previews=previews)
    pdf = xlsx.with_suffix(".pdf")
    _write_report(xlsx, pdf, balance_date, **report)
    return xlsx, pdf


def export_insurer(basis, balance_date, sources, folder, node, modules, *, company, ticker,
                   cost_of_equity, terminal_growth=.025, comparables=None, previews=None):
    v = value_property_casualty(basis, cost_of_equity=cost_of_equity, terminal_growth=terminal_growth)
    b = basis
    w = _Workbook(company, balance_date, "P&C insurance valuation", "USD millions")
    notes = ("This scenario values modeled common distributions. Subsidiary-level capital and upstream dividend "
             "restrictions and the current common-equity bridge require separate review.")
    if comparables is None:
        notes += " Market and comparable-insurer values are unavailable."
    w.column("Cover", "A", 7, ("Common equity from distributions", "Common equity from residual income",
                             "Terminal combined ratio"))
    w.column("Cover", "B", 7, (
        "=SUM(Forecast!B17:F17)+Forecast!G15/(Assumptions!B4-Assumptions!B5)/(1+Assumptions!B4)^5",
        "=Actuals!B10+SUM(Forecast!B18:F18)+Forecast!G16/(Assumptions!B4-Assumptions!B5)/(1+Assumptions!B4)^5",
        ("=Assumptions!B6", "total", PCT)))
    w.column("Cover", "A", 15, ((notes, "note", None),))
    w.check("Cover", B7=v.common_equity, B8=v.residual_income_equity)
    w.column("Checks", "A", 1, ("Insurer valuation checks", "USD millions", "", "Surplus rollforward",
                              "Distribution vs. residual-income valuation"))
    w.column("Checks", "B", 5, (("=Cover!B7-Cover!B8", "check", None),))
    w.check("Checks", B5=0)
    w.column("Assumptions", "A", 1, ("Insurance assumptions", "Annual rates", "", "Cost of common equity", "Terminal growth",
                                    "Management combined ratio target", "Comparable YTD premium growth", "Trailing combined ratio",
                                    "Investment income / premiums", "Financing and other costs / premiums", "Effective tax rate",
                                    "Statutory surplus / premiums"))
    assumptions = (cost_of_equity, terminal_growth, "=Actuals!B11", "=Actuals!B4/Actuals!B5-1", "=1-Actuals!B6/Actuals!B4",
                   "=Actuals!B7/Actuals!B4", "=Actuals!B8/Actuals!B4", "=Actuals!B9/(Actuals!B6+Actuals!B7-Actuals!B8)",
                   "=Actuals!B10/Actuals!B4")
    w.column("Assumptions", "B", 4, [(x, "input" if i<2 else "text", PCT) for i, x in enumerate(assumptions)])
    w.column("Forecast", "A", 1, ("Underwriting and statutory surplus", "USD millions", "Forecast period", "Earned premiums",
                                  "Combined ratio", "Underwriting income", "Investment income", "Financing and other costs",
                                  "Pretax operating income", "Normalized taxes", "Common income", "Opening surplus", "Closing surplus",
                                  "Required capital retention", "Common distribution capacity", "Residual income",
                                  "PV of distributions", "PV of residual income"))
    for i, (col, row) in enumerate(zip("BCDEFG", (*v.years, v.terminal), strict=True), 1):
        prev = "ABCDEF"[i-1]
        opening_sales = "Actuals!$B$4" if i==1 else f"{prev}4"
        opening_capital = "Actuals!$B$10" if i==1 else f"{prev}13"
        fade = "0" if i==6 else f"0.5^{i}"
        margin_fade = "0" if i>=5 else fade
        header = ("Terminal year" if i==6 else f"Year {i}", "header", None)
        w.column("Forecast", col, 3, (header,
            f"={opening_sales}*(1+Assumptions!$B$5+(Assumptions!$B$7-Assumptions!$B$5)*{fade})",
            (f"=Assumptions!$B$6+(Assumptions!$B$8-Assumptions!$B$6)*{margin_fade}", "text", PCT),
            f"={col}4*(1-{col}5)", f"={col}4*Assumptions!$B$9", f"={col}4*Assumptions!$B$10",
            f"={col}6+{col}7-{col}8", f"={col}9*Assumptions!$B$11", f"={col}9-{col}10",
            f"={opening_capital}", f"={col}4*Assumptions!$B$12", f"={col}13-{col}12",
            f"={col}11-{col}14", f"={col}11-{col}12*Assumptions!$B$4",
            f"={col}15/(1+Assumptions!$B$4)^{i}", f"={col}16/(1+Assumptions!$B$4)^{i}"))
        w.column("Checks", col, 3, (header, (f"=Forecast!{col}12+Forecast!{col}11-Forecast!{col}15-Forecast!{col}13", "check", None)))
        w.check("Checks", **{f"{col}4": 0})
        for number, value in ((4,row.premiums),(5,row.combined_ratio),(6,row.underwriting_income),
                              (11,row.common_income),(13,row.closing_surplus),(15,row.distributions)):
            w.check("Forecast", **{f"{col}{number}": value})
    w.column("Actuals", "A", 1, ("Insurance filing inputs", "USD millions", "", "Trailing earned premiums",
                                 "Prior-premium calibration base", "Trailing underwriting profit", "Trailing investment income",
                                 "Trailing financing and other costs", "Normalized taxes", "Reported statutory surplus",
                                 "Management combined ratio target"))
    w.column("Actuals", "B", 4, (b.earned_premiums,b.prior_earned_premiums,b.underwriting_profit,b.investment_income,
                                b.financing_and_other_costs,b.common_taxes,b.statutory_surplus,
                                (b.documented_combined_ratio_target,"text",PCT)))
    w.column("Sensitivity", "A", 1, ("Common equity sensitivity", "USD millions", "Cost of equity / terminal combined ratio"))
    for col, delta in zip("BCD", (-.01, 0, .01), strict=True):
        w.column("Sensitivity", col, 3, ((f"=Assumptions!B6+({delta})", "header", PCT),))
    for row, delta in enumerate((-.01, 0, .01), 4):
        w.column("Sensitivity", "A", row, ((f"=Assumptions!B4+({delta})", "text", PCT),))
        for col in "BCD":
            shock = f"({col}$3-Assumptions!B6)*(1-Assumptions!B11)"
            base = f"(1+$A{row})"
            # Any change to the target ratio affects earned premiums only by the
            # unfaded share of the difference. Capital retention is unchanged.
            terms = [
                f"(Forecast!{period}15-Forecast!{period}4*{shock}*{(1-.5**year) if year < 5 else 1})/{base}^{year}"
                for year, period in enumerate("BCDEF", 1)
            ]
            terminal = f"(Forecast!G15-Forecast!G4*{shock})/($A{row}-Assumptions!B5)/{base}^5"
            w.column("Sensitivity", col, row, ("=" + "+".join([*terms, terminal]),))
    w.check("Sensitivity", C5=v.common_equity)
    links = tuple(zip(("Current 10-Q", "Annual 10-K"), sources, strict=True))
    report_links = links
    peer_note = None
    comparison_checks = {}
    if comparables is not None:
        peer_note, comparison_checks, peer_links = _insurance_peer_table(w, comparables)
        links += peer_links
        report_links += tuple((f"{peer['ticker']} latest earnings filing", peer["filings"][1]
                               if len(peer["filings"]) > 1 else peer["filings"][0]) for peer in comparables["rows"])
    _sources(w, links, "The prior-premium calibration base reproduces comparable year-to-date growth.")
    report = {"company":company,"method":"Property-and-casualty research pilot","headline":f"Common equity ${v.common_equity/1000:,.2f}B",
              "values":{"B7":v.common_equity,"B8":v.residual_income_equity},
              "text":(f"The insurer's filed underwriting target implies a {b.documented_combined_ratio_target:.0%} combined ratio. "
                      "The forecast fades the current underwriting result toward that target and retains surplus in proportion to earned premiums.",
                      f"Investment income, financing costs and normalized taxes are projected separately. The model uses a "
                      f"{cost_of_equity:.2%} cost of equity and {terminal_growth:.2%} terminal growth.",notes),
              "chart":("Common distribution capacity, USD billions",[row.distributions/1000 for row in v.years]),
              "sources":report_links, "peer_note":peer_note, "peer_checks":comparison_checks, "compact":True,
              "sections":(("Underwriting risk", "The worksheet varies the underwriting target by one percentage point in each direction "
                           "and separately varies the cost of equity. A higher combined ratio reduces underwriting income and distributions."),)}
    return _export(w.plan(.005),folder,node,modules,ticker,balance_date,report,previews=previews)


def export_reit(evidence, folder, node, modules, *, company, ticker, discount_rate,
                terminal_growth=.02, nav_cap_rate=.07, comparables=None, previews=None):
    b = evidence.basis
    v = value_net_lease(b,discount_rate=discount_rate,terminal_growth=terminal_growth,nav_cap_rate=nav_cap_rate)
    w = _Workbook(company,evidence.date,"Net-lease REIT valuation","USD millions, except per common unit")
    notes = ("The model values the existing portfolio and includes outside common units on a pro-rata basis. "
             "The current capital bridge and effects of future investment still require review.")
    if comparables is None:
        notes += " A comparable net-lease valuation is unavailable."
    w.column("Cover","A",7,("DCF value per common unit","NAV per common unit","Property enterprise value","Common equity bridge"))
    w.column("Cover","B",7,(("=(B9+B10)/Actuals!B12","total",PRICE),
                            ("=(Forecast!B8/Assumptions!B6+B10)/Actuals!B12","total",PRICE),
                            "=SUM(Forecast!B12:F12)+Forecast!B15/(Assumptions!B4-Assumptions!B5)/(1+Assumptions!B4)^5",
                            "=Actuals!B8+Actuals!B9-Actuals!B10-Actuals!B11"))
    w.column("Cover","A",15,((notes,"note",None),))
    w.check("Cover",B7=v.value_per_share,B8=v.nav_per_share,B9=v.property_enterprise_value)
    w.column("Assumptions","A",1,("REIT assumptions","Annual rates","", "Property discount rate","Terminal growth","NAV capitalization rate",
                                  "Same-store rent growth","Renewal rate assumption","Renewal rent / prior rent",
                                  "Re-leasing downtime, fraction of a year"))
    w.column("Assumptions","B",4,[(x,"input",PCT) for x in (discount_rate,terminal_growth,nav_cap_rate,
                   b.annual_property_rent_growth,b.renewal_fraction,b.renewal_rent_ratio,b.vacancy_years)])
    w.column("Forecast","A",1,("Property cash flow","USD millions","Forecast period","Rent expiring","Contracted cash rent",
                               "Vacancy loss","Cash rent","Cash property NOI","Recurring capital and leasing costs","Overhead",
                               "Unlevered property cash flow","Discounted property cash flow","","Stabilized cash rent",
                               "Terminal cash flow"))
    for i,(col,row) in enumerate(zip("BCDEF",v.years,strict=True),1):
        prev = "ABCDE"[i-1]
        opening = "Actuals!$B$4" if i==1 else f"{prev}5"
        w.column("Forecast",col,3,((f"Year {i}","header",None),
            f"=Actuals!$B$4*(1+Assumptions!$B$7)^{i}*Actuals!{col}15",
            f"={opening}*(1+Assumptions!$B$7)+{col}4*Assumptions!$B$8*(Assumptions!$B$9-1)",
            f"={col}4*(1-Assumptions!$B$8)*Assumptions!$B$10",f"={col}5-{col}6",f"={col}7*Actuals!$B$5",
            f"={col}7/Actuals!$B$4*Actuals!$B$6",f"={col}7/Actuals!$B$4*Actuals!$B$7",
            f"={col}8-{col}9-{col}10",f"={col}11/(1+Assumptions!$B$4)^{i}"))
        for number,value in ((5,row.contracted_rent),(7,row.cash_rent),(8,row.cash_noi),(11,row.unlevered_cash_flow)):
            w.check("Forecast",**{f"{col}{number}":value})
    w.column("Forecast","B",14,("=F5*(1+Assumptions!B5)*(1-AVERAGE(Actuals!B15:F15)*(1-Assumptions!B8)*Assumptions!B10)",
                                "=B14*(Actuals!B5-(Actuals!B6+Actuals!B7)/Actuals!B4)"))
    w.column("Actuals","A",1,("REIT filing inputs","USD millions","", "Annualized pro-rata in-place rent","Cash property NOI margin",
                             "Recurring capital and leasing costs","Annualized overhead and tax proxy","Pro-rata cash",
                             "Loans and preferred equity investments","Pro-rata principal debt","Other preferred claims",
                             "Shares and outside common units (millions)","","Lease expirations by forecast period","Share of in-place rent"))
    w.column("Actuals","B",4,(b.annualized_in_place_cash_rent,(b.cash_noi_margin,"text",PCT),b.annual_recurring_capex_and_leasing_costs,
                             b.annual_cash_overhead,b.cash,b.net_nonproperty_investments,b.gross_debt,b.preferred_and_other_claims,b.shares_millions))
    for i,(col,share) in enumerate(zip("BCDEF",b.annual_expiring_rent_share,strict=True),1):
        w.column("Actuals",col,14,((f"Year {i}","header",None),(share,"text",PCT)))
    w.column("Actuals","A",17,("Pro-rata debt maturities",*(label for label,_ in evidence.maturities)))
    w.column("Actuals","B",18,tuple(amount for _,amount in evidence.maturities))
    end = 17+len(evidence.maturities)
    w.column("Checks","A",1,("REIT reconciliation","USD millions", "", "Maturities less gross debt"))
    w.column("Checks","B",4,((f"=SUM(Actuals!B18:B{end})-Actuals!B10","check",None),))
    w.check("Checks",B4=sum(amount for _,amount in evidence.maturities)-b.gross_debt)
    w.column("Sensitivity", "A", 1, ("Property valuation sensitivity", "USD per common unit",
                                    "Discount rate / terminal growth", "", "", "", "", "NAV capitalization rate"))
    for col, delta in zip("BCD", (-.005, 0, .005), strict=True):
        w.column("Sensitivity", col, 3, ((f"=Assumptions!B5+({delta})", "header", PCT),))
        w.column("Sensitivity", col, 8, ((f"=Assumptions!B6+({delta})", "header", PCT),
                                        (f"=(Forecast!B8/{col}8+Cover!B10)/Actuals!B12","text",PRICE)))
    for row, delta in enumerate((-.01, 0, .01), 4):
        w.column("Sensitivity", "A", row, ((f"=Assumptions!B4+({delta})", "text", PCT),))
        base = f"(1+$A{row})"
        flows = "+".join(f"Forecast!{period}11/{base}^{year}" for year, period in enumerate("BCDEF", 1))
        for col in "BCD":
            terminal = f"Forecast!B15*(1+{col}$3)/(1+Assumptions!B5)/($A{row}-{col}$3)/{base}^5"
            w.column("Sensitivity", col, row, ((f"=({flows}+{terminal}+Cover!B10)/Actuals!B12","text",PRICE),))
    w.check("Sensitivity", C5=v.value_per_share, C9=v.nav_per_share)
    links = (("SEC-filed operating supplement",evidence.source),)
    peer_note = None
    comparison_checks = {}
    if comparables is not None:
        peer_note, comparison_checks, peer_links = _reit_peer_table(w, comparables)
        links += peer_links
    _sources(w,links,"Lease expirations are allocated to September-ending periods. The overhead proxy uses annualized filed G&A and income taxes.")
    report = {"company":company,"method":"Net-lease REIT research pilot",
              "headline":f"DCF ${v.value_per_share:.2f} per common unit | NAV ${v.nav_per_share:.2f}",
              "values":{"B7":v.value_per_share,"B8":v.nav_per_share},
              "text":(f"The forecast starts from ${b.annualized_in_place_cash_rent/1000:.2f}B of pro-rata in-place rent. "
                      "Lease expirations, rent recapture, vacancy, maintenance and overhead drive property cash flow.",
                      f"The model discounts property cash flow at {discount_rate:.2%}. The separate NAV applies a "
                      f"{nav_cap_rate:.2%} cap rate to next-year cash property NOI. Both apply the same ownership-adjusted debt and credit investments.",notes),
              "chart":("Unlevered property cash flow, USD billions",[r.unlevered_cash_flow/1000 for r in v.years]),
              "sources":tuple((label,url) for label,url in links if not label.endswith(" closing price")),
              "peer_note":peer_note, "peer_checks":comparison_checks, "compact":True,
              "sections":(("Property valuation risk","The worksheet shows how discount rates and terminal rent growth affect DCF, "
                            "and how capitalization rates affect NAV."),)}
    return _export(w.plan(.005),folder,node,modules,ticker,evidence.date,report,previews=previews)


def _sources(w, links, note):
    w.column("Sources","A",1,("Sources and scope","",*(label for label,_ in links)))
    w.column("Sources","C",3,tuple(url for _,url in links))
    w.column("Sources","A",4+len(links),((note,"note",None),))


def _insurance_peer_table(w, comps):
    w.column("Comps", "A", 1, ("Property-and-casualty peers", f"Closes on {comps['quote_date']} | {comps['basis']}"))
    w.expected["Comps"] = {}
    for column, label in zip("ABCDE", ("Ticker", "Earnings date", "USD close", "Diluted EPS", "P/E"), strict=True):
        w.column("Comps", column, 4, ((label, "header", None),))
    links = tuple((f"Subject earnings filing {i}", url) for i, url in enumerate(comps["subject_filings"], 1))
    for index, peer in enumerate(comps["rows"], 5):
        fields = (peer["ticker"], str(peer["end"]), (peer["close"], "input", PRICE),
                  (peer["eps"], "input", PRICE), f"=C{index}/D{index}")
        for column, field in zip("ABCDE", fields, strict=True):
            w.column("Comps", column, index, (field,))
        w.check("Comps", **{f"E{index}": peer["multiple"]})
        links += tuple((f"{peer['ticker']} earnings filing {i}", url) for i, url in enumerate(peer["filings"], 1))
        links += ((f"{peer['ticker']} closing price", peer["quote_source"]),)
    quote = comps["subject_quote"]
    w.column("Comps", "A", 11, ("Median P/E", "Progressive diluted EPS", "Peer value per share", "Progressive closing price"))
    w.column("Comps", "B", 11, ("=MEDIAN(E5:E9)", (comps["subject_eps"], "input", PRICE),
                                ("=B11*B12", "total", PRICE), (quote["close"], "input", PRICE)))
    w.check("Comps", B13=comps["value"])
    w.column("Comps", "A", 16, ((comps["basis_note"], "note", None),))
    for i, peer in enumerate(comps["rows"], 18):
        w.column("Comps", "A", i, ((f"{peer['ticker']}. {peer['reason']}", "note", None),))
    symbols = ", ".join(peer["ticker"] for peer in comps["rows"])
    return (f"Peer-implied value ${comps['value']:.2f} per share. Progressive closed at ${quote['close']:.2f}. "
            f"{symbols} trade at a median {comps['median']:.2f}x "
            f"{comps['basis'].lower()} diluted earnings using {comps['quote_date']} closes. "
            "Reported earnings include realized investment gains and losses.",
            {"B13": comps["value"], "B14": quote["close"]}, links + (("Progressive closing price", quote["url"]),))


def _reit_peer_table(w, comps):
    w.column("Comps","A",1,("Net-lease REIT peers",f"{comps['year']} AFFO guidance | Closes on {comps['quote_date']}"))
    w.expected["Comps"] = {}
    for column,label in zip("ABCD",("Ticker","USD close","AFFO midpoint","P/AFFO"),strict=True):
        w.column("Comps",column,4,((label,"header",None),))
    subject = comps["subject"]
    links = [(f"{subject['ticker']} AFFO guidance",subject["source"]),(f"{subject['ticker']} closing price",subject["quote_source"])]
    for row,peer in enumerate(comps["rows"],5):
        fields = (peer["ticker"],(peer["close"],"input",PRICE),(peer["affo"],"input",PRICE),f"=B{row}/C{row}")
        for column,value in zip("ABCD",fields,strict=True):
            w.column("Comps",column,row,(value,))
        w.check("Comps",**{f"D{row}":peer["multiple"]})
        links.extend(((f"{peer['ticker']} AFFO guidance",peer["source"]),(f"{peer['ticker']} closing price",peer["quote_source"])))
    w.column("Comps","A",11,("Median P/AFFO","Realty Income AFFO midpoint","Peer value per share","Realty Income closing price"))
    w.column("Comps","B",11,("=MEDIAN(D5:D6)",(subject["affo"],"input",PRICE),
                              ("=B11*B12","total",PRICE),(subject["close"],"input",PRICE)))
    w.check("Comps",B13=comps["value"])
    for row,peer in enumerate(comps["rows"],17):
        w.column("Comps","A",row,((f"{peer['ticker']}. {peer['reason']}","note",None),))
    note = (f"Peer-implied value ${comps['value']:.2f} per share. Based on {comps['year']} AFFO guidance midpoints and "
            f"{comps['quote_date']} closes, ADC and NNN trade at a median {comps['median']:.2f}x. "
            "Each issuer defines its own AFFO adjustments.")
    return note,{"B13":comps["value"],"B14":subject["close"]},tuple(links)


def _write_report(xlsx, pdf, date, *, company, method, headline, values, text, chart, sources, peer_note=None,
                  peer_checks=None, chart_currency="$", sections=(), chart_labels=None, compact=False):
    from openpyxl import load_workbook
    from pypdf import PdfReader
    from reportlab.lib import colors
    from reportlab.graphics.shapes import Drawing, Rect, String
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate

    with xlsx.open("rb") as stream:
        cached = load_workbook(stream,read_only=True,data_only=True,keep_links=False)
        try:
            if any(not isclose(cached["Cover"][cell].value,value,abs_tol=1e-6) for cell,value in values.items()):
                raise ValueError("The report and workbook do not agree.")
            if peer_checks and any(not isclose(cached["Comps"][cell].value, value, abs_tol=1e-6)
                                   for cell, value in peer_checks.items()):
                raise ValueError("The report and workbook peer comparison do not agree.")
        finally:
            cached.close()
    styles = getSampleStyleSheet()
    if compact:
        styles["BodyText"].spaceBefore = 3
        styles["BodyText"].leading = 11
        styles["Heading2"].spaceBefore = 7
    stamp = sha256(xlsx.read_bytes()).hexdigest()
    story = []

    def paragraph(content, style="BodyText"):
        story.append(Paragraph(escape(content),styles[style]))

    paragraph(company,"Title")
    paragraph(f"{method} | Balance date {date}")
    paragraph(headline,"Heading2")
    paragraph("Forecast and valuation","Heading2")
    for description in text[:-1]:
        paragraph(description)
    paragraph(chart[0],"Heading3")
    graph = Drawing(435,118 if compact else 138)
    blue = colors.HexColor("#17365D")
    for i,value in enumerate(chart[1]):
        height = value/max(chart[1])*(72 if compact else 88)
        x = 35+78*i
        graph.add(Rect(x,21,40,height,fillColor=blue,strokeColor=None))
        graph.add(String(x+20,height+26,f"{chart_currency}{value:.2f}B",fontSize=9,textAnchor="middle"))
        label = chart_labels[i] if chart_labels else f"Year {i+1}"
        graph.add(String(x+20,7,label,fontSize=9,textAnchor="middle"))
    story.append(graph)
    if peer_note:
        paragraph("Comparable companies", "Heading2")
        paragraph(peer_note)
    for title, description in sections:
        paragraph(title, "Heading2")
        paragraph(description)
    links = [f'<link href="{escape(url)}" color="#17365D">{escape(label)}</link>' for label,url in sources]
    if compact:
        links = [" &nbsp; | &nbsp; ".join(links[i:i+3]) for i in range(0, len(links), 3)]
    source_section = [
        Paragraph("Scope and source evidence", styles["Heading2"]), Paragraph(escape(text[-1]), styles["BodyText"]),
        *(Paragraph(line, styles["BodyText"]) for line in links),
        Paragraph(f"Workbook SHA-256 {stamp}", styles["BodyText"]),
    ]
    if compact:
        story.append(KeepTogether(source_section))
    else:
        story.extend(source_section)
    margins = dict(topMargin=44, bottomMargin=44, leftMargin=62, rightMargin=62) if compact else {}
    SimpleDocTemplate(str(pdf),title=f"{company} | {method}", **margins).build(story)
    extracted = " ".join(" ".join(page.extract_text().split()) for page in PdfReader(pdf).pages)
    if headline not in extracted or stamp not in "".join(extracted.split()):
        raise ValueError("The report failed its valuation and workbook fingerprint checks.")
    if peer_note and peer_note.split(". ", 1)[0] not in extracted:
        raise ValueError("The report is missing its verified peer-implied value.")

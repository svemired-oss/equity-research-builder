"""A yen-denominated, interim-anchored valuation from Nintendo's public releases."""

from datetime import date, datetime
from io import BytesIO
import re
from urllib.error import URLError
from xml.etree import ElementTree as ET

from .fcff_valuation import DatedFCFF, EquityBridge, TerminalAssumptions, discount_fcff, fcff_sensitivity
from .operating_research import ResearchGap
from .specialty_export import PCT, _Workbook, _export, _sources
from .tokyo_market import collect_jgb_yield, public_document


_ORIGIN = "https://www.nintendo.co.jp"
_ARCHIVE = _ORIGIN + "/corporate/common/data/news_en.xml"
_SCOPE = ("The DCF starts at the interim balance date using the subsequent earnings guidance. It uses the dated market close "
          "as a reference. Lease and financing claims embedded in other liabilities, strategic investments, "
          "and comparable-company values require separate review. No post-balance fair value or trading recommendation is issued.")
_JPY = '"JPY "#,##0.00'


def _latest_releases(cutoff, document):
    try:
        if re.search(br"<!\s*(?:DOCTYPE|ENTITY)\b", document, re.I):
            raise ET.ParseError("Unexpected XML declaration")
        items = ET.fromstring(document).findall("item")
    except ET.ParseError as exc:
        raise ResearchGap("Nintendo's official earnings archive could not be read.") from exc
    mapping = {"Fiscal Year": (3, 31), "Three Months": (6, 30), "Six Months": (9, 30), "Nine Months": (12, 31)}
    releases = set()
    for item in items:
        title = item.findtext("news_text", "")
        match = re.fullmatch(r"Earnings Release: (Fiscal Year|Three Months|Six Months|Nine Months) Ended "
                             r"([A-Za-z]+ \d{1,2}, \d{4})", title)
        path = item.findtext("url", "")
        if not match or not re.fullmatch(r"/ir/pdf/20\d{2}/\d{6}e\.pdf", path):
            continue
        try:
            published = datetime.strptime(item.findtext("date_released", ""), "%Y.%m.%d").date()
            end = datetime.strptime(match[2], "%B %d, %Y").date()
        except ValueError:
            continue
        if published <= cutoff and 0 <= (published - end).days <= 90 and (end.month, end.day) == mapping[match[1]]:
            releases.add((end, published, _ORIGIN + path))
    if not releases:
        raise ResearchGap("Nintendo's official archive has no dated earnings reports for the requested cutoff.")
    latest = max(end for end, _, _ in releases)
    if latest.month == 3:
        raise ResearchGap("Nintendo's latest release is the fiscal year-end report. The next matched interim release is required for this scenario.")
    prior = date(latest.year, 3, 31)

    def unique(end):
        records = [row for row in releases if row[0] == end]
        if len(records) != 1:
            raise ResearchGap("Nintendo's archive did not provide one matching annual and interim earnings release.")
        return records[0]

    annual, interim = unique(prior), unique(latest)
    if (cutoff - interim[1]).days > 125:
        raise ResearchGap("Nintendo's latest verified interim earnings release is too old for the requested cutoff.")
    return annual, interim


def _pages(url, issuer, period):
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        pdf = PdfReader(BytesIO(public_document(url, size_limit=8_000_000)))
        pages = [page.extract_text(extraction_mode="layout") for page in pdf.pages]
    except (URLError, TimeoutError, PdfReadError) as exc:
        raise ResearchGap("Nintendo's public filing could not be verified.") from exc
    if not pages or issuer not in pages[0] or period not in pages[0] or "million yen" not in pages[0]:
        raise ResearchGap("Nintendo's issuer, period or reporting units did not match the expected filing.")
    return pages


def _row(page, label):
    amount = r"(?:\([\d,]+\)|[\d,]+)"
    gap = r"[^\S\n]"
    matched = re.findall(rf"^{gap}*{re.escape(label)}{gap}{{2,}}({amount}){gap}{{2,}}({amount}){gap}*$", page, flags=re.M)
    if len(matched) != 1:
        raise ResearchGap(f"Nintendo's filing did not provide a unique paired row for {label}.")
    return tuple(int(value.replace(",", "").replace("(", "-").replace(")", "")) for value in matched[0])


def _page(pages, *markers):
    candidates = [page for page in pages if all(marker in page for marker in markers)]
    if len(candidates) != 1:
        raise ResearchGap("Nintendo's filing did not provide the expected statement.")
    return candidates[0]


def _nintendo_filings(cutoff):
    prior, latest = _latest_releases(cutoff, public_document(_ARCHIVE, size_limit=2_000_000))
    basis, released, interim_url = latest
    annual_end, _, annual_url = prior
    fiscal_year = basis.year + 1
    interim = _pages(interim_url, "Nintendo Co., Ltd.", f"{basis:%B} {basis.day}, {basis.year}")
    annual = _pages(annual_url, "Nintendo Co., Ltd.", f"March 31, {annual_end.year}")
    balance = _page(interim, "Cash and deposits", "Total liabilities and net assets")
    income = _page(interim, "Operating profit", "Profit before income taxes")
    annual_income = _page(annual, "Operating profit", "Profit before income taxes")
    annual_cf = _page(annual, "Purchase of property, plant and equipment", "Depreciation")
    names = ("Cash and deposits", "Securities", "Notes and accounts receivable-trade", "Inventories",
             "Notes and accounts payable-trade", "Retirement benefit liability", "Non-controlling interests",
             "Total assets", "Total liabilities", "Total net assets")
    rows = {name: _row(balance, name) for name in names}
    if any(abs(assets-liabilities-equity) > 1 for assets, liabilities, equity in zip(
            rows["Total assets"], rows["Total liabilities"], rows["Total net assets"], strict=True)):
        raise ResearchGap("Nintendo's reported balance sheet did not reconcile.")
    guidance = re.search(rf"Year ending March 31, {fiscal_year}\s+([\d,]+)\s+\S+\s+([\d,]+)", interim[0])
    share_date = rf"As of {basis:%b}\.? {basis.day}, {basis.year}:\s+([\d,]+)"
    issued = re.search(r"Number of shares outstanding \(including treasury shares\).*?" + share_date,
                       interim[1], flags=re.S)
    treasury = re.search(r"Number of treasury shares.*?" + share_date, interim[1], flags=re.S)
    if not all((guidance, issued, treasury)):
        raise ResearchGap("Nintendo's fiscal guidance or ordinary share reconciliation could not be verified.")
    revenue, ebit = (int(value.replace(",", "")) for value in guidance.groups())
    common = [int(match[1].replace(",", ""))/1_000_000 for match in (issued, treasury)]
    sales = _row(annual_income, "Net sales")
    operating = _row(annual_income, "Operating profit")
    ytd_sales, ytd_ebit = (_row(income, label)[1] for label in ("Net sales", "Operating profit"))
    taxes = _row(annual_income, "Total income taxes")[1]
    pretax = _row(annual_income, "Profit before income taxes")[1]
    da = _row(annual_cf, "Depreciation")[1]
    capex = -_row(annual_cf, "Purchase of property, plant and equipment and intangible assets")[1]
    if not (0 < ytd_sales < revenue and 0 < ytd_ebit < ebit and 0 < taxes < pretax and min(da, capex, *sales) > 0
            and 0 < common[1] < common[0]):
        raise ResearchGap("Nintendo's forecast, tax, share or cash-flow inputs did not reconcile.")
    return {"rows": rows, "sales": sales, "operating": operating, "ytd_sales": ytd_sales, "ytd_ebit": ytd_ebit,
            "guide_sales": revenue, "guide_ebit": ebit, "tax": taxes, "pretax": pretax, "da": da, "capex": capex,
            "common": common, "balance_date": basis, "release_date": released, "annual_url": annual_url, "interim_url": interim_url}


def prepare_nintendo(cutoff, quote):
    if quote.get("ticker") != "7974" or quote.get("currency") != "JPY":
        raise ResearchGap("A verified Tokyo ordinary-share close is needed for Nintendo.")
    filings = _nintendo_filings(cutoff)
    yield_quote = collect_jgb_yield(date.fromisoformat(quote["trading_date"]))
    return {**filings, "quote": quote, "yield": yield_quote}


def export_nintendo(data, folder, node, modules, *, margin=None, reason=None):
    r = data["rows"]
    balance_date = data["balance_date"]
    fiscal_year = balance_date.year + 1
    fiscal_label = f"FY{fiscal_year%100:02}"
    sales = data["sales"][1]
    wc = [receivables + inventory - payables for receivables, inventory, payables in zip(
        r["Notes and accounts receivable-trade"], r["Inventories"], r["Notes and accounts payable-trade"], strict=True)]
    tax = data["tax"]/data["pretax"]
    margin = data["guide_ebit"]/data["guide_sales"] if margin is None else margin
    growth, premium, perpetuity, roic, cash_floor = .03, .045, .015, .16, .10
    rate = data["yield"]["rate"] + premium
    adjusted_cash = max(0., r["Cash and deposits"][1]+r["Securities"][1]-data["guide_sales"]*cash_floor)
    bridge = EquityBridge(balance_date, adjusted_cash, 0, 0, 0, r["Non-controlling interests"][1],
                          r["Retirement benefit liability"][1], data["common"][0]-data["common"][1])
    projection = []
    opening = wc[1]
    for index in range(5):
        fiscal_sales = data["guide_sales"]*(1+growth)**index
        closing = fiscal_sales*wc[0]/sales
        remaining = fiscal_sales if index else fiscal_sales-data["ytd_sales"]
        ebit = fiscal_sales*margin if index else data["guide_ebit"]-data["ytd_ebit"]
        flow = ebit*(1-tax) + remaining*(data["da"]-data["capex"])/sales - (closing-opening)
        projection.append((fiscal_sales, closing, DatedFCFF(date(fiscal_year+index, 3, 31), flow)))
        opening = closing
    terminal = TerminalAssumptions(projection[-1][0]*margin*(1-tax), perpetuity, roic)
    flows = [item[2] for item in projection]
    value = discount_fcff(flows, balance_date, rate, terminal, bridge)
    value_grid = fcff_sensitivity(flows, balance_date, terminal, bridge,
                                  wacc_rates=[rate+d for d in (-.01, 0, .01)],
                                  growth_rates=[perpetuity+d for d in (-.005, 0, .005)])
    q = data["quote"]
    w = _Workbook("Nintendo Co., Ltd. | XTKS:7974", balance_date, "Nintendo FCFF scenario", "JPY millions, except per share")
    w.column("Cover", "A", 7, ("Scenario value per ordinary share", f"JPX close {q['trading_date']}", "Value vs close",
                               "Enterprise value", "Common equity", "Ordinary shares ex treasury (millions)"))
    w.column("Cover", "B", 7, (("=B11/B12", "total", _JPY), (q["close"], "text", _JPY),
                               ("=B7/B8-1", "text", PCT), "=SUM(Forecast!B11:F11)+DCF!B7", "=B10+DCF!B9",
                               "=Actuals!C19-Actuals!C20"))
    w.column("Cover", "A", 15, ((_SCOPE, "note", None),))
    w.check("Cover", B7=value.value_per_share, B8=q["close"], B10=value.enterprise_value_millions, B11=value.equity_value_millions)
    w.column("Assumptions", "A", 1, ("Forecast assumptions", "Rates and ratios", "", "Cost of capital spread over JGB",
                                     "10-year Japanese government yield", "Discount rate", "Post-guidance sales growth", "Post-guidance operating margin",
                                     "Terminal growth", "Terminal marginal ROIC", "Tax / pretax", "Cash reserve / guided sales"))
    w.column("Assumptions", "B", 4, tuple((v, style, PCT) for v, style in (
        (premium, "input"), (data["yield"]["rate"], "text"), ("=B4+B5", "total"), (growth, "input"), (margin, "input"),
        (perpetuity, "input"), (roic, "input"), ("=Actuals!B16/Actuals!B17", "text"), (cash_floor, "input"))))
    if reason:
        w.column("Assumptions", "A", 14, ((f"Guided margin override: {reason}", "note", None),))
    w.column("Actuals", "A", 1, ("Nintendo filed data", "JPY millions", "Period", "Fiscal sales", "Operating profit",
                                 *r.keys(), "Income tax", "Pretax profit", "Depreciation", "Issued shares (millions)",
                                 "Treasury shares (millions)", "Cash capex", f"{fiscal_label} revenue guidance", f"{fiscal_label} EBIT guidance"))
    w.column("Actuals", "B", 3, (f"FY{balance_date.year%100:02} / March {balance_date.year}", sales, data["operating"][1], *(pair[0] for pair in r.values()),
                                 data["tax"], data["pretax"], data["da"], "", "", data["capex"],
                                 data["guide_sales"], data["guide_ebit"]))
    w.column("Actuals", "C", 3, (f"{fiscal_label} YTD / {balance_date:%b %Y}", data["ytd_sales"], data["ytd_ebit"], *(pair[1] for pair in r.values()),
                                 "", "", "", *data["common"]))
    w.column("Actuals", "D", 3, (f"FY{(balance_date.year-1)%100:02}", data["sales"][0], data["operating"][0]))
    w.column("Forecast", "A", 1, ("Fiscal forecast", "JPY millions", "Fiscal year ending March", "Full-year sales", "Modeled period sales",
                                  "Modeled period operating profit", "Period NOPAT", "March closing operating working capital",
                                  "Working capital investment / (release)", "FCFF", "Discounted FCFF", "ACT/365 years",
                                  "Cash capex", "Depreciation"))
    for index, (col, (fiscal_sales, closing, flow)) in enumerate(zip("BCDEF", projection, strict=True)):
        prev = "ABCDE"[index]
        opening = "Actuals!C8+Actuals!C9-Actuals!C10" if not index else f"{prev}8"
        w.column("Forecast", col, 3, ((f"FY{flow.period_end.year%100:02}"+(" stub" if not index else ""), "header", None),
            "=Actuals!B22" if not index else f"={prev}4*(1+Assumptions!$B$7)",
            f"={col}4-Actuals!C4" if not index else f"={col}4",
            "=Actuals!B23-Actuals!C5" if not index else f"={col}4*Assumptions!$B$8",
            f"={col}6*(1-Assumptions!$B$11)", f"={col}4/Actuals!$B$4*(Actuals!$B$8+Actuals!$B$9-Actuals!$B$10)",
            f"={col}8-({opening})", f"={col}7-{col}9-{col}13+{col}14",
            f"={col}10/(1+Assumptions!$B$6)^{col}12",
            f"=(DATE({flow.period_end.year},3,31)-DATE({balance_date.year},{balance_date.month},{balance_date.day}))/365",
            f"={col}5/Actuals!$B$4*Actuals!$B$21", f"={col}5/Actuals!$B$4*Actuals!$B$18"))
        w.check("Forecast", **{f"{col}4": fiscal_sales, f"{col}8": closing, f"{col}10": flow.amount_millions})
    w.column("DCF", "A", 1, ("Terminal value and equity bridge", "JPY millions", "", "Next-year NOPAT", "Net reinvestment", "Terminal FCFF",
                             "Discounted terminal value", "Modeled excess cash and current securities", "Common equity adjustment",
                             "WACC versus terminal growth"))
    w.column("DCF", "B", 4, ("=Forecast!F7*(1+Assumptions!B9)", "=B4*Assumptions!B9/Assumptions!B10", "=B4-B5",
                             "=B6/(Assumptions!B6-Assumptions!B9)/(1+Assumptions!B6)^Forecast!F12",
                             "=MAX(0,Actuals!C6+Actuals!C7-Actuals!B22*Assumptions!B12)", "=B8-Actuals!C11-Actuals!C12"))
    for column, delta in zip("BCD", (-.005, 0, .005), strict=True):
        w.column("DCF", column, 11, ((f"=Assumptions!B9+({delta})", "header", PCT),))
    for i, delta in enumerate((-.01, 0, .01), 12):
        w.column("DCF", "A", i, ((f"=Assumptions!B6+({delta})", "text", PCT),))
        d = f"(1+$A{i})"
        terms = "+".join(f"Forecast!{c}10/{d}^Forecast!{c}12" for c in "BCDEF")
        for column in "BCD":
            g = f"{column}$11"
            terminal_formula = f"Forecast!F7*(1+{g})*(1-{g}/Assumptions!B10)/($A{i}-{g})/{d}^Forecast!F12"
            w.column("DCF", column, i, ((f"=({terms}+{terminal_formula}+$B$9)/Cover!$B$12", "text", _JPY),))
            w.check("DCF", **{f"{column}{i}": value_grid[i-12]["BCD".index(column)]})
    w.column("Checks", "A", 1, ("Reconciliation checks", "JPY millions", "", "Filed assets less liabilities and net assets",
                                "Reported YTD + stub sales less annual guidance", "WACC sensitivity midpoint less base DCF"))
    w.column("Checks", "B", 4, ("=Actuals!C13-Actuals!C14-Actuals!C15", "=Actuals!C4+Forecast!B5-Actuals!B22", "=DCF!C13-Cover!B7"))
    w.check("Checks", B4=r["Total assets"][1]-r["Total liabilities"][1]-r["Total net assets"][1], B5=0, B6=0)
    links = (("Annual financial results", data["annual_url"]), ("Interim financial results and guidance", data["interim_url"]),
             ("JPX afternoon close", q["url"]), ("Japanese 10-year government yield", data["yield"]["url"]),
             ("Nintendo earnings release archive", _ARCHIVE))
    _sources(w, links, _SCOPE)
    report = {"company": "Nintendo Co., Ltd. | Tokyo 7974", "method": "JPY equity research pilot",
              "headline": f"DCF JPY {value.value_per_share:,.2f} per share | JPX close JPY {q['close']:,.0f}",
              "values": {"B7": value.value_per_share, "B8": q["close"]}, "chart_currency": "JPY ",
              "chart": ("Fiscal sales forecast, JPY billions", [item[0]/1000 for item in projection]),
              "chart_labels": [f"FY{item[2].period_end.year%100}" for item in projection],
              "compact": True, "sources": links,
              "sections": (("Investment view | Review", "Switch 2 adoption and software attachment will shape cash generation. "
                            "Stronger software mix could improve margins, while a weaker hardware cycle, yen moves, or higher inventory could reduce value."),
                           ("Sensitivity", f"Combined discount and growth sensitivities span JPY {min(map(min, value_grid)):,.0f}"
                            f" to JPY {max(map(max, value_grid)):,.0f} per share.")),
              "text": (f"Management guidance published {data['release_date']:%B %Y} is JPY {data['guide_sales']/1000:,.0f}B in sales and "
                       f"JPY {data['guide_ebit']/1000:,.0f}B in operating profit. Filed year-to-date results are removed from the cash-flow stub.",
                       f"Later revenue grows at {growth:.1%}. Operating margin is {margin:.1%}. The {rate:.2%} discount rate combines "
                       "the Japanese 10-year government yield and an editable analyst risk spread. Capex and depreciation scale from the last annual filing.",
                       _SCOPE)}
    if reason:
        report["text"] = (*report["text"][:-1], f"Guided margin change. {reason}", _SCOPE)
    return _export(w.plan(.005), folder, node, modules, "XTKS-7974", balance_date, report), value.value_per_share

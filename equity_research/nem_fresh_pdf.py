"""Generate and verify the Newmont research PDF from a dated workbook snapshot."""

from dataclasses import dataclass
from datetime import date
from math import fsum, isclose
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib.colors import HexColor

from .gd_fresh_pdf_charts import target_range_chart
from .gd_fresh_pdf_workbook import VerifiedPDFWorkbook
from .gd_pdf import BODY, INK, MARGIN, MUTED, RULE, _date_label, _usd
from .nem_current import METALS, _evidence, _million_quantity
from .nem_forward_statements import FY27, Q4, run_nem_statements
from .nem_nav import run_nem_nav
from .research_pdf import _ReportPage as _ResearchPage, _bn, _cards, _inspect_pdf, _make_pdf, _pct, _publish_pdf, _result


PAGE_COUNT = 6
GOLD = HexColor("#765321")
PALE_GOLD = HexColor("#F2E6D0")
WORLD_GOLD_COUNCIL = "https://www.gold.org/goldhub/research/gold-demand-trends/gold-demand-trends-q3-2026"
METAL_LABELS = {"gold": "Gold", "silver": "Silver", "copper": "Copper", "lead": "Lead", "zinc": "Zinc"}


@dataclass(frozen=True)
class _Research:
    as_of: date
    case: str
    workbook_name: str
    workbook: VerifiedPDFWorkbook
    nav: dict
    statements: dict
    settings: dict

    @property
    def selected(self):
        return self.nav["scenarios"][self.case]


class _ReportPage(_ResearchPage):
    def __init__(self, canvas, number, report, section, title, checks):
        super().__init__(canvas, number, report, section, title, checks, page_count=PAGE_COUNT,
                         identity="NEWMONT CORPORATION   /   NYSE: NEM", accent=GOLD)


def _investment_page(canvas, report, checks):
    page = _ReportPage(canvas, 1, report, "Investment view", "Newmont Corporation", checks)
    nav, case, wb = report.nav, report.selected, report.workbook
    close = wb.number("Cover", "B4")
    page.paragraph(f"{report.case.title()} case / {nav['mode'].title()} assumptions / {_date_label(report.as_of)} pre-open. "
                   f"Reference close {_usd(close)} on {_date_label(wb.day('Cover', 'B7'))}. "
                   f"Target date {_date_label(wb.day('Cover', 'B14'))}. [9, 13]")
    _cards(page, (
        ("Model rating", str(wb.value("Cover", "B12"))), ("Spot NAV per share", _usd(wb.number("Cover", "B9"))),
        ("12-month target", _usd(wb.number("Cover", "B10"))), ("12-month total return", _pct(wb.number("Cover", "B11"), signed=True)),
    ))
    peer = wb.number("Cover", "B13")
    base_spot = nav["scenarios"]["base"]["spot"]["value_per_share"]
    bull_target = nav["scenarios"]["bull"]["target"]["value_per_share"]
    page.heading("Investment thesis")
    page.paragraph(f"<b>The operating portfolio sets the valuation.</b> Existing mines and investments imply "
                   f"{_usd(base_spot)} in base spot NAV versus the {_usd(close)} reference close. The bull case "
                   f"values Newmont at {_usd(bull_target)} in 12 months. The reported-earnings peer median implies {_usd(peer)} at spot. "
                   "Higher gold prices and successful project development can raise NAV. [3, 4, 9, 13]")
    op = report.statements["operating_forecast"]
    volume = op["periods"][FY27]["metals"]["gold"]["volume"].attributable_gold_produced
    page.paragraph(f"<b>Mine execution drives near-term cash flow.</b> The operating forecast models {volume / 1000:.2f} million "
                   "attributable gold ounces in 2027. Newmont expects higher grades at Lihir and Boddington and first "
                   "commercial output from Tanami Expansion 2. The cash benefit depends on metal prices and site costs. [2, 3, 5, 13]")
    page.paragraph(f"<b>Development could extend the production tail.</b> The spot bridge includes the "
                   f"{_bn(case['spot']['bridge']['ngm_obligation_millions'])} Nevada Gold Mines obligation. "
                   "The model values existing operations and modeled development projects and assigns zero incremental value "
                   "to Fourmile and the unapproved Red Chris block cave. [2, 5, 10, 13]")
    target_range_chart(page, {key: value["target"]["value_per_share"] for key, value in nav["scenarios"].items()}, close)
    threshold = report.settings["scenarios"]["rating_threshold"]
    page.paragraph(f"Modeled total return includes {_usd(wb.number('Cover', 'B15'))} of dividends. "
                   f"Buy begins at {_pct(threshold, signed=True)} and Sell at {_pct(-threshold, signed=True)} or lower. "
                   "The range between the thresholds is Hold. [2, 13]", small=True)
    page.finish()


def _mine_volume_chart(page, report):
    page.heading("Attributable gold production through depletion")
    op = report.statements["operating_forecast"]
    volumes = [(2027, op["periods"][FY27]["metals"]["gold"]["volume"].attributable_gold_produced / 1000)]
    detail, register = report.nav["base_mine_cash"], op["mine_register"]
    end = max(row["model_production_end_year"] for row in report.nav["operating_mine_plans"].values())
    for year in range(2028, end + 1):
        ounces = fsum(rows.get(year, {}).get("production_and_sales", {}).get("gold", 0.0)
                      * (register[site]["ownership"] if register[site]["accounting"] == "full" else 1)
                      for site, rows in detail.items())
        volumes.append((year, ounces / 1000))
    canvas = page.canvas
    left, right = MARGIN + 32, MARGIN + BODY - 11
    top, bottom = page.y - 12, page.y - 103
    high = int(max(value for _, value in volumes) / 2 + 1) * 2
    years = [year for year, _ in volumes]
    pitch = (right - left) / len(years)
    canvas.saveState()
    canvas.setStrokeColor(RULE)
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 8)
    for amount in range(0, high + 1, 2):
        y = bottom + (top - bottom) * amount / high
        canvas.line(left, y, right, y)
        canvas.drawRightString(left - 7, y - 2.5, str(amount))
    for i, (year, value) in enumerate(volumes):
        x = left + i * pitch
        canvas.setFillColor(GOLD if i == 0 else HexColor("#BE9B63"))
        canvas.rect(x + 0.9, bottom, pitch - 1.8, (top - bottom) * value / high, fill=1, stroke=0)
        if i == 0 or year % 5 == 0 or i == len(years) - 1:
            canvas.setFillColor(INK)
            canvas.drawCentredString(x + pitch / 2, bottom - 13, str(year))
    canvas.restoreState()
    page.y = bottom - 20
    page.paragraph("Million ounces. Includes consolidated and proportionate operating mines, on an ownership-adjusted "
                   "basis. Excludes equity-method affiliates. [1, 4, 5, 13]", small=True)


def _operations_page(canvas, report, checks):
    page = _ReportPage(canvas, 2, report, "Industry and operating model", "Gold and the mine portfolio", checks)
    op, nav = report.statements["operating_forecast"], report.nav
    page.paragraph("<b>Investment demand matters.</b> The World Gold Council estimates 321 tonnes of Q3 net official "
                   "sector purchases and 211 tonnes of gold ETF inflows. Total gold demand including OTC fell 1% year "
                   "over year as jewelry and physical investment weakened. Flows can shift with real yields, the dollar "
                   "and risk appetite. [6]")
    page.paragraph("<b>Newmont's operating exposure.</b> Gold dominates sales. Cadia, Boddington and Red Chris "
                   "also produce copper. Peñasquito sells silver, lead and zinc, with a contractual silver stream. "
                   "The model forecasts production and sale volumes separately, applies realized-price adjustments, and "
                   "uses attributable volumes for ownership comparisons. [1, 2, 3, 4]")
    page.heading("Largest 2027 gold contributors")
    mines = op["periods"][FY27]["mines"]
    ranked = sorted(nav["operating_mine_plans"], key=lambda site: mines[site]["volumes"]["gold"].attributable_gold_produced, reverse=True)
    rows = [["Operating mine", "FY2026E (koz)", "FY2027E (koz)", "Last modeled year"]]
    for site in ranked[:6]:
        prior = op["periods"]["fy_2026"]["mines"][site]["volumes"]["gold"].attributable_gold_produced
        next_year = mines[site]["volumes"]["gold"].attributable_gold_produced
        rows.append([op["mine_register"][site]["name"], f"{prior:,.0f}", f"{next_year:,.0f}",
                     str(nav["operating_mine_plans"][site]["model_production_end_year"])])
    page.table(rows, [180, 110, 110, 128])
    page.paragraph("Site volumes represent attributable gold production. Model end years are analytical cutoffs. "
                   "Technical production profiles inform the forecast where published. Other paths use recoverable reserves "
                   "and disclosed mine-life context. [4, 5, 13]", small=True)
    _mine_volume_chart(page, report)
    page.finish()


def _financial_page(canvas, report, checks):
    page = _ReportPage(canvas, 3, report, "Financial forecasts", "Production and cash flow", checks)
    stm = report.statements
    op = stm["operating_forecast"]
    wb = report.workbook
    headings = ["FY2025A", "9M 2026A", "Q4 2026E", "FY2026E", "FY2027E"]
    gold = [op["historical_volumes"]["fy_2025"]["gold"], *(
        op["periods"][key]["metals"]["gold"]["volume"] for key in ("ytd_2026", Q4, "fy_2026", FY27)
    )]
    assumptions = op["assumptions"]
    site_growth = assumptions["fy_2027.attributable_gold_growth"]
    coproduct_growth = assumptions["fy_2027.penasquito_coproduct_change"]
    unit_cost_growth = assumptions["fy_2027.unit_cas_growth"]
    page.paragraph(f"Newmont guided to low-to-mid single-digit attributable gold growth in 2027. The linked forecast "
                   f"uses {_pct(site_growth, signed=True)} growth, a {_pct(coproduct_growth, signed=True)} change in Peñasquito "
                   f"coproduct output and {_pct(unit_cost_growth, signed=True)} unit cost growth before price-linked royalties. "
                   "Q4 estimates bridge nine-month results to the annual production, sales and cost outlook. [1, 2, 13]")
    page.heading("Linked operating and financial summary")
    rows = [["USD millions except ounces", *headings]]

    def values(sheet, row, sign=1):
        return [wb.number(sheet, f"{col}{row}") * sign for col in "BDEFG"]

    stats = {
        "Attributable gold produced (koz)": [vol.attributable_gold_produced for vol in gold],
        "Consolidated gold sold (koz)": [vol.sold for vol in gold],
        "Sales": values("Income statement", 6),
        "Costs applicable to sales": values("Income statement", 7, -1),
        "Parent net income": values("Income statement", 31),
        "Cash from operations": values("Cash flow", 13),
        "Consolidated cash capex": values("Cash flow", 15, -1),
        "Core consolidated unlevered FCF": [None, None, wb.number("Cash flow", "E25"), None, wb.number("Cash flow", "G25")],
        "Closing unrestricted cash": values("Cash flow", 27),
    }
    rows.extend([label, *(f"{value:,.0f}" if value is not None else "n.a." for value in values)] for label, values in stats.items())
    page.table(rows, [163, *([73] * 5)])
    page.paragraph("The statements show the selected case for each full period. The NAV applies scenario changes only after "
                   "the cutoff and holds near-term volumes and working capital fixed. Core FCF excludes affiliate cash "
                   "and earnings and includes 100% of Merian before the equity bridge. [1, 13]", small=True)
    page.heading("2027 sales by metal")
    metal_rows = [["Metal", "Sales volume", "Net realization", "Sales (USDm)"]]
    pounds = wb.number("Assumptions", "B26")
    for metal, label in METAL_LABELS.items():
        volume = op["periods"][FY27]["metals"][metal]["volume"]
        price = wb.number("Prices", f"G{49 + METALS.index(metal)}")
        sales = _million_quantity(metal, volume.sold, pounds) * price
        unit = "oz" if metal in ("gold", "silver") else "lb"
        metal_rows.append([label, f"{volume.sold:,.1f} {op['volume_units'][metal]}",
                           f"{_usd(price)}/{unit}", f"{sales:,.0f}"])
    page.table(metal_rows, [174, 118, 118, 118])
    stream = op["periods"][FY27]["noncash_stream_amortization_millions"]
    page.paragraph(f"Silver revenue includes ${stream:,.1f}m of noncash stream amortization. Mine cash flow excludes that amount. "
                   "The model recognizes base-metal prices in dollars per pound. [1, 3, 13]", small=True)
    page.heading("From statement cash flow to NAV")
    near = report.nav["base_dated_cash_flows"]
    price = op["modeled_prices"][FY27]["gold"]
    page.paragraph(f"The forecast starts from a ${price.benchmark_usd:,.0f}/oz gold benchmark. The base 2027 mine NAV uses "
                   f"{_bn(near[1]['consolidated_fcff_millions'])} in core unlevered cash flow after removing contingent "
                   f"Red Chris block cave spending. The cutoff leaves {_bn(near[0]['consolidated_fcff_millions'])} "
                   "of base Q4 cash flow. [2, 3, 10, 13]", small=True)
    page.finish()


def _valuation_page(canvas, report, checks):
    page = _ReportPage(canvas, 4, report, "Intrinsic valuation", "Finite-life net asset value", checks)
    case, wb = report.selected, report.workbook
    spot, target = case["spot"], case["target"]
    benchmarks = report.nav["long_term_benchmarks"]
    price_deck = benchmarks["world_bank_prices"]
    page.paragraph(f"The DCF discounts the Q4 stub, 2027 cash flow and subsequent mine plans at {_pct(wb.number('Cover', 'B8'))} "
                   f"nominal WACC. The 2028 World Bank reference is ${price_deck['gold']:,.0f}/oz gold, "
                   f"${price_deck['silver']:,.0f}/oz silver and ${price_deck['copper']:,.0f}/tonne copper. "
                   f"The model escalates the long-run deck at {benchmarks['nominal_inflation']:.0%} after 2028. [3, 4, 5, 7, 13]")
    page.heading(f"Mine value to common equity / {report.case.title()} case")
    bridge = [
        ("Operating mines less corporate costs, before closure", lambda d: d["mine_and_corporate_pv_millions"]),
        ("Reclamation and remediation PV", lambda d: -d["closure_pv_millions"]),
        ("Consolidated operating NAV", lambda d: d["consolidated_operating_nav_millions"]),
        ("Merian minority interest", lambda d: -d["merian_minority_nav_millions"]),
        ("Affiliate and other investments, including receivable", lambda d: d["nonoperating_investments_millions"]),
        ("Estimated unrestricted cash", lambda d: d["bridge"]["gross_cash_millions"]),
        ("Target cash adjustments", lambda d: d["bridge"]["net_cash_adjustment_millions"]),
        ("Notes and finance leases", lambda d: -d["bridge"]["debt_and_finance_leases_millions"]),
        ("Separate NGM cash payment", lambda d: -d["bridge"]["ngm_obligation_millions"]),
        ("Common equity NAV", lambda d: d["equity_nav_millions"]),
    ]
    rows = [["USD billions except per share", "Spot", "12-month target"]]
    rows.extend([label, _bn(calc(spot)), _bn(calc(target))] for label, calc in bridge)
    rows.append(["Value per diluted share", _usd(wb.number("Cover", "B9")), _usd(wb.number("Cover", "B10"))])
    page.table(rows, [303, 104, 121], extra=(("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),))
    marks = report.nav["investment_marks_millions"]
    page.paragraph(f"Lundin Gold ({_bn(marks['lundin_gold'])}) and LunR ({_bn(marks['lunr_royalties'])}) use gross quoted "
                   f"marks. Pueblo Viejo ({_bn(marks['pueblo_viejo'])}) uses a dated operator reserve-case NPV. "
                   "The target includes the NGM payment in cash. Cash adjustments account for stock compensation, "
                   "affiliate distributions and Merian minority cash. [1, 5, 8, 10, 13]",
                   small=True)
    page.heading("Base 12-month target sensitivity")
    s = report.nav["sensitivity"]
    shifts = s["gold_shift_usd"]
    rows = [["WACC / gold shift", *(f"{shift:+,.0f}" if shift else "0" for shift in shifts)]]
    rows.extend([_pct(wacc), *map(_usd, prices)] for wacc, prices in zip(s["wacc"], s["target"], strict=True))
    page.table(rows, [123, *([81] * 5)], extra=(
        ("BACKGROUND", (3, 3), (3, 3), PALE_GOLD), ("FONTNAME", (3, 3), (3, 3), "Helvetica-Bold"),
    ))
    base_col = shifts.index(0)
    center = len(shifts) // 2
    page.paragraph(f"Gold shifts are USD/oz across the base price path. At base WACC, "
                   f"a +${shifts[-1]:,.0f}/oz gold shock raises the target from {_usd(s['target'][center][base_col])} to "
                   f"{_usd(s['target'][center][-1])}. The workbook also includes the spot grid. [13]", small=True)
    page.finish()


def _relative_page(canvas, report, checks):
    page = _ReportPage(canvas, 5, report, "Relative valuation and risks", "Peers, catalysts and risks", checks)
    nav, wb = report.nav, report.workbook
    comps = nav["comparables"]
    page.paragraph(f"Newmont trades at {comps['subject_price_to_earnings']:.2f}x estimated trailing reported EPS of "
                   f"{_usd(comps['subject_ltm_eps_usd'])}. Peer prices use the same closing date. The range provides "
                   "an independent market cross-check for the finite-life NAV. [1, 2, 9, 11, 13]")
    page.heading("Gold miner trading comparison")
    rows = [["Company", "Close", "Trailing EPS", "P/E", "Implied NEM"]]
    for row in comps["peers"].values():
        rows.append([row["company"], _usd(row["close_usd"]), _usd(row["reported_ltm_diluted_eps_usd"]),
                     f"{row['price_to_earnings']:.2f}x", _usd(row["implied_nem_spot_usd"])])
    rows.append(["Peer median", "", "", f"{comps['median_price_to_earnings']:.2f}x", _usd(wb.number("Comps", "B10"))])
    page.table(rows, [162, 83, 95, 84, 104])
    page.paragraph("The model sums reported Q4 2025 and nine-month 2026 diluted EPS. Newmont uses U.S. GAAP. "
                   "Peers use IFRS. Disposal gains, impairments and tax items affect the comparisons. [1, 2, 11]", small=True)
    page.heading("Catalysts")
    page.paragraph("<b>Tanami and Ahafo North.</b> Newmont expects Tanami Expansion 2 commercial production in the "
                   "first half of 2027. At Ahafo North, ramp-up and higher-grade ore support the near-term outlook. "
                   "Track grades, throughput and cash costs as volumes rise. [2, 4]")
    page.paragraph("<b>Development decisions.</b> Newmont expects a Red Chris block-cave funding decision in early "
                   "2027. Fourmile's contribution could extend the Nevada operation, with development economics "
                   "sensitive to conversion of inferred resources, investment and timing. [2, 5, 10]")
    page.heading("Operating cases and risks")
    bear, bull = nav["scenarios"]["bear"], nav["scenarios"]["bull"]
    page.paragraph(f"<b>Bear {_usd(bear['target']['value_per_share'])} ({_pct(bear['total_return'], signed=True)}).</b> "
                   f"Gold falls ${abs(bear['gold_shift_usd']):,.0f}/oz, "
                   f"other metals fall {abs(bear['coproduct_price_shift']):.0%} and mine unit costs rise {bear['site_cost_shift']:.0%}. "
                   f"<b>Bull {_usd(bull['target']['value_per_share'])} ({_pct(bull['total_return'], signed=True)}).</b> "
                   "Equal shifts in the other direction lift cash margins. [13]")
    page.paragraph("The price shocks hold the reserve-based production profile fixed. Higher long-run gold prices, "
                   "reserve conversion or funded project approvals could increase economic mine life and value. "
                   "Lower grades, weaker recovery, permitting delays, higher closure costs or changes in mining taxes "
                   "would pressure cash flow. [1, 2, 4, 5, 13]")
    page.finish()


def _sources_page(canvas, report, checks):
    page = _ReportPage(canvas, 6, report, "Data and assumptions", "Sources and workbook", checks)
    register = report.nav["source_register"]

    def group(number, title, entries):
        page.source_group(number, title, tuple(
            (label, register[category][key]["url"] if category else key) for label, category, key in entries
        ))

    group(1, "Financial history", (("2025 Form 10-K", "production", "fy25_10k"), ("September 2026 Form 10-Q", "production", "q3_10q")))
    group(2, "Operating guidance", (("February guidance", "production", "feb_site_guidance"), ("October 22 results", "production", "q3_release")))
    group(3, "Commodity assumptions", (
        ("World Bank October monthly prices", "prices", "world_bank_november_pink_sheet"),
        ("October 2026-28 outlook", "nav-prices", "world_bank_october_outlook"),
        ("Wheaton silver stream", "prices", "wheaton_q3_2026"),
    ))
    group(4, "Mine reserves and operations", (
        ("2025 reserves", "mine-life", "reserve_release"), ("Boddington", "mine-life", "boddington_trs"),
        ("Cadia", "mine-life", "cadia_trs"), ("Lihir", "mine-life", "lihir_trs"), ("Ahafo", "mine-life", "ahafo_trs"),
    ))
    group(5, "Nevada Gold Mines and Pueblo Viejo", (
        ("American Barrick preliminary S-1", "mine-life", "barrick_s1"), ("NGM technical report", "mine-life", "ngm_trs"),
        ("Pueblo Viejo technical report", "mine-life", "pueblo_viejo_trs"),
    ))
    group(6, "Gold industry demand", (("World Gold Council Q3 2026 release", None, WORLD_GOLD_COUNCIL),))
    group(7, "Discount rate and inflation", (
        ("U.S. Treasury", "nav-valuation", "treasury_10_year"), ("Implied equity premium", "nav-valuation", "damodaran_november_erp"),
        ("Sector beta", "nav-valuation", "damodaran_precious_metals_beta"), ("Single-A yield", "nav-valuation", "ice_single_a_yield"),
        ("Federal Reserve", "nav-valuation", "fomc_long_run"),
    ))
    group(8, "Investment marks", (
        ("Lundin Gold ownership", "nav-valuation", "lug_circular"), ("Lundin Gold quote", "nav-valuation", "lug_stock_history"),
        ("LunR ownership", "nav-valuation", "lunr_investor_presentation"), ("LunR quote", "nav-valuation", "lunr_stock_history"),
        ("Bank of Canada FX", "nav-valuation", "boc_fx_november"),
    ))
    group(9, "November 11 share prices", (
        ("Newmont", "nav-valuation", "newmont_stock_history"), ("AEM", "comparables", "aem_close"),
        ("B", "comparables", "b_close"), ("KGC", "comparables", "kgc_close"),
    ))
    group(10, "Transaction and options", (
        ("NGM agreement", "financing", "ngm_agreement"), ("Fourmile assessment", "nav-valuation", "fourmile_initial_assessment"),
        ("Red Chris permit application", "nav-capital", "red_chris_joint_application"),
    ))
    group(11, "Peer reported earnings", tuple(
        (f"{ticker.upper()} {label}", "comparables", f"{ticker}_{key}")
        for ticker in ("aem", "b", "kgc") for label, key in (("Q4", "q4"), ("9M", "ytd"))
    ))
    group(12, "Debt and tax", (
        ("Newmont 2034 note terms", "nav-valuation", "newmont_2034_note_terms"), ("U.S. corporate tax", "nav-valuation", "irs_corporate_tax_rate"),
    ))
    page.heading("Material estimates")
    capital = report.nav["cost_of_capital"]
    wacc_inputs = report.settings
    page.paragraph(f"<b>Discount rate.</b> {_pct(wacc_inputs['risk_free_rate']['value'])} Treasury yield, "
                   f"{_pct(wacc_inputs['equity_risk_premium']['value'])} equity premium, {capital['levered_beta']:.3f} "
                   f"re-levered precious-metals beta and {_pct(wacc_inputs['pretax_cost_of_debt']['value'])} borrowing proxy. "
                   "Mine taxes, future recovery and project costs use analyst estimates where mine disclosures are incomplete. [4, 5, 7, 12]", small=True)
    tax = report.statements["periods"][FY27].income_statement["effective_tax_rate"]
    page.paragraph(f"<b>Cash flow.</b> The model uses a {tax:.0%} operating tax proxy and estimates working capital from sales, "
                   "production and historical turnover. It releases modeled working capital at mine wind-down. "
                   "Unlevered capex excludes estimated capitalized interest. [1, 2, 13]", small=True)
    liquidity = wacc_inputs["capital_structure"]["minimum_cash_framework_millions"]
    page.paragraph(f"<b>Cash and investments.</b> The bridge credits all estimated unrestricted cash. The issuer's "
                   f"{_bn(liquidity)} minimum cash framework is a liquidity objective. The Porcupine receivable uses "
                   "the four remaining contractual installments discounted at WACC. [1, 2, 13]", small=True)
    closure = report.nav["closure_payments_from_september"]
    residual = {year: value for year, value in closure.items() if year > 2028}
    years = " and ".join(str(year) for year in sorted(residual))
    page.paragraph(f"<b>Closure and NGM.</b> The model calibrates {_bn(fsum(residual.values()))} of nominal closure cash to "
                   f"effective dates in {years}. Actual payments can span decades. The preliminary S-1 leaves the NGM payment "
                   "date ambiguous. The model deducts the payment once using post-cutoff timing. [1, 5, 10, 13]", small=True)
    page.heading("Paired workbook")
    wb = report.workbook
    page.paragraph(f"<b>[13]</b> {escape(report.workbook_name)}. Verified {wb.cells_matched:,} saved cells against the "
                   "dated model, including formulas and calculated values.", small=True, gap=4)
    page.paragraph(f"Workbook SHA-256 {wb.sha256}", small=True, gap=4)
    if report.nav["mode"] == "guided":
        count = len(report.statements["operating_forecast"]["override_log"])
        page.paragraph(f"The workbook records {count} guided input changes and their reasons.", small=True, gap=4)
    page.require(wb.sha256)
    page.finish()


def _research_for_snapshot(workbook_name, wb, as_of, inputs_dir, case, guided):
    nav = run_nem_nav(as_of, inputs_dir, guided=guided)
    statements = run_nem_statements(as_of, inputs_dir, guided=guided)
    settings = _evidence(inputs_dir, "nav-valuation", as_of)
    report = _Research(as_of, case, workbook_name, wb, nav, statements, settings)
    selected = report.selected
    if (wb.day("Cover", "B3"), wb.day("Cover", "B7"), wb.day("Cover", "B14")) != (
        as_of, nav["comparables"]["quote_date"], nav["horizon"]
    ):
        raise ValueError("The workbook date, closing session and target date must match the report.")
    if (str(wb.value("Cover", "B5")).casefold(), str(wb.value("Cover", "B6")).casefold(), wb.value("Cover", "B12")) != (
        case, nav["mode"], selected["rating"]
    ):
        raise ValueError("The workbook case, mode and rating must match the report.")
    peer = nav["comparables"]["median_implied_nem_spot_usd"]
    metrics = {
        ("Cover", "B4"): nav["reference_close"]["price_usd"],
        ("Cover", "B8"): nav["cost_of_capital"]["wacc"],
        ("Cover", "B9"): selected["spot"]["value_per_share"],
        ("Cover", "B10"): selected["target"]["value_per_share"],
        ("Cover", "B11"): selected["total_return"],
        ("Cover", "B13"): peer,
        ("Cover", "B15"): selected["dividends_per_share"],
        ("Comps", "B10"): peer,
    }
    for (sheet, address), expected in metrics.items():
        if not isclose(wb.number(sheet, address), expected, rel_tol=1e-10, abs_tol=1e-6):
            raise ValueError(f"Workbook {sheet}!{address} differs from the dated research model.")
    return report


def _load_research(workbook, as_of, inputs_dir, case, guided):
    from .nem_fresh_excel import load_verified_nem_workbook

    verified = load_verified_nem_workbook(workbook, as_of, inputs_dir, case=case, guided=guided)
    return _research_for_snapshot(workbook.name, verified, as_of, inputs_dir, case, guided)


def _render_pdf(report):
    return _make_pdf(report, "Newmont Corporation", (
        _investment_page, _operations_page, _financial_page, _valuation_page, _relative_page, _sources_page,
    ))


def verify_nem_fresh_pdf(workbook: Path, pdf: Path, *, as_of: date, inputs_dir: Path,
                         case: str = "base", guided: Path | None = None) -> dict:
    """Verify the report's content, citations, charts and exact workbook partner."""
    report = _load_research(workbook, as_of, inputs_dir, case, guided)
    expected, checks = _render_pdf(report)
    if _inspect_pdf(pdf.read_bytes(), report, checks, PAGE_COUNT) != _inspect_pdf(expected, report, checks, PAGE_COUNT):
        raise ValueError("The PDF content differs from the dated workbook-backed report.")
    return _result(report, checks, PAGE_COUNT)


def export_nem_fresh_pdf(workbook: Path, output: Path, *, as_of: date, inputs_dir: Path,
                         case: str = "base", guided: Path | None = None) -> dict:
    """Verify the saved workbook, bind its SHA-256 and publish the dated research PDF."""
    if output.suffix.lower() != ".pdf":
        raise ValueError("The research report destination must have a .pdf extension.")
    if output.exists() or output.is_symlink():
        raise FileExistsError("Choose a new PDF output filename.")
    report = _load_research(workbook, as_of, inputs_dir, case, guided)
    content, checks = _render_pdf(report)
    _inspect_pdf(content, report, checks, PAGE_COUNT)
    _publish_pdf(content, output)
    return _result(report, checks, PAGE_COUNT)

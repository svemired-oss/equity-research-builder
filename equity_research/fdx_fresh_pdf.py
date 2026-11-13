"""Generate a dated FedEx research report from an exact verified workbook snapshot."""

from dataclasses import dataclass
from datetime import date
from math import isclose
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib.colors import HexColor

from .fdx_current import run_current_fdx
from .fdx_current_valuation import FDXValuationCase, run_fdx_valuation
from .fdx_forward_statements import _dated_inputs, run_fdx_statements
from .gd_fresh_pdf_charts import revenue_chart, target_range_chart
from .gd_fresh_pdf_workbook import VerifiedPDFWorkbook
from .gd_pdf import _date_label, _usd
from .research_pdf import (
    _ReportPage as _ResearchPage, _inspect_pdf as _inspect_research_pdf, _bn, _cards, _make_pdf, _pct, _publish_pdf, _result,
)


PAGE_COUNT = 6
PURPLE = HexColor("#4B2C72")
LAVENDER = HexColor("#E7DDF2")
CENSUS = "https://www2.census.gov/retail/releases/historical/ecomm/26q2.pdf"
SEGMENTS = (
    ("Express U.S. Domestic", "express_us_domestic", "express_us_domestic"),
    ("Express International", "express_international", "express_international"),
    ("Corporate / eliminations", "corporate_other_eliminations", "corporate_other_and_eliminations"),
)


@dataclass(frozen=True)
class _Research:
    as_of: date
    case: str
    workbook_name: str
    workbook: VerifiedPDFWorkbook
    current: dict
    history: dict
    historical_capex: float
    year_to_date_capex: float
    valuation: dict
    statements: dict
    revenue_forecasts: dict

    @property
    def selected(self) -> FDXValuationCase:
        return self.valuation["scenarios"][self.case]


class _ReportPage(_ResearchPage):
    def __init__(self, canvas, number, report, section, title, checks):
        super().__init__(canvas, number, report, section, title, checks, page_count=PAGE_COUNT,
                         identity="FEDEX CORPORATION   /   NYSE: FDX", accent=PURPLE)


def _investment_page(canvas, report, checks):
    page = _ReportPage(canvas, 1, report, "Investment view", "FedEx Corporation", checks)
    r, v, wb = report.selected, report.valuation, report.workbook
    close = wb.number("Cover", "B4")
    page.paragraph(f"{report.case.title()} case / {v['mode'].title()} assumptions / {_date_label(report.as_of)} pre-open. "
                   f"Reference close {_usd(close)} on {_date_label(v['quote_date'])}. "
                   f"Target date {_date_label(v['horizon'])}. [10, 13]")
    _cards(page, (
        ("Model rating", str(wb.value("Cover", "B12"))), ("Spot intrinsic value", _usd(wb.number("Cover", "B9"))),
        ("12-month target", _usd(wb.number("Cover", "B10"))), ("12-month total return", _pct(wb.number("Cover", "B11"), signed=True)),
    ))
    page.heading("Investment thesis")
    page.paragraph("<b>Domestic earnings depend on realized price and delivery density.</b> FedEx reported higher "
                   "package yields and better mix in Q3. The January list-rate increase creates a pricing opportunity, "
                   "with actual yield dependent on contracts, mix and surcharges. Network consolidation can spread fixed "
                   "costs across denser routes. [1, 2, 4]")
    page.paragraph("<b>International has room to improve margins.</b> Export volumes and freight weight helped Q3 results. "
                   "Trade policy and purchased transportation costs can offset those gains. The forecast varies both "
                   "volume and yield across the cross-border package and air freight services. [1, 2]")
    page.paragraph(f"<b>Cash conversion drives the valuation.</b> The {report.case} case projects "
                   f"{_bn(report.statements['years'][0].cash_flow['unlevered_free_cash_flow'])} of 2027 unlevered free cash flow. "
                   f"The closest peer's adjusted earnings imply {_usd(v['comparables'].ups_adjusted.implied_pe_usd)} "
                   f"to {_usd(v['comparables'].ups_adjusted.implied_ev_ebit_usd)} per FedEx share. "
                   f"Terminal value supplies {r.spot.terminal_share_of_enterprise_value:.0%} of spot enterprise value. [4, 8, 11, 13]")
    target_range_chart(page, {key: scenario.target.value_per_share for key, scenario in v["scenarios"].items()}, close)
    policy = v["rating_policy"]
    page.paragraph(f"The target implies {_pct(r.price_return, signed=True)} price return. Total return includes "
                   f"{_usd(r.modeled_dividends_per_share)} of modeled dividends. "
                   f"Buy begins at {_pct(policy.buy_at_or_above, signed=True)} total return and Sell at "
                   f"{_pct(policy.sell_at_or_below, signed=True)} or lower. The range between the thresholds is Hold. [13]", small=True)
    buy_price = close * (1 + policy.buy_at_or_above) - r.modeled_dividends_per_share
    page.paragraph(f"The price target needed to reach the Buy threshold is {_usd(buy_price)} at the modeled dividend. [13]", small=True)
    page.finish()


def _operations_page(canvas, report, checks):
    page = _ReportPage(canvas, 2, report, "Operating model", "Parcel and air freight", checks)
    operating = report.statements["operating_forecast"]
    hist, ytd = report.history["2025"], report.history["2026_ytd"]
    first, last = operating.years[0], operating.years[-1]
    page.paragraph("<b>Parcel demand and competition.</b> U.S. online retail sales rose 12.2% year over year in Q2 2026 "
                   "on a seasonally adjusted nominal basis and reached 17.1% of retail sales. Parcel demand also depends "
                   "on order size, merchant fulfillment and carrier market share. UPS competes across domestic and international "
                   "networks. Expeditors and C.H. Robinson provide narrower forwarding and brokerage comparisons. [6, 11]")
    page.paragraph("<b>Reporting basis.</b> FedEx Freight separated on June 1, 2026. The forecast keeps Express freight "
                   "within the parcel and air network and uses the December year-end. FedEx Supply Chain closed its sale "
                   "on October 1. Its standalone sales were unavailable, so the model estimates ongoing corporate revenue "
                   "from the Q4 residual and the historical seasonal share. [1, 2, 3, 5]")
    page.heading("Revenue and operating margin by segment")
    rows = [["Segment", "CY2025A", "9M CY2026A", "CY2027E", "CY2031E"]]
    for label, historical_key, forecast_key in SEGMENTS:
        actuals = [period["segments"][historical_key] for period in (hist, ytd)]
        forecasts = [(year.segment_revenue_millions[forecast_key], year.segment_operating_margin[forecast_key]) for year in (first, last)]
        values = [(row["revenue"], row["operating_income"] / row["revenue"]) for row in actuals]
        rows.append([label, *(f"{_bn(sales)} / {margin:.1%}" if forecast_key != SEGMENTS[-1][2] else _bn(sales)
                              for sales, margin in (*values, *forecasts))])
    page.table(rows, [144, 96, 96, 96, 96])
    ground = first.services["us_ground"]
    page.paragraph(f"The model forecasts daily volume times yield times operating days for nine services. "
                   f"It removes {_bn(operating.opening.fuel_surcharge_removed_millions)} of estimated incremental "
                   f"2026 fuel surcharge revenue from opening service yields. In 2027, the selected case uses "
                   f"{_pct(ground.volume_growth, signed=True)} Ground volume growth and "
                   f"{_pct(ground.yield_growth, signed=True)} yield growth across {first.operating_days} operating days. [2, 3, 13]", small=True)
    revenue_chart(page, tuple((year, report.history[str(year)]["revenue"] / 1000) for year in (2024, 2025)),
                  report.revenue_forecasts, report.case)
    page.finish()


def _financial_page(canvas, report, checks):
    page = _ReportPage(canvas, 3, report, "Financial forecasts", "Earnings and cash flow", checks)
    statements, current = report.statements, report.current
    q4, years = statements["q4"], statements["years"]
    prior = report.history["2025"]
    revenue_26 = current["calendar_year"]["forecast_revenue_millions"]
    g = current["guidance_bridge"]
    oi_26 = g["cy26_adjusted_operating_income_ytd_sum_millions"] + q4.income_statement["operating_income"]
    ni_26 = g["cy26_adjusted_net_income_ytd_sum_millions"] + q4.income_statement["net_income"]
    page.paragraph(f"The model estimates {_bn(q4.income_statement['revenue'])} in Q4 revenue, "
                   f"{_bn(q4.income_statement['operating_income'])} in adjusted operating income, "
                   f"and {_usd(q4.income_statement['diluted_eps_proxy'])} in adjusted diluted EPS. "
                   f"The EPS estimate uses the midpoint of management's {_usd(g['q4_adjusted_eps_guidance'][0])} to "
                   f"{_usd(g['q4_adjusted_eps_guidance'][1])} adjusted range. [2, 13]")
    page.heading("Linked forecast summary")
    rows = [["USD millions", "2025A", "2026E", *(f"{year.period_end.year}E" for year in years)]]
    stats = (
        ("Revenue", prior["revenue"], revenue_26, lambda year: year.income_statement["revenue"]),
        ("Operating income", prior["operating_income"], oi_26, lambda year: year.income_statement["operating_income"]),
        ("Operating margin", prior["operating_income"] / prior["revenue"], oi_26 / revenue_26,
         lambda year: year.income_statement["operating_income"] / year.income_statement["revenue"]),
        ("Continuing net income", prior["continuing_net_income"], ni_26, lambda year: year.income_statement["net_income"]),
        ("Cash from operations", None, None, lambda year: year.cash_flow["cash_from_operations"]),
        ("Cash capex", report.historical_capex, report.year_to_date_capex - q4.cash_flow["capex"],
         lambda year: -year.cash_flow["capex"]),
        ("Unlevered free cash flow", None, None, lambda year: year.cash_flow["unlevered_free_cash_flow"]),
        ("Closing cash", None, q4.balance_sheet.cash_before_reserve, lambda year: year.balance_sheet.cash_before_reserve),
        ("Notes and finance leases", None, q4.balance_sheet.notes + q4.balance_sheet.finance_leases,
         lambda year: year.balance_sheet.notes + year.balance_sheet.finance_leases),
    )
    for label, actual, estimate, get in stats:
        fmt = ".1%" if label == "Operating margin" else ",.0f"
        rows.append([label, *(format(val, fmt) if val is not None else "n.a." for val in (actual, estimate, *map(get, years)))])
    page.table(rows, [140, *([388 / 7] * 7)])
    page.paragraph("2025 earnings are recast GAAP continuing results. 2026 earnings use adjusted continuing results "
                   "and forecast Q4. Pre-spin cash flow includes Freight, so comparable continuing CFO and FCFF are "
                   "unavailable for 2025 and 2026. Modeled closing cash excludes tariff refunds held for customers. [1, 2, 3, 13]",
                   small=True)
    page.heading("Working capital and capital investment")
    first = years[0].income_statement
    page.paragraph(f"The CY2027 forecast uses the fuel-normalized service base and the post-sale corporate revenue proxy. "
                   f"The selected case models {_pct(first['revenue'] / revenue_26 - 1, signed=True)} revenue growth and "
                   f"{_pct(first['operating_income'] / oi_26 - 1, signed=True)} operating income growth from CY2026. [2, 5, 13]")
    mid = years[2]
    targets = statements["operating_forecast"].targets_2029
    page.paragraph(f"Management targets approximately {_bn(targets['revenue_millions'])} of 2029 revenue "
                   f"and {_bn(targets['operating_income_millions'])} of operating income. The selected case reaches "
                   f"{_bn(mid.income_statement['revenue'])} and {_bn(mid.income_statement['operating_income'])}, respectively. "
                   f"Cash capex reaches {-mid.cash_flow['capex'] / mid.income_statement['revenue']:.1%} of revenue. [4, 13]")
    page.paragraph(f"The modeled Q4 continuing cash bridge estimates {_bn(q4.cash_flow['cash_from_operations'])} of operating cash "
                   f"after customer pass-throughs and {_bn(-q4.cash_flow['capex'])} of capex. After the November cutoff, "
                   f"{_bn(report.selected.spot.projected_cash_flows[0].amount_millions)} of Q4 FCFF remains. "
                   "The workbook models balance-sheet funding, leases and debt principal separately. [1, 2, 9, 13]")
    page.paragraph("Unlevered cash flow applies the modeled tax rate to operating earnings, adds depreciation and "
                   "amortization, and reflects capex, working capital and voluntary pension funding. It keeps stock "
                   "compensation as an expense. [1, 2, 13]", small=True)
    page.finish()


def _valuation_page(canvas, report, checks):
    page = _ReportPage(canvas, 4, report, "Intrinsic value", "Discounted cash flow", checks)
    r, wb = report.selected, report.workbook
    cap, terminal = report.valuation["cost_of_capital"], r.spot.terminal
    page.paragraph(f"{_pct(wb.number('Cover', 'B13'))} WACC uses a {_pct(cap.risk_free_rate)} Treasury yield, "
                   f"a {_pct(cap.equity_risk_premium)} equity risk premium, a {cap.levered_beta:.3f} re-levered transportation "
                   f"beta and a {_pct(cap.pretax_cost_of_debt)} BBB borrowing-cost proxy. Terminal growth of "
                   f"{_pct(terminal.growth)} at {_pct(terminal.return_on_new_invested_capital)} marginal ROIC requires "
                   f"reinvestment of {terminal.reinvestment_rate:.1%} of terminal NOPAT. [4, 8, 12, 13]")
    page.heading("Enterprise value to equity value")
    rows = [["USD billions except per share", "Spot", "12-month target"]]
    stats = (
        ("PV of explicit free cash flow", lambda dcf: dcf.enterprise_value_millions - dcf.terminal_present_value_millions),
        ("PV of terminal free cash flow", lambda dcf: dcf.terminal_present_value_millions),
        ("Enterprise value", lambda dcf: dcf.enterprise_value_millions),
        ("Excess cash", lambda dcf: dcf.bridge.excess_cash_millions),
        ("Retained FedEx Freight stake", lambda dcf: dcf.bridge.fdxf_millions),
        ("InPost vehicle interest", lambda dcf: dcf.bridge.inpost_millions),
        ("Other investment securities", lambda dcf: dcf.bridge.debt_securities_millions + dcf.bridge.other_equity_securities_millions),
        ("Debt including finance leases", lambda dcf: -dcf.bridge.debt_including_finance_leases_millions),
        ("Common equity value", lambda dcf: dcf.equity_value_millions),
    )
    rows.extend([label, _bn(get(r.spot)), _bn(get(r.target))] for label, get in stats)
    rows.append(["Value per diluted share", _usd(wb.number("Cover", "B9")), _usd(wb.number("Cover", "B10"))])
    page.table(rows, [278, 125, 125], extra=(("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),))
    roll = r.horizon_cash_roll
    page.paragraph(f"The model deducts a {_bn(r.spot.bridge.reserve_millions)} operating reserve. The target cash "
                   f"and debt roll uses {_bn(roll.scheduled_note_principal_paid_millions)} of scheduled note payments "
                   f"and {_bn(roll.modeled_finance_lease_principal_paid_millions)} of modeled lease principal. "
                   "The remaining cash flows are discounted from the target date. The cash roll deducts post-cutoff stock "
                   "compensation to reflect the economic cost at fixed modeled shares. [1, 7, 9, 13]", small=True)
    page.heading("12-month target sensitivity")
    grid = r.target_sensitivity
    rows = [["WACC / growth", *(f"{value:.1%}" for value in grid.growth)]]
    rows.extend([_pct(wacc), *map(_usd, prices)] for wacc, prices in zip(grid.wacc, grid.value_per_share, strict=True))
    page.table(rows, [101, *([61] * 7)], extra=(
        ("BACKGROUND", (4, 4), (4, 4), LAVENDER), ("FONTNAME", (4, 4), (4, 4), "Helvetica-Bold"),
    ))
    high_wacc_target = grid.value_per_share[4][3]
    high_wacc_return = (high_wacc_target + r.modeled_dividends_per_share) / report.valuation["reference_close_usd"] - 1
    page.paragraph(f"The grid recalculates terminal reinvestment for each growth rate. At {_pct(grid.wacc[4])} WACC "
                   f"and {_pct(grid.growth[3])} growth, the target is {_usd(high_wacc_target)} "
                   f"with {_pct(high_wacc_return, signed=True)} total return. [13]", small=True)
    page.finish()


def _comparables_page(canvas, report, checks):
    page = _ReportPage(canvas, 5, report, "Relative valuation and risks", "Peers, catalysts and risks", checks)
    comps, v = report.valuation["comparables"], report.valuation
    wb = report.workbook
    page.paragraph(f"FedEx trades at {comps.subject_ev_to_ebit:.2f}x adjusted continuing LTM EBIT and "
                   f"{comps.subject_price_to_earnings:.2f}x adjusted continuing LTM earnings through "
                   f"{_date_label(comps.ltm_end)}. The trading comparisons use the same market close and serve as "
                   "spot checks. The model rating follows the DCF. [2, 3, 10, 11, 13]")
    page.heading("FedEx spot value implied by peer trading multiples")
    rows = [["Benchmark", "EV / EBIT", "P / E", "EV / EBIT price", "P / E price"]]
    for label, col, benchmark in (
        ("UPS GAAP", "B", comps.ups), ("UPS issuer-adjusted", "C", comps.ups_adjusted),
        ("UPS / EXPD median, GAAP", "D", comps.screened_median),
        ("UPS / EXPD / CHRW median, GAAP", "E", comps.broad_median),
    ):
        rows.append([label, f"{benchmark.ev_to_ebit:.2f}x", f"{benchmark.price_to_earnings:.2f}x",
                     _usd(wb.number("Comps", f"{col}9")), _usd(wb.number("Comps", f"{col}10"))])
    page.table(rows, [188, 75, 67, 103, 95])
    page.paragraph("UPS is the closest parcel network. Transformation and impairment charges affect its GAAP multiple. "
                   "UPS and FedEx use different earnings adjustments. FedEx trailing EPS uses an inferred Q4 diluted share count. "
                   "FedEx enterprise value deducts excess cash and separately valued investments. Peer enterprise values "
                   "deduct reported cash and equivalents. Expeditors is asset-light, and C.H. Robinson's pending RXO "
                   "acquisition limits its standalone comparison. [11, 13]", small=True)
    page.heading("Catalysts and operating cases")
    guidance = report.current["guidance_bridge"]["management_anchors"]
    price = guidance["calendar_2027"]
    network = guidance["network_optimization"]
    page.paragraph(f"<b>2027 pricing and network savings.</b> The January 4 parcel list-rate increase averages "
                   f"{price['parcel_list_rate_increase']:.1%}. The 2027 cumulative structural savings target is "
                   f"{_bn(network['cy27_cumulative_structural_savings_target_millions'])}. Watch realized yield, "
                   "parcel density and segment margin for evidence of conversion. [1, 2, 4]")
    page.paragraph("<b>Freight stake monetization.</b> FedEx aims to monetize its retained stake within 12 months of "
                   "September 30, subject to market conditions. Timing, net proceeds, taxes and the transaction form "
                   "remain uncertain. The DCF carries the gross stake value. [1, 7]")
    bear, bull = v["scenarios"]["bear"], v["scenarios"]["bull"]
    page.paragraph(f"<b>Bear {_usd(bear.target.value_per_share)} ({_pct(bear.total_return, signed=True)}).</b> "
                   f"Weak volumes and pricing limit network leverage, while wage and purchased transportation costs "
                   f"constrain margin. <b>Bull {_usd(bull.target.value_per_share)} ({_pct(bull.total_return, signed=True)}).</b> "
                   "Premium mix, stronger yield and network savings drive faster margin expansion. [2, 4, 13]")
    page.heading("Risks to the recommendation")
    page.paragraph("Cross-border tariffs and trade policy can redirect shipments and raise compliance costs. Labor and "
                   "aircraft expenses create operating leverage if volumes fall. Higher WACC compresses long-duration value. "
                   "The InPost value uses recent transaction cost for the consortium interest, whose current fair value is "
                   "unobserved. The model uses estimated interim operating cash timing and assumes no new repurchases. [1, 5, 7, 8, 9, 13]")
    page.finish()


def _sources_page(canvas, report, checks):
    page = _ReportPage(canvas, 6, report, "Data and assumptions", "Sources and workbook", checks)
    sources = report.valuation["source_register"]

    def group(number, label, items):
        links = []
        for name, category, key in items:
            url = sources[category][key]["url"] if category else key
            links.append((name, url))
        page.source_group(number, label, links)

    group(1, "Quarterly filing", (("September 2026 Form 10-Q", "operations", "cy26_q3_10q"),))
    group(2, "Operating data and guidance", (
        ("October earnings release", "operations", "cy26_q3_release"),
        ("October presentation", "operations", "cy26_q3_presentation"),
    ))
    group(3, "Calendar-year recasts", (
        ("CY2024 and CY2025", "operations", "cy24_cy25_recast"), ("CY2026 Q1 and Q2", "operations", "cy26_q1_q2_recast"),
    ))
    group(4, "Management targets", (("2026 Investor Day", "operations", "investor_day"),))
    group(5, "Supply Chain sale", (("October 1 completion", "operations", "supply_chain_sale"),))
    group(6, "Parcel market context", (("U.S. Census Q2 2026 retail e-commerce, August 18 release", None, CENSUS),))
    group(7, "Retained investments", (
        ("Freight Schedule 13G", "valuation_bridge", "freight_13g"),
        ("November 11 FDXF close", "valuation_bridge", "freight_close_ft"),
        ("InPost final tender results", "valuation_bridge", "inpost_final_tender"),
        ("InPost financing statement", "valuation_bridge", "inpost_position_statement"),
    ))
    group(8, "Cost of capital", (
        ("Treasury 10-year yield", "discount", "treasury_10_year"), ("Implied equity premium", "discount", "damodaran_november_erp"),
        ("Sector beta", "discount", "damodaran_transportation_beta"), ("BBB debt proxy", "discount", "ice_bbb_yield"),
        ("FedEx note terms", "discount", "fedex_2036_notes"),
    ))
    group(9, "Debt and dividends", (
        ("2026 Form 10-K", "financing", "may_10k"),
        ("Euro note terms", "financing", "euro_note_terms"),
        ("Aircraft certificate schedule", "financing", "aircraft_certificates_prospectus"),
        ("FedEx dividend history", "financing", "fedex_dividend_history"),
    ))
    group(10, "November 11 stock prices", (
        ("FDX", "comparables", "fdx_close"), ("UPS", "comparables", "ups_close"),
        ("EXPD", "comparables", "expd_close"), ("CHRW", "comparables", "chrw_close"),
    ))
    group(11, "Peer filings and releases", tuple(
        (f"{ticker} {name}", "comparables", f"{ticker.lower()}_{suffix}")
        for ticker in ("UPS", "EXPD", "CHRW") for name, suffix in (("10-Q", "10q"), ("Q4 release", "q4"))
    ))
    group(12, "Long-run growth context", (("Federal Reserve economic projections", "discount", "fomc_long_run"),))
    page.heading("Assumptions with the most effect")
    page.paragraph("<b>Operating estimates.</b> The model applies calendar-day timing to estimated cash flows. "
                   "Continuing-only nine-month cash flow, exact tariff refund timing and standalone Supply Chain "
                   "operating results were unavailable. Q4 company cash and free cash flow are estimates. [1, 2, 5]", small=True)
    page.paragraph("<b>Capital and value.</b> Treasury and BBB yields proxy financing inputs. The terminal ROIC is an "
                   "analyst assumption calibrated to the company's 2029 company-wide target. Net Freight proceeds and "
                   "InPost's fair value are unavailable. The model holds both asset marks constant through the target date. [4, 7, 8]", small=True)
    page.heading("Paired workbook")
    wb = report.workbook
    page.paragraph(f"<b>[13]</b> {escape(report.workbook_name)}. Verified {wb.cells_matched:,} saved workbook cells "
                   "against the dated model, including cached valuation figures and editable formulas.", small=True, gap=4)
    page.paragraph(f"Workbook SHA-256 {wb.sha256}", small=True, gap=4)
    if report.valuation["mode"] == "guided":
        count = len(report.valuation["override_log"])
        page.paragraph(f"This run applied {count} guided input changes. The workbook records the inputs and reasons.", small=True, gap=4)
    page.require(wb.sha256)
    page.finish()


def _load_research(workbook, as_of, inputs_dir, case, guided):
    from .fdx_fresh_excel import load_verified_fdx_workbook

    verified = load_verified_fdx_workbook(workbook, as_of, inputs_dir, case=case, guided=guided)
    return _research_for_snapshot(workbook.name, verified, as_of, inputs_dir, case, guided)


def _research_for_snapshot(workbook_name, workbook, as_of, inputs_dir, case, guided):
    current = run_current_fdx(as_of, inputs_dir, guided=guided)
    actuals = _dated_inputs(inputs_dir, "actuals", as_of)
    valuation = run_fdx_valuation(as_of, inputs_dir, guided=guided)
    cases = {name: run_fdx_statements(as_of, inputs_dir, case=name, guided=guided) for name in valuation["scenarios"]}
    starting_revenue = current["calendar_year"]["forecast_revenue_millions"] / 1000
    paths = {
        name: ((as_of.year, starting_revenue), *((year.period_end.year, year.income_statement["revenue"] / 1000)
                                                  for year in statements["years"]))
        for name, statements in cases.items()
    }
    research = _Research(as_of, case, workbook_name, workbook, current, actuals["income_statements"],
                         actuals["capital_spending"]["2025"]["total"], actuals["capital_spending"]["2026_ytd"]["total"],
                         valuation, cases[case], paths)
    r = research.selected
    if workbook.day("Cover", "B3") != as_of or str(workbook.value("Cover", "B5")).casefold() != case:
        raise ValueError("The workbook case and valuation date must match the report.")
    if workbook.value("Cover", "B12") != r.recommendation:
        raise ValueError("The workbook recommendation must match the research model.")
    for cell, expected in (
        ("B4", valuation["reference_close_usd"]), ("B9", r.spot.value_per_share), ("B10", r.target.value_per_share),
        ("B11", r.total_return), ("B13", valuation["cost_of_capital"].wacc),
    ):
        if not isclose(workbook.number("Cover", cell), expected, rel_tol=1e-10, abs_tol=1e-6):
            raise ValueError(f"Workbook Cover!{cell} differs from the dated research model.")
    return research


def _render_pdf(report):
    return _make_pdf(report, "FedEx Corporation", (
        _investment_page, _operations_page, _financial_page, _valuation_page, _comparables_page, _sources_page,
    ))


def _inspect_pdf(content, report, checks):
    return _inspect_research_pdf(content, report, checks, PAGE_COUNT)


def verify_fdx_fresh_pdf(workbook: Path, pdf: Path, *, as_of: date, inputs_dir: Path,
                         case: str = "base", guided: Path | None = None) -> dict:
    """Verify the report's values, chart content, source links and exact workbook partner."""
    report = _load_research(workbook, as_of, inputs_dir, case, guided)
    expected, checks = _render_pdf(report)
    if _inspect_pdf(pdf.read_bytes(), report, checks) != _inspect_pdf(expected, report, checks):
        raise ValueError("The PDF content differs from the dated workbook-backed report.")
    return _result(report, checks, PAGE_COUNT)


def export_fdx_fresh_pdf(workbook: Path, output: Path, *, as_of: date, inputs_dir: Path,
                         case: str = "base", guided: Path | None = None) -> dict:
    """Verify the Excel snapshot and publish a PDF linked to its SHA-256."""
    if output.suffix.lower() != ".pdf":
        raise ValueError("The research report destination must have a .pdf extension.")
    if output.exists() or output.is_symlink():
        raise FileExistsError("Choose a new PDF output filename.")
    report = _load_research(workbook, as_of, inputs_dir, case, guided)
    content, checks = _render_pdf(report)
    _inspect_pdf(content, report, checks)
    _publish_pdf(content, output)
    return _result(report, checks, PAGE_COUNT)

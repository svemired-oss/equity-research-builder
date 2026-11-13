"""Build the FedEx workbook's trading comparables and public source register."""

from datetime import date

from openpyxl.utils.datetime import to_excel

from .fdx_forward_statements import _dated_inputs
from .fdx_statements import USD


DATE_FORMAT = "mmm d, yyyy"
MULTIPLE = '0.00"x"'


def add_fdx_comparables(plan, inputs_dir, as_of, valuation):
    sheet = "Comps"
    comps = valuation["comparables"]
    ltm = valuation["comps_subject_ltm"]
    raw = _dated_inputs(inputs_dir, "comps", as_of)
    operations = _dated_inputs(inputs_dir, "operations", as_of)
    actuals = _dated_inputs(inputs_dir, "actuals", as_of)
    plan.set(sheet, "A1", "Trading comparables", "title")
    plan.set(sheet, "A2", "USD millions except per-share data and multiples", "subtitle")
    plan.set(sheet, "A4", "Implied FedEx spot value", "section")
    plan.set(sheet, "A13", "Trailing earnings and enterprise value", "section")
    plan.set(sheet, "A15", "USD millions unless indicated", "header")
    for row, label in {
        6: "EV / EBIT", 7: "P / E", 9: "Implied FDX price on EV / EBIT", 10: "Implied FDX price on P / E",
        16: "Unadjusted close date", 17: "Unadjusted close (USD)", 18: "Basic shares (millions)", 19: "Equity market value",
        20: "Financing debt incl. finance leases", 21: "Preferred equity", 22: "Noncontrolling interest",
        23: "Deductible cash", 24: "Other nonoperating investments", 25: "Enterprise value",
        27: "December 2025 quarter EBIT", 28: "September 2026 nine-month EBIT", 29: "Trailing EBIT",
        30: "December 2025 quarter net income", 31: "September 2026 nine-month net income", 32: "Trailing net income",
        33: "December 2025 quarter diluted shares", 34: "September 2026 nine-month diluted shares",
        35: "Trailing weighted diluted shares", 36: "Trailing diluted EPS (USD)", 38: "EV / EBIT", 39: "P / E",
    }.items():
        plan.set(sheet, f"A{row}", label, "total" if row in (9, 10, 19, 25, 29, 32, 35, 36, 38, 39) else "")

    subject_q4 = operations["q4_bridge"]["cy25_q4_adjusted_earnings"]
    subject_ytd = actuals["income_statements"]["2026_ytd"]
    plan.set(sheet, "A42", "Earnings period weights", "section")
    plan.set(sheet, "A43", "December 2025 quarter days")
    plan.set(sheet, "A44", "September 2026 nine-month days")
    plan.set(sheet, "A45", "FDX Q4 2025 adjusted EPS (reported, USD)")
    plan.set(sheet, "B43", comps.peers[0].prior_fourth_quarter.days, "actual", "0")
    plan.set(sheet, "B44", comps.peers[0].current_interim.days, "actual", "0")
    plan.set(sheet, "B45", subject_q4["diluted_eps"], "actual", USD)

    plan.set(sheet, "B15", "FDX adjusted", "header")
    plan.calc(sheet, "B16", "DCF!B4", to_excel(valuation["quote_date"]), fmt=DATE_FORMAT)
    plan.calc(sheet, "B17", "Cover!B4", comps.subject_close.price, fmt=USD)
    plan.calc(sheet, "B18", "DCF!B43", comps.subject_shares_millions)
    plan.calc(sheet, "B20", "DCF!B39", comps.subject_debt_millions)
    plan.calc(sheet, "B23", "DCF!B38", comps.subject_cash_millions)
    plan.calc(sheet, "B24", "DCF!B40", comps.subject_nonoperating_assets_millions)
    for row, value in {
        27: subject_q4["operating_income_millions"],
        28: comps.subject_ebit_millions - subject_q4["operating_income_millions"],
        30: subject_q4["net_income_millions"],
        31: ltm["adjusted_net_income_millions"] - subject_q4["net_income_millions"],
        34: subject_ytd["diluted_weighted_average_shares"],
    }.items():
        plan.set(sheet, f"B{row}", value, "actual")
    plan.calc(sheet, "B33", "B30/B45", subject_q4["net_income_millions"] / subject_q4["diluted_eps"])
    _trailing(plan, "B", (
        comps.subject_market_cap_millions, comps.subject_enterprise_value_millions, comps.subject_ebit_millions,
        ltm["adjusted_net_income_millions"], ltm["estimated_weighted_diluted_shares_millions"], comps.subject_eps,
        comps.subject_ev_to_ebit, comps.subject_price_to_earnings,
    ))

    peer_columns = dict(zip((peer.ticker for peer in comps.peers), "CDE", strict=True))
    for peer in comps.peers:
        col = peer_columns[peer.ticker]
        q4, ytd = peer.prior_fourth_quarter, peer.current_interim
        plan.set(sheet, f"{col}15", peer.ticker, "header")
        plan.set(sheet, f"{col}16", to_excel(peer.close.observed_on), "actual", DATE_FORMAT)
        plan.set(sheet, f"{col}17", peer.close.price, "actual", USD)
        for row, value in {
            18: peer.shares_millions, 20: peer.debt_millions, 21: peer.preferred_stock_millions,
            22: peer.noncontrolling_interest_millions, 23: peer.cash_millions, 27: q4.ebit_millions, 28: ytd.ebit_millions,
            30: q4.net_income_millions, 31: ytd.net_income_millions, 33: q4.diluted_shares_millions,
            34: ytd.diluted_shares_millions,
        }.items():
            plan.set(sheet, f"{col}{row}", value, "actual")
        _trailing(plan, col, (
            peer.market_cap_millions, peer.enterprise_value_millions, peer.ltm_ebit_millions, peer.ltm_net_income_millions,
            peer.ltm_diluted_shares_millions, peer.ltm_diluted_eps, peer.ev_to_ebit, peer.price_to_earnings,
        ))

    ups_column = peer_columns["UPS"]
    adjustments = next(peer["adjustments"] for peer in raw["peers"] if peer["ticker"] == "UPS")
    plan.set(sheet, "A47", "UPS issuer adjustments", "section")
    plan.set(sheet, "B48", "Dec. 2025 quarter", "header")
    plan.set(sheet, "C48", "Sep. 2026 YTD", "header")
    plan.set(sheet, "A49", "EBIT adjustments")
    plan.set(sheet, "A50", "Net income adjustments")
    for col, period in (("B", "prior_fourth_quarter"), ("C", "current_interim")):
        plan.set(sheet, f"{col}49", adjustments[f"{period}_ebit_millions"], "actual")
        plan.set(sheet, f"{col}50", adjustments[f"{period}_net_income_millions"], "actual")
    adjusted_ebit = f"{ups_column}29+SUM(B49:C49)"
    adjusted_ni = f"{ups_column}32+SUM(B50:C50)"
    benchmarks = (
        ("UPS GAAP", comps.ups, f"{ups_column}38", f"{ups_column}39"),
        ("UPS adjusted", comps.ups_adjusted, f"{ups_column}25/({adjusted_ebit})", f"{ups_column}17/(({adjusted_ni})/{ups_column}35)"),
        ("Screened GAAP", comps.screened_median, _median(comps.screened_median, peer_columns, 38),
         _median(comps.screened_median, peer_columns, 39)),
        ("Broad GAAP", comps.broad_median, _median(comps.broad_median, peer_columns, 38), _median(comps.broad_median, peer_columns, 39)),
    )
    for col, (label, benchmark, ev, pe) in zip("BCDE", benchmarks, strict=True):
        plan.set(sheet, f"{col}5", label, "header")
        plan.calc(sheet, f"{col}6", ev, benchmark.ev_to_ebit, fmt=MULTIPLE)
        plan.calc(sheet, f"{col}7", pe, benchmark.price_to_earnings, fmt=MULTIPLE)
        plan.calc(sheet, f"{col}9", f"({col}6*$B$29+DCF!$B$41)/DCF!$B$42", benchmark.implied_ev_ebit_usd, kind="total", fmt=USD)
        plan.calc(sheet, f"{col}10", f"{col}7*$B$36", benchmark.implied_pe_usd, kind="total", fmt=USD)
    for row, (label, detail) in enumerate((
        ("FDX basis", ltm["basis"]),
        ("UPS", "Closest integrated parcel peer. Its issuer adjustments use different definitions from FedEx's."),
        ("EXPD", "Asset-light forwarder. The screened median combines UPS and EXPD."),
        ("CHRW", "Pending RXO transaction limits comparability. CHRW appears in the broad median."),
        ("Cash basis", "FedEx uses estimated cutoff debt, excess cash and investment values. Peers use filed September debt and all reported cash."),
    ), start=53):
        plan.set(sheet, f"A{row}", label)
        plan.set(sheet, f"B{row}", detail, "note")


def _trailing(plan, col, expected):
    rows = (
        (19, f"{col}17*{col}18", None),
        (25, f"SUM({col}19:{col}22)-SUM({col}23:{col}24)", None),
        (29, f"SUM({col}27:{col}28)", None),
        (32, f"SUM({col}30:{col}31)", None),
        (35, f"({col}33*$B$43+{col}34*$B$44)/SUM($B$43:$B$44)", None),
        (36, f"{col}32/{col}35", USD),
        (38, f"{col}25/{col}29", MULTIPLE),
        (39, f"{col}17/{col}36", MULTIPLE),
    )
    for (row, formula, fmt), value in zip(rows, expected, strict=True):
        plan.calc("Comps", f"{col}{row}", formula, value, kind="total", fmt=fmt)


def _median(benchmark, columns, row):
    references = ",".join(f"{columns[ticker]}{row}" for ticker in benchmark.tickers)
    return f"MEDIAN({references})"


def add_fdx_sources(plan, inputs_dir, as_of, valuation):
    sheet = "Sources"
    plan.set(sheet, "A1", "Sources and modeling assumptions", "title")
    plan.set(sheet, "A2", f"Evidence through the {as_of:%B %d, %Y} pre-open cutoff", "subtitle")
    for col, label in zip("ABCD", ("Evidence and source ID", "Available", "Citation", "Public link"), strict=True):
        plan.set(sheet, f"{col}4", label, "header")
    row = 5
    for group, sources in valuation["source_register"].items():
        for source_id, source in sources.items():
            published = date.fromisoformat(source.get("available_on") or source["published_on"])
            citation = source.get("title") or source.get("citation") or source.get("document") or source_id.replace("_", " ").title()
            for col, value in zip("ABCD", (f"{group.replace('_', ' ').title()} / {source_id}", to_excel(published),
                                         citation, source["url"]), strict=True):
                plan.set(sheet, f"{col}{row}", value, "actual" if col == "B" else "note", DATE_FORMAT if col == "B" else None)
            row += 1

    row += 1
    plan.set(sheet, f"A{row}", "Key valuation assumptions", "section")
    discount = _dated_inputs(inputs_dir, "discount", as_of)
    assets = _dated_inputs(inputs_dir, "valuation-bridge", as_of)
    for label, item in (
        *((key.replace("_", " ").title(), discount[key]) for key in (
            "risk_free_rate", "equity_risk_premium", "unlevered_beta", "pretax_cost_of_debt", "tax_rate", "terminal_growth",
            "terminal_return_on_new_invested_capital", "debt_cross_check",
        )),
        ("Operating cash reserve", assets["operating_cash_reserve"]),
        ("FedEx Freight retained stake", assets["fdxf"]),
        ("InPost stake", assets["inpost"]),
        ("Other investments", assets["other_assets"]),
    ):
        row += 1
        plan.set(sheet, f"A{row}", label)
        plan.set(sheet, f"C{row}", item["note"], "note")

    row += 2
    plan.set(sheet, f"A{row}", "Forecast and valuation limitations", "section")
    for note in (*valuation["forecast_limitations"], *valuation["method_and_limitations"]):
        row += 1
        plan.set(sheet, f"C{row}", note, "note")
    if valuation["override_log"]:
        row += 2
        plan.set(sheet, f"A{row}", "Guided overrides", "section")
        for override in valuation["override_log"]:
            row += 1
            plan.set(sheet, f"A{row}", override.target, "note")
            plan.set(sheet, f"C{row}", f"{override.before:g} to {override.value:g}. {override.reason}", "note")

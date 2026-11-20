"""Prepare and verify the private, dated Oracle review pair."""

import argparse
from datetime import date
from hashlib import sha256
import json
from pathlib import Path
from statistics import median

from .oracle_capacity import compare_oracle_buildout
from .oracle_review_case import build_review_case


REVIEW_DATE = date(2026, 11, 13)
QUOTE = 125.35
MARKET = {"common_close": QUOTE, "risk_free": .0515, "beta": 1.78, "equity_risk_premium": .0441}
QUARTER = "https://www.sec.gov/Archives/edgar/data/1341439/000119312526389274/orcl-20260831.htm"
SOURCES = {
    "Oracle quarterly filings": QUARTER,
    "Oracle Investor Day guidance": "https://www.sec.gov/Archives/edgar/data/1341439/000119312526435853/d75538dex991.htm",
    "ORCL November 13 closing price": "https://investor.oracle.com/stock-information/default.aspx?LanguageId=1&print=1",
    "10-year Treasury yield": "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/TextView?field_tdr_date_value_month=202611&type=daily_treasury_yield_curve",
    "Equity risk premium reference": "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/home.htm",
    "November note issue yield": "https://www.sec.gov/Archives/edgar/data/1341439/000119312526443918/d87441dfwp.htm",
    "Equity offering and awards": "https://www.sec.gov/Archives/edgar/data/1341439/000119312526445847/d181425d424b5.htm",
    "Debt offering and capital table": "https://www.sec.gov/Archives/edgar/data/1341439/000119312526445846/d181336d424b2.htm",
}
PEERS = {
    "MSFT": (523.88, "https://finance.yahoo.com/quote/MSFT/history/"),
    "AMZN": (266.66, "https://finance.yahoo.com/quote/AMZN/history/"),
    "CRM": (217.84, "https://investor.salesforce.com/stock-info/"),
}
NOTES = (
    "The valuation uses analyst scenarios. The August 31 balance sheet is the last reported balance.",
    "Elapsed revenue uses the midpoint of Q2 guidance spread by days. Cash investment, financing and share movements are modeled.",
    "Opening unpaid capex and significant customer advances use quarterly disclosures as balance proxies. Vendor debt is not separately identifiable in the opening borrowing total.",
    "Oracle's FY2027 and FY2030 revenue targets anchor the forecast. Non-OCI lines use reported growth. OCI bridges to the targets, with interpolation and a slower final year.",
    "The equipment ownership and funding mix, lease starts and rent, depreciation, flat operating working capital, refinancing, taxes and share vesting are scenario inputs.",
    "The bear and bull cases lower or raise forecast EBITDA margin by four percentage points. The cutoff bridge is shared.",
    "Debt uses the August fair-value mark plus modeled changes, preferred uses liquidation preference, and minority interest uses book value. The operating cash reserve is $10 billion.",
    "The beta of 1.78 is an analyst input. The equity premium of 4.41% is a November reference. Both are editable.",
)


def prepare_review(directory: Path) -> tuple[dict, dict]:
    """Build a compact formula plan and matching research data from the saved evidence."""
    def read(ticker):
        return json.loads((directory / f"{ticker}-{REVIEW_DATE}.json").read_text(encoding="utf-8-sig"))

    return prepare_review_inputs({ticker: read(ticker) for ticker in ("ORCL", *PEERS)})


def prepare_review_inputs(packets, *, market=MARKET, quotes=None, treasury=None):
    """Build one paired review from already retrieved and dated inputs."""
    packet = packets["ORCL"].copy()
    as_of = date.fromisoformat(packet["as_of"])
    if quotes is None and as_of != REVIEW_DATE:
        raise ValueError("A new valuation cutoff requires independently collected closing prices.")
    quote_date = date.fromisoformat(quotes["ORCL"]["trading_date"]) if quotes is not None else REVIEW_DATE
    packet["capacity_scenarios"] = compare_oracle_buildout(as_of)
    quote = market["common_close"]
    cases = {label: build_review_case(packet, **market, margin_shift=shift)
             for label, shift in (("Bear", -.04), ("Base", 0), ("Bull", .04))}
    assumptions, pilot = cases["Base"]
    model = pilot.forecast
    value = model.valuation.value_per_share
    peers = []
    sources = dict(SOURCES)
    if quotes is not None:
        sources.pop("ORCL November 13 closing price")
        sources[f"ORCL {quote_date} closing price"] = quotes["ORCL"]["url"]
    if treasury is not None:
        sources["10-year Treasury yield"] = treasury["url"]
    for ticker, (close, quote_url) in PEERS.items():
        if quotes is not None:
            close, quote_url = quotes[ticker]["close"], quotes[ticker]["url"]
        peer = packets[ticker]
        if peer["issuer"]["ticker"] != ticker or peer["as_of"] != as_of.isoformat():
            raise ValueError(f"{ticker} peer evidence does not match the review.")
        period = peer["actuals"]["periods"][-1]
        facts = period["metrics"]
        shares, earnings = facts["ending_shares"] / 1e6, facts["net_income"] / 1e6
        if shares <= 0 or earnings <= 0:
            raise ValueError(f"{ticker} lacks a positive share count and annual earnings for P/E.")
        peers.append([ticker, close, shares, earnings, close * shares / earnings, period["period_end"]])
        sources[f"{ticker} financials"] = period["filing"]["url"]
        sources[f"{ticker} closing price"] = quote_url
    annual = packet["actuals"]["periods"][-1]["metrics"]
    prior_common_earnings = annual["net_income"] / 1e6
    comp_value = median(row[4] for row in peers) * prior_common_earnings / assumptions.equity_bridge.diluted_shares_millions
    report = {
        "as_of": as_of, "quote_date": quote_date,
        "value": value, "quote": quote, "wacc": model.cost_of_capital.wacc,
        "upside": value / quote - 1, "comps": peers, "comp_value": comp_value,
        "rating": "Buy" if value / quote - 1 >= .15 else "Sell" if value / quote - 1 <= -.15 else "Hold",
        "scenarios": {name: run.forecast.valuation.value_per_share for name, (_, run) in cases.items()},
        "periods": [{
            "year": period.closing.period_end.year, "revenue": row["consolidated_millions"],
            "fcff": period.cash_flow["unlevered_free_cash_flow"], "cash": period.closing.cash_millions,
        } for row, period in zip(packet["revenue_forecast"]["years"], model.periods)],
        "cutoff_cash": pilot.elapsed.closing.cash_millions,
        "cutoff_basic_shares": pilot.elapsed.closing.basic_shares_millions,
        "terminal_share": model.valuation.terminal_present_value_millions / model.valuation.enterprise_value_millions,
        "sources": sources, "notes": (*NOTES,
            "Closing prices use the cited November 13 public references." if quotes is None
            else f"All four raw USD closing prices came from Alpaca's historical SIP feed for {quote_date}.",
            f"SEC filings and the Treasury yield dated {treasury['date']} were retrieved for this run." if treasury is not None
            else "This run uses the saved SEC evidence and dated Treasury reference.",
        ),
    }
    return _workbook_plan(packet, assumptions, pilot, report, prior_common_earnings), report


def _workbook_plan(packet, a, pilot, report, prior_earnings):
    sheets, expected = {}, {}

    def tab(name):
        cells = sheets.setdefault(name, {})
        def put(address, value, style="text", format_=None):
            cells[address] = [value, style, format_]
        put("A1", name, "title")
        return put

    pct = "0.00%"
    per_share = '"$"0.00'
    cover = tab("Cover")
    cover("A2", f"ORCL | Private analyst scenario | {report['as_of']:%B %d, %Y}", "subtitle")
    cover("A3", f"Stock price date {report['quote_date']}", "note")
    for r, (label, val) in enumerate((
        ("Scenario", "Base"), ("Close", report["quote"]), ("DCF fair value per share", "=DCF!B15"),
        ("Upside to close", "=B7/B6-1"), ("Illustrative rating", '=IF(B8>=0.15,"Buy",IF(B8<=-0.15,"Sell","Hold"))'),
        ("Peer P/E cross-check", "=Comps!B12"), ("Modeled cutoff cash ($m)", "=Cutoff!D4"),
        ("Modeled cutoff basic shares (m)", "=Cutoff!D22"),
    ), 5):
        cover(f"A{r}", label, "total")
        cover(f"B{r}", val, "input" if r in (5, 6) else "total",
              pct if r == 8 else per_share if r in (6, 7, 10) else None)
    cover("A14", "First-year forecast columns cover the post-cutoff fiscal stub.", "note")
    cover("A15", "See Sources for assumptions and valuation limitations.", "note")
    actual = tab("Actuals")
    history = packet["actuals"]["periods"]
    for index, row in enumerate(history):
        actual(f"{'BCDEF'[index]}3", row["period_end"], "header")
    for i, metric in enumerate(("revenue", "operating_income", "net_income", "total_assets", "total_liabilities",
                                "total_equity", "operating_cash_flow", "investing_cash_flow", "financing_cash_flow",
                                "capex", "depreciation_amortization", "ending_shares"), 4):
        actual(f"A{i}", metric.replace("_", " ").capitalize())
        for col, row in zip("BCDEF", history):
            val = row["metrics"].get(metric)
            actual(f"{col}{i}", val / 1e6 if val is not None else "Unavailable")

    inputs = tab("Assumptions")
    revenue = tab("Revenue")
    statements = tab("Income statement")
    balance = tab("Balance sheet")
    cash = tab("Cash flow")
    checks = tab("Checks")
    dcf = tab("DCF")
    comps = tab("Comps")
    capex = tab("PP&E")
    funding = tab("Funding")
    wc = tab("Working capital")
    shares = tab("Shares")
    capital = tab("WACC")
    cutoff = tab("Cutoff")
    sources = tab("Sources")
    elapsed = pilot.elapsed
    cutoff("A2", "Last filed balance to valuation date | USD millions or millions of shares", "subtitle")
    for col, label in zip("BCD", ("August 31 filed basis¹", "Modeled movement", f"{report['as_of']} estimate")):
        cutoff(f"{col}3", label, "header")
    for row, (label, opening, closing) in enumerate((
        ("Cash", elapsed.opening.cash_millions, elapsed.closing.cash_millions),
        ("Working-capital assets", elapsed.opening.working_capital.assets_millions, elapsed.closing.working_capital.assets_millions),
        *[(label, getattr(elapsed.opening, field), getattr(elapsed.closing, field)) for label, field in (
            ("Net property", "ppe"), ("Intangibles", "intangibles_millions"),
            ("Operating lease assets", "operating_lease_assets_millions"), ("Other assets", "other_assets_millions"),
        )],
        ("Total assets", elapsed.opening.assets_millions, elapsed.closing.assets_millions),
        ("Working-capital liabilities", elapsed.opening.working_capital.liabilities_millions, elapsed.closing.working_capital.liabilities_millions),
        *[(label, getattr(elapsed.opening, field), getattr(elapsed.closing, field)) for label, field in (
            ("Operating lease liabilities", "operating_lease_liabilities_millions"), ("Notes", "notes_millions"),
            ("Vendor debt", "vendor_debt_millions"), ("Finance leases", "finance_leases_millions"),
            ("Unpaid capex", "capital_payables_millions"), ("Customer financing", "customer_financing_millions"),
            ("Dividends payable", "dividends_payable_millions"), ("Other liabilities", "other_liabilities_millions"),
        )],
        ("Retained earnings", elapsed.opening.retained_earnings_millions, elapsed.closing.retained_earnings_millions),
        ("Other equity and NCI", elapsed.opening.other_equity_millions + elapsed.opening.noncontrolling_interest_millions,
         elapsed.closing.other_equity_millions + elapsed.closing.noncontrolling_interest_millions),
        ("Basic shares", elapsed.opening.basic_shares_millions, elapsed.closing.basic_shares_millions),
    ), 4):
        if row == 6:
            opening, closing = opening.net_millions, closing.net_millions
        cutoff(f"A{row}", label, "total" if row == 10 else "text")
        for col, value in zip("BCD", (opening, closing - opening, f"=SUM(B{row}:C{row})")):
            cutoff(f"{col}{row}", f"=SUM({col}4:{col}9)" if row == 10 else value)
    cutoff("A23", "Assets less liabilities and equity")
    for col in "BD":
        cutoff(f"{col}23", f"={col}10-SUM({col}11:{col}21)", "check")
    cutoff("A25", "¹ Filing-based opening totals use modeled classifications for some components. See Sources.", "note")
    for row, (label, amount) in enumerate((
        ("Operating cash flow", elapsed.cash_flow["cash_from_operations"]),
        ("Investing cash flow", elapsed.cash_flow["cash_from_investing"]),
        ("Financing cash flow", elapsed.cash_flow["cash_from_financing"]),
        ("Net common offering proceeds within financing", a.elapsed.equity.stock_issuance_proceeds_millions),
        ("Net notes issued within financing", a.elapsed.debt.note_proceeds_millions),
        ("Primary shares sold", a.elapsed.equity.shares_sold_millions),
        ("Estimated shares vested", a.elapsed.equity.shares_vested_millions),
        ("Incremental diluted shares", a.elapsed.equity.dilutive_shares_millions),
    ), 27):
        cutoff(f"A{row}", label)
        cutoff(f"C{row}", amount)
    cutoff("C4", "=SUM(C27:C29)")
    cutoff("C22", "=SUM(C32:C33)")
    expected["Cutoff"] = {"B23": 0, "D23": 0, "D4": elapsed.closing.cash_millions,
                          "D22": elapsed.closing.basic_shares_millions}
    model_periods = pilot.forecast.periods
    base_revenue = packet["revenue_basis"]["annual"]["lines"]
    for i, (future, period) in enumerate(zip(a.forecast, model_periods)):
        col = "BCDEF"[i]
        prev = "ABCDE"[i]
        for put in (inputs, revenue, statements, cash, balance, checks, capex, funding, wc, shares):
            put(f"{col}3", ("Stub " if i == 0 else "FY") + str(future.period_end.year), "header")
        opening = period.opening
        prop, debt, lease = future.capex_funding, future.debt, future.operating_lease
        equity = future.equity
        for row, (label, value) in enumerate((
            ("Opening net property", opening.ppe.net_millions if not i else f"={prev}14"),
            ("Cash-funded additions", period.property.cash_funded_additions_millions),
            ("Vendor-financed additions", prop.vendor_financed_additions_millions),
            ("Finance lease additions", prop.finance_lease_additions_millions),
            ("Unpaid capital additions", prop.capital_payables_incurred_millions),
            ("Economic additions", f"=SUM({col}5:{col}8)"),
            ("Payments of capital payables", prop.capital_payables_paid_millions),
            ("Cash capex", f"=SUM({col}5,{col}10)"),
            ("Depreciation", future.property.depreciation_millions),
            ("Net book value disposed", period.property.disposal_net_book_value_millions),
            ("Closing net property", f"=SUM({col}4,{col}9)-SUM({col}12:{col}13)"),
        ), 4):
            capex(f"A{row}", label)
            capex(f"{col}{row}", value, "input" if isinstance(value, (int, float)) else "total")
        f = f"Funding!{col}"
        for row, (label, value) in enumerate((
            ("Opening notes", opening.notes_millions if not i else f"={prev}7"),
            ("New notes", debt.note_proceeds_millions),
            ("Notes repaid", debt.note_repayments_millions),
            ("Closing notes", f"=SUM({col}4:{col}5)-{col}6"),
            ("Opening vendor debt", opening.vendor_debt_millions if not i else f"={prev}10"),
            ("Vendor principal paid", debt.vendor_principal_paid_millions),
            ("Closing vendor debt", f"={col}8+'PP&E'!{col}6-{col}9"),
            ("Opening finance leases", opening.finance_leases_millions if not i else f"={prev}13"),
            ("Finance lease principal paid", debt.finance_lease_principal_paid_millions),
            ("Closing finance leases", f"={col}11+'PP&E'!{col}7-{col}12"),
            ("Opening operating lease liability", opening.operating_lease_liabilities_millions if not i else f"={prev}18"),
            ("Lease commencements", lease.lease_inceptions_millions),
            ("Lease liability accretion", lease.liability_accretion_millions),
            ("Operating cash rent", lease.cash_rent_millions),
            ("Closing operating lease liability", f"=SUM({col}14:{col}16)-{col}17"),
            ("Opening operating lease asset", opening.operating_lease_assets_millions if not i else f"={prev}21"),
            ("Operating rent expense", lease.rent_expense_millions),
            ("Closing operating lease asset", f"=SUM({col}19,{col}15:{col}16)-{col}20"),
            ("Opening capex payable", opening.capital_payables_millions if not i else f"={prev}23"),
            ("Closing capex payable", f"={col}22+'PP&E'!{col}8-'PP&E'!{col}10"),
            ("Opening customer financing", opening.customer_financing_millions if not i else f"={prev}26"),
            ("Change in customer financing", period.cash_flow["change_in_customer_financing"]),
            ("Closing customer financing", f"=SUM({col}24:{col}25)"),
            ("Opening dividends payable", opening.dividends_payable_millions if not i else f"={prev}30"),
            ("Dividends declared", equity.dividends_declared_millions),
            ("Dividends paid", equity.dividends_paid_millions),
            ("Closing dividends payable", f"=SUM({col}27:{col}28)-{col}29"),
            ("NCI distributions", equity.nci_distributions_paid_millions),
            ("Net financing cash flow", f"={col}5-SUM({col}6,{col}9,{col}12,{col}29,{col}31)+Shares!{col}11-Shares!{col}12"),
        ), 4):
            funding(f"A{row}", label)
            funding(f"{col}{row}", value, "input" if isinstance(value, (int, float)) else "total")
        for row, (label, value) in enumerate((
            ("Receivables", future.working_capital.receivables_millions),
            ("Inventory", future.working_capital.inventory_millions),
            ("Other operating assets", future.working_capital.other_operating_assets_millions),
            ("Trade payables", future.working_capital.trade_payables_millions),
            ("Operating deferred revenue", future.working_capital.ordinary_deferred_revenue_millions),
            ("Other operating liabilities", future.working_capital.other_operating_liabilities_millions),
            ("Operating assets", f"=SUM({col}4:{col}6)"),
            ("Operating liabilities", f"=SUM({col}7:{col}9)"),
            ("Closing net working capital", f"={col}10-{col}11"),
            ("Opening net working capital", opening.working_capital.net_millions if not i else f"={prev}12"),
            ("Cash released", f"={col}13-{col}12"),
        ), 4):
            wc(f"A{row}", label)
            wc(f"{col}{row}", value, "input" if isinstance(value, (int, float)) else "total")
        for row, (label, value) in enumerate((
            ("Opening basic shares", "=Cover!B12" if not i else f"={prev}8"),
            ("Primary shares sold", equity.shares_sold_millions),
            ("Shares vested", equity.shares_vested_millions),
            ("Shares repurchased", equity.shares_repurchased_millions),
            ("Closing basic shares", f"=SUM({col}4:{col}6)-{col}7"),
            ("Incremental dilutive shares", equity.dilutive_shares_millions),
            ("Closing diluted shares", f"=SUM({col}8:{col}9)"),
            ("Stock issuance proceeds", equity.stock_issuance_proceeds_millions),
            ("Stock repurchase cost", equity.stock_repurchase_cost_millions),
            ("Stock compensation", equity.stock_compensation_millions),
            ("Other equity increase", f"=SUM({col}11,{col}13)-{col}12"),
        ), 4):
            shares(f"A{row}", label)
            shares(f"{col}{row}", value, "input" if isinstance(value, (int, float)) else "total")
        revenue_row = packet["revenue_forecast"]["years"][i]
        for row, name in enumerate(revenue_row["amounts_millions"], 4):
            label = name.replace("_", " ").title()
            growth = packet["revenue_forecast"]["assumptions"][i]["growth_rates"][name]
            inputs(f"A{row}", f"{label} growth")
            inputs(f"{col}{row}", growth, "input", pct)
            revenue(f"A{row}", label)
            if not i:
                revenue(f"H{row}", base_revenue[name]["usd"] / 1e6)
            revenue(f"{col}{row}", f"={prev if i else 'H'}{row}*(1+Assumptions!{col}{row})")
        revenue("A10", "Full-year revenue", "total")
        revenue("H3", "FY2026 reported", "header")
        revenue(f"{col}10", f"=SUM({col}4:{col}9)", "total")
        amounts = (
            ("Reported and elapsed revenue", revenue_row["consolidated_millions"] - future.revenue_millions),
            ("Base EBITDA margin", future.ebitda_after_sbc_and_operating_rent_millions / future.revenue_millions),
            ("Tax rate", future.tax_rate), ("Property depreciation", f"='PP&E'!{col}12"),
            ("Intangible amortization", future.intangible_amortization_millions),
            ("Book interest expense", period.income_statement["interest_expense"]),
            ("Cash working-capital release", f"='Working capital'!{col}14"),
            ("Economic PP&E additions", f"='PP&E'!{col}9"),
            ("Cash capex", f"='PP&E'!{col}11"),
            ("Stock compensation", f"=Shares!{col}13"),
            ("Change in customer financing", f"={f}25"),
            ("Financing cash flow, net", f"={f}32"),
            ("NCI income", future.equity.net_income_attributable_to_nci_millions),
            ("Preferred dividends for attribution", future.equity.preferred_dividends_for_income_attribution_millions),
            ("Dividends declared", f"={f}28"),
            ("Other equity increase", f"=Shares!{col}14"),
            ("NCI distributions", f"={f}31"),
        )
        for row, (name, amount) in enumerate(amounts, 11):
            inputs(f"A{row}", name)
            inputs(f"{col}{row}", amount, "input" if isinstance(amount, (int, float)) else "text", pct if row in (12, 13) else None)
        inputs("A29", "Case EBITDA margin shift")
        inputs(f"{col}29", '=IF(Cover!$B$5="Bear",-0.04,IF(Cover!$B$5="Bull",0.04,0))', format_=pct)
        q = f"Assumptions!{col}"
        inc = f"'Income statement'!{col}"
        for row, (label, formula) in enumerate((
            ("Revenue", f"Revenue!{col}10-{q}11"),
            ("EBITDA after rent and SBC", f"{col}4*SUM({q}12,{q}29)"),
            ("Depreciation and amortization", f"SUM({q}14:{col}15)"),
            ("Operating income", f"{col}5-{col}6"),
            ("Interest", f"{q}16"),
            ("Pretax income", f"{col}7-{col}8"),
            ("Income tax", f"MAX(0,{col}9)*{q}13"),
            ("Consolidated net income", f"{col}9-{col}10"),
            ("Income available to common", f"{col}11-{q}23-{q}24"),
        ), 4):
            statements(f"A{row}", label)
            statements(f"{col}{row}", "=" + formula, "total" if row in (7, 11, 12) else "text")
        for row, (label, formula) in enumerate((
            ("Cash from operations", f"{inc}11+{inc}6+SUM({q}17,{q}20,{q}21)+{f}20-{f}17"),
            ("Cash from investing", f"-{q}19"),
            ("Cash from financing", f"{q}22"),
            ("Net change in cash", f"SUM({col}4:{col}6)"),
            ("Unlevered free cash flow", f"{inc}7-MAX(0,{inc}7)*{q}13+{inc}6+{q}17-{q}18+{f}20-{f}17"),
        ), 4):
            cash(f"A{row}", label)
            cash(f"{col}{row}", "=" + formula, "total" if row >= 7 else "text")
        closing = period.closing
        for row, (label, value) in enumerate((
            ("Cash", f"={prev}4+'Cash flow'!{col}7" if i else f"=Cover!B11+'Cash flow'!{col}7"),
            ("Operating working-capital assets", f"='Working capital'!{col}10"),
            ("Net property", f"='PP&E'!{col}14"),
            ("Intangibles", f"={prev}7-{q}15" if i else f"=$B$18-{q}15"),
            ("Operating lease assets", f"={f}21"),
            ("Other assets", "=$B$21"),
            ("Total assets", f"=SUM({col}4:{col}9)"),
            ("Total liabilities", f"=SUM('Working capital'!{col}11,{f}7,{f}10,{f}13,{f}18,{f}23,{f}26,{f}30,$B$22)"),
            ("Retained earnings", f"={prev}12+{inc}11-{q}23-{q}25" if i else f"=$B$19+{inc}11-{q}23-{q}25"),
            ("Other equity and NCI", f"={prev}13+{q}26+{q}23-{q}27" if i else f"=$B$20+{q}26+{q}23-{q}27"),
            ("Total liabilities and equity", f"=SUM({col}11:{col}13)"),
        ), 4):
            balance(f"A{row}", label)
            balance(f"{col}{row}", value, "total" if row in (10, 14) else "text")
        checks("A4", "Assets less liabilities and equity")
        checks(f"{col}4", f"='Balance sheet'!{col}10-'Balance sheet'!{col}14", "check")
        checks("A5", "Funding gap")
        checks(f"{col}5", f"=MAX(0,-'Balance sheet'!{col}4)", "check")
        expected.setdefault("Checks", {})[f"{col}4"] = 0
        expected["Checks"][f"{col}5"] = 0
        expected.setdefault("Cash flow", {})[f"{col}8"] = period.cash_flow["unlevered_free_cash_flow"]
        expected.setdefault("Balance sheet", {})[f"{col}4"] = closing.cash_millions
        expected["Balance sheet"][f"{col}10"] = closing.assets_millions
        expected["Balance sheet"][f"{col}11"] = closing.liabilities_millions
        expected.setdefault("Shares", {})[f"{col}10"] = period.income_statement["ending_diluted_shares"]
        expected.setdefault("Revenue", {})[f"{col}10"] = revenue_row["consolidated_millions"]
    for row, (label, val) in enumerate((
        ("Cutoff intangibles", pilot.elapsed.closing.intangibles_millions),
        ("Cutoff retained earnings", pilot.elapsed.closing.retained_earnings_millions),
        ("Cutoff other equity and NCI", pilot.elapsed.closing.other_equity_millions
         + pilot.elapsed.closing.noncontrolling_interest_millions),
        ("Other assets carried forward", pilot.elapsed.closing.other_assets_millions),
        ("Other liabilities carried forward", pilot.elapsed.closing.other_liabilities_millions),
    ), 18):
        balance(f"A{row}", label)
        balance(f"B{row}", val, "input")
    bridge = a.equity_bridge
    cost = a.cost_of_capital
    for row, (label, value) in enumerate((
        ("Risk-free rate", cost.risk_free_rate),
        ("Equity risk premium", cost.equity_risk_premium),
        ("Levered beta", cost.levered_beta),
        ("Market value of common", "=Cover!B6*Cover!B12"),
        ("Pretax cost of debt", cost.pretax_cost_of_debt),
        ("Market value of debt", "=DCF!B8"),
        ("Marginal tax rate", cost.marginal_tax_rate),
        ("Cost of preferred", cost.preferred_cost),
        ("Market value of preferred", "=DCF!B9"),
        ("Cost of common equity", "=B3+B4*B5"),
        ("After-tax cost of debt", "=B7*(1-B9)"),
        ("WACC", "=(B6*B12+B8*B13+B10*B11)/SUM(B6,B8,B11)"),
    ), 3):
        capital(f"A{row}", label)
        capital(f"B{row}", value, "input" if isinstance(value, (int, float)) else "total",
                pct if row in (3, 4, 7, 9, 10, 12, 13, 14) else None)
    for i, (label, val, format_) in enumerate((
        ("WACC", "=WACC!B14", pct), ("Terminal growth", a.terminal.growth_rate, pct),
        ("Terminal marginal ROIC", a.terminal.marginal_roic, pct),
        ("Excess cash", "=MAX(0,Cutoff!D4-B17)", None), ("Nonoperating assets", bridge.nonoperating_assets_millions, None),
        ("Debt and finance leases", bridge.debt_and_finance_leases_millions, None),
        ("Preferred", bridge.preferred_equity_millions, None), ("Minority interests", bridge.minority_interests_millions, None),
        ("Other claims", bridge.other_noncommon_claims_millions, None), ("Diluted shares (m)", "=Cutoff!D22+Cutoff!C34", None),
    ), 3):
        dcf(f"A{i}", label)
        dcf(f"B{i}", val, "input" if isinstance(val, (int, float)) else "total", format_)
    # The enterprise value starts after the input rows.
    dcf("A13", "Enterprise value")
    dcf("B13", "=SUM(B22:F22)+B16", "total")
    dcf("A14", "Common equity value")
    dcf("B14", "=B13+SUM(B6:B7)-SUM(B8:B11)", "total")
    dcf("A15", "Fair value per share")
    dcf("B15", "=IF(AND(B3>B4,B5>B4,B12>0),B14/B12,NA())", "total", per_share)
    dcf("A16", "PV of terminal value")
    dcf("B16", "=B23*(1+B4)*(1-B4/B5)/(B3-B4)/(1+B3)^F20", "total")
    dcf("A17", "Operating cash reserve")
    dcf("B17", elapsed.closing.cash_millions - bridge.excess_cash_millions, "input")
    for col, item in zip("BCDEF", pilot.forecast.valuation.periods):
        dcf(f"{col}20", item.discount_years)
        dcf(f"{col}21", f"='Cash flow'!{col}8")
        dcf(f"{col}22", f"={col}21/(1+$B$3)^{col}20")
    for row, title in ((20, "Discount years"), (21, "Forecast FCFF"), (22, "PV of FCFF"), (23, "Terminal normalized NOPAT")):
        dcf(f"A{row}", title)
    dcf("B23", "='Income statement'!F7*(1-Assumptions!F13)")
    dcf("A26", "WACC and growth sensitivity", "section")
    for i, col in enumerate("BCDEF"):
        dcf(f"{col}27", f"=$B$4+{(i-2)*.005}", "header", pct)
    for i in range(5):
        row = 28 + i
        dcf(f"A{row}", f"=$B$3+{(i-2)*.005}", "header", pct)
        for col in "BCDEF":
            pv = "+".join(f"${c}$21/(1+$A{row})^${c}$20" for c in "BCDEF")
            dcf(f"{col}{row}", f'=IF(AND($A{row}>{col}$27,$B$5>{col}$27,$B$12>0),'
                f'({pv}+$B$23*(1+{col}$27)*(1-{col}$27/$B$5)/($A{row}-{col}$27)'
                f'/(1+$A{row})^$F$20+SUM($B$6:$B$7)-SUM($B$8:$B$11))/$B$12,"")',
                format_=per_share)
    expected["DCF"] = {"B15": report["value"], "D30": report["value"]}
    expected["WACC"] = {"B14": report["wacc"]}
    for row, values in enumerate(pilot.forecast.sensitivity, 28):
        for col, value in zip("BCDEF", values):
            expected["DCF"][f"{col}{row}"] = value
    for index, label in enumerate(("Ticker", "Close", "Shares (m)", "FY net income ($m)", "Trailing FY P/E", "Fiscal year end")):
        comps(f"{'ABCDEF'[index]}5", label, "header")
    for row, record in enumerate(report["comps"], 6):
        ticker, close, shares, earnings, _, year_end = record
        for col, val in zip("ABCDEF", (ticker, close, shares, earnings, f"=B{row}*C{row}/D{row}", year_end)):
            comps(f"{col}{row}", val)
    comps("A10", "Peer median P/E")
    comps("B10", "=MEDIAN(E6:E8)")
    comps("A11", "Oracle FY26 parent net income ($m)")
    comps("B11", prior_earnings)
    comps("A12", "Indicative value per share")
    comps("B12", "=B10*B11/DCF!B12", "total", per_share)
    comps("A14", "As of latest annual filings. Dates differ and net income includes one-time items.", "note")
    expected["Comps"] = {"B12": report["comp_value"]}
    for row, (name, url) in enumerate(report["sources"].items(), 4):
        sources(f"A{row}", name)
        sources(f"C{row}", url)
    for row, note in enumerate(report["notes"], 25):
        sources(f"A{row}", note, "note")
    return {
        "cells": sheets, "case": "Base", "expected": expected,
        "scenarioControls": {name: name for name in report["scenarios"]},
        "scenarioExpected": {name: {"DCF": {"B15": val}} for name, val in report["scenarios"].items() if name != "Base"},
        "recalculationProbe": {
            "input": {"sheet": "Assumptions", "address": "F12", "delta": .01},
            "changed": [{"sheet": "DCF", "address": "B15"}, {"sheet": "Income statement", "address": "F7"}],
            "unchanged": [{"sheet": "Actuals", "address": "F4"}],
        },
    }


def write_review_pdf(report, output: Path, workbook: Path):
    """Write a short investment brief tied to the exact workbook delivered."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.graphics.shapes import Drawing, Line, Rect, String
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle, PageBreak
    from pypdf import PdfReader
    from openpyxl import load_workbook
    from xml.sax.saxutils import escape

    navy = colors.HexColor("#17365D")
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle("Section", parent=styles["Heading2"], textColor=navy, spaceBefore=15, spaceAfter=8))
    styles.add(ParagraphStyle("Reading", parent=styles["BodyText"], fontSize=10, leading=14, alignment=TA_LEFT))
    story = []
    text = lambda value: story.append(Paragraph(escape(value), styles["Reading"]))
    head = lambda value: story.append(Paragraph(escape(value), styles["Section"]))

    def table(rows, widths):
        table = Table(rows, colWidths=widths, repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), navy), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#EEF4FA"), colors.white]),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7), ("TOPPADDING", (0, 0), (-1, -1), 7),
        ]))
        story.append(table)

    cached = load_workbook(workbook, read_only=True, data_only=True)
    try:
        cover = cached["Cover"]
        if (abs(cover["B7"].value - report["value"]) > 1e-6 or cover["B9"].value != report["rating"]
                or abs(cover["B10"].value - report["comp_value"]) > 1e-6):
            raise ValueError("The saved workbook does not match the report valuation and rating.")
    finally:
        cached.close()
    stamp = sha256(workbook.read_bytes()).hexdigest()
    title = f"${report['value']:,.2f}"
    story.append(Paragraph("Oracle Corporation", styles["Title"]))
    text(f"ORCL | Equity research | {report['as_of']:%B %d, %Y}")
    text(f"Market close dated {report['quote_date']:%B %d, %Y}")
    story.append(Spacer(1, 12))
    table([["Scenario rating", "Close", "DCF fair value", "Upside", "WACC"],
           [report["rating"], f"${report['quote']:.2f}", title, f"{report['upside']:.1%}", f"{report['wacc']:.2%}"]],
          [100, 85, 105, 80, 80])
    head("Investment thesis")
    text("Oracle's cloud infrastructure buildout increases its potential revenue base. Conversion of contracted demand into recognized sales depends on power, hardware, and commissioning. In this model, rental costs and equipment reinvestment determine how much of the growth reaches investors.")
    head("Industry and competition")
    text("Microsoft and Amazon bring global cloud distribution and infrastructure scale. Salesforce provides an application-software comparison. The peers have different segment mixes, and the multiples use whole-company earnings.")
    head("Bear, base, and bull cases")
    table([["Operating case", "Fair value per share", "EBITDA margin change"]] + [
        [name, f"${val:.2f}", change] for (name, val), change in
        zip(report["scenarios"].items(), ("-4 percentage points", "Base", "+4 percentage points"))
    ], [130, 130, 190])
    text(f"The illustrative {report['rating']} rating applies a 15% upside or downside threshold to spot DCF fair value. Terminal value accounts for {report['terminal_share']:.0%} of enterprise value.")
    head("Catalysts and risks")
    text("Watch quarterly cloud growth, contracted demand conversion, new capacity entering service, and cash spending relative to the financing plan. Equipment refresh, delays, customer concentration, and the cost of refinancing could materially change the outcome.")
    story.append(PageBreak())
    head("Financial forecast and valuation")
    table([["Fiscal year", "Revenue ($bn)", "FCFF ($bn)", "Closing cash ($bn)"]] + [
        [str(row["year"]), f"{row['revenue']/1000:.1f}", f"{row['fcff']/1000:.1f}", f"{row['cash']/1000:.1f}"]
        for row in report["periods"]
    ], [105, 115, 115, 115])
    text("FY2027 revenue is for the full year. FY2027 FCFF covers cash flows after the valuation date.")
    chart = Drawing(450, 114)
    chart.add(String(10, 99, "Unlevered free cash flow ($bn)", fillColor=navy, fontName="Helvetica-Bold", fontSize=9))
    chart.add(Line(38, 39, 432, 39, strokeColor=colors.HexColor("#9AA9B7")))
    for index, row in enumerate(report["periods"]):
        x = 63 + index * 79
        fcff = row["fcff"] / 1000
        height = abs(fcff) / 2
        chart.add(Rect(x, 39 if fcff >= 0 else 39 - height, 26, height,
                       fillColor=navy if fcff >= 0 else colors.HexColor("#A94343"), strokeColor=None))
        chart.add(String(x + 13, 43 + height if fcff >= 0 else 27 - height, f"{fcff:.1f}", textAnchor="middle",
                         fontName="Helvetica", fontSize=8))
        chart.add(String(x + 13, -4, str(row["year"]), textAnchor="middle", fontName="Helvetica", fontSize=8))
    story.append(chart)
    head("Trading comparables")
    table([["Peer", "Close", "Latest fiscal year", "Reported P/E"]] + [
        [row[0], f"${row[1]:.2f}", row[5], f"{row[4]:.1f}x"] for row in report["comps"]
    ], [100, 100, 130, 120])
    text(f"Peer median P/E implies ${report['comp_value']:.2f} per share using Oracle's reported FY2026 earnings and modeled cutoff diluted shares. Reported earnings include one-time items and fiscal years differ.")
    head("Cutoff reconciliation")
    text(f"Estimated cash at the cutoff is ${report['cutoff_cash']/1000:.2f} billion, with {report['cutoff_basic_shares']/1000:.3f} billion basic shares. The model reflects the November offerings and separately estimates the elapsed operating and investing period.")
    story.append(PageBreak())
    head("Assumptions and sources")
    for note in report["notes"]:
        text(note)
    story.append(Spacer(1, 8))
    for title_text, url in report["sources"].items():
        story.append(Paragraph(f'<link href="{escape(url, {chr(34): "&quot;"})}" color="#17365D">{escape(title_text)}</link>',
                               styles["Reading"]))
    head("Workbook fingerprint")
    text(stamp)

    def foot(canvas, doc):
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(navy)
        canvas.drawString(72, 42, "Oracle Corporation  |  Private review")
        canvas.drawRightString(540, 42, str(doc.page))

    SimpleDocTemplate(str(output), title="Oracle Corporation | Equity Research", author="AI Equity Research Builder",
                      leftMargin=72, rightMargin=72, topMargin=46, bottomMargin=57).build(
                          story, onFirstPage=foot, onLaterPages=foot,
                      )
    extracted = "\n".join(page.extract_text() for page in PdfReader(output).pages)
    if title not in extracted or report["rating"] not in extracted or stamp not in "".join(extracted.split()):
        raise ValueError("Oracle report failed the valuation, rating, or workbook fingerprint checks.")


def export_review(plan, report, output, node, node_modules, *, previews=False):
    """Create the two verified files from one prepared model run."""
    from .workbook_renderer import render_workbook

    output.mkdir(parents=True, exist_ok=True)
    workbook = output / f"ORCL-{report['as_of']}.xlsx"
    pdf = workbook.with_suffix(".pdf")
    render_workbook(plan, workbook, node, node_modules, previews=output / "preview" if previews else None)
    write_review_pdf(report, pdf, workbook)
    return workbook, pdf


def main():
    """Regenerate the private review from the dated SEC packets."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--node", type=Path, required=True)
    parser.add_argument("--node-modules", type=Path, required=True)
    arguments = parser.parse_args()
    plan, report = prepare_review(arguments.evidence_dir)
    export_review(plan, report, arguments.output_dir, arguments.node, arguments.node_modules, previews=True)


if __name__ == "__main__":
    main()

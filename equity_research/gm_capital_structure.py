"""Reconcile GM's filing evidence before an automotive and captive-finance valuation."""

from datetime import date, timedelta
from math import isclose

from .filing_xbrl import load_filing_instance
from .gd_releases import recent_filings
from .gm_automotive_cash import (
    automotive_cash_evidence, automotive_release_evidence, completed_repurchase_program, wholesale_volume,
)
from .operating_research import ResearchGap


def _fact(instance, start, end, concept, members):
    matches = {fact.value / 1e6 for fact in instance.facts
               if fact.start == start and fact.end == end and fact.unit == "USD"
               and fact.concept.rsplit("}", 1)[-1] == concept
               and {member.rsplit("}", 1)[-1] for _, member in fact.dimensions} == members}
    if len(matches) != 1:
        scope = ", ".join(sorted(members)) or "the consolidated entity"
        raise ResearchGap(f"GM's filing did not uniquely identify {concept} for {scope} at {end}.")
    return matches.pop()


def prepare_gm_split(packet, client):
    """Return reconciled evidence for separate Automotive and GM Financial valuations."""
    if packet["issuer"]["cik"] != "0001467858":
        raise ValueError("GM filing evidence is required.")
    histories = packet["actuals"]["periods"][-2:]
    interim = packet["interim_actuals"]["periods"][-1:]
    if len(histories) != 2 or not interim:
        raise ResearchGap("GM needs two annual periods and a current interim report.")
    annual = load_filing_instance(client, "0001467858", histories[-1]["filing"]["accession"])
    quarter = load_filing_instance(client, "0001467858", interim[0]["filing"]["accession"])
    rows = []
    for period, instance in zip([*histories, *interim], (annual, annual, quarter), strict=True):
        start, end = [date.fromisoformat(period[key]) for key in ("period_start", "period_end")]
        auto = _fact(instance, start, end, "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax", {"AutomotiveMember"})
        finance = _fact(instance, start, end, "us-gaap:RevenueNotFromContractWithCustomer", set())
        total = period["metrics"]["revenue"] / 1e6
        if not isclose(auto + finance, total, abs_tol=1):
            raise ResearchGap("GM's automotive and captive-finance revenue do not reconcile to consolidated sales.")
        rows.append({"end": end.isoformat(), "automotive_revenue_millions": auto,
                     "finance_revenue_millions": finance, "total_millions": total, "source": instance.url})
    end = date.fromisoformat(interim[0]["period_end"])
    debt = {
        "automotive_debt_millions": _fact(quarter, None, end, "DebtAndFinanceLeaseLiabilities", {"AutomotiveMember"}),
        "finance_debt_millions": _fact(quarter, None, end, "us-gaap:LongTermDebt", {"GmFinancialMember"}),
    }
    finance = _finance_equity(client, packet["as_of"], end, date.fromisoformat(interim[0]["period_start"]))
    annual_url, interim_url = (period["filing"]["url"] for period in (histories[-1], interim[0]))
    annual_end = date.fromisoformat(histories[-1]["period_end"])
    annual_html, interim_html = client.get_text(annual_url), client.get_text(interim_url)
    auto_cash = automotive_cash_evidence(annual_html, interim_html, annual_end=annual_end, interim_end=end, finance=finance)
    auto_cash["sources"] = (annual_url, interim_url, *finance["sources"])
    revenue_drivers = _automotive_revenue(annual, quarter, histories[-1], interim[0])
    if not packet["earnings"]["releases"]:
        raise ResearchGap("GM's dated earnings supplement is required for the Automotive cash forecast.")
    release_url = packet["earnings"]["releases"][0]["url"]
    release = automotive_release_evidence(client.get_text(release_url), interim_end=end, annual_end=annual_end)
    release["source"] = release_url
    fiscal_start = date.fromisoformat(interim[0]["period_start"])
    for key, concept in (("interest_expense", "us-gaap:InterestExpenseNonoperating"), ("interest_income", "InterestIncome")):
        release[f"annual_{key}_millions"] = _fact(annual, date.fromisoformat(histories[-1]["period_start"]),
                                                   annual_end, concept, {"AutomotiveMember"})
        release[f"prior_ytd_{key}_millions"] = _fact(quarter, fiscal_start.replace(year=fiscal_start.year-1),
                                                    end.replace(year=end.year-1), concept, {"AutomotiveMember"})
    ending_shares = interim[0]["metrics"].get("ending_shares")
    if ending_shares is None or ending_shares <= 0:
        raise ResearchGap("The filed period-end GM common share count is required.")
    release["ending_parent_shares_millions"] = ending_shares / 1e6
    completed = completed_repurchase_program(interim_html, balance_date=end,
                                              filed_on=date.fromisoformat(interim[0]["filing"]["filed_on"]))
    if completed is not None:
        release["completed_repurchase"] = {**completed, "source": interim_url}
    cutoff = date.fromisoformat(packet["as_of"])
    cover_shares = [fact for fact in quarter.facts if fact.concept == "dei:EntityCommonStockSharesOutstanding"
                    and fact.unit == "shares" and not fact.dimensions and end < fact.end <= cutoff and fact.value > 0]
    if cover_shares:
        cover_date = max(fact.end for fact in cover_shares)
        counts = {fact.value for fact in cover_shares if fact.end == cover_date}
        if len(counts) == 1:
            release["cover_shares"] = {"date": cover_date.isoformat(), "millions": counts.pop()/1e6,
                                       "source": interim_url}
    return {"revenue": rows, "debt": debt, "finance_equity": finance,
            "automotive_cash": auto_cash, "automotive_revenue_drivers": revenue_drivers,
            "automotive_wholesale": wholesale_volume(annual_html, interim_html, annual_end, end),
            "automotive_release": release}


def _automotive_revenue(annual, quarter, full_year, interim):
    full_start, full_end = (date.fromisoformat(full_year[key]) for key in ("period_start", "period_end"))
    ytd_start, ytd_end = (date.fromisoformat(interim[key]) for key in ("period_start", "period_end"))
    periods = (
        ("full_year", annual, full_start, full_end),
        ("current_ytd", quarter, ytd_start, ytd_end),
        ("prior_ytd", quarter, ytd_start.replace(year=ytd_start.year - 1), ytd_end.replace(year=ytd_end.year - 1)),
    )
    categories = {"vehicles_and_parts": "VehiclesandPartsMember", "used_vehicles": "UsedVehiclesMember",
                  "services_and_other": "ServicesandOtherMember"}
    revenue_concept = "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"
    amounts = {}
    for label, instance, start, end in periods:
        drivers = {name: _fact(instance, start, end, revenue_concept,
                               {"us-gaap:OperatingSegmentsMember", "AutomotiveMember", member})
                   for name, member in categories.items()}
        total = _fact(instance, start, end, revenue_concept, {"AutomotiveMember"})
        # Product rows are published to the nearest million, separately from the total.
        if not isclose(sum(drivers.values()), total, rel_tol=0, abs_tol=2):
            raise ResearchGap("GM's Automotive product revenues do not reconcile to reported Automotive revenue.")
        amounts[label] = drivers
    trailing = {name: amounts["full_year"][name] + amounts["current_ytd"][name] - amounts["prior_ytd"][name]
                for name in categories}
    return {**amounts, "trailing": trailing, "sources": (annual.url, quarter.url)}


def _finance_equity(client, cutoff, end, start):
    cik = "0000804269"
    filings = recent_filings(client.submissions(cik), as_of=date.fromisoformat(cutoff))
    eligible = [filing for filing in filings
                if filing.form in {"10-Q", "10-Q/A"} and filing.report_date == end]
    if not eligible:
        raise ResearchGap("GM Financial has no standalone quarterly filing for the parent balance date.")
    filing = max(eligible, key=lambda item: (item.filing_date, item.accession))
    instance = load_filing_instance(client, cik, filing.accession)
    assets = _fact(instance, None, end, "us-gaap:Assets", set())
    liabilities = _fact(instance, None, end, "us-gaap:Liabilities", set())
    equity = _fact(instance, None, end, "us-gaap:StockholdersEquity", set())
    if not isclose(assets, liabilities + equity, abs_tol=1):
        raise ResearchGap("GM Financial's standalone balance sheet does not reconcile.")
    preference = {fact.value for fact in instance.facts
                  if fact.concept == "us-gaap:PreferredStockLiquidationPreference"
                  and fact.start is None and fact.end == end and fact.unit == "USD/shares" and not fact.dimensions}
    share_counts = {}
    for fact in instance.facts:
        if (fact.concept == "us-gaap:PreferredStockSharesOutstanding" and fact.start is None
                and fact.end == end and fact.unit == "shares" and len(fact.dimensions) == 1
                and fact.dimensions[0][0] == "us-gaap:StatementClassOfStockAxis"):
            share_counts.setdefault(fact.dimensions[0][1], set()).add(fact.value)
    if len(preference) != 1 or not share_counts or any(len(values) != 1 for values in share_counts.values()):
        raise ResearchGap("GM Financial's preferred liquidation claim is not uniquely supported.")
    claim = preference.pop() * sum(next(iter(values)) for values in share_counts.values()) / 1e6
    earnings = _fact(instance, start, end, "us-gaap:NetIncomeLossAvailableToCommonStockholdersBasic", set())
    prior_end = end.replace(year=end.year - 1)
    prior_start = start.replace(year=start.year - 1)
    annual_end = start - timedelta(days=1)
    financials = [item for item in filings if item.form in {"10-K", "10-K/A"} and item.report_date == annual_end]
    prior_interim = [item for item in filings if item.form in {"10-Q", "10-Q/A"} and item.report_date == prior_end]
    if not financials or not prior_interim:
        raise ResearchGap("GM Financial needs the matched annual and prior interim filings.")
    annual = load_filing_instance(client, cik, max(financials, key=lambda item: (item.filing_date, item.accession)).accession)
    prior_quarter = load_filing_instance(client, cik, max(prior_interim, key=lambda item: (item.filing_date, item.accession)).accession)

    def earning_assets(filing_instance, boundary):
        return {
            "net_receivables_millions": _fact(filing_instance, None, boundary, "us-gaap:NotesReceivableNet", set()),
            "leased_vehicles_millions": _fact(filing_instance, None, boundary, "us-gaap:PropertySubjectToOrAvailableForOperatingLeaseNet", set()),
        }

    def trailing_value(concept):
        full_year = _fact(annual, prior_start, annual_end, concept, set())
        current = _fact(instance, start, end, concept, set())
        prior = _fact(instance, prior_start, prior_end, concept, set())
        return full_year + current - prior

    return {"preferred_liquidation_millions": claim, "common_capital_proxy_millions": equity - claim,
            "ytd_common_earnings_millions": earnings,
            "trailing_common_earnings_millions": trailing_value("us-gaap:NetIncomeLossAvailableToCommonStockholdersBasic"),
            "trailing_common_distributions_millions": trailing_value("us-gaap:DividendsCommonStock"),
            "earning_assets": earning_assets(instance, end), "prior_earning_assets": earning_assets(prior_quarter, prior_end),
            "end": end, "sources": (annual.url, prior_quarter.url, instance.url)}

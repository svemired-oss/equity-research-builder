"""Read full-year pro forma revenue disclosures without inventing acquiree sales."""

from datetime import date
from math import isfinite

from .filing_xbrl import load_filing_instance
from .operating_research import ResearchGap

_PRO_FORMA_REVENUE = "us-gaap:BusinessAcquisitionsProFormaRevenue"
_INCLUDED_REVENUE = "us-gaap:BusinessCombinationProFormaInformationRevenueOfAcquireeSinceAcquisitionDateActual"


def _acquiree_increment(facts, start, end):
    """Use one identified acquiree's full-year revenue less its already-consolidated contribution."""
    relevant = [fact for fact in facts if fact.unit == "USD" and fact.start == start and fact.end == end
                and len(fact.dimensions) == 1 and fact.dimensions[0][0] == "us-gaap:BusinessAcquisitionAxis"]
    revenue_tags = {"us-gaap:Revenues", "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
                    "us-gaap:SalesRevenueNet"}
    members = {fact.dimensions for fact in relevant if fact.concept in revenue_tags}
    if len(members) != 1:
        return None
    member = next(iter(members))
    sales = {fact.value for fact in relevant if fact.dimensions == member and fact.concept in revenue_tags}
    included = {fact.value for fact in relevant if fact.dimensions == member and fact.concept == _INCLUDED_REVENUE}
    if len(sales) != 1 or len(included) != 1:
        return None
    annual, recognized = next(iter(sales)), next(iter(included))
    if not 0 <= recognized <= annual:
        raise ResearchGap("Disclosed acquiree revenue already included exceeds its full-year sales.")
    return annual - recognized


def material_acquisitions(packet):
    annual = packet["actuals"]["periods"]
    interim = packet["interim_actuals"]["periods"]
    if not annual or not interim:
        return []
    sales = annual[-1]["metrics"].get("revenue")
    if sales is None or sales <= 0:
        return []
    return [fact for fact in packet["operating_facts"]["observations"]
            if fact["metric"] == "cash_spent_on_acquisitions"
            and fact["period_end"] == interim[-1]["period_end"] and fact["value"] > .10 * sales]


def extract_acquisition_basis(packet, instance):
    """Require matching full-year combined revenue for both historical comparison periods."""
    periods = packet["actuals"]["periods"][-2:]
    if len(periods) != 2:
        raise ResearchGap("Material acquisition needs two comparable annual revenue periods.")
    result = {"periods": [], "source": instance.url}
    for period in periods:
        if not period.get("period_start"):
            raise ResearchGap("Material acquisition needs a verified annual period start.")
        start, end = (date.fromisoformat(period[key]) for key in ("period_start", "period_end"))
        values = {fact.value for fact in instance.facts
                  if fact.concept == _PRO_FORMA_REVENUE and fact.unit == "USD"
                  and fact.start == start and fact.end == end and not fact.dimensions}
        increment = _acquiree_increment(instance.facts, start, end) if not values else None
        if not values and increment is not None:
            values = {period["metrics"]["revenue"] + increment}
        if len(values) != 1:
            reason = "conflicting" if len(values) > 1 else "missing"
            raise ResearchGap(f"Material acquisition has {reason} disclosed full-year combined pro forma revenue "
                              f"(or acquiree revenue and revenue already included) for {period['period_end']}.")
        total = next(iter(values))
        reported = period["metrics"]["revenue"]
        if not isfinite(total) or total < reported:
            raise ResearchGap("Acquisition pro forma revenue is below the comparable reported revenue.")
        result["periods"].append({
            "period_start": period["period_start"], "period_end": period["period_end"],
            "reported_revenue": reported, "combined_revenue": total, "incremental_acquiree_revenue": total - reported,
            "unit": "USD", "concept": _PRO_FORMA_REVENUE if increment is None else "acquiree_sales_less_already_included",
            "url": instance.url,
        })
    return result


def collect_acquisition_basis(packet, client):
    if not material_acquisitions(packet):
        return
    period = packet["interim_actuals"]["periods"][-1]
    instance = load_filing_instance(client, packet["issuer"]["cik"], period["filing"]["accession"])
    packet["acquisition_revenue_basis"] = extract_acquisition_basis(packet, instance)

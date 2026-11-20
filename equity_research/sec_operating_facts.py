"""Collect dimensional revenue and disclosed backlog facts from SEC filings."""

from collections import defaultdict
from datetime import date
from decimal import Decimal
import re

from .filing_xbrl import FilingInstance, load_filing_instance
from .sec_guidance import _ReleaseText


_REVENUE = {
    "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax",
    "us-gaap:RevenueFromContractWithCustomerIncludingAssessedTax",
    "us-gaap:Revenues",
    "us-gaap:SalesRevenueNet",
    "us-gaap:RegulatedAndUnregulatedOperatingRevenue",
}
_OPERATING_INCOME = "us-gaap:OperatingIncomeLoss"
_BACKLOG = "us-gaap:RevenueRemainingPerformanceObligation"
_SEGMENT_AXIS = "us-gaap:StatementBusinessSegmentsAxis"
_PRODUCT_AXIS = "srt:ProductOrServiceAxis"
_OPERATING_SEGMENTS = ("srt:ConsolidationItemsAxis", "us-gaap:OperatingSegmentsMember")
_CORPORATE = ("srt:ConsolidationItemsAxis", "us-gaap:CorporateNonSegmentMember")
_RPO_TIMING = re.compile(
    r"\bremaining performance obligations were \$((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s+"
    r"(million|billion|trillion)"
    r"[^.;]{0,80},\s+of which we expect to recognize approximately (\d+(?:\.\d+)?)% as revenues "
    r"over the next (?:12|twelve) months,\s+(\d+(?:\.\d+)?)% over the subsequent months? 13 to months? 36,\s+"
    r"(\d+(?:\.\d+)?)% over the subsequent months? 37 to months? 60\s*,?\s+and the remainder thereafter\b",
    re.IGNORECASE,
)
_DOLLAR_SCALE = {"million": 10**6, "billion": 10**9, "trillion": 10**12}
_MONTH_NAMES = "January February March April May June July August September October November December".split()


def _classification(fact, start, end):
    if fact.unit != "USD" or fact.end != end:
        return None
    if fact.start is None and fact.concept == _BACKLOG and not fact.dimensions:
        return "remaining_performance_obligations"
    if start is None or fact.start != start:
        return None
    acquisition_tag = fact.concept == "us-gaap:PaymentsToAcquireBusinessesNetOfCashAcquired" or fact.concept.rsplit("}", 1)[-1] == (
        "PaymentsToAcquireBusinessesNetOfCashAcquiredAndResearchAndDevelopmentInProcess")
    if acquisition_tag and not fact.dimensions:
        return "cash_spent_on_acquisitions"
    # Walmart presents company-campus income in its segment revenue
    # reconciliation as OtherIncome rather than as a reportable segment.
    if fact.concept == "us-gaap:OtherIncome" and len(fact.dimensions) == 1:
        axis, member = fact.dimensions[0]
        if axis == "srt:ConsolidationItemsAxis" and re.fullmatch(
                r"\{https?://www\.walmart\.com/\d{8}\}CorporateAndReconcilingItemsMember", member):
            return "corporate_nonsegment_revenue"
    if fact.concept not in _REVENUE and fact.concept != _OPERATING_INCOME:
        return None
    if not fact.dimensions and fact.concept in _REVENUE:
        return "consolidated_revenue"
    if fact.dimensions == (_CORPORATE,) and fact.concept in _REVENUE:
        return "corporate_nonsegment_revenue"

    # Ignore geography, legal entities and other qualifiers that change scope.
    # The operating-segments qualifier does not identify a driver by itself.
    dimensions = tuple(dimension for dimension in fact.dimensions if dimension != _OPERATING_SEGMENTS)
    if len(dimensions) != 1:
        return None
    axis, _ = dimensions[0]
    if axis == _SEGMENT_AXIS:
        return "segment_operating_income" if fact.concept == _OPERATING_INCOME else "segment_revenue"
    if axis == _PRODUCT_AXIS and fact.concept in _REVENUE:
        return "product_or_service_revenue"
    return None


def _observations(instance: FilingInstance, period: dict) -> tuple[list[dict], list[dict]]:
    end = date.fromisoformat(period["period_end"])
    start = date.fromisoformat(period["period_start"]) if period.get("period_start") else None
    filing = period["filing"]
    contexts = {(start, end)}
    if start:
        # Use comparable durations from the same filing as the latest figures,
        # including fiscal calendars with a 53-week year.
        prior = {
            (fact.start, fact.end) for fact in instance.facts
            if fact.start is not None and not fact.dimensions and fact.concept in _REVENUE and fact.unit == "USD"
            and 357 <= (end - fact.end).days <= 373 and 357 <= (start - fact.start).days <= 373
            and abs((end - start).days - (fact.end - fact.start).days) <= 7
        }
        if len(prior) == 1:
            contexts.update(prior)
    by_fact = defaultdict(set)
    for fact in instance.facts:
        if (fact.start, fact.end) in contexts:
            metric = _classification(fact, fact.start, fact.end)
        elif fact.start is None and fact.end == end:
            metric = _classification(fact, None, end)
        else:
            continue
        if metric is not None:
            start_iso = fact.start.isoformat() if fact.start else ""
            by_fact[(fact.end.isoformat(), start_iso, metric, fact.concept, fact.dimensions)].add(fact.value)

    facts, conflicts = [], []
    for (end_iso, start_iso, metric, concept, dimensions), values in sorted(by_fact.items()):
        observation = {
            "metric": metric,
            "concept": concept,
            "comparison": end_iso != end.isoformat(),
            "dimensions": [{"axis": axis, "member": member} for axis, member in dimensions],
            "unit": "USD",
            "period_start": start_iso or None,
            "period_end": end_iso,
            "form": filing["form"],
            "filed_on": filing["filed_on"],
            "accession": filing["accession"],
            "url": instance.url,
        }
        if len(values) == 1:
            facts.append({**observation, "value": next(iter(values))})
        else:
            conflicts.append({**observation, "values": sorted(values)})
    return facts, conflicts


def _backlog_timing(document: str, tagged_amount: dict, filing: dict) -> tuple[dict | None, str | None]:
    end = date.fromisoformat(tagged_amount["period_end"])
    reported_date = f"{_MONTH_NAMES[end.month - 1]} {end.day}, {end.year}"
    schedules = {}
    for paragraph in _ReleaseText(document).blocks:
        for match in _RPO_TIMING.finditer(paragraph):
            # The note can place the as-of date immediately before or after the dollar figure.
            surrounding = paragraph[max(0, match.start() - 55):match.end()]
            if not re.search(rf"\bas of {reported_date}\b", surrounding, re.I):
                continue
            amount = Decimal(match[1].replace(",", "")) * _DOLLAR_SCALE[match[2].lower()]
            if amount != tagged_amount["value"]:
                continue
            buckets = tuple(Decimal(value) for value in match.groups()[2:])
            if sum(buckets) <= 100:
                schedules[buckets] = surrounding
    if len(schedules) != 1:
        reason = "conflicting" if schedules else "no matching"
        return None, f"{filing['accession']}: {reason} backlog conversion schedule in the supported disclosure format."
    buckets, evidence = next(iter(schedules.items()))
    months = [(1, 12), (13, 36), (37, 60), (61, None)]
    percentages = (*buckets, 100 - sum(buckets))
    return {
        "metric": "remaining_performance_obligation_recognition",
        "period_start": None,
        "period_end": end.isoformat(),
        "filed_on": filing["filed_on"],
        "accession": filing["accession"],
        "url": filing["url"],
        "evidence": evidence,
        "approximate": True,
        "windows": [
            {"start_month": first, "end_month": last, "percent": float(percent), "derived": last is None}
            for (first, last), percent in zip(months, percentages, strict=True)
        ],
    }, None


def collect_operating_facts(client, cik: str, actuals: dict, interim: dict) -> dict:
    """Read the latest mapped annual and interim filings without combining dimensions."""
    periods = [*actuals["periods"][-1:], *interim["periods"][-1:]]
    observations, conflicts, missing = [], [], []
    for period in periods:
        filing = period.get("filing")
        if not filing:
            missing.append(f"{period['period_end']}: no mapped filing for operating facts.")
            continue
        accession = filing["accession"]
        try:
            instance = load_filing_instance(client, cik, accession)
        except (ValueError, OSError) as exc:
            missing.append(f"{accession}: extracted operating facts unavailable. {exc}")
            continue
        facts, issues = _observations(instance, period)
        observations.extend(facts)
        conflicts.extend(issues)
        if not facts:
            missing.append(f"{accession}: no unambiguous USD operating facts matched the reporting period.")
        backlog = [fact for fact in facts if fact["metric"] == "remaining_performance_obligations"]
        if backlog and filing.get("url"):
            try:
                timing, issue = _backlog_timing(client.get_text(filing["url"]), backlog[0], filing)
            except (ValueError, ArithmeticError, OSError):
                timing, issue = None, f"{accession}: unable to read backlog conversion timing."
            if timing:
                observations.append(timing)
            if issue:
                missing.append(issue)
    if not periods:
        missing.append("No mapped annual or interim period is available for operating facts.")
    return {
        "status": "conflicting" if conflicts else "available" if observations else "missing",
        "observations": observations,
        "conflicts": conflicts,
        "missing": missing,
        "limitations": [
            "Only selected tagged facts from the latest mapped annual and interim filings are included, with a prior-year comparison when the dates match uniquely.",
            "Revenue observations may overlap. A product group can include other product rows, and segments can include intersegment revenue.",
            "Where available in the supported note format, backlog recognition percentages are issuer estimates in rolling month windows.",
        ],
    }

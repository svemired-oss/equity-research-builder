"""Stage 1 coverage fallbacks: revenue reconciliation, interim debt crosswalk and co-registrant instances."""

from datetime import date

import pytest

from equity_research import filing_xbrl
from equity_research.filing_xbrl import FilingInstance, XbrlFact
from equity_research.operating_evidence import recover_comparative_debt
from equity_research.revenue_fallbacks import (
    CONSOLIDATED_LINE, RECONCILING_LINE, fallback_revenue_candidates,
)

PRIOR, LATEST = "2024-12-31", "2025-12-31"
QUALIFIER = {"axis": "srt:ConsolidationItemsAxis", "member": "us-gaap:OperatingSegmentsMember"}


def periods(prior_total, latest_total):
    filing = {"accession": "a-1"}
    return ({"period_end": PRIOR, "metrics": {"revenue": prior_total * 1e6}, "filing": filing},
            {"period_end": LATEST, "metrics": {"revenue": latest_total * 1e6}, "filing": filing})


def fact(member, end, value, metric="segment_revenue", axis="us-gaap:StatementBusinessSegmentsAxis",
         concept="us-gaap:Revenues", qualified=False):
    dimensions = [{"axis": axis, "member": f"co:{member}"}]
    if qualified:
        dimensions.insert(0, QUALIFIER)
    if metric == "corporate_nonsegment_revenue":
        dimensions = [{"axis": "srt:ConsolidationItemsAxis", "member": "us-gaap:CorporateNonSegmentMember"}]
    return {"metric": metric, "concept": concept, "unit": "USD", "accession": "a-1", "period_end": end,
            "dimensions": dimensions, "value": value * 1e6}


def lines(values, **kwargs):
    return [fact(member, end, value, **kwargs) for member, by_end in values.items() for end, value in by_end.items()]


def test_nested_product_lines_resolve_to_the_finest_partition():
    # Salesforce-shaped: a subscription parent beside its five children.
    product = {"metric": "product_or_service_revenue", "axis": "srt:ProductOrServiceAxis"}
    children = {"A": (10, 12), "B": (20, 21), "C": (30, 33), "D": (5, 6), "E": (15, 18)}
    rows = {key: {PRIOR: old, LATEST: new} for key, (old, new) in children.items()}
    rows["Subscription"] = {PRIOR: 80, LATEST: 90}
    rows["Services"] = {PRIOR: 20, LATEST: 22}
    candidates = fallback_revenue_candidates(*periods(100, 112), lines(rows, **product))
    chosen = candidates["product_or_service_revenue"]
    assert set(chosen["latest"]) == {*children, "Services"}
    assert "nested parent and child" in chosen["basis"]


def test_two_unrelated_partitions_are_ambiguous_and_fall_back():
    product = {"metric": "product_or_service_revenue", "axis": "srt:ProductOrServiceAxis"}
    rows = {"A": {PRIOR: 40, LATEST: 45}, "B": {PRIOR: 60, LATEST: 65},
            "C": {PRIOR: 30, LATEST: 35}, "D": {PRIOR: 70, LATEST: 75}}
    candidates = fallback_revenue_candidates(*periods(100, 110), lines(rows, **product))
    assert set(candidates) == {"consolidated_revenue"}
    assert candidates["consolidated_revenue"]["latest"] == {CONSOLIDATED_LINE: 110}
    assert candidates["consolidated_revenue"]["basis"].startswith("Analyst proxy")


def test_gross_and_net_segment_populations_are_evaluated_separately():
    # ConocoPhillips-shaped: the qualified Canada value is before intersegment eliminations.
    net = {"Alaska": {PRIOR: 30, LATEST: 31}, "Canada": {PRIOR: 20, LATEST: 24},
           "Lower48": {PRIOR: 50, LATEST: 55}}
    gross = {**net, "Canada": {PRIOR: 28, LATEST: 33}}
    facts = lines(net) + lines(gross, qualified=True)
    candidates = fallback_revenue_candidates(*periods(100, 110), facts)
    assert candidates["segment_revenue"]["latest"] == {"Alaska": 31, "Canada": 24, "Lower48": 55}


def test_single_segment_pairs_with_the_corporate_line():
    facts = lines({"Biopharma": {PRIOR: 95, LATEST: 104}})
    facts += [fact("Corporate", PRIOR, 5, metric="corporate_nonsegment_revenue"),
              fact("Corporate", LATEST, 6, metric="corporate_nonsegment_revenue")]
    candidates = fallback_revenue_candidates(*periods(100, 110), facts)
    assert candidates["segment_revenue"]["latest"] == {"Biopharma": 104, "CorporateNonSegmentMember": 6}
    assert "corporate line" in candidates["segment_revenue"]["basis"]


@pytest.mark.parametrize(("prior_gap", "latest_gap", "accepted"), [
    (1.5, 2, True),      # Unallocated hedging or corporate revenue.
    (-3, -4, True),      # Intersegment eliminations.
    (6, 6, False),       # More than 5% of revenue.
    (2, -2, False),      # Sign change leaves no defined growth rate.
])
def test_reconciling_line_is_bounded_and_same_sign(prior_gap, latest_gap, accepted):
    rows = {"North": {PRIOR: 60 - prior_gap, LATEST: 66 - latest_gap}, "South": {PRIOR: 40, LATEST: 44}}
    candidates = fallback_revenue_candidates(*periods(100, 110), lines(rows))
    if accepted:
        chosen = candidates["segment_revenue"]
        assert chosen["prior"][RECONCILING_LINE] == pytest.approx(prior_gap)
        assert chosen["latest"][RECONCILING_LINE] == pytest.approx(latest_gap)
        assert sum(chosen["latest"].values()) == pytest.approx(110)
        assert chosen["basis"].startswith("Analyst proxy")
    else:
        assert set(candidates) == {"consolidated_revenue"}


def test_missing_consolidated_revenue_produces_no_fallback():
    prior, latest = periods(100, 110)
    latest["metrics"]["revenue"] = None
    assert fallback_revenue_candidates(prior, latest, []) == {}


def instance(values):
    return FilingInstance("https://www.sec.gov/q.xml", tuple(
        XbrlFact(f"us-gaap:{concept}", None, date.fromisoformat(end), (), "USD", value * 1e6)
        for concept, by_end in values.items() for end, value in by_end.items()))


def debt_periods(**annual):
    annual_period = {"period_end": "2025-12-27", "metrics": {key: value * 1e6 for key, value in annual.items()}}
    interim = {"period_end": "2026-09-05", "metrics": {}, "filing": {"accession": "q-1", "url": "https://www.sec.gov/q"},
               "metric_issues": {"current_debt": "not_reported_in_eligible_interim_filings"}}
    return annual_period, interim


def test_interim_debt_uses_the_concept_matching_the_annual_comparative():
    # PepsiCo-shaped: the 10-K tags DebtCurrent, the 10-Q tags ShortTermBorrowings.
    annual, interim = debt_periods(current_debt=6861, long_term_debt=42321)
    notes = recover_comparative_debt(interim, annual, instance({
        "ShortTermBorrowings": {"2025-12-27": 6861, "2026-09-05": 9223},
        "CommercialPaper": {"2026-09-05": 4800},
        "LongTermDebtAndCapitalLeaseObligations": {"2025-12-27": 42321, "2026-09-05": 42658},
    }))
    assert interim["metrics"]["current_debt"] == 9223e6
    assert interim["metrics"]["long_term_debt"] == 42658e6
    assert interim["sources"]["current_debt"]["tag"] == "ShortTermBorrowings"
    assert interim["sources"]["current_debt"]["derivation"] == "year_end_comparative_crosswalk"
    assert "current_debt" not in interim["metric_issues"]
    assert len(notes) == 2 and "matches the annual current debt" in notes[0]


def test_interim_debt_crosswalk_rejects_mismatch_conflict_and_ambiguity():
    annual, interim = debt_periods(current_debt=6861)
    recover_comparative_debt(interim, annual, instance({"ShortTermBorrowings": {"2025-12-27": 6000, "2026-09-05": 9223}}))
    assert "current_debt" not in interim["metrics"]
    recover_comparative_debt(interim, annual, instance({
        "ShortTermBorrowings": {"2025-12-27": 6861, "2026-09-05": 9223},
        "DebtCurrent": {"2025-12-27": 6861, "2026-09-05": 9000},
    }))
    assert "current_debt" not in interim["metrics"]
    interim["metric_issues"]["current_debt"] = "conflicting_values_in_latest_eligible_filing"
    recover_comparative_debt(interim, annual, instance({"ShortTermBorrowings": {"2025-12-27": 6861, "2026-09-05": 9223}}))
    assert "current_debt" not in interim["metrics"]


def test_co_registrant_instance_link_is_read_from_the_issuer_path(monkeypatch):
    index = ('<table><tr><td>1</td><td>EXTRACTED XBRL INSTANCE DOCUMENT</td>'
             '<td><a href="/Archives/edgar/data/3153/000009212226000006/so-20251231_htm.xml">x</a></td></tr></table>')
    requested = []

    class Client:
        def get_text(self, url):
            requested.append(url)
            return index if url.endswith("-index.htm") else "<xbrl/>"

    monkeypatch.setattr(filing_xbrl, "parse_filing_instance", lambda text, url: FilingInstance(url, ()))
    loaded = filing_xbrl.load_filing_instance(Client(), "0000092122", "0000092122-26-000006")
    assert loaded.url == "https://www.sec.gov/Archives/edgar/data/92122/000009212226000006/so-20251231_htm.xml"
    foreign = index.replace("000009212226000006", "000000000026000001")
    Client.get_text = lambda self, url: foreign if url.endswith("-index.htm") else "<xbrl/>"
    with pytest.raises(filing_xbrl.SECError, match="Missing"):
        filing_xbrl.load_filing_instance(Client(), "0000092122", "0000092122-26-000006")

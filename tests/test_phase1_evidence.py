from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
import json
from pathlib import Path

import pytest

from equity_research import acquisition_basis, operating_filing_bridge
from equity_research.apple_export import workbook_plan
from equity_research.filing_xbrl import FilingInstance, XbrlFact
from equity_research.operating_evidence import assume_absent_balances, apply_capital_events, recover_alternative_cash
from equity_research.operating_research import ResearchGap, _balance, prepare_operating_model, revenue_candidates
from equity_research.sec_capital_events import _read_events
from equity_research.sec_financials import _choose_fact

FIXTURES = Path(__file__).parent / "fixtures"


def read(name):
    return json.loads((FIXTURES / name).read_text())


def period():
    return read("operating.json")["packet"]["actuals"]["periods"][-1]


@pytest.mark.parametrize("metric", ["short_term_investments", "finance_lease_current",
                                   "finance_lease_noncurrent", "commercial_paper"])
def test_optional_zero_is_conditioned_and_audited(metric):
    row = period()
    row["metrics"].pop(metric, None)
    assume_absent_balances(row)
    assert row["metrics"][metric] == 0
    assert row["sources"][metric]["kind"] == "analyst_assumption"
    assert "proxy" in row["sources"][metric]["basis"]
    row = period()
    row["metrics"].pop(metric, None)
    row["metrics"]["total_assets"] += 1_000_001
    assume_absent_balances(row)
    assert row["metrics"].get(metric) is None


def test_zero_cannot_hide_a_reported_conflict_or_unallocated_leases():
    row = period()
    row["metrics"].pop("finance_lease_current")
    row["metric_issues"] = {"finance_lease_current": "conflicting_values_in_latest_eligible_filing"}
    with pytest.raises(ResearchGap, match="conflicted"):
        assume_absent_balances(row)
    row.pop("metric_issues")
    row["combined_unallocated_leases"] = True
    with pytest.raises(ResearchGap, match="Combined"):
        assume_absent_balances(row)


def test_latest_filing_matching_units_preserves_restatement_provenance():
    older = {"filed_on": "2025-02-01", "period_end": "2024-12-31", "accession": "old",
             "form": "10-K", "url": "https://example.org/old"}
    newer = {**older, "filed_on": "2026-02-01", "accession": "new", "period_end": "2025-12-31", "url": "https://example.org/new"}
    end = date(2024, 12, 31)
    observations = {("cash_and_equivalents", end): {
        "old": {"CashAndCashEquivalentsAtCarryingValue": {(None, 100)}},
        "new": {"CashAndCashEquivalentsAtCarryingValue": {(None, 120)}},
    }}
    filings = {"old": older, "new": newer}
    value, source, issue = _choose_fact("cash_and_equivalents", end, None, observations, filings, "missing")
    assert (value, issue, source["accession"], source["unit"]) == (120, None, "new", "USD")
    assert source["superseded"][0]["accession"] == "old"
    observations["cash_and_equivalents", end]["new"]["CashAndCashEquivalentsAtCarryingValue"].add((None, 121))
    assert _choose_fact("cash_and_equivalents", end, None, observations, filings, "missing")[2] == "conflicting_values_in_latest_eligible_filing"


def test_bridge_instance_uses_latest_usd_disclosure(monkeypatch):
    row = period()
    row["filing"].update(filed_on="2026-02-01", form="10-K", period_end="2025-12-31")
    newer = {**row["filing"], "accession": "amendment", "filed_on": "2026-03-01", "form": "10-K/A"}
    packet = {"as_of": "2026-06-30", "issuer": {"cik": "0000000001"}, "filings": {"annual": [newer]}}
    fact = XbrlFact("us-gaap:CashAndCashEquivalentsAtCarryingValue", None, date(2025, 12, 31), (), "USD", 120)
    instances = {"amendment": FilingInstance("https://example.org/new", (fact,)),
                 row["filing"]["accession"]: FilingInstance("https://example.org/old", (replace(fact, value=100),))}
    monkeypatch.setattr(operating_filing_bridge, "load_filing_instance", lambda client, cik, accession: instances[accession])
    instance, selected = operating_filing_bridge._latest_period_instance(packet, row, None, {})
    assert selected["accession"] == "amendment"
    assert instance.facts[0].value == 120
    with pytest.raises(ResearchGap, match="Conflicting"):
        operating_filing_bridge._single([fact, replace(fact, value=121)], {fact.concept})
    instances["amendment"] = FilingInstance("https://example.org/nonusd", (replace(fact, unit="EUR"),))
    assert operating_filing_bridge._latest_period_instance(packet, row, None, {})[1]["accession"] == row["filing"]["accession"]


@pytest.mark.parametrize("concept", [
    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    "CashAndDueFromBanks",
])
def test_cash_alternative_ties_to_cash_flow_and_excludes_known_restrictions(concept):
    data = read("phase1.json")["cash"]
    row = period()
    row["period_start"] = "2025-01-01"
    row["metrics"].pop("cash_and_equivalents")
    end, start = date(2025, 12, 31), date(2025, 1, 1)
    facts = [
        XbrlFact("us-gaap:" + concept, None, start - timedelta(days=1), (), "USD", data["opening"]),
        XbrlFact("us-gaap:" + concept, None, end, (), "USD", data["closing"]),
        XbrlFact("us-gaap:CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsPeriodIncreaseDecreaseIncludingExchangeRateEffect",
                 start, end, (), "USD", data["change"]),
        XbrlFact("us-gaap:RestrictedCash", None, end, (), "USD", data["restricted"]),
    ]
    instance = FilingInstance("https://example.org/cash", tuple(facts))
    recover_alternative_cash(row, instance)
    expected = data["available"] if "Restricted" in concept else data["closing"]
    assert row["metrics"]["cash_and_equivalents"] == expected
    assert row["sources"]["cash_and_equivalents"]["kind"] == "cash_proxy"
    row["metrics"].pop("cash_and_equivalents")
    facts[2] = replace(facts[2], value=data["change"] + 1_000_001)
    recover_alternative_cash(row, FilingInstance(instance.url, tuple(facts)))
    assert "cash_and_equivalents" not in row["metrics"]


def test_completed_financing_rolls_cash_debt_and_shares_once():
    data = read("phase1.json")
    events = data["financing_events"]
    cash, debt, shares, changes = apply_capital_events({"capital_bridge": {"events": events + [events[0]]}},
        date(2025, 12, 31), date(2026, 6, 30), 20, 30, 10)
    assert (cash, debt, shares) == pytest.approx(tuple(data["expected_bridge"].values()))
    assert len(changes) == 3
    assert apply_capital_events({"capital_bridge": {"events": events}}, date(2025, 12, 31),
        date(2026, 6, 30), 20, 30, 9.8, date(2026, 6, 15))[2] == pytest.approx(9.8)
    bad = deepcopy(events)
    bad[0].pop("net_proceeds")
    with pytest.raises(ResearchGap, match="net proceeds"):
        apply_capital_events({"capital_bridge": {"events": bad}}, date(2025, 12, 31), date(2026, 6, 30), 20, 30, 10)
    with pytest.raises(ResearchGap, match="cutoff"):
        apply_capital_events({"capital_bridge": {"events": events}}, date(2025, 12, 31), date(2026, 4, 1), 20, 30, 10)


def test_financing_parser_extracts_completed_disclosures_only():
    records = list(_read_events(read("phase1.json")["capital_filing"]))
    assert [row["kind"] for row in records] == ["debt_issuance", "debt_repayment", "share_repurchase"]
    assert records[0]["net_proceeds"] == 9_800_000
    assert records[1]["cash_paid"] == 2_000_000
    assert records[2]["shares_repurchased"] == 200_000
    assert list(_read_events("<p>Item 2.03</p><p>We may repurchase up to $5 billion.</p>")) == []


def test_preferred_and_minority_book_proxies_flow_through_excel():
    data = read("operating.json")
    args = (data["packet"], data["treasury"], data["quote"], data["selection"])
    candidates = revenue_candidates(data["packet"])
    original = prepare_operating_model(*args, candidates)
    row = data["packet"]["actuals"]["periods"][-1]
    row["metrics"]["preferred_stock_book_value"] = 5_000_000
    row["metrics"]["noncontrolling_interest"] = 2_000_000
    revised = prepare_operating_model(*args, candidates)
    assert original["scenarios"]["Base"].value_per_share - revised["scenarios"]["Base"].value_per_share == pytest.approx(.7)
    plan = workbook_plan(revised)
    assert plan["cells"]["DCF"]["B10"][0] == 5
    assert plan["cells"]["DCF"]["B14"][0] == "=B13+B6+B7-SUM(B8:B10)"
    assert any("Book-value proxy" in note for note in revised["notes"])


def test_financing_bridge_exports_live_formulas():
    data = read("operating.json")
    data["packet"]["capital_bridge"]["events"] = read("phase1.json")["financing_events"]
    model = prepare_operating_model(data["packet"], data["treasury"], data["quote"], data["selection"],
                                    revenue_candidates(data["packet"]))
    plan = workbook_plan(model)
    assert plan["cells"]["DCF"]["B6"][0].startswith("='Equity bridge'!")
    assert plan["cells"]["DCF"]["B12"][0].startswith("='Equity bridge'!")
    assert plan["expected"]["DCF"]["B6"] == pytest.approx(22.8)


def acquisition_packet():
    packet = read("operating.json")["packet"]
    packet["actuals"]["periods"][-2]["period_start"] = "2024-01-01"
    packet["actuals"]["periods"][-1]["period_start"] = "2025-01-01"
    return packet


def test_pro_forma_revenue_is_disclosed_and_removes_double_counting():
    packet = acquisition_packet()
    amounts = read("phase1.json")["pro_forma"]
    facts = []
    for period, key in zip(packet["actuals"]["periods"][-2:], ("prior", "latest"), strict=True):
        facts.append(XbrlFact("us-gaap:BusinessAcquisitionsProFormaRevenue", date.fromisoformat(period["period_start"]),
                               date.fromisoformat(period["period_end"]), (), "USD", amounts[key]))
    basis = acquisition_basis.extract_acquisition_basis(packet, FilingInstance("https://example.org/acquisition", tuple(facts)))
    packet["acquisition_revenue_basis"] = basis
    assert revenue_candidates(packet)["pro_forma_acquisition"]["latest"] == {"CombinedOperations": 150}
    facts.clear()
    for period, key in zip(packet["actuals"]["periods"][-2:], ("prior", "latest"), strict=True):
        dims = (("us-gaap:BusinessAcquisitionAxis", "example:Acquiree"),)
        start, end = (date.fromisoformat(period[k]) for k in ("period_start", "period_end"))
        facts.extend([
            XbrlFact("us-gaap:Revenues", start, end, dims, "USD", amounts["acquiree_" + key]),
            XbrlFact("us-gaap:BusinessCombinationProFormaInformationRevenueOfAcquireeSinceAcquisitionDateActual",
                     start, end, dims, "USD", amounts["included_" + key]),
        ])
    assert acquisition_basis.extract_acquisition_basis(packet, FilingInstance("https://example.org/acquiree", tuple(facts)))["periods"][-1]["combined_revenue"] == 150_000_000
    with pytest.raises(ResearchGap, match="missing disclosed full-year"):
        acquisition_basis.extract_acquisition_basis(packet, FilingInstance("https://example.org/missing", ()))


def test_revenue_selection_ignores_nonrevenue_observations_without_dimensions():
    packet = read("operating.json")["packet"]
    expected = revenue_candidates(packet)
    packet["operating_facts"]["observations"].append({
        "metric": "remaining_performance_obligations_timing", "period_end": "2025-12-31",
        "value": 100, "url": "https://example.org/backlog"})
    assert revenue_candidates(packet) == expected
    packet["operating_facts"]["observations"][0].pop("dimensions")
    with pytest.raises(ResearchGap, match="reporting dimensions"):
        revenue_candidates(packet)


def test_material_acquisition_requires_full_year_disclosure_and_logs_margin_proxy():
    packet = acquisition_packet()
    latest = deepcopy(packet["actuals"]["periods"][-1])
    latest["period_end"] = "2026-06-30"
    packet["interim_actuals"]["periods"] = [latest]
    packet["operating_facts"]["observations"].append({
        "metric": "cash_spent_on_acquisitions", "period_end": "2026-06-30", "value": 50e6})
    from equity_research.operating_research import verify_comparable_operating_basis
    with pytest.raises(ResearchGap, match="disclosed full-year"):
        verify_comparable_operating_basis(packet)
    facts = [XbrlFact("us-gaap:BusinessAcquisitionsProFormaRevenue", date.fromisoformat(row["period_start"]),
                       date.fromisoformat(row["period_end"]), (), "USD", amount)
             for row, amount in zip(packet["actuals"]["periods"][-2:], (135e6, 150e6), strict=True)]
    packet["acquisition_revenue_basis"] = acquisition_basis.extract_acquisition_basis(
        packet, FilingInstance("https://example.org/proforma", tuple(facts)))
    verify_comparable_operating_basis(packet)
    data = read("operating.json")
    model = prepare_operating_model(packet, data["treasury"], data["quote"],
        {**data["selection"], "group": "pro_forma_acquisition"}, revenue_candidates(packet))
    assert model["sales"]["CombinedOperations"] == 150
    assert any("analyst proxies" in note for note in model["notes"])
    with pytest.raises(ResearchGap, match="conflicting"):
        acquisition_basis.extract_acquisition_basis(packet, FilingInstance("https://example.org/conflict",
            tuple(facts + [replace(facts[0], value=136e6)])))


def test_real_export_recalculates_financing_and_claims(tmp_path):
    import shutil
    if not shutil.which("soffice"):
        pytest.skip("LibreOffice installed in Linux CI")
    from equity_research.workbook_renderer import render_workbook
    from openpyxl import load_workbook
    data = read("operating.json")
    data["packet"]["capital_bridge"]["events"] = read("phase1.json")["financing_events"]
    row = data["packet"]["actuals"]["periods"][-1]
    row["metrics"]["preferred_stock_book_value"] = 5_000_000
    row["metrics"]["noncontrolling_interest"] = 2_000_000
    model = prepare_operating_model(data["packet"], data["treasury"], data["quote"], data["selection"],
                                    revenue_candidates(data["packet"]))
    plan = workbook_plan(model)
    plan["recalculationProbe"] = {"input": {"sheet": "Equity bridge", "address": "B6", "delta": 1},
                                  "changed": [{"sheet": "DCF", "address": "B15"}],
                                  "unchanged": [{"sheet": "Actuals", "address": "F4"}]}
    output = tmp_path / "financing.xlsx"
    render_workbook(plan, output, None, None)
    saved = load_workbook(output, data_only=True)
    assert saved["DCF"]["B6"].value == pytest.approx(22.8)
    assert saved["DCF"]["B8"].value == pytest.approx(38)
    assert saved["DCF"]["B12"].value == pytest.approx(9.8)
    assert saved["DCF"]["B10"].value == 5
    assert saved["Cover"]["B7"].value == pytest.approx(model["scenarios"]["Base"].value_per_share)

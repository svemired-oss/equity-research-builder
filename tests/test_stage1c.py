"""Stage 1c: current-debt zero rule, segment and related-party debt, noncurrent debt and
cash tag fallbacks, guidance quote cleaning and the separation stop.

Fixture values are the filed amounts (USD millions) from each issuer's 10-K or 10-Q
used by the 2026-11-19 benchmark collection.
"""
from datetime import date
import copy

import pytest

from equity_research import operating_filing_bridge as bridge
from equity_research.filing_xbrl import FilingInstance, XbrlFact
from equity_research.operating_evidence import (assume_absent_balances, recover_segment_debt, related_party_debt,
                                                separation_basis_gap)
from equity_research.operating_research import ResearchGap, _balance
from equity_research.report_evidence import clean_passage

M = 1e6


def fact(concept, end, value, dims=(), start=None, unit="USD"):
    name = concept if ":" in concept or concept.startswith("{") else f"us-gaap:{concept}"
    return XbrlFact(name, None if start is None else date.fromisoformat(start), date.fromisoformat(end),
                    tuple(dims), unit, value * M if unit == "USD" else value)


def instance(*facts, url="https://www.sec.gov/Archives/edgar/data/1/x.xml"):
    return FilingInstance(url, tuple(facts))


def period(end, metrics, *, tags=None, start=None, form="10-Q", accession="0000000000-26-000001"):
    filing = {"form": form, "accession": accession, "filed_on": "2026-10-30", "period_end": end,
              "url": f"https://www.sec.gov/Archives/edgar/data/1/{accession}.htm"}
    return {"period_end": end, "period_start": start, "filing": filing,
            "metrics": {key: None if value is None else value * M for key, value in metrics.items()},
            "sources": {key: {"tag": tag} for key, tag in (tags or {}).items()}, "metric_issues": {}}


# --- Current portion of term debt is zero only when a filed total proves it --------------

def _balanced(end, extra, tags):
    base = {"total_assets": 1000, "total_liabilities": 600, "total_equity": 400, "cash_and_equivalents": 50,
            "short_term_investments": 0, "current_assets": 300, "current_liabilities": 200,
            "finance_lease_current": 0, "finance_lease_noncurrent": 0}
    return period(end, {**base, **extra}, tags=tags)


def test_nem_interim_long_term_debt_total_equals_noncurrent_so_current_portion_is_zero():
    # Newmont 10-Q, 2026-09-30: LongTermDebt 5,087 = LongTermDebtNoncurrent 5,087; no current line.
    p = _balanced("2026-09-30", {"long_term_debt": 5087, "long_term_debt_including_current": 5087},
                  {"long_term_debt": "LongTermDebtNoncurrent"})
    _, _, _, debt, *_ = _balance(p)
    assert p["metrics"]["current_portion_long_term_debt"] == 0 and debt == 5087
    note = p["sources"]["current_portion_long_term_debt"]["basis"]
    assert "us-gaap:LongTermDebt of USD 5,087 million equals noncurrent debt" in note and "balance sheet ties" in note


def test_meta_annual_long_term_debt_total_equals_noncurrent():
    # Meta 10-K, 2025-12-31: LongTermDebt 58,744 = LongTermDebtNoncurrent 58,744.
    p = _balanced("2025-12-31", {"long_term_debt": 58744, "long_term_debt_including_current": 58744,
                                 "finance_lease_current": 308, "finance_lease_noncurrent": 876},
                  {"long_term_debt": "LongTermDebtNoncurrent"})
    _, _, _, debt, *_ = _balance(p)
    assert p["metrics"]["current_portion_long_term_debt"] == 0
    assert debt == pytest.approx(58744 + 308 + 876)


def test_nflx_notes_payable_equals_noncurrent_plus_short_term_debt():
    # Netflix 10-K, 2025-12-31: NotesPayable 14,463.0 = 13,463.971 noncurrent + 998.865 short-term.
    p = _balanced("2025-12-31", {"long_term_debt": 13463.971, "short_term_borrowings": 998.865,
                                 "notes_payable_total": 14463.0},
                  {"long_term_debt": "LongTermDebtNoncurrent"})
    _, _, _, debt, *_ = _balance(p)
    assert p["metrics"]["current_portion_long_term_debt"] == 0
    assert debt == pytest.approx(14462.836)
    assert "plus USD 999 million of separately filed short-term borrowings" in p["operating_bridge_notes"][0]


@pytest.mark.parametrize("change", [
    {"long_term_debt_including_current": 5300},       # Total exceeds noncurrent: a current portion exists.
    {"total_liabilities": 650},                       # Balance sheet does not tie.
    {"current_debt": 10},                             # A current-debt line is filed.
])
def test_zero_current_portion_needs_a_tie_and_no_filed_current_line(change):
    p = _balanced("2026-09-30", {"long_term_debt": 5087, "long_term_debt_including_current": 5087, **change},
                  {"long_term_debt": "LongTermDebtNoncurrent"})
    assume_absent_balances(p)
    assert p["metrics"].get("current_portion_long_term_debt") is None


def test_zero_current_portion_is_not_derived_from_the_same_tag():
    # A noncurrent value that itself came from LongTermDebt cannot prove a zero current portion.
    p = _balanced("2026-09-30", {"long_term_debt": 5087, "long_term_debt_including_current": 5087},
                  {"long_term_debt": "LongTermDebt"})
    assume_absent_balances(p)
    assert p["metrics"].get("current_portion_long_term_debt") is None


# --- Caterpillar: debt tagged only by segment ---------------------------------------------

AXIS = "srt:ProductOrServiceAxis"
FP, MET = "{http://www.cat.com/20260930}FinancialProductsMember", "{http://www.cat.com/20260930}MachineryPowerEnergyMember"


def _cat_instance(current_liabilities=40666):
    def seg(concept, end, member, value):
        return fact(concept, end, value, ((AXIS, member),))
    q, y = "2026-09-30", "2025-12-31"
    return instance(
        seg("LongTermDebtAndCapitalLeaseObligationsCurrent", q, FP, 8144),
        seg("LongTermDebtAndCapitalLeaseObligationsCurrent", q, MET, 36),
        seg("LongTermDebtAndCapitalLeaseObligations", q, FP, 21552),
        seg("LongTermDebtAndCapitalLeaseObligations", q, MET, 10614),
        seg("LongTermDebtAndCapitalLeaseObligations", y, FP, 20018),
        seg("LongTermDebtAndCapitalLeaseObligations", y, MET, 10678),
        seg("ShortTermBorrowings", q, FP, 4855),
        fact("AccountsPayableCurrent", q, 10573), fact("AccruedLiabilitiesCurrent", q, 6038),
        fact("ContractWithCustomerLiabilityCurrent", q, 5329), fact("DividendsPayableCurrent", q, 0),
        fact("EmployeeRelatedLiabilitiesCurrent", q, 2663), fact("OtherLiabilitiesCurrent", q, 3028),
        fact("LiabilitiesCurrent", q, current_liabilities))


def test_cat_interim_debt_is_the_validated_sum_of_its_two_segments():
    annual = period("2025-12-31", {"long_term_debt": 30696}, form="10-K")
    interim = period("2026-09-30", {"current_liabilities": 40666})
    notes = recover_segment_debt(interim, _cat_instance(), annual)
    m = interim["metrics"]
    assert m["current_portion_long_term_debt_and_finance_leases"] == 8180 * M
    assert m["long_term_debt_and_finance_leases"] == 32166 * M
    assert m["short_term_borrowings"] == 4855 * M
    assert "consolidated noncurrent total of USD 30,696 million at 2025-12-31" in notes[0]
    assert interim["sources"]["long_term_debt_and_finance_leases"]["members"] == {FP: 21552 * M, MET: 10614 * M}


def test_cat_segment_debt_fails_closed_when_current_liabilities_do_not_tie():
    annual = period("2025-12-31", {"long_term_debt": 30696}, form="10-K")
    interim = period("2026-09-30", {})
    assert recover_segment_debt(interim, _cat_instance(current_liabilities=41000), annual) == []
    assert interim["metrics"].get("current_portion_long_term_debt_and_finance_leases") is None


def test_cat_segment_debt_needs_a_consolidated_total_for_the_members():
    annual = period("2025-12-31", {"long_term_debt": 31000}, form="10-K")    # Members sum to 30,696.
    interim = period("2026-09-30", {})
    assert recover_segment_debt(interim, _cat_instance(), annual) == []


# --- Bridge-level fixtures ------------------------------------------------------------------

class _Client:
    def get_text(self, url):
        return "<html><body><p>No relevant text.</p></body></html>"


def _packet(ticker, annual, interim=()):
    filings = {"annual": [annual["filing"]], "interim": [p["filing"] for p in interim]}
    return {"issuer": {"ticker": ticker, "cik": "0000000001", "name": ticker}, "as_of": "2026-11-19",
            "filings": filings, "actuals": {"periods": [annual]}, "interim_actuals": {"periods": list(interim)}}


def _run_bridge(monkeypatch, packet, instances):
    monkeypatch.setattr(bridge, "load_filing_instance", lambda client, cik, accession: instances[accession])
    monkeypatch.setattr(bridge, "reconciled_current_cash", lambda *args: None)
    bridge.enrich_operating_bridge(packet, _Client())
    return packet


def test_apd_noncurrent_debt_sums_related_and_nonrelated_party_lines(monkeypatch):
    # Air Products 10-K, 2026-09-30: 16,607.7 nonrelated + 172.4 related; current 921.7; short-term 77.0.
    end = "2026-09-30"
    annual = period(end, {"cash_and_equivalents": 1331.7, "short_term_borrowings": 77.0,
                          "current_portion_long_term_debt_and_finance_leases": 921.7, "total_assets": 41209.8,
                          "total_liabilities": 24059.0, "total_equity": 17150.8, "current_assets": 5016.4,
                          "current_liabilities": 4708.9, "short_term_investments": 0, "net_ppe": 1, "ending_shares": 222,
                          "finance_lease_current": 0, "finance_lease_noncurrent": 0},
                    form="10-K", start="2025-10-01", accession="0000002969-26-000050")
    rp = "us-gaap:RelatedPartyTransactionsByRelatedPartyAxis"
    filed = instance(fact("LongTermDebtAndCapitalLeaseObligations", end, 16607.7, ((rp, "us-gaap:NonrelatedPartyMember"),)),
                     fact("LongTermDebtAndCapitalLeaseObligations", end, 172.4, ((rp, "us-gaap:RelatedPartyMember"),)),
                     fact("LongTermDebtAndCapitalLeaseObligationsCurrent", end, 921.7))
    packet = _run_bridge(monkeypatch, _packet("APD", annual), {"0000002969-26-000050": filed})
    m = packet["actuals"]["periods"][0]["metrics"]
    assert m["long_term_debt_and_finance_leases"] == pytest.approx(16780.1 * M)
    assert _balance(copy.deepcopy(packet["actuals"]["periods"][0]))[3] == pytest.approx(77.0 + 921.7 + 16780.1)
    assert any("related-party (USD 172.4 million)" in n for n in packet["actuals"]["periods"][0]["operating_bridge_notes"])


def test_related_party_debt_needs_exactly_both_members():
    rp = "us-gaap:RelatedPartyTransactionsByRelatedPartyAxis"
    one = [fact("LongTermDebtAndCapitalLeaseObligations", "2026-09-30", 172.4, ((rp, "us-gaap:RelatedPartyMember"),))]
    assert related_party_debt(one, "us-gaap:LongTermDebtAndCapitalLeaseObligations") is None


@pytest.mark.parametrize("ticker,current,noncurrent", [("CMCSA", 5958, 92979), ("UPS", 608, 23519)])
def test_cmcsa_and_ups_noncurrent_debt_uses_the_filed_debt_and_lease_line(monkeypatch, ticker, current, noncurrent):
    # Comcast and UPS 10-Ks, 2025-12-31: DebtCurrent with LongTermDebtAndCapitalLeaseObligations on the face.
    end = "2025-12-31"
    annual = period(end, {"cash_and_equivalents": 5000, "short_term_investments": 0, "current_debt": current,
                          "long_term_debt_and_lease_obligations": noncurrent, "total_assets": 200000,
                          "total_liabilities": 120000, "total_equity": 80000, "current_assets": 20000,
                          "current_liabilities": 15000, "net_ppe": 1, "ending_shares": 100},
                    form="10-K", start="2025-01-01", accession="0000000001-26-000002")
    filed = instance(fact("DebtCurrent", end, current))
    packet = _run_bridge(monkeypatch, _packet(ticker, annual), {"0000000001-26-000002": filed})
    p = packet["actuals"]["periods"][0]
    assert p["metrics"]["long_term_debt"] == noncurrent * M and p["reported_debt_basis"] is True
    assert _balance(copy.deepcopy(p))[3] == pytest.approx(current + noncurrent)
    assert any("us-gaap:LongTermDebtAndCapitalLeaseObligations line" in n for n in p["operating_bridge_notes"])


def _cash_case(monkeypatch, ticker, aggregate, opening, change, extra=(), metrics=None):
    """Annual cash comes from the 10-K's own cash-flow reconciliation, not the later 10-Q."""
    end, start = "2025-12-31", "2025-01-01"
    annual = period(end, {"cash_and_equivalents": None, "total_assets": 1000, "total_liabilities": 600,
                          "total_equity": 400, "current_assets": 300, "current_liabilities": 200,
                          "long_term_debt": 100, "current_debt": 10, "net_ppe": 1, "ending_shares": 10,
                          "finance_lease_current": 0, "finance_lease_noncurrent": 0, **(metrics or {})},
                    form="10-K", start=start, accession="0000000001-26-000010")
    interim = period("2026-09-30", {"cash_and_equivalents": 1, "short_term_investments": 0, "current_debt": 10,
                                    "long_term_debt": 100, "net_ppe": 1, "ending_shares": 10,
                                    "finance_lease_current": 0, "finance_lease_noncurrent": 0},
                     accession="0000000001-26-000020")
    total = "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"
    ten_k = instance(fact(total, end, aggregate), fact(total, "2024-12-31", opening),
                     fact(f"{total}PeriodIncreaseDecreaseIncludingExchangeRateEffect", end, change, start=start), *extra)
    ten_q = instance(fact(total, end, aggregate), fact("Assets", "2026-09-30", 1))     # No full-year cash flow.
    packet = _run_bridge(monkeypatch, _packet(ticker, annual, [interim]),
                         {"0000000001-26-000010": ten_k, "0000000001-26-000020": ten_q})
    return packet["actuals"]["periods"][0]


@pytest.mark.parametrize("ticker,aggregate,opening", [("PG", 9942, 10019), ("CSX", 670, 933)])
def test_pg_and_csx_annual_cash_reconciles_from_the_10k_cash_flow(monkeypatch, ticker, aggregate, opening):
    p = _cash_case(monkeypatch, ticker, aggregate, opening, aggregate - opening)
    assert p["metrics"]["cash_and_equivalents"] == aggregate * M
    assert p["sources"]["cash_and_equivalents"]["url"].endswith("x.xml")
    assert any(n.startswith("Cash proxy from us-gaap:CashCashEquivalentsRestrictedCash") for n in p["operating_bridge_notes"])


def test_annual_cash_proxy_still_needs_the_cash_flow_tie(monkeypatch):
    p = _cash_case(monkeypatch, "PG", 9942, 10019, 50)
    assert p["metrics"]["cash_and_equivalents"] is None


def test_tgt_short_term_investments_inside_cash_are_not_added_again(monkeypatch):
    # Target 10-K, 2026-01-31: cash 5,488 = cash, cash equivalents and short-term investments 5,488;
    # ShortTermInvestments 4,611 is a component of cash equivalents.
    p = _cash_case(monkeypatch, "TGT", 5488, 4762, 726,
                   extra=(fact("CashCashEquivalentsAndShortTermInvestments", "2025-12-31", 5488),),
                   metrics={"short_term_investments": 4611})
    assert p["metrics"]["cash_and_equivalents"] == 5488 * M
    assert p["metrics"]["short_term_investments"] == 0
    assert any("USD 4,611 million are inside the cash line" in n for n in p["operating_bridge_notes"])


# --- HON and FDX: separation after the 10-K -----------------------------------------------

def _separation_instance(year_end, current, noncurrent, after=0, spin=True):
    facts = [fact("AssetsOfDisposalGroupIncludingDiscontinuedOperationCurrent", year_end, current),
             fact("DisposalGroupIncludingDiscontinuedOperationAssetsNoncurrent", year_end, noncurrent),
             fact("AssetsOfDisposalGroupIncludingDiscontinuedOperationCurrent", "2026-09-30", after),
             fact("DisposalGroupIncludingDiscontinuedOperationAssetsNoncurrent", "2026-09-30", 0)]
    if spin:
        facts.append(fact("{http://honeywell.com/20260930}PreSeparationFunding", "2026-09-30", 15835, start="2026-01-01"))
    return instance(*facts)


def test_hon_aerospace_spin_off_stops_with_a_pro_forma_basis_reason():
    # Honeywell 10-Q, 2026-09-30: 8,232 + 11,612 of disposal-group assets at 2025-12-31; total assets 73,681.
    annual = period("2025-12-31", {"total_assets": 73681}, form="10-K")
    interim = period("2026-09-30", {})
    with pytest.raises(ResearchGap, match=r"Pro forma basis required: HON's 10-Q filed 2026-10-30 reports a spin-off\. "
                                          r"Discontinued operations held USD 19,844 million of assets at 2025-12-31 "
                                          r"\(27% of total assets\) and were separated by 2026-09-30"):
        separation_basis_gap(annual, interim, _separation_instance("2025-12-31", 8232, 11612), "HON")


def test_fdx_freight_separation_stops_and_small_disposals_do_not(monkeypatch):
    # FedEx 10-Q, 2026-09-30: 1,488 + 5,407 at 2026-05-31; total assets 98,937 (7%).
    annual = period("2026-05-31", {"total_assets": 98937}, form="10-K")
    interim = period("2026-09-30", {})
    with pytest.raises(ResearchGap, match=r"USD 6,895 million of assets at 2026-05-31 \(7% of total assets\)"):
        separation_basis_gap(annual, interim, _separation_instance("2026-05-31", 1488, 5407, spin=False))
    # A 2% disposal group does not change the basis.
    separation_basis_gap(annual, interim, _separation_instance("2026-05-31", 1000, 979))
    # The bridge applies the check before any balance recovery.
    packet = _packet("HON", period("2025-12-31", {"total_assets": 73681}, form="10-K", accession="0000773840-26-000010"),
                     [period("2026-09-30", {}, accession="0000773840-26-000143")])
    with pytest.raises(ResearchGap, match="Pro forma basis required"):
        _run_bridge(monkeypatch, packet, {"0000773840-26-000143": _separation_instance("2025-12-31", 8232, 11612)})


# --- PEP, JNJ, VZ: guidance quotes match in the workbook and the PDF -----------------------

@pytest.mark.parametrize("raw,clean", [
    ("•Expects organic revenue, core constant currency and core EPS growth to accelerate.",
     "Expects organic revenue, core constant currency and core EPS growth to accelerate."),            # PEP
    ("• Strong operational performance results in the Company’s raised guidance.",
     "Strong operational performance results in the Company’s raised guidance."),                   # JNJ
    ("\x7fRaised full-year 2026 guidance\x07 for key metrics​ for the year.",
     "Raised full-year 2026 guidance for key metrics for the year."),                                   # VZ
])
def test_guidance_quotes_lose_control_characters_and_list_markers(raw, clean):
    assert clean_passage(raw) == clean


def test_cleaned_quote_round_trips_through_the_workbook_and_pdf(tmp_path):
    from pypdf import PdfReader
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate
    from equity_research.operating_report import _normalize
    from equity_research.model_template.report_inputs import clean_passage as workbook_clean

    raw = "•Expects organic revenue, core constant currency and core earnings per share (EPS) growth."
    text = workbook_clean(raw)
    path = tmp_path / "quote.pdf"
    SimpleDocTemplate(str(path)).build([Paragraph(text, getSampleStyleSheet()["BodyText"])])
    extracted = _normalize(" ".join(page.extract_text() for page in PdfReader(path).pages))
    assert extracted == _normalize(text)
    assert all(ord(char) >= 32 for char in extracted)

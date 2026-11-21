"""Stage 1b: debt-cost chain, peer crash, provider retry, missing facts and the 10-Q financing start."""

from copy import deepcopy
from datetime import date
import io
import json
from shutil import which

import pytest

from equity_research import market_valuation as mv
from equity_research import operating_research as op
from equity_research.filing_xbrl import FilingInstance, XbrlFact
from equity_research.operating_research import ResearchGap

from test_market_valuation import fixture_model


def annual(end, **metrics):
    return {"period_end": end, "filing": {"url": f"https://www.sec.gov/{end}.htm"},
            "metrics": {key: value * 1e6 for key, value in metrics.items()}}


def unreconciled(monkeypatch):
    def balance(row):
        raise ResearchGap("Required financial fact is missing: cash and equivalents.")
    monkeypatch.setattr(op, "_balance", balance)


def test_filed_debt_tags_give_a_rate_when_the_funded_debt_bridge_fails(monkeypatch):
    # PG-shaped: interest and debt are filed, but the full balance bridge lacks cash.
    unreconciled(monkeypatch)
    packet = {"actuals": {"periods": [
        annual("2025-06-30", interest_expense=907, current_debt=9513, long_term_debt=24995),
        annual("2026-06-30", interest_expense=877, current_debt=11296, long_term_debt=22842)]}}
    debt = mv.debt_cost(packet, .04)
    assert debt["value"] == pytest.approx(877 / ((9513 + 24995 + 11296 + 22842) / 2))
    assert debt["debt_tag"] == "DebtCurrent + LongTermDebtNoncurrent"
    assert debt["basis"].startswith("Filed interest expense / average filed")


def test_filed_debt_uses_one_basis_and_falls_back_to_long_term_debt(monkeypatch):
    # META-shaped: no current debt line, LongTermDebt includes current maturities.
    unreconciled(monkeypatch)
    packet = {"actuals": {"periods": [
        annual("2024-12-31", interest_expense=683, long_term_debt_including_current=28826),
        annual("2025-12-31", interest_expense=1090, long_term_debt_including_current=58744)]}}
    debt = mv.debt_cost(packet, .04)
    assert debt["debt_tag"] == "LongTermDebt"
    assert debt["average_debt"] == pytest.approx((28826 + 58744) / 2)


def test_filed_debt_needs_positive_interest_and_a_plausible_rate(monkeypatch):
    unreconciled(monkeypatch)
    no_interest = {"actuals": {"periods": [annual("2025-12-31", long_term_debt_including_current=100)]}}
    with pytest.raises(ValueError, match="Neither filed interest"):
        mv.debt_cost(no_interest, .04)
    implausible = {"actuals": {"periods": [annual("2025-12-31", interest_expense=50,
                                                   long_term_debt_including_current=100)]}}
    with pytest.raises(ValueError):
        mv.debt_cost(implausible, .04)


def test_fred_requests_use_an_agent_fred_accepts(monkeypatch):
    seen = []

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake(request, timeout):
        seen.append(request.get_header("User-agent"))
        return Response(b"observation_date,BAMLC0A4CBBB\n2026-11-19,1.17\n")
    monkeypatch.setattr(mv, "urlopen", fake)
    assert mv._series("BAMLC0A4CBBB", date(2026, 11, 9), date(2026, 11, 19)) == {date(2026, 11, 19): 1.17}
    assert seen == [mv.FRED_AGENT] and seen[0].startswith("Python-urllib/")


def test_spread_retries_then_the_labeled_proxy_reaches_the_wacc_sheet_and_report(monkeypatch):
    calls = []

    def unavailable(url, attempts=3, timeout=30, agent=mv.AGENT):
        calls.append(url)
        raise TimeoutError("timed out")
    monkeypatch.setattr(mv, "_download", unavailable)
    with pytest.raises(OSError):
        mv._series("BAMLC0A4CBBB", date(2026, 11, 9), date(2026, 11, 19))
    assert len(calls) == 2  # CSV, then the FRED data table, each with its own retries.

    proxy = mv.proxy_debt_cost(.04, date(2026, 11, 19))
    assert proxy["value"] == pytest.approx(.055) and proxy["spread"] == .015
    assert proxy["basis"].startswith("Analyst proxy: BBB spread unavailable")
    model = fixture_model()
    model["valuation_market"]["debt"] = proxy
    from equity_research.model_template import workbook_plan
    plan = workbook_plan(model)
    assert plan["cells"]["WACC"]["B40"][0].startswith("Analyst proxy: BBB spread unavailable")
    assert plan["cells"]["WACC"]["B10"][0] == .015


@pytest.mark.skipif(not which("soffice"), reason="Requires LibreOffice")
def test_proxy_debt_basis_is_printed_in_the_verified_pdf(tmp_path):
    from equity_research.acceptance import check_pair
    from equity_research.model_template import export_operating
    from pypdf import PdfReader
    packet_model = fixture_model()
    data = json.loads((__import__("pathlib").Path(__file__).parent / "fixtures/operating.json").read_text())
    packet = data["packet"]
    packet.update(valuation_market={**packet_model["valuation_market"], "debt": mv.proxy_debt_cost(.04, date(2026, 6, 30))},
                  valuation_comps=packet_model["valuation_comps"])
    model = op.prepare_operating_model(packet, data["treasury"], data["quote"], data["selection"],
                                       op.revenue_candidates(packet))
    book, report = export_operating(model, tmp_path)
    summary = {"rating": model["valuation_summary"]["rating"], "spot_value_usd": model["fcfe"]["Base"].value_per_share}
    check_pair({"source_mode": "operating_research", "summary": summary}, book.read_bytes(), report.read_bytes())
    text = " ".join(page.extract_text() for page in PdfReader(report).pages)
    assert "Analyst proxy: BBB spread unavailable" in " ".join(text.split())


def test_peers_without_annual_periods_raise_value_errors_not_index_errors():
    # EOG and NUE: a foreign 20-F or 40-F peer has no U.S. GAAP annual periods. Driver collection
    # ran on every peer when the selection used a physical driver and raised a bare IndexError.
    from equity_research.drivers import collect
    from equity_research.operating_filing_bridge import enrich_operating_bridge
    foreign = {"issuer": {"ticker": "MT", "cik": "0001243429"}, "as_of": "2026-11-19",
               "actuals": {"periods": []}, "interim_actuals": {"periods": []}}
    with pytest.raises(ValueError, match="No annual U.S. GAAP 10-K period"):
        collect(foreign, client=None)
    with pytest.raises(ValueError, match="No annual U.S. GAAP 10-K period"):
        enrich_operating_bridge(foreign, client=None)


def _response(selection):
    body = {"status": "completed", "output": [{"type": "message", "content": [
        {"type": "output_text", "text": json.dumps(selection)}]}]}

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False
    return Response(json.dumps(body).encode())


def _selection(**changes):
    return {"group": "segment_revenue", "reason": "Segments reconcile.", "thesis": "Steady growth.",
            "risks": "Cyclical demand.", "driver_type": "segment_growth", "driver_passage": "none",
            "peer_candidates": [{"ticker": "LOW", "reason": "Home improvement."}], **changes}


def _provider(monkeypatch, responses):
    from equity_research import research_agent, spend_budget
    sent = []

    def fake(request, timeout):
        sent.append(json.loads(request.data))
        return _response(responses[len(sent) - 1])
    monkeypatch.setattr(research_agent, "urlopen", fake)
    monkeypatch.setattr(spend_budget, "reserve", lambda payload: None)
    packet = {"issuer": {"ticker": "APD", "sic": "2810"}, "driver_passages": {},
              "actuals": {"periods": [{"filing": {"url": "https://www.sec.gov/a.htm"}}]},
              "revenue_guidance": {}, "event_revenue_guidance": {}, "earnings": {"releases": []}}
    return research_agent, packet, sent


def test_provider_text_failure_is_retried_once(monkeypatch):
    research_agent, packet, sent = _provider(monkeypatch, [
        _selection(thesis="Line one.\nLine two."), _selection()])
    selection = research_agent.select_forecast(packet, {"segment_revenue": {}}, key="k", model="m")
    assert selection["thesis"] == "Steady growth." and len(sent) == 2
    assert "thesis text failed length or character checks" in sent[1]["instructions"]


def test_provider_text_failure_twice_stops_with_the_specific_reason(monkeypatch):
    research_agent, packet, sent = _provider(monkeypatch, [_selection(reason="x" * 451)] * 2)
    with pytest.raises(ResearchGap, match="reason text failed length or character checks"):
        research_agent.select_forecast(packet, {"segment_revenue": {}}, key="k", model="m")
    assert len(sent) == 2


def test_peer_retry_sends_the_valuation_eligibility_rules(monkeypatch):
    # HD: the retry must say which peers the acceptance comps can use.
    research_agent, packet, sent = _provider(monkeypatch, [_selection(), _selection()])
    research_agent.select_forecast(packet, {"segment_revenue": {}}, key="k", model="m",
                                   rejected_peers=["FND", "TSCO"], peer_rules=research_agent.PEER_RULES)
    evidence = json.loads(sent[0]["input"])
    assert evidence["unavailable_peers"] == ["FND", "TSCO"]
    rules = evidence["peer_eligibility_rules"]
    assert "first three digits" in rules and "labeled fallback" in rules and "0.1x and 10x" in rules
    # The first request carries the same rules, so proposals start from the 3-digit SIC rule.
    research_agent.select_forecast(packet, {"segment_revenue": {}}, key="k", model="m")
    assert json.loads(sent[-1]["input"])["peer_eligibility_rules"] == rules


def _instance(end, values):
    return FilingInstance("https://www.sec.gov/q.xml", tuple(
        XbrlFact(f"us-gaap:{concept}", None, date.fromisoformat(end), (), "USD", value * 1e6)
        for concept, amounts in values.items() for value in amounts))


def test_interim_balances_missing_from_company_facts_come_from_the_10q_instance():
    # DUK and SO: Company Facts has no interim balance sheet for the combined 10-Q.
    from equity_research.operating_evidence import recover_instance_balances
    period = {"period_end": "2026-09-30", "filing": {"accession": "q-1", "url": "https://www.sec.gov/q.htm"},
              "metrics": {"cash_and_equivalents": 400e6, "total_assets": None, "liabilities_and_equity": None,
                          "current_assets": None, "goodwill": None},
              "metric_issues": {"total_assets": "not_reported_in_eligible_interim_filings",
                                "liabilities_and_equity": "not_reported_in_eligible_interim_filings",
                                "current_assets": "not_reported_in_eligible_interim_filings",
                                "goodwill": "conflicting_values_in_latest_eligible_filing"}}
    notes = recover_instance_balances(period, _instance("2026-09-30", {
        "Assets": [195000], "LiabilitiesAndStockholdersEquity": [195000], "AssetsCurrent": [9000, 9100],
        "Goodwill": [19000], "CashAndCashEquivalentsAtCarryingValue": [999]}))
    metrics = period["metrics"]
    assert metrics["total_assets"] == metrics["liabilities_and_equity"] == 195000e6
    assert metrics["current_assets"] is None          # Conflicting instance values stay missing.
    assert metrics["goodwill"] is None                # A recorded conflict is never overwritten.
    assert metrics["cash_and_equivalents"] == 400e6   # Present values are kept.
    assert period["sources"]["total_assets"]["derivation"] == "filing_instance"
    assert "total_assets" not in period["metric_issues"]
    assert len(notes) == 1 and "10-Q XBRL instance" in notes[0]


def test_missing_short_term_investments_are_zero_when_the_balance_sheet_ties():
    from equity_research.operating_evidence import assume_absent_balances
    period = {"period_end": "2026-09-30", "filing": {"accession": "q-1", "url": "https://www.sec.gov/q.htm"},
              "metrics": {"total_assets": 195000e6, "liabilities_and_equity": 195000e6, "total_equity": 53000e6},
              "metric_issues": {"short_term_investments": "not_reported_in_eligible_interim_filings"}}
    untied = deepcopy(period)
    untied["metrics"]["liabilities_and_equity"] = 190000e6
    untied["metrics"]["total_liabilities"] = 130000e6
    assume_absent_balances(period)
    assume_absent_balances(untied)
    assert period["metrics"]["short_term_investments"] == 0
    assert period["sources"]["short_term_investments"]["kind"] == "analyst_assumption"
    assert any(note.startswith("Analyst assumption: short term investments is zero")
               for note in period["operating_bridge_notes"])
    assert "short_term_investments" not in untied["metrics"]


def _normalize(facts):
    """Run one annual period through the normalizer with the given duration tags."""
    from equity_research import sec_financials as sf
    filing = {"accession": "a-1", "form": "10-K", "filed_on": "2026-02-01", "url": "https://www.sec.gov/k.htm",
              "period_end": "2025-12-31"}
    start, end = date(2025, 1, 1), date(2025, 12, 31)
    observations = {}
    for metric, tags in sf._DURATION_METRICS.items():
        for tag in tags:
            if tag in facts:
                observations.setdefault((metric, end), {}).setdefault("a-1", {}).setdefault(tag, []).append(
                    (start, facts[tag] * 1e6))
    result = {"periods": [], "missing": [], "checks": []}
    sf._record_period(result, end, start, None, observations, {"a-1": filing}, [filing], sf._NOT_REPORTED)
    return result["periods"][0]


def test_operating_income_proxy_for_issuers_without_an_operating_subtotal():
    # JNJ 2025: pretax 32,581 + interest expense 971 - interest income 1,056 - other income 7,209.
    period = _normalize({"IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest": 32581,
                         "InterestExpenseNonoperating": 971, "InvestmentIncomeInterest": 1056,
                         "OtherNonoperatingIncomeExpense": 7209})
    assert period["metrics"]["operating_income"] == pytest.approx(25287e6)
    source = period["sources"]["operating_income"]
    assert source["derivation"] == "operating_income_proxy" and source["basis"].startswith("Analyst proxy")
    filed = _normalize({"OperatingIncomeLoss": 100, "InvestmentIncomeInterest": 5,
                        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest": 90,
                        "InterestExpense": 10, "OtherNonoperatingIncomeExpense": 5})
    assert filed["metrics"]["operating_income"] == 100e6 and "derivation" not in filed["sources"]["operating_income"]
    incomplete = _normalize({"IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest": 90,
                             "InterestExpense": 10})
    assert incomplete["metrics"]["operating_income"] is None


def test_depreciation_only_and_capex_tag_fallbacks():
    # GOOGL tags only Depreciation. VZ tags capex as PaymentsToAcquireOtherProductiveAssets.
    googl = _normalize({"Depreciation": 21136})
    assert googl["metrics"]["depreciation_amortization"] == 21136e6
    assert googl["sources"]["depreciation_amortization"]["derivation"] == "depreciation_only_proxy"
    vz = _normalize({"PaymentsToAcquireOtherProductiveAssets": 17011, "DepreciationAndAmortization": 18349})
    assert vz["metrics"]["capex"] == 17011e6 and vz["sources"]["capex"]["tag"] == "PaymentsToAcquireOtherProductiveAssets"
    assert vz["metrics"]["depreciation_amortization"] == 18349e6
    both = _normalize({"PaymentsToAcquirePropertyPlantAndEquipment": 900, "PaymentsToAcquireOtherProductiveAssets": 50})
    assert both["metrics"]["capex"] == 900e6


def _financing(annual_debt, interim_debt, annual_shares, interim_shares, repurchases=27332e6):
    latest = {"period_end": "2026-01-31"}
    interim = {"period_end": "2026-07-31", "filing": {"url": "https://www.sec.gov/q.htm"},
               "operating_bridge_source": "https://www.sec.gov/q.xml",
               "metrics": {"share_repurchases": repurchases},
               "sources": {"share_repurchases": {"url": "https://www.sec.gov/q.htm"}}}
    return op._financing_start(latest, interim, {"ending_shares": annual_shares}, annual_debt,
                               {"debt": interim_debt, "shares": interim_shares})


def test_material_post_year_end_financing_starts_the_forecast_at_the_10q():
    # CRM: debt 14,974 -> 39,288 and shares 929 -> 823 after a debt-funded buyback.
    start = _financing(14974, 39288, 929, 823)
    assert start["debt_change"] == pytest.approx(24314) and start["repurchases"] == pytest.approx(27332)
    assert "start from the 2026-07-31 10-Q" in start["note"]
    assert _financing(1000, 1050, 100, 95) is None                    # Both moves within 10%.
    assert _financing(1000, 1050, 100, 85)["debt_change"] == 50       # A share move alone triggers it.


@pytest.mark.skipif(not which("soffice"), reason="Requires LibreOffice")
def test_financing_start_keeps_the_statements_tied(tmp_path):
    from openpyxl import load_workbook
    from equity_research.acceptance import check_pair
    from equity_research.model_template import export_operating
    model = fixture_model()
    url = "https://www.sec.gov/q.htm"
    model["financing_start"] = {"debt_change": 25.0, "repurchases": 12.0, "repurchases_url": url,
                                "period_end": "2026-03-31", "url": url, "basis": "fixture"}
    from equity_research.valuation_summary import summarize
    op.attach_fcfe(model)
    model["valuation_summary"] = summarize(model, model["valuation_comps"])
    book, report = export_operating(model, tmp_path)
    summary = {"rating": model["valuation_summary"]["rating"], "spot_value_usd": model["fcfe"]["Base"].value_per_share}
    check_pair({"source_mode": "operating_research", "summary": summary}, book.read_bytes(), report.read_bytes())
    sheet = load_workbook(book, data_only=True)["Schedules"]
    assert sheet["B16"].value == pytest.approx(25) and sheet["B24"].value == pytest.approx(12)
    assert sheet["B19"].value == pytest.approx((sheet["B15"].value + 25) * load_workbook(book, data_only=True)["WACC"]["B6"].value)
    assert sheet["B26"].value == pytest.approx(model["shares"])

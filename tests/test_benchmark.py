import importlib.util
import json
from pathlib import Path

import pytest


@pytest.fixture
def benchmark():
    spec = importlib.util.spec_from_file_location("benchmark", Path(__file__).parents[1] / "scripts/benchmark.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fixed_universe(benchmark):
    from collections import Counter
    rows = benchmark.load_universe(benchmark.ROOT / "benchmarks/tickers.csv")
    assert len(rows) == 37
    sectors = Counter(row["sector"] for row in rows)
    assert sectors.pop("healthcare") == 2 and set(sectors.values()) == {5}
    assert {"utilities", "energy"}.isdisjoint(sectors)
    excluded = benchmark.load_universe(benchmark.ROOT / "benchmarks/excluded-tickers.csv")
    assert len(excluded) == 13 and not {r["ticker"] for r in excluded} & {r["ticker"] for r in rows}
    assert {"GD", "FAST", "SBUX", "NVDA"} <= {row["ticker"] for row in rows}


def test_stage_two_sector_review_excludes_legacy_and_failed_rows(benchmark, tmp_path):
    def row(symbol, sector, ratio, status="PASS", basis="stage2_fcfe"):
        return {"ticker": symbol, "sector": sector, "status": status, "stage": "verified_pair",
                "reason": "", "value_price_ratio": ratio, "valuation_basis": basis}
    records = [row("LO1","low",.5), row("LO2","low",.7), row("OLD","low",5,basis="legacy"),
               row("FAILED","low",5,status="FAIL"), row("HI","high",1.41), row("OK","in",1.4),
               row("MISSING","none",None)]
    report = tmp_path/"review.md"
    benchmark.write_results(report, records)
    text = report.read_text(encoding="utf-8")
    assert "| low | 2 | 0.6000 | Review |" in text
    assert "| high | 1 | 1.4100 | Review |" in text
    assert "| in | 1 | 1.4000 | Within range |" in text
    assert "| none | 0 |" in text


def test_failure_groups_preserve_missing_data_and_code_errors(benchmark):
    assert benchmark.failure_type("Required financial fact is missing: cash and equivalents") == "missing_balance_fact"
    assert benchmark.failure_type("'dimensions'") == "collection_code_error"
    assert benchmark.failure_type("Material acquisition needs full-year revenue") == "acquisition_or_merger_basis"


def test_collector_screening_result_returns_gap_without_missing_service_state(benchmark, tmp_path, monkeypatch):
    from datetime import date
    monkeypatch.setenv("SEC_USER_AGENT", "Test contact@example.com")
    from equity_research import sec_intake, web_api
    packet = {"issuer": {"cik": "0000034088", "ticker": "XOM", "sic": "2911"},
              "coverage": {"annual_statement_history": 0}}
    monkeypatch.setattr(sec_intake, "collect_ticker_evidence", lambda *args, **kwargs: packet)
    monkeypatch.setattr(web_api, "_screen_ticker", lambda *args, **kwargs: {
        "missing": "Valuation requires normalized annual financials from the issuer's reporting framework."})
    with pytest.raises(benchmark.ResearchGap, match="normalized annual financials"):
        benchmark.capture("XOM", date(2026, 11, 19), tmp_path)


def test_missing_cache_and_recorded_failure_are_not_passes(benchmark, tmp_path):
    row = {"ticker": "TEST", "sector": "synthetic"}
    missing = benchmark.run_one(row, tmp_path, None, False, tmp_path)
    assert missing["status"] == "FAIL"
    assert missing["stage"] == "cache"
    benchmark.cache_write(tmp_path / "TEST.json.gz", {
        "schema_version": 1, "ticker": "TEST", "cutoff": "2026-06-30",
        "stage": "collection", "error": "A verified balance is unavailable.",
    })
    failed = benchmark.run_one(row, tmp_path, None, False, tmp_path)
    assert failed["status"] == "FAIL"
    assert failed["reason"] == "A verified balance is unavailable."
    benchmark.write_results(tmp_path / "results.md", [missing, failed])
    assert "0/2 (0.0%)" in (tmp_path / "results.md").read_text()


def test_targeted_refresh_archives_failed_cache(benchmark, tmp_path, monkeypatch):
    path = tmp_path / "TEST.json.gz"
    benchmark.cache_write(path, {"schema_version": 1, "ticker": "TEST", "cutoff": "2026-06-30",
                               "stage": "collection", "error": "Old harness failure"})
    old = path.read_bytes()
    def fail_collection(*args):
        raise benchmark.ResearchGap("Actual filing gap")
    monkeypatch.setattr(benchmark, "capture", fail_collection)
    result = benchmark.run_one({"ticker": "TEST", "sector": "synthetic"}, tmp_path, None, True, tmp_path, True)
    assert result["reason"] == "Actual filing gap"
    assert list((tmp_path / ".history").glob("TEST-*.gz"))[0].read_bytes() == old


def test_export_failure_cannot_count_as_success(benchmark, tmp_path, monkeypatch):
    data = json.loads((Path(__file__).parent / "fixtures/operating.json").read_text())
    data["candidates"] = benchmark.revenue_candidates(data["packet"])
    data["comparables"] = "Peer evidence unavailable in synthetic test"
    benchmark.cache_write(tmp_path / "TEST.json.gz", {
        "schema_version": 1, "ticker": "TEST", "cutoff": "2026-06-30", "evidence": data,
    })
    def fail_export(*args):
        raise ValueError("Saved workbook failed validation")
    monkeypatch.setattr(benchmark, "export_operating", fail_export)
    outcome = benchmark.run_one({"ticker": "TEST", "sector": "synthetic"}, tmp_path, None, False, tmp_path)
    assert outcome["status"] == "FAIL"
    assert "Saved workbook failed validation" in outcome["reason"]
    model = benchmark.prepare_operating_model(data["packet"], data["treasury"], data["quote"], data["selection"],
                                              data["candidates"])
    # The benchmark ratio is the base FCFE value at the cost of equity over the close.
    assert outcome["value_price_ratio"] == pytest.approx(model["fcfe"]["Base"].value_per_share / model["close"])


def test_offline_real_export_and_pair_check(benchmark, tmp_path, monkeypatch):
    import shutil
    if not shutil.which("soffice"):
        pytest.skip("Real export requires LibreOffice, installed in CI")
    from test_market_valuation import fixture_data
    data = fixture_data()
    data["candidates"] = benchmark.revenue_candidates(data["packet"])
    data["comparables"] = {
        "basis": "Synthetic annual", "quote_date": "2026-06-30", "median": 6,
        "subject_eps": 5, "value": 30, "basis_note": "Synthetic comparison only",
        "subject_filings": ["https://example.org/synthetic/annual"], "excluded": [],
        "rows": [{"ticker": symbol, "end": "2025-12-31", "close": 30, "eps": 5, "multiple": 6,
                  "reason": "Synthetic peer", "filings": ["https://example.org/synthetic/peer"],
                  "quote_source": "https://example.org/synthetic/quote"} for symbol in ("ONE", "TWO")],
    }
    benchmark.cache_write(tmp_path / "TEST.json.gz", {
        "schema_version": 1, "ticker": "TEST", "cutoff": "2026-06-30", "evidence": data,
    })
    outcome = benchmark.run_one({"ticker": "TEST", "sector": "synthetic"}, tmp_path, None, False, tmp_path)
    assert outcome["status"] == "PASS", outcome["reason"]
    model = benchmark.prepare_operating_model(data["packet"], data["treasury"], data["quote"], data["selection"],
                                              data["candidates"])
    assert outcome["value"] == pytest.approx(model["fcfe"]["Base"].value_per_share)
    assert outcome["valuation_basis"] == "stage2_fcfe" and outcome["beta_basis"] == "regression"
    assert outcome["fcff_value_price_ratio"] == pytest.approx(model["scenarios"]["Base"].value_per_share / model["close"])

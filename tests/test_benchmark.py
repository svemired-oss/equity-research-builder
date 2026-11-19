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
    assert len(rows) == 50
    assert set(Counter(row["sector"] for row in rows).values()) == {5}
    assert {"GD", "FAST", "SBUX", "DHR", "NVDA"} <= {row["ticker"] for row in rows}


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


def test_export_failure_cannot_count_as_success(benchmark, tmp_path, monkeypatch):
    data = json.loads((Path(__file__).parent / "fixtures/operating.json").read_text())
    data["candidates"] = benchmark.revenue_candidates(data["packet"])
    data["comparables"] = "Peer evidence unavailable in synthetic test"
    benchmark.cache_write(tmp_path / "TEST.json.gz", {
        "schema_version": 1, "ticker": "TEST", "cutoff": "2026-06-30", "evidence": data,
    })
    def fail_export(*args):
        raise ValueError("Saved workbook failed validation")
    monkeypatch.setattr(benchmark, "export_apple", fail_export)
    outcome = benchmark.run_one({"ticker": "TEST", "sector": "synthetic"}, tmp_path, None, False, tmp_path)
    assert outcome["status"] == "FAIL"
    assert "Saved workbook failed validation" in outcome["reason"]
    assert outcome["value_price_ratio"] == pytest.approx(1.1175812324305436)


def test_offline_real_export_and_pair_check(benchmark, tmp_path, monkeypatch):
    import shutil
    if not shutil.which("soffice"):
        pytest.skip("Real export requires LibreOffice, installed in CI")
    data = json.loads((Path(__file__).parent / "fixtures/operating.json").read_text())
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
    assert outcome["value"] == pytest.approx(33.52743697291631)

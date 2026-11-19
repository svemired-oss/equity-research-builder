"""Replay cached generic U.S. research through valuation, Excel export and PDF checks."""

import argparse
import csv
from datetime import date, datetime, timezone
import gzip
from hashlib import sha256
import json
from pathlib import Path
import socket
import sys
from tempfile import TemporaryDirectory
from time import monotonic

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from equity_research.acceptance import check_pair
from equity_research.apple_export import export_apple
from equity_research.operating_research import (
    operating_rating, prepare_operating_model, revenue_candidates, verify_comparable_operating_basis, verify_valuation_range,
)
from equity_research.sec_client import configured_sec_contact
from equity_research.web_api import _ResearchService


def load_universe(path):
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    if not rows or len({row["ticker"] for row in rows}) != len(rows):
        raise ValueError("The benchmark needs distinct tickers.")
    if any(not row["ticker"].isalnum() or not row["sector"] for row in rows):
        raise ValueError("Invalid ticker or missing benchmark sector.")
    return rows


def capture(ticker, cutoff, work_dir):
    """Use the same collector as the web worker, including AI and verified peer selection."""
    service = object.__new__(_ResearchService)
    service.contact = configured_sec_contact()
    service.inputs_dir = work_dir / "inputs"
    inputs = service._collect_operating("benchmark", ticker, cutoff)
    if inputs is None:
        raise ValueError("The generic operating collector did not return a model input.")
    return dict(zip(("packet", "treasury", "quote", "selection", "candidates", "comparables"), inputs, strict=True))


def cache_write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(gzip.compress(json.dumps(value, default=str, allow_nan=False).encode(), mtime=0))
    temporary.replace(path)


def replay(ticker, evidence, folder):
    if evidence["packet"]["issuer"]["ticker"] != ticker:
        raise ValueError("Cache issuer differs from the requested ticker.")
    verify_comparable_operating_basis(evidence["packet"])
    candidates = revenue_candidates(evidence["packet"])
    if candidates != evidence["candidates"]:
        raise ValueError("Cached revenue candidates differ from the reconciled filing evidence.")
    model = prepare_operating_model(evidence["packet"], evidence["treasury"], evidence["quote"],
                                    evidence["selection"], candidates)
    review = verify_valuation_range(model)
    if review:
        model["notes"] += (review,)
    comps = evidence["comparables"]
    if isinstance(comps, str):
        model["notes"] += (comps,)
    else:
        model["comparables"] = comps
    ratio = model["scenarios"]["Base"].value_per_share / model["close"]
    workbook, report = export_apple(model, folder, None, None)
    run = {"ticker": ticker, "source_mode": "operating_research",
           "summary": {"spot_value_usd": model["scenarios"]["Base"].value_per_share,
                       "rating": operating_rating(ratio - 1), "reference_close_usd": model["close"]}}
    check_pair(run, workbook.read_bytes(), report.read_bytes())
    return {"value_price_ratio": ratio, "value": model["scenarios"]["Base"].value_per_share,
            "price": model["close"], "cutoff": str(model["as_of"]),
            "workbook_sha256": sha256(workbook.read_bytes()).hexdigest()}


def run_one(row, cache_dir, cutoff, collect, work_dir):
    started = monotonic()
    result = {**row, "status": "FAIL", "stage": "cache", "reason": "", "value_price_ratio": None}
    path = cache_dir / (row["ticker"] + ".json.gz")
    try:
        if collect and not path.exists():
            try:
                evidence = capture(row["ticker"], cutoff, work_dir)
                record = {"schema_version": 1, "ticker": row["ticker"], "cutoff": str(cutoff),
                          "captured_at": datetime.now(timezone.utc).isoformat(), "evidence": evidence}
            except Exception as exc:
                record = {"schema_version": 1, "ticker": row["ticker"], "cutoff": str(cutoff),
                          "stage": "collection", "error_type": type(exc).__name__, "error": str(exc)}
            cache_write(path, record)
        if not path.exists():
            raise ValueError("Cached evidence is missing. Collect this ticker before evaluating model coverage.")
        record = json.loads(gzip.decompress(path.read_bytes()))
        result["cache_sha256"] = sha256(path.read_bytes()).hexdigest()
        if record["ticker"] != row["ticker"] or record["schema_version"] != 1:
            raise ValueError("Cache identity or schema differs from this runner.")
        result["cutoff"] = record["cutoff"]
        if record.get("error"):
            result.update(stage=record["stage"], reason=record["error"])
        else:
            result["stage"] = "model_export_verification"
            with TemporaryDirectory(dir=work_dir) as directory:
                outcome = replay(row["ticker"], record["evidence"], Path(directory))
            result.update(outcome, status="PASS", stage="verified_pair")
    except Exception as exc:
        result["reason"] = f"{type(exc).__name__}: {exc}"
    result["seconds"] = round(monotonic() - started, 2)
    return result


def write_results(output, results):
    passed = sum(row["status"] == "PASS" for row in results)
    missing = sum(row["stage"] == "cache" for row in results)
    lines = ["# Generic operating model baseline", "",
             f"Completed {len(results)} tickers. Verified pairs {passed}/{len(results)} ({passed/len(results):.1%}).",
             f"Missing or invalid caches {missing}. These count as failures in the full-set rate.",
             "A complete baseline requires a recorded collection attempt for every ticker.",
             "PASS requires the existing valuation checks, real formula workbook export, and paired PDF verification.",
             "The rate measures mechanical completion. Economic assumptions and narrative still require analyst review.", "",
             "| Ticker | Sector | Status | Stage | Value / price | Reason |",
             "| --- | --- | --- | --- | --- | --- |"]
    for row in results:
        ratio = row["value_price_ratio"]
        reason = row["reason"].replace("|", "/").replace("\n", " ")
        lines.append(f"| {row['ticker']} | {row['sector']} | {row['status']} | {row['stage']} | "
                     f"{format(ratio, '.4f') if ratio is not None else '—'} | {reason} |")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    output.with_suffix(".json").write_text(json.dumps(results, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tickers", type=Path, default=ROOT / "benchmarks/tickers.csv")
    parser.add_argument("--cache", type=Path, default=ROOT / "private/benchmark-cache")
    parser.add_argument("--output", type=Path, default=ROOT / "benchmarks/results.md")
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    parser.add_argument("--collect", action="store_true", help="Collect uncached tickers using configured providers. Incurs API usage.")
    args = parser.parse_args()
    if args.collect:
        import os
        required = ("OPENAI_API_KEY", "APCA_API_KEY_ID", "APCA_API_SECRET_KEY")
        if not configured_sec_contact() or any(not os.environ.get(key, "").strip() for key in required):
            parser.error("Collection requires SEC_USER_AGENT and all three provider secrets on this machine.")
    else:
        def blocked_network(*args, **kwargs):
            raise RuntimeError("Offline benchmark attempted a network request.")
        socket.socket = blocked_network
    rows = load_universe(args.tickers)
    work = ROOT / "private/benchmark-work"
    work.mkdir(parents=True, exist_ok=True)
    results = []
    for row in rows:
        result = run_one(row, args.cache, args.as_of, args.collect, work)
        results.append(result)
        write_results(args.output, results)
        print(f"{row['ticker']} {result['status']} {result['reason']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

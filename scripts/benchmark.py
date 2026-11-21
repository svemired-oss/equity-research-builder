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
from traceback import extract_tb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from equity_research.acceptance import check_pair
from equity_research.apple_export import export_apple
from equity_research.model_template import export_operating
from equity_research.operating_research import (
    ResearchGap, operating_rating, prepare_operating_model, revenue_candidates,
    verify_comparable_operating_basis, verify_valuation_range,
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
    service = _ResearchService(work_dir, None, None, {"snapshots": []}, oracle=True)
    service.runs["benchmark"] = {"id": "benchmark", "ticker": ticker, "state": "running"}
    try:
        inputs = service._collect_operating("benchmark", ticker, cutoff)
        if inputs is None:
            outcome = service.runs["benchmark"]
            raise ResearchGap(outcome.get("error") or outcome.get("evidence", {}).get("missing")
                              or "The generic operating collector did not return a model input.")
        return dict(zip(("packet", "treasury", "quote", "selection", "candidates", "comparables"), inputs, strict=True))
    finally:
        service.close()


def cache_write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(gzip.compress(json.dumps(value, default=str, allow_nan=False).encode(), mtime=0))
    temporary.replace(path)


GATED_SECTOR_VALUES = 2  # docs/PLAN.md: single-value sectors are reported but not gated.


def replay(ticker, evidence, folder, result, *, legacy=False, artifacts=None):
    if evidence["packet"]["issuer"]["ticker"] != ticker:
        raise ValueError("Cache issuer differs from the requested ticker.")
    verify_comparable_operating_basis(evidence["packet"])
    candidates = revenue_candidates(evidence["packet"])
    if candidates != evidence["candidates"]:
        raise ValueError("Cached revenue candidates differ from the reconciled filing evidence.")
    selection = evidence["selection"] if not legacy else {**evidence["selection"], "driver_type": "segment_growth"}
    packet = evidence["packet"] if not legacy else {
        k:v for k,v in evidence["packet"].items() if k not in ("valuation_market","valuation_comps")}
    model = prepare_operating_model(packet, evidence["treasury"], evidence["quote"],
                                    selection, candidates)
    result["driver_type"] = model["driver"]["type"]
    result["driver_reason"] = model["driver"]["reason"]
    value = (model["scenarios"] if legacy else model["fcfe"])["Base"].value_per_share
    ratio = value / model["close"]
    result.update(value_price_ratio=ratio, value=value, price=model["close"], cutoff=str(model["as_of"]))
    rating = operating_rating(ratio - 1)
    if "valuation_summary" in model and not legacy:
        recommendation = model["valuation_summary"]
        rating = recommendation["rating"]
        selection = model.get("peer_selection") or {}
        comps = model.get("valuation_comps") or {}
        result.update(valuation_basis="stage2_fcfe", target_price=recommendation["target"], rating=rating,
                      fcff_value_price_ratio=recommendation["fcff"] / model["close"],
                      cost_of_equity=model["cost_of_equity"], beta=model["cost"].levered_beta,
                      beta_basis=model["beta"]["source"], peer_tier=selection.get("tier", "not recorded"),
                      peers=[row["ticker"] for row in selection.get("peers", ())],
                      comps_peers=len(comps.get("rows", ())),
                      peer_value_price_ratio=(recommendation["peer"] / model["close"]
                                              if recommendation["peer"] is not None else None))
    if "roic_evidence" in model and not legacy:
        result.update(terminal_roic=model["roic"], roic_basis=model["roic_evidence"]["basis"],
                      prior_roic_value_price_ratio=model["roic_comparison"]["value_per_share"] / model["close"])
    review = verify_valuation_range(model)
    if review:
        model["notes"] += (review,)
    comps = evidence["comparables"]
    if isinstance(comps, str):
        model["notes"] += (comps,)
    else:
        model["comparables"] = comps
    exporter = export_apple if legacy else export_operating
    workbook, report = exporter(model, folder, None, None)
    run = {"ticker": ticker, "source_mode": "operating_research",
           "summary": {"spot_value_usd": value,
                       "rating": rating, "reference_close_usd": model["close"]}}
    check_pair(run, workbook.read_bytes(), report.read_bytes())
    if artifacts is not None:
        from shutil import copyfile

        artifacts.mkdir(parents=True, exist_ok=True)
        for file in (workbook, report):
            copyfile(file, artifacts / file.name)
    return {"value_price_ratio": result["value_price_ratio"], "value": value,
            "price": model["close"], "cutoff": str(model["as_of"]),
            "price_date": str(model["price_date"]), "rating": rating,
            "workbook_sha256": sha256(workbook.read_bytes()).hexdigest(),
            "report_sha256": sha256(report.read_bytes()).hexdigest()}


def run_one(row, cache_dir, cutoff, collect, work_dir, refresh=False, *, legacy=False, artifacts=None):
    started = monotonic()
    result = {**row, "status": "FAIL", "stage": "cache", "reason": "", "value_price_ratio": None}
    path = cache_dir / (row["ticker"] + ".json.gz")
    try:
        if collect and (refresh or not path.exists()):
            try:
                evidence = capture(row["ticker"], cutoff, work_dir)
                record = {"schema_version": 1, "ticker": row["ticker"], "cutoff": str(cutoff),
                          "captured_at": datetime.now(timezone.utc).isoformat(), "evidence": evidence}
            except Exception as exc:
                record = {"schema_version": 1, "ticker": row["ticker"], "cutoff": str(cutoff),
                          "stage": "collection", "error_type": type(exc).__name__, "error": str(exc),
                          "error_origin": [f"{Path(frame.filename).name}:{frame.lineno} {frame.name}"
                                           for frame in extract_tb(exc.__traceback__)[-4:]]}
            if path.exists():
                old = path.read_bytes()
                history = cache_dir / ".history" / (row["ticker"] + "-" + sha256(old).hexdigest() + ".json.gz")
                history.parent.mkdir(parents=True, exist_ok=True)
                history.write_bytes(old)
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
                outcome = replay(row["ticker"], record["evidence"], Path(directory), result,
                                 legacy=legacy, artifacts=artifacts)
            result.update(outcome, status="PASS", stage="verified_pair")
    except Exception as exc:
        result["reason"] = f"{type(exc).__name__}: {exc}"
    if result["status"] == "FAIL":
        result["failure_type"] = failure_type(result["reason"])
    result["seconds"] = round(monotonic() - started, 2)
    return result


def failure_type(reason):
    text = reason.lower()
    rules = (
        ("cache", ("cached evidence", "cache identity")),
        ("acquisition_or_merger_basis", ("acquisition", "acquiree", "pro forma", "merger")),
        ("financing_bridge", ("net proceeds", "financing event", "debt issuance", "debt repayment", "share repurchase")),
        ("conflicting_filing_facts", ("conflicting",)),
        ("revenue_reconciliation", ("segment revenue", "revenue fact", "revenue grouping", "revenue reconciliation")),
        ("financial_history", ("normalized annual", "three normalized")),
        ("missing_balance_fact", ("required financial fact", "investment balance", "lease balance", "lease liability")),
        ("statement_reconciliation", ("balance sheet", "reconciliation", "cash aggregate")),
        ("valuation_guard", ("valuation outside", "standard dcf", "wacc", "terminal growth", "review reinvestment",
                             "cost of equity", "beta unavailable")),
        ("comparable_evidence", ("peer valuation", "comparables")),
        ("provider_or_market_data", ("openai", "alpaca", "treasury", "market close", "closing price")),
        ("collection_code_error", ("'dimensions'", "has no attribute")),
    )
    return next((label for label, phrases in rules if any(phrase in text for phrase in phrases)), "other")


def write_results(output, results):
    passed = sum(row["status"] == "PASS" for row in results)
    physical = sum(row["status"] == "PASS" and row.get("driver_type", "segment_growth") != "segment_growth" for row in results)
    missing = sum(row["stage"] == "cache" for row in results)
    lines = ["# Generic operating model benchmark", "",
             f"Completed {len(results)} tickers. Verified pairs {passed}/{len(results)} ({passed/len(results):.1%}).",
             f"Verified pairs using non-fallback drivers: {physical}/{passed}" + (f" ({physical/passed:.1%})." if passed else " (no verified pairs)."),
             f"Missing or invalid caches {missing}. These count as failures in the full-set rate.",
             "A complete baseline requires a recorded collection attempt for every ticker.",
             "PASS requires the valuation checks (base FCFE value / price between 0.5x and 2x), real formula workbook export, and paired PDF verification.",
             "The rate measures mechanical completion. Economic assumptions and narrative still require analyst review.", "",
             "## Remaining failures by type", "",
             "| Failure type | Count |", "| --- | --- |"]
    from collections import Counter
    counts = Counter(row.get("failure_type", failure_type(row["reason"])) for row in results if row["status"] != "PASS")
    lines.extend(f"| {label} | {count} |" for label, count in sorted(counts.items()))
    from statistics import median
    lines.extend(["", "## Sector valuation review", "",
                  "Median base FCFE value at the cost of equity / close among verified pairs.",
                  "Failed runs are excluded. A pass already requires value / price between 0.5x and 2x.", "",
                  "| Sector | Verified values | Median value / price | Status |",
                  "| --- | ---: | ---: | --- |"])
    in_range, without, single = [], [], []
    for sector in sorted({r["sector"] for r in results}):
        ratios = [r["value_price_ratio"] for r in results if r["sector"] == sector and r["status"] == "PASS"
                  and r.get("valuation_basis") == "stage2_fcfe" and r["value_price_ratio"] is not None]
        if ratios:
            center = median(ratios)
            inside = .7 <= center <= 1.4
            if len(ratios) >= GATED_SECTOR_VALUES:
                in_range.append(inside)
                flag = "Within range" if inside else "Review"
            else:
                single.append(sector)
                flag = f"Reported, not gated ({'inside' if inside else 'outside'} 0.7-1.4)"
            lines.append(f"| {sector} | {len(ratios)} | {center:.4f} | {flag} |")
        else:
            without.append(sector)
            lines.append(f"| {sector} | 0 | — | No evidence |")
    ratings = {name: sum(r["status"] == "PASS" and r.get("rating") == name for r in results)
               for name in ("Buy", "Hold", "Sell")}
    lines.extend(["", "## Stage 2 gate", "",
                  f"- Verified pairs: {passed}/{len(results)} (gate: 15 or more) — {'met' if passed >= 15 else 'not met'}.",
                  f"- Sectors with {GATED_SECTOR_VALUES} or more verified values inside 0.7-1.4: "
                  f"{sum(in_range)}/{len(in_range)} — {'met' if in_range and all(in_range) else 'not met'}.",
                  f"- Single-value sectors (reported, not gated): {', '.join(single) if single else 'none'}.",
                  f"- Sectors without any verified value: {', '.join(without) if without else 'none'}.",
                  f"- Ratings among verified pairs: Buy {ratings['Buy']}, Hold {ratings['Hold']}, Sell {ratings['Sell']}."])
    lines.extend(["", "## Valuation detail for verified pairs", "",
                  "| Ticker | FCFE value / price | At former 15% ROIC | Terminal ROIC | FCFF / price | Cost of equity | Beta basis | Peer tier | Peers | Comps peers | Rating |",
                  "| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | ---: | --- |"])
    for row in results:
        if row["status"] == "PASS" and row.get("valuation_basis") == "stage2_fcfe":
            number = lambda key, spec: format(row[key], spec) if row.get(key) is not None else "—"
            lines.append(f"| {row['ticker']} | {number('value_price_ratio', '.4f')} | {number('prior_roic_value_price_ratio', '.4f')} | "
                         f"{number('terminal_roic', '.1%')} | {number('fcff_value_price_ratio', '.4f')} | "
                         f"{number('cost_of_equity', '.2%')} | {row.get('beta_basis', '—')} | {row.get('peer_tier', '—')} | "
                         f"{' '.join(row.get('peers', ())) or 'none'} | {row.get('comps_peers', '—')} | {row.get('rating', '—')} |")
    lines.extend(["", "## Per-ticker outcomes", "",
             "| Ticker | Sector | Status | Driver | Stage | Value / price | Reason |",
             "| --- | --- | --- | --- | --- | --- | --- |"])
    for row in results:
        ratio = row["value_price_ratio"]
        reason = row["reason"].replace("|", "/").replace("\n", " ")
        lines.append(f"| {row['ticker']} | {row['sector']} | {row['status']} | {row.get('driver_type','Unverified')} | {row['stage']} | "
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
    parser.add_argument("--legacy-exporter", action="store_true", help="Replay with the Phase 1 workbook for comparison.")
    parser.add_argument("--artifacts", type=Path, help="Keep verified workbook and PDF pairs in this private directory.")
    parser.add_argument("--only", nargs="+", help="Replay only the listed symbols from the benchmark universe.")
    parser.add_argument("--refresh", nargs="+", default=[], help="Recollect only these tickers, archiving their old caches. Requires --collect.")
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
    if args.refresh and (not args.collect or set(args.refresh) - {row["ticker"] for row in rows}):
        parser.error("--refresh requires --collect and symbols in the benchmark universe.")
    if args.only:
        requested = set(args.only)
        if requested - {row["ticker"] for row in rows} or set(args.refresh) - requested:
            parser.error("--only must select benchmark symbols and include all --refresh symbols.")
        rows = [row for row in rows if row["ticker"] in requested]
    work = ROOT / "private/benchmark-work"
    work.mkdir(parents=True, exist_ok=True)
    results = []
    for row in rows:
        result = run_one(row, args.cache, args.as_of, args.collect, work, row["ticker"] in args.refresh,
                         legacy=args.legacy_exporter, artifacts=args.artifacts)
        results.append(result)
        write_results(args.output, results)
        print(f"{row['ticker']} {result['status']} {result['reason']}", flush=True)
    return 2 if any(row["stage"] == "cache" for row in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())

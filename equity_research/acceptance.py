"""Run the private ticker-to-download acceptance checks."""

import argparse
from datetime import date, timedelta
from hashlib import sha256
from io import BytesIO
import json
from math import isclose, isfinite
from pathlib import Path
import re
import time
from urllib.error import URLError
from urllib.request import Request, urlopen

from openpyxl import load_workbook
from pypdf import PdfReader


def _get(port, path, payload=None):
    if not path.startswith("/api/"):
        raise ValueError("Expected a local research API path.")
    data = json.dumps(payload).encode() if payload is not None else None
    request = Request(f"http://127.0.0.1:{port}{path}", data=data, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=15) as response:
        return response.read()


def check_pair(run, workbook, report):
    """Verify the downloaded files through an independent reader."""
    if run["source_mode"] != "operating_research":
        raise ValueError("The run did not use the reusable live workflow.")
    if run.get("evidence", {}).get("valuation_method") == "Bank distributions":
        return _check_bank_pair(run, workbook, report)
    if "spot_value_local" in run["summary"]:
        return _check_local_pair(run, workbook, report)
    if run.get("evidence", {}).get("reporting_currency"):
        return _check_foreign_pair(run, workbook, report)
    summary = run["summary"]
    formula_cells = (("Cover", "B7"), ("Income statement", "B4"), ("Balance sheet", "B4"),
                     ("Cash flow", "B12"), ("DCF", "D30"), ("Comps", "B14"))
    with BytesIO(workbook) as stream:
        formulas = load_workbook(stream, read_only=True, keep_links=False)
        try:
            for tab, cell in formula_cells:
                if tab not in formulas or formulas[tab][cell].data_type != "f":
                    raise ValueError(f"{tab}!{cell} is missing its forecast formula.")
        finally:
            formulas.close()
    with BytesIO(workbook) as stream:
        cached = load_workbook(stream, read_only=True, data_only=True, keep_links=False)
        try:
            for sheet in cached:
                if any(cell.data_type == "e" for row in sheet.iter_rows() for cell in row):
                    raise ValueError(f"Formula error found on {sheet.title}.")
            for col in "BCDEF":
                difference = cached["Checks"][f"{col}4"].value
                if type(difference) not in (int, float) or abs(difference) > 1e-5:
                    raise ValueError(f"Balance sheet failed reconciliation in forecast column {col}.")
            fair = cached["Cover"]["B7"].value
            peer = cached["Comps"]["B14"].value
            if not isinstance(peer, (int, float)) or not isfinite(peer) or peer <= 0:
                raise ValueError("A verified peer valuation is required for acceptance.")
            if cached["Cover"]["B9"].value != summary["rating"] or not isclose(fair, summary["spot_value_usd"], abs_tol=1e-6):
                raise ValueError("The workbook headline differs from the site.")
            if not isclose(cached["DCF"]["D30"].value, fair, abs_tol=1e-6):
                raise ValueError("The sensitivity grid does not reproduce the base DCF.")
        finally:
            cached.close()
    text = " ".join(" ".join(page.extract_text().split()) for page in PdfReader(BytesIO(report)).pages)
    fingerprint = sha256(workbook).hexdigest()
    if fingerprint not in "".join(text.split()) or f"DCF fair value ${fair:.2f}" not in text:
        raise ValueError("The PDF does not match the exact workbook or DCF.")
    if f"Peer-implied value ${peer:.2f}" not in text:
        raise ValueError("The PDF peer value differs from the workbook.")
    return {"dcf": fair, "peer_value": peer, "rating": summary["rating"]}


def _check_bank_pair(run, workbook, report):
    with BytesIO(workbook) as stream:
        formulas = load_workbook(stream, read_only=True, keep_links=False)
        try:
            for sheet, address in (("Cover", "B7"), ("Cover", "B8"), ("Cover", "B10"), ("Comps", "B10")):
                if formulas[sheet][address].data_type != "f":
                    raise ValueError(f"{sheet}!{address} is missing a bank valuation formula.")
        finally:
            formulas.close()
    with BytesIO(workbook) as stream:
        saved = load_workbook(stream, read_only=True, data_only=True, keep_links=False)
        try:
            cover, comp = saved["Cover"], saved["Comps"]
            fair, peer, close = (sheet[address].value for sheet, address in
                                 ((cover, "B10"), (comp, "B10"), (comp, "B11")))
            checks = [(cover["B7"].value, cover["B8"].value), (close, run["summary"]["reference_close_usd"]),
                      (fair, run["summary"]["spot_value_usd"])]
            checks.extend((saved["Checks"][f"{col}{row}"].value, 0) for col in "BCDEFG" for row in (4, 5, 6))
            if (any(cell.data_type == "e" for sheet in saved for row in sheet.iter_rows() for cell in row)
                    or any(not isclose(left, right, abs_tol=1e-6) for left, right in checks) or peer <= 0):
                raise ValueError("The bank model has an unreconciled valuation, funding or capital check.")
        finally:
            saved.close()
    text = " ".join(" ".join(page.extract_text().split()) for page in PdfReader(BytesIO(report)).pages)
    if (f"Distribution value ${fair:,.2f} per common share" not in text or
            f"Peer-implied value ${peer:.2f} per common share" not in text or
            sha256(workbook).hexdigest() not in "".join(text.split())):
        raise ValueError("The bank PDF does not reconcile to the workbook.")
    return {"dcf": fair, "peer_value": peer, "rating": run["summary"]["rating"], "method": "Bank distributions"}


def _check_local_pair(run, workbook, report):
    evidence, summary = run["evidence"], run["summary"]
    currency, quote = evidence["reporting_currency"], evidence["market_quote"]
    benchmark = evidence["yield_benchmark"]
    if currency != quote["currency"] or currency != benchmark["currency"] or run["price_date"] != quote["trading_date"]:
        raise ValueError("The local market quote, benchmark and workbook denomination do not agree.")
    with BytesIO(workbook) as stream:
        formulas = load_workbook(stream, read_only=True, keep_links=False)
        try:
            for sheet, cell in (("Cover", "B7"), ("Forecast", "B10"), ("DCF", "C13")):
                if formulas[sheet][cell].data_type != "f":
                    raise ValueError(f"{sheet}!{cell} is missing its forecast formula.")
        finally:
            formulas.close()
    with BytesIO(workbook) as stream:
        saved = load_workbook(stream, read_only=True, data_only=True, keep_links=False)
        try:
            if any(cell.data_type == "e" for sheet in saved for row in sheet.iter_rows() for cell in row):
                raise ValueError("The local workbook contains a formula error.")
            fair, close = (saved["Cover"][address].value for address in ("B7", "B8"))
            checks = ((fair, summary["spot_value_local"]), (close, quote["close"]), (close, summary["reference_close_local"]),
                      (fair, saved["DCF"]["C13"].value), (saved["Checks"]["B5"].value, 0))
            if any(not isclose(left, right, abs_tol=1e-6) for left, right in checks) or abs(saved["Checks"]["B4"].value) > 1:
                raise ValueError("The local workbook and reported valuation failed reconciliation.")
        finally:
            saved.close()
    text = " ".join(" ".join(page.extract_text().split()) for page in PdfReader(BytesIO(report)).pages)
    if f"DCF {currency} {fair:,.2f} per share" not in text or sha256(workbook).hexdigest() not in "".join(text.split()):
        raise ValueError("The PDF does not match the local workbook.")
    return {"dcf": fair, "value_per_ordinary_share": fair, "currency": currency, "rating": summary["rating"]}


def _check_foreign_pair(run, workbook, report):
    from .foreign_fx import listed_unit_value

    evidence, summary = run["evidence"], run["summary"]
    currency = evidence["reporting_currency"]
    if evidence["fx"]["date"] != run["price_date"] or not evidence["quote_fx_same_day"]:
        raise ValueError("Foreign acceptance requires a matched U.S. close and FX observation.")
    if evidence["yield_benchmark"].get("currency") != currency:
        raise ValueError("The benchmark and DCF use different currencies.")
    formulas = load_workbook(BytesIO(workbook), read_only=True, keep_links=False)
    try:
        required = (("Cover","B10"), ("Forecast","B4"), ("Forecast","G16"), ("Forecast","G32"),
                    ("Sensitivity","C5"), ("Comps","B12"))
        for sheet, address in required:
            if sheet not in formulas or formulas[sheet][address].data_type != "f":
                raise ValueError(f"{sheet}!{address} is missing its verified calculation.")
    finally:
        formulas.close()
    cached = load_workbook(BytesIO(workbook), read_only=True, data_only=True, keep_links=False)
    try:
        for sheet in cached:
            if any(cell.data_type == "e" for row in sheet.iter_rows() for cell in row):
                raise ValueError(f"Formula error found on {sheet.title}.")
        for col in "BCDEFG":
            for row in range(4, 8):
                amount = cached["Checks"][f"{col}{row}"].value
                if type(amount) not in (float, int) or not isclose(amount, 0, abs_tol=1e-6):
                    raise ValueError(f"Foreign statement reconciliation failed in {col}{row}.")
        cover, actuals = cached["Cover"], cached["Actuals"]
        local, fair, peer = (cover[address].value for address in ("B9", "B10", "B15"))
        converted = listed_unit_value(cover["B8"].value, actuals["B21"].value, evidence["listing"], evidence["fx"])
        checks = ((local, summary["value_per_ordinary_share"]), (local, cached["Sensitivity"]["C5"].value),
                  (fair, summary["spot_value_usd"]), (fair, converted), (peer, cached["Comps"]["B12"].value))
        if any(not isclose(left, right, abs_tol=1e-7) for left, right in checks):
            raise ValueError("The site, FX conversion, sensitivity and workbook headline did not reconcile.")
    finally:
        cached.close()
    text = " ".join(" ".join(page.extract_text().split()) for page in PdfReader(BytesIO(report)).pages)
    expected = (f"DCF {currency} {local:.2f} per ordinary share | USD {fair:.2f} per ADS",
                f"Peer-implied value USD {peer:.2f} per ADS")
    if sha256(workbook).hexdigest() not in "".join(text.split()) or any(label not in text for label in expected):
        raise ValueError("The foreign PDF does not match its workbook fingerprint or published values.")
    return {"dcf": fair, "peer_value": peer, "value_per_ordinary_share": local, "currency": currency}


def _download_run(port, request, cutoff, destination):
    run = json.loads(_get(port, "/api/runs", request))
    deadline = time.monotonic() + 300
    while run["state"] in ("queued", "running") and time.monotonic() < deadline:
        time.sleep(2)
        run = json.loads(_get(port, f"/api/runs/{run['id']}"))
    if run["state"] in ("queued", "running"):
        raise ValueError(f"Research run {run['id']} is still {run['state']} after five minutes.")
    if run["state"] != "completed" or not run.get("downloads"):
        raise ValueError(run.get("error") or run.get("evidence", {}).get("missing") or "No completed download pair.")
    if run["as_of"] != cutoff.isoformat() or not cutoff - timedelta(days=7) <= date.fromisoformat(run["price_date"]) <= cutoff:
        raise ValueError("The run does not have the requested cutoff and a recent market close.")
    files = []
    name = request["ticker"] + ("-guided" if request.get("mode") == "guided" else "")
    name = name.replace(":", "-")
    for kind, suffix in (("workbook", ".xlsx"), ("report", ".pdf")):
        entry = run["downloads"][kind]
        content = _get(port, entry["path"])
        if sha256(content).hexdigest() != entry["sha256"] or len(content) != entry["size_bytes"]:
            raise ValueError(f"The {kind} download failed its integrity check.")
        (destination / f"{name}-{cutoff}{suffix}").write_bytes(content)
        files.append(content)
    outcome = check_pair(run, *files)
    return run, files, {**outcome, "sector": run["company"]["sector"], "run_id": run["id"]}


def run_ticker(port, ticker, cutoff, destination, *, guided=False):
    run, files, outcome = _download_run(port, {"ticker": ticker}, cutoff, destination)
    if guided:
        local = "spot_value_local" in run["summary"]
        bank = outcome.get("method") == "Bank distributions"
        target = "cost_of_equity" if bank else "margin_shift" if outcome.get("currency") and not local else "margin"
        field = next((row for row in run["guided"]["fields"] if row["target"] == target), None)
        if field is None:
            raise ValueError("The run offers no supported guided assumption change.")
        default = field["default"]
        revised = default + .005 if default + .005 <= field["max"] else default - .005
        change = {"target": target, "value": revised, "reason": "Acceptance test of assumption recalculation."}
        request = {"ticker": ticker, "mode": "guided", "base_run_id": run["id"], "overrides": [change]}
        second, revised_files, result = _download_run(port, request, cutoff, destination)
        close_key = "reference_close_local" if local else "reference_close_usd"
        if second["price_date"] != run["price_date"] or second["summary"][close_key] != run["summary"][close_key]:
            raise ValueError("Guided generation changed the source market close.")
        direction = -1 if bank else 1
        if (result["dcf"] - outcome["dcf"]) * (revised - default) * direction <= 0:
            raise ValueError("Guided valuation did not move as expected for the changed assumption.")
        actuals = []
        for workbook, is_revised in ((files[0], False), (revised_files[0], True)):
            cached = load_workbook(BytesIO(workbook), read_only=True, data_only=True, keep_links=False)
            try:
                actuals.append(list(cached["Actuals"].values))
                cell = "B5" if bank else "B8" if outcome.get("currency") else "B3"
                if is_revised and not isclose(cached["Assumptions"][cell].value, revised, abs_tol=1e-10):
                    raise ValueError("The guided workbook does not use the revised margin.")
            finally:
                cached.close()
        peers_changed = "peer_value" in outcome and not isclose(result["peer_value"], outcome["peer_value"], abs_tol=1e-6)
        if actuals[0] != actuals[1] or peers_changed:
            raise ValueError("Guided generation changed historical data or the peer value.")
        field_name = "cost_of_equity" if bank else "margin"
        outcome["guided"] = {f"original_{field_name}": default, f"revised_{field_name}": revised,
                             "dcf": result["dcf"], "run_id": second["id"]}
    return outcome


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tickers", nargs="+", type=str.upper)
    parser.add_argument("--port", type=int, default=8053)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--guided", action="store_true", help="Also verify a supported assumption override for each ticker.")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--foreign", action="store_true", help="Verify foreign operating-company downloads and local currencies.")
    modes.add_argument("--banks", action="store_true", help="Verify bank distribution and peer valuations.")
    args = parser.parse_args()
    if args.banks:
        if len(set(args.tickers)) != len(args.tickers) or any(t not in ("BAC", "JPM", "WFC") for t in args.tickers):
            parser.error("Provide distinct reviewed bank symbols (BAC, JPM, WFC).")
    elif args.foreign:
        allowed = r"(?:(?:XLON:)?[A-Z][A-Z0-9]{0,7}|XTKS:[0-9][A-Z0-9]{3}|[0-9][A-Z0-9]{3}\.T)"
        if len(set(args.tickers)) != len(args.tickers) or any(not re.fullmatch(allowed, t) for t in args.tickers):
            parser.error("Provide distinct foreign operating-company symbols or qualified foreign listings.")
    elif (len(set(args.tickers)) != 3 or any(not re.fullmatch(r"[A-Z0-9]+(?:[.-][A-Z0-9]+)*", t)
                                           or len(t) > 12 for t in args.tickers)):
        parser.error("Provide three distinct U.S. stock ticker symbols.")
    catalog = json.loads(_get(args.port, "/api/companies"))
    exchange_only = args.foreign and set(args.tickers).issubset(catalog.get("exchange_generation", []))
    if not catalog.get("operating_generation") and not exchange_only:
        parser.error("The local server does not have live operating-company generation enabled.")
    reserved = {row["ticker"] for row in catalog["snapshots"]}
    reserved.update(catalog.get(key, {}).get("ticker") for key in ("generation", "apple_generation"))
    if reserved.intersection(args.tickers):
        parser.error("Choose tickers without an existing company-specific model.")
    args.output.mkdir(parents=True, exist_ok=True)
    cutoff = date.today()
    results = {}
    for ticker in args.tickers:
        try:
            results[ticker] = run_ticker(args.port, ticker, cutoff, args.output, guided=args.guided)
            if args.foreign and not results[ticker].get("currency"):
                raise ValueError("The foreign acceptance run did not use a local-currency model.")
            if args.banks and results[ticker].get("method") != "Bank distributions":
                raise ValueError("The acceptance run did not use the bank distributions model.")
        except (KeyError, TypeError, ValueError, URLError, OSError) as exc:
            results[ticker] = {"error": str(exc)}
        print(f"{ticker} {json.dumps(results[ticker])}", flush=True)
    (args.output / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    if any("error" in result for result in results.values()):
        raise SystemExit(1)
    if not (args.foreign or args.banks) and len({result["sector"] for result in results.values()}) < 3:
        raise SystemExit("The three runs did not cover three SEC industry classifications.")
    print("Mechanical checks passed. Review the company-specific drivers and sources before acceptance.")


if __name__ == "__main__":
    main()

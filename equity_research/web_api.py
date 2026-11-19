"""Serve dated portfolio research and verified guided runs over a local HTTP API."""

from concurrent.futures import ThreadPoolExecutor
from base64 import b64encode
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timezone
from functools import partial
from hashlib import sha256
from hmac import compare_digest
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from math import ceil, isfinite
import json
import logging
import mimetypes
from os import environ
from pathlib import Path
from shutil import which
import re
from tempfile import TemporaryDirectory
from threading import Event, Lock, Thread
from time import time
from urllib.error import URLError
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from .web_jobs import JobStore
from .sec_client import configured_sec_contact


_SUMMARY_FIELDS = ("reference_close_usd", "spot_value_usd", "target_price_usd", "total_return", "rating")
_MEDIA = {
    "workbook": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "report": "application/pdf",
}
_CASES = ("bear", "base", "bull")
_MAX_BODY = 16_384
_MAX_PENDING = 4
_RETENTION = 24 * 60 * 60
_RUN_LIMITS = ((3600, 8, "hour"), (_RETENTION, 20, "24 hours"))
_MAX_PAIR = 16 * 1024 * 1024
_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class _GuideField:
    target: str
    label: str
    unit: str
    min: float
    max: float
    step: str = "any"


_GUIDES = {
    "FDX": (
        _GuideField("us_ground.volume_growth", "Q4 U.S. Ground volume growth", "decimal, 0.05 = 5%", -0.15, 0.25),
        _GuideField("us_ground.yield_growth", "Q4 U.S. Ground yield growth", "decimal, 0.05 = 5%", -0.15, 0.25),
        _GuideField("us_priority.volume_growth", "Q4 U.S. Priority volume growth", "decimal, 0.05 = 5%", -0.15, 0.25),
        _GuideField("us_priority.yield_growth", "Q4 U.S. Priority yield growth", "decimal, 0.05 = 5%", -0.15, 0.25),
        _GuideField("corporate_other_and_eliminations.revenue_millions", "Q4 corporate and eliminations revenue", "USD millions", 200, 750),
    ),
    "NEM": (
        _GuideField("q4_2026.gold.benchmark_usd", "Q4 2026 gold price", "USD/oz", 3000, 5500),
        _GuideField("fy_2027.gold.benchmark_usd", "2027 gold price", "USD/oz", 3250, 5500),
        _GuideField("fy_2027.attributable_gold_growth", "2027 attributable gold production growth", "decimal, 0.05 = 5%", 0, 0.08),
        _GuideField("fy_2027.penasquito_coproduct_change", "2027 Peñasquito coproduct output change", "decimal, 0.05 = 5%", -0.2, 0.05),
        _GuideField("fy_2027.unit_cas_growth", "2027 site unit cost growth", "decimal, 0.05 = 5%", -0.05, 0.15),
    ),
}


class _APIError(Exception):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


def _defaults(ticker, as_of, inputs_dir):
    if ticker == "NEM":
        from .nem_current import run_current_nem

        return run_current_nem(as_of, inputs_dir)["assumptions"]
    from .fdx_current import run_current_fdx

    q4 = run_current_fdx(as_of, inputs_dir)["q4_revenue"]
    values = {f"{service}.{driver}": getattr(q4["services"][service], driver)
              for service in ("us_ground", "us_priority") for driver in ("volume_growth", "yield_growth")}
    values["corporate_other_and_eliminations.revenue_millions"] = q4["corporate_other_implied_by_annual_guidance_millions"]
    return values


def _workbook_cases(ticker, snapshot):
    cases = []
    for index, name in enumerate(_CASES):
        if ticker == "FDX":
            cells = [f"{col}{18 + index}" for col in "BCDE"]
        else:
            col = "BCD"[index]
            cells = [f"{col}{row}" for row in range(19, 23)]
        spot, target, total, rating = (snapshot.value("Cover", cell) for cell in cells)
        cases.append({"case": name, "spot_value_usd": spot, "target_price_usd": target,
                      "total_return": total, "rating": rating})
    return cases


def _generate(ticker, as_of, inputs_dir, node, node_modules, case, overrides, filenames, gd_template=None):
    if ticker == "GD":
        from .gd_fresh_excel import export_fresh_gd_workbook
        from .gd_fresh_pdf import export_fresh_gd_pdf
    elif ticker == "FDX":
        from .fdx_fresh_excel import export_fresh_fdx_workbook, load_verified_fdx_workbook
        from .fdx_fresh_pdf import export_fdx_fresh_pdf

        export_workbook, export_report, load_workbook = (
            export_fresh_fdx_workbook, export_fdx_fresh_pdf, load_verified_fdx_workbook,
        )
    else:
        from .nem_fresh_excel import export_fresh_nem_workbook, load_verified_nem_workbook
        from .nem_fresh_pdf import export_nem_fresh_pdf

        export_workbook, export_report, load_workbook = (
            export_fresh_nem_workbook, export_nem_fresh_pdf, load_verified_nem_workbook,
        )
    work_dir = inputs_dir.parent / "private" / "web-runs"
    work_dir.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="run-", dir=work_dir) as stage:
        directory = Path(stage)
        workbook, report = (directory / filenames[kind] for kind in _MEDIA)
        if ticker == "GD":
            from .web_catalog import _read_workbook, _report_pages, _SNAPSHOTS

            export_fresh_gd_workbook(gd_template, workbook, as_of, inputs_dir, node, node_modules, case=case)
            result = export_fresh_gd_pdf(gd_template, workbook, report, as_of, inputs_dir, case=case)
            contents = {"workbook": workbook.read_bytes(), "report": report.read_bytes()}
            if result.workbook_sha256 != sha256(contents["workbook"]).hexdigest():
                raise ValueError("The generated GD workbook and PDF do not match.")
            snapshot = next(spec for spec in _SNAPSHOTS if spec.ticker == "GD")
            pages = _report_pages(contents["report"], snapshot, result.workbook_sha256)
            values = _read_workbook(contents["workbook"], snapshot, pages)
            return {key: values[key] for key in _SUMMARY_FIELDS}, values["cases"], contents
        guided = None
        if overrides:
            guided = directory / "assumptions.json"
            guided.write_text(json.dumps({"overrides": overrides}, allow_nan=False), encoding="utf-8")
        export_workbook(workbook, as_of, inputs_dir, node, node_modules, case=case, guided=guided)
        pdf_result = export_report(workbook, report, as_of=as_of, inputs_dir=inputs_dir, case=case, guided=guided)
        snapshot = load_workbook(workbook, as_of, inputs_dir, case=case, guided=guided)
        contents = {"workbook": workbook.read_bytes(), "report": report.read_bytes()}
        if snapshot.sha256 != pdf_result["workbook_sha256"] or snapshot.sha256 != sha256(contents["workbook"]).hexdigest():
            raise ValueError("The downloaded report and workbook must share the verified workbook fingerprint.")
        summary = {
            "reference_close_usd": snapshot.number("Cover", "B4"),
            "spot_value_usd": snapshot.number("Cover", "B9"),
            "target_price_usd": snapshot.number("Cover", "B10"),
            "total_return": snapshot.number("Cover", "B11"),
            "rating": snapshot.value("Cover", "B12"),
        }
        cases = _workbook_cases(ticker, snapshot)
    return summary, cases, contents


def _screen_ticker(ticker, as_of, contact, *, packet=None):
    from .sec_client import SECClient
    from .sec_intake import collect_ticker_evidence

    if packet is None:
        packet = collect_ticker_evidence(SECClient(contact), ticker, as_of=as_of, years=5, statements_only=True)
    issuer = packet["issuer"]
    coverage = packet["coverage"]
    sic = str(issuer.get("sic") or "")
    if sic == "6798":
        criteria = "a REIT model with property NOI, recurring capital spending, and debt schedules"
    elif sic in {"6020", "6021", "6022", "6029", "6035", "6036", "6712"}:
        criteria = "a bank model with loan and deposit schedules, credit provisions, and holding-company capital requirements"
    elif sic in {"6331", "6351"}:
        criteria = "a property-and-casualty insurance model with premiums, claims, and statutory surplus"
    elif sic in {"6311", "6321", "6324"}:
        criteria = "an insurance model with product-specific liabilities and regulatory capital"
    elif sic.isdigit() and 6000 <= int(sic) <= 6799:
        criteria = "a financial-sector model with earning assets, funding costs, and regulatory capital"
    elif coverage["annual_statement_history"] == 0:
        criteria = "normalized annual financials from the issuer's reporting framework"
    else:
        criteria = "a reviewed operating forecast, supporting schedules, and valuation inputs"
    return {"company": issuer["name"], "industry": issuer.get("industry"),
            "annual_periods": coverage["annual_statement_history"],
            "annual_filings": coverage["annual_filing_links"],
            "missing": f"Valuation requires {criteria}."}


def _download_path(namespace, identity, kind):
    return f"/api/{namespace}/{identity}/downloads/{kind}"


class _ResearchService:
    def __init__(self, root, node, node_modules, catalog, *, oracle=False, oracle_evidence=None, oracle_dated_closes=False):
        from .web_catalog import read_demo_download

        self.inputs_dir = root / "inputs"
        self.node, self.node_modules = node, node_modules
        self.catalog = deepcopy(catalog)
        self.companies = {}
        self.assets = {}
        self.runs = {}
        self.operating_inputs = {}
        self.lock = Lock()
        self.submission_lock = Lock()
        self.reloading = False
        self.oracle_evidence = oracle_evidence
        self.oracle_dated_closes = oracle_dated_closes
        self.oracle = oracle
        self.hosted_name = environ.get("RENDER_EXTERNAL_HOSTNAME", "").strip()
        self.hosted_auth = None
        self.spend_limit = None
        if self.hosted_name:
            from .spend_budget import MODEL, daily_limit
            password = environ.get("RECRUITER_PASSWORD", "")
            if not re.fullmatch(r"[!-~]{16,128}",password):
                raise ValueError("Set a recruiter password of 16 to 128 printable ASCII characters without spaces.")
            if environ.get("RESEARCH_MODEL") != MODEL:
                raise ValueError(f"Hosted spend limits are priced for {MODEL}.")
            self.hosted_auth = b"Basic " + b64encode(("recruiter:"+password).encode("ascii"))
            self.spend_limit = daily_limit(environ.get("OPENAI_DAILY_CAP_USD"))
        self.contact = configured_sec_contact() if oracle else ""
        if oracle:
            self.catalog["ticker_screening"] = bool(self.contact)
            self.catalog["exchange_generation"] = ["XTKS:7974", "7974.T"] if self.contact else []
            self.catalog["operating_generation"] = bool(self.contact and all(
                environ.get(key, "").strip() for key in ("OPENAI_API_KEY", "APCA_API_KEY_ID", "APCA_API_SECRET_KEY")))
            if self.contact:
                from .apple_pilot import AS_OF

                self.catalog["apple_generation"] = {
                    "ticker": "AAPL", "company": "Apple Inc.", "sector": "Consumer electronics and services",
                    "as_of": AS_OF.isoformat(), "source_mode": "apple_dated_close",
                }
        if oracle:
            from .oracle_review_export import REVIEW_DATE

            source_mode = "live_collection"
            if oracle_evidence is not None:
                source_mode = "dated_evidence"
            elif oracle_dated_closes:
                source_mode = "live_filings_dated_closes"
            self.catalog["generation"] = {
                "ticker": "ORCL", "company": "Oracle Corporation", "sector": "Software and cloud infrastructure",
                "as_of": (date.today() if source_mode == "live_collection" else REVIEW_DATE).isoformat(),
                "source_mode": source_mode,
            }
        spreadsheet_available = bool(which("soffice"))
        for item in self.catalog["snapshots"]:
            ticker, date_text = item["ticker"], item["as_of"]
            self.companies[ticker] = item
            if oracle:
                item["regenerate"] = spreadsheet_available and ticker in ("GD","FDX","NEM")
            fields = _GUIDES.get(ticker, ())
            enabled = bool(fields and spreadsheet_available)
            defaults = _defaults(ticker, date.fromisoformat(date_text), self.inputs_dir) if enabled else {}
            item["guided"] = {
                "available": enabled,
                "fields": [{**vars(field), "default": defaults[field.target]} for field in fields] if enabled else [],
            }
            self.assets[ticker] = {}
            for kind in _MEDIA:
                artifact = item["downloads"][kind]
                filename = artifact["filename"]
                if not filename or Path(filename).name != filename or any(char in filename for char in ("/", "\\", ":", '"', "\r", "\n")):
                    raise ValueError("Invalid filename in the published catalog.")
                content = read_demo_download(root, ticker, kind=kind)
                if len(content) != artifact["size_bytes"] or sha256(content).hexdigest() != artifact["sha256"]:
                    raise ValueError("A published download has changed since the catalog was built.")
                self.assets[ticker][kind] = content
                artifact["path"] = _download_path("companies", ticker, kind)
        store_path = ":memory:"
        if self.catalog.get("live_only"):
            work_dir = Path(environ["RESEARCH_STATE_DIR"]) if self.hosted_name else root / "private" / "web-runs"
            work_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            store_path = work_dir / "research-jobs.sqlite3"
        self.jobs = JobStore(store_path)
        self._purge(time())
        self.runs = self.jobs.restore()
        for run_id, run in tuple(self.runs.items()):
            if run["state"] in ("queued", "running"):
                self._finish(run_id, {"state": "failed", "error":
                    "The builder restarted before this run finished. Start a new run."})
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="equity-research")

    def _validate(self, request):
        from .listing_directory import qualified_symbol

        if not isinstance(request, dict) or request.keys() - {"ticker", "mode", "case", "overrides", "base_run_id"}:
            raise _APIError(400, "Send ticker, mode, case and optional overrides as a JSON object.")
        raw_ticker = request.get("ticker")
        ticker = raw_ticker.strip().upper() if isinstance(raw_ticker, str) else ""
        mode, case = request.get("mode", "automated"), request.get("case", "base")
        if mode not in ("automated", "guided") or case not in _CASES:
            raise _APIError(400, "Choose automated or guided mode and a bear, base or bull case.")
        overrides = request.get("overrides", [])
        if not isinstance(overrides, list):
            raise _APIError(400, "Send overrides as a JSON array.")
        if "base_run_id" in request:
            base_id = request["base_run_id"]
            if mode != "guided" or case != "base" or not isinstance(base_id, str):
                raise _APIError(400, "Guided operating forecasts use a completed base run.")
            with self.lock:
                base_run = self.runs.get(base_id)
                inputs = self.operating_inputs.get(base_id)
                if not base_run or inputs is None or base_run["ticker"] != ticker or base_run["mode"] != "automated":
                    raise _APIError(400, "Start a new Automated run before using Guided mode. Its working evidence may have expired or the builder restarted.")
                item = {**base_run["company"], "as_of": base_run["as_of"],
                        "guided": base_run["guided"], "operating_inputs": inputs}
            return item, mode, case, self._validate_overrides(item, overrides)
        if self.oracle and ticker == "ORCL":
            if mode != "automated" or case != "base" or overrides:
                raise _APIError(400, "The Oracle pilot generates the Base case with editable inputs in Excel.")
            return self.catalog["generation"], mode, case, overrides
        item = self.companies.get(ticker)
        if item is None:
            recognized = qualified_symbol(ticker) or re.fullmatch(r"[A-Z0-9]+(?:[.-][A-Z0-9]+)*", ticker)
            if self.oracle and self.contact and recognized and len(ticker) <= 13:
                if mode != "automated" or case != "base" or overrides:
                    raise _APIError(400, "Ticker evidence checks use Automated mode.")
                return {"ticker": ticker}, mode, case, overrides
            message = "Enter ORCL, GD, FDX, or NEM. Other ticker checks require an SEC contact on the server."
            raise _APIError(400, message if self.oracle else "Choose a supported ticker from the catalog.")
        if mode == "automated":
            if overrides or case != item["case"]:
                raise _APIError(400, "The published automated pair uses the base case. Compare all cases in the catalog.")
            return item, mode, case, overrides
        if not item["guided"]["available"]:
            raise _APIError(400, "Guided workbook and report generation is unavailable for this ticker on this server.")
        return item, mode, case, self._validate_overrides(item, overrides)

    @staticmethod
    def _validate_overrides(item, overrides):
        fields = {row["target"]: row for row in item["guided"]["fields"]}
        if not 1 <= len(overrides) <= len(fields):
            raise _APIError(400, "Submit at least one supported guided change.")
        seen, cleaned = set(), []
        for row in overrides:
            if not isinstance(row, dict) or set(row) != {"target", "value", "reason"}:
                raise _APIError(400, "Each override needs a target, value and reason.")
            target, value, reason = row["target"], row["value"], row["reason"]
            field = fields.get(target) if isinstance(target, str) else None
            if field is None or target in seen:
                raise _APIError(400, "Use supported guided fields once each.")
            if type(value) not in (int, float) or not field["min"] <= value <= field["max"] or not isfinite(value):
                raise _APIError(400, f"{field['label']} must be between {field['min']:g} and {field['max']:g}. "
                                     f"{field.get('explanation', '')}".strip())
            if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 240 or not reason.isprintable():
                raise _APIError(400, "Give each change a one-line reason of 1 to 240 characters.")
            if reason.lstrip().startswith("="):
                raise _APIError(400, "Use plain text for the override reason.")
            seen.add(target)
            cleaned.append({"target": target, "value": value, "reason": reason.strip()})
        return cleaned

    def create_run(self, request):
        if not isinstance(request, dict):
            raise _APIError(400, "Send ticker, mode, case and optional overrides as a JSON object.")
        request = dict(request)
        token = request.pop("request_id", uuid4().hex)
        if not isinstance(token, str) or not re.fullmatch(r"[a-f0-9]{32}", token):
            raise _APIError(400, "Invalid research request ID.")
        normalized = {"mode": "automated", "case": "base", "overrides": [], **request}
        if isinstance(normalized.get("ticker"), str):
            normalized["ticker"] = normalized["ticker"].strip().upper()
        fingerprint = sha256(json.dumps(normalized, sort_keys=True).encode()).hexdigest()
        with self.submission_lock:
            with self.lock:
                self._purge(time())
                replay = self.jobs.replay(token)
                if replay:
                    previous, job_id = replay
                    if previous != fingerprint:
                        raise _APIError(409, "That request ID already belongs to a different submission.")
                    return HTTPStatus.OK, deepcopy(self.runs[job_id])
            return self._create_run(request, partial(self._queue, token, fingerprint))

    def _create_run(self, request, enqueue):
        from .listing_directory import qualified_symbol

        item, mode, case, overrides = self._validate(request)
        ticker = item["ticker"]
        if "operating_inputs" in item:
            inputs = item["operating_inputs"]
            if isinstance(inputs, dict) and "bank" in inputs:
                return enqueue({"ticker": ticker, "mode": mode, "case": case, "as_of": item["as_of"],
                                    "source_mode": "operating_research"}, self._finish_bank, inputs, overrides)
            if isinstance(inputs, dict) and "nintendo" in inputs:
                return enqueue({"ticker": ticker, "mode": mode, "case": case, "as_of": item["as_of"],
                                    "source_mode": "operating_research"}, self._finish_nintendo,
                                   inputs["nintendo"], inputs["listing"], overrides)
            if isinstance(inputs, dict) and "foreign_evidence" in inputs:
                return enqueue({"ticker": ticker, "mode": mode, "case": case, "as_of": item["as_of"],
                                    "source_mode": "operating_research"}, self._finish_foreign_guided,
                                   inputs["foreign_evidence"], overrides)
            return enqueue({"ticker": ticker, "mode": mode, "case": case, "as_of": item["as_of"],
                                "source_mode": "operating_research"}, self._finish_operating,
                               item["operating_inputs"], overrides)
        if qualified := qualified_symbol(ticker):
            return enqueue({"ticker": ticker, "mode": mode, "as_of": date.today().isoformat(),
                                "source_mode": "operating_research"}, self._finish_exchange, qualified)
        if ticker == "ORCL":
            as_of = date.today().isoformat() if item["source_mode"] == "live_collection" else item["as_of"]
            return enqueue(
                {"ticker": ticker, "mode": mode, "case": case, "as_of": as_of,
                 "source_mode": item["source_mode"]},
                self._finish_oracle,
            )
        if ticker == "AAPL" and "apple_generation" in self.catalog:
            as_of = self.catalog["apple_generation"]["as_of"]
            return enqueue({"ticker": ticker, "mode": mode, "case": case, "as_of": as_of,
                                "price_date": as_of, "source_mode": "apple_dated_close"}, self._finish_apple)
        if ticker not in self.companies:
            if self.catalog.get("operating_generation"):
                return enqueue({"ticker": ticker, "mode": mode, "as_of": date.today().isoformat(),
                                    "source_mode": "operating_research"}, self._finish_operating)
            return enqueue({"ticker": ticker, "mode": mode, "as_of": date.today().isoformat(),
                            "source_mode": "filing_screen"}, self._finish_screen)
        run = {"ticker": ticker, "mode": mode, "case": case, "as_of": item["as_of"],
               "price_date": item["price_date"], "target_date": item["target_date"]}
        if mode == "automated":
            if item.get("regenerate"):
                run["source_mode"] = "reference_regeneration"
                return enqueue(run, self._finish_run, item, case, overrides)
            return HTTPStatus.OK, self._published_run(item)
        return enqueue(run, self._finish_run, item, case, overrides)

    def _queue(self, token, fingerprint, run, finish, *arguments):
        run_id = uuid4().hex
        with self.lock:
            duplicate = self.jobs.active_duplicate(fingerprint)
            if duplicate:
                with self.jobs.db:
                    self.jobs.alias(token, fingerprint, duplicate)
                return HTTPStatus.ACCEPTED, deepcopy(self.runs[duplicate])
            if self.hosted_auth and run["mode"] == "automated":
                now = time()
                midnight = datetime.fromtimestamp(now,timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0)
                cached = self.jobs.completed_duplicate(fingerprint,midnight.timestamp(),now)
                if cached:
                    with self.jobs.db:
                        self.jobs.alias(token,fingerprint,cached)
                    return HTTPStatus.OK, deepcopy(self.runs[cached])
            if self.reloading:
                raise _APIError(503, "The private server is reloading. Retry in a moment.")
            pending = sum(row["state"] in ("queued", "running") for row in self.runs.values())
            if pending >= _MAX_PENDING:
                raise _APIError(429, "The research queue is full. Try again after an existing run completes.")
            now = time()
            available, maximum, label = max(
                (self.jobs.next_slot(now, window, maximum), maximum, label)
                for window, maximum, label in _RUN_LIMITS)
            if available > now:
                minutes = ceil((available - now) / 60)
                raise _APIError(429, f"The demo allows {maximum} new runs per {label}. "
                                f"Try again in {minutes} min. Existing downloads remain available until expiry.")
            run.update(id=run_id, state="queued")
            self.jobs.admit(run, now, token, fingerprint)
            self.runs[run_id] = run
            self.executor.submit(self._execute, finish, run_id, *arguments)
            return HTTPStatus.ACCEPTED, deepcopy(run)

    def _execute(self, finish, run_id, *arguments):
        from .spend_budget import enforce
        context = (self.jobs,self.lock,self.spend_limit) if self.spend_limit is not None else None
        try:
            with enforce(context):
                finish(run_id, *arguments)
        except Exception:
            _LOG.exception("Unhandled failure in research job %s", run_id)
        with self.lock:
            unfinished = self.runs[run_id]["state"] in ("queued", "running")
        if unfinished:
            self._finish(run_id, {"state": "failed", "error": "The research worker stopped before producing verified files. Start a new run."})

    @staticmethod
    def _published_run(item):
        return {"id": f"{item['ticker'].lower()}-automated", "ticker": item["ticker"], "mode": "automated",
                "case": item["case"], "as_of": item["as_of"], "price_date": item["price_date"],
                "target_date": item["target_date"], "state": "completed",
                "summary": {field: item[field] for field in _SUMMARY_FIELDS}, "cases": deepcopy(item["cases"]),
                "downloads": deepcopy(item["downloads"])}

    def _finish_run(self, run_id, item, case, overrides):
        with self.lock:
            self.runs[run_id]["state"] = "running"
            mode = self.runs[run_id]["mode"]
        filenames = {kind: f"{Path(entry['filename']).stem}_{mode}_{case}{Path(entry['filename']).suffix}"
                     for kind, entry in item["downloads"].items()}
        try:
            summary, cases, contents = _generate(item["ticker"], date.fromisoformat(item["as_of"]), self.inputs_dir,
                                                 self.node, self.node_modules, case, overrides, filenames)
            self._save_result(run_id, filenames, summary, cases, contents)
        except Exception:
            _LOG.exception("Research run %s failed", run_id)
            self._finish(run_id, {"state": "failed", "error": "The model could not produce a verified pair."})

    def _finish_screen(self, run_id):
        with self.lock:
            run = self.runs[run_id]
            run["state"] = "running"
            ticker, as_of = run["ticker"], date.fromisoformat(run["as_of"])
        try:
            evidence = _screen_ticker(ticker, as_of, self.contact)
            self._finish(run_id, {"state": "completed", "evidence": evidence})
        except Exception:
            _LOG.exception("SEC screen for %s failed", ticker)
            self._finish(run_id, {"state": "failed", "error": "The ticker could not be verified in EDGAR or the SEC data was unavailable."})

    def _finish_operating(self, run_id, inputs=None, overrides=()):
        from .model_template import export_operating, workbook_plan
        from .model_template.guided import fields as guided_fields
        from .operating_research import ResearchGap, prepare_operating_model, verify_valuation_range

        with self.lock:
            record = self.runs[run_id]
            record["state"] = "running"
            ticker, as_of = record["ticker"], date.fromisoformat(record["as_of"])
        try:
            if inputs is None:
                inputs = self._collect_operating(run_id, ticker, as_of)
                if inputs is None:
                    return
            packet, treasury, quote, selection, candidates, comps = inputs
            model = prepare_operating_model(packet, treasury, quote, selection, candidates)
            review = verify_valuation_range(model)
            if review:
                model["notes"] += (review,)
            if isinstance(comps, str):
                model["notes"] += (comps,)
            else:
                model["comparables"] = comps
            plan = workbook_plan(model)
            fields = guided_fields(plan, model)
            work_dir = self.inputs_dir.parent / "private" / "web-runs"
            work_dir.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(prefix="operating-", dir=work_dir) as stage:
                from functools import partial
                from .operating_report import fact_sheet, write_thesis
                key = environ.get("OPENAI_API_KEY", "").strip()
                writer = partial(write_thesis, key=key, model=environ.get("RESEARCH_MODEL", "gpt-5-mini").strip()) if key else None
                files = dict(zip(_MEDIA, export_operating(model, Path(stage), report_writer=writer,
                                                         overrides=overrides, plan=plan), strict=True))
                contents = {kind: file.read_bytes() for kind, file in files.items()}
            sheet = fact_sheet(contents["workbook"])
            facts = {key: row["value"] for key,row in sheet["facts"].items()}
            if overrides:
                from dataclasses import replace
                base = replace(model["scenarios"]["Base"], value_per_share=facts["dcf"])
                verify_valuation_range({**model, "scenarios":{"Base":base},
                                        "margin":facts["margin"], "implied_margin":facts["implied_margin"]})
            cases = [{"case": name, "spot_value_usd": facts[key]} for name,key in
                     (("bear","bear_value"),("base","dcf"),("bull","bull_value"))]
            summary = {"reference_close_usd": facts["close"], "spot_value_usd": facts["dcf"],
                       "spot_upside": facts["upside"], "rating": facts["rating"]}
            if type(facts["target"]) in (int,float):
                summary.update(target_value_usd=facts["target"],target_total_return=facts["total_return"])
            self._save_result(run_id, {kind: file.name for kind, file in files.items()}, summary, cases, contents,
                              operating_inputs=inputs if not overrides else None, price_date=model["price_date"].isoformat(),
                              guided={"available": not overrides, "fields": fields if not overrides else []},
                              company={"ticker": ticker, "company": model["company"],
                                       "sector": packet["issuer"].get("industry") or "Operating company"})
        except ResearchGap as exc:
            self._finish(run_id, {"state": "failed", "error": str(exc)})
        except Exception:
            _LOG.exception("Operating research run %s failed", run_id)
            message = ("A filing or market-data source could not respond or be verified. Try again later."
                       if inputs is None else "The builder could not produce a verified Excel and PDF pair. No files were published.")
            self._finish(run_id, {"state": "failed", "error": message})

    def _finish_exchange(self, run_id, qualified):
        from .listing_directory import resolve_jpx, reviewed_london_listing
        from .operating_research import ResearchGap
        from .tokyo_market import collect_jpx_close

        with self.lock:
            run = self.runs[run_id]
            run["state"] = "running"
            as_of = date.fromisoformat(run["as_of"])
        exchange, code = qualified
        try:
            if exchange == "XLON":
                listing = reviewed_london_listing(code)
                self._collect_operating(run_id, listing["sec_ticker"], as_of, home_listing=listing)
                return
            listing = resolve_jpx(code, as_of)
            kind = listing["market_segment"]
            fund = any(term in kind.lower() for term in ("etf", "etn", "reit", "fund"))
            if code == "7974" and not fund:
                from .nintendo_valuation import prepare_nintendo

                quote = collect_jpx_close(code, as_of)
                self._finish_nintendo(run_id, prepare_nintendo(as_of, quote), listing)
                return
            quote = None
            if not fund:
                try:
                    quote = collect_jpx_close(code, as_of)
                except ResearchGap:
                    pass
            gap = ("This exchange classification requires a fund or product-specific valuation." if fund else
                   f"Verified Tokyo close of JPY {quote['close']:,.2f} on {quote['trading_date']}. A reviewed local filing model is needed for this issuer."
                   if quote else "Verified Tokyo listing. Local price and filing evidence are needed for this issuer.")
            self._finish(run_id, {"state": "completed", "evidence": {
                "company": listing["name"], "industry": listing["industry"], "home_listing": listing,
                "market_quote": quote,
                "annual_periods": 0, "annual_filings": 0, "missing": gap,
            }})
        except ResearchGap as exc:
            self._finish(run_id, {"state": "failed", "error": str(exc)})
        except (URLError, TimeoutError):
            _LOG.exception("Exchange data source unavailable for %s:%s", exchange, code)
            self._finish(run_id, {"state": "failed", "error": "An exchange filing or market-data source is unavailable. Try again later."})
        except Exception:
            _LOG.exception("Exchange research failed for %s:%s", exchange, code)
            self._finish(run_id, {"state": "failed", "error": "The exchange evidence could not be verified."})

    def _finish_nintendo(self, run_id, data, listing, overrides=()):
        from .nintendo_valuation import export_nintendo

        with self.lock:
            record = self.runs[run_id]
            record["state"] = "running"
            ticker = record["ticker"]
        params = {"margin": overrides[0]["value"], "reason": overrides[0]["reason"]} if overrides else {}
        try:
            work_dir = self.inputs_dir.parent / "private" / "web-runs"
            work_dir.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(prefix="tokyo-", dir=work_dir) as stage:
                pair, value = export_nintendo(data, Path(stage), self.node, self.node_modules, **params)
                files = dict(zip(_MEDIA, pair, strict=True))
                contents = {kind: file.read_bytes() for kind, file in files.items()}
            quote = data["quote"]
            basis = data["balance_date"]
            summary = {"reference_close_local": quote["close"], "spot_value_local": value,
                       "spot_upside": value / quote["close"] - 1, "rating": "Review"}
            gap = f"The yen DCF uses the filed {basis} balance date. Trading comparables and updates to the equity bridge require review."
            evidence = {"company": listing["name"], "industry": listing["industry"], "home_listing": listing,
                        "reporting_currency": "JPY", "annual_periods": 2, "annual_filings": 1, "missing": gap,
                        "market_quote": quote, "yield_benchmark": data["yield"]}
            fields = [{"target": "margin", "label": "Operating margin after the guided fiscal year", "unit": "decimal", "min": .05,
                       "max": .35, "default": data["guide_ebit"]/data["guide_sales"],
                       "basis": "Implied by current fiscal-year management guidance as an analyst projection",
                       "source": data["interim_url"]}]
            self._save_result(run_id, {kind: file.name for kind, file in files.items()}, summary, [], contents,
                              evidence=evidence, price_date=quote["trading_date"], balance_date=basis.isoformat(),
                              company={"ticker": ticker, "company": listing["name"], "sector": listing["industry"]},
                              operating_inputs={"nintendo": data, "listing": listing} if not overrides else None,
                              guided={"available": not overrides, "fields": fields if not overrides else []})
        except Exception:
            _LOG.exception("Tokyo research run %s failed", run_id)
            self._finish(run_id, {"state": "failed", "error": "The Nintendo workbook or PDF failed generation or verification. No files were published."})

    def _collect_operating(self, run_id, ticker, as_of, *, home_listing=None):
        from .bank_filings import BANK_CIKS
        from .market_quotes import AlpacaQuoteClient, collect_market_quotes
        from .operating_comps import prepare_comparables
        from .operating_filing_bridge import enrich_operating_bridge
        from .operating_research import ResearchGap, revenue_candidates, verify_comparable_operating_basis
        from .research_agent import select_forecast
        from .sec_client import SECClient
        from .sec_intake import collect_ticker_evidence
        from .treasury_rates import collect_treasury_yield

        client = SECClient(self.contact)
        screen = collect_ticker_evidence(client, ticker, as_of=as_of, statements_only=True)
        if home_listing and screen["issuer"]["cik"] != home_listing["cik"]:
            raise ResearchGap("The home listing and the SEC filing resolve to different issuers.")
        if screen.get("foreign_filings"):
            evidence = self._foreign_screen(screen, client, as_of)
            if home_listing:
                evidence["home_listing"] = home_listing
            if screen["issuer"]["cik"] in {"0001000184", "0001900304"}:
                try:
                    self._finish_foreign(run_id, evidence)
                    return
                except ResearchGap as exc:
                    evidence["missing"] = str(exc)
            self._finish(run_id, {"state": "completed", "evidence": evidence})
            return
        sic = str(screen["issuer"].get("sic") or "")
        if (sic.isdigit() and 6000 <= int(sic) <= 6799) or not screen["coverage"]["annual_statement_history"]:
            result = _screen_ticker(ticker, as_of, self.contact, packet=screen)
            if screen["issuer"].get("cik") in BANK_CIKS | {"0000080661", "0000726728"}:
                packet = collect_ticker_evidence(client, ticker, as_of=as_of, release_limit=2)
                treasury = collect_treasury_yield(as_of)
                if treasury["status"] == "available":
                    self._finish_specialty_pilot(run_id, packet, client, treasury["rate"], result)
                    return
            self._finish(run_id, {"state": "completed", "evidence": result})
            return
        packet = collect_ticker_evidence(client, ticker, as_of=as_of, release_limit=2)
        if packet["issuer"]["cik"] == "0001467858":
            from .gm_capital_structure import prepare_gm_split
            from .gm_export import export_gm
            from .gm_valuation import AutomotiveAssumptions

            split = prepare_gm_split(packet, client)
            treasury = collect_treasury_yield(as_of)
            if treasury["status"] != "available":
                raise ResearchGap("A dated Treasury yield is needed for the GM Financial valuation.")
            assumptions = AutomotiveAssumptions(wacc=treasury["rate"] + .04, finance_cost_of_equity=treasury["rate"] + .045)
            work_dir = self.inputs_dir.parent / "private" / "web-runs"
            work_dir.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(prefix="captive-", dir=work_dir) as stage:
                pair = export_gm(split, assumptions, treasury, Path(stage), self.node, self.node_modules)
                files = dict(zip(_MEDIA, pair, strict=True))
                contents = {kind: file.read_bytes() for kind, file in files.items()}
            evidence = _screen_ticker(ticker, as_of, self.contact, packet=screen)
            anchor = split["finance_equity"]["end"]
            evidence["missing"] = (f"The Automotive and GM Financial model uses the filed {anchor} balance date. "
                                   "A current valuation needs a post-quarter equity bridge and suitable peer valuations.")
            self._save_result(run_id, {kind: file.name for kind, file in files.items()}, {}, [], contents, evidence=evidence)
            return
        if packet["issuer"]["cik"] == "0000100885" and packet["interim_actuals"]["periods"]:
            from .sec_guidance import _ReleaseText

            latest = packet["interim_actuals"]["periods"][-1]["filing"]["url"]
            filing = _ReleaseText(client.get_text(latest))
            if any("pending acquisition" in block.lower() and "norfolk southern" in block.lower()
                   for block in filing.blocks):
                raise ResearchGap("Union Pacific's filing reports a pending Norfolk Southern acquisition. "
                                  "A current model requires stand-alone and merger cases and the financing terms.")
        from .acquisition_basis import collect_acquisition_basis
        collect_acquisition_basis(packet, client)
        verify_comparable_operating_basis(packet)
        from .drivers import collect
        collect(packet, client)
        if packet["issuer"]["cik"] == "0000823768":
            from .waste_management_revenue import waste_management_candidates

            candidates = waste_management_candidates(packet, client)
        else:
            candidates = revenue_candidates(packet)
        enrich_operating_bridge(packet, client)
        provider = {"key": environ["OPENAI_API_KEY"].strip(),
                    "model": environ.get("RESEARCH_MODEL", "gpt-5-mini").strip()}
        selection = select_forecast(packet, candidates, **provider)
        if selection["group"] == "unsupported":
            raise ResearchGap(selection["reason"])
        subject = packet["issuer"]["ticker"]
        price_client = AlpacaQuoteClient(environ["APCA_API_KEY_ID"].strip(), environ["APCA_API_SECRET_KEY"].strip())
        proposed = selection["peer_candidates"]
        market = collect_market_quotes(price_client, [subject, *(peer["ticker"] for peer in proposed)], as_of=as_of)
        quote = market["quotes"].get(subject)
        treasury = collect_treasury_yield(as_of)
        if not quote or quote["trading_date"] != market["latest_completed_session"] or treasury["status"] != "available":
            raise ResearchGap("The latest completed-session stock close or Treasury yield could not be verified.")
        quotes = dict(market["quotes"])
        peers = {}

        def collect_peers(choices):
            for symbol in dict.fromkeys(row["ticker"] for row in choices):
                if symbol == subject or symbol not in quotes:
                    continue
                try:
                        peers[symbol] = collect_ticker_evidence(client, symbol, as_of=as_of, years=3, release_limit=1)
                except ValueError as exc:
                    _LOG.info("Peer %s was unavailable: %s", symbol, exc)

        collect_peers(proposed)
        try:
            comps = prepare_comparables(packet, proposed, peers, quotes, quote["trading_date"])
        except ResearchGap as exc:
            comps = f"Peer valuation unavailable. {exc}"
        if isinstance(comps, dict) and comps["value"] is None:
            try:
                seen = {row["ticker"] for row in proposed} | {subject}
                retry = select_forecast(packet, candidates, rejected_peers=sorted(seen), **provider)
                added = [row for row in retry["peer_candidates"] if row["ticker"] not in seen]
                existing = {row["ticker"] for row in comps["rows"]}
                added = added[:5 - len(existing)]
                if added:
                    extra_market = collect_market_quotes(price_client, [row["ticker"] for row in added], as_of=as_of)
                    quotes.update(extra_market["quotes"])
                    collect_peers(added)
                    tried = [row for row in proposed if row["ticker"] in existing] + added
                    updated = prepare_comparables(packet, tried, peers, quotes, quote["trading_date"])
                    updated["excluded"] = list(dict.fromkeys([*comps["excluded"], *updated["excluded"]]))
                    comps = updated
            except ResearchGap:
                _LOG.info("Alternate peer selection was unavailable for %s", subject)
        from .drivers import choose
        from .market_valuation import collect_market_inputs
        from .valuation_comps import collect_forward_eps, prepare as prepare_valuation_comps, same_business
        kind = choose(packet, selection)["type"]
        if kind != "segment_growth":
            for peer in peers.values():
                try:
                    collect(peer, client)
                except ValueError:
                    continue
        sic = str(packet["issuer"].get("sic") or "")
        industry_peers = [ticker for ticker, peer in peers.items() if len(sic) == 4
                          and str(peer["issuer"].get("sic") or "")[:3] == sic[:3]
                          and same_business(packet, peer, kind)]
        try:
            packet["valuation_market"] = collect_market_inputs(packet, price_client, quote, industry_peers, treasury["rate"])
        except (ValueError, OSError, KeyError) as exc:
            raise ResearchGap(f"Market WACC evidence unavailable. {exc}") from exc
        try:
            collect_forward_eps(packet, client)
            for peer in peers.values():
                collect_forward_eps(peer, client)
                try:
                    enrich_operating_bridge(peer, client)
                except ValueError:
                    continue
            packet["valuation_comps"] = prepare_valuation_comps(
                packet, [{"ticker": ticker} for ticker in peers], peers, quotes, kind)
        except ResearchGap as exc:
            packet["valuation_comps"] = {"value": None, "rows": [], "summary": {}, "reason": str(exc)}
        from .report_evidence import collect as collect_report_evidence
        collect_report_evidence(packet, client)
        return packet, treasury, quote, selection, candidates, comps

    def _foreign_screen(self, screen, client, as_of):
        from .asml_filings import collect_asml_interim
        from .foreign_comps import collect_foreign_comps
        from .foreign_financials import collect_foreign_financials
        from .foreign_fx import collect_eur_benchmark, collect_gbp_benchmark, collect_usd_fx
        from .foreign_interim import collect_foreign_interim, sap_revenue_scenario
        from .haleon_filings import collect_haleon_interim
        from .market_quotes import AlpacaQuoteClient, collect_market_quotes

        issuer = screen["issuer"]
        filings = screen["foreign_filings"]
        financials = collect_foreign_financials(client, issuer, filings["annual"])
        quote = None
        if financials.listing["ordinary_shares_per_listed_unit"] is not None:
            try:
                quotes = AlpacaQuoteClient(environ.get("APCA_API_KEY_ID", "").strip(),
                                           environ.get("APCA_API_SECRET_KEY", "").strip())
                market = collect_market_quotes(quotes, issuer["ticker"], as_of=as_of)
                candidate = market["quotes"].get(issuer["ticker"])
                if candidate and candidate["trading_date"] == market["latest_completed_session"]:
                    quote = candidate
            except (ValueError, OSError):
                _LOG.info("A matched U.S. close was unavailable for %s", issuer["ticker"])
        market_cutoff = date.fromisoformat(quote["trading_date"]) if quote else as_of
        fx = collect_usd_fx(financials.currency, market_cutoff)
        get_yield = {"EUR": collect_eur_benchmark, "GBP": collect_gbp_benchmark}.get(financials.currency)
        benchmark = get_yield(market_cutoff) if get_yield else None
        annual_end = date.fromisoformat(financials.periods[-1]["period_end"])
        if issuer["cik"] == "0001900304":
            interim, scenario = collect_haleon_interim(client, filings["interim_reports"], annual_end,
                                                      financials.latest_revenue_drivers)
        elif issuer["cik"] == "0000937966":
            interim = collect_asml_interim(client, filings["interim_reports"], annual_end)
            scenario = None
        else:
            interim = collect_foreign_interim(client, issuer, filings["interim_reports"], annual_end)
            scenario = sap_revenue_scenario(annual_end, financials.latest_revenue_drivers, interim)
        if scenario:
            scenario["annual_source"] = financials.listing["source"]
        comps = None
        if scenario and quote:
            try:
                comps = collect_foreign_comps(client, quotes, issuer, financials, quote, fx, as_of)
            except (ValueError, OSError):
                _LOG.info("A verified foreign peer comparison is unavailable for %s", issuer["ticker"], exc_info=True)
        reconciled = sum(row["balance_reconciled"] for row in financials.periods)
        ratio = financials.listing["ordinary_shares_per_listed_unit"]
        if ratio is None:
            share_note = "The listing ratio still requires evidence."
        elif financials.listing["kind"] == "ordinary":
            share_note = "The U.S. listing represents one directly traded common share per unit."
        else:
            shares = "share" if ratio == 1 else "shares"
            share_note = f"The filed depositary ratio is {ratio:g} ordinary {shares} per U.S.-listed unit."
        fx_note = (f"The ECB conversion is {fx['usd_per_unit']:.6g} USD per {financials.currency} "
                   f"on {fx['date']}." if fx["status"] == "available" else "A dated ECB rate is unavailable.")
        if quote and fx.get("date") != quote["trading_date"]:
            fx_note += " A same-session U.S. close and FX observation are still needed for valuation."
        if scenario:
            interim_note = ("The interim statements reconcile and four revenue lines support a Q4 growth scenario. "
                            "A current valuation requires a multi-year forecast and verified exports.")
        elif interim["status"] == "available":
            interim_note = "The interim statements reconcile. " + interim.get(
                "capital_gap", "A current valuation requires a company forecast and verified exports.")
            if guidance := interim.get("dated_sales_guidance"):
                low, high = guidance["annual_range"]
                interim_note = f"The interim statements reconcile and management guides to €{low/1000:g}–€{high/1000:g}B annual sales. " + interim["capital_gap"]
        else:
            interim_note = "A current valuation requires normalized interim statements, a company forecast, and verified exports."
        return {
            "company": issuer["name"], "ticker": issuer["ticker"], "industry": issuer.get("industry"),
            "exchange": issuer.get("exchange"),
            "annual_periods": len(financials.periods), "annual_financials": financials.periods,
            "annual_filings": screen["coverage"]["annual_filing_links"], "reporting_currency": financials.currency,
            "accounting": financials.taxonomy, "listing": financials.listing, "fx": fx, "yield_benchmark": benchmark,
            "market_quote": quote, "quote_fx_same_day": bool(quote and fx.get("date") == quote["trading_date"]),
            "comparables": comps,
            "interim_reports": len(filings["interim_reports"]), "interim": interim, "revenue_scenario": scenario,
            "missing": (f"{reconciled} annual balance sheets reconcile in {financials.currency} under "
                        f"{financials.taxonomy}. {share_note} {fx_note} {interim_note}"),
        }

    def _finish_foreign_guided(self, run_id, evidence, overrides):
        from .operating_research import ResearchGap

        with self.lock:
            self.runs[run_id]["state"] = "running"
        try:
            self._finish_foreign(run_id, evidence, overrides)
        except ResearchGap as exc:
            self._finish(run_id, {"state": "failed", "error": str(exc)})
        except Exception:
            _LOG.exception("Guided foreign run %s failed", run_id)
            self._finish(run_id, {"state": "failed", "error": "The guided research files could not be verified."})

    def _finish_foreign(self, run_id, evidence, overrides=()):
        from dataclasses import replace

        from .sap_export import export_sap
        from .sap_valuation import SAP_GUIDES, foreign_defaults, prepare_foreign_operating

        evidence = dict(evidence)
        ticker = evidence["ticker"]
        defaults = foreign_defaults(ticker)
        assumptions = replace(defaults, **{row["target"]: row["value"] for row in overrides})
        model = prepare_foreign_operating(evidence, assumptions, ticker=ticker)
        model["overrides"] = overrides
        work_dir = self.inputs_dir.parent / "private" / "web-runs"
        work_dir.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="foreign-operating-", dir=work_dir) as stage:
            pair = export_sap(model, Path(stage), self.node, self.node_modules)
            files = dict(zip(_MEDIA, pair, strict=True))
            contents = {kind: file.read_bytes() for kind, file in files.items()}
        evidence["missing"] = (f"Verified {ticker} DCF and research report. The valuation uses the filed "
                               f"{model['dcf'].as_of} balance date and a weighted-average diluted-share proxy.")
        if evidence.get("home_listing"):
            evidence["missing"] += (f" The London ordinary-share DCF value is GBP {model['dcf'].value_per_share:.2f}. "
                                    "The closing price shown is for the U.S. ADS.")
        value = model["usd_per_ads"]
        close = evidence["market_quote"]["close"]
        summary = {"reference_close_usd": close, "spot_value_usd": value, "spot_upside": value / close - 1,
                   "value_per_ordinary_share": model["dcf"].value_per_share}
        fields = [{"target": target, "label": label, "unit": "decimal", "min": low, "max": high,
                   "default": getattr(defaults, target), "basis": "Editable analyst assumption"}
                  for target,(label, low, high) in SAP_GUIDES.items()]
        self._save_result(run_id, {kind: file.name for kind, file in files.items()}, summary, [], contents,
                          evidence=evidence, price_date=model["market_date"].isoformat(),
                          balance_date=model["dcf"].as_of.isoformat(),
                          company={"ticker":ticker, "company":evidence["company"], "sector":evidence["industry"]},
                          operating_inputs={"foreign_evidence":evidence} if not overrides else None,
                          guided={"available": not overrides, "fields": fields if not overrides else []})

    def _finish_specialty_pilot(self, run_id, packet, client, risk_free, result):
        from .bank_comps import prepare_bank_comps
        from .bank_filings import BANK_CIKS, prepare_bank_filings
        from .insurer_filings import prepare_progressive
        from .market_quotes import AlpacaQuoteClient
        from .reit_filings import prepare_realty_income
        from .specialty_export import export_insurer, export_reit

        issuer = packet["issuer"]
        if issuer["cik"] in BANK_CIKS:
            bank = prepare_bank_filings(packet, client)
            comps = None
            try:
                quote_client = AlpacaQuoteClient(environ.get("APCA_API_KEY_ID", "").strip(),
                                                environ.get("APCA_API_SECRET_KEY", "").strip())
                comps = prepare_bank_comps(client, quote_client, packet, bank.balance_date)
            except (ValueError, OSError):
                _LOG.info("Could not verify bank tangible book comparables.", exc_info=True)
            self._finish_bank(run_id, {"bank": bank, "comparables": comps, "cost_of_equity": risk_free+.045,
                                       "evidence": result, "ticker": issuer["ticker"]})
            return
        work_dir = self.inputs_dir.parent / "private" / "web-runs"
        work_dir.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="specialty-", dir=work_dir) as stage:
            export_args = (Path(stage), self.node, self.node_modules)
            names = {"company": issuer["name"], "ticker": issuer["ticker"]}
            if issuer["cik"] == "0000080661":
                basis, balance_date, sources = prepare_progressive(packet, client)
                comps = self._insurance_comparables(packet, client)
                pair = export_insurer(basis, balance_date, sources, *export_args, **names, cost_of_equity=risk_free + .045,
                                      comparables=comps)
                gaps = "subsidiary capital and dividend restrictions and a current common-equity bridge"
                if comps is None:
                    gaps += ", plus verified market prices and peer earnings"
            else:
                reit = prepare_realty_income(packet, client)
                comps = self._reit_comparables(packet, client)
                pair = export_reit(reit, *export_args, **names, discount_rate=risk_free + .03, comparables=comps)
                balance_date = reit.date
                gaps = "a current capital bridge" if comps else "a current capital bridge, same-session market closes, and net-lease peers"
            files = dict(zip(_MEDIA, pair, strict=True))
            contents = {kind: file.read_bytes() for kind, file in files.items()}
        result["missing"] = f"The pilot uses the filed {balance_date} balance date. A current stock valuation requires {gaps}."
        self._save_result(run_id, {kind: file.name for kind, file in files.items()}, {}, [], contents,
                          evidence=result)

    def _finish_bank(self, run_id, inputs, overrides=()):
        from .bank_export import export_bank
        from .operating_research import ResearchGap

        with self.lock:
            self.runs[run_id]["state"] = "running"
        ticker, bank, comps = (inputs[key] for key in ("ticker", "bank", "comparables"))
        evidence = dict(inputs["evidence"])
        defaults = {"cost_of_equity": inputs["cost_of_equity"], "capital_buffer": .015, "terminal_growth": .025}
        assumptions = {**defaults, **{row["target"]: row["value"] for row in overrides}}
        fields = [{"target": target, "label": label, "unit": "decimal", "min": low, "max": high,
                   "default": defaults[target], "basis": "Editable analyst assumption"}
                  for target, label, low, high in (
                      ("cost_of_equity", "Cost of common equity", .06, .20),
                      ("capital_buffer", "CET1 buffer over the disclosed floor", 0., .05),
                      ("terminal_growth", "Terminal growth", .005, .04))]
        try:
            work_dir = self.inputs_dir.parent / "private" / "web-runs"
            work_dir.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(prefix="bank-", dir=work_dir) as stage:
                pair, valuation = export_bank(bank, Path(stage), self.node, self.node_modules, company=evidence["company"],
                                             ticker=ticker, comparables=comps, overrides=overrides, **assumptions)
                files = dict(zip(_MEDIA, pair, strict=True))
                contents = {kind: file.read_bytes() for kind, file in files.items()}
            evidence["missing"] = (f"The distribution valuation uses the filed {bank.balance_date} capital and share counts. "
                                   "Prospective leverage, liquidity, and upstream dividend limits require separate review.")
            if comps is None:
                evidence["missing"] += " A verified comparable-bank valuation is unavailable."
            summary = {}
            price_date = None
            if bank.common_shares_millions and comps:
                close = comps["subject"]["close"]
                value = valuation.common_equity/bank.common_shares_millions
                summary = {"reference_close_usd": close, "spot_value_usd": value, "spot_upside": value/close-1,
                           "rating": "Review"}
                price_date = comps["quote_date"]
                evidence.update(reporting_currency="USD", valuation_method="Bank distributions")
            self._save_result(run_id, {kind: file.name for kind, file in files.items()}, summary, [], contents,
                              company={"ticker": ticker, "company": evidence["company"], "sector": evidence["industry"]},
                              evidence=evidence, balance_date=bank.balance_date.isoformat(), price_date=price_date,
                              operating_inputs=inputs if not overrides else None,
                              guided={"available": not overrides, "fields": fields if not overrides else []})
        except ResearchGap as exc:
            self._finish(run_id, {"state": "failed", "error": str(exc)})
        except Exception:
            _LOG.exception("Bank research run %s failed", run_id)
            self._finish(run_id, {"state": "failed", "error": "The builder could not verify the bank workbook and PDF."})

    @staticmethod
    def _insurance_comparables(packet, client):
        from .market_quotes import AlpacaQuoteClient, collect_market_quotes
        from .operating_comps import prepare_comparables
        from .sec_intake import collect_ticker_evidence

        as_of = date.fromisoformat(packet["as_of"])
        candidates = [{"ticker": "TRV", "reason": "U.S. property-and-casualty carrier with personal and commercial lines."},
                      {"ticker": "ALL", "reason": "U.S. personal-lines carrier with a substantial auto business."}]
        quote_client = AlpacaQuoteClient(environ["APCA_API_KEY_ID"].strip(), environ["APCA_API_SECRET_KEY"].strip())
        subject = packet["issuer"]["ticker"]
        try:
            market = collect_market_quotes(quote_client, [subject, *(row["ticker"] for row in candidates)], as_of=as_of)
            quotes = market["quotes"]
            quote_date = market["latest_completed_session"]
            if quotes.get(subject, {}).get("trading_date") != quote_date or subject not in quotes:
                return None
            peers = {row["ticker"]: collect_ticker_evidence(client, row["ticker"], as_of=as_of, years=2, statements_only=True)
                     for row in candidates if row["ticker"] in quotes}
            comps = prepare_comparables(packet, candidates, peers, quotes, quote_date, industry_sic="6331")
            return {**comps, "subject_quote": quotes[subject]} if comps["value"] is not None else None
        except (ValueError, OSError):
            _LOG.info("Could not verify the live P&C peer comparison.", exc_info=True)
            return None

    @staticmethod
    def _reit_comparables(packet, client):
        from .market_quotes import AlpacaQuoteClient, collect_market_quotes
        from .reit_comps import prepare_reit_comparables
        from .sec_intake import collect_ticker_evidence

        as_of = date.fromisoformat(packet["as_of"])
        comparison = ("ADC", "NNN")
        quote_client = AlpacaQuoteClient(environ["APCA_API_KEY_ID"].strip(), environ["APCA_API_SECRET_KEY"].strip())
        try:
            market = collect_market_quotes(quote_client, ["O", *comparison], as_of=as_of)
            packets = {"O": packet}
            for ticker in comparison:
                packets[ticker] = collect_ticker_evidence(client, ticker, as_of=as_of, years=1, release_limit=1)
            return prepare_reit_comparables(packets, market["quotes"], market["latest_completed_session"], client)
        except (ValueError, OSError, TypeError):
            _LOG.info("Could not verify the live net-lease peer comparison.", exc_info=True)
            return None

    def _finish_oracle(self, run_id):
        from .oracle_web_run import OracleInputsUnavailable, generate_oracle_run

        with self.lock:
            run = self.runs[run_id]
            run["state"] = "running"
            as_of = run["as_of"]
        try:
            summary, cases, contents, quote_date = generate_oracle_run(self.inputs_dir.parent / "private" / "web-runs",
                                                            self.node, self.node_modules, self.oracle_evidence,
                                                            dated_closes=self.oracle_dated_closes, as_of=date.fromisoformat(as_of))
            filenames = {"workbook": f"ORCL-{as_of}.xlsx", "report": f"ORCL-{as_of}.pdf"}
            self._save_result(run_id, filenames, summary, cases, contents, price_date=quote_date.isoformat())
        except OracleInputsUnavailable as exc:
            self._finish(run_id, {"state": "failed", "error": str(exc)})
        except Exception:
            _LOG.exception("Oracle research run %s failed", run_id)
            self._finish(run_id, {"state": "failed", "error": "Oracle generation could not produce a verified pair."})

    def _finish_apple(self, run_id):
        from .apple_export import export_apple
        from .apple_pilot import AS_OF, CLOSE, prepare_apple
        from .sec_client import SECClient
        from .sec_intake import collect_ticker_evidence
        from .treasury_rates import collect_treasury_yield

        with self.lock:
            self.runs[run_id]["state"] = "running"
        try:
            packet = collect_ticker_evidence(SECClient(self.contact), "AAPL", as_of=AS_OF)
            model = prepare_apple(packet, collect_treasury_yield(AS_OF))
            work_dir = self.inputs_dir.parent / "private" / "web-runs"
            work_dir.mkdir(parents=True, exist_ok=True)
            with TemporaryDirectory(prefix="apple-", dir=work_dir) as stage:
                files = dict(zip(_MEDIA, export_apple(model, Path(stage), self.node, self.node_modules), strict=True))
                contents = {kind: file.read_bytes() for kind, file in files.items()}
            cases = [{"case": name.lower(), "spot_value_usd": result.value_per_share}
                     for name, result in model["scenarios"].items()]
            value = model["scenarios"]["Base"].value_per_share
            upside = value / CLOSE - 1
            summary = {"reference_close_usd": CLOSE, "spot_value_usd": value, "spot_upside": upside,
                       "rating": "Buy" if upside >= .15 else "Sell" if upside <= -.15 else "Hold"}
            self._save_result(run_id, {kind: file.name for kind, file in files.items()}, summary, cases, contents)
        except Exception:
            _LOG.exception("Apple generation %s failed", run_id)
            self._finish(run_id, {"state": "failed", "error": "Apple generation could not verify its filing inputs or paired files."})

    def _save_result(self, run_id, filenames, summary, cases, contents, *, operating_inputs=None, **run_fields):
        if sum(map(len, contents.values())) > _MAX_PAIR:
            raise ValueError("The generated Excel and PDF pair exceeds the demo storage limit.")
        downloads = {kind: {
            "filename": filenames[kind], "path": _download_path("runs", run_id, kind),
            "size_bytes": len(content), "sha256": sha256(content).hexdigest(),
        } for kind, content in contents.items()}
        self._finish(run_id, {"state": "completed", "summary": summary, "cases": cases, "downloads": downloads, **run_fields},
                     contents, operating_inputs=operating_inputs)

    def _finish(self, run_id, outcome, contents=None, *, operating_inputs=None):
        with self.lock:
            expires = time() + _RETENTION
            run = {**self.runs[run_id], **outcome, "expires_at": datetime.fromtimestamp(expires, timezone.utc).isoformat()}
            self.jobs.finish(run, expires, contents)
            self.runs[run_id] = run
            if operating_inputs is not None:
                self.operating_inputs[run_id] = operating_inputs

    def _purge(self, now):
        for run_id in self.jobs.purge(now):
            self.runs.pop(run_id, None)
            self.operating_inputs.pop(run_id, None)

    def get_run(self, run_id):
        if run_id.endswith("-automated"):
            item = self.companies.get(run_id.removesuffix("-automated").upper())
            if item is not None and run_id == f"{item['ticker'].lower()}-automated":
                return self._published_run(item)
        with self.lock:
            self._purge(time())
            run = self.runs.get(run_id)
            if run is not None:
                result = deepcopy(run)
                if result.get("source_mode") == "operating_research" and run_id not in self.operating_inputs:
                    result.pop("guided", None)
                return result
        raise _APIError(404, "This research run has expired or is no longer stored. Start a new run.")

    def get_asset(self, namespace, identity, kind):
        if kind in _MEDIA and namespace == "companies":
            company = self.companies.get(identity)
            if company is not None:
                return company["downloads"][kind]["filename"], self.assets[identity][kind]
        if kind in _MEDIA and namespace == "runs":
            with self.lock:
                self._purge(time())
                content = self.jobs.download(identity, kind)
                if content is not None:
                    return self.runs[identity]["downloads"][kind]["filename"], content
        raise _APIError(404, "This download has expired or has no verified file. Start a new run if needed.")

    def close(self):
        self.executor.shutdown(wait=True, cancel_futures=True)
        self.jobs.close()


def _json_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate JSON key.")
        value[key] = item
    return value


class _Handler(BaseHTTPRequestHandler):
    timeout = 15

    def _send(self, content, *, status=200, content_type="application/json; charset=utf-8", filename=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        if status == 401:
            self.send_header("WWW-Authenticate", 'Basic realm="Private equity research", charset="UTF-8"')
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(content)

    def _json(self, value, status=200):
        self._send(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8"), status=status)

    def _path(self):
        service = self.server.research
        hosted = getattr(service,"hosted_name","")
        if hosted:
            if self.headers.get_all("Host",[]) != [hosted]:
                raise _APIError(421,"Use the configured research hostname.")
            if self.command in ("GET","HEAD") and self.path == "/healthz":
                return self.path
            if self.headers.get_all("X-Forwarded-Proto",[]) != ["https"]:
                raise _APIError(403,"Open the research site over HTTPS.")
            auth = self.headers.get_all("Authorization",[])
            if len(auth)!=1 or not compare_digest(auth[0].encode("latin-1"),service.hosted_auth):
                raise _APIError(401,"Sign in with the recruiter password.")
        if service.oracle and not hosted:
            port = self.server.server_port
            allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
            codespaces = environ.get("CODESPACES") == "true"
            if codespaces:
                name = environ.get("CODESPACE_NAME", "")
                domain = environ.get("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN", "")
                if re.fullmatch(r"[a-z0-9-]+", name) and re.fullmatch(r"[a-z0-9.-]+", domain):
                    allowed.add(f"{name}-{port}.{domain}")
            hosts = self.headers.get_all("Host", [])
            if len(hosts) != 1 or hosts[0].lower() not in allowed:
                raise _APIError(421, "Open the private builder at its localhost address.")
            # The GitHub sign-in redirect can carry a cross-site fetch header
            # on the first page request. Port authentication still applies.
            landing_page = codespaces and self.command in ("GET", "HEAD") and self.path == "/"
            if self.headers.get("Sec-Fetch-Site") == "cross-site" and not landing_page:
                raise _APIError(403, "Open the private builder directly in your browser.")
        if hosted and self.command=="POST" and self.headers.get("Sec-Fetch-Site") == "cross-site":
            raise _APIError(403,"Open the research site directly in your browser.")
        try:
            path = unquote(urlsplit(self.path).path, errors="strict")
        except ValueError as exc:
            raise _APIError(400, "Invalid request path.") from exc
        if "\\" in path or "\0" in path or any(part in (".", "..") for part in path.split("/")):
            raise _APIError(404, "Path unavailable.")
        return path

    def _read_json(self):
        if self.headers.get_content_type() != "application/json":
            raise _APIError(415, "Send application/json.")
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or self.headers.get("Transfer-Encoding") is not None:
            raise _APIError(400, "Send one Content-Length header for the JSON body.")
        try:
            length = int(lengths[0])
        except ValueError as exc:
            raise _APIError(400, "A valid Content-Length is required.") from exc
        if not 1 <= length <= _MAX_BODY:
            raise _APIError(413, "Request body is too large or empty.")
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"), object_pairs_hook=_json_object)
        except (ValueError, RecursionError) as exc:
            raise _APIError(400, "Invalid JSON request.") from exc

    def do_POST(self):
        try:
            if self._path() != "/api/runs":
                raise _APIError(404, "Endpoint unavailable.")
            status, result = self.server.research.create_run(self._read_json())
            self._json(result, status)
        except _APIError as exc:
            self._json({"error": str(exc)}, exc.status)

    def do_GET(self):
        try:
            path = self._path()
            parts = path.removeprefix("/").split("/")
            service = self.server.research
            if path == "/healthz":
                self._json({"status":"ok"})
            elif parts == ["api", "companies"]:
                self._json(service.catalog)
            elif len(parts) == 3 and parts[:2] == ["api", "runs"]:
                self._json(service.get_run(parts[2]))
            elif len(parts) == 5 and parts[0] == "api" and parts[1] in ("companies", "runs") and parts[3] == "downloads":
                filename, content = service.get_asset(parts[1], parts[2], parts[4])
                self._send(content, content_type=_MEDIA[parts[4]], filename=filename)
            elif parts[0] == "api":
                raise _APIError(404, "Endpoint unavailable.")
            else:
                self._serve_static(path)
        except _APIError as exc:
            self._json({"error": str(exc)}, exc.status)

    do_HEAD = do_GET

    def _serve_static(self, path):
        root = self.server.static_root
        name = "index.html" if path == "/" else path.lstrip("/")
        try:
            candidate = (root / name).resolve()
            if not candidate.is_relative_to(root) or not candidate.is_file():
                raise FileNotFoundError(name)
            content = candidate.read_bytes()
        except (OSError, RuntimeError) as exc:
            message = "Page unavailable. Build the web interface to serve it here." if path == "/" else "File unavailable."
            raise _APIError(404, message) from exc
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self._send(content, content_type=content_type)


def _server(root, host, port, node, node_modules, catalog, *, oracle=False, oracle_evidence=None, oracle_dated_closes=False):
    from shutil import which

    if bool(node) != bool(node_modules):
        raise ValueError("Configure both the Node executable and module directory to enable guided runs.")
    if node is not None and (not node.is_file() or not node_modules.is_dir()):
        raise ValueError("Guided runtime paths must point to a Node executable and module directory.")
    if oracle and host != "127.0.0.1" and not environ.get("RENDER_EXTERNAL_HOSTNAME"):
        raise ValueError("Live generation requires a localhost server.")
    if oracle and node is None and not which("soffice"):
        raise ValueError("Install LibreOffice Calc (soffice on PATH) or configure the Node spreadsheet runtime.")
    service = _ResearchService(root, node, node_modules, catalog, oracle=oracle, oracle_evidence=oracle_evidence,
                               oracle_dated_closes=oracle_dated_closes)
    try:
        server = ThreadingHTTPServer((host, port), _Handler)
    except OSError:
        service.close()
        raise
    server.research = service
    server.static_root = (root / "web" / "dist").resolve()
    return server


def _watch_sources(server, root, stop, reload_requested):
    def stamps():
        result = {}
        for path in (root / "equity_research").glob("*.py"):
            try:
                result[path.name] = path.stat().st_mtime_ns
            except FileNotFoundError:
                continue
        return result

    initial = stamps()
    previous = initial
    idle_checks = 0
    while not stop.wait(1):
        current = stamps()
        if current == initial or current != previous:
            idle_checks = 0
            previous = current
            continue
        with server.research.lock:
            busy = any(row["state"] in ("queued", "running") for row in server.research.runs.values())
            idle_checks = 0 if busy else idle_checks + 1
            if idle_checks < 6:
                continue
            server.research.reloading = True
            reload_requested.set()
        server.shutdown()
        return


def serve_research(root: Path, *, host: str = "127.0.0.1", port: int = 8000,
                   node: Path | None = None, node_modules: Path | None = None,
                   oracle=False, oracle_evidence: Path | None = None, oracle_dated_closes=False,
                   reload_on_change=False, live_only=False) -> bool:
    """Run the recruiter demo server until interrupted."""
    from .web_catalog import build_catalog

    root = root.resolve()
    if live_only and not environ.get("SEC_USER_AGENT", "").strip():
        raise ValueError("Set SEC_USER_AGENT to the project name and contact before starting live research.")
    catalog = {"schema_version": 1, "snapshots": [], "live_only": True} if live_only else build_catalog(root)
    server = _server(root, host, port, node, node_modules, catalog,
                     oracle=oracle or live_only, oracle_evidence=oracle_evidence, oracle_dated_closes=oracle_dated_closes)
    stop, reload_requested = Event(), Event()
    if reload_on_change:
        Thread(target=_watch_sources, args=(server, root, stop, reload_requested), daemon=True).start()
    try:
        with server:
            print(f"Equity Research Builder listening on http://{host}:{server.server_port}", flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.research.close()
    return reload_requested.is_set()

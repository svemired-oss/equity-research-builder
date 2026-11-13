"""Serve dated portfolio research and verified guided runs over a local HTTP API."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from math import isfinite
import json
import logging
import mimetypes
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from urllib.parse import unquote, urlsplit
from uuid import uuid4


_SUMMARY_FIELDS = ("reference_close_usd", "spot_value_usd", "target_price_usd", "total_return", "rating")
_MEDIA = {
    "workbook": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "report": "application/pdf",
}
_CASES = ("bear", "base", "bull")
_MAX_BODY = 16_384
_MAX_PENDING = 4
_MAX_FINISHED = 16
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


def _generate(ticker, as_of, inputs_dir, node, node_modules, case, overrides, filenames):
    if ticker == "FDX":
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
        guided = directory / "assumptions.json"
        guided.write_text(json.dumps({"overrides": overrides}, allow_nan=False), encoding="utf-8")
        workbook, report = (directory / filenames[kind] for kind in _MEDIA)
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


def _download_path(namespace, identity, kind):
    return f"/api/{namespace}/{identity}/downloads/{kind}"


class _ResearchService:
    def __init__(self, root, node, node_modules, catalog):
        from .web_catalog import read_demo_download

        self.inputs_dir = root / "inputs"
        self.node, self.node_modules = node, node_modules
        self.catalog = deepcopy(catalog)
        self.companies = {}
        self.assets = {}
        self.runs = {}
        self.generated_assets = {}
        self.lock = Lock()
        for item in self.catalog["snapshots"]:
            ticker, date_text = item["ticker"], item["as_of"]
            self.companies[ticker] = item
            fields = _GUIDES.get(ticker, ())
            enabled = bool(fields and node and node_modules)
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
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="equity-research")

    def _validate(self, request):
        if not isinstance(request, dict) or request.keys() - {"ticker", "mode", "case", "overrides"}:
            raise _APIError(400, "Send ticker, mode, case and optional overrides as a JSON object.")
        raw_ticker = request.get("ticker")
        ticker = raw_ticker.strip().upper() if isinstance(raw_ticker, str) else ""
        item = self.companies.get(ticker)
        if item is None:
            raise _APIError(400, "Choose a supported ticker from the catalog.")
        mode, case = request.get("mode", "automated"), request.get("case", "base")
        if mode not in ("automated", "guided") or case not in _CASES:
            raise _APIError(400, "Choose automated or guided mode and a bear, base or bull case.")
        overrides = request.get("overrides", [])
        if not isinstance(overrides, list):
            raise _APIError(400, "Send overrides as a JSON array.")
        if mode == "automated":
            if overrides or case != item["case"]:
                raise _APIError(400, "The published automated pair uses the base case. Compare all cases in the catalog.")
            return item, mode, case, overrides
        if not item["guided"]["available"]:
            raise _APIError(400, "Guided workbook and report generation is unavailable for this ticker on this server.")
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
                raise _APIError(400, f"{field['label']} must be between {field['min']:g} and {field['max']:g}.")
            if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 240 or not reason.isprintable():
                raise _APIError(400, "Give each change a one-line reason of 1 to 240 characters.")
            if reason.lstrip().startswith("="):
                raise _APIError(400, "Use plain text for the override reason.")
            seen.add(target)
            cleaned.append({"target": target, "value": value, "reason": reason.strip()})
        return item, mode, case, cleaned

    def create_run(self, request):
        item, mode, case, overrides = self._validate(request)
        ticker = item["ticker"]
        run = {"ticker": ticker, "mode": mode, "case": case, "as_of": item["as_of"],
               "price_date": item["price_date"], "target_date": item["target_date"]}
        if mode == "automated":
            return HTTPStatus.OK, self._published_run(item)
        run_id = uuid4().hex
        with self.lock:
            pending = sum(row["state"] in ("queued", "running") for row in self.runs.values())
            if pending >= _MAX_PENDING:
                raise _APIError(429, "The guided queue is full. Try again after an existing run completes.")
            run.update(id=run_id, state="queued")
            self.runs[run_id] = run
            self.executor.submit(self._finish_run, run_id, item, case, overrides)
            return HTTPStatus.ACCEPTED, deepcopy(run)

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
        filenames = {kind: f"{Path(entry['filename']).stem}_guided_{case}{Path(entry['filename']).suffix}"
                     for kind, entry in item["downloads"].items()}
        try:
            summary, cases, contents = _generate(item["ticker"], date.fromisoformat(item["as_of"]), self.inputs_dir,
                                                 self.node, self.node_modules, case, overrides, filenames)
            downloads = {kind: {
                "filename": filenames[kind], "path": _download_path("runs", run_id, kind),
                "size_bytes": len(content), "sha256": sha256(content).hexdigest(),
            } for kind, content in contents.items()}
            outcome = {"state": "completed", "summary": summary, "cases": cases, "downloads": downloads}
        except Exception:
            _LOG.exception("Guided research run %s failed", run_id)
            contents = None
            outcome = {"state": "failed", "error": "The guided model could not produce a verified pair. "
                       "Try changes closer to the published assumptions."}
        with self.lock:
            self.runs[run_id].update(outcome)
            if contents is not None:
                self.generated_assets[run_id] = contents
            finished = [key for key, row in self.runs.items() if row["state"] in ("completed", "failed")]
            for oldest in finished[:-_MAX_FINISHED]:
                self.runs.pop(oldest)
                self.generated_assets.pop(oldest, None)

    def get_run(self, run_id):
        if run_id.endswith("-automated"):
            item = self.companies.get(run_id.removesuffix("-automated").upper())
            if item is not None and run_id == f"{item['ticker'].lower()}-automated":
                return self._published_run(item)
        with self.lock:
            run = self.runs.get(run_id)
            if run is not None:
                return deepcopy(run)
        raise _APIError(404, "Research run unavailable.")

    def get_asset(self, namespace, identity, kind):
        if kind in _MEDIA and namespace == "companies":
            company = self.companies.get(identity)
            if company is not None:
                return company["downloads"][kind]["filename"], self.assets[identity][kind]
        if kind in _MEDIA and namespace == "runs":
            with self.lock:
                contents = self.generated_assets.get(identity)
                if contents is not None:
                    return self.runs[identity]["downloads"][kind]["filename"], contents[kind]
        raise _APIError(404, "Download unavailable.")

    def close(self):
        self.executor.shutdown(wait=True, cancel_futures=True)


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
        if filename:
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(content)

    def _json(self, value, status=200):
        self._send(json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8"), status=status)

    def _path(self):
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
            if parts == ["api", "companies"]:
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


def _server(root, host, port, node, node_modules, catalog):
    if bool(node) != bool(node_modules):
        raise ValueError("Configure both the Node executable and module directory to enable guided runs.")
    if node is not None and (not node.is_file() or not node_modules.is_dir()):
        raise ValueError("Guided runtime paths must point to a Node executable and module directory.")
    service = _ResearchService(root, node, node_modules, catalog)
    try:
        server = ThreadingHTTPServer((host, port), _Handler)
    except OSError:
        service.close()
        raise
    server.research = service
    server.static_root = (root / "web" / "dist").resolve()
    return server


def serve_research(root: Path, *, host: str = "127.0.0.1", port: int = 8000,
                   node: Path | None = None, node_modules: Path | None = None) -> None:
    """Run the recruiter demo server until interrupted."""
    from .web_catalog import build_catalog

    root = root.resolve()
    server = _server(root, host, port, node, node_modules, build_catalog(root))
    try:
        with server:
            print(f"Equity Research Builder listening on http://{host}:{server.server_port}", flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.research.close()

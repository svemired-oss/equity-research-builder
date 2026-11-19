"""Check HTTP routes, guided validation and result isolation without binding a network port."""

from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch
from urllib.error import URLError

from equity_research.web_api import _Handler, _ResearchService


ROOT = Path(__file__).resolve().parents[1]


def _http(service, raw):
    stream = BytesIO()

    class Handler(_Handler):
        def setup(self):
            self.rfile = BytesIO(raw)
            self.wfile = stream

        def finish(self):
            pass

        def log_message(self, *_):
            pass

    header, separator, body = raw.partition(b"\r\n\r\n")
    if b"\r\nhost:" not in header.lower():
        raw = header + b"\r\nHost: localhost:8053" + separator + body
    Handler(None, ("127.0.0.1", 0), SimpleNamespace(research=service, static_root=ROOT / "web" / "dist", server_port=8053))
    headers, _, content = stream.getvalue().partition(b"\r\n\r\n")
    return int(headers.split(b" ")[1]), headers, content


def _post(service, value):
    body = json.dumps(value).encode()
    return _http(service, b"POST /api/runs HTTP/1.0\r\nContent-Type: application/json\r\nContent-Length: "
                 + str(len(body)).encode() + b"\r\n\r\n" + body)


class HostedJobTests(TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def service(self):
        with patch.dict("os.environ", {"SEC_USER_AGENT": "Builder test contact@example.com"}):
            return _ResearchService(self.root, None, None,
                                    {"snapshots": [], "schema_version": 1, "live_only": True}, oracle=True)

    def test_duplicate_lost_response_and_download_survive_restart_until_expiry(self):
        service = self.service()
        gate = Event()
        payload = {"ticker": "XTKS:7974", "request_id": "1" * 32}
        files = {"workbook": b"workbook", "report": b"report"}

        def complete(run_id, _):
            if gate.wait(5):
                service._save_result(run_id, {"workbook": "test.xlsx", "report": "test.pdf"}, {}, [], files)

        with patch.object(service, "_finish_exchange", side_effect=complete) as finish:
            try:
                # Ignore the first POST response, then replay with the same ID.
                self.assertEqual(_post(service, payload)[0], 202)
                _, _, body = _post(service, payload)
                job_id = json.loads(body)["id"]
                _, _, body = _post(service, {**payload, "request_id": "2" * 32})
                self.assertEqual(json.loads(body)["id"], job_id)
                self.assertEqual(_post(service, {**payload, "ticker": "AAPL"})[0], 409)
            finally:
                gate.set()
                service.close()
            self.assertEqual(finish.call_count, 1)
        reopened = self.service()
        try:
            status, _, body = _post(reopened, {**payload, "request_id": "2" * 32})
            self.assertEqual(status, 200)
            run = json.loads(body)
            self.assertEqual(run["id"], job_id)
            for kind, meta in run["downloads"].items():
                status, _, content = _http(reopened, f"GET {meta['path']} HTTP/1.0\r\n\r\n".encode())
                self.assertEqual((status, content), (200, files[kind]))
            from datetime import datetime
            expiry = datetime.fromisoformat(run["expires_at"]).timestamp()
            with patch("equity_research.web_api.time", return_value=expiry + 1):
                self.assertEqual(_http(reopened, f"GET /api/runs/{job_id} HTTP/1.0\r\n\r\n".encode())[0], 404)
                self.assertEqual(_http(reopened, f"GET {meta['path']} HTTP/1.0\r\n\r\n".encode())[0], 404)
        finally:
            reopened.close()

    def test_restart_marks_unfinished_job_and_retains_quota(self):
        service = self.service()
        from time import time
        service.jobs.admit({"id": "4" * 32, "ticker": "XTKS:7974", "state": "queued"}, time(), "3" * 32, "test")
        service.close()
        reopened = self.service()
        self.addCleanup(reopened.close)
        failed = reopened.get_run("4" * 32)
        self.assertEqual(failed["state"], "failed")
        self.assertIn("restarted", failed["error"])
        with patch("equity_research.web_api._RUN_LIMITS", ((3600, 1, "hour"),)):
            status, _, content = _post(reopened, {"ticker": "XTKS:7974"})
        self.assertEqual(status, 429)
        self.assertIn(b"1 new runs per hour", content)
        self.assertIn(b"Try again in 60 min", content)

    def test_screening_only_records_configuration_without_claiming_model_failure(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY": "", "APCA_API_KEY_ID": "", "APCA_API_SECRET_KEY": ""}):
            service = self.service()
        self.addCleanup(service.close)
        self.assertFalse(service.catalog["operating_generation"])
        with patch("equity_research.web_api._screen_ticker", return_value={"annual_periods": 5}):
            _, _, body = _post(service, {"ticker": "DHR"})
            service.executor.submit(lambda: None).result(timeout=5)
        run = service.get_run(json.loads(body)["id"])
        self.assertEqual((run["source_mode"], run["state"]), ("filing_screen", "completed"))
        self.assertNotIn("downloads", run)

    def test_host_accepts_contact_email_without_exposing_it_in_catalog(self):
        with patch.dict("os.environ", {"SEC_USER_AGENT": "test@example.com"}):
            service = _ResearchService(self.root, None, None, {"snapshots": [], "live_only": True}, oracle=True)
        self.addCleanup(service.close)
        self.assertEqual(service.contact, "Equity Research Builder test@example.com")
        self.assertNotIn("test@example.com", json.dumps(service.catalog))

    def test_source_outage_and_export_failure_have_no_downloads(self):
        service = self.service()
        self.addCleanup(service.close)
        with patch("equity_research.listing_directory.resolve_jpx", side_effect=URLError("unavailable")):
            _, _, body = _post(service, {"ticker": "XTKS:7974"})
            service.executor.submit(lambda: None).result(timeout=5)
        run = service.get_run(json.loads(body)["id"])
        self.assertEqual(run["state"], "failed")
        self.assertIn("market-data source is unavailable", run["error"])
        self.assertNotIn("downloads", run)
        with (patch("equity_research.listing_directory.resolve_jpx", return_value={"market_segment": "Prime"}),
              patch("equity_research.tokyo_market.collect_jpx_close"),
              patch("equity_research.nintendo_valuation.prepare_nintendo"),
              patch("equity_research.nintendo_valuation.export_nintendo", side_effect=RuntimeError("failed export"))):
            _, _, body = _post(service, {"ticker": "XTKS:7974"})
            service.executor.submit(lambda: None).result(timeout=5)
        run = service.get_run(json.loads(body)["id"])
        self.assertEqual(run["state"], "failed")
        self.assertIn("No files were published", run["error"])
        self.assertNotIn("downloads", run)


class ResearchWebAPITests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = json.loads((ROOT / "tests/fixtures/web_catalog.json").read_text())
        cls.contents = {"workbook": b"synthetic workbook bytes", "report": b"synthetic report bytes"}
        for item in cls.catalog["snapshots"]:
            item["downloads"] = {kind: {
                "filename": item["ticker"] + (".xlsx" if kind == "workbook" else ".pdf"),
                "size_bytes": len(content), "sha256": sha256(content).hexdigest(),
            } for kind, content in cls.contents.items()}
        cls.downloads_patch = patch("equity_research.web_catalog.read_demo_download",
            side_effect=lambda root, ticker, kind: cls.contents[kind])
        cls.downloads_patch.start()
        cls.addClassCleanup(cls.downloads_patch.stop)
        from equity_research.web_api import _GUIDES
        cls.defaults_patch = patch("equity_research.web_api._defaults",
            side_effect=lambda ticker, *_: {field.target: (field.min + field.max) / 2 for field in _GUIDES[ticker]})
        cls.defaults_patch.start()
        cls.addClassCleanup(cls.defaults_patch.stop)
        cls.service = _ResearchService(ROOT, Path("node"), Path("node_modules"), cls.catalog)

    @classmethod
    def tearDownClass(cls):
        cls.service.close()

    def test_automated_catalog_and_download_use_verified_bytes(self):
        status, _, body = _http(self.service, b"GET /api/companies HTTP/1.0\r\n\r\n")
        self.assertEqual(status, 200)
        gd = next(item for item in json.loads(body)["snapshots"] if item["ticker"] == "GD")
        self.assertFalse(gd["guided"]["available"])
        status, _, body = _post(self.service, {"ticker": " gd "})
        self.assertEqual(status, 200)
        run = json.loads(body)
        self.assertEqual(run["state"], "completed")
        for kind, artifact in run["downloads"].items():
            path = artifact["path"].encode()
            status, headers, content = _http(self.service, b"GET " + path + b" HTTP/1.0\r\n\r\n")
            self.assertEqual(status, 200)
            self.assertEqual(artifact["sha256"], sha256(content).hexdigest())
            self.assertIn(artifact["filename"].encode(), headers)
            status, headers, content = _http(self.service, b"HEAD " + path + b" HTTP/1.0\r\n\r\n")
            self.assertEqual(status, 200)
            self.assertEqual(content, b"", kind)

    def test_validation_and_path_confinement(self):
        for request in (
            {"ticker": "GD", "mode": "guided"},
            {"ticker": "FDX", "mode": "automated", "case": "bull"},
            {"ticker": "FDX", "mode": "guided", "overrides": [{"target": "us_ground.yield_growth", "value": True, "reason": "Invalid."}]},
            {"ticker": "FDX", "mode": "guided", "overrides": [{"target": "us_ground.yield_growth", "value": 0.05, "reason": " =1+1"}]},
        ):
            status, _, body = _post(self.service, request)
            self.assertEqual(status, 400, body)
        for path in (b"/../../inputs/private.json", b"/%2e%2e/README.md", b"/api/companies/GD/downloads/%2e%2e%2fREADME.md"):
            status, _, _ = _http(self.service, b"GET " + path + b" HTTP/1.0\r\n\r\n")
            self.assertEqual(status, 404, path)

    def test_private_builder_rejects_nonlocal_hosts_and_cross_site_requests(self):
        service = _ResearchService(ROOT, Path("node"), Path("node_modules"), self.service.catalog, oracle=True)
        self.addCleanup(service.close)
        for headers, expected in (
            (b"Host: localhost:8053", 200),
            (b"Host: research.example:8053", 421),
            (b"Host: localhost:8053\r\nHost: example.org", 421),
            (b"Host: localhost:8053\r\nSec-Fetch-Site: cross-site", 403),
        ):
            with self.subTest(headers=headers):
                status, _, _ = _http(service, b"GET /api/companies HTTP/1.0\r\n" + headers + b"\r\n\r\n")
                self.assertEqual(status, expected)

    def test_guided_run_exposes_only_its_paired_downloads(self):
        service = _ResearchService(ROOT, Path("node"), Path("node_modules"), self.service.catalog)
        fake_files = {"workbook": b"paired workbook", "report": b"paired report"}
        summary = {"target_price_usd": 61.12}
        try:
            cases = [{"case": "base", "target_price_usd": 61.12}]
            with patch("equity_research.web_api._generate", return_value=(summary, cases, fake_files)) as generate:
                status, _, body = _post(service, {"ticker": "NEM", "mode": "guided",
                    "overrides": [{"target": "fy_2027.gold.benchmark_usd", "value": 4450, "reason": "Gold price sensitivity."}]})
                self.assertEqual(status, 202, body)
                run_id = json.loads(body)["id"]
                service.executor.submit(lambda: None).result(timeout=5)
                self.assertEqual(generate.call_args.args[-2][0]["value"], 4450)
            status, _, body = _http(service, f"GET /api/runs/{run_id} HTTP/1.0\r\n\r\n".encode())
            self.assertEqual(status, 200)
            run = json.loads(body)
            self.assertEqual(run["state"], "completed")
            self.assertEqual(run["summary"], summary)
            self.assertEqual(run["cases"], cases)
            for kind, artifact in run["downloads"].items():
                status, _, content = _http(service, f"GET {artifact['path']} HTTP/1.0\r\n\r\n".encode())
                self.assertEqual(status, 200)
                self.assertEqual(content, fake_files[kind])
        finally:
            service.close()

    def test_oracle_pilot_reports_missing_live_inputs_without_downloads(self):
        service = _ResearchService(ROOT, Path("node"), Path("node_modules"), self.service.catalog, oracle=True)
        self.addCleanup(service.close)
        status, _, body = _post(self.service, {"ticker": "ORCL"})
        self.assertEqual(status, 400, body)
        status, _, body = _http(service, b"GET /api/companies HTTP/1.0\r\n\r\n")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["generation"]["source_mode"], "live_collection")
        with patch.dict("os.environ", {"SEC_USER_AGENT": "Equity Research Test contact@example.com",
                                      "APCA_API_KEY_ID": "", "APCA_API_SECRET_KEY": ""}):
            status, _, body = _post(service, {"ticker": " orcl "})
            self.assertEqual(status, 202, body)
            run_id = json.loads(body)["id"]
            service.executor.submit(lambda: None).result(timeout=5)
        status, _, body = _http(service, f"GET /api/runs/{run_id} HTTP/1.0\r\n\r\n".encode())
        self.assertEqual(status, 200)
        run = json.loads(body)
        self.assertEqual(run["state"], "failed")
        self.assertIn("Alpaca credentials", run["error"])
        self.assertNotIn("downloads", run)

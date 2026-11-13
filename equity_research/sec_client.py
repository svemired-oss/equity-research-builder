"""Read SEC EDGAR JSON and filing documents with a declared contact."""

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from http.client import IncompleteRead
import json
import re
from threading import Lock
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


_SEC_HOSTS = {"data.sec.gov", "www.sec.gov", "sec.gov"}
_REQUEST_INTERVAL = 0.2
_TIMEOUT = 12
_ATTEMPTS = 3
_MAX_RETRY_DELAY = 10
_MAX_RESPONSE_BYTES = 32 * 1024 * 1024


class SECError(ValueError):
    """The SEC data request could not be completed."""


class SECHTTPError(SECError):
    """EDGAR returned an unsuccessful HTTP response."""

    def __init__(self, status: int, url: str):
        self.status = status
        self.url = url
        super().__init__(f"SEC returned HTTP {status} for {url}.")


def _cik(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{1,10}", value) or int(value) == 0:
        raise ValueError("CIK must contain one to ten digits and identify a nonzero filer.")
    return value.zfill(10)


def _accession(value: str) -> str:
    if isinstance(value, str) and re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}", value):
        return value
    if isinstance(value, str) and re.fullmatch(r"[0-9]{18}", value):
        return f"{value[:10]}-{value[10:12]}-{value[12:]}"
    raise ValueError("Invalid EDGAR accession number.")


def filing_document_url(cik: str, accession: str, filename: str) -> str:
    """Build the canonical Archives URL for a filename in an EDGAR filing."""
    if not isinstance(filename, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", filename):
        raise ValueError("Filing filename must be a single EDGAR filename.")
    directory = _accession(accession).replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{int(_cik(cik))}/{directory}/{filename}"


def filing_index_url(cik: str, accession: str) -> str:
    """Build the GD filing detail page URL with its exhibit table."""
    return filing_document_url(cik, accession, f"{_accession(accession)}-index.htm")


class _NoRedirects(HTTPRedirectHandler):
    # Canonical endpoints work directly and keep each request inside the pacing limit.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _retry_delay(header: str | None, attempt: int) -> float:
    backoff = 0.5 * 2**attempt
    if header:
        try:
            return max(backoff, int(header))
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(header)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                return max(backoff, (retry_at - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                pass
    return backoff


class SECClient:
    """Client for public SEC endpoints. Use one instance for a collection run."""

    def __init__(self, user_agent: str):
        if not isinstance(user_agent, str):
            raise ValueError("SEC User-Agent must be text.")
        self.user_agent = user_agent.strip()
        contact = re.search(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", self.user_agent)
        if (
            not contact
            or not self.user_agent[: contact.start()].strip()
            or not self.user_agent.isascii()
            or any(ord(character) < 32 or ord(character) == 127 for character in user_agent)
        ):
            raise ValueError("SEC User-Agent must contain an application or organization name and contact email.")
        self._opener = build_opener(_NoRedirects())
        self._pacing_lock = Lock()
        self._last_request = time.monotonic() - _REQUEST_INTERVAL

    def submissions(self, cik: str) -> dict:
        return self.get_json(f"https://data.sec.gov/submissions/CIK{_cik(cik)}.json")

    def company_facts(self, cik: str) -> dict:
        return self.get_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{_cik(cik)}.json")

    def get_json(self, url: str) -> dict:
        body, _ = self._request(url)
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeError) as exc:
            raise SECError(f"SEC returned invalid JSON for {url}.") from exc
        if not isinstance(payload, dict):
            raise SECError(f"SEC returned an unexpected JSON shape for {url}.")
        return payload

    def get_text(self, url: str) -> str:
        body, encoding = self._request(url)
        try:
            return body.decode(encoding, errors="replace")
        except LookupError as exc:
            raise SECError(f"SEC returned an unsupported text encoding for {url}.") from exc

    def _pace(self) -> None:
        with self._pacing_lock:
            delay = self._last_request + _REQUEST_INTERVAL - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self._last_request = time.monotonic()

    def _request(self, url: str) -> tuple[bytes, str]:
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.netloc.lower() not in _SEC_HOSTS:
            raise ValueError("EDGAR requests require a canonical SEC HTTPS host.")
        request = Request(url, headers={"User-Agent": self.user_agent, "Accept-Encoding": "identity"})
        for attempt in range(_ATTEMPTS):
            self._pace()
            try:
                with self._opener.open(request, timeout=_TIMEOUT) as response:
                    body = response.read(_MAX_RESPONSE_BYTES + 1)
                    if len(body) > _MAX_RESPONSE_BYTES:
                        raise SECError(f"SEC response exceeded the size limit for {url}.")
                    return body, response.headers.get_content_charset() or "utf-8"
            except HTTPError as exc:
                try:
                    delay = _retry_delay(exc.headers.get("Retry-After") if exc.headers else None, attempt)
                    transient = exc.code == 429 or 500 <= exc.code < 600
                    if not transient or attempt == _ATTEMPTS - 1 or delay > _MAX_RETRY_DELAY:
                        raise SECHTTPError(exc.code, url) from exc
                finally:
                    exc.close()
            except (URLError, TimeoutError, ConnectionError, IncompleteRead) as exc:
                reason = exc.reason if isinstance(exc, URLError) else exc
                if not isinstance(reason, (TimeoutError, ConnectionError, IncompleteRead)) or attempt == _ATTEMPTS - 1:
                    raise SECError(f"Could not retrieve SEC data for {url}: {reason}") from exc
                delay = _retry_delay(None, attempt)
            time.sleep(delay)

        raise RuntimeError("EDGAR request attempts exhausted.")

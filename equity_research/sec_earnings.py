"""Discover dated earnings exhibits from issuer-specific SEC filing indexes."""

from dataclasses import dataclass
from html import unescape
import re
from urllib.parse import urljoin, urlsplit

from .gd_releases import TextClient, _Page
from .sec_client import filing_document_url, filing_index_url


_EXHIBIT = re.compile(r"EX-99(?:\.\d+)?$")
_HTML_FILE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*\.html?$", re.IGNORECASE)
_ITEMS = re.compile(r"[,;\s]+")
_RESULTS = re.compile(
    r"\b(?:reports?|announces?|releases?|publishes?)\b[^.!?]{0,95}\b"
    r"(?:(?:financial|operating|quarterly|annual)\s+results|"
    r"(?:quarter|half(?:[- ]year)?|full[- ]year|year[- ]end|Q[1-4])\b[^.!?]{0,38}\b(?:results|earnings)|"
    r"earnings\s+(?:results|for))\b"
    r"|\b(?:quarterly|annual|financial|quarter|half[- ]year|full[- ]year|year[- ]end|Q[1-4])"
    r"\s+(?:\d{4}\s+)?(?:financial\s+)?(?:results|earnings\s+release)\b",
    re.IGNORECASE,
)
_SCHEDULE = re.compile(
    r"\b(?:will\s+(?:report|release|announce|publish)|to\s+(?:report|release|announce|publish)|"
    r"(?:date|schedule|notice)\s+(?:for|of)|conference\s+call\s+(?:notice|details))\b",
    re.IGNORECASE,
)
_TITLE = re.compile(r"<title\b[^>]*>([^<]{1,250})</title\s*>", re.IGNORECASE)
_HEADLINE = re.compile(r"<(?P<tag>h[1-4]|p)\b[^>]*>(?P<text>.{1,2000}?)</(?P=tag)\s*>", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class _Exhibit:
    url: str
    description: str
    form: str


def _result_words(text: str) -> bool:
    return bool(_RESULTS.search(text) and not _SCHEDULE.search(text))


def _index_exhibits(client: TextClient, cik: str, accession: str) -> tuple[_Exhibit, ...]:
    index_url = filing_index_url(cik, accession)
    directory = urlsplit(index_url).path.rpartition("/")[0] + "/"
    candidates = {}
    for table in _Page(client.get_text(index_url)).tables:
        for row in table.rows:
            if len(row) != 5 or len(row[2].links) != 1:
                continue
            exhibit_form = row[3].text.upper()
            if not _EXHIBIT.fullmatch(exhibit_form):
                continue
            try:
                link = urlsplit(urljoin(index_url, row[2].links[0]))
            except ValueError:
                continue
            if (link.scheme, link.netloc, link.query, link.fragment) != ("https", "www.sec.gov", "", ""):
                continue
            if not link.path.startswith(directory):
                continue
            filename = link.path.removeprefix(directory)
            if not _HTML_FILE.fullmatch(filename):
                continue
            url = filing_document_url(cik, accession, filename)
            item = _Exhibit(url, row[1].text[:250], exhibit_form)
            prior = candidates.get(url)
            if prior is None or _result_words(item.description):
                candidates[url] = item
    return tuple(sorted(candidates.values(), key=lambda item: (
        not _result_words(item.description), item.form != "EX-99.1", item.form, item.url,
    )))


def _read_exhibit(client: TextClient, exhibit: _Exhibit) -> tuple[str | None, bool, bool]:
    document = client.get_text(exhibit.url)
    opening = document[:128_000]
    leading = _Page(opening).text[:1400]
    if not leading or re.match(r"(?:403 Forbidden|404 Not Found|Access Denied|Your Request Originates)\b", leading, re.I):
        raise ValueError("The SEC exhibit returned no readable release content.")
    raw_title = _TITLE.search(document[:16_000])
    title = " ".join(unescape(raw_title.group(1)).split()) if raw_title else None
    if _SCHEDULE.search((title or "") + " " + exhibit.description):
        return title, False, True
    if title and _result_words(title):
        return title, True, False
    scheduled_headline = False
    for heading in _HEADLINE.finditer(opening[:40_000]):
        text = _Page(heading["text"]).text
        if len(text) <= 250 and _result_words(text):
            return text, True, False
        if len(text) <= 250 and _SCHEDULE.search(text):
            scheduled_headline = True
    if _result_words(exhibit.description) and not scheduled_headline:
        return exhibit.description, True, False
    match = _RESULTS.search(leading)
    if match:
        # Select the observed results phrase instead of copying the release body.
        context = leading[max(0, match.start() - 45):match.end() + 30]
        if not _SCHEDULE.search(context):
            return match.group(0), True, False
    schedule = scheduled_headline or bool(_SCHEDULE.search(leading[:420]))
    return title, False, schedule


def _earnings_filing(filing):
    if filing["form"] == "6-K":
        return True
    return filing["form"] == "8-K" and "2.02" in _ITEMS.split(filing.get("items") or "")


def collect_earnings_releases(client: TextClient, cik: str, filings, *, limit: int = 4) -> dict:
    """Return verified earnings exhibit links from a bounded set of dated filings."""
    if type(limit) is not int or not 1 <= limit <= 10:
        raise ValueError("Earnings exhibit limit must be between one and ten.")
    candidates = sorted(
        (filing for filing in filings if _earnings_filing(filing)),
        key=lambda filing: (str(filing["filed_on"]), filing["accession"]), reverse=True,
    )
    releases, missing, seen = [], [], set()
    for filing in candidates[:limit * 2]:
        accession = filing["accession"]
        if accession in seen:
            continue
        seen.add(accession)
        try:
            exhibits = _index_exhibits(client, cik, accession)
        except (ValueError, OSError):
            missing.append(f"{accession}: SEC filing index unavailable.")
            continue
        confirmed = []
        for exhibit in exhibits[:2]:
            try:
                title, explicit, schedule = _read_exhibit(client, exhibit)
            except (ValueError, OSError):
                missing.append(f"{accession}: SEC exhibit unavailable or unreadable.")
                continue
            item_202 = filing["form"] == "8-K" and len(exhibits) == 1 and exhibit.form in ("EX-99.1", "EX-99")
            if not schedule and (explicit or item_202):
                evidence = "Earnings results wording" if explicit else "Form 8-K Item 2.02 and one same-filing earnings exhibit"
                confirmed.append((exhibit.url, title or exhibit.description or exhibit.form, evidence))
        if len(confirmed) != 1:
            if filing["form"] == "8-K" or confirmed:
                reason = "multiple exhibits identify earnings results" if confirmed else "no verified earnings release exhibit"
                missing.append(f"{accession}: {reason}.")
            continue
        url, title, evidence = confirmed[0]
        releases.append({
            "form": filing["form"], "accession": accession, "filed_on": filing["filed_on"],
            "filing_report_date": filing.get("period_end"), "url": url, "title": title,
            "filing_url": filing_index_url(cik, accession), "evidence": evidence,
        })
        if len(releases) == limit:
            break
    if not releases:
        missing.append("No verifiable SEC earnings release was found in the eligible recent 8-K or 6-K filings.")
    return {"releases": releases, "missing": list(dict.fromkeys(missing))}

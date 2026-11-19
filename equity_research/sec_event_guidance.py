"""Identify potential guidance passages in verified SEC event exhibits."""

from datetime import date, timedelta
import re
from urllib.parse import urlsplit

from .sec_client import filing_document_url
from .sec_earnings import _index_exhibits
from .sec_guidance import _ReleaseText


_MAX_EXHIBIT_CHARS = 2_000_000
_MAX_PASSAGES = 20
_PASSAGE_LENGTH = 650
_ITEMS = re.compile(r"[,;\s]+")
_FISCAL_PERIOD = re.compile(r"\b(?:FY[ -]*(?:20)?\d{2}|fiscal(?:\s+year)?[ -]+20\d{2})\b", re.I)
_FORWARD = re.compile(
    r"\b(?:guidance|outlook|targets?|forecast\w*|expect\w*|project(?:s|ed|ing)|anticipat\w*|"
    r"plans?\s+to|commit\w*\s+to|will\s+be|reaffirm\w*|positive\s+free\s+cash\s+flow|peak\s+capex)\b",
    re.I,
)
_BOILERPLATE = re.compile(
    r"^(?:safe\s+harbor(?:\s+statements?)?|forward[ -]+looking\s+statements?|"
    r"cautionary\s+(?:note|statement)\b.*|(?:use of )?non[ -]*GAAP financial measures(?: and .*?)?)\s*:?$",
    re.I,
)
_LEGAL_PREAMBLE = re.compile(
    r"^(?:this\s+(?:press\s+)?release\s+(?:contains|includes)|"
    r"statements\s+in\s+this\s+(?:press\s+)?release\b).*"
    r"\b(?:forward[ -]+looking statements?|material risks and uncertainties)\b",
    re.I,
)
_METRICS = {
    "revenue": re.compile(r"\b(?:revenues?|sales)\b", re.I),
    "operating_margin": re.compile(r"\boperating margins?\b", re.I),
    "operating_income": re.compile(r"\boperating (?:income|profit)\b", re.I),
    "earnings_per_share": re.compile(r"\b(?:earnings per share|EPS)\b", re.I),
    "capital_expenditures": re.compile(r"\b(?:capital expenditures?|capex)\b", re.I),
    "free_cash_flow": re.compile(r"\b(?:free cash flow|FCF)\b", re.I),
    "financing": re.compile(r"\b(?:capital raises?|capital markets|funding|financing|gross debt)\b", re.I),
    "leverage": re.compile(r"\bnet debt to (?:adjusted )?EBITDA\b", re.I),
    "backlog": re.compile(r"\b(?:backlog|remaining performance obligations?|RPO)\b", re.I),
}
_LIMITATIONS = (
    "The caller verifies the exhibit in the issuer's SEC filing index and supplies its filing date.",
    "Passages are evidence candidates. Fiscal years, financial definitions, qualifiers, and units require validation before modeling.",
    "The parser looks for forward guidance language and explicit fiscal years in visible prose. "
    "Guidance in linked decks, graphics, or disconnected table cells may be missed.",
)


class _EventText(_ReleaseText):
    # EDGAR exhibits often place narrative bullet points in table cells.
    _CONTAINERS = {"table", "tr", "td", "th"}
    _HIDDEN = {"nav", "footer", "ix:header"}

    @classmethod
    def _tag(cls, tag):
        if tag in cls._CONTAINERS:
            return "div"
        if tag in cls._HIDDEN:
            return "script"
        return tag

    def handle_starttag(self, tag, attrs):
        super().handle_starttag(self._tag(tag), attrs)

    def handle_endtag(self, tag):
        super().handle_endtag(self._tag(tag))


def _verify_url(url, cik, accession):
    path = urlsplit(url).path
    filename = path.rsplit("/", 1)[-1]
    if not filename.lower().endswith((".htm", ".html")) or url != filing_document_url(cik, accession, filename):
        raise ValueError("Event guidance requires an HTML URL for the same issuer and SEC accession.")


def extract_event_guidance(html: str, *, url: str, cik: str, accession: str, filed_on: date, as_of: date) -> dict:
    """Return dated excerpts without converting them to forecast assumptions or fetching URLs."""
    if type(filed_on) is not date or type(as_of) is not date:
        raise ValueError("Event guidance requires a filing date and cutoff date.")
    _verify_url(url, cik, accession)
    result = {"status": "missing", "observations": [], "missing": [], "limitations": list(_LIMITATIONS)}
    if filed_on > as_of:
        result["missing"].append("The exhibit was filed after the valuation cutoff.")
        return result
    if len(html) > _MAX_EXHIBIT_CHARS:
        result["missing"].append("The exhibit exceeds the guidance parser's size limit.")
        return result
    seen = set()
    for block in _EventText(html).blocks:
        if _BOILERPLATE.match(block) or _LEGAL_PREAMBLE.match(block):
            break
        if len(block) > _PASSAGE_LENGTH or not (_FISCAL_PERIOD.search(block) and _FORWARD.search(block)):
            continue
        metrics = [metric for metric, pattern in _METRICS.items() if pattern.search(block)]
        if not metrics or block in seen:
            continue
        if len(result["observations"]) == _MAX_PASSAGES:
            result["limitations"].append(f"Only the first {_MAX_PASSAGES} distinct passages were retained.")
            break
        seen.add(block)
        result["observations"].append({
            "evidence": block, "metrics": metrics, "url": url, "accession": accession, "filed_on": filed_on.isoformat(),
        })
    if result["observations"]:
        result["status"] = "available"
    else:
        result["missing"].append("No guidance passages with recognized metrics and explicit fiscal years were found.")
    return result


def collect_event_guidance(client, cik: str, filings: list[dict], *, as_of: date) -> dict:
    """Check recent Regulation FD disclosures for same-accession guidance exhibits."""
    earliest = as_of - timedelta(days=365)
    candidates = [
        filing for filing in filings
        if filing["form"] in {"8-K", "8-K/A"}
        and "7.01" in _ITEMS.split(filing["items"])
        and earliest.isoformat() <= filing["filed_on"] <= as_of.isoformat()
    ]
    candidates.sort(key=lambda filing: (filing["filed_on"], filing["accession"]), reverse=True)
    observations, missing = [], []
    for filing in candidates[:4]:
        accession = filing["accession"]
        filed_on = date.fromisoformat(filing["filed_on"])
        try:
            exhibits = _index_exhibits(client, cik, accession)
        except (ValueError, OSError):
            missing.append(f"{accession}: SEC event filing index unavailable.")
            continue
        for exhibit in exhibits[:2]:
            try:
                result = extract_event_guidance(client.get_text(exhibit.url), url=exhibit.url,
                                                cik=cik, accession=accession, filed_on=filed_on, as_of=as_of)
            except (ValueError, OSError):
                missing.append(f"{accession}: SEC event exhibit unavailable or unreadable.")
                continue
            observations.extend(result["observations"])
            missing.extend(f"{accession}: {issue}" for issue in result["limitations"][len(_LIMITATIONS):])
    if not observations:
        missing.append("No guidance passages were found in the selected SEC event filings.")
    return {
        "status": "available" if observations else "missing",
        "observations": observations,
        "missing": missing,
        "limitations": [
            *_LIMITATIONS,
            "Search covers up to four recent Form 8-K Item 7.01 filings in the past year and two HTML exhibits per filing.",
        ],
    }

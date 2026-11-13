"""Read numeric facts from an SEC filing's extracted XBRL instance."""

from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from io import StringIO
from math import isfinite
import re
from typing import Protocol
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree as ET

from .sec_client import SECError, filing_document_url, filing_index_url


_INSTANCE = "{http://www.xbrl.org/2003/instance}"
_DIMENSION = "{http://xbrl.org/2006/xbrldi}"
_ISO4217 = "{http://www.xbrl.org/2003/iso4217}"
_XSI = "{http://www.w3.org/2001/XMLSchema-instance}"
_EXTRACTED_INSTANCE = "extracted xbrl instance document"
_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")


class TextClient(Protocol):
    def get_text(self, url: str) -> str: ...


@dataclass(frozen=True)
class XbrlFact:
    concept: str
    start: date | None
    end: date
    dimensions: tuple[tuple[str, str], ...]
    unit: str
    value: float


@dataclass(frozen=True)
class FilingInstance:
    url: str
    facts: tuple[XbrlFact, ...]


@dataclass(frozen=True)
class _Context:
    start: date | None
    end: date
    dimensions: tuple[tuple[str, str], ...]


class _FilingIndex(HTMLParser):
    """Collect links from the Document column of the extracted-instance row."""

    def __init__(self, html: str):
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self._row: list[list[str]] | None = None
        self._row_links: list[str] = []
        self._cell: list[str] | None = None
        self.feed(html)
        self.close()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
            self._row_links = []
            self._cell = None
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []
            self._row.append(self._cell)
        elif tag == "a" and self._row is not None and len(self._row) == 3 and self._cell is not None:
            href = dict(attrs).get("href")
            if href:
                self._row_links.append(href)

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "tr":
            if self._row is not None and len(self._row) >= 3:
                description = " ".join("".join(self._row[1]).split()).casefold()
                if description == _EXTRACTED_INSTANCE:
                    self.links.extend(self._row_links)
            self._row = None
            self._cell = None
        elif tag in {"td", "th"}:
            self._cell = None


def load_filing_instance(client: TextClient, cik: str, accession: str) -> FilingInstance:
    index_url = filing_index_url(cik, accession)
    index = _FilingIndex(client.get_text(index_url))
    candidates = set()
    for href in index.links:
        try:
            link = urljoin(index_url, href)
            filename = urlsplit(link).path.rsplit("/", 1)[-1]
            if filename.lower().endswith(".xml") and link == filing_document_url(cik, accession, filename):
                candidates.add(link)
        except ValueError:
            continue
    if len(candidates) != 1:
        issue = "Ambiguous" if candidates else "Missing"
        raise SECError(f"{issue} SEC extracted XBRL instance in {index_url}.")
    url = candidates.pop()
    return parse_filing_instance(client.get_text(url), url)


def _canonical(expanded: str, gd_namespace: str | None) -> str:
    if not expanded.startswith("{"):
        return expanded
    namespace, _, local = expanded[1:].partition("}")
    if namespace == gd_namespace:
        return f"gd:{local}"
    try:
        parsed = urlsplit(namespace)
    except ValueError:
        return expanded
    host = (parsed.hostname or "").removeprefix("www.")
    taxonomy = parsed.path.strip("/").partition("/")[0]
    if host == "fasb.org" and taxonomy in {"us-gaap", "srt"}:
        return f"{taxonomy}:{local}"
    if host == "xbrl.sec.gov" and taxonomy in {"dei", "country", "currency", "exch"}:
        return f"{taxonomy}:{local}"
    if host in {"gd.com", "generaldynamics.com"}:
        return f"gd:{local}"
    return expanded


def _expand_qname(text: str, namespaces: dict[str, str]) -> str | None:
    prefix, separator, local = text.strip().partition(":")
    if not separator:
        prefix, local = "", prefix
    if not local or ":" in local or any(character.isspace() for character in local):
        return None
    namespace = namespaces.get(prefix)
    return f"{{{namespace}}}{local}" if namespace else None


def _parse_xml(xml: str, url: str) -> tuple[ET.Element, dict[ET.Element, dict[str, str]], str | None]:
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", xml, re.IGNORECASE):
        raise SECError(f"Unexpected XML type declaration in {url}.")
    namespaces: dict[str, str] = {}
    parents: list[dict[str, str]] = []
    pending: dict[str, str] = {}
    qname_scopes: dict[ET.Element, dict[str, str]] = {}
    gd_namespace = None
    try:
        parsed = ET.iterparse(StringIO(xml), events=("start-ns", "start", "end"))
        for event, item in parsed:
            if event == "start-ns":
                prefix, namespace = item
                pending[prefix] = namespace
            elif event == "start":
                if not parents:
                    gd_namespace = pending.get("gd")
                parents.append(namespaces)
                if pending:
                    namespaces = namespaces | pending
                    pending.clear()
                if item.tag in {f"{_DIMENSION}explicitMember", f"{_INSTANCE}measure"}:
                    qname_scopes[item] = namespaces
            else:
                namespaces = parents.pop()
    except (ET.ParseError, ValueError) as exc:
        raise SECError(f"Invalid extracted XBRL XML for {url}.") from exc
    if parsed.root.tag != f"{_INSTANCE}xbrl":
        raise SECError(f"SEC document is not an XBRL instance at {url}.")
    return parsed.root, qname_scopes, gd_namespace


def _read_context(
    element: ET.Element, scopes: dict[ET.Element, dict[str, str]], gd_namespace: str | None,
    expected_cik: str | None,
) -> _Context | None:
    if expected_cik is not None:
        identifier = element.findtext(f"{_INSTANCE}entity/{_INSTANCE}identifier", "").strip()
        if identifier.lstrip("0") != expected_cik:
            return None
    period = element.find(f"{_INSTANCE}period")
    if period is None:
        return None
    try:
        shape = tuple(child.tag for child in period)
        if shape == (f"{_INSTANCE}instant",):
            start, end = None, date.fromisoformat((period[0].text or "").strip())
        elif shape == (f"{_INSTANCE}startDate", f"{_INSTANCE}endDate"):
            start = date.fromisoformat((period[0].text or "").strip())
            end = date.fromisoformat((period[1].text or "").strip())
            if start > end:
                return None
        else:
            return None
    except ValueError:
        return None

    dimensions = {}
    segment = element.findall(f"{_INSTANCE}entity/{_INSTANCE}segment")
    scenario = element.findall(f"{_INSTANCE}scenario")
    for container in segment + scenario:
        for member in container:
            # A typed or otherwise unsupported qualifier must never become consolidated.
            if member.tag != f"{_DIMENSION}explicitMember":
                return None
            axis = _expand_qname(member.get("dimension", ""), scopes[member])
            value = _expand_qname(member.text or "", scopes[member])
            if axis is None or value is None:
                return None
            name = _canonical(axis, gd_namespace)
            if name in dimensions:
                return None
            dimensions[name] = _canonical(value, gd_namespace)
    return _Context(start, end, tuple(sorted(dimensions.items())))


def _read_measure(element: ET.Element | None, scopes: dict[ET.Element, dict[str, str]]) -> str | None:
    if element is None or len(element) != 1 or element[0].tag != f"{_INSTANCE}measure":
        return None
    measure = element[0]
    name = _expand_qname(measure.text or "", scopes[measure])
    if name in {f"{_INSTANCE}shares", f"{_INSTANCE}pure"}:
        return name.rsplit("}", 1)[-1]
    if name and name.startswith(_ISO4217):
        currency = name[len(_ISO4217):]
        return currency if re.fullmatch(r"[A-Z]{3}", currency) else None
    return None


def _read_unit(element: ET.Element, scopes: dict[ET.Element, dict[str, str]]) -> str | None:
    divide = element.find(f"{_INSTANCE}divide")
    if divide is None:
        return _read_measure(element, scopes)
    numerator = _read_measure(divide.find(f"{_INSTANCE}unitNumerator"), scopes)
    denominator = _read_measure(divide.find(f"{_INSTANCE}unitDenominator"), scopes)
    return f"{numerator}/{denominator}" if numerator and denominator else None


def parse_filing_instance(xml: str, url: str) -> FilingInstance:
    """Parse supported contexts and units, preserving repeated numeric facts."""
    root, scopes, gd_namespace = _parse_xml(xml, url)
    archive = re.match(r"/Archives/edgar/data/([0-9]+)/", urlsplit(url).path)
    expected_cik = archive[1].lstrip("0") if archive else None
    contexts: dict[str, _Context | None] = {}
    units: dict[str, str | None] = {}
    for element in root:
        key = element.get("id")
        if not key:
            continue
        if element.tag == f"{_INSTANCE}context":
            if key in contexts:
                raise SECError(f"Duplicate XBRL context {key!r} in {url}.")
            contexts[key] = _read_context(element, scopes, gd_namespace, expected_cik)
        elif element.tag == f"{_INSTANCE}unit":
            if key in units:
                raise SECError(f"Duplicate XBRL unit {key!r} in {url}.")
            units[key] = _read_unit(element, scopes)

    facts = []
    for element in root:
        context = contexts.get(element.get("contextRef", ""))
        unit = units.get(element.get("unitRef", ""))
        if context is None or unit is None or len(element) or element.get(f"{_XSI}nil") in {"true", "1"}:
            continue
        text = (element.text or "").strip()
        if not _NUMBER.fullmatch(text):
            continue
        value = float(text)
        if isfinite(value):
            facts.append(XbrlFact(
                _canonical(element.tag, gd_namespace), context.start, context.end, context.dimensions, unit, value,
            ))
    return FilingInstance(url, tuple(facts))

"""Discover the latest GD earnings exhibit and read its labeled operating evidence."""

import re
from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser
from typing import Protocol
from urllib.parse import urljoin, urlsplit

from .sec_client import filing_document_url, filing_index_url


GD_CIK = "0000040533"


class TextClient(Protocol):
    def get_text(self, url: str) -> str: ...


@dataclass(frozen=True)
class RecentFiling:
    form: str
    accession: str
    filing_date: date
    report_date: date | None
    items: str
    primary_document: str


@dataclass(frozen=True)
class ReleaseMetric:
    metric: str
    year: int
    value: int
    unit: str
    evidence: str


@dataclass(frozen=True)
class GuidanceEvidence:
    metric: str
    year: int | None
    low: float
    high: float
    unit: str
    evidence: str


@dataclass(frozen=True)
class EarningsRelease:
    filing_date: date
    accession: str
    url: str
    title: str | None
    metrics: tuple[ReleaseMetric, ...]
    guidance: tuple[GuidanceEvidence, ...]
    guidance_note: str


def recent_filings(submissions: dict, *, as_of: date) -> tuple[RecentFiling, ...]:
    """Read aligned EDGAR recent filing columns and filter by filing availability."""
    try:
        recent = submissions["filings"]["recent"]
    except (KeyError, TypeError) as exc:
        raise ValueError("SEC submissions are missing recent filing metadata.") from exc
    if not isinstance(recent, dict):
        raise ValueError("SEC recent filing metadata must be a JSON object.")

    forms = recent.get("form")
    if not isinstance(forms, list):
        raise ValueError("SEC recent filing metadata is missing the form column.")

    def column(name: str, *, required: bool = False) -> list[str]:
        if name not in recent and not required:
            return [""] * len(forms)
        values = recent.get(name)
        if not isinstance(values, list) or len(values) != len(forms) or any(
            not isinstance(value, str) for value in values
        ):
            raise ValueError(f"SEC recent filing column {name} is invalid or misaligned.")
        return values

    filings = []
    for form, accession, filed, reported, items, primary in zip(
        column("form", required=True), column("accessionNumber", required=True),
        column("filingDate", required=True), column("reportDate"), column("items"),
        column("primaryDocument"), strict=True,
    ):
        filed_date = date.fromisoformat(filed)
        if filed_date <= as_of:
            filings.append(RecentFiling(
                form, accession, filed_date, date.fromisoformat(reported) if reported else None,
                items, primary,
            ))
    return tuple(sorted(filings, key=lambda filing: (filing.filing_date, filing.accession), reverse=True))


def _normalize(text: str) -> str:
    return " ".join(text.split())


@dataclass
class _Cell:
    parts: list[str] = field(default_factory=list)
    links: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return _normalize("".join(self.parts))


@dataclass
class _Table:
    rows: list[list[_Cell]] = field(default_factory=list)
    row: list[_Cell] | None = None
    cell: _Cell | None = None


class _Page(HTMLParser):
    def __init__(self, document: str):
        super().__init__(convert_charrefs=True)
        self.tables: list[_Table] = []
        self._stack: list[_Table] = []
        self._text: list[str] = []
        self._hidden = 0
        self.feed(document)
        self.close()

    @property
    def text(self) -> str:
        return _normalize("".join(self._text))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._hidden += 1
        if self._hidden:
            return
        if tag in {"div", "p", "td", "th", "tr", "br", "h1", "h2", "h3"}:
            self._text.append(" ")
        if tag == "table":
            table = _Table()
            self.tables.append(table)
            self._stack.append(table)
        elif self._stack:
            table = self._stack[-1]
            if tag == "tr":
                table.row = []
                table.rows.append(table.row)
                table.cell = None
            elif tag in {"td", "th"} and table.row is not None:
                table.cell = _Cell()
                table.row.append(table.cell)
            elif tag == "a" and table.cell is not None:
                href = dict(attrs).get("href")
                if href:
                    table.cell.links.append(href)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._hidden:
            self._hidden -= 1
            return
        if self._stack:
            table = self._stack[-1]
            if tag == "table":
                self._stack.pop()
            elif tag == "tr":
                table.row = None
                table.cell = None
            elif tag in {"td", "th"}:
                table.cell = None

    def handle_data(self, data: str) -> None:
        if self._hidden:
            return
        self._text.append(data)
        if self._stack and self._stack[-1].cell is not None:
            self._stack[-1].cell.parts.append(data)


def _exhibit_url(index: _Page, accession: str, index_url: str) -> str:
    directory = urlsplit(index_url).path.rpartition("/")[0] + "/"
    urls = set()
    for table in index.tables:
        for row in table.rows:
            if len(row) != 5 or len(row[2].links) != 1:
                continue
            exhibit_type = row[3].text.upper()
            if exhibit_type not in {"EX-99.1", "EX-99"}:
                continue
            link = urlsplit(urljoin(index_url, row[2].links[0]))
            if (link.scheme, link.netloc, link.query, link.fragment) != ("https", "www.sec.gov", "", ""):
                continue
            if not link.path.startswith(directory):
                continue
            filename = link.path.removeprefix(directory)
            if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*\.html?", filename, re.IGNORECASE):
                continue
            # The GD index can label Exhibit 99.1 generically as EX-99.
            if exhibit_type == "EX-99" and not re.search(r"ex(?:hibit)?99[._-]?1\b", filename, re.IGNORECASE):
                continue
            urls.add(filing_document_url(GD_CIK, accession, filename))
    if len(urls) != 1:
        raise ValueError(f"SEC filing {accession} did not identify one same-filing HTML exhibit for Exhibit 99.1.")
    return urls.pop()


def _annual_deliveries(page: _Page, filed_date: date) -> tuple[ReleaseMetric, ...]:
    matches = set()
    for table in page.tables:
        rows = [tuple(text for cell in row if (text := cell.text)) for row in table.rows]
        rows = [row for row in rows if row]
        for index, row in enumerate(rows):
            if row != ("Gulfstream Aircraft Deliveries (units):",) or not 2 <= index < len(rows) - 3:
                continue
            if rows[index - 2] != ("Fourth Quarter", "Twelve Months"):
                continue
            year = filed_date.year - 1
            if rows[index - 1] != (str(year), str(year - 1), str(year), str(year - 1)):
                continue
            component_rows = rows[index + 1:index + 4]
            if any(len(row) != 5 for row in component_rows):
                continue
            if tuple(row[0] for row in component_rows) != ("Large-cabin aircraft", "Mid-cabin aircraft", "Total"):
                continue
            try:
                large, mid, total = (
                    tuple(int(value) for value in row[1:]) for row in component_rows
                )
            except ValueError:
                continue
            if any(min(values) < 0 for values in (large, mid, total)) or any(
                large[column] + mid[column] != total[column] for column in range(4)
            ):
                continue
            matches.add((year, total[2], total[3]))
    if len(matches) != 1:
        return ()
    year, current, previous = matches.pop()
    return tuple(
        ReleaseMetric(
            "gulfstream_deliveries", period, value, "aircraft",
            f"Aerospace supplemental data, Gulfstream aircraft deliveries, Twelve Months {period} column, Total row.",
        )
        for period, value in ((year, current), (year - 1, previous))
    )


def is_earnings_filing(filing: RecentFiling) -> bool:
    return filing.form == "8-K" and "2.02" in re.split(r"[,;\s]+", filing.items)


def get_gd_release_for_filing(client: TextClient, filing: RecentFiling) -> EarningsRelease:
    """Read the earnings exhibit for a selected original Item 2.02 filing."""
    if not is_earnings_filing(filing):
        raise ValueError("Expected a Form 8-K earnings filing.")
    index_url = filing_index_url(GD_CIK, filing.accession)
    url = _exhibit_url(_Page(client.get_text(index_url)), filing.accession, index_url)
    page = _Page(client.get_text(url))
    text = page.text
    match = re.search(r"\bGeneral Dynamics Reports [^.]{1,110}? Financial Results\b", text, re.IGNORECASE)
    title = match.group() if match else None
    guidance_note = "Numerical guidance remains unverified in this exhibit feed."
    if re.search(r"\bguidance (?:presented|provided|discussed) during (?:the|its) (?:earnings )?call\b", text, re.IGNORECASE):
        guidance_note = "The exhibit directs readers to the earnings call and supplemental charts. Numerical guidance is unverified in the exhibit."
    return EarningsRelease(
        filing.filing_date, filing.accession, url, title, _annual_deliveries(page, filing.filing_date),
        (), guidance_note,
    )


def get_latest_gd_release(client: TextClient, submissions: dict, *, as_of: date) -> EarningsRelease | None:
    """Find the latest original Item 2.02 8-K exhibit available at the cutoff."""
    filing = next((filing for filing in recent_filings(submissions, as_of=as_of) if is_earnings_filing(filing)), None)
    return get_gd_release_for_filing(client, filing) if filing is not None else None

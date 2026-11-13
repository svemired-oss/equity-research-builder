"""Read explicitly stated fiscal-year outlook from GD's annual filing."""

from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from math import isfinite
import re

from .gd_inputs import InputSource


SEGMENTS = {
    "aerospace": "aerospace",
    "marine systems": "marine_systems",
    "combat systems": "combat_systems",
    "technologies": "technologies",
}
OUTLOOK_METRICS = (
    *(f"{segment}_{measure}" for segment in SEGMENTS.values() for measure in ("revenue", "operating_margin")),
    "effective_tax_rate",
)
NUMBER = r"[0-9]+(?:\.[0-9]+)?"
SEGMENT_OUTLOOK = re.compile(
    rf"\bwe expect the (?P<segment>{'|'.join(SEGMENTS)}) segment's (?P<year>\d{{4}}) revenue "
    rf"to (?:increase to |decrease to |be )(?P<revenue_qualifier>approximately |around )?"
    rf"\$\s*(?P<revenue_low>{NUMBER})(?:\s*(?:-|to)\s*\$\s*(?P<revenue_high>{NUMBER}))? "
    rf"billion with (?:an )?operating margin of (?P<margin_qualifier>approximately |around )?"
    rf"(?P<margin>{NUMBER})\s*%",
    re.IGNORECASE,
)
TAX_OUTLOOK = re.compile(
    rf"\bfor (?P<year>\d{{4}})\s*, we expect a full-year effective tax rate of "
    rf"(?P<qualifier>approximately |around )?(?P<rate>{NUMBER})\s*%",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AnnualOutlook:
    metric: str
    year: int
    low: float
    high: float
    unit: str
    approximate: bool
    source: InputSource


class _FilingText(HTMLParser):
    HIDDEN = {"script", "style", "ix:header", "ix:hidden"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.HIDDEN:
            self.hidden_depth += 1

    def handle_endtag(self, tag):
        if tag in self.HIDDEN:
            self.hidden_depth = max(0, self.hidden_depth - 1)

    def handle_data(self, data):
        if not self.hidden_depth:
            self.parts.append(data)


def parse_gd_annual_outlook(
    html: str, *, year: int, accession: str, filing_date: date, url: str,
) -> tuple[AnnualOutlook, ...]:
    """Recognize GD's dated Item 7 outlook wording and omit ambiguous matches."""
    parser = _FilingText()
    parser.feed(html)
    parser.close()
    text = " ".join(" ".join(parser.parts).split()).translate(str.maketrans("’–—", "'--"))
    found: dict[str, list[AnnualOutlook]] = {}

    def add(metric: str, low: float, high: float, unit: str, approximate: bool, section: str) -> None:
        if not isfinite(high) or not 0 <= low <= high or (unit == "ratio" and high > 1):
            return
        source = InputSource("sec_filing", f"Form 10-K {accession}, Item 7, {section}.", filing_date, url=url)
        found.setdefault(metric, []).append(AnnualOutlook(metric, year, low, high, unit, approximate, source))

    for match in SEGMENT_OUTLOOK.finditer(text):
        if int(match["year"]) != year:
            continue
        section = match["segment"].lower()
        segment = SEGMENTS[section]
        low = float(match["revenue_low"]) * 1_000
        high = float(match["revenue_high"] or match["revenue_low"]) * 1_000
        margin = float(match["margin"]) / 100
        add(f"{segment}_revenue", low, high, "USD millions", bool(match["revenue_qualifier"]), f"{section} outlook")
        add(f"{segment}_operating_margin", margin, margin, "ratio", bool(match["margin_qualifier"]), f"{section} outlook")

    for match in TAX_OUTLOOK.finditer(text):
        if int(match["year"]) == year:
            rate = float(match["rate"]) / 100
            add("effective_tax_rate", rate, rate, "ratio", bool(match["qualifier"]), "provision for income tax outlook")

    return tuple(
        entries[0] for entries in found.values()
        if all(entry == entries[0] for entry in entries)
    )

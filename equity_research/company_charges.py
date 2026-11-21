"""A company-specific "Other operating charges" income-statement line (Stage 4a, KO normalization fix).

Some issuers (KO) report impairments, remeasurements and restructuring on one income-statement line
labeled "Other operating charges", tagged with a generic us-gaap concept that other filers use for
recurring cost. The line counts as an unusual item only when the 10-K's own income statement carries
that label and its note itemizes impairments, remeasurements or restructuring.
"""
from datetime import date
from html import unescape
import re

LABEL = re.compile(r"^other operating charges$", re.I)
NOTE_HEADING = "Other Operating Charges"
ITEMIZED = re.compile(r"\b(?:impairments?|remeasurements?|restructuring)\b", re.I)
_ROW = re.compile(r"<tr\b.*?</tr>", re.I | re.S)
_CELL = re.compile(r"<td\b.*?</td>", re.I | re.S)
_IX_NAME = re.compile(r"<ix:nonfraction\b[^>]*\bname=\"([^\"]+)\"", re.I)
_TAG = re.compile(r"<[^>]+>")


def _text(fragment):
    return " ".join(unescape(_TAG.sub(" ", fragment)).split())


def income_statement_concept(html):
    """The inline XBRL concept on the first table row labeled exactly "Other operating charges"."""
    for row in _ROW.finditer(html):
        cells = _CELL.findall(row.group(0))
        if cells and LABEL.fullmatch(_text(cells[0])):
            names = _IX_NAME.findall(row.group(0))
            if names:
                return names[0]
    return None


def note_itemizes(html):
    """True when an "Other Operating Charges" note discusses impairments, remeasurements or restructuring."""
    text = _text(html)
    start = 0
    while (found := text.find(NOTE_HEADING, start)) >= 0:
        if ITEMIZED.search(text[found:found + 4000]):
            return True
        start = found + len(NOTE_HEADING)
    return False


def _local(concept):
    return concept.rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def values_from_instance(instance, concept, period_ends):
    """Annual, undimensioned values of the concept for each fiscal year end, USD."""
    result = {}
    for fact in instance.facts:
        if (_local(fact.concept) == _local(concept) and not fact.dimensions and fact.unit == "USD"
                and fact.start is not None and (fact.end - fact.start).days > 300 and fact.end.isoformat() in period_ends):
            result[fact.end.isoformat()] = fact.value
    return result


def collect(packet, client, html):
    """Store the company line's values in the packet, or the reason it does not qualify."""
    latest = packet["actuals"]["periods"][-1]
    concept = income_statement_concept(html)
    if concept is None:
        packet["company_operating_charges"] = {"values": {}, "reason": "No income-statement line labeled Other operating charges."}
        return
    if not note_itemizes(html):
        packet["company_operating_charges"] = {"values": {}, "concept": concept,
                                               "reason": "The Other Operating Charges note does not itemize impairments, "
                                                         "remeasurements or restructuring."}
        return
    from .filing_xbrl import load_filing_instance
    try:
        instance = load_filing_instance(client, packet["issuer"]["cik"], latest["filing"]["accession"])
    except (OSError, ValueError) as exc:
        packet["company_operating_charges"] = {"values": {}, "concept": concept, "reason": f"Filing instance unavailable. {exc}"}
        return
    ends = {period["period_end"] for period in packet["actuals"]["periods"]}
    packet["company_operating_charges"] = {
        "label": "Other operating charges", "concept": concept, "url": latest["filing"]["url"],
        "values": values_from_instance(instance, concept, ends),
        "basis": f"10-K income-statement line labeled Other operating charges ({_local(concept)}); its note itemizes "
                 "impairments, remeasurements or restructuring",
    }

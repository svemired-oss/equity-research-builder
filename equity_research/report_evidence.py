"""Collect dated, source-scoped passages for the operating report."""
from datetime import date
import re
import unicodedata

from .sec_guidance import _ReleaseText

HEADING = re.compile(r"^item\s+(1a|1b|1|2)\s*[.\u2014:\-]?\s*(business|risk factors|unresolved staff comments|properties)?\s*$", re.I)
EVENT = re.compile(r"\b(?:expects?|announced|outlook|guidance|completed|launched|scheduled)\b", re.I)
BOILERPLATE = re.compile(r"\b(?:risk factors should be considered|following risks could harm|"
                         r"additional risks|forward-looking statements|safe harbor)\b", re.I)


# List markers that filings use as bullets. The PDF font has no glyph for some of
# them, so text extraction would return a different character than the workbook.
LIST_MARKERS = "\u2022\u2023\u2043\u2219\u25aa\u25ab\u25cf\u25e6\u25a0\u25a1\u00b7\uf0b7\uf0a7\u27a2\u2713"


def clean_passage(text):
    """Return a quote that the workbook and the PDF render identically.

    Control, format, private-use and unassigned characters become spaces, list
    markers are removed and whitespace is collapsed. Words and numbers are unchanged.
    """
    text = unicodedata.normalize("NFKC", text)
    text = "".join(" " if char in LIST_MARKERS or unicodedata.category(char).startswith("C") else char
                   for char in text)
    return re.sub(r"\s+", " ", text).strip(" -")


def annual_passages(html):
    sections = {"1": [], "1a": []}
    active = None
    for block in _ReleaseText(html).blocks:
        match = HEADING.fullmatch(block)
        if match:
            active = match[1].lower()
        elif active in sections and 65 <= len(block) <= 1100 and not block.startswith(("http", "Table of Contents")):
            sections[active].append(block)
    industry = re.compile(r"\b(?:competition|competitors|industry|market|customers)\b", re.I)
    # Select complete filing sentences to keep their qualifiers and context.
    def snippets(blocks, pattern=None):
        sentences = []
        for block in blocks:
            for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z])", block):
                sentence = sentence.lstrip(" •\ufffd\u2022\u25cf")
                if (50 <= len(sentence) <= 370 and "\ufffd" not in sentence and not BOILERPLATE.search(sentence)
                        and (pattern is None or pattern.search(sentence))):
                    sentences.append(sentence)
        return list(dict.fromkeys(sentences))
    return {"business":snippets(sections["1"])[:2],
            "industry":snippets(sections["1"],industry)[:2],
            "risk":snippets(sections["1a"])[:3]}


def collect(packet, client):
    latest = packet["actuals"]["periods"][-1]["filing"]
    records = []
    url = latest["url"]
    cutoff = date.fromisoformat(packet["as_of"])
    try:
        filed = date.fromisoformat(latest["filed_on"])
        if filed <= cutoff and latest["form"] in ("10-K", "10-K/A"):
            for kind, rows in annual_passages(client.get_text(url)).items():
                records.extend({"kind":kind,"filed_on":str(filed),"url":url,"text":row} for row in rows)
    except (KeyError, OSError, ValueError):
        pass
    for item in packet.get("event_guidance", {}).get("observations", [])[:3]:
        if date.fromisoformat(item["filed_on"]) <= cutoff:
            records.append({"kind":"catalyst","filed_on":item["filed_on"],"url":item["url"],"text":item["evidence"]})
    for item in packet.get("earnings", {}).get("releases", [])[:2]:
        if date.fromisoformat(item["filed_on"]) > cutoff:
            continue
        try:
            blocks = _ReleaseText(client.get_text(item["url"])).blocks[:100]
        except (ValueError,OSError):
            continue
        passage = next((s for b in blocks for s in re.split(r"(?<=[.!?])\s+(?=[A-Z])", b)
                        if 65 <= len(s) <= 360 and EVENT.search(s)
                        and "\ufffd" not in s and not s.rstrip().endswith(":") and not BOILERPLATE.search(s)), None)
        if passage:
            records.append({"kind":"catalyst","filed_on":item["filed_on"],"url":item["url"],"text":passage})
    for record in records:
        record["text"] = clean_passage(record["text"])
    packet["report_evidence"] = [record for record in records if record["text"]]


"""Collect dated, source-scoped passages for the operating report."""
from datetime import date
import re
import unicodedata

from .sec_guidance import _ReleaseText

HEADING = re.compile(r"^item\s+(1a|1b|1c|1|2)\s*[.\u2014:\-]?\s*(business|risk factors|unresolved staff comments|"
                     r"cybersecurity|properties)?\s*\.?\s*$", re.I)
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


# Stage 4a section rules (docs/PLAN.md): industry text comes only from Item 1 subsections on
# competition, industry, markets or customers; business text is the first factual Item 1 paragraphs
# on products and segments; risks are the five most specific Item 1A risk-factor headings.
EXCLUDED = re.compile(r"\b(?:purpose|vision|mission|values|esg|sustainab\w*|environmental,? social|social impact|"
                      r"diversity|inclusion|human capital|culture|philanthrop\w*|net[- ]zero|climate|"
                      r"the terms? .{0,60}\b(?:mean|means|refer)|incorporated (?:in|under)|available information|"
                      r"website|forward-looking)\b", re.I)
FACTUAL = re.compile(r"\b(?:products?|services?|segments?|brands?|operat\w*|sells?|sold|manufactur\w*|"
                     r"distribut\w*|customers?|revenues?|business(?:es)?|offer\w*|provid\w*|portfolio)\b", re.I)
OVERVIEW_HEADING = re.compile(r"\b(?:general|overview|business|our company|products?|segments?|description|merchandise)\b", re.I)
INDUSTRY_PRIORITY = tuple(re.compile(rf"\b(?:{p})\b", re.I) for p in (
    r"competition|competitive|competitors", r"industry|industries", r"markets?", r"customers?"))
RISK_MODAL = re.compile(r"\b(?:could|may|might|would|can|will)\b", re.I)
GENERIC_RISK = re.compile(r"\b(?:economic|geopolitical|foreign currency|exchange rates?|interest rates?|income tax|"
                          r"tax (?:laws|rates)|cyber\w*|information (?:technology|systems)|privacy|data protection|"
                          r"talent|key personnel|employees|litigation|legal proceedings|climate|weather|pandemic|"
                          r"counterparty|impairment charges|stock price|accounting|internal control|"
                          r"laws and regulations|indebtedness|credit ratings?)\b", re.I)
STOPWORDS = set("""a an and are as at be been but by can could do for from has have if in into is it its may might
not of on or our over such than that the their them these they this those to under unable we were which while will
with would business businesses results financial operations adversely affect affected negatively impact significant
company changes increase increased reduce demand other""".split())
PARAGRAPH_LIMIT = 700


def _is_heading(block):
    return (3 <= len(block) <= 90 and not block.endswith((".", ":", ";", ",")) and block[0].isupper()
            and not block.startswith(("•", "(", "Table of Contents")) and not block.isdigit())


def _trim(paragraph, limit=PARAGRAPH_LIMIT):
    """Whole sentences up to the limit, so the quote keeps its qualifiers."""
    kept = ""
    for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z])", paragraph):
        if len(kept) + len(sentence) + 1 > limit:
            break
        kept = f"{kept} {sentence}".strip()
    return kept


def _words(text):
    return {w for w in re.findall(r"[a-z][a-z'-]{3,}", text.lower()) if w not in STOPWORDS}


def annual_passages(html):
    """Business, industry and risk passages from the annual report's Item 1 and Item 1A."""
    sections = {"1": [], "1a": []}
    active = None
    for block in _ReleaseText(html).blocks:
        match = HEADING.fullmatch(block)
        if match:
            active = match[1].lower()
        elif active in sections and not block.startswith(("http", "Table of Contents")) and not block.isdigit():
            sections[active].append(block)
    # Item 1 paragraphs tagged with their subsection heading.
    tagged, heading = [], "Item 1"
    for block in sections["1"]:
        if _is_heading(block):
            heading = block
        elif (120 <= len(block) <= 2500 and "\ufffd" not in block and block[0].isupper()
              and not block.startswith("•")):
            tagged.append((heading, block))
    def factual_sentences(block):
        """Drop purpose, values, ESG, definition and incorporation sentences; keep the factual rest."""
        kept = [x for x in re.split(r"(?<=[.!?])\s+(?=[A-Z])", block) if not EXCLUDED.search(x) and not BOILERPLATE.search(x)]
        return " ".join(kept)
    clean = [(h, factual_sentences(b)) for h, b in tagged if not EXCLUDED.search(h)]
    clean = [(h, b) for h, b in clean if len(b) >= 100]
    # Business: the first factual paragraphs, preferring overview, products and segment subsections.
    factual = [(h, b) for h, b in clean if FACTUAL.search(b)]
    preferred = [(h, b) for h, b in factual if OVERVIEW_HEADING.search(h)]
    business = [(h, _trim(b)) for h, b in (preferred or factual)[:2]]
    # Industry: competition first, then industry, markets and customers subsections.
    industry = []
    for pattern in INDUSTRY_PRIORITY:
        industry = [(h, _trim(b)) for h, b in clean if pattern.search(h)][:2]
        if industry:
            break
    # A word's weight is how often the company's own Item 1 uses it (capped), so headings about the
    # company's products, channels and partners outrank generic risk language.
    from collections import Counter
    counts = Counter(w for _, b in tagged for w in re.findall(r"[a-z][a-z'-]{3,}", b.lower()) if w not in STOPWORDS)
    candidates = []
    for order, block in enumerate(sections["1a"]):
        sentence = block.strip()
        if (40 <= len(sentence) <= 300 and sentence.endswith(".") and sentence[0].isupper()
                and len(re.findall(r"[.!?]\s+[A-Z]", sentence[:-1])) <= 1 and RISK_MODAL.search(sentence)
                and "\ufffd" not in sentence and not BOILERPLATE.search(sentence)):
            words = _words(sentence)
            specific = (sum(min(counts[w], 10) for w in words) / max(len(words), 1)
                        - 4 * len(GENERIC_RISK.findall(sentence)))
            candidates.append((specific, order, sentence))
    top = sorted(sorted(candidates, key=lambda c: (-c[0], c[1]))[:5], key=lambda c: c[1])
    return {"business": business, "industry": industry,
            "risk": [("Item 1A risk factor heading", sentence) for _, _, sentence in top]}


def collect(packet, client):
    latest = packet["actuals"]["periods"][-1]["filing"]
    records = []
    url = latest["url"]
    cutoff = date.fromisoformat(packet["as_of"])
    try:
        filed = date.fromisoformat(latest["filed_on"])
        if filed <= cutoff and latest["form"] in ("10-K", "10-K/A"):
            html = client.get_text(url)
            for kind, rows in annual_passages(html).items():
                records.extend({"kind":kind,"filed_on":str(filed),"url":url,"text":row,"section":heading}
                               for heading, row in rows)
            from .company_charges import collect as collect_company_charges
            collect_company_charges(packet, client, html)
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
        record["section"] = clean_passage(record.get("section", ""))
    packet["report_evidence"] = [record for record in records if record["text"]]


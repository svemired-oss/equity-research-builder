"""Extract explicitly stated fiscal-year consolidated revenue guidance from SEC releases."""

from collections import defaultdict
from datetime import date
from html.parser import HTMLParser
from math import isfinite
import re
from urllib.parse import urlsplit


_BLOCK = {"br", "div", "h1", "h2", "h3", "h4", "li", "p", "section"}
_SKIP = {"head", "script", "style", "table", "ix:hidden"}
_YEAR = r"(?P<year>20[0-9]{2})"
_FY = r"(?:full\s+(?:fiscal\s+)?year|fiscal\s+year|fiscal|(?:full\s+)?FY)"
_ANNUAL = re.compile(rf"\b{_FY}[\s-]*{_YEAR}\b", re.I)
_QUARTER = re.compile(
    r"\b(?:Q[1-4](?=\b|FY)|quarter(?:ly)?|(?:first|second)\s+half|(?:three|six|nine)\s+months)\b", re.I,
)
_OUTLOOK = re.compile(r"\b(?:outlook|guidance|forecast|financial expectations)\b", re.I)
_SUBJECT = r"(?:(?:total|consolidated)\s+)*(?:net\s+)?(?:revenues?|sales)"
_EXPECTATION = r"(?:expect(?:s)?|project(?:s)?|anticipate(?:s)?|forecast(?:s)?|estimate(?:s)?)"
_COPULA = r"(?:to\s+(?:be|reach)\s+|(?:of|at|is)\s+)?"
_GUIDANCE_START = re.compile(
    rf"\b(?:{_EXPECTATION}\s+(?:{_FY}\s*20\d{{2}}\s+)?{_SUBJECT}\s+{_COPULA}|"
    rf"{_SUBJECT}\s+(?:(?:is|are)\s+)?(?:expected|projected|forecast)\s+{_COPULA}|"
    rf"{_SUBJECT}\s+(?:guidance|outlook|forecast)\s+(?:of|is|at|to|increased to|raised to|lowered to)\s+|"
    rf"confirm(?:s)?\s+(?:(?:our|its)\s+)?(?:prior\s+)?{_SUBJECT}\s+guidance\s+of\s+)",
    re.I,
)
_LABEL = re.compile(rf"^(?:(?:total|consolidated)\s+)+(?:net\s+)?(?:revenues?|sales)\s*[:\-]\s*", re.I)
_HISTORICAL = re.compile(
    r"\b(?:previously\s+(?:expected|projected|guided|forecast)|prior\s+(?:guidance|forecast|outlook)\s+(?:was|of)|"
    r"(?:does?|did|will)\s+not\s+|withdraw\w*|suspend\w*|no\s+longer\s+)\b", re.I,
)
_NONCONSOLIDATED = re.compile(r"\b(?:segment|cloud|subscription|service|product|regional|organic|constant.currency|excluding|adjusted)\s*$", re.I)
_NONSTANDARD = re.compile(r"\b(?:constant.currency|excluding|organic|pro\s+forma|assuming|under\s+(?:the|this)\s+scenario)\b", re.I)
_PREFIX = r"(?:US\s*\$|USD\b|CA\s*\$|C\$|CAD\b|AU\s*\$|A\$|AUD\b|NZ\s*\$|NZD\b|HK\s*\$|HKD\b|SGD\b|EUR\b|€|GBP\b|£|JPY\b|CHF\b|INR\b|CNY\b|\$)"
_ISO = r"(?:USD|CAD|AUD|NZD|HKD|SGD|EUR|GBP|JPY|CHF|INR|CNY)"
def _amount_pattern(key):
    return (rf"(?P<{key}_prefix>{_PREFIX})?\s*(?P<{key}_number>\d{{1,3}}(?:,\d{{3}})+(?:\.\d+)?|\d+(?:\.\d+)?)"
            rf"\s*(?P<{key}_scale>million|billion|trillion)?\b(?:\s+(?P<{key}_suffix>{_ISO})\b)?")


_FIRST, _SECOND = map(_amount_pattern, ("first", "second"))
_RANGE = re.compile(
    rf"(?:in\s+(?:a|the)\s+range\s+of\s+|between\s+|from\s+)?{_FIRST}\s*(?:to\b|and\b|[-–—])\s*{_SECOND}", re.I,
)
_BOUND = re.compile(
    rf"(?P<relation>at\s+least|no\s+less\s+than|minimum\s+of|at\s+most|no\s+more\s+than|up\s+to|"
    rf"maximum\s+of|more\s+than|less\s+than|above|below|approximately|about|around|roughly)?\s*{_FIRST}", re.I,
)
_UNIT = {"million": 10**6, "billion": 10**9, "trillion": 10**12}
_CURRENCIES = {"US$": "USD", "CA$": "CAD", "C$": "CAD", "AU$": "AUD", "A$": "AUD",
               "NZ$": "NZD", "HK$": "HKD", "€": "EUR", "£": "GBP"}
_DISCLOSED_DOLLAR = re.compile(
    r"\b(?:all\s+(?:amounts|figures)|(?:amounts|figures)\s+in\s+this\s+release)\s+(?:are\s+)?"
    r"(?:stated\s+|expressed\s+|presented\s+|reported\s+)?in\s+(U\.?S\.?|Canadian|Australian|New Zealand|Hong Kong|Singapore)\s+dollars\b",
    re.I,
)
_USD_CONTEXT = re.compile(r"\b(?:in\s+USD\b[^.]{0,70}\$|\$[^.]{0,70}\bin\s+USD\b)", re.I)
_DOLLAR_NAMES = {"us": "USD", "canadian": "CAD", "australian": "AUD", "new zealand": "NZD",
                 "hong kong": "HKD", "singapore": "SGD"}
_ARCHIVE_RELEASE = re.compile(r"/Archives/edgar/data/[1-9]\d*/(?P<accession>\d{18})/[A-Za-z0-9_][A-Za-z0-9_.-]*\.html?$", re.I)


class _ReleaseText(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.blocks = []
        self.parts = []
        self.hidden = []
        self.feed(html)
        self.close()
        self._flush()

    def _flush(self):
        block = " ".join("".join(self.parts).split())
        if block:
            self.blocks.append(block)
        self.parts.clear()

    def handle_starttag(self, tag, attrs):
        if self.hidden:
            if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
                self.hidden.append(tag)
            return
        attributes = dict(attrs)
        hidden_style = re.search(r"(?:display:\s*none|visibility:\s*hidden)", attributes.get("style") or "", re.I)
        if tag in _SKIP or ((hidden_style or "hidden" in attributes) and tag in _BLOCK | {"span"}):
            self._flush()
            self.hidden.append(tag)
        elif tag in _BLOCK:
            self._flush()

    def handle_startendtag(self, tag, attrs):
        if not self.hidden and tag in _BLOCK:
            self._flush()

    def handle_endtag(self, tag):
        if self.hidden:
            if tag in self.hidden:
                index = len(self.hidden) - 1 - self.hidden[::-1].index(tag)
                del self.hidden[index:]
        elif tag in _BLOCK:
            self._flush()

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def _currency_code(value):
    token = value.upper().replace(" ", "") if value else None
    return _CURRENCIES.get(token, token)


def _amounts(match, keys, dollar_currency):
    raw = match.groupdict()
    denominations = {code for key in keys for loc in ("prefix", "suffix")
                     if (code := _currency_code(raw[f"{key}_{loc}"]))}
    if "$" in denominations:
        if len(denominations) == 2:
            denominations.remove("$")
        elif dollar_currency:
            denominations.remove("$")
            denominations.add(dollar_currency)
    if len(denominations) != 1 or "$" in denominations:
        return None
    scales = {raw[f"{key}_scale"].lower() for key in keys if raw[f"{key}_scale"]}
    if len(scales) > 1 and any(raw[f"{key}_scale"] is None for key in keys):
        return None
    # Compact ranges can share an explicitly written scale, as in "$88 to $92 billion".
    shared = next(iter(scales), "") if len(scales) <= 1 else ""
    try:
        values = [float(raw[f"{key}_number"].replace(",", "")) * _UNIT.get((raw[f"{key}_scale"] or shared).lower(), 1)
                  for key in keys]
    except (OverflowError, ValueError):
        return None
    if any(not isfinite(value) or value <= 0 for value in values):
        return None
    return denominations.pop(), values, all(
        raw[f"{key}_prefix"] in (None, "$") and raw[f"{key}_suffix"] is None for key in keys
    )


def _value(text, dollar_currency):
    match = _RANGE.match(text)
    if match:
        parsed = _amounts(match, ("first", "second"), dollar_currency)
        if parsed is None:
            return None
        currency, (lower, upper), implicit = parsed
        if lower >= upper:
            return None
        kind, point, relation = "range", None, None
    else:
        match = _BOUND.match(text)
        if not match:
            return None
        parsed = _amounts(match, ("first",), dollar_currency)
        if parsed is None:
            return None
        currency, (amount,), implicit = parsed
        relation = (match["relation"] or "").lower()
        tail = text[match.end():]
        if not relation and (suffix := re.match(r"\s+(?:or\s+(?:more|higher)|and\s+above)\b", tail, re.I)):
            relation = "at least"
            end = match.end() + suffix.end()
        elif not relation and (suffix := re.match(r"\s+or\s+(?:less|lower)\b", tail, re.I)):
            relation = "at most"
            end = match.end() + suffix.end()
        else:
            end = match.end()
        if relation in {"at least", "no less than", "minimum of", "more than", "above"}:
            kind, lower, upper, point = "floor", amount, None, None
        elif relation in {"at most", "no more than", "up to", "maximum of", "less than", "below"}:
            kind, lower, upper, point = "ceiling", None, amount, None
        else:
            kind, lower, upper, point = "point", None, None, amount
    if kind == "range":
        end = match.end()
    tail = text[end:]
    if re.match(r"\s*(?:%|percent\b|per\b|[–—+-]\s*\d|\d|\.\d|,\d)", tail, re.I):
        return None
    return {"kind": kind, "currency": currency, "lower": lower, "upper": upper, "point": point,
            "qualifier": relation or None}, end, implicit


def _release_observations(document, release):
    blocks = _ReleaseText(document).blocks
    release_text = " ".join(blocks)
    dollar_names = {_DOLLAR_NAMES[name.casefold().replace(".", "")] for name in _DISCLOSED_DOLLAR.findall(release_text)}
    if len(dollar_names) == 1:
        dollar_currency, basis = dollar_names.pop(), "Issuer currency disclosure"
    elif not dollar_names and _USD_CONTEXT.search(release_text):
        dollar_currency, basis = "USD", "USD amounts identified in the release"
    elif not dollar_names and release.get("form") in {"8-K", "8-K/A"}:
        dollar_currency, basis = "USD", "Unqualified dollar amount in Form 8-K"
    else:
        dollar_currency, basis = None, None
    observed, heading = [], None
    for index, block in enumerate(blocks):
        annual = _ANNUAL.search(block)
        if len(block) <= 100 and _OUTLOOK.search(block) and not _GUIDANCE_START.search(block):
            heading = (int(annual["year"]), index, block) if annual and not _QUARTER.search(block) else None
            continue
        if _QUARTER.search(block[:180]):
            heading = None
            continue
        if len(block) > 750:
            heading = None
            continue
        matches = list(_GUIDANCE_START.finditer(block))
        if not matches and heading and (label := _LABEL.match(block)):
            matches = [label]
        for start in matches:
            if _HISTORICAL.search(block[:start.end()]) and not start.group().lower().startswith("confirm"):
                continue
            if _NONCONSOLIDATED.search(block[:start.start()]) or _NONSTANDARD.search(block):
                continue
            years = {int(match["year"]) for match in _ANNUAL.finditer(block[:start.end()])}
            if not years and heading and index - heading[1] <= 3:
                years = {heading[0]}
            if len(years) != 1:
                continue
            amount = _value(block[start.end():], dollar_currency)
            if amount is None:
                continue
            result, end, implicit = amount
            excerpt = block[:start.end() + end].rstrip()
            if len(excerpt) > 250:
                continue
            if not annual and heading:
                excerpt = f"{heading[2]}. {excerpt}"
            observed.append({
                "fiscal_year": years.pop(), **result, "evidence": excerpt,
                "currency_basis": basis if implicit else "Explicit currency",
                "filed_on": str(release["filed_on"]), "url": release["url"], "accession": release["accession"],
            })
    return observed


def _verified_url(release):
    url = urlsplit(release["url"])
    path = _ARCHIVE_RELEASE.fullmatch(url.path)
    return (url.scheme, url.netloc, url.query, url.fragment) == ("https", "www.sec.gov", "", "") and (
        path is not None and path["accession"] == release["accession"].replace("-", "")
    )


def _interval(row):
    if row["point"] is not None:
        return row["point"], row["point"]
    return row["lower"] or 0, row["upper"] if row["upper"] is not None else float("inf")


def collect_revenue_guidance(client, releases) -> dict:
    """Read full-year dollar revenue guidance from verified earnings exhibits."""
    groups, missing = defaultdict(dict), []
    for release in releases:
        try:
            date.fromisoformat(str(release["filed_on"]))
            if not _verified_url(release):
                raise ValueError("Unexpected earnings exhibit URL.")
            records = _release_observations(client.get_text(release["url"]), release)
        except (KeyError, OSError, ValueError):
            missing.append(f"{release.get('accession', 'Unknown filing')}: earnings exhibit unavailable for guidance.")
            continue
        if not records:
            missing.append(f"{release['accession']}: no unambiguous full-year consolidated revenue guidance found.")
        for row in records:
            fingerprint = tuple(row[key] for key in ("kind", "lower", "upper", "point"))
            groups[(row["filed_on"], row["fiscal_year"], row["currency"])].setdefault(fingerprint, row)
    observations, conflicts = [], []
    for (filed, year, currency), unique in sorted(groups.items(), reverse=True):
        rows = list(unique.values())
        intervals = [_interval(row) for row in rows]
        if max(lower for lower, _ in intervals) > min(upper for _, upper in intervals):
            conflicts.append({"filed_on": filed, "fiscal_year": year, "currency": currency, "candidates": rows})
        else:
            observations.extend(rows)
    if not observations and not conflicts and not missing:
        missing.append("No verified earnings exhibits were supplied for revenue guidance extraction.")
    return {"status": "conflicting" if conflicts else "available" if observations else "missing",
            "observations": observations, "missing": missing, "conflicts": conflicts}

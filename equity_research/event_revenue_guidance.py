"""Structure explicit consolidated annual revenue amounts in SEC event guidance."""

from collections import defaultdict
from datetime import date
import re

from .sec_event_guidance import _verify_url
from .sec_guidance import _interval, _value


_YEAR = r"(?:FY\s*[- ]?\s*(?P<short>\d{2})(?!\d)|(?:FY|fiscal(?:\s+year)?)\s*[- ]?\s*(?P<long>20\d{2}))\b"
_SUBJECT = r"(?:total|consolidated)\s+(?:net\s+)?(?:revenues?|sales)"
_GUIDANCE = r"(?:outlook|guidance|target)"
_EXPECT = r"(?:expects?|projects?|forecasts?|anticipates?)"
_STARTS = (
    re.compile(rf"\b(?:reaffirm\w*|confirm\w*|updat\w*|set|maintain\w*|provid\w*)\s+"
               rf"(?:(?:its|our)\s+)?{_YEAR}\s+{_GUIDANCE}\s+for\s+{_SUBJECT}\s+(?:of|at)\s+", re.I),
    re.compile(rf"\b(?:for|in)\s+{_YEAR},?\s+(?:(?:we|the company)\s+)?"
               rf"{_EXPECT}\s+{_SUBJECT}\s+(?:of|at|to be|to reach)\s+", re.I),
    re.compile(rf"\b{_YEAR}\s+{_SUBJECT}\s+(?:{_GUIDANCE}\s+(?:of|at)|"
               rf"(?:is|are)\s+(?:expected|projected|forecast)\s+to\s+(?:be|reach))\s+", re.I),
)
_PRE_VALUE_WITH_SUBJECT_AFTER = re.compile(rf"\b{_GUIDANCE}\s+for\s+{_YEAR}\s+of\s+", re.I)
_POST_VALUE_SUBJECT = re.compile(rf"\s+in\s+{_SUBJECT}\b", re.I)
_POST_VALUE_YEAR = re.compile(rf"\s+(?:in|for)\s+{_YEAR}", re.I)
_TRAILING_YEAR_START = re.compile(
    rf"\b(?:(?:reach(?:es|ing)?|{_EXPECT}|target(?:s|ing)?)\s+{_SUBJECT}\s+(?:of|at|to be|to reach)\s+|"
    rf"{_SUBJECT}\s+(?:(?:is|are)\s+)?(?:expected|projected|forecast)\s+to\s+(?:be|reach)\s+)", re.I,
)
_FORWARD = re.compile(
    rf"\b(?:{_GUIDANCE}|{_EXPECT}|expected|projected|forecast|reaffirm\w*|will)\b", re.I,
)
_REJECT = re.compile(
    r"\b(?:Q[1-4](?=\b|FY)|quarter(?:ly)?|(?:first|second)\s+half|(?:three|six|nine)\s+months|"
    r"segment(?:s|'s|’s)?|division(?:s|'s|’s)?|business unit|business line|region(?:al)?|"
    r"constant[ -]currency|organic|adjusted (?:total|consolidated)|non[ -]GAAP (?:total|consolidated)|"
    r"pro forma|exclud\w*|cumulative|combined|scenario|assuming|conditional|illustrativ\w*|if|"
    r"analyst(?:s|'s|’s)?|consensus|previously|withdraw\w*|suspend\w*|no longer|"
    r"does not|do not|did not|not (?:expect\w*|project\w*|guid\w*|forecast\w*))\b", re.I,
)
_SENTENCES = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")
_SCALE = re.compile(r"\b(?:million|billion|trillion)\b", re.I)
_YEAR_CONNECTOR = re.compile(rf"{_YEAR}\s*(?:[-–—/]|(?:and|or|to|through)\s+)\s*$", re.I)
_AFTER_YEAR_CONNECTOR = re.compile(rf"\s*(?:[-–—/]|(?:and|or|to|through)\s+)\s*{_YEAR}", re.I)
_AMOUNT_TAIL = re.compile(
    r"\s*(?:%|percent\b|per\b|CAGR\b|to\b|or\b|"
    r"(?:Canadian|Australian|New Zealand|Hong Kong|Singapore) dollars\b|dollars\s+(?:CAD|AUD|NZD|HKD|SGD)\b)",
    re.I,
)


def _usd_value(text, dollar_currency):
    parsed = _value(text, dollar_currency)
    if parsed is None:
        return None
    result, end, implicit = parsed
    if result["currency"] != "USD" or not _SCALE.search(text[:end]):
        return None
    # A plus sign immediately after a point amount explicitly marks a floor.
    if text[end:end + 1] == "+":
        if result["kind"] != "point" or result["qualifier"] or re.match(r"\s*\d", text[end + 1:]):
            return None
        result.update(kind="floor", lower=result["point"], point=None, qualifier="+")
        end += 1
    if _AMOUNT_TAIL.match(text[end:]):
        return None
    return result, end, "USD reporting currency" if implicit else "Explicit USD currency"


def _year(match):
    return int(match["long"] or ("20" + match["short"]))


def _amount_candidates(sentence, dollar_currency):
    if _REJECT.search(sentence):
        return
    for pattern in _STARTS:
        for match in pattern.finditer(sentence):
            if _YEAR_CONNECTOR.search(sentence[:match.start()]):
                continue
            if amount := _usd_value(sentence[match.end():], dollar_currency):
                yield _year(match), amount
    for match in _PRE_VALUE_WITH_SUBJECT_AFTER.finditer(sentence):
        amount = _usd_value(sentence[match.end():], dollar_currency)
        if amount and _POST_VALUE_SUBJECT.match(sentence[match.end() + amount[1]:]):
            yield _year(match), amount
    for match in _TRAILING_YEAR_START.finditer(sentence):
        if not _FORWARD.search(sentence[:match.end()]):
            continue
        amount = _usd_value(sentence[match.end():], dollar_currency)
        year = _POST_VALUE_YEAR.match(sentence[match.end() + amount[1]:]) if amount else None
        if year and not _AFTER_YEAR_CONNECTOR.match(sentence[match.end() + amount[1] + year.end():]):
            yield _year(year), amount


def parse_event_revenue_guidance(observations: list[dict], *, cik: str, as_of: date,
                                 dollar_currency: str | None = None) -> dict:
    """Return dated USD full-fiscal-year revenue bounds or points from verified event passages."""
    if type(as_of) is not date or dollar_currency not in (None, "USD"):
        raise ValueError("Supply a cutoff date and specify USD only when confirmed by the reporting basis.")
    groups, missing = defaultdict(dict), []
    for row in observations:
        if "revenue" not in row.get("metrics", ()):
            continue
        try:
            filed_on = date.fromisoformat(row["filed_on"])
            _verify_url(row["url"], cik, row["accession"])
            evidence = row["evidence"]
            if not isinstance(evidence, str) or len(evidence) > 650:
                raise ValueError("Invalid event passage.")
        except (KeyError, TypeError, ValueError):
            missing.append("A revenue passage had invalid SEC exhibit provenance or text.")
            continue
        if filed_on > as_of:
            continue
        for sentence in _SENTENCES.split(evidence):
            for fiscal_year, (amount, _, currency_basis) in _amount_candidates(sentence, dollar_currency):
                if not filed_on.year <= fiscal_year <= filed_on.year + 10:
                    continue
                record = {
                    "fiscal_year": fiscal_year, **amount, "evidence": sentence, "currency_basis": currency_basis,
                    "filed_on": filed_on.isoformat(), "url": row["url"], "accession": row["accession"],
                }
                key = tuple(record[name] for name in ("kind", "lower", "upper", "point"))
                groups[(filed_on.isoformat(), fiscal_year)].setdefault(key, record)
    observations, conflicts = [], []
    for (filed_on, fiscal_year), unique in sorted(groups.items(), reverse=True):
        candidates = list(unique.values())
        intervals = [_interval(row) for row in candidates]
        if max(lower for lower, _ in intervals) > min(upper for _, upper in intervals):
            conflicts.append({
                "filed_on": filed_on, "fiscal_year": fiscal_year, "currency": "USD", "candidates": candidates,
            })
        else:
            observations.extend(candidates)
    if not observations and not conflicts:
        missing.append("No unambiguous full-year consolidated USD revenue amounts were found in event guidance.")
    return {"status": "conflicting" if conflicts else "available" if observations else "missing",
            "observations": observations, "missing": list(dict.fromkeys(missing)), "conflicts": conflicts}

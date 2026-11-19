"""Fetch a dated U.S. Treasury par yield for USD discount-rate assumptions."""

from datetime import date, timedelta
from math import isfinite
import re
from urllib.error import URLError
from urllib.request import Request, urlopen
from xml.etree import ElementTree


_BASE = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"
_NAMESPACES = {
    "a": "http://www.w3.org/2005/Atom",
    "m": "http://schemas.microsoft.com/ado/2007/08/dataservices/metadata",
    "d": "http://schemas.microsoft.com/ado/2007/08/dataservices",
}
_MAX_BYTES = 512_000


def _download(url: str) -> bytes:
    request = Request(url, headers={
        "User-Agent": "Equity Research Builder/1.0",
        "Accept": "application/atom+xml, application/xml",
    })
    with urlopen(request, timeout=8) as response:
        return response.read(_MAX_BYTES + 1)


def _yields(document: bytes, earliest: date, as_of: date) -> dict:
    if len(document) > _MAX_BYTES or re.search(rb"<!\s*(?:DOCTYPE|ENTITY)\b", document, re.I):
        raise ValueError("Unexpected Treasury feed document.")
    root = ElementTree.fromstring(document)
    if root.tag != f"{{{_NAMESPACES['a']}}}feed":
        raise ValueError("Unexpected Treasury feed structure.")
    values = {}
    for row in root.findall("a:entry/a:content/m:properties", _NAMESPACES):
        label = row.findtext("d:NEW_DATE", namespaces=_NAMESPACES)
        raw = row.findtext("d:BC_10YEAR", namespaces=_NAMESPACES)
        if not label or not raw:
            continue
        try:
            day = date.fromisoformat(label.split("T", 1)[0])
            percent = float(raw)
        except ValueError:
            continue
        if earliest <= day <= as_of and isfinite(percent):
            values.setdefault(day, set()).add(percent)
    return values


def collect_treasury_yield(as_of: date, *, fetch=_download) -> dict:
    """Read the most recent ten-year par yield within seven calendar days."""
    earliest = as_of - timedelta(days=7)
    months = dict.fromkeys((as_of.strftime("%Y%m"), earliest.strftime("%Y%m")))
    for month in months:
        url = f"{_BASE}?data=daily_treasury_yield_curve&field_tdr_date_value_month={month}"
        try:
            values = _yields(fetch(url), earliest, as_of)
        except (ElementTree.ParseError, URLError, OSError, ValueError):
            return {"status": "missing", "reason": "The official Treasury yield feed could not be read.", "url": url}
        if not values:
            continue
        day = max(values)
        if len(values[day]) != 1:
            return {"status": "conflicting", "reason": "The latest Treasury observation has conflicting yields.", "url": url}
        return {
            "status": "available",
            "metric": "us_treasury_10_year_par_yield",
            "date": day.isoformat(),
            "rate": next(iter(values[day])) / 100,
            "url": url,
        }
    return {"status": "missing", "reason": "No ten-year Treasury par yield was reported in the preceding seven calendar days."}

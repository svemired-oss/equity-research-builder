"""Resolve exchange-qualified listings without guessing a U.S. ticker."""

from datetime import datetime
from io import BytesIO
import re
from urllib.error import URLError
from urllib.request import Request, urlopen
from zipfile import BadZipFile

from .operating_research import ResearchGap


_JPX = "https://www.jpx.co.jp/english/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_e.xlsx"
_LONDON_HALEON = "https://www.londonstockexchange.com/en/quote/equity/HLN/company-page"
_CODE = r"[0-9][A-Z0-9]{3}"


def qualified_symbol(symbol):
    if re.fullmatch(rf"XTKS:{_CODE}", symbol):
        return "XTKS", symbol.split(":", 1)[1]
    if re.fullmatch(rf"{_CODE}\.T", symbol):
        return "XTKS", symbol[:-2]
    if re.fullmatch(r"XLON:[A-Z][A-Z0-9]{0,7}", symbol):
        return "XLON", symbol.split(":", 1)[1]
    return None


def _fetch_jpx():
    req = Request(_JPX, headers={"User-Agent": "Equity Research Builder", "Accept": "application/octet-stream"})
    try:
        with urlopen(req, timeout=10) as response:
            data = response.read(2_000_001)
    except (OSError, URLError) as exc:
        raise ResearchGap("The Japan Exchange Group directory is temporarily unavailable.") from exc
    if len(data) > 2_000_000:
        raise ResearchGap("The Tokyo listing directory exceeded its expected size.")
    return data


def resolve_jpx(code, as_of, *, fetch=_fetch_jpx):
    from openpyxl import load_workbook
    from openpyxl.utils.exceptions import InvalidFileException

    try:
        workbook = load_workbook(BytesIO(fetch()), read_only=True, data_only=True, keep_links=False)
    except (BadZipFile, InvalidFileException, ValueError, OSError) as exc:
        if isinstance(exc, ResearchGap):
            raise
        raise ResearchGap("The Tokyo listing directory could not be read.") from exc
    try:
        sheet = workbook.active
        rows = sheet.iter_rows(max_col=6, values_only=True)
        columns = next(rows, ())
        if columns != ("Effective Date", "Local Code", "Name (English)", "Section/Products", "33 Sector(Code)", "33 Sector(name)"):
            raise ResearchGap("The Tokyo listing directory columns have changed.")
        records = {row for row in rows if str(row[1]).strip() == code}
    finally:
        workbook.close()
    if len(records) != 1:
        raise ResearchGap(f"XTKS:{code} has no unique listing in the current Japan Exchange Group directory.")
    effective, _, name, section, _, industry = records.pop()
    try:
        effective = datetime.strptime(str(effective), "%Y%m%d").date()
    except ValueError as exc:
        raise ResearchGap("The Tokyo directory did not identify a valid listing date.") from exc
    if not 0 <= (as_of - effective).days <= 62:
        raise ResearchGap("The Tokyo listing snapshot does not match the research cutoff.")
    if not all(isinstance(field, str) and field.strip() for field in (name, section, industry)):
        raise ResearchGap("The Tokyo directory row is missing issuer or security classification.")
    return {"exchange": "XTKS", "local_code": code, "name": name, "market_segment": section,
            "industry": industry if industry != "-" else None, "effective": effective.isoformat(),
            "currency": "JPY", "url": _JPX}


def reviewed_london_listing(code):
    if code != "HLN":
        raise ResearchGap(f"XLON:{code} needs a reviewed U.K. listing and filing connection.")
    return {"exchange": "XLON", "local_code": code, "name": "Haleon plc", "currency": "GBP",
            "url": _LONDON_HALEON, "sec_ticker": "HLN", "cik": "0001900304"}

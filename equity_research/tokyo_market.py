"""Dated primary-market closing prices and sovereign yields for Japanese shares."""

import csv
from datetime import date, timedelta
from io import BytesIO, StringIO
from math import isfinite
import re
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .operating_research import ResearchGap


_JPX = "https://www.jpx.co.jp/automation/markets/statistics-equities/daily/files"
_MOF = "https://www.mof.go.jp/english/policy/jgbs/reference/interest_rate/jgbcme.csv"


def public_document(url, *, size_limit):
    with urlopen(Request(url, headers={"User-Agent": "Equity Research Builder"}), timeout=12) as response:
        data = response.read(size_limit + 1)
    if len(data) > size_limit:
        raise ResearchGap("The public document exceeds the reviewed size limit.")
    return data


def _trading_row_close(line, code):
    line = line.strip()
    if not re.match(rf"^{re.escape(code)}\s+[\d,]+\s+\S", line):
        return None
    cols = re.split(r"\s{2,}", line)
    precision = r"[\d,]+\.\d{2,4}"
    first = next((i for i, item in enumerate(cols[2:], 2) if re.fullmatch(precision, item)), None)
    if first is None:
        return None
    prices = cols[first:first+8]
    if len(prices) != 8 or not all(re.fullmatch(precision, amount) for amount in prices):
        return None
    prices = tuple(float(amount.replace(",", "")) for amount in prices)
    for op, hi, lo, close in (prices[:4], prices[4:]):
        if not 0 < lo <= min(op, close) <= max(op, close) <= hi:
            raise ResearchGap("The Tokyo price record failed its daily trading range checks.")
    return prices[-1]


def read_jpx_close(document, code, day):
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        pdf = PdfReader(BytesIO(document))
        if not 1 <= len(pdf.pages) <= 550:
            raise ResearchGap("The Tokyo trading report has an unexpected page count.")
        header = pdf.pages[0].extract_text()
        if f"{day.year}年{day.month}月{day.day}日" not in header or "Auction Trades Regular Way" not in header:
            raise ResearchGap("The Tokyo market report did not match the requested exchange date and session.")
        for page in pdf.pages:
            plain = page.extract_text()
            if not re.search(rf"(?<![A-Z0-9]){re.escape(code)}(?![A-Z0-9])", plain):
                continue
            for line in page.extract_text(extraction_mode="layout").splitlines():
                close = _trading_row_close(line, code)
                if close is not None:
                    return close
    except (PdfReadError, OSError, ValueError) as exc:
        if isinstance(exc, ResearchGap):
            raise
        raise ResearchGap("The daily Tokyo quote report could not be read.") from exc
    raise ResearchGap(f"No verified afternoon close was published for {code} on {day}.")


def collect_jpx_close(code, cutoff, *, fetch=public_document):
    for age in range(8):
        day = cutoff - timedelta(days=age)
        if day.weekday() >= 5:
            continue
        url = f"{_JPX}/{day:%Y%m}/stq_{day:%Y%m%d}.pdf"
        try:
            document = fetch(url, size_limit=8_000_000)
        except HTTPError as exc:
            if exc.code == 404:
                continue
            raise ResearchGap("The Tokyo exchange quote source is temporarily unavailable.") from exc
        except OSError as exc:
            raise ResearchGap("The Tokyo exchange quote source is temporarily unavailable.") from exc
        close = read_jpx_close(document, code, day)
        return {"trading_date": day.isoformat(), "close": close, "currency": "JPY", "url": url, "ticker": code}
    raise ResearchGap("There is no published Tokyo close within the past week of the requested cutoff.")


def collect_jgb_yield(cutoff, *, fetch=public_document):
    values = {}
    for row in csv.reader(StringIO(fetch(_MOF, size_limit=100_000).decode("cp932"))):
        if not row or not re.fullmatch(r"\d{4}/\d{1,2}/\d{1,2}", row[0]) or len(row) <= 10:
            continue
        try:
            day = date(*map(int, row[0].split("/")))
            rate = float(row[10]) / 100
        except ValueError:
            continue
        if 0 <= (cutoff - day).days <= 7 and isfinite(rate) and 0 < rate < .15:
            values.setdefault(day, set()).add(rate)
    if not values or len(values[max(values)]) != 1:
        raise ResearchGap("The Ministry of Finance did not publish a unique recent Japanese 10-year government yield.")
    day = max(values)
    return {"date": day.isoformat(), "rate": values[day].pop(), "currency": "JPY", "url": _MOF}

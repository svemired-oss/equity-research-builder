"""Translate a dated foreign common value into the U.S.-listed trading unit."""

import csv
from datetime import date, datetime, timedelta
from io import StringIO
from math import isfinite
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .operating_research import ResearchGap


_ECB = "https://data-api.ecb.europa.eu/service/data/"
_EUR_BENCHMARK = "B.U2.EUR.4F.G_N_A.SV_C_YM.PY_10Y"
_LIMIT = 512_000


def _download(url):
    with urlopen(Request(url, headers={"Accept": "text/csv"}), timeout=8) as response:
        raw = response.read(_LIMIT + 1)
    if len(raw) > _LIMIT:
        raise ValueError("The ECB response exceeds the expected size.")
    return raw.decode("utf-8-sig")


def collect_usd_fx(currency, as_of, *, fetch=_download):
    """Return USD per reporting-currency unit for a common ECB observation day."""
    if currency == "USD":
        return {"status": "available", "currency": "USD", "date": as_of.isoformat(), "usd_per_unit": 1., "url": None}
    if currency not in {"EUR", "GBP", "JPY", "CAD", "CHF", "SEK", "DKK", "NOK", "AUD", "INR", "HKD", "SGD",
                        "CNY", "KRW", "BRL", "MXN", "NZD", "PLN", "CZK", "HUF", "ZAR"}:
        return {"status": "missing", "currency": currency, "reason": "There is no reviewed exchange-rate series."}
    earliest = as_of-timedelta(days=7)
    query = "USD" if currency=="EUR" else f"USD+{currency}"
    url = f"{_ECB}EXR/D.{query}.EUR.SP00.A?startPeriod={earliest}&endPeriod={as_of}&format=csvdata"
    series = {"USD": {}, currency: {}}
    try:
        rows = csv.DictReader(StringIO(fetch(url)))
        for row in rows:
            target = row.get("CURRENCY")
            keys = ("FREQ", "CURRENCY_DENOM", "EXR_TYPE", "EXR_SUFFIX")
            if target not in series or tuple(row.get(key) for key in keys) != ("D", "EUR", "SP00", "A"):
                continue
            if row.get("OBS_STATUS") != "A" or row.get("UNIT_MULT") not in (None, "0"):
                continue
            day = date.fromisoformat(row["TIME_PERIOD"])
            rate = float(row["OBS_VALUE"])
            if earliest <= day <= as_of and isfinite(rate) and rate > 0:
                series[target].setdefault(day,set()).add(rate)
    except (URLError, OSError, ValueError, KeyError, TypeError, csv.Error):
        return {"status":"missing","currency":currency,"reason":"The official ECB rate could not be read.","url":url}
    days = set(series["USD"])
    if currency != "EUR":
        days &= set(series[currency])
    if not days:
        return {"status":"missing","currency":currency,"reason":"A common ECB observation day is unavailable.","url":url}
    day = max(days)
    numerator = series["USD"][day]
    denominator = {1.} if currency=="EUR" else series[currency][day]
    if len(numerator)!=1 or len(denominator)!=1:
        return {"status":"conflicting","currency":currency,"reason":"The latest ECB rate is conflicting.","url":url}
    return {"status":"available","currency":currency,"date":day.isoformat(),
            "usd_per_unit":numerator.pop()/denominator.pop(),"url":url}


def collect_eur_benchmark(as_of, *, fetch=_download):
    """Get the ECB's dated AAA euro-area ten-year government par yield."""
    earliest = as_of - timedelta(days=7)
    url = f"{_ECB}YC/{_EUR_BENCHMARK}?startPeriod={earliest}&endPeriod={as_of}&format=csvdata"
    observations = {}
    try:
        for row in csv.DictReader(StringIO(fetch(url))):
            if (row.get("KEY") != "YC." + _EUR_BENCHMARK or row.get("UNIT") != "PCPA"
                    or row.get("UNIT_MULT") != "0" or row.get("OBS_STATUS") != "A"):
                continue
            day = date.fromisoformat(row["TIME_PERIOD"])
            rate = float(row["OBS_VALUE"]) / 100
            if earliest <= day <= as_of and isfinite(rate):
                observations.setdefault(day, set()).add(rate)
    except (URLError, OSError, ValueError, KeyError, TypeError, csv.Error):
        return {"status": "missing", "url": url, "reason": "The ECB government yield could not be read."}
    if not observations:
        return {"status": "missing", "url": url, "reason": "A recent EUR government yield is unavailable."}
    day = max(observations)
    rates = observations[day]
    if len(rates) != 1:
        return {"status": "conflicting", "url": url, "reason": "The latest EUR government yield is ambiguous."}
    return {"status": "available", "date": day.isoformat(), "rate": rates.pop(), "currency": "EUR",
            "instrument": "ECB AAA euro-area 10-year government par yield", "url": url}


def _boe_csv(url):
    request = Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "text/csv,*/*"})
    with urlopen(request, timeout=8) as response:
        data = response.read(_LIMIT + 1)
    if len(data) > _LIMIT:
        raise ValueError("The Bank of England response exceeds the expected size.")
    return data.decode("utf-8-sig")


def collect_gbp_benchmark(as_of, *, fetch=_boe_csv):
    """Collect the daily nominal 10-year UK government par yield, series IUDMNPY."""
    start = as_of - timedelta(days=7)
    params = {"csv.x": "yes", "Datefrom": start.strftime("%d/%b/%Y"), "Dateto": as_of.strftime("%d/%b/%Y"),
              "SeriesCodes": "IUDMNPY", "CSVF": "TN", "UsingCodes": "Y", "VPD": "Y", "VFD": "N"}
    url = "https://www.bankofengland.co.uk/boeapps/database/_iadb-fromshowcolumns.asp?" + urlencode(params)
    observations = {}
    try:
        rows = csv.DictReader(StringIO(fetch(url)))
        for row in rows:
            if not row.get("IUDMNPY"):
                continue
            day = datetime.strptime(row["DATE"], "%d %b %Y").date()
            rate = float(row["IUDMNPY"]) / 100
            if start <= day <= as_of and isfinite(rate):
                observations.setdefault(day, set()).add(rate)
    except (URLError, OSError, ValueError, KeyError, TypeError, csv.Error):
        return {"status": "missing", "url": url, "reason": "The Bank of England yield could not be read."}
    if not observations:
        return {"status": "missing", "url": url, "reason": "A recent GBP government yield is unavailable."}
    day = max(observations)
    rates = observations[day]
    if len(rates) != 1:
        return {"status": "conflicting", "url": url, "reason": "The latest GBP government yield is ambiguous."}
    return {"status": "available", "date": day.isoformat(), "rate": rates.pop(), "currency": "GBP",
            "instrument": "Bank of England 10-year gilt nominal par yield", "url": url}


def listed_unit_value(value_in_reporting_currency, ordinary_shares, listing, fx):
    """Convert aggregate common equity and ordinary shares to USD per listed unit."""
    ratio = listing.get("ordinary_shares_per_listed_unit")
    rate = fx.get("usd_per_unit")
    if (fx.get("status") != "available" or not rate or rate <= 0 or not isfinite(rate)
            or not ratio or ratio <= 0 or ordinary_shares <= 0 or not isfinite(ordinary_shares)
            or not isfinite(ratio) or not isfinite(value_in_reporting_currency)):
        raise ResearchGap("Verified ordinary shares, a depositary ratio, and dated FX are required.")
    return value_in_reporting_currency / ordinary_shares * ratio * rate

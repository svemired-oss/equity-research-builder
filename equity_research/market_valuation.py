"""Dated equity risk premium, price regression and debt cost inputs."""

import csv
from datetime import date, datetime, timedelta, timezone
from math import isfinite
import re
from statistics import covariance, median, variance
import sys
from time import sleep
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .market_quotes import MarketQuoteError, _BARS_SOURCE
from .sec_guidance import _ReleaseText

ERP = "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/home.htm"
INDEX = "https://fred.stlouisfed.org/series/SP500"
FRED = "https://fred.stlouisfed.org/graph/fredgraph.csv"
SPREAD = "https://fred.stlouisfed.org/series/BAMLC0A4CBBB"
# Dated observations verified against Damodaran's monthly posting. Keep these
# available for reproducible historical runs when the rolling homepage moves on.
ERP_SNAPSHOTS = {date(2026, 11, 1): .0441}


AGENT = "Equity Research Builder"
# FRED's edge drops connections whose User-Agent does not start with a known HTTP
# library name, so FRED requests identify as urllib plus this client.
FRED_AGENT = f"Python-urllib/{sys.version_info.major}.{sys.version_info.minor} equity-research-builder"
PROXY_SPREAD = .015
PROXY_BASIS = ("Analyst proxy: BBB spread unavailable. Pretax cost of debt is the dated Treasury yield "
               "plus a 1.5% borrowing spread.")


def _download(url, attempts=3, timeout=30, agent=AGENT):
    for attempt in range(attempts):
        try:
            with urlopen(Request(url, headers={"User-Agent": agent}), timeout=timeout) as response:
                return response.read(2_000_000).decode(errors="replace")
        except OSError:
            if attempt == attempts - 1:
                raise
            sleep(2 ** attempt)


SPY_PROXY = "SPY ETF split-adjusted closes as S&P 500 proxy"


def _index_series(client, start, end):
    """Prefer SPY bars from the market-data provider; FRED SP500 is the secondary source."""
    try:
        bars = client.adjusted_daily_bars("SPY", start, end)
        closes = {}
        for row in bars:
            day = date.fromisoformat(row["t"][:10])
            if start <= day <= end and day not in closes and isinstance(row.get("c"), (int, float)) and row["c"] > 0:
                closes[day] = row["c"]
        if len(closes) >= 400:
            return closes, _BARS_SOURCE, SPY_PROXY
    except (MarketQuoteError, ValueError, KeyError, TypeError, OSError):
        pass
    try:
        series = _series("SP500", start, end)
    except (OSError, ValueError):
        series = {}
    if not series:
        raise ValueError("Neither SPY bars nor the FRED S&P 500 series were available for beta.")
    return series, INDEX, "FRED S&P 500 index"


def dated_erp(html, cutoff):
    from .drivers import Tables
    text = " ".join([*_ReleaseText(html).blocks,
                     *(cell for table in Tables(html).tables for row in table for cell in row)])
    match = re.search(r"Implied ERP on ([A-Za-z]+ \d{1,2}, \d{4})\s*=\s*([\d.]+)\s*%"
                      r"\s*\(Trailing 12 month, with adjusted payout\)", text)
    if match:
        effective = datetime.strptime(match[1], "%B %d, %Y").date()
        rate = float(match[2])/100
        if 0 <= (cutoff-effective).days <= 45 and 0 < rate < .2:
            return {"value": rate, "date": str(effective), "url": ERP, "basis": "Damodaran adjusted payout implied ERP"}
    eligible = [day for day in ERP_SNAPSHOTS if 0 <= (cutoff-day).days <= 45]
    if eligible:
        effective = max(eligible)
        return {"value": ERP_SNAPSHOTS[effective], "date": str(effective), "url": ERP,
                "basis": "Archived Damodaran adjusted-payout implied ERP"}
    raise ValueError("No dated Damodaran adjusted-payout ERP within 45 days of the cutoff.")


def weekly_beta(stock, index, start, end):
    """Use the same last observed index session in each week and split-adjusted stock prices."""
    weeks = {}
    for day in sorted(index):
        if start <= day <= end:
            friday = day + timedelta(days=4-day.weekday())
            if day.weekday() <= 4 and friday <= end:
                weeks[friday] = day
    pairs = []
    for friday, day in weeks.items():
        previous = weeks.get(friday-timedelta(days=7))
        if previous is not None and day in stock and previous in stock:
            values = (stock[day], stock[previous], index[day], index[previous])
            if all(isinstance(v, (int, float)) and isfinite(v) and v > 0 for v in values):
                pairs.append((index[day]/index[previous]-1, stock[day]/stock[previous]-1))
    if len(pairs) < 90:
        raise ValueError("At least 90 matched weekly returns are required for two-year beta.")
    market, equity = zip(*pairs, strict=True)
    var = variance(market)
    if var <= 0:
        raise ValueError("Market returns have zero variance.")
    raw = covariance(market, equity)/var
    return {"raw": raw, "value": (2*raw+1)/3, "weeks": len(pairs), "start": str(start), "date": str(end),
            "url": _BARS_SOURCE, "index_url": INDEX, "basis": "Two-year weekly regression, Blume adjusted"}


def select_beta(subject, peers):
    if subject:
        return subject
    if len(peers) >= 2:
        return {"value": median(p["value"] for p in peers.values()), "peers": peers, "url": _BARS_SOURCE,
                "index_url": INDEX, "basis": "Median Blume beta of eligible same-industry peers",
                "date": next(iter(peers.values()))["date"]}
    raise ValueError("Subject beta unavailable and fewer than two eligible industry beta observations.")


def debt_cost(packet, risk_free, observed_spread=None):
    from .operating_research import _balance
    from copy import deepcopy
    annual = packet["actuals"]["periods"][-2:]
    if annual:
        latest = annual[-1]
        interest = latest["metrics"].get("interest_expense")
        balances = []
        for row in annual:
            try:
                balances.append((row, _balance(deepcopy(row))[3]))
            except ValueError:
                continue
        # Only the latest annual balance sheet may carry the enriched debt detail.
        if balances and balances[-1][0] is not latest:
            balances = []
        if isinstance(interest, (int,float)) and balances:
            average = sum(debt for _, debt in balances)/len(balances)
            note = ("Average of opening and closing funded debt" if len(balances) == 2 else
                    "Closing funded debt; the opening balance did not reconcile, so the average is unavailable")
            if average > 0 and 0 < interest/1e6/average < .35:
                return {"value": interest/1e6/average, "interest": interest/1e6, "average_debt": average,
                        "date": latest["period_end"], "url": latest["filing"]["url"],
                        "prior_url": balances[0][0]["filing"]["url"], "debt_note": note,
                        "basis": ("Filed interest expense / average carrying debt" if len(balances) == 2 else
                                  "Filed interest expense / closing carrying debt")}
        filed = _filed_debt_rate(annual, interest)
        if filed:
            return filed
    if observed_spread:
        return {**observed_spread, "value": risk_free+observed_spread["value"],
                "spread": observed_spread["value"], "basis": "BBB market spread proxy, issuer rating unavailable"}
    raise ValueError("Neither filed interest and average debt nor dated rating-band spread is available.")


# Filed debt totals in order of coverage, for when the full funded-debt bridge does not reconcile.
_FILED_DEBT = (
    (("current_debt", "long_term_debt"), "DebtCurrent + LongTermDebtNoncurrent"),
    (("long_term_debt_including_current",), "LongTermDebt"),
    (("long_term_debt_and_lease_obligations",), "LongTermDebtAndCapitalLeaseObligations"),
)


def _filed_debt_rate(annual, interest):
    """Filed interest expense / average of one filed total-debt basis used in both years."""
    latest = annual[-1]
    if type(interest) not in (int, float) or not isfinite(interest) or interest <= 0:
        return None

    def total(period, keys):
        values = [period["metrics"].get(key) for key in keys]
        if any(type(v) not in (int, float) or not isfinite(v) or v < 0 for v in values):
            return None
        return sum(values) / 1e6

    for keys, label in _FILED_DEBT:
        closing = total(latest, keys)
        if not closing:
            continue
        rows = [row for row in annual if total(row, keys) is not None]
        if rows[-1] is not latest:
            continue
        average = sum(total(row, keys) for row in rows) / len(rows)
        if average <= 0 or not 0 < interest/1e6/average < .35:
            continue
        both = len(rows) == 2
        note = (f"Average of opening and closing filed {label}" if both else
                f"Closing filed {label}; the opening balance is not filed on the same basis")
        return {"value": interest/1e6/average, "interest": interest/1e6, "average_debt": average,
                "date": latest["period_end"], "url": latest["filing"]["url"], "prior_url": rows[0]["filing"]["url"],
                "debt_note": note, "debt_tag": label,
                "basis": (f"Filed interest expense / {'average' if both else 'closing'} filed {label}. "
                          "Used because the funded-debt bridge did not reconcile")}
    return None


def proxy_debt_cost(risk_free, cutoff):
    """Last resort when no filed rate or dated spread exists. Labeled as an analyst proxy."""
    return {"value": risk_free + PROXY_SPREAD, "spread": PROXY_SPREAD, "date": str(cutoff), "url": None,
            "basis": PROXY_BASIS, "proxy": True}


def _series(series, start, end):
    params = urlencode({"id": series, "cosd": start, "coed": end})
    try:
        rows = csv.DictReader(_download(f"{FRED}?{params}", timeout=15, agent=FRED_AGENT).splitlines())
        records = [(row.get("observation_date") or row["DATE"], row[series]) for row in rows]
    except (OSError, ValueError, KeyError):
        records = []
    if not records:
        from .drivers import Tables
        html = _download(f"https://fred.stlouisfed.org/data/{series}", timeout=15, agent=FRED_AGENT)
        if not re.search(rf"\b{re.escape(series)}\b", html):
            raise ValueError(f"FRED did not identify series {series}.")
        records = [(row[0], row[1]) for table in Tables(html).tables for row in table
                   if len(row) == 2 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", row[0])]
    result = {}
    for day, value in records:
        stamp = date.fromisoformat(day)
        if start <= stamp <= end and value not in ("", "."):
            value = float(value)
            if isfinite(value):
                result[stamp] = value
    return result


def collect_market_inputs(packet, client, quote, peers, risk_free):
    end = date.fromisoformat(quote["trading_date"])
    cutoff = date.fromisoformat(packet["as_of"])
    start = end-timedelta(days=735)
    index, index_url, index_basis = _index_series(client, start, end)
    betas = {}
    ranges = None
    for ticker in (quote["ticker"], *peers):
        symbol = quote["provider_symbol"] if ticker == quote["ticker"] else re.sub(r"-([A-Z])$", r".\1", ticker)
        try:
            bars = client.adjusted_daily_bars(symbol, start, end)
            prices = {}
            for row in bars:
                day = date.fromisoformat(row["t"][:10])
                if start <= day <= end and day not in prices:
                    prices[day] = row
            if ticker == quote["ticker"]:
                year = [r for d,r in prices.items() if d >= end-timedelta(weeks=52)]
                if len(year) >= 240 and end in prices and all(
                        type(r.get(k)) in (int,float) and isfinite(r[k]) and r[k]>0 for r in year for k in ("h","l")):
                    ranges = {"low": min(r["l"] for r in year), "high": max(r["h"] for r in year),
                              "date": str(end), "url": _BARS_SOURCE}
            betas[ticker] = weekly_beta({day: r["c"] for day,r in prices.items()}, index, start, end)
            if ticker == quote["ticker"]:
                break
        except (MarketQuoteError, ValueError, KeyError, TypeError):
            continue
    for item in betas.values():
        item["index_url"], item["index_basis"] = index_url, index_basis
    beta = select_beta(betas.pop(quote["ticker"], None), betas)
    beta["index_url"], beta["index_basis"] = index_url, index_basis
    try:
        html = _download(ERP)
    except OSError:
        html = ""
    erp = dated_erp(html, cutoff)
    try:
        debt = debt_cost(packet, risk_free)
    except ValueError:
        try:
            spreads = _series("BAMLC0A4CBBB", end-timedelta(days=10), end)
        except (OSError, ValueError):
            spreads = {}
        if spreads:
            day = max(spreads)
            debt = debt_cost(packet, risk_free, {"value": spreads[day]/100, "date": str(day), "url": SPREAD})
        else:
            debt = proxy_debt_cost(risk_free, cutoff)
    return {"beta": beta, "erp": erp, "debt": debt, "range": ranges}


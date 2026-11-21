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


MIN_MONTHS = 36
HISTORICAL_ERP_PAGE = "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/histretSP.html"
# docs/method-notes.md, Rules for the automated model, rule 2. The value is the historical ERP recorded from
# the three reviewed models; the cited page (updated January 5, 2026) was not parsed to re-verify it.
HISTORICAL_ERP = {"value": .065, "date": "2026-01-05", "url": HISTORICAL_ERP_PAGE,
                  "basis": "Damodaran historical equity risk premium, 6.5% as recorded in docs/method-notes.md "
                           "from the reviewed models (page updated January 5, 2026; value not re-verified)"}


def _month_ends(index, start, end):
    """Last observed index session in each complete calendar month inside the window."""
    last = {}
    for day in sorted(index):
        if start <= day <= end:
            last[(day.year, day.month)] = day
    # The month containing `end` is incomplete unless it is also the window end month's final session.
    last.pop((end.year, end.month), None)
    return [last[key] for key in sorted(last)]


def monthly_beta(stock, index, start, end, minimum=MIN_MONTHS):
    """Five-year monthly OLS regression of split-adjusted stock returns on index returns. Raw slope, no adjustment."""
    sessions = _month_ends(index, start, end)
    pairs = []
    for previous, day in zip(sessions, sessions[1:]):
        if day in stock and previous in stock:
            values = (stock[day], stock[previous], index[day], index[previous])
            if all(isinstance(v, (int, float)) and isfinite(v) and v > 0 for v in values):
                pairs.append((index[day]/index[previous]-1, stock[day]/stock[previous]-1))
    if len(pairs) < max(minimum, 3):
        raise ValueError(f"At least {max(minimum, 3)} matched monthly returns are required for the five-year beta.")
    market, equity = zip(*pairs, strict=True)
    var = variance(market)
    if var <= 0:
        raise ValueError("Market returns have zero variance.")
    raw = covariance(market, equity)/var
    return {"raw": raw, "value": raw, "months": len(pairs), "start": str(sessions[0]), "date": str(sessions[-1]),
            "url": _BARS_SOURCE, "index_url": INDEX, "basis": "Five-year monthly regression, raw OLS slope"}


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


def _peer_leverage(packet, quote):
    """Funded debt / market equity and the median filed effective tax rate for comparable beta."""
    from copy import deepcopy
    from .operating_research import _balance
    periods = packet.get("interim_actuals", {}).get("periods") or packet["actuals"]["periods"]
    balance = deepcopy(periods[-1])
    try:
        metrics, _, _, debt, *_ = _balance(balance)
        basis = "Funded debt from the reconciled balance-sheet bridge"
    except ValueError:
        # Leverage for unlevering a peer beta only. Use a filed total, labeled; zero only if no debt fact is filed.
        metrics = {k: v / 1e6 for k, v in balance["metrics"].items() if type(v) in (int, float)}
        debt, basis = None, None
        for keys, label in _FILED_DEBT:
            values = [metrics.get(key) for key in keys]
            if all(type(v) in (int, float) and v >= 0 for v in values):
                debt, basis = sum(values), f"Filed {label}; the full balance-sheet bridge did not reconcile"
                break
        debt_keys = ("current_debt", "long_term_debt", "long_term_debt_including_current", "total_debt",
                     "long_term_debt_and_lease_obligations", "long_term_debt_and_finance_leases",
                     "current_portion_long_term_debt", "commercial_paper", "short_term_borrowings")
        if debt is None and not any(key in metrics for key in debt_keys):
            debt, basis = 0., "No funded debt fact is filed on the balance sheet; debt treated as zero"
        if debt is None:
            raise ValueError("Peer funded debt could not be read from a filed total.")
    shares = metrics.get("ending_shares")
    if type(shares) not in (int, float) or shares <= 0:
        raise ValueError("Peer common shares are unavailable.")
    rates = []
    for period in packet["actuals"]["periods"][-3:]:
        m = period["metrics"]
        taxes, pretax = m.get("income_taxes"), m.get("income_before_taxes")
        if type(taxes) in (int, float) and type(pretax) in (int, float) and pretax > 0:
            rates.append(taxes / pretax)
    if not rates or not 0 <= median(rates) < .5:
        raise ValueError("A filed effective tax rate between 0% and 50% is unavailable.")
    equity = shares * quote["close"]
    return {"debt": debt, "debt_basis": basis, "market_cap": equity, "debt_to_equity": debt / equity, "tax_rate": median(rates),
            "tax_years": len(rates), "balance_date": balance["period_end"],
            "balance_url": balance["filing"]["url"], "tax_url": packet["actuals"]["periods"][-1]["filing"]["url"],
            "quote_url": quote["url"], "close": quote["close"], "shares": shares}


def collect_market_inputs(packet, client, quote, peers, risk_free, peer_packets=None, peer_quotes=None):
    """Five-year monthly regression betas for the subject and peers, the historical ERP and debt cost.

    The subject regression is recorded at any length; the model applies the 36-month rule.
    """
    end = date.fromisoformat(quote["trading_date"])
    cutoff = date.fromisoformat(packet["as_of"])
    # From the first day of the month before the five-year mark, so its month-end anchors the first of 60 returns.
    start = (date(end.year - 5, end.month, 1) - timedelta(days=1)).replace(day=1)
    index, index_url, index_basis = _index_series(client, start, end)
    betas, failures = {}, {}
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
            subject = ticker == quote["ticker"]
            betas[ticker] = monthly_beta({day: r["c"] for day,r in prices.items()}, index, start, end,
                                         minimum=3 if subject else MIN_MONTHS)
        except (MarketQuoteError, ValueError, KeyError, TypeError) as exc:
            failures[ticker] = str(exc) or type(exc).__name__
            continue
    for item in betas.values():
        item["index_url"], item["index_basis"] = index_url, index_basis
    regression = betas.pop(quote["ticker"], None)
    if regression is None:
        regression = {"value": None, "months": 0, "unavailable": failures.get(quote["ticker"], "No price history."),
                      "basis": "Five-year monthly regression unavailable", "url": _BARS_SOURCE,
                      "date": str(end), "index_url": index_url, "index_basis": index_basis}
    comparable = {}
    for ticker in peers:
        if ticker not in betas:
            comparable[ticker] = {"excluded": f"Beta unavailable. {failures.get(ticker, '')}".strip()}
            continue
        try:
            leverage = _peer_leverage(peer_packets[ticker], peer_quotes[ticker])
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            comparable[ticker] = {"excluded": f"Leverage or tax evidence unavailable. {exc}".strip()}
            continue
        comparable[ticker] = {**betas[ticker], **leverage}
    try:
        html = _download(ERP)
    except OSError:
        html = ""
    try:
        implied = dated_erp(html, cutoff)
    except ValueError as exc:
        implied = {"value": None, "unavailable": str(exc), "url": ERP, "basis": "Damodaran implied ERP unavailable"}
    erp = {**HISTORICAL_ERP, "implied": implied}
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
    return {"beta": regression, "regression_beta": regression, "peer_betas": comparable,
            "erp": erp, "debt": debt, "range": ranges}

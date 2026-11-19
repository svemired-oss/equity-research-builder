"""Peer eligibility and multiple distributions for general operating-company research."""
from copy import deepcopy
from datetime import date
from math import isfinite
import re
from statistics import median, quantiles

from .drivers import FALLBACK, PLUGINS, extract
from .operating_comps import _earnings_basis
from .operating_research import ResearchGap, _balance

METRICS = {"EV / EBITDA": "ebitda", "EV / Sales": "sales", "P/E": "eps"}
SIZE_LOW, SIZE_HIGH = .25, 4.


def collect_forward_eps(packet, client):
    """Read an explicit USD next-12-month GAAP diluted EPS point or range."""
    from .sec_guidance import _ReleaseText
    pattern = re.compile(r"\b(?:expect|expects|project|projects|forecast|forecasts)\s+(?:next[- ]12[- ]months?\s+)"
                         r"GAAP diluted (?:EPS|earnings per share)\s+(?:of\s+|to be\s+)?\$(\d+(?:\.\d+)?)"
                         r"(?:\s*(?:to|[–-])\s*\$(\d+(?:\.\d+)?))?(?=[.\s,;]|$)", re.I)
    cutoff = date.fromisoformat(packet["as_of"])
    for release in packet.get("earnings", {}).get("releases", [])[:1]:
        day = date.fromisoformat(release["filed_on"])
        if not 0 <= (cutoff-day).days <= 120:
            continue
        try:
            blocks = _ReleaseText(client.get_text(release["url"])).blocks
        except (OSError, ValueError):
            continue
        values = set()
        for block in blocks:
            if re.search(r"non[- ]GAAP|adjusted|previous|withdraw|excluding", block, re.I):
                continue
            for lower, upper in pattern.findall(block):
                low, high = float(lower), float(upper or lower)
                if 0 < low <= high:
                    values.add((low+high)/2)
        if len(values) == 1 and packet["actuals"]["periods"][-1].get("sources", {}).get("revenue", {}).get("unit") == "USD":
            packet["forward_eps"] = {"value":values.pop(),"date":str(day),"url":release["url"]}


def driver_types(packet):
    return {kind for passage in packet.get("driver_passages", {}).values()
            for kind in PLUGINS if extract(packet, kind, passage)["type"] != FALLBACK}


def same_business(subject, peer, kind):
    if kind != FALLBACK:
        return kind in driver_types(peer)
    # A failed extraction alone establishes no business similarity.
    a, b = (str(p["issuer"].get("sic") or "") for p in (subject, peer))
    return len(a) == len(b) == 4 and a.isdigit() and a[:3] == b[:3] and not driver_types(peer)


def _profile(packet, quote, cutoff):
    end, eps, filings = _earnings_basis(packet, cutoff, trailing=True, require_positive=False)
    latest = packet["actuals"]["periods"][-1]
    interims = packet.get("earnings_interim", {})
    periods = [latest]
    if interims.get("current"):
        periods += [interims["current"][-1], interims["prior"][-1]]
    def ltm(key):
        values = [p["metrics"].get(key) for p in periods]
        if any(type(x) not in (float,int) or not isfinite(x) for x in values):
            return None
        return sum(x*s for x,s in zip(values, (1,1,-1)))/1e6
    sales, ebit, da = (ltm(k) for k in ("revenue", "operating_income", "depreciation_amortization"))
    balances = packet.get("interim_actuals", {}).get("periods", [])
    balance = deepcopy(balances[-1] if balances else latest)
    metrics, cash, investments, debt, *_ = _balance(balance)
    shares = metrics.get("ending_shares")
    if shares is None or shares <= 0 or quote["close"] <= 0:
        raise ResearchGap("Comparable market capitalization needs filed common shares and a close.")
    from .operating_evidence import apply_capital_events
    cash, debt, shares, _ = apply_capital_events(
        packet, date.fromisoformat(balance["period_end"]), cutoff, cash, debt, shares,
        date.fromisoformat(balance.get("share_count_date", balance["period_end"])))
    equity = shares*quote["close"]
    claims = debt + metrics.get("preferred_stock_book_value",0) + metrics.get("noncontrolling_interest",0)
    adjustment = cash+investments+balance.get("nonoperating_equity_investments", 0)/1e6-claims
    return {"end": str(end), "filings": list(dict.fromkeys((*filings,balance["filing"]["url"]))),
            "close": quote["close"], "quote_url": quote["url"], "shares": shares,
            "equity": equity, "ev": equity-adjustment, "adjustment": adjustment,
            "sales": sales, "ebitda": ebit+da if ebit is not None and da is not None else None,
            "eps": eps, "forward_eps": packet.get("forward_eps")}


def multiple_summary(rows, subject):
    results = {}
    for label, denominator in METRICS.items():
        values = [row["multiples"][label] for row in rows if label in row["multiples"]]
        base = subject[denominator]
        if len(values) < 2 or base is None or base <= 0:
            continue
        cuts = quantiles(values, n=4, method="inclusive")
        def implied(value):
            return value*base if label == "P/E" else (value*base+subject["adjustment"])/subject["shares"]
        results[label] = {"quartiles": cuts, "values": [implied(x) for x in cuts], "count": len(values)}
    return results


def prepare(subject, candidates, packets, quotes, kind):
    ticker = subject["issuer"]["ticker"]
    cutoff = date.fromisoformat(subject["as_of"])
    basis = _profile(subject, quotes[ticker], cutoff)
    rows, excluded = [], []
    seen = {subject["issuer"]["cik"]}
    for candidate in candidates:
        peer = packets.get(candidate["ticker"])
        quote = quotes.get(candidate["ticker"])
        name = candidate["ticker"]
        try:
            if not peer or not quote or quote["trading_date"] != quotes[ticker]["trading_date"] or quote["currency"] != "USD":
                raise ResearchGap("Same-session USD market and SEC evidence required.")
            issuer = peer["issuer"]
            if issuer["ticker"] != name or issuer["cik"] in seen or not same_business(subject, peer, kind):
                raise ResearchGap("Distinct issuer with the same driver type required.")
            row = _profile(peer, quote, cutoff)
            ratio = row["equity"]/basis["equity"]
            if not SIZE_LOW <= ratio <= SIZE_HIGH:
                raise ResearchGap("Market capitalization outside 0.25x–4x subject band.")
            if abs((date.fromisoformat(row["end"])-date.fromisoformat(basis["end"])).days) > 100:
                raise ResearchGap("Trailing periods end more than 100 days apart.")
        except ResearchGap as exc:
            excluded.append([name,str(exc)])
            continue
        seen.add(issuer["cik"])
        row.update(ticker=name, multiples={})
        for label,key in METRICS.items():
            numerator = row["close"] if label == "P/E" else row["ev"]
            if row[key] is not None and row[key] > 0 and numerator > 0:
                row["multiples"][label] = numerator/row[key]
        rows.append(row)
        if len(rows) == 5:
            break
    forward = [row for row in rows if row["forward_eps"]]
    use_forward = bool(basis["forward_eps"] and len(forward)>=2)
    if use_forward:
        basis["eps"] = basis["forward_eps"]["value"]
        for row in rows:
            evidence = row["forward_eps"]
            if evidence:
                row["eps"] = evidence["value"]
                row["multiples"]["P/E"] = row["close"]/row["eps"]
            else:
                row["eps"] = None
                row["multiples"].pop("P/E",None)
    summary = multiple_summary(rows,basis)
    medians = [r["values"][1] for r in summary.values()]
    return {"subject": basis, "rows": rows, "excluded": excluded, "summary": summary,
            "value": median(medians) if medians else None, "basis": "LTM",
            "driver": kind, "quote_date": quotes[ticker]["trading_date"], "pe_basis": "Forward GAAP NTM" if use_forward else "LTM GAAP"}


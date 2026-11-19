"""Comparable-bank valuation using same-quarter tangible book and same-session closes."""

from datetime import date
import re
from statistics import median

from .gd_releases import _Page
from .market_quotes import collect_market_quotes
from .operating_research import ResearchGap
from .sec_intake import collect_ticker_evidence


_BANKS = {"JPM": "0000019617", "BAC": "0000070858", "WFC": "0000072971"}
_QUARTERS = {3: "First", 6: "Second", 9: "Third", 12: "Fourth"}


def read_tangible_book(document, ticker, balance_date):
    if ticker not in _BANKS or balance_date.month not in _QUARTERS:
        raise ResearchGap("This bank or reporting quarter lacks a reviewed tangible book format.")
    values = set()
    for table in _Page(document).tables:
        rows = [[cell.text for cell in row if cell.text.strip() and cell.text != "$"] for row in table.rows]
        rows = [row for row in rows if row]
        header = " ".join(" ".join(row) for row in rows[:4])
        if ticker == "JPM":
            marker = f"{balance_date.month // 3}Q{balance_date.year % 100:02d}"
            valid = any(row[0] == marker for row in rows[:4])
        elif ticker == "BAC":
            valid = f"{balance_date.year} Quarters" in header and any(
                len(row) > 1 and row[1] == _QUARTERS[balance_date.month] for row in rows[:4])
        else:
            stamp = rf"{balance_date:%b}\s+{balance_date.day},\s*{balance_date.year}"
            valid = header.startswith("Summary Financial Data") and re.search(stamp, header)
        if not valid:
            continue
        for row in rows:
            if (len(row) >= 2 and re.match(r"Tangible book value(?:\b|\()", row[0], re.I)
                    and re.fullmatch(r"[\d,]+\.\d{2}", row[1])):
                values.add(float(row[1].replace(",", "")))
    if len(values) != 1 or min(values) <= 0:
        raise ResearchGap(f"{ticker} did not uniquely disclose positive tangible book per share for {balance_date}.")
    return values.pop()


def prepare_bank_comps(client, quote_client, packet, balance_date):
    cutoff = date.fromisoformat(packet["as_of"])
    symbol = packet["issuer"]["ticker"]
    if _BANKS.get(symbol) != packet["issuer"]["cik"]:
        raise ResearchGap("The bank does not match a reviewed common stock issuer.")
    market = collect_market_quotes(quote_client, _BANKS, as_of=cutoff)
    session = market["latest_completed_session"]
    if not session or not 0 <= (date.fromisoformat(session) - balance_date).days <= 120:
        raise ResearchGap("A recent bank balance date and a completed market session are required.")
    entries = {}
    for ticker, cik in _BANKS.items():
        evidence = packet if ticker == symbol else collect_ticker_evidence(
            client, ticker, as_of=cutoff, years=1, statements_only=True)
        issuer = evidence["issuer"]
        if issuer["cik"] != cik or issuer["ticker"] != ticker or str(issuer.get("sic")) != "6021":
            raise ResearchGap("The bank comparable does not match the reviewed common stock issuer.")
        filings = evidence["earnings_interim"]["current"]
        quote = market["quotes"].get(ticker)
        if not filings or filings[-1]["period_end"] != balance_date.isoformat() or not quote or (
                quote["trading_date"] != session or quote["currency"] != "USD" or quote["close"] <= 0):
            raise ResearchGap("Bank comparables require the same quarter-end and US trading session.")
        url = filings[-1]["filing"]["url"]
        book = read_tangible_book(client.get_text(url), ticker, balance_date)
        entries[ticker] = dict(ticker=ticker, book=book, close=quote["close"], multiple=quote["close"]/book, source=url,
                               quote_source=quote["url"])
    subject = entries.pop(symbol)
    peers = list(entries.values())
    multiple = median(row["multiple"] for row in peers)
    return {"subject": subject, "rows": peers, "quote_date": session, "median": multiple,
            "value": multiple * subject["book"]}

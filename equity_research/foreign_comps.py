"""Check the same-session U.S. price against filed annual EPS, including ADS conversion."""

from datetime import date
from statistics import median

from .foreign_fx import listed_unit_value
from .market_quotes import collect_market_quotes
from .operating_comps import _earnings_basis
from .operating_research import ResearchGap
from .sec_intake import collect_ticker_evidence


_PEERS = {
    "SAP": {"CRM": "Enterprise cloud applications and subscription sales.",
            "ORCL": "Enterprise applications, databases and cloud infrastructure.",
            "ADBE": "Subscription enterprise and creative software with different end markets."},
    "HLN": {"CL": "Oral care and consumer health-adjacent retail distribution.",
            "CHD": "Consumer brands with oral care and over-the-counter health exposure.",
            "PG": "Oral health and personal care with a broader consumer portfolio."},
}


def collect_foreign_comps(client, quote_client, issuer, financials, quote, fx, as_of):
    """Collect two or more eligible annual P/E comparables with independent identity checks."""
    candidates = _PEERS.get(issuer["ticker"])
    eps = financials.latest_diluted_eps
    if not candidates or eps is None or eps <= 0 or not quote or quote["trading_date"] != fx.get("date"):
        return None
    subject_end = date.fromisoformat(financials.periods[-1]["period_end"])
    quotes = collect_market_quotes(quote_client, candidates, as_of=as_of)["quotes"]
    seen = {issuer["cik"]}
    rows = []
    for symbol, reason in candidates.items():
        close = quotes.get(symbol)
        if not close or close["trading_date"] != quote["trading_date"] or close["currency"] != "USD":
            continue
        try:
            packet = collect_ticker_evidence(client, symbol, as_of=as_of, years=1, statements_only=True)
            peer = packet["issuer"]
            if peer["ticker"] != symbol or peer["cik"] in seen or packet.get("foreign_filings"):
                continue
            end, earnings, sources = _earnings_basis(packet, as_of, trailing=False)
        except (ResearchGap, ValueError, OSError):
            continue
        if abs((subject_end - end).days) > 190:
            continue
        seen.add(peer["cik"])
        rows.append({"ticker": symbol, "reason": reason, "fiscal_end": end.isoformat(),
                     "close": close["close"], "eps": earnings, "multiple": close["close"] / earnings,
                     "quote_source": close["url"], "filings": sources})
    if len(rows) < 2:
        return None
    subject_eps = listed_unit_value(eps, 1, financials.listing, fx)
    multiple = median(row["multiple"] for row in rows)
    return {"basis": "Latest filed fiscal-year diluted earnings", "quote_date": quote["trading_date"],
            "subject_end": subject_end.isoformat(), "subject_eps_local": eps, "subject_eps_usd": subject_eps,
            "rows": rows, "median": multiple, "value": multiple * subject_eps}

"""Verify net-lease peer multiples against comparable full-year AFFO guidance."""

from datetime import date
from math import isfinite
import re
from statistics import median

from .operating_research import ResearchGap
from .sec_guidance import _ReleaseText


def read_affo_guidance(html, year):
    """Read uniquely stated USD AFFO-per-share guidance in announcement prose."""
    pattern = re.compile(rf"\b{year}\s+AFFO\s+per\s+(?:diluted\s+)?(?:common\s+)?share\s+guidance"
                         r"\s*(?:to|of|is|range of)?\s*\$\s*(\d+\.\d{2})"
                         r"\s*(?:to|[-–—])\s*\$\s*(\d+\.\d{2})", re.I)
    ranges = {(float(m[1]),float(m[2])) for block in _ReleaseText(html).blocks for m in pattern.finditer(block)}
    if len(ranges)!=1:
        raise ResearchGap("A unique current-year AFFO guidance range was not disclosed.")
    low, high = ranges.pop()
    if not 0 < low <= high:
        raise ResearchGap("Filed AFFO guidance does not support a positive common multiple.")
    return (low+high)/2


def prepare_reit_comparables(packets, quotes, market_date, client):
    """Use two net-lease issuers and verify the common closing session."""
    year = date.fromisoformat(market_date).year
    peers = {
        "ADC": "U.S. retail net-lease properties with investment-grade tenant exposure.",
        "NNN": "Diversified U.S. single-tenant retail net leases.",
    }
    rows = []
    for ticker in ("O",*peers):
        packet = packets.get(ticker)
        quote = quotes.get(ticker)
        if (not packet or str(packet["issuer"].get("sic"))!="6798" or packet["issuer"]["ticker"]!=ticker
                or not quote or quote["ticker"]!=ticker or quote["trading_date"]!=market_date or quote["currency"]!="USD"
                or not isfinite(quote["close"]) or quote["close"]<=0):
            raise ResearchGap(f"{ticker} needs a verified REIT issuer and USD close from the same session.")
        releases = packet["earnings"]["releases"]
        if not releases:
            raise ResearchGap(f"{ticker} has no filed earnings release for AFFO guidance.")
        latest = releases[0]
        if not 0 <= (date.fromisoformat(packet["as_of"])-date.fromisoformat(latest["filed_on"])).days <= 120:
            raise ResearchGap(f"{ticker} has stale AFFO guidance.")
        amount = read_affo_guidance(client.get_text(latest["url"]),year)
        rows.append({"ticker":ticker,"close":quote["close"],"quote_source":quote["url"],
                     "affo":amount,"multiple":quote["close"]/amount,"source":latest["url"],
                     "reason":peers.get(ticker)})
    subject,*comps = rows
    multiple = median(row["multiple"] for row in comps)
    return {"rows":comps,"subject":subject,"median":multiple,"value":multiple*subject["affo"],
            "quote_date":market_date,"year":year}

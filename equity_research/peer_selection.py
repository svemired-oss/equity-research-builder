"""Screen research-proposed peers against fixed SEC industry and size rules.

The AI proposes tickers. Code keeps only those that meet these rules, and the
final list is saved with the evidence so an offline replay uses the same peers.
"""

from math import isfinite

SIZE_LOW, SIZE_HIGH = .1, 10.
MIN_PEERS = 3
MAX_PEERS = 5
THREE_DIGIT = "3-digit SIC"
TWO_DIGIT = "2-digit SIC fallback"

PEER_RULES = (
    "Each peer must be a U.S. GAAP 10-K filer whose SEC SIC code (issuer.sic) shares the subject's first three "
    "digits. Peers sharing only the first two digits are used only as a labeled fallback when fewer than three "
    f"3-digit peers qualify. Market capitalization must be between {SIZE_LOW:g}x and {SIZE_HIGH:g}x the subject's."
)


def sic_tier(subject_sic, peer_sic):
    """Return the industry tier a peer qualifies under, or None."""
    a, b = (str(code or "") for code in (subject_sic, peer_sic))
    if not (len(a) == len(b) == 4 and a.isdigit() and b.isdigit()) or 6000 <= int(b) <= 6799:
        return None
    if a[:3] == b[:3]:
        return THREE_DIGIT
    if a[:2] == b[:2]:
        return TWO_DIGIT
    return None


def _market_cap(packet, quote):
    """Latest filed common shares (interim first, then annual) times the close, USD millions."""
    periods = [*packet["actuals"]["periods"], *packet.get("interim_actuals", {}).get("periods", ())]
    periods.sort(key=lambda period: period.get("period_end", ""))
    shares = next((p["metrics"]["ending_shares"] for p in reversed(periods)
                   if type(p["metrics"].get("ending_shares")) in (int, float)), None)
    close = quote.get("close")
    if (type(shares) not in (int, float) or not isfinite(shares) or shares <= 0
            or type(close) not in (int, float) or not isfinite(close) or close <= 0):
        return None
    return shares / 1e6 * close


def screen_peers(subject, proposals, packets, quotes):
    """Apply the SIC and size rules to every proposal. Returns the saved peer selection."""
    ticker = subject["issuer"]["ticker"]
    subject_quote = quotes[ticker]
    subject_cap = _market_cap(subject, subject_quote)
    if subject_cap is None:
        return {"subject_sic": str(subject["issuer"].get("sic") or ""), "subject_market_cap": None, "tier": "none",
                "peers": [], "rejected": [[row["ticker"], "Subject market capitalization is unavailable."]
                                          for row in proposals], "rules": PEER_RULES}
    sic = str(subject["issuer"].get("sic") or "")
    qualified, rejected, seen = [], [], {subject["issuer"]["cik"]}
    for proposal in proposals:
        symbol = proposal["ticker"]
        if symbol == ticker or symbol in {row["ticker"] for row in qualified} or symbol in {r[0] for r in rejected}:
            continue
        peer, quote = packets.get(symbol), quotes.get(symbol)
        if peer is None or not peer["actuals"]["periods"]:
            rejected.append([symbol, "No U.S. GAAP 10-K evidence was available."])
            continue
        if (not quote or quote["trading_date"] != subject_quote["trading_date"] or quote["currency"] != "USD"):
            rejected.append([symbol, "No USD close on the subject's quote date."])
            continue
        issuer = peer["issuer"]
        if issuer["cik"] in seen or issuer["ticker"] != symbol:
            rejected.append([symbol, "Duplicate issuer or listed ticker differs from the SEC record."])
            continue
        tier = sic_tier(sic, issuer.get("sic"))
        if tier is None:
            rejected.append([symbol, f"SIC {issuer.get('sic') or 'unavailable'} shares neither the 3-digit nor the "
                                     f"2-digit code of subject SIC {sic}."])
            continue
        cap = _market_cap(peer, quote)
        if cap is None:
            rejected.append([symbol, "Market capitalization needs filed common shares and a close."])
            continue
        ratio = cap / subject_cap
        if not SIZE_LOW <= ratio <= SIZE_HIGH:
            rejected.append([symbol, f"Market capitalization {ratio:.2f}x the subject, outside {SIZE_LOW:g}x-{SIZE_HIGH:g}x."])
            continue
        seen.add(issuer["cik"])
        qualified.append({"ticker": symbol, "sic": str(issuer.get("sic")), "tier": tier,
                          "market_cap_ratio": ratio, "reason": proposal.get("reason", "")})
    three = [row for row in qualified if row["tier"] == THREE_DIGIT]
    if len(three) >= MIN_PEERS:
        final = three
        rejected.extend([row["ticker"], "2-digit SIC fallback not needed: 3 or more 3-digit SIC peers qualified."]
                        for row in qualified if row["tier"] == TWO_DIGIT)
    else:
        final = three + [row for row in qualified if row["tier"] == TWO_DIGIT]
    for row in final[MAX_PEERS:]:
        rejected.append([row["ticker"], f"Beyond the first {MAX_PEERS} qualifying peers."])
    final = final[:MAX_PEERS]
    tiers = {row["tier"] for row in final}
    return {"subject_sic": sic, "subject_market_cap": subject_cap,
            "tier": (THREE_DIGIT if tiers == {THREE_DIGIT} else TWO_DIGIT if final else "none"),
            "peers": final, "rejected": rejected,
            "rules": PEER_RULES}


def needs_more(selection):
    return sum(row["tier"] == THREE_DIGIT for row in selection["peers"]) < MIN_PEERS

"""Verify equity multiples from dated filings and same-session market quotes."""

from datetime import date
from math import isclose, isfinite
from statistics import median

from .operating_research import ResearchGap


def _filed_earnings(period):
    metrics, sources = period["metrics"], period.get("sources", {})
    net_income = metrics.get("net_income")
    shares = metrics.get("diluted_shares")
    unit = sources.get("net_income", {}).get("unit")
    if net_income is None and isinstance(shares, (int, float)) and shares > 0:
        consolidated = metrics.get("consolidated_net_income")
        eps = metrics.get("diluted_eps")
        earnings_source = sources.get("consolidated_net_income", {})
        eps_source = sources.get("diluted_eps", {})
        if (isinstance(consolidated, (int, float)) and isinstance(eps, (int, float))
                and earnings_source.get("accession") == eps_source.get("accession") == sources.get("diluted_shares", {}).get("accession")
                and eps_source.get("unit") == "USD/shares"
                and isclose(consolidated / shares, eps, abs_tol=.011)):
            net_income = consolidated
            unit = earnings_source.get("unit")
    if (type(net_income) not in (int, float) or not isfinite(net_income)
            or type(shares) not in (int, float) or not isfinite(shares) or shares <= 0
            or unit != "USD"):
        raise ResearchGap("Verified USD common earnings and diluted shares are required.")
    return net_income, shares


def _earnings_basis(packet, as_of, *, trailing, require_positive=True):
    periods = packet["actuals"]["periods"]
    if not periods or packet["as_of"] != as_of.isoformat():
        raise ResearchGap("No annual financials for this valuation cutoff.")
    year = periods[-1]
    try:
        end = date.fromisoformat(year["period_end"])
        start = date.fromisoformat(year["period_start"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ResearchGap("The filing lacks a verified earnings period.") from exc
    if not 350 <= (end - start).days <= 378 or not 0 <= (as_of - end).days <= 460:
        raise ResearchGap("The filed period is stale or is not a full fiscal year.")
    net_income, shares = _filed_earnings(year)
    sources = [year["filing"]["url"]]
    interims = packet.get("earnings_interim", {})
    if trailing and interims.get("current"):
        if not interims.get("prior"):
            raise ResearchGap("A matching prior-year fiscal YTD earnings period is required.")
        current, prior = interims["current"][-1], interims["prior"][-1]
        try:
            current_end, prior_end = (date.fromisoformat(p["period_end"]) for p in (current, prior))
            current_start, prior_start = (date.fromisoformat(p["period_start"]) for p in (current, prior))
        except (KeyError, TypeError, ValueError) as exc:
            raise ResearchGap("Fiscal YTD earnings dates could not be verified.") from exc
        ytd_days = (current_end - current_start).days + 1
        prior_days = (prior_end - prior_start).days + 1
        year_days = (end - start).days + 1
        if ((current_start - end).days != 1 or prior_start != start or not start <= prior_end < end
                or not 357 <= (current_end - prior_end).days <= 373 or abs(ytd_days - prior_days) > 7
                or not 60 <= ytd_days < 300):
            raise ResearchGap("Fiscal YTD earnings periods are not comparable.")
        current_income, current_shares = _filed_earnings(current)
        prior_income, prior_shares = _filed_earnings(prior)
        net_income += current_income - prior_income
        shares = (shares * year_days + current_shares * ytd_days - prior_shares * prior_days) / (
            year_days + ytd_days - prior_days)
        end = current_end
        sources.extend(p["filing"]["url"] for p in (current, prior))
    if (require_positive and net_income <= 0) or shares <= 0:
        raise ResearchGap("Positive trailing common earnings and diluted shares are required.")
    if trailing and not 0 <= (as_of - end).days <= 160:
        raise ResearchGap("The trailing earnings period is stale.")
    return end, net_income / shares, tuple(dict.fromkeys(sources))


def prepare_comparables(subject, candidates, packets, quotes, quote_date, *, industry_sic=None):
    """Prefer comparable trailing periods, falling back to a wholly annual set."""
    if industry_sic is not None and str(subject["issuer"].get("sic")) != industry_sic:
        raise ResearchGap("The subject does not match the selected peer industry.")
    try:
        recent = _prepare(subject, candidates, packets, quotes, quote_date, trailing=True, industry_sic=industry_sic)
    except ResearchGap:
        return _prepare(subject, candidates, packets, quotes, quote_date, trailing=False, industry_sic=industry_sic)
    if recent["value"] is not None:
        return recent
    annual = _prepare(subject, candidates, packets, quotes, quote_date, trailing=False, industry_sic=industry_sic)
    return annual if annual["value"] is not None else recent


def _prepare(subject, candidates, packets, quotes, quote_date, *, trailing, industry_sic=None):
    as_of = date.fromisoformat(subject["as_of"])
    subject_end, subject_eps, subject_sources = _earnings_basis(subject, as_of, trailing=trailing)
    rows, excluded = [], []
    seen = {subject["issuer"]["cik"]}
    for candidate in candidates:
        ticker = candidate["ticker"]
        try:
            quote = quotes.get(ticker)
            if not quote or quote["ticker"] != ticker or quote["trading_date"] != quote_date or quote["currency"] != "USD":
                raise ResearchGap("No verified USD close on the subject's quote date.")
            peer = packets.get(ticker)
            if peer is None:
                raise ResearchGap("SEC financials could not be verified.")
            issuer = peer["issuer"]
            if issuer["ticker"] != ticker or issuer["cik"] in seen:
                raise ResearchGap("Issuer is duplicated or the listed ticker differs.")
            sic = str(issuer.get("sic") or "")
            if industry_sic is not None and sic != industry_sic:
                raise ResearchGap("The peer's SEC industry differs from the subject's.")
            if industry_sic is None and (not sic.isdigit() or 6000 <= int(sic) <= 6799):
                raise ResearchGap("An operating-company classification is required.")
            end, eps, sources = _earnings_basis(peer, as_of, trailing=trailing)
            limit = 100 if trailing else 190
            if abs((end - subject_end).days) > limit:
                raise ResearchGap(f"The earnings periods end more than {limit} days apart.")
            close = quote["close"]
            if type(close) not in (int, float) or not isfinite(close) or close <= 0:
                raise ResearchGap("The closing price is invalid.")
        except ResearchGap as exc:
            excluded.append((ticker, str(exc)))
            continue
        seen.add(issuer["cik"])
        rows.append({"ticker": ticker, "end": end, "eps": eps, "close": close, "multiple": close / eps,
                     "filings": sources, "quote_source": quote["url"], "reason": candidate["reason"]})
    multiple = median(row["multiple"] for row in rows) if len(rows) >= 2 else None
    return {"rows": rows, "excluded": excluded, "subject_eps": subject_eps, "subject_end": subject_end,
            "subject_filings": subject_sources, "quote_date": quote_date, "median": multiple,
            "basis": "Trailing 12 months" if trailing else "Reported fiscal year",
            "basis_note": ("Trailing figures use filed annual results plus current YTD less matched prior YTD. "
                           "Diluted shares use day weights. Period ends can differ by up to 100 days."
                           if trailing else "Reported fiscal year ends can differ by up to 190 days."),
            "value": multiple * subject_eps if multiple is not None else None}

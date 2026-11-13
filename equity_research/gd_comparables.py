"""Calculate dated trading multiples from reported earnings and capitalization."""

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from statistics import median

from .gd_inputs import InputSource, _array, _day, _fields, _number, _read, _source, _text
from .gd_run_inputs import DatedClose, _header, _public_url, load_market_inputs


@dataclass(frozen=True)
class ReportedEarnings:
    period_end: date
    operating_earnings_millions: float
    common_net_earnings_millions: float


@dataclass(frozen=True)
class ComparableEvidence:
    ticker: str
    selection_reason: str
    close: DatedClose
    annual: ReportedEarnings
    prior_interim: ReportedEarnings
    interim: ReportedEarnings
    annual_source: InputSource
    interim_source: InputSource
    shares: int
    shares_on: date
    cash_millions: float
    carrying_debt_millions: float
    noncontrolling_interest_millions: float
    preferred_stock_millions: float


@dataclass(frozen=True)
class TradingMultiples:
    evidence: ComparableEvidence
    market_cap_millions: float
    enterprise_value_millions: float
    ltm_ebit_millions: float
    ltm_common_earnings_millions: float
    ev_to_ebit: float | None
    price_to_earnings: float | None


@dataclass(frozen=True)
class GDComparables:
    as_of: date
    reference_close_date: date
    subject: TradingMultiples
    peers: tuple[TradingMultiples, ...]
    median_ev_to_ebit: float | None
    median_price_to_earnings: float | None
    peer_count_ev_to_ebit: int
    peer_count_price_to_earnings: int
    implied_price_ev_to_ebit: float | None
    implied_price_price_to_earnings: float | None
    notes: tuple[str, ...]


def _reported(raw: object, label: str) -> ReportedEarnings:
    fields = _fields(raw, label, {"period_end", "operating_earnings", "common_net_earnings"})
    return ReportedEarnings(
        _day(fields["period_end"], f"{label} period end"),
        _number(fields["operating_earnings"], f"{label} operating earnings"),
        _number(fields["common_net_earnings"], f"{label} common net earnings"),
    )


def _company(raw: object, as_of: date) -> ComparableEvidence:
    row = _fields(raw, "company", {
        "ticker", "selection_reason", "close", "annual", "prior_interim", "interim",
        "annual_source", "interim_source", "capital",
    })
    ticker = _text(row["ticker"], "ticker")
    if not ticker.isascii() or not ticker.isalpha() or not ticker.isupper():
        raise ValueError(f"Invalid comparable ticker {ticker!r}.")
    annual, prior, interim = (_reported(row[key], f"{ticker} {key}") for key in ("annual", "prior_interim", "interim"))
    annual_source = _source(row["annual_source"], f"{ticker} annual filing", as_of)
    interim_source = _source(row["interim_source"], f"{ticker} interim filing", as_of)
    if (annual.period_end != date(as_of.year - 1, 12, 31) or prior.period_end.year != as_of.year - 1
            or interim.period_end.year != as_of.year
            or not all(end.month in (9, 10) and abs((end - date(end.year, 9, 30)).days) <= 7
                       for end in (prior.period_end, interim.period_end))):
        raise ValueError(f"{ticker} requires a prior December year-end and matched third-quarter periods.")
    for result, source in ((annual, annual_source), (interim, interim_source)):
        if source.kind != "sec_filing" or not _public_url(source.url) or source.available_on < result.period_end:
            raise ValueError(f"{ticker} needs dated public SEC sources for its financials.")

    quote = _fields(row["close"], f"{ticker} close", {"price", "observed_on", "price_type", "source"})
    price = _number(quote["price"], f"{ticker} close price")
    observed_on = _day(quote["observed_on"], f"{ticker} close date")
    source = _source(quote["source"], f"{ticker} close source", as_of)
    if (price <= 0 or quote["price_type"] != "unadjusted_close" or observed_on.weekday() >= 5
            or not 0 < (as_of - observed_on).days <= 7 or not observed_on <= source.available_on
            or source.kind != "market_data" or not _public_url(source.url)):
        raise ValueError(f"{ticker} needs a positive unadjusted prior close with a dated public source.")
    if interim_source.available_on > observed_on:
        raise ValueError(f"{ticker} interim financials must have been public by the reference close.")

    keys = ("cash", "carrying_debt", "noncontrolling_interest", "preferred_stock")
    capital = _fields(row["capital"], f"{ticker} capitalization", {"shares", "shares_on", *keys})
    shares = capital["shares"]
    shares_on = _day(capital["shares_on"], f"{ticker} shares date")
    amounts = tuple(_number(capital[key], f"{ticker} {key}") for key in keys)
    if (type(shares) is not int or shares <= 0 or min(amounts) < 0
            or not interim.period_end <= shares_on <= interim_source.available_on):
        raise ValueError(f"{ticker} needs valid reported capitalization and a dated share count.")
    return ComparableEvidence(
        ticker, _text(row["selection_reason"], f"{ticker} selection reason"), DatedClose(price, observed_on, source),
        annual, prior, interim, annual_source, interim_source, shares, shares_on, *amounts,
    )


def _multiples(company: ComparableEvidence) -> TradingMultiples:
    equity = company.shares * company.close.price / 1_000_000
    enterprise = (equity + company.carrying_debt_millions + company.noncontrolling_interest_millions
                  + company.preferred_stock_millions - company.cash_millions)
    annual, prior, current = company.annual, company.prior_interim, company.interim
    ebit = annual.operating_earnings_millions - prior.operating_earnings_millions + current.operating_earnings_millions
    earnings = annual.common_net_earnings_millions - prior.common_net_earnings_millions + current.common_net_earnings_millions
    return TradingMultiples(
        company, equity, enterprise, ebit, earnings,
        enterprise / ebit if enterprise > 0 and ebit > 0 else None,
        equity / earnings if earnings > 0 else None,
    )


def run_gd_comparables(as_of: date, inputs_dir: Path) -> GDComparables:
    """Reconcile the market date and compare GD with the selected peers."""
    stamp = as_of.isoformat()
    path = inputs_dir / f"gd-comparables-{stamp}.json"
    item = _fields(_read(path), "GD comparable inputs", {"schema_version", "ticker", "currency", "as_of", "companies"})
    market = load_market_inputs(inputs_dir / f"gd-market-{stamp}.json")
    close = market.valuation_close
    if as_of != _header(item) or market.as_of != as_of or close is None:
        raise ValueError("The comparable set and GD market inputs must use the requested cutoff.")
    companies = [_company(raw, as_of) for raw in _array(item["companies"], "comparable companies")]
    by_ticker = {company.ticker: company for company in companies}
    if len(by_ticker) != len(companies) or "GD" not in by_ticker or len(companies) < 3:
        raise ValueError("The comparison requires GD and at least two distinct peers.")
    if any(company.close.observed_on != close.observed_on for company in companies):
        raise ValueError("Every company must use the same reference trading session.")
    if by_ticker["GD"].close.price != close.price:
        raise ValueError("The GD reference close disagrees with the DCF market snapshot.")

    subject = _multiples(by_ticker["GD"])
    peers = tuple(_multiples(company) for company in companies if company.ticker != "GD")
    ev_multiples = [peer.ev_to_ebit for peer in peers if peer.ev_to_ebit is not None]
    pe_multiples = [peer.price_to_earnings for peer in peers if peer.price_to_earnings is not None]
    ev_median = median(ev_multiples) if len(ev_multiples) >= 2 else None
    pe_median = median(pe_multiples) if len(pe_multiples) >= 2 else None
    net_claims = subject.enterprise_value_millions - subject.market_cap_millions
    shares_millions = subject.evidence.shares / 1_000_000
    implied_ev = ev_median * subject.ltm_ebit_millions - net_claims if ev_median is not None else None
    implied_pe = pe_median * subject.ltm_common_earnings_millions if pe_median is not None else None
    return GDComparables(
        as_of, close.observed_on, subject, peers, ev_median, pe_median, len(ev_multiples), len(pe_multiples),
        implied_ev / shares_millions if implied_ev is not None and implied_ev > 0 and subject.ev_to_ebit is not None else None,
        implied_pe / shares_millions if implied_pe is not None and subject.price_to_earnings is not None else None,
        (
            "Use the preceding session's unadjusted close and latest filed cover shares for each company. "
            "The DCF uses a separately modeled GD share count at cutoff.",
            "Use reported operating earnings and net earnings attributable to common for FY2025 plus 9M2026 less 9M2025. "
            "Reported contract charges, pension classification and other accounting differences can affect comparability.",
            "Enterprise value adds balance-sheet carrying debt, reported noncontrolling interests and preferred shares "
            "and subtracts cash and equivalents. Lease liabilities outside reported debt and pension deficits are outside this EV convention.",
            "Medians exclude GD and require at least two valid peers per metric. Nonpositive denominators are unavailable.",
            "Implied prices apply the trailing peer medians to GD's trailing earnings using reported capitalization at the snapshot. "
            "They are spot valuation cross-checks.",
        ),
    )

"""FedEx trading comparables on a common closing-price and LTM reporting basis."""

from dataclasses import dataclass
from datetime import date
from math import isclose
from pathlib import Path
from statistics import median
from urllib.parse import urlsplit

from .gd_inputs import InputSource, _array, _day, _fields, _number, _object, _read, _source, _text
from .gd_run_inputs import DatedClose, _public_url


@dataclass(frozen=True)
class FDXPeerEarnings:
    period_start: date
    period_end: date
    ebit_millions: float
    net_income_millions: float
    diluted_shares_millions: float | None
    diluted_eps: float | None
    source: InputSource

    @property
    def days(self) -> int:
        return (self.period_end - self.period_start).days + 1


@dataclass(frozen=True)
class FDXPeerTradingMultiples:
    ticker: str
    company: str
    role: str
    comparison_note: str
    pending_transaction: bool
    close: DatedClose
    balance_sheet_date: date
    share_count_date: date
    share_classes: dict[str, int]
    cash_millions: float
    current_debt_millions: float
    long_term_debt_millions: float
    noncontrolling_interest_millions: float
    preferred_stock_millions: float
    annual: FDXPeerEarnings
    prior_interim: FDXPeerEarnings
    prior_fourth_quarter: FDXPeerEarnings
    current_interim: FDXPeerEarnings
    market_cap_millions: float
    enterprise_value_millions: float
    ltm_ebit_millions: float
    ltm_net_income_millions: float
    ltm_diluted_shares_millions: float
    ltm_diluted_eps: float
    ev_to_ebit: float
    price_to_earnings: float

    @property
    def shares_millions(self) -> float:
        return sum(self.share_classes.values()) / 1_000_000

    @property
    def debt_millions(self) -> float:
        return self.current_debt_millions + self.long_term_debt_millions


@dataclass(frozen=True)
class FDXPeerBenchmark:
    tickers: tuple[str, ...]
    earnings_basis: str
    ev_to_ebit: float
    price_to_earnings: float
    implied_ev_ebit_usd: float
    implied_pe_usd: float


@dataclass(frozen=True)
class FDXComparableValuation:
    as_of: date
    reference_close_date: date
    ltm_end: date
    subject_close: DatedClose
    subject_basis: str
    subject_ebit_millions: float
    subject_eps: float
    subject_shares_millions: float
    subject_valuation_shares_millions: float
    subject_debt_millions: float
    subject_cash_millions: float
    subject_nonoperating_assets_millions: float
    subject_market_cap_millions: float
    subject_enterprise_value_millions: float
    subject_ev_to_ebit: float
    subject_price_to_earnings: float
    peers: tuple[FDXPeerTradingMultiples, ...]
    ups: FDXPeerBenchmark
    ups_adjusted: FDXPeerBenchmark
    screened_median: FDXPeerBenchmark
    broad_median: FDXPeerBenchmark
    notes: tuple[str, ...]

    @property
    def ups_implied_ev_ebit_usd(self) -> float:
        return self.ups.implied_ev_ebit_usd

    @property
    def ups_implied_pe_usd(self) -> float:
        return self.ups.implied_pe_usd

    @property
    def median_implied_ev_ebit_usd(self) -> float:
        return self.screened_median.implied_ev_ebit_usd

    @property
    def median_implied_pe_usd(self) -> float:
        return self.screened_median.implied_pe_usd


def _amount(value, label, *, allow_zero=False):
    amount = _number(value, label)
    if amount < 0 or (not allow_zero and amount == 0):
        raise ValueError(f"{label} must be {'nonnegative' if allow_zero else 'positive'}.")
    return amount


def _reference(value, sources, kind, observed_on, label):
    source = sources.get(_text(value, f"{label} source"))
    if source is None or source.kind != kind or source.available_on < observed_on:
        raise ValueError(f"{label} requires a dated public {kind} source.")
    return source


def _close(raw, close_date, sources, ticker):
    row = _fields(raw, f"{ticker} close", {"price", "observed_on", "price_type", "source"})
    if row["price_type"] != "unadjusted_close" or _day(row["observed_on"], "close date") != close_date:
        raise ValueError(f"{ticker} must use the same unadjusted reference close as FDX.")
    source = _reference(row["source"], sources, "market_data", close_date, f"{ticker} close")
    return DatedClose(_amount(row["price"], f"{ticker} close price"), close_date, source)


def _earnings(raw, label, start, end, sources, *, kind, with_eps=False):
    keys = {"period_start", "period_end", "source", "ebit_millions", "net_income_millions"}
    if with_eps:
        keys |= {"diluted_shares_millions", "diluted_eps"}
    row = _fields(raw, label, keys)
    if _day(row["period_start"], f"{label} start") != start or _day(row["period_end"], f"{label} end") != end:
        raise ValueError(f"{label} must cover {start} through {end}.")
    source = _reference(row["source"], sources, kind, end, label)
    ebit = _number(row["ebit_millions"], f"{label} EBIT")
    earnings = _number(row["net_income_millions"], f"{label} net income")
    if not with_eps:
        return FDXPeerEarnings(start, end, ebit, earnings, None, None, source)
    shares = _amount(row["diluted_shares_millions"], f"{label} diluted shares")
    eps = _number(row["diluted_eps"], f"{label} diluted EPS")
    if not isclose(earnings / shares, eps, abs_tol=0.01):
        raise ValueError(f"{label} EPS does not reconcile to reported earnings and diluted shares.")
    return FDXPeerEarnings(start, end, ebit, earnings, shares, eps, source)


def _peer(raw, close_date, ltm_end, sources):
    row = _fields(raw, "peer", {
        "ticker", "company", "role", "comparison_note", "pending_transaction", "close", "capital",
        "annual", "prior_interim", "prior_fourth_quarter", "current_interim",
    }, {"adjustments"})
    ticker = _text(row["ticker"], "peer ticker")
    prior_year = ltm_end.year - 1
    prior_start, prior_end = date(prior_year, 1, 1), date(prior_year, 12, 31)
    annual = _earnings(row["annual"], f"{ticker} annual", prior_start, prior_end, sources, kind="sec_filing")
    prior = _earnings(row["prior_interim"], f"{ticker} prior interim", prior_start, date(prior_year, 9, 30),
                      sources, kind="sec_filing")
    quarter = _earnings(row["prior_fourth_quarter"], f"{ticker} prior Q4", date(prior_year, 10, 1), prior_end,
                        sources, kind="issuer_release", with_eps=True)
    current = _earnings(row["current_interim"], f"{ticker} current interim", date(ltm_end.year, 1, 1), ltm_end,
                         sources, kind="sec_filing", with_eps=True)
    if (not isclose(annual.ebit_millions - prior.ebit_millions, quarter.ebit_millions, abs_tol=0.001)
            or not isclose(annual.net_income_millions - prior.net_income_millions,
                           quarter.net_income_millions, abs_tol=0.001)):
        raise ValueError(f"{ticker} fourth-quarter earnings do not reconcile to FY less prior nine months.")

    close = _close(row["close"], close_date, sources, ticker)
    capital_keys = ("cash_millions", "current_debt_millions", "long_term_debt_millions",
                    "noncontrolling_interest_millions", "preferred_stock_millions")
    capital = _fields(row["capital"], f"{ticker} capitalization", {
        "balance_sheet_date", "share_count_date", "share_classes", *capital_keys,
    })
    balance_sheet_date = _day(capital["balance_sheet_date"], f"{ticker} balance sheet date")
    share_count_date = _day(capital["share_count_date"], f"{ticker} share count date")
    share_classes = _object(capital["share_classes"], f"{ticker} share classes")
    if (balance_sheet_date != ltm_end or not ltm_end <= share_count_date <= current.source.available_on
            or not share_classes or any(type(value) is not int or value <= 0 for value in share_classes.values())
            or type(row["pending_transaction"]) is not bool):
        raise ValueError(f"{ticker} has invalid capitalization or transaction metadata.")
    cash, current_debt, long_term_debt, minority, preferred = (
        _amount(capital[key], f"{ticker} {key}", allow_zero=True) for key in capital_keys
    )
    market_cap = close.price * sum(share_classes.values()) / 1_000_000
    ev = _amount(market_cap + current_debt + long_term_debt + minority + preferred - cash, f"{ticker} EV")
    ltm_ebit = _amount(current.ebit_millions + quarter.ebit_millions, f"{ticker} LTM EBIT")
    ltm_earnings = _amount(current.net_income_millions + quarter.net_income_millions, f"{ticker} LTM earnings")
    share_days = current.days * current.diluted_shares_millions + quarter.days * quarter.diluted_shares_millions
    ltm_shares = share_days / (current.days + quarter.days)
    ltm_eps = ltm_earnings / ltm_shares
    peer = FDXPeerTradingMultiples(
        ticker, _text(row["company"], f"{ticker} company"), _text(row["role"], f"{ticker} role"),
        _text(row["comparison_note"], f"{ticker} comparison note"), row["pending_transaction"], close,
        balance_sheet_date, share_count_date, dict(share_classes), cash, current_debt, long_term_debt, minority,
        preferred, annual, prior, quarter, current, market_cap, ev, ltm_ebit, ltm_earnings, ltm_shares,
        ltm_eps, ev / ltm_ebit, close.price / ltm_eps,
    )
    return peer, row.get("adjustments")


def _ups_adjusted(ups, raw):
    periods = ("prior_fourth_quarter", "current_interim")
    keys = ("ebit_millions", "net_income_millions", "adjusted_diluted_eps")
    row = _fields(raw, "UPS non-GAAP reconciliation", {f"{period}_{key}" for period in periods for key in keys})
    ebit, ni = ups.ltm_ebit_millions, ups.ltm_net_income_millions
    for period in periods:
        reported = getattr(ups, period)
        ebit_change, ni_change, eps = (_number(row[f"{period}_{key}"], f"UPS {period} {key}") for key in keys)
        if not isclose((reported.net_income_millions + ni_change) / reported.diluted_shares_millions,
                       eps, abs_tol=0.01):
            raise ValueError(f"UPS adjusted {period} EPS does not reconcile to adjusted earnings and diluted shares.")
        ebit += ebit_change
        ni += ni_change
    ebit = _amount(ebit, "UPS adjusted LTM EBIT")
    ni = _amount(ni, "UPS adjusted LTM earnings")
    return ups.enterprise_value_millions / ebit, ups.close.price / (ni / ups.ltm_diluted_shares_millions)


def build_fdx_comps(
    evidence: object,
    *,
    subject_ebit_millions: float,
    subject_eps: float,
    subject_shares_millions: float,
    subject_debt_millions: float,
    subject_cash_millions: float,
    subject_nonoperating_assets_millions: float = 0,
    subject_valuation_shares_millions: float | None = None,
    subject_basis: str = "FedEx continuing operations, trailing 12 months",
    expected_close_date: date | None = None,
    expected_close_usd: float | None = None,
) -> FDXComparableValuation:
    """Apply dated parcel and logistics multiples to FedEx earnings and capitalization."""
    row = _fields(evidence, "FDX comparable inputs", {
        "schema_version", "ticker", "currency", "as_of", "reference_close_date", "ltm_end",
        "subject_close", "sources", "peers",
    })
    if (type(row["schema_version"]) is not int or row["schema_version"] != 1
            or (row["ticker"], row["currency"]) != ("FDX", "USD")):
        raise ValueError("Expected version 1 USD comparable inputs for FDX.")
    as_of = _day(row["as_of"], "comparable valuation cutoff")
    close_date = _day(row["reference_close_date"], "reference close date")
    ltm_end = _day(row["ltm_end"], "peer LTM end")
    if (ltm_end != date(as_of.year, 9, 30) or not ltm_end < close_date or close_date.weekday() >= 5
            or not 0 < (as_of - close_date).days <= 7):
        raise ValueError("The comparable snapshot requires September LTM results and a prior session close.")
    source_rows = _object(row["sources"], "comparable sources")
    sources = {key: _source(value, key, close_date) for key, value in source_rows.items()}
    if any(not _public_url(source.url) or urlsplit(source.url).scheme != "https" for source in sources.values()):
        raise ValueError("Comparable inputs must cite public HTTPS sources available by the reference close.")
    close = _close(row["subject_close"], close_date, sources, "FDX")
    if ((expected_close_date is not None and close_date != expected_close_date)
            or (expected_close_usd is not None and close.price != expected_close_usd)):
        raise ValueError("The comparable FDX reference close disagrees with the DCF market snapshot.")

    inputs = [_peer(raw, close_date, ltm_end, sources) for raw in _array(row["peers"], "peers")]
    peers = tuple(peer for peer, _ in inputs)
    by_ticker = {peer.ticker: (peer, adjustments) for peer, adjustments in inputs}
    if len(peers) != 3 or set(by_ticker) != {"UPS", "CHRW", "EXPD"}:
        raise ValueError("The FDX peer set requires exactly UPS, CHRW, and EXPD.")
    screened = tuple(peer for peer in peers if not peer.pending_transaction)
    ups, adjustments = by_ticker["UPS"]
    if ups.role != "parcel_network" or ups.pending_transaction or len(screened) < 2:
        raise ValueError("The FDX peer set requires UPS as the parcel benchmark and at least two screened peers.")

    basis = _text(subject_basis, "FDX earnings basis")
    ebit = _amount(subject_ebit_millions, "FDX EBIT")
    eps = _amount(subject_eps, "FDX EPS")
    shares = _amount(subject_shares_millions, "FDX basic shares")
    valuation_shares = (shares if subject_valuation_shares_millions is None
                        else _amount(subject_valuation_shares_millions, "FDX valuation shares"))
    if valuation_shares < shares:
        raise ValueError("FDX valuation shares must be at least the basic share count.")
    debt = _amount(subject_debt_millions, "FDX debt", allow_zero=True)
    cash = _amount(subject_cash_millions, "FDX cash", allow_zero=True)
    nonoperating_assets = _amount(subject_nonoperating_assets_millions, "FDX nonoperating assets", allow_zero=True)
    net_claims = debt - cash - nonoperating_assets
    market_cap = shares * close.price
    ev = _amount(market_cap + net_claims, "FDX enterprise value")

    def benchmark(tickers, earnings_basis, ev_multiple, pe_multiple):
        implied_equity = ev_multiple * ebit - net_claims
        return FDXPeerBenchmark(tickers, earnings_basis, ev_multiple, pe_multiple,
                                implied_equity / valuation_shares, pe_multiple * eps)

    def peer_median(group):
        return benchmark(tuple(peer.ticker for peer in group), "GAAP",
                         median(peer.ev_to_ebit for peer in group), median(peer.price_to_earnings for peer in group))

    return FDXComparableValuation(
        as_of, close_date, ltm_end, close, basis, ebit, eps, shares, valuation_shares, debt, cash,
        nonoperating_assets, market_cap, ev, ev / ebit, close.price / eps, peers,
        benchmark(("UPS",), "GAAP", ups.ev_to_ebit, ups.price_to_earnings),
        benchmark(("UPS",), "UPS issuer-adjusted", *_ups_adjusted(ups, adjustments)),
        peer_median(screened), peer_median(peers),
        (
            "Unadjusted USD closes use one trading session. Peer capitalization uses filed cover shares "
            "and September 30 debt, cash, and minority interests. UPS Class A is valued at the Class B close based "
            "on one-for-one conversion and equal economic rights.",
            "Peer LTM EBIT and net income add reported fourth-quarter 2025 and nine-month 2026 results. "
            "LTM EPS divides trailing net income by the calendar-day-weighted diluted shares for those periods.",
            "Peer EV adds debt, finance leases included in debt, preferred stock and book minority interest to "
            "market capitalization and deducts cash and equivalents. GAAP EBIT includes operating lease expense, "
            "so operating lease liabilities are excluded from EV. Peer EV uses filed September balances.",
            "UPS is the integrated parcel benchmark. The supplemental adjusted UPS benchmark uses UPS's own "
            "reconciliations, which can differ from FedEx's adjustments. EXPD is an asset-light forwarding cross-check. "
            "CHRW's pending RXO acquisition limits its standalone multiple to the broad median cross-check.",
            "FDX EV/EBIT applies the peer multiple to the supplied earnings and bridges through the supplied debt, "
            "cash and separately valued nonoperating assets. The DCF integration supplies estimated cutoff balances "
            "and excess cash for FDX, while peer EV uses all filed cash and equivalents. P/E applies the peer multiple "
            "directly to supplied EPS.",
        ),
    )


def load_fdx_comps(path: Path, **subject) -> FDXComparableValuation:
    """Read the dated evidence file and calculate the trading comparables."""
    return build_fdx_comps(_read(path), **subject)

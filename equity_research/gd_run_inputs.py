"""Read dated assumptions and observed year-end share prices for a sourced GD run."""

from dataclasses import dataclass
from datetime import date
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit

from .gd_inputs import (
    GUIDED_SCALARS,
    SELECTORS,
    Cell,
    InputSource,
    _cell,
    _choice,
    _day,
    _fields,
    _metadata,
    _number,
    _object,
    _read,
    _source,
)


_COMMON_FIELDS = {"schema_version", "ticker", "currency", "as_of", "source"}


@dataclass(frozen=True)
class ForecastAssumptions:
    as_of: date
    assumptions: dict[Cell, float]
    scenarios: dict[int, int]
    cost_of_equity: float
    terminal_growth: float
    dividend_payout_ratio: float
    source: InputSource
    provenance: dict[str, InputSource]


@dataclass(frozen=True)
class DatedClose:
    price: float
    observed_on: date
    source: InputSource


@dataclass(frozen=True)
class MarketInputs:
    as_of: date
    year_end_close: float
    year_end_close_date: date
    source: InputSource
    valuation_close: DatedClose | None = None


def _header(item: dict) -> date:
    _metadata(item)
    if item["ticker"] != "GD" or item["currency"] != "USD":
        raise ValueError("These run inputs require ticker GD and currency USD.")
    return _day(item["as_of"], "as_of")


def load_forecast_assumptions(path: Path) -> ForecastAssumptions:
    """Read explicitly supplied forecast values; the model checks input coverage."""
    required = _COMMON_FIELDS | {"assumptions", "scenarios"} | GUIDED_SCALARS
    item = _fields(_read(path), "forecast", required, {"provenance"})
    as_of = _header(item)
    source = _source(item["source"], "forecast source", as_of)
    if source.kind != "model_assumption":
        raise ValueError("Forecast source must have kind model_assumption.")

    annual = _object(item["assumptions"], "assumptions")
    assumptions = {_cell(address, forecast=True): _number(value, address) for address, value in annual.items()}
    choices = _fields(item["scenarios"], "scenario", {str(row) for row in SELECTORS.values()})
    scenarios = {int(row): _choice(value, f"Cover!I{row}") for row, value in choices.items()}

    cost_of_equity = _number(item["cost_of_equity"], "cost_of_equity")
    terminal_growth = _number(item["terminal_growth"], "terminal_growth")
    dividend_payout_ratio = _number(item["dividend_payout_ratio"], "dividend_payout_ratio")
    if cost_of_equity <= 0:
        raise ValueError("cost_of_equity must be positive.")
    if not -1 < terminal_growth < cost_of_equity:
        raise ValueError("terminal_growth must be greater than -1 and below cost_of_equity.")
    if not 0 <= dividend_payout_ratio <= 1:
        raise ValueError("dividend_payout_ratio must be between 0 and 1.")

    source_keys = annual.keys() | SELECTORS.keys() | GUIDED_SCALARS
    entries = _fields(item.get("provenance", {}), "forecast provenance", set(), source_keys)
    provenance = {key: _source(entry, f"source for {key}", as_of) for key, entry in entries.items()}
    archived = [key for key, entry in provenance.items() if entry.kind == "archived_workbook"]
    if archived:
        raise ValueError(f"Forecast provenance must be independent of the archived workbook for {', '.join(archived)}.")

    return ForecastAssumptions(
        as_of, assumptions, scenarios, cost_of_equity, terminal_growth, dividend_payout_ratio, source, provenance,
    )


def _public_url(url: str | None) -> bool:
    if url is None or any(character.isspace() for character in url):
        return False
    parsed = urlsplit(url)
    host = (parsed.hostname or "").removesuffix(".")
    if not host or parsed.username is not None or parsed.password is not None:
        return False
    try:
        return ip_address(host).is_global
    except ValueError:
        return "." in host and not host.endswith((".local", ".localhost"))


def load_market_inputs(path: Path) -> MarketInputs:
    """Read a cited unadjusted closing price for the preceding fiscal year-end."""
    required = _COMMON_FIELDS | {"year_end_close", "year_end_close_date", "price_type"}
    item = _fields(_read(path), "market inputs", required, {"valuation_close"})
    as_of = _header(item)
    if as_of.year < 2:
        raise ValueError("Market inputs require a preceding fiscal year.")
    if item["price_type"] != "unadjusted_close":
        raise ValueError("price_type must be unadjusted_close.")
    close = _number(item["year_end_close"], "year_end_close")
    if close <= 0:
        raise ValueError("year_end_close must be positive.")

    close_date = _day(item["year_end_close_date"], "year_end_close_date")
    year_end = date(as_of.year - 1, 12, 31)
    if not 0 <= (year_end - close_date).days <= 7 or close_date.weekday() >= 5:
        raise ValueError("year_end_close_date must be a weekday on or within seven days before the preceding December 31.")

    source = _source(item["source"], "market source", as_of)
    if source.kind != "market_data" or not _public_url(source.url):
        raise ValueError("Market source must have kind market_data and cite a public HTTP(S) URL.")
    if source.available_on < close_date:
        raise ValueError("Market source availability cannot precede the closing price date.")

    valuation_close = None
    if "valuation_close" in item:
        quote = _fields(item["valuation_close"], "valuation close", {"price", "observed_on", "price_type", "source"})
        price = _number(quote["price"], "valuation close price")
        observed_on = _day(quote["observed_on"], "valuation close observed_on")
        quote_source = _source(quote["source"], "valuation close source", as_of)
        if price <= 0 or quote["price_type"] != "unadjusted_close":
            raise ValueError("Valuation close must be a positive unadjusted closing price.")
        if not 0 <= (as_of - observed_on).days <= 7 or observed_on.weekday() >= 5:
            raise ValueError("Valuation close must use a weekday within seven days through the cutoff.")
        if quote_source.kind != "market_data" or not _public_url(quote_source.url) or quote_source.available_on < observed_on:
            raise ValueError("Valuation close needs a public market source dated on or after the observation.")
        valuation_close = DatedClose(price, observed_on, quote_source)
    return MarketInputs(as_of, close, close_date, source, valuation_close)

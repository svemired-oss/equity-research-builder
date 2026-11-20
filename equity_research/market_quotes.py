"""Collect private U.S. equity closing quotes from Alpaca's historical SIP feed."""

from datetime import date, datetime, time, timedelta, timezone
from http.client import IncompleteRead
import json
from math import isfinite
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener

from .sec_client import _NoRedirects


_CALENDAR_URL = "https://paper-api.alpaca.markets/v3/calendar/XNYS"
_BARS_URL = "https://data.alpaca.markets/v2/stocks/bars"
_CALENDAR_SOURCE = "https://docs.alpaca.markets/us/reference/calendar-2"
_BARS_SOURCE = "https://docs.alpaca.markets/us/reference/stockbars"
_SYMBOL = re.compile(r"[A-Z0-9]+(?:[.-][A-Z0-9]+)*")
_MAX_RESPONSE = 1_000_000
_SIP_DELAY = timedelta(minutes=15)
_US_MARKET = "America/New_York"


class MarketQuoteError(ValueError):
    """A verified market quote could not be obtained from the provider."""


class AlpacaQuoteClient:
    """Read market calendars and historical bars with Alpaca paper credentials."""

    def __init__(self, key_id: str, secret_key: str):
        if any(not isinstance(value, str) or not re.fullmatch(r"[\x21-\x7e]+", value)
               for value in (key_id, secret_key)):
            raise ValueError("Supply both the Alpaca paper API key ID and secret key.")
        self._headers = {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret_key, "Accept": "application/json"}
        self._opener = build_opener(_NoRedirects())

    def _get(self, url, params):
        request = Request(f"{url}?{urlencode(params)}", headers=self._headers)
        try:
            with self._opener.open(request, timeout=10) as response:
                body = response.read(_MAX_RESPONSE + 1)
        except HTTPError as exc:
            exc.close()
            raise MarketQuoteError(f"Alpaca returned HTTP {exc.code}.") from None
        except (URLError, TimeoutError, ConnectionError, IncompleteRead):
            raise MarketQuoteError("Alpaca could not be reached.") from None
        if len(body) > _MAX_RESPONSE:
            raise MarketQuoteError("Alpaca's response exceeded the size limit.")
        try:
            payload = json.loads(body)
        except ValueError:
            raise MarketQuoteError("Alpaca returned invalid JSON.") from None
        if not isinstance(payload, dict):
            raise MarketQuoteError("Alpaca returned an unexpected response shape.")
        return payload

    def calendar(self, start: date, end: date) -> dict:
        return self._get(_CALENDAR_URL, {"start": start.isoformat(), "end": end.isoformat()})

    def daily_bars(self, symbols, *, start: datetime, end: datetime) -> dict:
        return self._get(_BARS_URL, {
            "symbols": ",".join(symbols), "timeframe": "1Day", "feed": "sip", "adjustment": "raw",
            "currency": "USD", "start": start.isoformat(), "end": end.isoformat(),
            "asof": start.date().isoformat(),
        })

    def adjusted_daily_bars(self, symbol, start, end):
        payload = self._get(_BARS_URL, {"symbols": symbol, "start": str(start),
                                        "end": f"{end}T23:59:59Z", "timeframe": "1Day",
                                        "feed": "sip", "adjustment": "split", "currency": "USD",
                                        "limit": 1000, "asof": str(end)})
        rows = (payload.get("bars") or {}).get(symbol)
        if payload.get("next_page_token") or not isinstance(rows, list):
            raise MarketQuoteError("Incomplete split-adjusted price history.")
        return rows


def _timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("The timestamp is missing its time zone.")
    return parsed


def _completed_sessions(payload, start, as_of, now):
    try:
        sessions = {}
        if payload["market"]["mic"] != "XNYS" or payload["market"]["timezone"] != _US_MARKET:
            raise ValueError("The response identifies a different market.")
        if not isinstance(payload["calendar"], list):
            raise ValueError("The response is missing calendar rows.")
        for row in payload["calendar"]:
            day = date.fromisoformat(row["date"])
            close = _timestamp(row["core_end"])
            if (not start <= day <= as_of or day in sessions or close.date() != day
                    or close.utcoffset() not in {timedelta(hours=-4), timedelta(hours=-5)}):
                raise ValueError("Invalid calendar date or regular session close.")
            sessions[day] = close
    except (AttributeError, KeyError, TypeError, ValueError):
        raise MarketQuoteError("Alpaca returned an invalid U.S. market calendar.") from None
    return sorted(((day, close) for day, close in sessions.items() if close <= now), reverse=True)


def _bar_close(rows, bar_start):
    if not isinstance(rows, list) or len(rows) != 1:
        return None
    try:
        value = rows[0]["c"]
        if _timestamp(rows[0]["t"]) == bar_start and type(value) in (int, float) and isfinite(value) and value > 0:
            return value
    except (AttributeError, KeyError, OverflowError, TypeError, ValueError):
        pass
    return None


def collect_market_quotes(client, symbols, *, as_of: date, now: datetime | None = None) -> dict:
    """Return same-session raw USD closes and flag missing or provider-delayed observations."""
    if type(as_of) is not date:
        raise ValueError("A market quote cutoff date is required.")
    if isinstance(symbols, str):
        symbols = (symbols,)
    tickers = dict.fromkeys(str(symbol).strip().upper() for symbol in symbols)
    if not tickers or len(tickers) > 50 or any(len(ticker) > 20 or not _SYMBOL.fullmatch(ticker) for ticker in tickers):
        raise ValueError("Request market quotes for one to fifty valid equity tickers.")
    now = now if now is not None else datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.utcoffset() is None:
        raise ValueError("The quote collection time must include a time zone.")
    result = {
        "status": "unavailable", "as_of": as_of.isoformat(), "latest_completed_session": None,
        "quote_session": None, "quotes": {}, "missing": [], "calendar_url": _CALENDAR_SOURCE,
    }
    calendar_end = min(as_of, now.astimezone(timezone.utc).date())
    start = calendar_end - timedelta(days=14)
    try:
        sessions = _completed_sessions(client.calendar(start, calendar_end), start, calendar_end, now)
        if not sessions:
            raise MarketQuoteError("No completed market session was found by the cutoff.")
        result["latest_completed_session"] = sessions[0][0].isoformat()
        available = next(((day, close) for day, close in sessions if close + _SIP_DELAY <= now), None)
        if available is None:
            raise MarketQuoteError("No completed session is available under the historical feed delay.")
        day, close = available
        result["quote_session"] = day.isoformat()
        if day != sessions[0][0]:
            result["missing"].append("The latest completed session is still within the historical feed delay.")
        # Alpaca uses the left boundary of the New York day as its daily bar timestamp.
        bar_start = datetime.combine(day, time.min, tzinfo=close.tzinfo)
        provider_symbols = {ticker: re.sub(r"-([A-Z])$", r".\1", ticker) for ticker in tickers}
        payload = client.daily_bars(tuple(dict.fromkeys(provider_symbols.values())), start=bar_start, end=close)
        bars = payload.get("bars")
        if not isinstance(bars, dict) or payload.get("next_page_token"):
            raise MarketQuoteError("Alpaca returned an incomplete or invalid daily bars response.")
    except MarketQuoteError as exc:
        result["missing"].append(str(exc))
        return result

    for ticker, symbol in provider_symbols.items():
        value = _bar_close(bars.get(symbol), bar_start)
        if value is None:
            result["missing"].append(f"{ticker}: no verified daily SIP close for {day}.")
            continue
        result["quotes"][ticker] = {
            "ticker": ticker, "provider_symbol": symbol, "trading_date": day.isoformat(), "close": value,
            "currency": "USD", "source": "Alpaca", "url": _BARS_SOURCE,
            "method": "Historical SIP daily bar close, raw",
        }
    if result["quotes"]:
        result["status"] = "partial" if result["missing"] else "available"
    return result

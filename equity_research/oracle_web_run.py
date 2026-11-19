"""Generate the calibrated Oracle review from a private browser request."""

from datetime import date
from os import environ
from pathlib import Path
from tempfile import TemporaryDirectory

from .oracle_review_export import MARKET, PEERS, REVIEW_DATE, export_review, prepare_review, prepare_review_inputs


class OracleInputsUnavailable(ValueError):
    """Required evidence was unavailable for the calibrated review."""


def _prepare_live(*, dated_closes=False, as_of=None):
    from .market_quotes import AlpacaQuoteClient, collect_market_quotes
    from .sec_client import SECClient, SECError, configured_sec_contact
    from .sec_intake import collect_ticker_evidence
    from .treasury_rates import collect_treasury_yield

    contact = configured_sec_contact()
    key = environ.get("APCA_API_KEY_ID", "").strip()
    secret = environ.get("APCA_API_SECRET_KEY", "").strip()
    if not contact:
        raise OracleInputsUnavailable("Live collection requires the server's SEC contact in SEC_USER_AGENT.")
    if not dated_closes and (not key or not secret):
        raise OracleInputsUnavailable("Live quote collection requires the server's Alpaca credentials.")
    as_of = as_of or (REVIEW_DATE if dated_closes else date.today())
    symbols = ("ORCL", *PEERS)
    quotes = None
    if not dated_closes:
        market_result = collect_market_quotes(AlpacaQuoteClient(key, secret), symbols, as_of=as_of)
        session = market_result["latest_completed_session"]
        quotes = market_result["quotes"]
        missing = [ticker for ticker in symbols if ticker not in quotes or quotes[ticker]["trading_date"] != session]
        if missing:
            raise OracleInputsUnavailable(f"No verified closing price for {', '.join(missing)} on the latest completed session.")
        if (as_of - date.fromisoformat(session)).days > 7:
            raise OracleInputsUnavailable("The latest verified equity close is too old for this valuation cutoff.")
    treasury = collect_treasury_yield(as_of)
    if treasury["status"] != "available":
        raise OracleInputsUnavailable("The official 10-year Treasury rate is unavailable for the review.")
    try:
        sec = SECClient(contact)
        packets = {ticker: collect_ticker_evidence(sec, ticker, as_of=as_of, years=5,
                                                  release_limit=4 if ticker == "ORCL" else 0)
                   for ticker in symbols}
    except SECError as exc:
        raise OracleInputsUnavailable("EDGAR did not return the required filings. Retry after SEC access is restored.") from exc
    market = {**MARKET, "risk_free": treasury["rate"]}
    if quotes is not None:
        market["common_close"] = quotes["ORCL"]["close"]
    try:
        return prepare_review_inputs(packets, market=market, quotes=quotes, treasury=treasury)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise OracleInputsUnavailable("The SEC evidence could not support the calibrated Oracle forecast. A new reporting period needs an updated forecast.") from exc


def generate_oracle_run(work_dir, node, node_modules, evidence_dir, *, dated_closes=False, as_of=None):
    """Return verified artifacts. A configured evidence directory selects the dated replay."""
    plan, report = _prepare_live(dated_closes=dated_closes, as_of=as_of) if evidence_dir is None else prepare_review(evidence_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="oracle-", dir=work_dir) as stage:
        paths = export_review(plan, report, Path(stage), node, node_modules)
        contents = {kind: path.read_bytes() for kind, path in zip(("workbook", "report"), paths, strict=True)}
    quote = report["quote"]
    summary = {"reference_close_usd": quote, "spot_value_usd": report["value"],
               "spot_upside": report["upside"], "rating": report["rating"]}
    cases = [{"case": name.lower(), "spot_value_usd": value} for name, value in report["scenarios"].items()]
    return summary, cases, contents, report["quote_date"]

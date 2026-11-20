"""Resolve a public ticker and collect dated EDGAR evidence for a new model."""

from datetime import date
import re

from .event_revenue_guidance import parse_event_revenue_guidance
from .gd_releases import recent_filings
from .oracle_capacity import compare_oracle_buildout
from .oracle_debt_pricing import collect_oracle_debt_pricing
from .oracle_dilution import collect_oracle_dilution
from .oracle_dividends import collect_oracle_dividends
from .oracle_funding import collect_oracle_funding_schedules, oracle_issued_note_payments
from .oracle_model_inputs import (
    build_oracle_opening_evidence, collect_oracle_amortization, reconcile_oracle_offering_adjustments,
    summarize_oracle_cutoff_movements,
)
from .oracle_offerings import collect_oracle_offerings
from .oracle_pilot import oracle_pilot_readiness
from .oracle_quarter_guidance import collect_oracle_quarter_guidance
from .oracle_revenue import build_oracle_revenue_basis, build_oracle_revenue_forecast
from .sec_capital_events import collect_capital_events
from .sec_client import _cik, filing_document_url, filing_index_url
from .sec_earnings import collect_earnings_releases
from .sec_event_guidance import collect_event_guidance
from .sec_financials import normalize_annual_financials, normalize_latest_interim_financials
from .sec_guidance import collect_revenue_guidance
from .sec_operating_facts import collect_operating_facts


TICKER_DIRECTORY_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
_ANNUAL_FORMS = {"10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A", "10-KT", "10-KT/A"}
_INTERIM_FORMS = {"10-Q", "10-Q/A", "10-QT", "10-QT/A"}
_EVENT_FORMS = {"8-K", "8-K/A", "6-K", "6-K/A"}
_OFFERING_FORMS = {"424B2", "424B5", "FWP"}
_FINANCING_ITEMS = {"1.01", "2.03", "3.02", "8.01"}


def _ticker(symbol: str) -> str:
    ticker = symbol.strip().upper() if isinstance(symbol, str) else ""
    if not re.fullmatch(r"[A-Z0-9]+(?:[.-][A-Z0-9]+)*", ticker) or len(ticker) > 20:
        raise ValueError("Enter an EDGAR ticker such as ORCL or BRK-B.")
    return ticker


def resolve_ticker(client, symbol: str) -> dict:
    """Use the SEC ticker and exchange directory, rejecting ambiguous issuer matches."""
    ticker = _ticker(symbol)
    directory = client.get_json(TICKER_DIRECTORY_URL)
    fields, rows = directory.get("fields"), directory.get("data")
    if (not isinstance(fields, list) or any(not isinstance(field, str) for field in fields)
            or not isinstance(rows, list) or len(fields) != len(set(fields))):
        raise ValueError("The SEC ticker directory has an invalid structure.")
    required = ("cik", "name", "ticker", "exchange")
    if any(field not in fields for field in required):
        raise ValueError("The SEC ticker directory is missing issuer fields.")
    indexes = {field: fields.index(field) for field in required}
    matches = []
    for row in rows:
        if not isinstance(row, list) or len(row) != len(fields):
            raise ValueError("An issuer row in the SEC ticker directory is malformed.")
        listed = row[indexes["ticker"]]
        if not isinstance(listed, str) or listed.upper().replace(".", "-") != ticker.replace(".", "-"):
            continue
        cik, name, exchange = (row[indexes[key]] for key in ("cik", "name", "exchange"))
        if type(cik) is not int or not isinstance(name, str) or not name.strip():
            raise ValueError("The matched SEC ticker is missing a valid issuer name or CIK.")
        matches.append({"ticker": listed.upper(), "cik": _cik(str(cik)), "name": name, "exchange": exchange})
    if not matches:
        raise ValueError(f"{ticker} was not found in the SEC ticker directory. Another filing source may be required.")
    if len({record["cik"] for record in matches}) != 1:
        raise ValueError(f"{ticker} matches more than one SEC filer.")
    return next((record for record in matches if record["ticker"] == ticker), matches[0])


def _prior_filings(client, cik: str, submissions: dict, as_of: date, years: int):
    files = submissions.get("filings", {}).get("files", [])
    oldest_year = as_of.year - years - 1
    for file in files:
        name = file.get("name", "")
        if not isinstance(name, str) or not re.fullmatch(rf"CIK{cik}-submissions-\d+\.json", name):
            raise ValueError("An archived SEC submissions filename is invalid.")
        first, last = (date.fromisoformat(file[key]) for key in ("filingFrom", "filingTo"))
        if first <= as_of and last.year >= oldest_year:
            archive = client.get_json(f"https://data.sec.gov/submissions/{name}")
            yield from recent_filings({"filings": {"recent": archive}}, as_of=as_of)


def _filing_rows(client, issuer: dict, submissions: dict, as_of: date, years: int) -> list[dict]:
    filings = list(recent_filings(submissions, as_of=as_of))
    # A high volume of ownership reports can push annual filings into the archive.
    annual_dates = {filing.report_date for filing in filings if filing.form in _ANNUAL_FORMS and filing.report_date}
    recent_events = any(filing.form in _EVENT_FORMS and (as_of - filing.filing_date).days <= 120 for filing in filings)
    if len(annual_dates) < years or not recent_events:
        filings.extend(_prior_filings(client, issuer["cik"], submissions, as_of, years))
    forms = _ANNUAL_FORMS | _INTERIM_FORMS | _EVENT_FORMS
    if int(issuer["cik"]) == 1341439:
        forms |= _OFFERING_FORMS
    rows = {}
    for filing in filings:
        if filing.form not in forms or filing.accession in rows:
            continue
        cik, accession = issuer["cik"], filing.accession
        rows[accession] = {
            "form": filing.form,
            "accession": accession,
            "filed_on": filing.filing_date.isoformat(),
            "period_end": filing.report_date.isoformat() if filing.report_date else None,
            "url": (filing_document_url(cik, accession, filing.primary_document) if filing.primary_document
                    else filing_index_url(cik, accession)),
            "index_url": filing_index_url(cik, accession),
            "items": filing.items,
        }
    return sorted(rows.values(), key=lambda row: (row["filed_on"], row["accession"]), reverse=True)


def collect_ticker_evidence(client, symbol: str, *, as_of: date, years: int = 5, release_limit: int = 4,
                            statements_only: bool = False) -> dict:
    """Collect dated actuals, operating evidence, and scoped guidance from EDGAR."""
    if type(as_of) is not date or type(years) is not int or not 1 <= years <= 10:
        raise ValueError("Supply a cutoff date and request one to ten fiscal years.")
    if type(release_limit) is not int or not 0 <= release_limit <= 10:
        raise ValueError("Request zero to ten earnings releases.")
    issuer = resolve_ticker(client, symbol)
    cik = issuer["cik"]
    submissions = client.submissions(cik)
    if _cik(str(submissions.get("cik"))) != cik:
        raise ValueError("The SEC submissions response belongs to a different issuer.")
    filings = _filing_rows(client, issuer, submissions, as_of, years)
    annual = [row for row in filings if row["form"] in _ANNUAL_FORMS and row["period_end"]]
    interim = [row for row in filings if row["form"] in _INTERIM_FORMS]
    facts = client.company_facts(cik)
    if _cik(str(facts.get("cik"))) != cik:
        raise ValueError("The SEC Company Facts response belongs to a different issuer.")
    actuals = normalize_annual_financials(facts, cik, as_of, annual, years=years)
    latest_interim = normalize_latest_interim_financials(facts, cik, as_of, annual + interim)
    earnings_interim = {"current": latest_interim["periods"], "prior": []}
    if latest_interim.get("status") == "available":
        current = latest_interim["periods"][-1]
        end = date.fromisoformat(current["period_end"])
        prior_filings = [row for row in interim
                         if 357 <= (end - date.fromisoformat(row["period_end"])).days <= 373]
        earnings_interim["prior"] = normalize_latest_interim_financials(
            facts, cik, as_of,
            [row for row in annual if row["period_end"] < min(item["period_end"] for item in prior_filings)] + prior_filings,
        )["periods"] if current["period_start"] and prior_filings else []
    if statements_only:
        foreign = [row for row in annual if row["form"] in {"20-F", "20-F/A", "40-F", "40-F/A"}]
        # A filer can change reporting regimes. An old 40-F is no longer the
        # governing annual basis when the issuer now files domestic reports.
        if foreign and max(row["period_end"] for row in foreign) != max(row["period_end"] for row in annual):
            foreign = []
        return {
            "issuer": {**issuer, "sic": submissions.get("sic"), "industry": submissions.get("sicDescription")},
            "as_of": as_of.isoformat(),
            "actuals": actuals,
            "earnings_interim": earnings_interim,
            **({"foreign_filings": {"annual": foreign[:years*2],
                                    "interim_reports": [row for row in filings if row["form"] in {"6-K", "6-K/A"}][
                                        :180 if cik == "0001900304" else 10]}}
               if foreign else {}),
            "coverage": {"annual_statement_history": len(actuals["periods"]), "annual_filing_links": len(annual),
                         "latest_interim_financials": len(latest_interim["periods"])},
        }
    operating_facts = collect_operating_facts(client, cik, actuals, latest_interim)
    available_balances = [row["period_end"] for row in (*actuals["periods"], *latest_interim["periods"])
                          if row.get("metrics", {}).get("total_assets") is not None]
    last_balance = max(available_balances, default=None)
    current_reports = [row for row in filings if last_balance and row["filed_on"] > last_balance
                       and (row["form"] in {"6-K", "6-K/A"} or
                            row["form"] in {"8-K", "8-K/A"} and _FINANCING_ITEMS.intersection(re.split(r"[,;\s]+", row["items"])))]
    capital_bridge = collect_capital_events(client, current_reports, balance_date=last_balance, as_of=as_of)
    release_result = collect_earnings_releases(client, cik, filings, limit=release_limit) if release_limit else {"releases": [], "missing": []}
    guidance = collect_revenue_guidance(client, release_result["releases"])
    event_guidance = collect_event_guidance(client, cik, filings, as_of=as_of)
    # A normalized revenue source records the issuer's reporting currency.
    latest_period = next(iter(latest_interim["periods"]), None) or next(iter(actuals["periods"][-1:]), None)
    reporting_currency = latest_period.get("sources", {}).get("revenue", {}).get("unit") if latest_period else None
    event_revenue = parse_event_revenue_guidance(
        event_guidance["observations"], cik=cik, as_of=as_of,
        dollar_currency="USD" if reporting_currency == "USD" else None,
    )
    missing = []
    if not annual:
        missing.append("No annual filing with a reported fiscal period was found by the cutoff.")
    if not interim:
        missing.append("No 10-Q was found. Foreign filers may report interim results in 6-K exhibits.")
    if not actuals["periods"]:
        missing.append("No supported annual US GAAP history was mapped from EDGAR Company Facts.")
    packet = {
        "issuer": {**issuer, "sic": submissions.get("sic"), "industry": submissions.get("sicDescription")},
        "as_of": as_of.isoformat(),
        "coverage": {
            "annual_statement_history": len(actuals["periods"]),
            "interim_filing_links": len(interim[:4]),
            "latest_interim_financials": len(latest_interim["periods"]),
            "earnings_release_links": len(release_result["releases"]),
            "earnings_release_revenue_guidance": guidance["status"],
            "operating_facts": operating_facts["status"],
            "event_guidance": event_guidance["status"],
            "event_revenue_guidance": event_revenue["status"],
            "post_balance_current_report_links": len(current_reports),
            "identified_capital_events": len(capital_bridge["events"]),
            "market_data": "not_collected",
            "valuation": "not_generated",
        },
        "filings": {"annual": annual[:years * 2], "interim": interim[:4], "post_balance_current_reports": current_reports},
        "actuals": actuals,
        "interim_actuals": latest_interim,
        "earnings_interim": earnings_interim,
        "operating_facts": operating_facts,
        "earnings": release_result,
        "revenue_guidance": guidance,
        "event_guidance": event_guidance,
        "event_revenue_guidance": event_revenue,
        "capital_bridge": capital_bridge,
        "missing": missing,
        "sources": {
            "ticker_directory": TICKER_DIRECTORY_URL,
            "submissions": f"https://data.sec.gov/submissions/CIK{cik}.json",
            "company_facts": f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
        },
        "limitations": [
            "The SEC ticker directory reflects current listings. Historical ticker or issuer changes may require a different identifier.",
            "The cutoff includes the full SEC filing date. Intraday availability is outside this intake.",
            "Financial normalization uses USD US GAAP tags. Dimensional operating facts and event guidance passages are evidence for later forecast selection.",
            "Structured revenue guidance covers explicit full fiscal-year consolidated dollar amounts found in the selected SEC earnings releases.",
            "Post-balance current reports are candidates for reviewing financing and other material events. Their effects on cash, debt and shares are not yet reconciled.",
        ],
    }
    if int(cik) == 1341439:
        offering = collect_oracle_offerings(client, filings, capital_bridge, as_of=as_of)
        capital_bridge["offering_terms"] = offering
        packet["coverage"]["offering_terms"] = offering["status"]
        ownership = [
            {
                "form": filing.form, "accession": filing.accession, "filed_on": filing.filing_date.isoformat(),
                "url": filing_document_url(cik, filing.accession, filing.primary_document.rsplit("/", 1)[-1]),
            }
            for filing in recent_filings(submissions, as_of=as_of)
            if filing.form in {"4", "4/A"} and re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*\.xml",
                                                            filing.primary_document.rsplit("/", 1)[-1], re.I)
        ]
        for key, evidence in (
            ("dividends", collect_oracle_dividends(client, filings, balance_date=last_balance, as_of=as_of,
                                                   payment_filings=ownership)),
            ("dilution_evidence", collect_oracle_dilution(client, offering, as_of=as_of)),
            ("funding_schedules", collect_oracle_funding_schedules(client, packet)),
            ("debt_pricing", collect_oracle_debt_pricing(client, filings, offering, as_of=as_of)),
        ):
            capital_bridge[key] = evidence
            packet["coverage"][key] = evidence["status"]
        latest_annual = packet["actuals"]["periods"][-1:]
        if latest_annual:
            final_year = date.fromisoformat(latest_annual[0]["period_end"]).year + 5
            try:
                payments = oracle_issued_note_payments(offering, as_of=as_of, through=date(final_year, 5, 31))
            except ValueError as exc:
                capital_bridge["issued_note_payments"] = {"status": "missing", "missing": [str(exc)]}
            else:
                capital_bridge["issued_note_payments"] = {
                    "status": "available", "payments": payments, "source": offering["debt"]["source"],
                }
        packet["quarter_revenue_guidance"] = collect_oracle_quarter_guidance(client, release_result["releases"],
                                                                           filings, as_of=as_of)
        packet["coverage"]["quarter_revenue_guidance"] = packet["quarter_revenue_guidance"]["status"]
        packet["opening_evidence"] = build_oracle_opening_evidence(packet)
        packet["coverage"]["opening_evidence"] = packet["opening_evidence"]["status"]
        amortization = collect_oracle_amortization(client, packet["opening_evidence"])
        packet["opening_evidence"]["intangible_amortization"] = amortization
        packet["coverage"]["intangible_amortization"] = amortization["status"]
        packet["capital_bridge"]["offering_adjustments"] = reconcile_oracle_offering_adjustments(
            packet["opening_evidence"], packet["capital_bridge"]["offering_terms"],
        )
        packet["coverage"]["offering_adjustments"] = packet["capital_bridge"]["offering_adjustments"]["status"]
        capital_bridge["cutoff_movements"] = summarize_oracle_cutoff_movements(packet["opening_evidence"], capital_bridge)
        packet["revenue_basis"] = build_oracle_revenue_basis(packet)
        packet["coverage"]["revenue_basis"] = packet["revenue_basis"]["status"]
        packet["revenue_forecast"] = build_oracle_revenue_forecast(packet, packet["revenue_basis"])
        packet["coverage"]["revenue_forecast"] = packet["revenue_forecast"]["status"]
        packet["valuation_readiness"] = oracle_pilot_readiness(packet)
        packet["capacity_scenarios"] = compare_oracle_buildout(as_of)
    return packet

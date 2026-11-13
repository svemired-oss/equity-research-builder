"""Collect dated SEC evidence for the GD input layout."""

from dataclasses import dataclass, field
from datetime import date
from math import isclose
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from .filing_xbrl import load_filing_instance
from .gd_actuals import SourcedValue, map_gd_actuals
from .gd_history_checks import ANNUAL_CASH_FIELDS, AnnualCashActuals, HistoricalCheck, reconcile_gd_history
from .gd_inputs import InputSource, _array, _cell, _day, _fields, _metadata, _number, _object, _read, _source, _text
from .gd_outlook import OUTLOOK_METRICS, AnnualOutlook, parse_gd_annual_outlook
from .gd_releases import EarningsRelease, RecentFiling, get_gd_release_for_filing, is_earnings_filing, recent_filings
from .gd_schedule_history import map_gd_schedule_history
from .gd_segment_history import map_gd_segment_history, map_gd_segment_tables
from .reconciliation import TOLERANCE_MILLIONS
from .sec_client import SECError, filing_document_url, filing_index_url


GD_CIK = "0000040533"
HISTORY_COLUMNS = "BCDEF"


class EvidenceClient(Protocol):
    def submissions(self, cik: str) -> dict: ...

    def company_facts(self, cik: str) -> dict: ...

    def get_text(self, url: str) -> str: ...


@dataclass(frozen=True)
class FilingReference:
    form: str
    accession: str
    filing_date: date
    report_date: date
    url: str


@dataclass(frozen=True)
class GDHistoryEvidence:
    as_of: date
    historical_years: tuple[int, ...]
    history: dict[str, float]
    provenance: dict[str, InputSource]
    missing: dict[str, str]
    latest_interim: FilingReference | None = field(default=None, kw_only=True)
    cash_flow_actuals: dict[int, AnnualCashActuals] | None = field(default=None, kw_only=True)


@dataclass(frozen=True)
class GDEvidence(GDHistoryEvidence):
    filings: tuple[FilingReference, ...]
    detail_filings: tuple[FilingReference, ...]
    earnings_release: EarningsRelease | None
    annual_outlook: tuple[AnnualOutlook, ...]
    historical_checks: tuple[HistoricalCheck, ...]
    coverage_limits: tuple[str, ...]
    ticker: str = "GD"
    currency: str = "USD"
    schema_version: int = 1
    document_type: str = "gd_source_evidence"
    projection_ready: bool = False


def _validate_filing_reference(filing: FilingReference, as_of: date) -> None:
    if not isinstance(filing, FilingReference) or filing.form not in ("10-K", "10-Q"):
        raise ValueError("Expected dated GD Form 10-K or 10-Q filing metadata.")
    if type(filing.report_date) is not date or type(filing.filing_date) is not date or not (
        filing.report_date <= filing.filing_date <= as_of
    ):
        raise ValueError("The filing must cover a period on or before its filing date and be available by the cutoff.")
    if not isinstance(filing.url, str):
        raise ValueError("The filing requires its canonical GD EDGAR URL.")
    filename = urlsplit(filing.url).path.rsplit("/", 1)[-1]
    if filing.url != filing_document_url(GD_CIK, filing.accession, filename):
        raise ValueError("The filing requires its canonical GD EDGAR URL and matching accession.")


def _load_filing_reference(raw: object, label: str, as_of: date) -> FilingReference:
    item = _fields(raw, label, {"form", "accession", "filing_date", "report_date", "url"})
    filing = FilingReference(
        _text(item["form"], f"{label} form"),
        _text(item["accession"], f"{label} accession"),
        _day(item["filing_date"], f"{label} filing date"),
        _day(item["report_date"], f"{label} report date"),
        _text(item["url"], f"{label} URL"),
    )
    _validate_filing_reference(filing, as_of)
    return filing


def load_gd_history_evidence(path: Path) -> GDHistoryEvidence:
    """Read the dated history section of a collector result for a fresh model run."""
    document = _object(_read(path), "GD source evidence")
    _metadata(document)
    if (document.get("document_type"), document.get("ticker"), document.get("currency")) != (
        "gd_source_evidence", "GD", "USD",
    ):
        raise ValueError("Expected a GD source evidence document in USD.")

    as_of = _day(document.get("as_of"), "evidence as_of")
    years = tuple(range(as_of.year - 5, as_of.year))
    if as_of.year <= 5 or document.get("historical_years") != list(years):
        raise ValueError("Evidence must cover the five fiscal years before the forecast year.")
    sources = _object(document.get("provenance"), "historical provenance")
    values = _object(document.get("history"), "sourced history")
    if sources.keys() != values.keys():
        raise ValueError("Every historical evidence value must have exactly one source entry.")

    history = {}
    provenance = {}
    for key, value in values.items():
        _, _, index = _cell(key, forecast=False)
        source = _source(sources[key], f"source for {key}", as_of)
        if source.kind not in ("sec_filing", "issuer_release", "derived"):
            raise ValueError(f"Historical evidence at {key} requires a filing, issuer release, or derived fact.")
        if source.kind == "sec_filing":
            url = urlsplit(source.url)
            if url.scheme != "https" or url.hostname not in ("www.sec.gov", "data.sec.gov", "sec.gov"):
                raise ValueError(f"SEC evidence at {key} requires an official SEC HTTPS URL.")
        if source.available_on < date(as_of.year + index, 12, 31):
            raise ValueError(f"The evidence for {key} predates the end of its fiscal year.")
        history[key] = _number(value, key)
        provenance[key] = source

    missing = {key: _text(value, f"gap for {key}") for key, value in _object(document.get("missing"), "evidence gaps").items()}
    if missing.keys() & history.keys():
        raise ValueError("A historical cell cannot be both sourced and missing.")
    filings = tuple(
        _load_filing_reference(raw, "filing", as_of)
        for raw in _array(document.get("filings", []), "filings")
    )
    if "history_basis" in document:
        if document["history_basis"] != "original_annual_filings":
            raise ValueError("Unsupported GD historical evidence basis.")
        originals = {
            year: tuple(filing for filing in filings if filing.form == "10-K" and filing.report_date == date(year, 12, 31))
            for year in years
        }
        if any(len(refs) != 1 for refs in originals.values()):
            raise ValueError("As-filed history requires one exact-year 10-K for each fiscal year.")
        for key, source in provenance.items():
            _, _, index = _cell(key, forecast=False)
            (filing,) = originals[as_of.year + index]
            if source.kind not in {"sec_filing", "derived"} or (source.available_on, source.url) != (
                filing.filing_date, filing.url,
            ):
                raise ValueError(f"As-filed evidence at {key} must cite its exact-year 10-K.")
    latest_interim = max(
        (filing for filing in filings if filing.form == "10-Q"),
        key=lambda filing: (filing.report_date, filing.filing_date, filing.accession),
        default=None,
    )
    if (raw_interim := document.get("latest_interim")) is not None:
        explicit = _load_filing_reference(raw_interim, "latest_interim", as_of)
        if explicit.form != "10-Q" or (latest_interim is not None and explicit != latest_interim):
            raise ValueError("latest_interim must identify the latest eligible 10-Q and agree with the filings list.")
        latest_interim = explicit
    cash_flow_actuals = None
    if (raw_cash := document.get("cash_flow_actuals")) is not None:
        cash_flow_actuals = {}
        annual_sources = {
            (filing.report_date.year, filing.filing_date, filing.url)
            for filing in filings if filing.form == "10-K" and (filing.report_date.month, filing.report_date.day) == (12, 31)
        }
        for key, raw in _fields(raw_cash, "annual cash flow actuals", set(), {str(year) for year in years}).items():
            year = int(key)
            label = f"FY{year} cash flow actuals"
            row = _fields(raw, label, ANNUAL_CASH_FIELDS | {"source"})
            source = _source(row["source"], label, as_of)
            if source.kind != "sec_filing" or (year, source.available_on, source.url) not in annual_sources:
                raise ValueError(f"{label} must cite its exact-year 10-K in filings.")
            amounts = {name: _number(row[name], f"{label}.{name}") for name in sorted(ANNUAL_CASH_FIELDS)}
            if min(amounts["capital_expenditures"], amounts["cash_beginning"], amounts["cash_ending"]) < 0:
                raise ValueError(f"{label} requires nonnegative capex and beginning and ending cash.")
            cash_flow_actuals[year] = AnnualCashActuals(amounts, source)
    return GDHistoryEvidence(
        as_of, years, history, provenance, missing, latest_interim=latest_interim, cash_flow_actuals=cash_flow_actuals,
    )


def _statement_filings(recent: tuple[RecentFiling, ...]) -> tuple[FilingReference, ...]:
    candidates = []
    for filing in recent:
        if filing.form not in ("10-K", "10-Q") or filing.report_date is None or filing.report_date > filing.filing_date:
            continue
        url = (
            filing_document_url(GD_CIK, filing.accession, filing.primary_document)
            if filing.primary_document else filing_index_url(GD_CIK, filing.accession)
        )
        candidates.append(
            FilingReference(filing.form, filing.accession, filing.filing_date, filing.report_date, url)
        )
    return tuple(sorted(candidates, key=lambda item: (item.report_date, item.filing_date, item.accession), reverse=True))


def collect_gd_evidence(client: EvidenceClient, *, as_of: date) -> GDEvidence:
    """Retrieve available evidence without filling the uncovered GD input cells."""
    years = tuple(range(as_of.year - 5, as_of.year))
    submissions = client.submissions(GD_CIK)
    if str(submissions.get("cik", "")).lstrip("0") != GD_CIK.lstrip("0"):
        raise ValueError("SEC submissions response does not identify General Dynamics.")
    recent = recent_filings(submissions, as_of=as_of)
    statements = _statement_filings(recent)
    latest = {form: next((filing for filing in statements if filing.form == form), None) for form in ("10-K", "10-Q")}
    filings = tuple(filing for filing in latest.values() if filing is not None)
    actuals = map_gd_actuals(client.company_facts(GD_CIK), as_of=as_of, years=years)
    history = {key: fact.value for key, fact in actuals.facts.items()}
    provenance = {key: fact.source for key, fact in actuals.facts.items()}
    missing = dict(actuals.missing)
    conflicts = {key for key, reason in missing.items() if reason.startswith("Conflicting")}
    for form, filing in latest.items():
        if filing is None:
            missing[form] = f"No dated {form} was available in the recent submissions at the cutoff."

    def accept(key: str, value: float, source: InputSource) -> None:
        if key in conflicts:
            return
        if key in history and not isclose(history[key], value, rel_tol=0.0, abs_tol=TOLERANCE_MILLIONS):
            missing[key] = (
                f"Conflicting sources reported {history.pop(key):g} and {value:g}. "
                f"{provenance.pop(key).citation} {source.citation}"
            )
            conflicts.add(key)
            return
        if key not in history:
            history[key] = value
            provenance[key] = source
        missing.pop(key, None)

    def accept_mapping(values: dict[str, SourcedValue], gaps: dict[str, str]) -> None:
        for key, fact in values.items():
            accept(key, fact.value, fact.source)
        for key, reason in gaps.items():
            if reason.startswith("Conflicting") and key not in conflicts:
                history.pop(key, None)
                provenance.pop(key, None)
                missing[key] = reason
                conflicts.add(key)
            elif key not in history:
                missing.setdefault(key, reason)

    release = None
    earnings_filings = tuple(filing for filing in recent if is_earnings_filing(filing))
    if earnings_filings:
        try:
            release = get_gd_release_for_filing(client, earnings_filings[0])
        except ValueError as exc:
            missing["earnings_release"] = f"Earnings exhibit retrieval failed. {exc}"

    if release is None:
        missing.setdefault("earnings_release", "No supported earnings exhibit was found in the available SEC filing history.")
    elif not release.guidance:
        missing["earnings_release_guidance"] = release.guidance_note

    annual_outlook = ()
    annual_html = None
    annual = latest["10-K"]
    if annual is not None:
        try:
            annual_html = client.get_text(annual.url)
            annual_outlook = parse_gd_annual_outlook(
                annual_html, year=as_of.year, accession=annual.accession,
                filing_date=annual.filing_date, url=annual.url,
            )
        except SECError as exc:
            missing["annual_outlook"] = f"Annual filing retrieval failed. {exc}"
    found_outlook = {item.metric for item in annual_outlook}
    for metric in OUTLOOK_METRICS:
        if metric not in found_outlook:
            missing[f"annual_outlook:{metric}"] = f"No unambiguous FY{as_of.year} guidance in the selected annual filing."

    detail_filings = []
    for year, column in zip(years, HISTORY_COLUMNS, strict=True):
        detail_key = f"filing_details:FY{year}"
        filing = next((item for item in statements if item.form == "10-K" and item.report_date == date(year, 12, 31)), None)
        if filing is None:
            missing[detail_key] = "No original annual filing for this fiscal year was found at the cutoff."
            continue
        segment_gaps = True
        try:
            instance = load_filing_instance(client, GD_CIK, filing.accession)
        except ValueError as exc:
            missing[detail_key] = f"Annual filing detail unavailable. {exc}"
        else:
            detail_filings.append(FilingReference(filing.form, filing.accession, filing.filing_date, filing.report_date, instance.url))
            for mapper in (map_gd_segment_history, map_gd_schedule_history):
                values, gaps = mapper(instance, year=year, column=column, accession=filing.accession, filing_date=filing.filing_date)
                if mapper is map_gd_segment_history:
                    segment_gaps = bool(gaps)
                accept_mapping(values, gaps)
        # The instance covers segment totals and earnings. The forecast also
        # needs the latest year's product and service breakdown from the 10-K.
        if segment_gaps or year == years[-1]:
            try:
                html = annual_html if filing == annual and annual_html is not None else client.get_text(filing.url)
            except SECError as exc:
                missing[f"segment_tables:FY{year}"] = f"Annual statement tables unavailable. {exc}"
                continue
            values, gaps = map_gd_segment_tables(
                html, filing.url, year=year, column=column, accession=filing.accession, filing_date=filing.filing_date,
            )
            accept_mapping(values, gaps)

    delivery_fields = {year: f"Revenue model!{column}13" for year, column in zip(years, HISTORY_COLUMNS, strict=True)}

    def accept_deliveries(exhibit: EarningsRelease) -> None:
        for metric in exhibit.metrics:
            if metric.metric != "gulfstream_deliveries" or metric.year not in delivery_fields:
                continue
            key = delivery_fields[metric.year]
            accept(
                key, metric.value,
                InputSource("issuer_release", f"Form 8-K {exhibit.accession}, {metric.evidence}", exhibit.filing_date, url=exhibit.url),
            )

    if release is not None:
        accept_deliveries(release)
    for filing in earnings_filings[1:]:
        year = filing.filing_date.year - 1
        covered_periods = (delivery_fields[period] for period in (year, year - 1) if period in delivery_fields)
        if filing.filing_date.month > 2 or not any(key not in history and key not in conflicts for key in covered_periods):
            continue
        try:
            accept_deliveries(get_gd_release_for_filing(client, filing))
        except ValueError as exc:
            missing.setdefault(f"earnings_release:FY{year}", f"Historical earnings exhibit unavailable. {exc}")

    for year, column in zip(years, HISTORY_COLUMNS, strict=True):
        deliveries = f"Revenue model!{column}13"
        aircraft_revenue = f"Revenue model!{column}8"
        implied_price = f"Revenue model!{column}15"
        if deliveries not in history:
            missing.setdefault(deliveries, f"No verified full-year FY{year} delivery count in the eligible earnings exhibits.")
        if history.get(deliveries, 0) > 0 and aircraft_revenue in history:
            accept(
                implied_price, history[aircraft_revenue] / history[deliveries],
                InputSource(
                    "derived", f"Aircraft manufacturing revenue ({aircraft_revenue}) divided by aircraft deliveries ({deliveries}).",
                    max(provenance[aircraft_revenue].available_on, provenance[deliveries].available_on),
                ),
            )
        elif implied_price not in history:
            missing.setdefault(implied_price, "Requires sourced aircraft manufacturing revenue and a positive delivery count.")

    return GDEvidence(
        as_of=as_of,
        historical_years=years,
        history=history,
        provenance=provenance,
        missing=missing,
        filings=filings,
        detail_filings=tuple(detail_filings),
        earnings_release=release,
        annual_outlook=annual_outlook,
        historical_checks=reconcile_gd_history(history, years),
        latest_interim=latest["10-Q"],
        coverage_limits=(
            "Annual detail covers the original 10-Ks available in recent submissions. Conflicting facts are held out.",
            "Quarterly filing references identify the documents. The historical value mapping uses fiscal years.",
            "Annual guidance reflects the filing date. Later issuer updates may supersede it.",
            "Guidance is evidence only and does not populate forecast assumptions automatically.",
            "The evidence bundle requires additional inputs before it can drive a standalone valuation.",
        ),
    )

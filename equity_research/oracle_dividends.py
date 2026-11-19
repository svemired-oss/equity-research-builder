"""Collect Oracle cash-dividend declarations and separately documented payments."""

from datetime import date, timedelta
from decimal import Decimal
import re
from urllib.parse import urlsplit
from xml.etree import ElementTree

from .sec_capital_events import _DATE, _MONTHS, _SECTION
from .sec_client import filing_document_url
from .sec_event_guidance import _EventText


_CIK = "1341439"
_AMOUNT = r"(?:[1-9]\d{0,2}(?:,\d{3})+|\d+)(?:\.\d{1,6})?"
_DECLARE = re.compile(r"\bboard(?: of directors)? (?:has )?declared (?:a )?(?:quarterly )?cash dividends? of ", re.I)
_SECURITY = r"(?:mandatory convertible preferred stock|common stock)"
_PER_SHARE = re.compile(rf"\$({_AMOUNT}) per share of (?:our |its )?outstanding ({_SECURITY})\b", re.I)
_SCHEDULE = re.compile(
    rf"\b({_SECURITY}) dividend is payable on ({_DATE}) to stockholders of record as of "
    rf"(?:the close of business on )?({_DATE})\b",
    re.I,
)
_DEPOSITARY = re.compile(
    r"\bDepositary Shares, each representing a 1/([1-9]\d{0,2}(?:,\d{3})*|[1-9]\d+)th interest in a share of "
    r"(?:\d+(?:\.\d+)?% )?Series D Mandatory Convertible Preferred Stock\b",
    re.I,
)
_PAYMENT = re.compile(
    rf"\bon ({_DATE})(?:\s*\(\$[\d.,]+\))?, the date on which dividends were paid on Oracle['’]s common stock\b",
    re.I,
)
_DENIAL = re.compile(r"^(?:403 Forbidden|404 Not Found|Access Denied|Your Request Originates)\b", re.I)
_XML_DECLARATION = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)\b", re.I)
_MAX_DOCUMENT_CHARS = 512_000
_DECLARATION_LOOKBACK = timedelta(days=120)
_PAYMENT_LOOKAHEAD = timedelta(days=10)
_MAX_REPORTS = 20


def _date(label: str) -> date:
    month, day, year = label.replace(",", "").split()
    return date(int(year), _MONTHS.index(month.capitalize()) + 1, int(day))


def _security(label: str) -> str:
    return "common" if label.casefold() == "common stock" else "series_d_mandatory_convertible_preferred"


def _verified_document(client, filing: dict, suffixes: tuple[str, ...]) -> str:
    url = filing["url"]
    filename = urlsplit(url).path.rsplit("/", 1)[-1]
    if not filename.casefold().endswith(suffixes) or url != filing_document_url(_CIK, filing["accession"], filename):
        raise ValueError("Expected a canonical Oracle SEC filing URL.")
    document = client.get_text(url)
    if len(document) > _MAX_DOCUMENT_CHARS or _DENIAL.search(document.lstrip()):
        raise ValueError("Oracle SEC filing exceeded the extraction limit or was unavailable.")
    return document


def _declarations(html: str, filing: dict) -> list[dict]:
    blocks = _EventText(html).blocks
    depositary_ratios = {
        int(match[1].replace(",", "")) for block in blocks for match in _DEPOSITARY.finditer(block)
    }
    results, item = [], None
    for block in blocks:
        if heading := _SECTION.match(block):
            item = heading[1]
        if item != "8.01" or not (declared := _DECLARE.search(block)):
            continue
        sentence, separator, dates = block[declared.end():].partition(". ")
        if not separator:
            continue
        per_share = list(_PER_SHARE.finditer(sentence))
        amounts = {_security(match[2]): Decimal(match[1].replace(",", "")) for match in per_share}
        if len(amounts) != len(per_share):
            continue
        for match in _SCHEDULE.finditer(dates):
            security = _security(match[1])
            amount = amounts.get(security)
            if not amount or amount <= 0:
                continue
            if security != "common" and len(depositary_ratios) != 1:
                # The body names the mandatory convertible generically.
                # Require the cover-page terms to identify its series.
                continue
            try:
                payment = _date(match[2]).isoformat()
                record = _date(match[3]).isoformat()
            except ValueError:
                continue
            if not filing["filed_on"] <= record <= payment:
                continue
            event = {
                "security": security,
                "per_share_usd": float(amount),
                "share_basis": "common_share" if security == "common" else "underlying_preferred_share",
                "announced_by": filing["filed_on"],
                "record_date": record,
                "payment_date": payment,
                "filed_on": filing["filed_on"],
                "accession": filing["accession"],
                "url": filing["url"],
                "evidence": block,
            }
            if security != "common":
                ratio = next(iter(depositary_ratios))
                event["depositary_shares_per_preferred_share"] = ratio
                event["per_depositary_share_usd"] = float(amount / ratio)
            results.append(event)
    return results


def _common_payment(document: str, filed_on: str) -> dict[str, str]:
    if _XML_DECLARATION.search(document):
        raise ValueError("Unexpected ownership form XML declaration.")
    form = ElementTree.fromstring(document)
    issuer = (form.findtext("issuer/issuerCik") or "").strip().lstrip("0")
    if form.tag != "ownershipDocument" or issuer != _CIK:
        raise ValueError("Ownership report does not identify Oracle as the issuer.")
    evidence = {}
    for footnote in form.findall("footnotes/footnote"):
        text = " ".join("".join(footnote.itertext()).split())
        for match in _PAYMENT.finditer(text):
            payment = _date(match[1]).isoformat()
            if payment <= filed_on:
                evidence[payment] = text
    return evidence


def collect_oracle_dividends(
    client, current_reports, *, balance_date: str | None, as_of: date, payment_filings=(),
) -> dict:
    """Collect verified per-share declarations for payments after the opening date."""
    result = {
        "status": "unavailable", "balance_date": balance_date, "as_of": as_of.isoformat(),
        "events": [], "missing": [],
        "limitations": [
            "Scheduled payments require separate confirmation. An elapsed scheduled date alone does not confirm cash paid.",
            "Aggregate cash is unavailable without the actual cash paid or a verified record-date eligible share count.",
            "Only Oracle cash dividend disclosures matching the supported SEC filing wording are extracted.",
        ],
    }
    if balance_date is None:
        result["missing"].append("No opening balance date is available.")
        return result
    balance = date.fromisoformat(balance_date)
    if balance > as_of:
        raise ValueError("Opening balance date cannot be after the valuation cutoff.")
    earliest = (balance - _DECLARATION_LOOKBACK).isoformat()
    cutoff = as_of.isoformat()
    reports = {
        filing["accession"]: filing for filing in current_reports if filing["form"] in {"8-K", "8-K/A"}
        and "8.01" in re.split(r"[,;\s]+", filing.get("items") or "")
        and earliest <= filing["filed_on"] <= cutoff
    }
    events = {}
    for filing in sorted(reports.values(), key=lambda row: (row["filed_on"], row["accession"]), reverse=True)[:_MAX_REPORTS]:
        try:
            declarations = _declarations(_verified_document(client, filing, (".htm", ".html")), filing)
        except (OSError, ValueError):
            result["missing"].append(f"{filing['accession']}: Oracle dividend declaration evidence unavailable.")
            continue
        for event in declarations:
            if event["payment_date"] <= balance_date:
                continue
            key = (event["security"], event["payment_date"])
            terms = (event["per_share_usd"], event["record_date"])
            events.setdefault(key, {}).setdefault(terms, event)
    if len(reports) > _MAX_REPORTS:
        result["missing"].append("Older current reports were outside the declaration extraction limit.")

    common = {payment for security, payment in events if security == "common" and payment <= cutoff}
    confirmations = {}
    candidates = {
        filing["accession"]: filing for filing in payment_filings if filing["form"] in {"4", "4/A"}
        and filing["filed_on"] <= cutoff and any(
            date.fromisoformat(payment) <= date.fromisoformat(filing["filed_on"])
            <= date.fromisoformat(payment) + _PAYMENT_LOOKAHEAD for payment in common
        )
    }
    for filing in sorted(candidates.values(), key=lambda row: (row["filed_on"], row["accession"]))[:_MAX_REPORTS]:
        try:
            paid = _common_payment(_verified_document(client, filing, (".xml",)), filing["filed_on"])
        except (OSError, ValueError, ElementTree.ParseError):
            continue
        for payment, evidence in paid.items():
            if payment in common:
                confirmations.setdefault(payment, {**{key: filing[key] for key in ("url", "accession", "filed_on")},
                                                   "evidence": evidence})
        if common <= confirmations.keys():
            break

    for (security, payment), matches in sorted(events.items(), key=lambda item: (item[0][1], item[0][0])):
        if len(matches) != 1:
            result["missing"].append(f"Conflicting Oracle {security} dividend declarations for {payment}.")
            continue
        event = next(iter(matches.values()))
        confirmation = confirmations.get(payment) if security == "common" else None
        event["payment_status"] = (
            "scheduled_after_cutoff" if payment > cutoff else "confirmed_paid" if confirmation else "due_by_cutoff_unconfirmed"
        )
        if confirmation:
            event["payment_confirmation"] = confirmation
        result["events"].append(event)
    if result["events"]:
        result["status"] = "partial" if result["missing"] else "available"
    elif not result["missing"]:
        result["missing"].append("No supported Oracle cash dividend declarations were found for the window.")
    return result

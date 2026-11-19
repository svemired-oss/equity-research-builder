"""Read the final Oracle note-pricing term sheet for a matched offering."""

from calendar import monthrange
from datetime import date
from decimal import Decimal
import re

from .oracle_offerings import _REGISTRATION, _day, _int, _page, _source, _table_rows
from .sec_capital_events import _DATE


_TERM_SHEET = re.compile(rf"\bIssuer Free Writing Prospectus dated ({_DATE})\b", re.I)
_FINAL = re.compile(r"\bFINAL PRICING TERM SHEET\b", re.I)
_USD = re.compile(r"\bAll references to dollar amounts are references to U\.S\. dollars\b", re.I)
_DOLLARS = re.compile(r"\$((?:[1-9]\d{0,2}(?:,\d{3})+|[1-9]\d*))")
_PERCENT = r"(\d+(?:\.\d+)?)%"
_TREASURY = re.compile(rf"{_PERCENT} due ({_DATE})", re.I)
_SPREAD = re.compile(r"\+(\d+) basis points", re.I)
_SETTLEMENT = re.compile(rf"({_DATE})(?: \(T\+\d+\))?", re.I)
_PRICE = re.compile(rf"{_PERCENT} of the principal amount", re.I)
_YIELD = re.compile(_PERCENT)
_FIELDS = {
    "Issuer:", "Principal Amount:", "Maturity:", "Coupon:", "Price to Public:",
    "Benchmark Treasury:", "Benchmark Treasury Yield:", "Spread to Benchmark Treasury:",
    "Yield to Maturity:", "Trade Date:", "Settlement Date:",
}


def _match(pattern, value):
    match = pattern.fullmatch(value)
    if not match:
        raise ValueError("Unexpected term sheet amount, percentage, or date format.")
    return match


def _note_row(terms, priced_on, closed_on):
    if terms["Issuer:"].casefold() != "oracle corporation":
        raise ValueError("The pricing table identifies a different issuer.")
    traded = _day(terms["Trade Date:"])
    settles = _day(_match(_SETTLEMENT, terms["Settlement Date:"])[1])
    maturity = _day(terms["Maturity:"])
    benchmark = _match(_TREASURY, terms["Benchmark Treasury:"])
    benchmark_maturity = _day(benchmark[2])
    if (traded, settles) != (priced_on, closed_on) or min(maturity, benchmark_maturity) <= closed_on:
        raise ValueError("The trade, settlement, or maturity dates do not match the closed issue.")
    principal = _int(_match(_DOLLARS, terms["Principal Amount:"])[1])
    coupon = Decimal(_match(_YIELD, terms["Coupon:"])[1])
    issue_price = Decimal(_match(_PRICE, terms["Price to Public:"])[1])
    treasury_yield = Decimal(_match(_YIELD, terms["Benchmark Treasury Yield:"])[1])
    issue_ytm = Decimal(_match(_YIELD, terms["Yield to Maturity:"])[1])
    spread_bps = int(_match(_SPREAD, terms["Spread to Benchmark Treasury:"])[1])
    if issue_price <= 0 or issue_ytm <= 0 or treasury_yield + Decimal(spread_bps) / 100 != issue_ytm:
        raise ValueError("The issue yield does not reconcile to the Treasury benchmark and spread.")
    return {
        "principal_usd": principal,
        "maturity_year": maturity.year,
        "matures_on": maturity.isoformat(),
        "coupon_percent": float(coupon),
        "issue_price_percent_of_par": float(issue_price),
        "issue_ytm_rate": float(issue_ytm / 100),
        "benchmark_treasury_coupon_rate": float(Decimal(benchmark[1]) / 100),
        "benchmark_treasury_matures_on": benchmark_maturity.isoformat(),
        "benchmark_treasury_yield_rate": float(treasury_yield / 100),
        "spread_bps": spread_bps,
    }


def _pricing_terms(page, offering, priced_on, closed_on):
    header = page.text[:1500]
    cover_dates = {_day(match[1]) for match in _TERM_SHEET.finditer(header)}
    shelves = set(_REGISTRATION.findall(header))
    if cover_dates != {priced_on} or shelves != {offering["shelf"]} or not _FINAL.search(header):
        raise ValueError("No final term sheet for the verified shelf and pricing date.")
    if not _USD.search(header):
        raise ValueError("The final term sheet does not identify a U.S. dollar issue.")

    tranches = []
    for table in page.tables:
        terms = {}
        for row in _table_rows(table):
            if len(row) == 2 and row[0] in _FIELDS:
                label, value = row
                if label in terms and terms[label] != value:
                    raise ValueError("Conflicting terms in a pricing table.")
                terms[label] = value
        if terms.keys() >= _FIELDS:
            tranches.append(_note_row(terms, priced_on, closed_on))
    return tranches


def _matches_closed_debt(rows, debt):
    def identifiers(notes):
        return sorted((note["maturity_year"], note["principal_usd"], Decimal(str(note["coupon_percent"])))
                      for note in notes)

    if not rows or identifiers(rows) != identifiers(debt["tranches"]):
        return False
    maturity_dates = {row["maturity_year"]: row["matures_on"] for row in rows}
    return len(maturity_dates) == len(rows) and sum(row["principal_usd"] for row in rows) == debt["principal_usd"] and all(
        "matures_on" not in note or note["matures_on"] == maturity_dates[note["maturity_year"]]
        for note in debt["tranches"]
    )


def collect_oracle_debt_pricing(client, filings, offering: dict, *, as_of: date) -> dict:
    """Read issue-date yields and spreads after validating the closed note tranches."""
    if type(as_of) is not date:
        raise ValueError("Oracle debt pricing requires a valuation cutoff date.")
    result = {
        "status": "missing",
        "tranches": [],
        "benchmark": None,
        "missing": [],
        "limitations": [
            "The issue-date yields and spreads are dated financing evidence. Current market yields and the cost of debt may differ.",
            "The benchmark is the tranche whose underlying Treasury maturity is closest to ten years at issuance. "
            "This is a maturity comparison and does not estimate bond duration.",
        ],
    }
    debt = offering.get("debt")
    if offering.get("status") not in {"available", "partial"} or not debt:
        result["missing"].append("No verified closed Oracle note issue is available for pricing.")
        return result
    priced_on = date.fromisoformat(offering["priced_on"])
    closed_on = date.fromisoformat(offering["closed_on"])
    if not priced_on <= closed_on <= as_of:
        result["missing"].append("The verified Oracle note issue closed after the valuation cutoff.")
        return result

    matching = {}
    filings_by_accession = {
        row["accession"]: row for row in filings
        if row["form"] == "FWP" and priced_on.isoformat() <= row["filed_on"] <= closed_on.isoformat()
    }
    for accession, filing in filings_by_accession.items():
        try:
            tranches = _pricing_terms(_page(client, filing), offering, priced_on, closed_on)
            if not _matches_closed_debt(tranches, debt):
                raise ValueError("Pricing tranches do not match the closed debt offering.")
        except (OSError, ValueError) as exc:
            result["missing"].append(f"{accession}: {exc}")
            continue
        matching[accession] = (filing, tranches)

    if len(matching) != 1:
        result["missing"].append("No unique final pricing term sheet matched the closed Oracle note issue.")
        result["status"] = "conflicting" if matching else "missing"
        return result
    filing, tranches = next(iter(matching.values()))
    source = _source(filing)
    tranches = sorted(tranches, key=lambda row: row["matures_on"])
    target_year = priced_on.year + 10
    target = priced_on.replace(year=target_year, day=min(priced_on.day, monthrange(target_year, priced_on.month)[1]))
    distances = [(abs((date.fromisoformat(row["benchmark_treasury_matures_on"]) - target).days), row) for row in tranches]
    closest = min(distance for distance, _ in distances)
    benchmarks = [row for distance, row in distances if distance == closest]
    if len(benchmarks) != 1:
        result["missing"].append("No unique Treasury maturity was closest to a ten-year benchmark.")
    return {
        **result,
        "status": "available" if len(benchmarks) == 1 else "partial",
        "shelf": offering["shelf"],
        "currency": "USD",
        "priced_on": priced_on.isoformat(),
        "closed_on": closed_on.isoformat(),
        "source": source,
        "tranches": tranches,
        "benchmark": benchmarks[0] if len(benchmarks) == 1 else None,
    }

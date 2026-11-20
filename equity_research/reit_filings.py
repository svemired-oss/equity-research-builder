"""Source Realty Income's net-lease model from its filed operating supplement."""

from dataclasses import dataclass
from datetime import date
from math import isclose
import re
from urllib.parse import urljoin

from .gd_releases import _Page
from .operating_research import ResearchGap
from .reit_valuation import NetLeaseBasis
from .sec_client import filing_document_url, filing_index_url
from .sec_guidance import _ReleaseText


@dataclass(frozen=True)
class NetLeaseEvidence:
    date: date
    basis: NetLeaseBasis
    maturities: tuple[tuple[str, float], ...]
    source: str


_NUMBER = r"((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)"


def _match(text, regex, label):
    occurrences = set(re.findall(regex, text, re.I))
    if len(occurrences) != 1:
        raise ResearchGap(f"Realty Income's supplement did not uniquely disclose {label}.")
    return occurrences.pop()


def _value(text, prefix, label, *, scale=1):
    found = _match(text, prefix + r"\s*\$?\s*" + _NUMBER, label)
    return float(found.replace(",", ""))/scale


def _supplement(client, cik, accession):
    index = filing_index_url(cik, accession)
    candidates = set()
    for table in _Page(client.get_text(index)).tables:
        for row in table.rows:
            if len(row) == 5 and row[3].text.upper() == "EX-99.2" and len(row[2].links) == 1:
                url = urljoin(index, row[2].links[0])
                filename = url.rsplit("/", 1)[-1]
                if re.fullmatch(r"[A-Za-z0-9_.-]+\.html?", filename) and url == filing_document_url(cik, accession, filename):
                    candidates.add(url)
    if len(candidates) != 1:
        raise ResearchGap("There is no uniquely identified Realty Income SEC supplemental exhibit.")
    return candidates.pop()


def prepare_realty_income(packet, client, *, renewal_fraction=.90):
    """Match the pro-rata rent, cash, debt and common-unit scope at quarter end."""
    cik = packet["issuer"]["cik"]
    if cik != "0000726728":
        raise ResearchGap("The REIT needs a reviewed ownership and lease-disclosure mapping.")
    interim = packet["interim_actuals"]["periods"][-1:]
    releases = packet["earnings"]["releases"]
    if not interim or not releases:
        raise ResearchGap("Current interim and supplemental REIT filings are required.")
    end = date.fromisoformat(interim[0]["period_end"])
    if (end.month, end.day) != (9, 30):
        raise ResearchGap("This REIT mapping requires a September quarter and its next-five-year lease schedule.")
    source = _supplement(client, cik, releases[0]["accession"])
    text = _ReleaseText(client.get_text(source)).blocks
    stamp = f"{end:%B} {end.day}, {end.year}"

    def block(*tokens):
        rows = [item for item in text if all(token in item for token in tokens)]
        if len(rows) != 1:
            raise ResearchGap("The REIT supplement is missing a uniquely scoped operating table.")
        return rows[0]

    expiry = block("LEASE EXPIRATIONS", stamp)
    rent = _value(expiry, r"Total\s+[\d,]+\s+[\d,]+", "annualized base rent", scale=1000)
    signed_leases = block("LEASING ACTIVITY", stamp)
    recapture = _value(signed_leases, r"Rent Recapture Rate\(1\)\s+\d+\.\d+\s*%\s+\d+\.\d+\s*%\s+"
                      r"\d+\.\d+\s*%\s+", "year-to-date incumbent rent recapture", scale=100)
    vacancy = _value(signed_leases, r"Average months vacant\s+\S+\s+\S+\s+\S+\s+\S+",
                     "year-to-date months vacant for new clients", scale=12)
    y = end.year
    expiring = []
    for year in range(y, y+6):
        raw = _match(expiry, rf"\b{year}\s+[\d,]+\s+[\d,]+\s+\$?\s*{_NUMBER}\s+{_NUMBER}(?:\s*%)?(?=\s)",
                     f"{year} expiring rent")
        expiring.append(float(raw[0].replace(",", ""))/1000)
    # Convert calendar-year maturities into five September-ending periods.
    buckets = tuple(((before if index == 0 else before/4)+after*.75)/rent
                    for index, (before, after) in enumerate(zip(expiring, expiring[1:])))
    if sum(buckets) > 1:
        raise ResearchGap("The lease maturity buckets exceed the pro-rata in-place rent.")

    noi = block("Annualized Pro Forma Cash NOI, Excluding Lease Termination Income", "Cash income on loans")
    annual_noi = _value(noi, r"Annualized Pro Forma Cash NOI, Excluding Lease Termination Income\(5\)",
                        "annualized pro-rata cash NOI", scale=1000)
    nonproperty = _value(noi, r"Plus: Cash income on loans and preferred equity investments", "credit investment cash flow", scale=1000)
    credit_pf = _value(noi, r"pro forma adjustments for loans and preferred equity investments\(4\)",
                       "pro forma credit income adjustment", scale=1000)
    property_noi = annual_noi-4*(nonproperty+credit_pf)

    credit = block("Credit Investments as of", stamp, "INVESTMENT IN PREFERRED EQUITY")
    loans = _value(credit, r"\bLoans\s+\d+\s+\$[\d,]+\s+", "credit portfolio loan principal")
    preferred = _value(credit, r"Preferred Equity\s+\d+\s+N/A", "preferred equity investments")
    cap = block("Capitalization as of", stamp, "Share(2)")
    cash = _value(cap, r"Cash and cash equivalents\(4\)", "pro-rata cash and cash equivalents")
    debt = _value(cap, r"Mortgages Payable\s+[\d,.]+\s+Subtotal", "pro-rata debt principal")
    shares = sum(_value(cap, pattern, label, scale=1000) for pattern, label in (
        (r"\bCommon Stock", "common shares"), (r"\bCommon Units\(1\)", "outside common units")))
    guidance = block("Revised", "Same store rent growth", "YTD Actuals")
    bands = _match(guidance, r"Same store rent growth\s+" + _NUMBER + r"%\s*[-–]\s*" + _NUMBER + r"%",
                   "same-store rent growth guidance range")
    rent_growth = sum(float(rate) for rate in bands)/200

    ffo = block("Recurring capital expenditures", "AFFO available to common stockholders", "AFFO per common share: Basic")
    costs = []
    for name in ("Leasing costs and commissions", "Recurring capital expenditures"):
        amount = _match(ffo, re.escape(name)+r"\s*\("+_NUMBER+r"\)", name)
        costs.append(float(amount.replace(",", ""))/1000)
    history = block("General and administrative", "Consolidated Statements of Income")
    overhead = _value(history, r"General and administrative", "consolidated G&A", scale=1000)
    taxes = _match(history, r"Income taxes\s+\("+_NUMBER+r"\)", "consolidated income taxes")
    operating_overhead = overhead+float(taxes.replace(",", ""))/1000

    schedule = block("Debt Maturities and Currency Summary as of", stamp)
    maturity_rows = []
    for label in (*map(str, range(y, y+5)), "Thereafter"):
        token = r"\$?\s*(?:[\d,.]+|\([\d,.]+\)|[—–�-])\s+"
        amount = _match(schedule, rf"\b{label}\s+"+token*9+r"\$?\s*"+_NUMBER+r"\s+\d+\.\d+",
                        f"{label} pro-rata maturity amount")
        maturity_rows.append((label, float(amount.replace(",", ""))))
    if not isclose(sum(amount for _, amount in maturity_rows), debt, rel_tol=0, abs_tol=.5):
        raise ResearchGap("Realty Income's disclosed maturity buckets do not reconcile to pro-rata debt.")

    result = NetLeaseBasis(rent, property_noi/rent, 4*sum(costs), 4*operating_overhead, rent_growth, buckets,
                           renewal_fraction, recapture, vacancy, cash, loans+preferred, debt, 0., shares,
                           "Pro-rata existing property portfolio and common units")
    return NetLeaseEvidence(end, result, tuple(maturity_rows), source)

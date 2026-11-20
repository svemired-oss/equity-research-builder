"""Normalize foreign annual filings without mixing statement currency or taxonomy."""

from dataclasses import dataclass
from datetime import date
from math import isclose
import re

from .filing_xbrl import load_filing_instance
from .gd_releases import _Page
from .operating_research import ResearchGap
from .sec_guidance import _ReleaseText


_IFRS = {
    "revenue": ("Revenue",),
    "operating_income": ("ProfitLossFromOperatingActivities",),
    "pretax_income": ("ProfitLossBeforeTax",),
    "net_income": ("ProfitLoss",),
    "operating_cash": ("CashFlowsFromUsedInOperatingActivities",),
    "investing_cash": ("CashFlowsFromUsedInInvestingActivities",),
    "financing_cash": ("CashFlowsFromUsedInFinancingActivities",),
    "capital_expenditures": (
        "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
        "PurchaseOfPropertyPlantAndEquipmentIntangibleAssetsOtherThanGoodwillInvestmentPropertyAndOtherNoncurrentAssets",
    ),
    "assets": ("Assets",), "liabilities": ("Liabilities",), "equity": ("Equity",),
    "cash": ("CashAndCashEquivalents",),
}
_GAAP = {
    "revenue": ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"),
    "operating_income": ("OperatingIncomeLoss",),
    "pretax_income": ("IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",),
    "net_income": ("ProfitLoss", "NetIncomeLoss"),
    "operating_cash": ("NetCashProvidedByUsedInOperatingActivities",),
    "investing_cash": ("NetCashProvidedByUsedInInvestingActivities",),
    "financing_cash": ("NetCashProvidedByUsedInFinancingActivities",),
    "capital_expenditures": ("PaymentsToAcquirePropertyPlantAndEquipment",),
    "assets": ("Assets",), "liabilities": ("Liabilities",),
    "equity": ("StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest", "StockholdersEquity"),
    "cash": ("CashAndCashEquivalentsAtCarryingValue",),
}
_INSTANTS = {"assets", "liabilities", "equity", "cash"}
_CORE = ("revenue", "net_income", "operating_cash", "investing_cash", "financing_cash", "assets", "liabilities", "equity")
_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
          "nine": 9, "ten": 10, "twenty": 20, "fifty": 50, "one-half": .5, "half": .5}
_AMOUNT = r"(?:\d+(?:/\d+|\.\d+)?|" + "|".join(sorted(_WORDS, key=len, reverse=True)) + r")"
_ADR = re.compile(
    r"(?:\beach\s+(?:ADR|ADS)\s+(?:currently\s+)?(?:represents?|equals?)\s+"
    r"|\b(?:American Depositary (?:Shares?|Receipts?)|ADSs?|ADRs?)(?:[\s,]+each)\s+"
    r"(?:representing|represents?|equal to|equals?)\s+)"
    rf"({_AMOUNT})\s+(?:of\s+)?(?:(?:an?|its|\w+['’]s)\s+)?(?:ordinary shares?|common shares?|shares? of)", re.I)


@dataclass(frozen=True)
class ForeignFinancials:
    taxonomy: str
    currency: str
    periods: tuple[dict, ...]
    listing: dict
    latest_revenue_drivers: dict | None = None
    latest_diluted_eps: float | None = None


def filing_share_ratio(html, ticker):
    """Recognize explicit depositary ratios and proved ordinary-share cover titles."""
    text = " ".join(_ReleaseText(html).blocks)
    cover_rows = [
        " ".join(cell.text for cell in row)
        for table in _Page(html).tables[:12]
        for row in table.rows
        if any(re.fullmatch(re.escape(ticker), cell.text, re.I) for cell in row)
    ]
    ratios = set()
    cover = " ".join(cover_rows)
    filed_ratios = tuple(_ADR.finditer(cover)) or tuple(_ADR.finditer(text))
    for matched in filed_ratios:
        amount = matched[1].lower()
        if amount in _WORDS:
            ratio = _WORDS[amount]
        elif "/" in amount:
            first, last = map(int, amount.split("/"))
            if not last:
                raise ResearchGap("The filed depositary ratio has an invalid denominator.")
            ratio = first/last
        else:
            ratio = float(amount)
        if ratio > 0:
            ratios.add(ratio)
    if len(ratios) > 1:
        raise ResearchGap("The filing contains conflicting depositary ratios.")
    if ratios:
        return {"kind": "depositary", "ordinary_shares_per_listed_unit": ratios.pop()}
    exchange = r"(?:The\s+)?(?:Nasdaq(?:\s+Stock Market(?:\s+LLC)?)?|New York Stock Exchange)"
    common = r"(?:Ordinary Shares|Common Shares(?:,\s*no par value)?)"
    if any(re.fullmatch(rf"{common}\s+{re.escape(ticker)}\s+{exchange}", " ".join(row.split()), re.I)
           for row in cover_rows):
        return {"kind": "ordinary", "ordinary_shares_per_listed_unit": 1}
    # Cover text may join adjacent inline display boxes. The DEI tags identify
    # the same displayed class and ticker before we accept that layout.
    plain_cover = text[:3500]
    ordinary = re.search(rf"\bOrdinary Shares\s*{re.escape(ticker)}\b\s+{exchange}", plain_cover, re.I)
    title_tag = r'''name=["']dei:Security12bTitle["'][^>]*>\s*Ordinary Shares\s*<'''
    ticker_tag = rf'''name=["']dei:TradingSymbol["'][^>]*>\s*{re.escape(ticker)}\s*<'''
    if ordinary and re.search(title_tag, html, re.I) and re.search(ticker_tag, html, re.I):
        return {"kind": "ordinary", "ordinary_shares_per_listed_unit": 1}
    return {"kind": "unverified", "ordinary_shares_per_listed_unit": None}


def _tag(concept):
    if concept.startswith("us-gaap:"):
        return "US GAAP", concept.removeprefix("us-gaap:")
    if re.match(r"^\{https?://xbrl\.ifrs\.org/taxonomy/[^}]+/ifrs-full\}", concept):
        return "IFRS", concept.rsplit("}", 1)[1]
    return None, None


def _annual_revenue(instance, report_date):
    names = {(name, tag) for name, fields in (("IFRS", _IFRS), ("US GAAP", _GAAP)) for tag in fields["revenue"]}
    return [fact for fact in instance.facts if fact.start is not None and not fact.dimensions
            and re.fullmatch(r"[A-Z]{3}", fact.unit) and 350 <= (fact.end-fact.start).days <= 378
            and fact.end <= report_date and _tag(fact.concept) in names]


def _precise_value(facts):
    if len({fact.value for fact in facts}) == 1:
        return facts[0].value
    precision = []
    for fact in facts:
        if fact.decimals == "INF":
            radius = 0.
        elif re.fullmatch(r"-?\d{1,2}", fact.decimals or ""):
            radius = .5 * 10. ** -int(fact.decimals)
        else:
            return None
        precision.append((radius, fact.value))
    _, value = min(precision)
    if max(value - radius for radius, value in precision) <= min(value + radius for radius, value in precision):
        return value
    return None


def _period(instance, *, start, end, taxonomy, currency):
    mapping = _IFRS if taxonomy == "IFRS" else _GAAP
    facts = {}
    for fact in instance.facts:
        if fact.end != end or fact.unit != currency or fact.dimensions or fact.start not in (None, start):
            continue
        family, tag = _tag(fact.concept)
        if family == taxonomy:
            facts.setdefault((fact.start, tag), []).append(fact)
    metrics, conflicts = {}, []
    for metric, tags in mapping.items():
        context_start = None if metric in _INSTANTS else start
        for tag in tags:
            values = facts.get((context_start, tag))
            if values:
                value = _precise_value(values)
                if value is None:
                    conflicts.append(metric)
                else:
                    metrics[metric] = value
                break
    return metrics, conflicts


def _sap_annual_drivers(instance, end, revenue):
    labels = {
        "Cloud": ("SAP", "CloudSubscriptionsAndSupportRevenue"),
        "Software and support": ("SAP", "SoftwareLicensesAndSupportRevenue"),
        "Software support": ("IFRS", "RevenueFromRenderingOfInformationTechnologyMaintenanceAndSupportServices"),
        "Services": ("IFRS", "RevenueFromRenderingOfInformationTechnologyConsultingServices"),
    }
    start = date(end.year, 1, 1)
    drivers = {}
    for label, (family, tag) in labels.items():
        concepts = []
        for fact in instance.facts:
            if (fact.start, fact.end, fact.unit, fact.dimensions) != (start, end, "EUR", ()):
                continue
            sap_tag = fact.concept.startswith("{http://www.sap.com/") and fact.concept.endswith("}" + tag)
            if (family == "SAP" and sap_tag) or _tag(fact.concept) == (family, tag):
                concepts.append(fact)
        amount = _precise_value(concepts) if concepts else None
        if amount is None or amount <= 0:
            return None
        drivers[label] = amount / 1e6
    drivers["Software licenses"] = drivers.pop("Software and support") - drivers["Software support"]
    if drivers["Software licenses"] <= 0 or abs(sum(drivers.values()) - revenue / 1e6) > 2:
        return None
    return drivers


def _haleon_annual_drivers(instance, end, revenue):
    from .haleon_filings import PRODUCTS

    start = date(end.year, 1, 1)
    rows = {member: [] for member in PRODUCTS.values()}
    for fact in instance.facts:
        if ((fact.start, fact.end, fact.unit) != (start, end, "GBP")
                or _tag(fact.concept) != ("IFRS", "Revenue") or len(fact.dimensions) != 1):
            continue
        axis, member = (tag.rsplit("}", 1)[-1] for tag in fact.dimensions[0])
        if axis == "ProductsAndServicesAxis" and member in rows:
            rows[member].append(fact)
    drivers = {}
    for label, member in PRODUCTS.items():
        value = _precise_value(rows[member]) if rows[member] else None
        if value is None or value <= 0:
            return None
        drivers[label] = value / 1e6
    return drivers if abs(sum(drivers.values()) - revenue / 1e6) <= 2 else None


def collect_foreign_financials(client, issuer, filings, *, years=3):
    """Use the latest eligible annual filing and its like-for-like comparisons first."""
    annual = sorted((row for row in filings if row["form"] in {"20-F", "20-F/A", "40-F", "40-F/A"} and row["period_end"]),
                    key=lambda row: (row["period_end"], row["filed_on"], row["accession"]), reverse=True)
    if not annual:
        raise ResearchGap("No SEC annual foreign-issuer filing is available.")
    selected = []
    for row in annual:
        if any(item["period_end"] == row["period_end"] for item in selected):
            continue
        selected.append(row)
        if len(selected) == years:
            break
    ticker, cik = issuer["ticker"], issuer["cik"]
    top = selected[0]
    current_end = date.fromisoformat(top["period_end"])
    latest = load_filing_instance(client, cik, top["accession"])
    candidates = {(_tag(fact.concept)[0], fact.unit, fact.start) for fact in _annual_revenue(latest,current_end)
                  if fact.end == current_end}
    # SEC reports can include convenience translations. Select only the
    # denomination that spans the audited balance sheet and cash flows.
    qualified = []
    for taxonomy, currency, start in candidates:
        metrics, conflicts = _period(latest,start=start,end=current_end,taxonomy=taxonomy,currency=currency)
        if all(key in metrics for key in _CORE) and not set(conflicts).intersection(_CORE):
            qualified.append((taxonomy,currency))
    if len(qualified) != 1:
        raise ResearchGap("No uniquely reconciled annual accounting basis and reporting currency was found.")
    taxonomy,currency = qualified[0]
    periods = {}
    for index,row in enumerate(selected):
        instance = latest if index==0 else load_filing_instance(client,cik,row["accession"])
        report_end = date.fromisoformat(row["period_end"])
        contexts = {(fact.start,fact.end) for fact in _annual_revenue(instance,report_end)
                    if _tag(fact.concept)[0]==taxonomy and fact.unit==currency}
        all_ends = sorted({end for _,end in contexts},reverse=True)[:years-index]
        for end in all_ends:
            starts = {start for start,day in contexts if day==end}
            if len(starts)!=1:
                raise ResearchGap(f"Multiple fiscal reporting durations were found for {end}.")
            start = starts.pop()
            metrics, conflicts = _period(instance,start=start,end=end,taxonomy=taxonomy,currency=currency)
            entry = periods.setdefault(end,{"period_start":start.isoformat(),"period_end":end.isoformat(),
                                            "metrics":{},"sources":{},"conflicts":[]})
            for key,value in metrics.items():
                if key not in entry["metrics"] and key not in entry["conflicts"]:
                    entry["metrics"][key] = value
                    entry["sources"][key] = {"url":row["url"],"filed_on":row["filed_on"],
                                              "accession":row["accession"],"unit":currency}
            entry["conflicts"].extend(key for key in conflicts if key not in entry["metrics"])
    output = []
    for _,entry in sorted(periods.items())[-years:]:
        m = entry["metrics"]
        entry["missing"] = [key for key in (_IFRS if taxonomy=="IFRS" else _GAAP) if key not in m]
        if all(key in m for key in ("assets","liabilities","equity")):
            entry["balance_reconciled"] = isclose(m["assets"],m["liabilities"]+m["equity"],rel_tol=0,
                                                  abs_tol=min(2e6,max(1000,m["assets"]*.0001)))
        else:
            entry["balance_reconciled"] = False
        output.append(entry)
    if not output or not output[-1]["balance_reconciled"]:
        raise ResearchGap("The latest foreign balance sheet did not reconcile in its reporting currency.")
    listing = filing_share_ratio(client.get_text(top["url"]),ticker)
    drivers = (_sap_annual_drivers(latest, current_end, output[-1]["metrics"]["revenue"])
               if cik == "0001000184" and taxonomy == "IFRS" and currency == "EUR" else None)
    if cik == "0001900304" and taxonomy == "IFRS" and currency == "GBP":
        drivers = _haleon_annual_drivers(latest, current_end, output[-1]["metrics"]["revenue"])
    eps_facts = [fact for fact in latest.facts if fact.end == current_end and not fact.dimensions
                 and fact.start and 350 <= (fact.end - fact.start).days <= 378
                 and fact.unit == f"{currency}/shares"
                 and _tag(fact.concept) in {("IFRS", "DilutedEarningsLossPerShare"),
                                           ("US GAAP", "EarningsPerShareDiluted")}]
    eps = _precise_value(eps_facts) if eps_facts else None
    return ForeignFinancials(taxonomy, currency, tuple(output), {**listing, "source": top["url"]}, drivers, eps)

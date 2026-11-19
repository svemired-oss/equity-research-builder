"""Map Waste Management's external revenue on comparable post-acquisition periods."""

from datetime import date
import re

from .gd_releases import _Page
from .operating_research import ResearchGap


_LINES = {
    "Total Collection and Disposal": "CollectionAndDisposalMember",
    "Recycling Processing and Sales": "RecyclingProcessingAndSalesMember",
    "Renewable Energy": "RenewableEnergySegmentMember",
    "Healthcare Solutions": "HealthcareSolutionsMember",
    "Corporate and Other": "ServiceOtherMember",
}


def _interim_sales(document, current_year, expected):
    matches = set()
    for table in _Page(document).tables:
        rows = [tuple(cell.text.replace("\u200b", "").strip() for cell in row) for row in table.rows]
        rows = [tuple(value for value in row if value and value != "$") for row in rows]
        text = " ".join(value for row in rows[:8] for value in row)
        if not re.search(r"Net\s*Operating\s*Revenues", text, re.I) or not re.search(
                r"Nine Months Ended September\s*30", text, re.I):
            continue
        values = {}
        year = None
        for row in rows:
            if not row:
                continue
            if len(row) == 1 and row[0] in (str(current_year - 1), str(current_year)):
                year = int(row[0])
                values.setdefault(year, {})
            elif year is not None and row[0] in (*_LINES, "Total") and len(row) >= 4:
                if all(re.fullmatch(r"[\d,]+", item) for item in row[1:4]):
                    external, internal, gross = (int(value.replace(",", "")) for value in row[1:4])
                    if gross == external + internal:
                        values[year].setdefault(row[0], set()).add(external)
        if not all(year in values and set(values[year]) == {*_LINES, "Total"}
                   for year in (current_year - 1, current_year)):
            continue
        if any(len(items) != 1 for period in values.values() for items in period.values()):
            continue
        pair = tuple(tuple(next(iter(values[year][label])) for label in (*_LINES, "Total"))
                     for year in (current_year - 1, current_year))
        if all(sum(amounts[:-1]) == amounts[-1] == target for amounts, target in zip(pair, expected, strict=True)):
            matches.add(pair)
    if len(matches) != 1:
        raise ResearchGap("Waste Management's net sales by business line did not reconcile for both September YTD periods.")
    return tuple(dict(zip(_LINES.values(), amounts[:-1], strict=True)) for amounts in matches.pop())


def waste_management_candidates(packet, client):
    annual = packet["actuals"]["periods"]
    if len(annual) < 3:
        raise ResearchGap("Three audited Waste Management annual periods are required.")
    facts = packet["operating_facts"]["observations"]
    historical = []
    for period in annual[-2:]:
        amounts = {key:set() for key in _LINES.values()}
        for fact in facts:
            dimensions = fact["dimensions"]
            if (fact["metric"] != "product_or_service_revenue" or len(dimensions) != 1
                    or fact["unit"] != "USD" or fact["period_end"] != period["period_end"]
                    or fact["accession"] != annual[-1]["filing"]["accession"]):
                continue
            key = dimensions[0]["member"].rsplit("}", 1)[-1].rsplit(":", 1)[-1]
            if key in amounts:
                amounts[key].add(fact["value"] / 1e6)
        if any(len(values) != 1 for values in amounts.values()):
            raise ResearchGap("The annual filing did not uniquely report each net Waste Management business line.")
        lines = {key:values.pop() for key, values in amounts.items()}
        if sum(lines.values()) != period["metrics"]["revenue"] / 1e6:
            raise ResearchGap("Waste Management's annual external sales do not match consolidated revenue.")
        historical.append(lines)
    flows = packet["earnings_interim"]
    if not flows["current"] or not flows["prior"]:
        raise ResearchGap("Waste Management requires two comparable YTD periods after the Stericycle acquisition.")
    prior, current = flows["prior"][-1], flows["current"][-1]
    end = date.fromisoformat(current["period_end"])
    prior_end = date.fromisoformat(prior["period_end"])
    if (end.month, end.day) != (9, 30) or prior_end != end.replace(year=end.year - 1):
        raise ResearchGap("Matched September filings are required for the post-acquisition revenue comparison.")
    if date.fromisoformat(prior["period_start"]) <= date(2024, 11, 4):
        raise ResearchGap("The prior comparative predates Waste Management's Stericycle acquisition.")
    source = current["filing"]["url"]
    expected = tuple(period["metrics"]["revenue"] / 1e6 for period in (prior, current))
    ytd_prior, ytd_current = _interim_sales(client.get_text(source), end.year, expected)
    if any(value <= 0 for row in (ytd_prior, ytd_current) for value in row.values()):
        raise ResearchGap("Waste Management requires positive revenue for every modeled business line.")
    return {"product_or_service_revenue": {"prior": historical[0], "latest": historical[1],
        "comparable_interim": {"prior": ytd_prior, "current": ytd_current, "source": source,
            "note": "Forecast growth uses matched September year-to-date external sales after the Stericycle acquisition. "
                    "The annual history retains the reported partial-year acquisition effects."}}}

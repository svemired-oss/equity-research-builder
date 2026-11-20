"""Read SAP's filed consolidated interim statements with explicit IFRS checks."""

from datetime import date
import re
from urllib.parse import parse_qs, urljoin, urlsplit

from .gd_releases import _Page
from .operating_research import ResearchGap
from .sec_client import filing_document_url


_DRIVERS = ("Cloud", "Software licenses", "Software support", "Services")
_INCOME = {
    "revenue": "Total revenue",
    "operating_income": "Operating profit (loss)",
    "pretax_income": "Profit (loss) before tax",
    "net_income": "Profit (loss) after tax",
}
_BALANCE = {"assets": "Total assets", "liabilities": "Total liabilities", "equity": "Total equity",
            "cash": "Cash and cash equivalents"}
_CASH = {
    "operating_cash": "Net cash flows from operating activities",
    "investing_cash": "Net cash flows from investing activities",
    "financing_cash": "Net cash flows from financing activities",
    "capital_expenditures": "Purchase of intangible assets and property, plant, and equipment",
    "cash_fx": "Effect of foreign currency rates on cash and cash equivalents",
    "cash_change": "Net increase/decrease in cash and cash equivalents",
    "opening_cash": "Cash and cash equivalents at the beginning of the period",
    "closing_cash": "Cash and cash equivalents at the end of the period",
    "depreciation": "Depreciation and amortization",
}


def _candidates(client, row, *, cik="0001000184", exhibit="EX-99.1", extension=r"html?"):
    for table in _Page(client.get_text(row["index_url"])).tables:
        for cells in table.rows:
            if len(cells) != 5 or cells[3].text.upper() != exhibit or len(cells[2].links) != 1:
                continue
            url = urljoin(row["index_url"], cells[2].links[0])
            parsed = urlsplit(url)
            if parsed.netloc == "www.sec.gov" and parsed.path in {"/ix", "/ixviewer/doc/action"}:
                target = parse_qs(parsed.query).get("doc", ())
                if len(target) == 1 and target[0].startswith("/Archives/edgar/data/"):
                    url = urljoin(row["index_url"], target[0])
            filename = url.rsplit("/", 1)[-1]
            if re.fullmatch(rf"[A-Za-z0-9_.-]+\.{extension}", filename):
                if url == filing_document_url(cik, row["accession"], filename):
                    yield url


def _table(tables, labels, predicate):
    matches = [table for table in tables if predicate(table) and
               set(labels) <= {row[0] for row in table}]
    if len(matches) != 1:
        raise ResearchGap("The filing did not provide a unique set of consolidated IFRS statements.")
    values = {}
    for row in matches[0]:
        if row[0] in labels:
            if row[0] in values or len(row) < 3:
                raise ResearchGap(f"The interim IFRS row for {row[0]} is ambiguous.")
            try:
                values[row[0]] = tuple(int(value.replace(",", "").replace("–", "-").replace("−", "-"))
                                       for value in row[1:3])
            except ValueError as exc:
                raise ResearchGap(f"The interim IFRS row for {row[0]} is not numeric.") from exc
    return values


def _sap_capital(tables, stamp, text, current):
    labels = ("Financial liabilities", "Other financial assets", "Trade and other receivables",
              "Trade and other payables", "Contract liabilities", "Non-controlling interests",
              "Intangible assets", "Property, plant, and equipment")
    matches = [table for table in tables if table and re.fullmatch(stamp, table[0][0], re.I)]
    if len(matches) != 1:
        return None
    values = {label: [] for label in labels}
    for row in matches[0]:
        if row[0] in values:
            if len(row) < 3 or any(not re.fullmatch(r"[\d,]+", x) for x in row[1:3]):
                return None
            values[row[0]].append(int(row[1].replace(",", "")))
    if any(len(values[x]) != (2 if i < 5 else 1) for i, x in enumerate(labels)):
        return None
    shares = set(re.findall(
        rf"For the three months ended September 30, {current} and {current - 1}, the weighted average "
        r"number of shares was [\d,]+ million \(diluted:? ([\d,]+) million\)", text))
    if len(shares) != 1:
        return None
    financing = _table(tables, ("Finance costs",), lambda table:
                       table and table[0][:3] == ["€ millions, unless otherwise stated", f"Q1–Q3 {current}",
                                                  f"Q1–Q3 {current - 1}"]
                       and len(table) > 1 and table[1][0] == "Cloud")
    debt, investments, receivables, payables, contract, nci, intangibles, ppe = (
        sum(values[label]) for label in labels)
    return {"financial_liabilities": debt, "other_financial_assets": investments,
            "receivables": receivables, "payables": payables, "contract_liabilities": contract,
            "minority": nci, "ppe_intangibles": intangibles + ppe,
            "diluted_shares": int(shares.pop().replace(",", "")), "finance_cost_ytd": -financing["Finance costs"][0]}


def read_sap_interim(html, filed_on, source):
    """Read actuals and drivers from SAP's Q1–Q3 EUR filing exhibit."""
    page = _Page(html)
    tables = [[row for cells in table.rows if (row := [cell.text for cell in cells if cell.text])]
              for table in page.tables]
    stamp = r"as at 9/30/(20\d{2}) and 12/31/(20\d{2})"
    years = {match.groups() for table in tables if table and (match := re.fullmatch(stamp, table[0][0], re.I))}
    if len(years) != 1:
        raise ResearchGap("The exhibit does not declare one current and prior SAP balance date.")
    current, prior = map(int, years.pop())
    end = date(current, 9, 30)
    if prior != current - 1 or not 0 <= (filed_on - end).days <= 90:
        raise ResearchGap("The SAP interim disclosure is inconsistent with the filing date.")
    heading = [f"Q1–Q3 {current}", f"Q1–Q3 {prior}"]
    income = _table(tables, (*_DRIVERS, *_INCOME.values()), lambda table:
                    table and table[0][:3] == ["€ millions, unless otherwise stated", *heading]
                    and len(table) > 1 and table[1][0] == "Cloud")
    balance = _table(tables, _BALANCE.values(), lambda table:
                     len(table) > 1 and re.fullmatch(stamp, table[0][0], re.I)
                     and table[1][:3] == ["€ millions", str(current), str(prior)])
    cash = _table(tables, _CASH.values(), lambda table:
                  table and table[0][:3] == ["€ millions", *heading])
    periods = []
    for index, year in enumerate((current, prior)):
        flows = {key: cash[label][index] for key, label in _CASH.items()}
        sales = {key: income[label][index] for key, label in _INCOME.items()}
        balances = {key: balance[label][index] for key, label in _BALANCE.items()}
        drivers = {label: income[label][index] for label in _DRIVERS}
        if (abs(sum(drivers.values()) - sales["revenue"]) > 2
                or abs(balances["assets"] - balances["liabilities"] - balances["equity"]) > 2
                or abs(flows["cash_change"] - sum(flows[key] for key in
                       ("operating_cash", "investing_cash", "financing_cash", "cash_fx"))) > 2
                or abs(flows["closing_cash"] - flows["opening_cash"] - flows["cash_change"]) > 2):
            raise ResearchGap("The SAP interim statements exceed the filed EUR millions rounding tolerance.")
        # The comparative balance sheet is prior December. The comparative flows end in September.
        expected_cash = balances["cash"] if index == 0 else periods[0]["cash_flow"]["opening_cash"]
        observed_cash = flows["closing_cash"] if index == 0 else balances["cash"]
        if abs(observed_cash - expected_cash) > 2 or flows["capital_expenditures"] >= 0:
            raise ResearchGap("SAP interim cash or capex could not be reconciled to its reported scope.")
        periods.append({"flow_end": date(year, 9, 30).isoformat(),
                        "balance_end": (end if index == 0 else date(prior, 12, 31)).isoformat(),
                        "income": sales, "balance": balances, "cash_flow": flows, "revenue_drivers": drivers})
    return {"status": "available", "currency": "EUR", "scale": "millions", "accounting": "IFRS",
            "filed_on": filed_on.isoformat(), "source": source, "periods": periods,
            "capital": _sap_capital(tables, stamp, page.text, current)}


def collect_foreign_interim(client, ticker_info, reports, annual_end):
    if ticker_info["cik"] != "0001000184":
        return {"status": "unreviewed"}
    for row in reports:
        filed_on = date.fromisoformat(row["filed_on"])
        if filed_on <= annual_end:
            break
        if filed_on.month not in (10, 11, 12):
            continue
        for source in set(_candidates(client, row)):
            try:
                result = read_sap_interim(client.get_text(source), filed_on, source)
            except ResearchGap:
                continue
            if date.fromisoformat(result["periods"][0]["flow_end"]) > annual_end:
                return result
    return {"status": "missing", "reason": "No reconciled SAP Q3 IFRS exhibit follows the latest annual statements."}


def sap_revenue_scenario(annual_end, annual_drivers, interim):
    """Apply reported YTD growth to each comparable prior-year fourth quarter."""
    if not annual_drivers or interim["status"] != "available":
        return None
    latest, previous = interim["periods"]
    if previous["balance_end"] != annual_end.isoformat():
        return None
    current_drivers, prior_drivers = latest["revenue_drivers"], previous["revenue_drivers"]
    rows = []
    for label in _DRIVERS:
        now, then = current_drivers[label], prior_drivers[label]
        historical_q4 = annual_drivers[label] - then
        if min(now, then, historical_q4) <= 0:
            return None
        growth = now / then - 1
        forecast_q4 = historical_q4 * (1 + growth)
        rows.append({"label": label, "prior_q4": historical_q4, "ytd_current": now, "ytd_comparative": then,
                     "growth_ratio": growth, "forecast_q4": forecast_q4, "forecast_full_year": now + forecast_q4})
    return {"period_end": date(annual_end.year + 1, 12, 31).isoformat(), "drivers": rows,
            "currency": "EUR", "scale": "millions", "total_revenue": sum(row["forecast_full_year"] for row in rows),
            "basis": "Reported year-to-date growth applied to prior-year Q4 revenue for each line.",
            "source": interim["source"]}

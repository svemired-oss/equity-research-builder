"""Read Haleon's reporting-currency results from its consolidated SEC exhibits."""

from datetime import date
from io import BytesIO
import re

from .foreign_interim import _candidates
from .gd_releases import _Page
from .operating_research import ResearchGap


CIK = "0001900304"
PRODUCTS = {
    "Oral Health": "OralHealthMember",
    "Vitamins, Minerals and Supplements": "VitaminsMineralsAndSupplementsMember",
    "Pain Relief": "PainReliefMember",
    "Respiratory Health": "RespiratoryHealthMember",
    "Digestive Health": "DigestiveHealthMember",
    "Therapeutic Skin Health and Other": "TherapeuticSkinHealthAndOtherMember",
}


def _number(value):
    value = value.replace(",", "")
    if value in {"-", "—", "–"}:
        return 0
    if not re.fullmatch(r"-?\d+|\(\d+\)", value):
        raise ResearchGap(f"Unexpected input in Haleon's financial tables: {value!r}.")
    return -int(value[1:-1]) if value.startswith("(") else int(value)


def _one_table(tables, header, labels):
    choices = [table for table in tables if table[:len(header)] == header
               and set(labels) <= {row[0] for row in table}]
    if len(choices) != 1:
        raise ResearchGap("The Haleon exhibit did not provide one matching set of IFRS tables.")
    values = {}
    for row in choices[0]:
        if row[0] in labels:
            pair = tuple(_number(x) for x in row[-2:])
            if row[0] in values and values[row[0]] != pair:
                raise ResearchGap(f"The Haleon exhibit reported conflicting values for {row[0]}.")
            values[row[0]] = pair
    return values


def read_haleon_half_year(html, filed_on, source):
    year = filed_on.year
    end = date(year, 6, 30)
    if not 0 <= (filed_on - end).days <= 60:
        raise ResearchGap("The Haleon half-year filing date differs from its expected reporting window.")
    tables = []
    page = _Page(html)
    for table in page.tables:
        rows = ([cell.text.replace("\u200b", "").strip() for cell in row] for row in table.rows)
        tables.append([present for row in rows if (present := [value for value in row if value])])
    years = [str(year), str(year - 1)]
    flow_header = [years, ["Notes", "£m", "£m"]]
    income = _one_table(tables, flow_header, ("Revenue", "Operating profit", "Profit before tax",
                                             "Profit after tax for the period", "Finance expense"))
    bs = _one_table(tables, [[f"30 June{year}", f"31 December{year - 1}"], ["Notes", "£m", "£m"]],
                    ("Total assets", "Total liabilities", "Total equity", "Cash and cash equivalents", "Inventories",
                     "Trade and other receivables", "Trade and other payables", "Short-term borrowings",
                     "Long-term borrowings", "Non-controlling interests", "Property, plant and equipment",
                     "Right of use assets", "Intangible assets", "Other investments"))
    cash = _one_table(tables, flow_header, (
        "Net cash inflow from operating activities", "Net cash outflow from investing activities",
        "Net cash outflow from financing activities", "Purchase of property, plant and equipment",
        "Purchase of intangible assets", "Depreciation of property, plant and equipment and right of use assets",
        "Amortisation of intangible assets", "Exchange adjustments", "Overdrafts",
        "Cash and cash equivalents and bank overdrafts at the beginning of the period",
        "Cash and cash equivalents and bank overdrafts at the end of the period"))
    sales = _one_table(tables, [["Six months ended 30 June"], years, ["Revenue by product category", "£m", "£m"]],
                       (*PRODUCTS, "Group revenue"))
    shares = _one_table(tables, [["Six months ended 30 June"], years], ("Dilutive weighted average number of shares (million)",))
    for index in (0, 1):
        if abs(bs["Total assets"][index] + bs["Total liabilities"][index] - bs["Total equity"][index]) > 2:
            raise ResearchGap("Haleon's IFRS balance sheet did not reconcile.")
        if abs(sum(sales[label][index] for label in PRODUCTS) - income["Revenue"][index]) > 2:
            raise ResearchGap("Haleon's product sales did not reconcile to reported revenue.")
        roll = sum(cash[label][index] for label in ("Net cash inflow from operating activities",
                   "Net cash outflow from investing activities", "Net cash outflow from financing activities",
                   "Cash and cash equivalents and bank overdrafts at the beginning of the period", "Exchange adjustments"))
        if abs(roll - cash["Cash and cash equivalents and bank overdrafts at the end of the period"][index]) > 2:
            raise ResearchGap("Haleon's statement of cash flows did not reconcile.")
    opening = {"balance_end": end.isoformat(), "flow_end": end.isoformat(),
               "balance": {"assets": bs["Total assets"][0], "liabilities": -bs["Total liabilities"][0],
                           "equity": bs["Total equity"][0], "cash": bs["Cash and cash equivalents"][0]},
               "income": {"revenue": income["Revenue"][0], "operating_income": income["Operating profit"][0],
                          "pretax_income": income["Profit before tax"][0], "net_income": income["Profit after tax for the period"][0]},
               "cash_flow": {
                   "depreciation": cash["Depreciation of property, plant and equipment and right of use assets"][0]
                                   + cash["Amortisation of intangible assets"][0],
                   "capital_expenditures": cash["Purchase of property, plant and equipment"][0]
                                           + cash["Purchase of intangible assets"][0]}}
    if abs(opening["balance"]["cash"] + cash["Overdrafts"][0]
           - cash["Cash and cash equivalents and bank overdrafts at the end of the period"][0]) > 2:
        raise ResearchGap("Haleon's cash and overdraft scopes did not reconcile.")
    capital = {"financial_liabilities": -bs["Short-term borrowings"][0] - bs["Long-term borrowings"][0],
               "other_financial_assets": bs["Other investments"][0],
               "receivables": bs["Trade and other receivables"][0] + bs["Inventories"][0],
               "payables": -bs["Trade and other payables"][0], "contract_liabilities": 0,
               "minority": bs["Non-controlling interests"][0],
               "ppe_intangibles": sum(bs[label][0] for label in ("Property, plant and equipment", "Right of use assets", "Intangible assets")),
               "diluted_shares": shares["Dilutive weighted average number of shares (million)"][0],
               "finance_cost_ytd": -income["Finance expense"][0]}
    capex_guidance = re.search(r"capital expenditure is expected to increase to c\.\s*(\d+(?:\.\d+)?)% "
                               r"of revenue over the next three to five years", page.text, re.I)
    return {"status": "available", "currency": "GBP", "scale": "millions", "accounting": "IFRS", "months": 6,
            "filed_on": filed_on.isoformat(), "source": source, "periods": [opening], "capital": capital,
            "product_sales": {key: sales[key] for key in PRODUCTS},
            "capex_target": float(capex_guidance[1]) / 100 if capex_guidance else None}


def read_haleon_q3(data, filed_on):
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    year = filed_on.year
    if not 0 <= (filed_on - date(year, 9, 30)).days <= 65:
        raise ResearchGap("The Haleon trading update falls outside the third-quarter reporting window.")
    try:
        pdf = PdfReader(BytesIO(data))
        pages = [p.extract_text(extraction_mode="layout") for p in pdf.pages[:20]]
    except (PdfReadError, ValueError, TypeError, OSError) as exc:
        raise ResearchGap("The SEC trading update PDF could not be read.") from exc
    if not pages or not re.search(rf"(?:Q3\s+{year}|{year}\s+Q3)\s*Trading Statement", pages[0]):
        raise ResearchGap("The SEC exhibit does not identify the current Haleon trading statement.")
    labels = {("VMS" if text.startswith("Vitamins") else text):text for text in PRODUCTS}
    labels["Group revenue"] = "Group revenue"
    candidate = []
    for page in pages:
        values = {}
        for short, label in labels.items():
            found = re.findall(rf"^\s*{re.escape(short)}\s+([\d,]+)\s+([\d,]+)\s+\(?[\d.]+\)?%",
                               page, re.MULTILINE)
            if len(found) == 1:
                values[label] = tuple(_number(item) for item in found[0])
        if len(values) == len(labels):
            candidate.append(values)
    if len(candidate) != 1:
        raise ResearchGap("The Haleon third-quarter exhibit did not uniquely disclose two years of product sales.")
    sales = candidate[0]
    if any(abs(sum(sales[key][i] for key in PRODUCTS) - sales["Group revenue"][i]) > 2 for i in (0, 1)):
        raise ResearchGap("Haleon's third-quarter product revenue did not reconcile.")
    return sales


def collect_haleon_interim(client, reports, annual_end, annual_drivers):
    """Require the detailed half-year filing and incorporate the subsequent trading update."""
    if not annual_drivers:
        return {"status": "missing"}, None
    year = annual_end.year + 1
    half = None
    q3 = None
    for row in reports:
        filed = date.fromisoformat(row["filed_on"])
        if filed.year != year:
            continue
        if not half and filed.month in (7, 8) and re.search(r"/hln-[^/]+x6k\.htm$", row["url"], re.I):
            for source in set(_candidates(client, row, cik=CIK)):
                try:
                    half = read_haleon_half_year(client.get_text(source), filed, source)
                    break
                except ResearchGap:
                    continue
        if not q3 and filed.month in (10, 11) and row.get("items", "") == "":
            for source in set(_candidates(client, row, cik=CIK, exhibit="EX-99.2", extension="pdf")):
                if not re.search(r"/q3[^/]*pressrelease[^/]*\.pdf$", source, re.I):
                    continue
                try:
                    q3 = read_haleon_q3(client.get_bytes(source), filed), source
                    break
                except ResearchGap:
                    continue
        if half and q3:
            break
    if not half or not q3:
        return half or {"status": "missing"}, None
    sales, source = q3
    groups = (("Oral Health", ("Oral Health",)), ("Vitamins, minerals and supplements", (tuple(PRODUCTS)[1],)),
              ("Pain relief", ("Pain Relief",)), ("Respiratory, digestive and skin health", tuple(PRODUCTS)[3:]))
    drivers = []
    for name, members in groups:
        now = sum(half["product_sales"][key][0] + sales[key][0] for key in members)
        then = sum(half["product_sales"][key][1] + sales[key][1] for key in members)
        prior_q4 = sum(annual_drivers[key] for key in members) - then
        if min(now, then, prior_q4) <= 0:
            raise ResearchGap("Haleon's year-to-date revenue did not support a prior-quarter comparison.")
        growth = now / then - 1
        drivers.append({"label": name, "prior_q4": prior_q4, "ytd_current": now, "ytd_comparative": then,
                        "growth_ratio": growth, "forecast_q4": prior_q4 * (1 + growth),
                        "forecast_full_year": now + prior_q4 * (1 + growth)})
    scenario = {"period_end": date(year, 12, 31).isoformat(), "drivers": drivers, "currency": "GBP", "scale": "millions",
                "total_revenue": sum(row["forecast_full_year"] for row in drivers), "source": source}
    if half["capex_target"] is not None:
        scenario["capex_target"] = half["capex_target"]
    return half, scenario

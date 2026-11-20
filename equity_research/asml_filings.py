"""Read ASML's matching US GAAP interim exhibits and management sales ranges."""

from datetime import date
from math import isclose
import re

from .foreign_interim import _candidates
from .gd_releases import _Page
from .operating_research import ResearchGap


_CIK = "0000937966"
_AMOUNT = r"(?:\([\d,]+\.\d+\)|[\d,]+\.\d+|—|–|-)"
_INCOME = {"revenue": "Total net sales", "systems": "Net system sales",
           "service": "Net service and field option sales", "operating_income": "Income from operations",
           "pretax_income": "Income before income taxes", "income_after_taxes": "Income after income taxes"}
_BALANCE = {"cash": "Cash and cash equivalents", "short_term_investments": "Short-term investments",
            "long_term_borrowing": "Long-term debt", "assets": "Total assets",
            "liabilities": "Total liabilities", "equity": "Total shareholders’ equity",
            "trade_receivables": "Accounts receivable, net", "inventory": "Inventories, net"}
_CASH = {"depreciation": "Depreciation and amortization",
         "operating_cash": "Net cash provided by (used in) operating activities",
         "investing_cash": "Net cash provided by (used in) investing activities",
         "financing_cash": "Net cash provided by (used in) financing activities",
         "equipment_capex": "Purchase of property, plant and equipment",
         "intangible_capex": "Purchase of intangible assets",
         "cash_fx": "Effect of changes in exchange rates on cash",
         "cash_change": "Net increase (decrease) in cash and cash equivalents",
         "opening_cash": "Cash and cash equivalents at beginning of the period",
         "closing_cash": "Cash and cash equivalents at end of the period"}
_LIMIT = ("The US GAAP interim exhibit groups current liabilities. The valuation needs separately verified current "
          "borrowings and operating liabilities, plus a comparable-company valuation.")


def _row(text, label, columns):
    columns_pattern = r"\s+".join([f"({_AMOUNT})"] * columns)
    matches = re.findall(rf"(?:^|\s){re.escape(label)}\s+{columns_pattern}(?![.,\d])", text)
    if len(matches) != 1:
        raise ResearchGap(f"ASML's interim exhibit did not uniquely identify {label}.")
    return tuple(0. if value in {"—", "–", "-"} else float(value.replace(",", "").replace("(", "-").rstrip(")"))
                 for value in matches[0])


def _scope(text, marker):
    start = text.partition(marker)[2]
    if not start or "Summary US GAAP Quarterly Summary" not in start:
        raise ResearchGap("ASML's interim statements have an unexpected layout.")
    return start.split("Summary US GAAP Quarterly Summary", 1)[0]


def read_asml_interim(document, guidance, *, balance_end, filed_on, sources):
    text = _Page(document).text
    release = _Page(guidance).text
    year = balance_end.year
    if (balance_end.month != 9 or not 0 <= (filed_on - balance_end).days <= 60
            or not re.search(rf"ASML Financial statements US GAAP Q3 {year}\b", text, re.I)):
        raise ResearchGap("The ASML statement date or reporting framework could not be verified.")
    inc = _scope(text, "Three months ended Nine months ended ")
    if not re.match(rf"Sep \d{{1,2}}, Sep {balance_end.day}, Sep \d{{1,2}}, Sep {balance_end.day}, "
                    rf"\(Unaudited, €, in millions, except per share data\) {year-1} {year} {year-1} {year} ", inc):
        raise ResearchGap("The ASML nine-month columns do not match the dated SEC filing.")
    balances = _scope(text, "Assets Cash and cash equivalents ")
    balances = "Cash and cash equivalents " + balances
    flows = _scope(text, "Summary US GAAP Consolidated Balance Sheets Cash flows from operating activities ")
    annual_balance, interim_balance = {}, {}
    for key, label in _BALANCE.items():
        annual_balance[key], interim_balance[key] = _row(balances, label, 2)
    current_flows = {key: _row(flows, label, 4)[3] for key, label in _CASH.items()}
    current_flows["capital_expenditures"] = current_flows["equipment_capex"] + current_flows["intangible_capex"]
    previous, current = {}, {}
    for key, label in _INCOME.items():
        _, _, previous[key], current[key] = _row(inc, label, 4)
    close = current_flows["closing_cash"]
    movement = sum(current_flows[key] for key in ("operating_cash", "investing_cash", "financing_cash", "cash_fx"))
    pairs = [(current_flows["opening_cash"], annual_balance["cash"]), (close, interim_balance["cash"]),
             (close-current_flows["opening_cash"], movement), (movement, current_flows["cash_change"])]
    pairs += [(row["systems"] + row["service"], row["revenue"]) for row in (previous, current)]
    pairs += [(row["assets"] - row["liabilities"], row["equity"]) for row in (annual_balance, interim_balance)]
    if current_flows["capital_expenditures"] >= 0 or any(not isclose(a, b, rel_tol=0, abs_tol=.25) for a, b in pairs):
        raise ResearchGap("ASML's balance sheet, sales mix or cash flows did not reconcile.")
    fy = re.search(rf"We expect total net sales for {year} to be between €([\d.]+) billion and €([\d.]+) billion",
                   release, re.I)
    q4 = re.search(rf"Q4 {year} total net sales between €([\d.]+) billion and €([\d.]+) billion", release, re.I)
    if not fy or not q4:
        raise ResearchGap("ASML's annual and fourth-quarter management sales ranges could not be verified.")
    full, stub = (tuple(float(amount)*1000 for amount in match.groups()) for match in (fy, q4))
    if not 0 < stub[0] <= stub[1] or not full[0] <= full[1] or (
            full[0] > current["revenue"] + stub[1] or full[1] < current["revenue"] + stub[0]):
        raise ResearchGap("ASML's annual guidance is incompatible with reported sales and fourth-quarter guidance.")
    sales = {"annual_range": full, "q4_range": stub, "source": sources[1],
             "systems_growth": current["systems"]/previous["systems"]-1,
             "service_growth": current["service"]/previous["service"]-1}
    return {"status": "available", "currency": "EUR", "accounting": "US GAAP", "filed_on": filed_on.isoformat(),
            "months": 9, "source": sources[0], "capital_gap": _LIMIT, "dated_sales_guidance": sales,
            "periods": [{"balance_end": balance_end.isoformat(), "balance": interim_balance, "income": current,
                         "cash_flow": current_flows, "prior_comparable_income": previous}]}


def collect_asml_interim(client, reports, annual_end):
    for row in reports:
        filed = date.fromisoformat(row["filed_on"])
        stamp = row.get("period_end")
        if filed <= annual_end:
            break
        if not stamp or not 10 <= filed.month <= 11:
            continue
        end = date.fromisoformat(stamp)
        if end <= annual_end or end.month != 9:
            continue
        statements = set(_candidates(client, row, cik=_CIK, exhibit="EX-99.3"))
        releases = set(_candidates(client, row, cik=_CIK))
        if len(statements) == len(releases) == 1:
            statement, release = statements.pop(), releases.pop()
            try:
                return read_asml_interim(client.get_text(statement), client.get_text(release), balance_end=end,
                                         filed_on=filed, sources=(statement, release))
            except ResearchGap:
                continue
    return {"status": "missing", "reason": "No reconciled ASML nine-month US GAAP exhibit follows the annual report."}

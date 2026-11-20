"""Classify reported Oracle balances and list unresolved forecast inputs."""

from dataclasses import dataclass
from datetime import date
from math import fsum, isfinite
import re

from .forecast_schedules import PPEBalance
from .gd_releases import _Page
from .linked_forecast import ForecastBalance, WorkingCapital


_MILLION = 1_000_000
_FISCAL_YEAR = re.compile(r"(Remainder of )?[Ff]iscal (20\d{2})$")
_REPORTED_MILLIONS = re.compile(r"(?:\d{1,3}(?:,\d{3})+|\d+)$")
_REQUIRED = (
    "total_assets", "total_liabilities", "total_equity", "cash_and_equivalents", "cash_and_restricted_cash",
    "current_assets", "other_current_liabilities", "other_noncurrent_liabilities", "income_taxes_payable_noncurrent",
    "goodwill", "deferred_income_tax_assets_net", "other_noncurrent_assets",
    "available_for_sale_debt_securities_current", "gross_ppe", "accumulated_depreciation",
    "construction_in_progress", "net_ppe", "receivables", "prepaid_and_other_current_assets",
    "accounts_payable", "employee_related_liabilities_current", "deferred_revenue_current",
    "deferred_revenue_noncurrent", "notes_payable_current", "long_term_notes_and_loans",
    "operating_lease_right_of_use_assets", "operating_lease_current", "operating_lease_noncurrent",
    "finance_lease_current", "finance_lease_noncurrent", "preferred_stock_book_value",
    "noncontrolling_interest", "retained_earnings", "ending_shares",
)


def build_oracle_opening_evidence(packet: dict) -> dict:
    """Keep the filing-date accounting anchors distinct from cutoff-date estimates."""
    if packet["issuer"]["cik"].lstrip("0") != "1341439":
        raise ValueError("These balance classifications apply only to Oracle.")
    periods = packet["interim_actuals"]["periods"] or packet["actuals"]["periods"][-1:]
    result = {
        "status": "missing", "opening_date": None, "facts": {}, "checks": [], "missing": [],
        "unresolved_for_cutoff": [
            "Elapsed operating cash flow, investment and financing movements between the reporting date and valuation cutoff.",
            "Outstanding customer financing and accrued capex balances, classified separately from operating working capital.",
            "Point-in-time dilution and economic values for preferred stock and noncontrolling interests.",
        ],
    }
    if not periods:
        result["missing"].append("No reported Oracle balance sheet is available.")
        return result
    period = periods[-1]
    result["opening_date"] = period["period_end"]
    accession = period["filing"]["accession"]
    facts = result["facts"]
    for name in _REQUIRED:
        source = period["sources"].get(name, {})
        value = period["metrics"].get(name)
        if type(value) not in (int, float) or not isfinite(value) or source.get("accession") != accession:
            result["missing"].append(f"{name}: no same-filing opening balance.")
            continue
        unit = "shares" if name == "ending_shares" else "USD"
        if source.get("unit") != unit:
            result["missing"].append(f"{name}: unexpected reporting unit.")
            continue
        facts[name] = {"value_millions": value / _MILLION, "unit": unit, "source": source}
    values = {name: row["value_millions"] for name, row in facts.items()}
    if {"gross_ppe", "construction_in_progress", "accumulated_depreciation", "net_ppe"} <= values.keys():
        ppe = PPEBalance(values["gross_ppe"] - values["construction_in_progress"],
                         values["construction_in_progress"], values["accumulated_depreciation"])
        result["property"] = {
            "gross_in_service_millions": ppe.gross_in_service_millions,
            "construction_in_progress_millions": ppe.construction_in_progress_millions,
            "accumulated_depreciation_millions": ppe.accumulated_depreciation_millions,
            "net_millions": ppe.net_millions,
        }
        result["checks"].append({
            "name": "gross_property_less_accumulated_depreciation",
            "difference_millions": ppe.net_millions - values["net_ppe"],
        })
        if min(ppe.gross_in_service_millions, ppe.construction_in_progress_millions,
               ppe.accumulated_depreciation_millions, ppe.net_millions) < 0:
            result["missing"].append("Reported property components do not support the in-service and construction classifications.")
    if {"total_assets", "total_liabilities", "total_equity"} <= values.keys():
        result["checks"].append({
            "name": "assets_less_liabilities_and_equity",
            "difference_millions": fsum((values["total_assets"], -values["total_liabilities"], -values["total_equity"])),
        })
    result["status"] = "opening_evidence" if not result["missing"] else "partial"
    for row in result["checks"]:
        row["passed"] = abs(row["difference_millions"]) <= 1
        if not row["passed"]:
            result["status"] = "conflicting"
    return result


def reconcile_oracle_offering_adjustments(opening: dict, offering: dict) -> dict:
    """Tie only the disclosed transactions to the reported balance-date bases."""
    result = {
        "status": "unavailable", "balance_date": opening["opening_date"], "checks": [], "missing": [],
        "note": "Transaction-only adjustments. Cutoff balances require subsequent operating, investing, financing and share movements.",
    }
    if offering["status"] != "available" or offering["balance_date"] != opening["opening_date"]:
        result["missing"].append("No verified offering matches the opening balance date.")
        return result
    facts = opening["facts"]
    equity, debt = offering["equity"], offering["debt"]
    required = {"notes_payable_current", "long_term_notes_and_loans", "ending_shares"}
    if not required <= facts.keys() or not {"balance_date_basic_shares", "pro_forma_basic_shares"} <= equity.keys():
        result["missing"].append("The notes and common share anchors need same-date filing evidence.")
        return result
    opening_notes = fsum(facts[key]["value_millions"] for key in ("notes_payable_current", "long_term_notes_and_loans"))
    opening_basic = facts["ending_shares"]["value_millions"] * _MILLION
    result["checks"] = [
        {"name": "prospectus_opening_notes_match_reported_notes",
         "difference_millions": opening_notes - debt["balance_date_debt_usd"] / _MILLION,
         "passed": abs(opening_notes - debt["balance_date_debt_usd"] / _MILLION) <= 1},
        {"name": "exact_prospectus_opening_shares_match_rounded_balance_sheet",
         "difference_millions": (opening_basic - equity["balance_date_basic_shares"]) / _MILLION,
         "passed": abs(opening_basic - equity["balance_date_basic_shares"]) <= 500_000},
    ]
    if not all(check["passed"] for check in result["checks"]):
        result["status"] = "conflicting"
        result["missing"].append("Prospectus bases do not agree with the reported opening balances.")
        return result
    result["status"] = "transactions_matched"
    result["known_movements"] = {
        "closed_on": offering["closed_on"],
        "notes_carrying_increase_estimate_millions": debt["estimated_carrying_debt_increase_usd"] / _MILLION,
        "notes_proceeds_before_expenses_millions": debt["proceeds_before_expenses_usd"] / _MILLION,
        "common_equity_net_proceeds_estimate_millions": (
            equity["estimated_net_proceeds_usd"] / _MILLION if "estimated_net_proceeds_usd" in equity else None
        ),
        "issued_common_shares": equity["shares_issued"],
        "pro_forma_basic_shares": equity["pro_forma_basic_shares"],
        "sources": [offering["closing_source"], equity["source"], debt["source"]],
    }
    return result


def summarize_oracle_cutoff_movements(opening: dict, capital: dict) -> dict:
    """Show the offering-only roll-forward and preserve unresolved cutoff movements."""
    result = {"status": "unavailable", "balance_date": opening["opening_date"], "missing": []}
    adjustments = capital["offering_adjustments"]
    if adjustments["status"] != "transactions_matched":
        result["missing"].append("The offering adjustments are unavailable.")
        return result
    facts = opening["facts"]
    if "cash_and_equivalents" not in facts:
        result["missing"].append("Reported cash and equivalents are unavailable.")
        return result
    movements = adjustments["known_movements"]
    equity_proceeds = movements["common_equity_net_proceeds_estimate_millions"]
    carrying_increase = movements["notes_carrying_increase_estimate_millions"]
    cash = facts["cash_and_equivalents"]["value_millions"]
    debt = capital["offering_terms"]["debt"]
    opening_debt = debt["balance_date_debt_usd"] / _MILLION
    result.update(
        status="offering_only",
        reported_cash_millions=cash,
        estimated_net_equity_proceeds_millions=equity_proceeds,
        estimated_net_note_proceeds_proxy_millions=carrying_increase,
        cash_after_offerings_only_millions=(
            fsum((cash, equity_proceeds, carrying_increase)) if equity_proceeds is not None else None
        ),
        notes_after_offering_only_millions=opening_debt + carrying_increase,
        basic_shares_after_offering_only=movements["pro_forma_basic_shares"],
        dividend_events=capital.get("dividends", {}).get("events", []),
        sources=[facts["cash_and_equivalents"]["source"], *movements["sources"]],
    )
    result["missing"] = [
        "Elapsed operating and investing cash flow and other financing movements.",
        "Aggregate cash dividends, actual offering expenses, and other share movements.",
    ]
    return result


def collect_oracle_amortization(client, opening: dict) -> dict:
    """Read the filed future amortization table, keeping a fiscal stub as a stub."""
    result = {"status": "missing", "balance_date": opening["opening_date"], "schedule": [], "missing": []}
    property_source = opening["facts"].get("net_ppe", {}).get("source")
    if not property_source:
        result["missing"].append("No eligible Oracle balance-date filing was available.")
        return result
    try:
        page = _Page(client.get_text(property_source["url"]))
    except (OSError, ValueError):
        result["missing"].append("The balance-date filing was unavailable for the amortization schedule.")
        return result
    balance_date = date.fromisoformat(opening["opening_date"])
    annual_balance = (balance_date.month, balance_date.day) == (5, 31)
    first_fiscal_year = balance_date.year + (annual_balance or balance_date.month >= 6)
    tables = set()
    for table in page.tables:
        rows = []
        for row in table.rows:
            cells = [cell.text for cell in row if cell.text and cell.text != "$"]
            if len(cells) == 2 and _REPORTED_MILLIONS.fullmatch(cells[1]):
                rows.append((cells[0], int(cells[1].replace(",", ""))))
        if len(rows) < 4 or rows[-1][0] != "Total intangible assets, net" or rows[-2][0] != "Thereafter":
            continue
        schedule = []
        for label, amount in rows[:-2]:
            match = _FISCAL_YEAR.fullmatch(label)
            if not match:
                break
            schedule.append((int(match[2]), bool(match[1]), amount))
        if (len(schedule) != len(rows) - 2 or len(schedule) < 2
                or any(remainder for _, remainder, _ in schedule[1:])
                or any(following[0] != previous[0] + 1 for previous, following in zip(schedule, schedule[1:]))
                or sum(amount for _, _, amount in schedule) + rows[-2][1] != rows[-1][1]):
            continue
        if schedule[0][:2] != (first_fiscal_year, not annual_balance):
            continue
        tables.add((tuple(schedule), rows[-2][1], rows[-1][1]))
    if len(tables) != 1:
        result["status"] = "conflicting" if tables else "missing"
        result["missing"].append("No unique amortization schedule reconciled to the filed net intangible amount.")
        return result
    schedule, thereafter, net = next(iter(tables))
    return {
        **result, "status": "available", "net_intangibles_millions": net,
        "thereafter_millions": thereafter,
        "schedule": [{"fiscal_year": year, "is_remainder": remainder, "amount_millions": amount}
                     for year, remainder, amount in schedule],
        "source": property_source,
    }


@dataclass(frozen=True)
class OracleOpeningClassifications:
    """Amounts in millions, tied to the balance sheet rather than the cash flow statement."""

    capex_payables_within_accounts_payable: float
    financing_within_deferred_revenue: float
    vendor_debt_within_reported_borrowings: float
    dividends_payable_within_other_liabilities: float
    operating_prepaids_within_unrestricted_other_current_assets: float
    operating_accruals_within_other_liabilities: float


def classify_oracle_opening_balance(opening: dict, split: OracleOpeningClassifications) -> ForecastBalance:
    """Use explicit classifications and independently reconcile the published totals."""
    if opening["status"] != "opening_evidence" or opening["intangible_amortization"]["status"] != "available":
        raise ValueError("Oracle's opening balances and amortization schedule must reconcile first.")
    for name, value in vars(split).items():
        if type(value) not in (float, int) or not isfinite(value) or value < 0:
            raise ValueError(f"{name} requires an explicit, nonnegative estimate or observed amount.")
    reported = {name: item["value_millions"] for name, item in opening["facts"].items()}
    period_end = date.fromisoformat(opening["opening_date"])
    # Restricted cash is in the prepaid and other assets line. Use cash-flow
    # reconciled precision for the amount, rather than the footnote's rounded figure.
    restricted = reported["cash_and_restricted_cash"] - reported["cash_and_equivalents"]
    if restricted < 0 or restricted + split.operating_prepaids_within_unrestricted_other_current_assets > reported["prepaid_and_other_current_assets"]:
        raise ValueError("Classified prepaid assets exceed the reported prepaid and other current assets.")
    current_assets = fsum(reported[key] for key in (
        "cash_and_equivalents", "available_for_sale_debt_securities_current", "receivables", "prepaid_and_other_current_assets",
    ))
    if abs(current_assets - reported["current_assets"]) > 1:
        raise ValueError("The modeled current asset lines do not match Oracle's reporting layout.")

    deferred = reported["deferred_revenue_current"] + reported["deferred_revenue_noncurrent"]
    borrowings = reported["notes_payable_current"] + reported["long_term_notes_and_loans"]
    lease_operating = reported["operating_lease_current"] + reported["operating_lease_noncurrent"]
    lease_finance = reported["finance_lease_current"] + reported["finance_lease_noncurrent"]
    # Current operating leases and both finance-lease balances are embedded in
    # other liabilities. Noncurrent operating leases are a separate reported row.
    remaining_liabilities = fsum((
        reported["other_current_liabilities"], reported["other_noncurrent_liabilities"],
        -reported["deferred_revenue_noncurrent"], -reported["operating_lease_current"], -lease_finance,
        -split.dividends_payable_within_other_liabilities, -split.operating_accruals_within_other_liabilities,
    ))
    trade_payables = reported["accounts_payable"] - split.capex_payables_within_accounts_payable
    ordinary_deferred = deferred - split.financing_within_deferred_revenue
    notes = borrowings - split.vendor_debt_within_reported_borrowings
    if min(remaining_liabilities, trade_payables, ordinary_deferred, notes) < 0:
        raise ValueError("The supplied liability classifications exceed their reported totals.")
    working_capital = WorkingCapital(
        receivables_millions=reported["receivables"],
        inventory_millions=0.0,  # No separate inventory line on this layout.
        other_operating_assets_millions=split.operating_prepaids_within_unrestricted_other_current_assets,
        trade_payables_millions=trade_payables,
        ordinary_deferred_revenue_millions=ordinary_deferred,
        other_operating_liabilities_millions=(
            reported["employee_related_liabilities_current"] + split.operating_accruals_within_other_liabilities
        ),
    )
    ppe = opening["property"]
    ppe_balance = PPEBalance(ppe["gross_in_service_millions"], ppe["construction_in_progress_millions"],
                             ppe["accumulated_depreciation_millions"])
    intangibles = opening["intangible_amortization"]["net_intangibles_millions"]
    operating_rou = reported["operating_lease_right_of_use_assets"]
    other_assets = fsum((
        reported["available_for_sale_debt_securities_current"], reported["goodwill"],
        reported["deferred_income_tax_assets_net"], reported["other_noncurrent_assets"], -intangibles,
        reported["prepaid_and_other_current_assets"], -working_capital.other_operating_assets_millions,
    ))
    nci, retained = reported["noncontrolling_interest"], reported["retained_earnings"]
    balance = ForecastBalance(
        period_end=period_end, cash_millions=reported["cash_and_equivalents"], working_capital=working_capital,
        ppe=ppe_balance, intangibles_millions=intangibles, operating_lease_assets_millions=operating_rou,
        operating_lease_liabilities_millions=lease_operating, notes_millions=notes,
        vendor_debt_millions=split.vendor_debt_within_reported_borrowings, finance_leases_millions=lease_finance,
        capital_payables_millions=split.capex_payables_within_accounts_payable,
        customer_financing_millions=split.financing_within_deferred_revenue,
        dividends_payable_millions=split.dividends_payable_within_other_liabilities, other_assets_millions=other_assets,
        other_liabilities_millions=remaining_liabilities + reported["income_taxes_payable_noncurrent"],
        retained_earnings_millions=retained, other_equity_millions=reported["total_equity"] - retained - nci,
        noncontrolling_interest_millions=nci, basic_shares_millions=reported["ending_shares"],
    )
    if other_assets < 0 or abs(balance.assets_millions - reported["total_assets"]) > 1e-6:
        raise ValueError("Oracle's classified opening assets do not reconcile to reported assets.")
    if abs(balance.liabilities_millions - reported["total_liabilities"]) > 1e-6:
        raise ValueError("Oracle's classified opening liabilities do not reconcile to reported liabilities.")
    return balance

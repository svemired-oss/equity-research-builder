"""Map exact annual SEC Company Facts to the GD model's historical rows."""

from dataclasses import dataclass
from datetime import date
from math import isfinite
import re

from .gd_inputs import InputSource
from .sec_client import filing_index_url


GD_CIK = "0000040533"
TAXONOMY = "us-gaap"
ANNUAL_FORMS = ("10-K", "10-K/A")


@dataclass(frozen=True)
class SourcedValue:
    value: float
    source: InputSource
    period_end: date
    accession: str
    form: str
    taxonomy: str
    tag: str
    period_start: date | None = None


@dataclass(frozen=True)
class ActualsResult:
    facts: dict[str, SourcedValue]
    missing: dict[str, str]
    years: tuple[int, ...]


@dataclass(frozen=True)
class _Concept:
    label: str
    tags: tuple[str, ...]
    targets: tuple[tuple[str, int], ...]
    sign: int = 1


_INCOME = "Income statement"
_BALANCE = "Balance sheet"
_SCHEDULES = "Schedules"
_REVENUE = "Revenue model"
_EQUITY = "Equity"

# Alternate tags are considered only for equivalent consolidated concepts.
# GD presents expense and treasury-stock lines as deductions.
_DURATION = (
    _Concept("Total revenue", ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"),
             ((_INCOME, 9), (_REVENUE, 64), (_SCHEDULES, 104))),
    _Concept("General and administrative expense", ("GeneralAndAdministrativeExpense",), ((_INCOME, 14),), -1),
    _Concept("Operating earnings", ("OperatingIncomeLoss",), ((_INCOME, 17), (_REVENUE, 74))),
    _Concept("Earnings before income tax", ("IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",),
             ((_INCOME, 22),)),
    _Concept("Income tax provision", ("IncomeTaxExpenseBenefit",), ((_INCOME, 24),), -1),
    _Concept("Net earnings attributable to the parent", ("NetIncomeLoss",), ((_INCOME, 26), (_EQUITY, 9))),
    _Concept("Capital expenditures", ("PaymentsToAcquirePropertyPlantAndEquipment",), ((_SCHEDULES, 20), (_SCHEDULES, 30))),
)

_INSTANT = (
    _Concept("Cash and equivalents", ("CashAndCashEquivalentsAtCarryingValue",), ((_BALANCE, 8),)),
    _Concept("Accounts receivable", ("AccountsReceivableNetCurrent",), ((_BALANCE, 9), (_SCHEDULES, 116))),
    _Concept("Unbilled receivables", ("ContractWithCustomerAssetCurrent",), ((_BALANCE, 10),)),
    _Concept("Inventories", ("InventoryNet",), ((_BALANCE, 11), (_SCHEDULES, 117))),
    _Concept("Other current assets", ("OtherAssetsCurrent",), ((_BALANCE, 12),)),
    _Concept("Total current assets", ("AssetsCurrent",), ((_BALANCE, 13),)),
    _Concept("Net property, plant and equipment", ("PropertyPlantAndEquipmentNet",),
             ((_BALANCE, 16), (_SCHEDULES, 16), (_SCHEDULES, 50))),
    _Concept("Net intangibles excluding goodwill", ("IntangibleAssetsNetExcludingGoodwill",), ((_BALANCE, 17),)),
    _Concept("Goodwill", ("Goodwill",), ((_BALANCE, 18),)),
    _Concept("Other noncurrent assets", ("OtherAssetsNoncurrent",), ((_BALANCE, 19),)),
    _Concept("Total noncurrent assets", ("AssetsNoncurrent",), ((_BALANCE, 20),)),
    _Concept("Total assets", ("Assets",), ((_BALANCE, 22),)),
    _Concept("Short-term debt and current portion of long-term debt", ("DebtCurrent",), ((_BALANCE, 26), (_SCHEDULES, 88))),
    _Concept("Accounts payable", ("AccountsPayableCurrent",), ((_BALANCE, 27), (_SCHEDULES, 118))),
    _Concept("Customer advances and deposits", ("ContractWithCustomerLiabilityCurrent",), ((_BALANCE, 28),)),
    _Concept("Other current liabilities", ("OtherLiabilitiesCurrent",), ((_BALANCE, 29),)),
    _Concept("Total current liabilities", ("LiabilitiesCurrent",), ((_BALANCE, 30),)),
    _Concept("Long-term debt", ("LongTermDebtNoncurrent",), ((_BALANCE, 33), (_SCHEDULES, 89))),
    _Concept("Other noncurrent liabilities", ("OtherLiabilitiesNoncurrent",), ((_BALANCE, 34),)),
    _Concept("Total noncurrent liabilities", ("LiabilitiesNoncurrent",), ((_BALANCE, 35),)),
    _Concept("Total liabilities", ("Liabilities",), ((_BALANCE, 37),)),
    _Concept("Common stock", ("CommonStockValue",), ((_BALANCE, 40), (_EQUITY, 45))),
    _Concept("Additional paid-in capital", ("AdditionalPaidInCapital", "AdditionalPaidInCapitalCommonStock"),
             ((_BALANCE, 41), (_EQUITY, 32), (_EQUITY, 46))),
    _Concept("Retained earnings", ("RetainedEarningsAccumulatedDeficit",), ((_BALANCE, 42), (_EQUITY, 13), (_EQUITY, 47))),
    _Concept("Treasury stock", ("TreasuryStockValue", "TreasuryStockCommonValue"), ((_BALANCE, 43), (_EQUITY, 22), (_EQUITY, 48)), -1),
    _Concept("Accumulated other comprehensive income or loss", ("AccumulatedOtherComprehensiveIncomeLossNetOfTax",),
             ((_BALANCE, 44), (_EQUITY, 41), (_EQUITY, 49))),
    _Concept("Total shareholders' equity", ("StockholdersEquity",), ((_BALANCE, 45), (_EQUITY, 52))),
    _Concept("Total liabilities and shareholders' equity", ("LiabilitiesAndStockholdersEquity",), ((_BALANCE, 47),)),
)


@dataclass(frozen=True)
class _Fact:
    dollars: float
    tag: str
    filed: date
    accession: str
    form: str


def _annual_fact(row: object, tag: str, start: date | None, end: date, as_of: date) -> _Fact | None:
    if not isinstance(row, dict):
        return None
    expected_start = start.isoformat() if start else None
    if (row.get("start") != expected_start or row.get("end") != end.isoformat()
            or type(row.get("fy")) is not int or row["fy"] != end.year
            or row.get("fp") != "FY" or row.get("form") not in ANNUAL_FORMS):
        return None
    accession = row.get("accn")
    if not isinstance(accession, str) or not re.fullmatch(r"[0-9]{10}-[0-9]{2}-[0-9]{6}", accession):
        return None
    try:
        filed = date.fromisoformat(row.get("filed"))
        dollars = row.get("val")
        if (row["filed"] != filed.isoformat() or not end <= filed <= as_of
                or type(dollars) not in (int, float) or not isfinite(dollars)):
            return None
    except (TypeError, ValueError, OverflowError):
        return None
    return _Fact(dollars, tag, filed, accession, row["form"])


def _select(concepts: dict, mapping: _Concept, start: date | None, end: date, as_of: date) -> SourcedValue | str:
    candidates = []
    for tag in mapping.tags:
        data = concepts.get(tag, {})
        units = data.get("units", {}) if isinstance(data, dict) else {}
        rows = units.get("USD", []) if isinstance(units, dict) else []
        if not isinstance(rows, list):
            continue
        for row in rows:
            fact = _annual_fact(row, tag, start, end, as_of)
            if fact is not None:
                candidates.append(fact)
    if not candidates:
        return f"No eligible {end.year} annual USD fact for {mapping.label} by {as_of.isoformat()}."

    latest = max(fact.filed for fact in candidates)
    contemporaneous = [fact for fact in candidates if fact.filed == latest]
    if len({fact.dollars for fact in contemporaneous}) != 1:
        return f"Conflicting {end.year} annual USD facts for {mapping.label} filed {latest.isoformat()}."
    selected = min(contemporaneous, key=lambda fact: (mapping.tags.index(fact.tag), fact.accession))
    return SourcedValue(
        value=mapping.sign * selected.dollars / 1_000_000,
        source=InputSource(
            kind="sec_filing",
            citation=(f"General Dynamics Form {selected.form} for FY{end.year}, {mapping.label}. "
                      f"{TAXONOMY}:{selected.tag}, accession {selected.accession}."),
            available_on=selected.filed,
            url=filing_index_url(GD_CIK, selected.accession),
        ),
        period_end=end,
        accession=selected.accession,
        form=selected.form,
        taxonomy=TAXONOMY,
        tag=selected.tag,
        period_start=start,
    )


def map_gd_actuals(company_facts: dict, *, as_of: date, years: tuple[int, ...]) -> ActualsResult:
    """Right-align the requested FYs to GD's B:F history and track missing mappings."""
    if not 1 <= len(years) <= 5 or any(
        type(year) is not int or not 1 <= year <= 9999 or year != years[0] + index
        for index, year in enumerate(years)
    ):
        raise ValueError("Expected one to five consecutive fiscal years for GD history.")
    if not isinstance(company_facts, dict):
        raise ValueError("Expected a SEC Company Facts object.")
    if "cik" in company_facts and company_facts["cik"] not in (40533, GD_CIK):
        raise ValueError("SEC Company Facts do not identify General Dynamics.")
    facts = company_facts.get("facts")
    if not isinstance(facts, dict) or not isinstance(facts.get(TAXONOMY), dict):
        raise ValueError("SEC Company Facts are missing the US GAAP taxonomy.")

    mapped = {}
    missing = {}
    for column, year in zip("BCDEF"[-len(years):], years, strict=True):
        end = date(year, 12, 31)
        for mappings, start in ((_DURATION, date(year, 1, 1)), (_INSTANT, None)):
            for mapping in mappings:
                selected = _select(facts[TAXONOMY], mapping, start, end, as_of)
                for sheet, row in mapping.targets:
                    key = f"{sheet}!{column}{row}"
                    if isinstance(selected, SourcedValue):
                        mapped[key] = selected
                    else:
                        missing[key] = selected
    return ActualsResult(mapped, missing, years)

"""Map selected GD statement detail and schedule anchors from its annual filing."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from math import fsum, isfinite

from .filing_xbrl import FilingInstance
from .gd_actuals import SourcedValue
from .gd_inputs import InputSource


INCOME = "Income statement"
BALANCE = "Balance sheet"
SCHEDULES = "Schedules"
EQUITY = "Equity"
USD_MILLIONS = 1_000_000
Dimensions = tuple[tuple[str, str], ...]
Targets = tuple[tuple[str, int], ...]
PRODUCT = (("srt:ProductOrServiceAxis", "us-gaap:ProductMember"),)
SERVICE = (("srt:ProductOrServiceAxis", "us-gaap:ServiceMember"),)


@dataclass(frozen=True)
class _Query:
    tag: str
    period: str = "ending"
    dimensions: Dimensions = ()
    unit: str = "USD"

    @property
    def description(self) -> str:
        if not self.dimensions:
            return self.tag
        dimensions = ", ".join(f"{axis}={member}" for axis, member in self.dimensions)
        return f"{self.tag} ({dimensions})"


REVENUE = "RevenueFromContractWithCustomerExcludingAssessedTax"
COST_OF_SALES = "CostOfGoodsAndServicesSold"
DEPRECIATION_RESERVE = "AccumulatedDepreciationDepletionAndAmortizationPropertyPlantAndEquipment"
CURRENT_DEBT = "DebtCurrent"
NONCURRENT_DEBT = "LongTermDebtNoncurrent"
NET_INTEREST = "InterestIncomeExpenseNet"
MATURITIES = (
    "LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths",
    "LongTermDebtMaturitiesRepaymentsOfPrincipalInYearTwo",
    "LongTermDebtMaturitiesRepaymentsOfPrincipalInYearThree",
    "LongTermDebtMaturitiesRepaymentsOfPrincipalInYearFour",
    "LongTermDebtMaturitiesRepaymentsOfPrincipalInYearFive",
    "LongTermDebtMaturitiesRepaymentsOfPrincipalAfterYearFive",
)


def _gaap(tag: str, period: str = "ending", dimensions: Dimensions = (), unit: str = "USD") -> _Query:
    return _Query(f"us-gaap:{tag}", period, dimensions, unit)


_REPORTED = (
    # The filing's product/service axis separates consolidated line items from segment revenue.
    (_gaap(REVENUE, "annual", PRODUCT), ((INCOME, 7),), 1),
    (_gaap(REVENUE, "annual", SERVICE), ((INCOME, 8),), 1),
    (_gaap(COST_OF_SALES, "annual", PRODUCT), ((INCOME, 12),), -1),
    (_gaap(COST_OF_SALES, "annual", SERVICE), ((INCOME, 13),), -1),
    (_gaap(COST_OF_SALES, "annual", PRODUCT), ((SCHEDULES, 105),), 1),
    (_gaap(COST_OF_SALES, "annual", SERVICE), ((SCHEDULES, 106),), 1),
    (_gaap("CostsAndExpenses", "annual"), ((INCOME, 15),), -1),
    (_gaap("OtherNonoperatingIncomeExpense", "annual"), ((INCOME, 19),), 1),
    (_gaap(NET_INTEREST, "annual"), ((INCOME, 20),), 1),
    (_gaap(NET_INTEREST, "annual"), ((SCHEDULES, 95),), -1),
    (_gaap("UnbilledReceivablesCurrent"), ((BALANCE, 10),), 1),
    (_gaap("FiniteLivedIntangibleAssetsNet"), ((BALANCE, 17),), 1),
    # Note I gives the PP&E balances. The cash-flow statement supplies depreciation.
    (_gaap("MachineryAndEquipmentGross"), ((SCHEDULES, 8),), 1),
    (_gaap("BuildingsAndImprovementsGross"), ((SCHEDULES, 9),), 1),
    (_gaap("ConstructionInProgressGross"), ((SCHEDULES, 10),), 1),
    (_gaap("LandImprovements"), ((SCHEDULES, 11),), 1),
    (_gaap("PropertyPlantAndEquipmentGross"), ((SCHEDULES, 13), (SCHEDULES, 33), (SCHEDULES, 47)), 1),
    (_gaap(DEPRECIATION_RESERVE), ((SCHEDULES, 14), (SCHEDULES, 48)), -1),
    (_gaap(DEPRECIATION_RESERVE), ((SCHEDULES, 42),), 1),
    (_gaap("PropertyPlantAndEquipmentGross", "opening"), ((SCHEDULES, 29),), 1),
    (_gaap(DEPRECIATION_RESERVE, "opening"), ((SCHEDULES, 38),), 1),
    (_gaap("Depreciation", "annual"), ((SCHEDULES, 21), (SCHEDULES, 39)), 1),
    (_gaap("DividendsCash", "annual"), ((EQUITY, 10),), -1),
    (_gaap("RetainedEarningsAccumulatedDeficit", "opening"), ((EQUITY, 8),), 1),
    (_gaap("TreasuryStockValue", "opening"), ((EQUITY, 17),), -1),
    (_gaap("AdditionalPaidInCapital", "opening"), ((EQUITY, 26),), 1),
    (_gaap("AccumulatedOtherComprehensiveIncomeLossNetOfTax", "opening"), ((EQUITY, 36),), 1),
    (_gaap("OtherComprehensiveIncomeLossNetOfTax", "annual"), ((EQUITY, 37),), 1),
    (_gaap("CommonStockSharesOutstanding", unit="shares"), ((EQUITY, 21),), 1),
)


class _MissingFact(ValueError):
    pass


def map_gd_schedule_history(
    instance: FilingInstance, *, year: int, column: str, accession: str, filing_date: date,
) -> tuple[dict[str, SourcedValue], dict[str, str]]:
    """Use one annual filing's exact periods, dimensions and units for GD detail."""
    if column not in ("B", "C", "D", "E", "F"):
        raise ValueError("Historical GD schedule columns must be B through F.")
    start, end = date(year, 1, 1), date(year, 12, 31)
    if filing_date < end:
        raise ValueError("The GD annual filing date precedes the fiscal year-end.")
    periods = {"annual": (start, end), "ending": (None, end), "opening": (None, date(year - 1, 12, 31))}
    index: dict[tuple, set[float]] = {}
    for fact in instance.facts:
        if isfinite(fact.value):
            key = fact.concept, fact.start, fact.end, fact.dimensions, fact.unit
            index.setdefault(key, set()).add(fact.value)

    facts: dict[str, SourcedValue] = {}
    missing: dict[str, str] = {}

    def lookup(query: _Query) -> float:
        period_start, period_end = periods[query.period]
        candidates = index.get((query.tag, period_start, period_end, query.dimensions, query.unit), set())
        if len(candidates) != 1:
            status = "Conflicting" if candidates else "Missing"
            raise _MissingFact(f"{status} FY{year} {query.period} {query.unit} fact for {query.description}.")
        return next(iter(candidates))

    def store(
        targets: Targets, label: str, queries: tuple[_Query, ...], calculate: Callable[..., float], *, derived: bool,
    ) -> None:
        addresses = tuple(f"{sheet}!{column}{row}" for sheet, row in targets)
        try:
            value = calculate(*(lookup(query) for query in queries))
            if not isfinite(value):
                raise _MissingFact(f"FY{year} {label} is not finite.")
        except (_MissingFact, ZeroDivisionError) as exc:
            note = str(exc) if isinstance(exc, _MissingFact) else f"FY{year} {label} has a zero denominator."
            missing.update(dict.fromkeys(addresses, note))
            return

        tags = ", ".join(query.description for query in queries)
        fiscal_start, fiscal_end = periods[queries[0].period] if not derived else (start, end)
        source = InputSource(
            kind="derived" if derived else "sec_filing",
            citation=f"General Dynamics FY{year} Form 10-K, {label}. {tags}. Accession {accession}.",
            available_on=filing_date,
            url=instance.url,
        )
        fact = SourcedValue(
            value=value, source=source, period_end=fiscal_end, accession=accession,
            form="10-K", taxonomy="derived" if derived else "us-gaap", tag=tags, period_start=fiscal_start,
        )
        facts.update(dict.fromkeys(addresses, fact))

    for query, targets, sign in _REPORTED:
        store(targets, query.period, (query,), lambda value: sign * value / USD_MILLIONS, derived=False)

    revenue = _gaap(REVENUE, "annual")
    for row, concept in (
        (59, "UnbilledReceivablesCurrent"), (61, "OtherAssetsCurrent"), (68, "OtherAssetsNoncurrent"),
        (77, "ContractWithCustomerLiabilityCurrent"), (78, "OtherLiabilitiesCurrent"), (83, "OtherLiabilitiesNoncurrent"),
    ):
        store(((BALANCE, row),), "year-end balance divided by annual revenue", (_gaap(concept), revenue),
              lambda balance, sales: balance / sales, derived=True)
    store(((INCOME, 30),), "signed tax provision divided by pretax earnings", (
        _gaap("IncomeTaxExpenseBenefit", "annual"),
        _gaap("IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest", "annual"),
    ), lambda provision, pretax: -provision / pretax, derived=True)
    store(((BALANCE, 66),), "change in net finite-lived intangibles", (
        _gaap("FiniteLivedIntangibleAssetsNet"), _gaap("FiniteLivedIntangibleAssetsNet", "opening"),
    ), lambda closing, opening: (closing - opening) / USD_MILLIONS, derived=True)

    current_debt = (_gaap(CURRENT_DEBT), _gaap(NONCURRENT_DEBT))
    opening_debt = (_gaap(CURRENT_DEBT, "opening"), _gaap(NONCURRENT_DEBT, "opening"))
    store(((SCHEDULES, 75), (SCHEDULES, 90)), "current and noncurrent debt at carrying value", current_debt,
          lambda *values: fsum(values) / USD_MILLIONS, derived=True)
    store(((SCHEDULES, 73), (SCHEDULES, 81)), "principal from all six disclosed maturity bands",
          tuple(_gaap(tag) for tag in MATURITIES), lambda *values: fsum(values) / USD_MILLIONS, derived=True)
    store(((SCHEDULES, 94),), "average of opening and closing debt carrying amounts", current_debt + opening_debt,
          lambda *values: fsum(values) / 2 / USD_MILLIONS, derived=True)
    store(((SCHEDULES, 93),), "net interest expense divided by average debt carrying value",
          (_gaap(NET_INTEREST, "annual"),) + current_debt + opening_debt,
          lambda interest, *debt: -interest / (fsum(debt) / 2), derived=True)

    working_capital = (_gaap("AccountsReceivableNetCurrent"), _gaap("InventoryNet"), _gaap("AccountsPayableCurrent"))
    store(((SCHEDULES, 120),), "receivables plus inventories less accounts payable", working_capital,
          lambda receivables, inventory, payables: (receivables + inventory - payables) / USD_MILLIONS, derived=True)
    return facts, missing

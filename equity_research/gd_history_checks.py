"""Reconcile sourced GD history and measure coverage against a private model run."""

from dataclasses import dataclass
from datetime import date
from math import fsum, isclose

from .gd_inputs import GDInputs, InputSource, cell_address
from .gd_projection import required_history_cells
from .reconciliation import TOLERANCE_MILLIONS


ANNUAL_CASH_FIELDS = {
    "cash_from_operations", "capital_expenditures", "cash_from_investing", "cash_from_financing",
    "cash_from_discontinued", "cash_change", "cash_beginning", "cash_ending", "issuer_free_cash_flow",
}


@dataclass(frozen=True)
class AnnualCashActuals:
    amounts: dict[str, float]
    source: InputSource


@dataclass(frozen=True)
class HistoricalCheck:
    name: str
    year: int
    status: str
    difference_millions: float | None
    missing: tuple[str, ...] = ()


@dataclass(frozen=True)
class HistoryDifference:
    reference: float
    sourced: float


@dataclass(frozen=True)
class HistoryComparison:
    compared: int
    matched: int
    required: int
    required_available: int
    missing_required: tuple[str, ...]
    differences: dict[str, HistoryDifference]


def _check(
    name: str, year: int, values: dict[str, float], components: tuple[str, ...], total: str,
    *, subtract: tuple[str, ...] = (),
) -> HistoricalCheck:
    absent = tuple(key for key in (*components, *subtract, total) if key not in values)
    if absent:
        return HistoricalCheck(name, year, "unavailable", None, absent)
    difference = fsum(values[key] for key in components) - fsum(values[key] for key in subtract) - values[total]
    status = "passed" if abs(difference) <= TOLERANCE_MILLIONS else "failed"
    return HistoricalCheck(name, year, status, difference)


def reconcile_gd_history(
    history: dict[str, float], years: tuple[int, ...],
    *, cash_flow_actuals: dict[int, AnnualCashActuals] | None = None,
) -> tuple[HistoricalCheck, ...]:
    """Check independently reported totals against their sourced components."""
    checks = []
    for year, col in zip(years, "BCDEF"[-len(years):], strict=True):
        revenue = f"Revenue model!{col}"
        income = f"Income statement!{col}"
        balance = f"Balance sheet!{col}"
        schedule = f"Schedules!{col}"
        specifications = (
            ("Assets equal liabilities and equity", (balance + "22",), balance + "47"),
            ("Equity components sum to reported equity", tuple(f"{balance}{row}" for row in range(40, 45)), balance + "45"),
            ("Segments sum to consolidated revenue", tuple(f"{revenue}{row}" for row in range(60, 64)), income + "9"),
            ("Segments and corporate costs sum to operating earnings", tuple(f"{revenue}{row}" for row in range(69, 74)), income + "17"),
            ("Gross PP&E less accumulated depreciation equals net PP&E", (schedule + "13", schedule + "14"), balance + "16"),
            ("Pretax earnings less taxes equal net earnings", (income + "22", income + "24"), income + "26"),
        )
        for name, components, total in specifications:
            checks.append(_check(name, year, history, components, total))
        if cash_flow_actuals is None:
            continue

        def rows(prefix: str, *numbers: int) -> tuple[str, ...]:
            return tuple(f"{prefix}{number}" for number in numbers)

        specifications = (
            ("Product and service revenue sum to total revenue", rows(income, 7, 8), income + "9"),
            ("Cost lines sum to operating costs", rows(income, 12, 13, 14), income + "15"),
            ("Revenue and signed costs sum to operating earnings", rows(income, 9, 15), income + "17"),
            ("Operating earnings and nonoperating items sum to pretax earnings", rows(income, 17, 19, 20), income + "22"),
            ("Current asset detail sums to current assets", rows(balance, 8, 9, 10, 11, 12), balance + "13"),
            ("Noncurrent asset detail sums to noncurrent assets", rows(balance, 16, 17, 18, 19), balance + "20"),
            ("Current and noncurrent assets sum to total assets", rows(balance, 13, 20), balance + "22"),
            ("Current liability detail sums to current liabilities", rows(balance, 26, 27, 28, 29), balance + "30"),
            ("Noncurrent liability detail sums to noncurrent liabilities", rows(balance, 33, 34), balance + "35"),
            ("Liabilities and equity sum to their reported total", rows(balance, 30, 35, 45), balance + "47"),
            ("PP&E classes sum to gross PP&E", rows(schedule, 8, 9, 10, 11), schedule + "13"),
            ("Aerospace product groups sum to segment revenue", rows(revenue, 8, 9), revenue + "60"),
            ("Marine Systems product groups sum to segment revenue", rows(revenue, 23, 24, 25), revenue + "61"),
            ("Combat Systems product groups sum to segment revenue", rows(revenue, 36, 37, 38), revenue + "62"),
            ("Technologies product groups sum to segment revenue", rows(revenue, 49, 50), revenue + "63"),
        )
        checks.extend(_check(name, year, history, components, total) for name, components, total in specifications)

        annual_cash = cash_flow_actuals.get(year)
        cash = annual_cash.amounts.copy() if annual_cash else {}
        if balance + "8" in history:
            cash[balance + "8"] = history[balance + "8"]
        specifications = (
            ("Cash-flow sections sum to reported cash change", (
                "cash_from_operations", "cash_from_investing", "cash_from_financing", "cash_from_discontinued",
            ), "cash_change"),
            ("Opening cash and cash change sum to closing cash", ("cash_beginning", "cash_change"), "cash_ending"),
            ("Closing cash agrees across statements", ("cash_ending",), balance + "8"),
        )
        checks.extend(_check(name, year, cash, components, total) for name, components, total in specifications)
        checks.append(_check(
            "Operating cash flow less capex equals issuer free cash flow", year, cash,
            ("cash_from_operations",), "issuer_free_cash_flow", subtract=("capital_expenditures",),
        ))
        if year != years[0]:
            previous = cash_flow_actuals.get(year - 1)
            if previous is not None:
                cash["prior_cash_ending"] = previous.amounts["cash_ending"]
            checks.append(_check(
                "Opening cash agrees with prior year ending cash", year, cash, ("prior_cash_ending",), "cash_beginning",
            ))
    return tuple(checks)


def compare_gd_history(
    history: dict[str, float], years: tuple[int, ...], *, as_of: date, reference: GDInputs,
) -> HistoryComparison:
    """Compare like-period values and show which required model inputs still lack evidence."""
    start = reference.forecast_ends[0].year
    if reference.as_of != as_of or years != tuple(range(start - 5, start)):
        raise ValueError("The evidence and comparison input file must share the same cutoff and fiscal years.")

    required = required_history_cells(reference)
    archived = {cell_address(cell): value for cell, value in reference.history.items()}
    compared = sorted(history.keys() & archived.keys())
    differences = {
        key: HistoryDifference(archived[key], history[key]) for key in compared
        if not isclose(archived[key], history[key], rel_tol=0.0, abs_tol=TOLERANCE_MILLIONS)
    }
    return HistoryComparison(
        compared=len(compared), matched=len(compared) - len(differences),
        required=len(required), required_available=len(required & history.keys()),
        missing_required=tuple(sorted(required - history.keys())), differences=differences,
    )

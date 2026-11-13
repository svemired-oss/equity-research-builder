"""Reconcile cached GD V4 statements and schedules in USD millions."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class Check:
    name: str
    compared: int
    max_abs_difference_millions: float
    tolerance_millions: float

    @property
    def passed(self) -> bool:
        return self.max_abs_difference_millions <= self.tolerance_millions


# The replay reads unrounded numeric values. Allow one dollar of numerical drift.
TOLERANCE_MILLIONS = 1e-6
ALL_PERIODS = "BCDEFGHIJKL"
FORECAST_PERIODS = "GHIJKL"


def _check(name: str, differences: Iterable[float]) -> Check:
    residuals = [abs(difference) for difference in differences]
    return Check(name, len(residuals), max(residuals), TOLERANCE_MILLIONS)


def check_statements(number: Callable[[str, str], float]) -> list[Check]:
    """Compare line items and cross-sheet totals for the approved workbook layout."""
    return [
        _check(
            "Assets equal liabilities plus equity",
            (
                number("Balance sheet", f"{col}22") - number("Balance sheet", f"{col}47")
                for col in ALL_PERIODS
            ),
        ),
        _check(
            "Forecast closing cash ties to the balance sheet",
            (
                number("Cash flow", f"{col}56") - number("Balance sheet", f"{col}8")
                for col in FORECAST_PERIODS
            ),
        ),
        _check(
            "Forecast opening cash plus cash generated equals closing cash",
            (
                number("Cash flow", f"{col}55")
                + number("Cash flow", f"{col}53")
                - number("Cash flow", f"{col}56")
                for col in FORECAST_PERIODS
            ),
        ),
        _check(
            "Segment revenue sums to income-statement revenue",
            (
                sum(number("Revenue model", f"{col}{row}") for row in range(60, 64))
                - number("Income statement", f"{col}9")
                for col in ALL_PERIODS
            ),
        ),
        _check(
            "Segment and corporate operating earnings tie to the income statement",
            (
                sum(number("Revenue model", f"{col}{row}") for row in range(69, 74))
                - number("Income statement", f"{col}17")
                for col in ALL_PERIODS
            ),
        ),
        _check(
            "Net PP&E schedule ties to the balance sheet",
            (
                number("Schedules", f"{col}50") - number("Balance sheet", f"{col}16")
                for col in ALL_PERIODS
            ),
        ),
        _check(
            "Debt schedule ties to current and long-term balance-sheet debt",
            (
                number("Schedules", f"{col}90")
                - number("Balance sheet", f"{col}26")
                - number("Balance sheet", f"{col}33")
                for col in ALL_PERIODS
            ),
        ),
        _check(
            "Working capital schedule ties to receivables, inventories, and payables",
            (
                number("Schedules", f"{col}120")
                - number("Balance sheet", f"{col}9")
                - number("Balance sheet", f"{col}11")
                + number("Balance sheet", f"{col}27")
                for col in ALL_PERIODS
            ),
        ),
        _check(
            "Equity schedule ties to the balance sheet",
            (
                number("Equity", f"{col}52") - number("Balance sheet", f"{col}45")
                for col in ALL_PERIODS
            ),
        ),
    ]

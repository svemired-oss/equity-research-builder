"""Reproduce the approved March 30, 2026 GD workbook valuation."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from hashlib import sha256
from io import BytesIO
from math import isclose, isfinite
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils.cell import coordinate_to_tuple
from openpyxl.utils.datetime import from_excel

from .reconciliation import Check, check_statements
from .valuation import ForecastCashFlow, value_fcfe


BASELINE_SHA256 = "eaf785413a955513a33221ed7ef26b7ddf5f9e14a638508510522172504733d4"
BASELINE_DATE = date(2026, 3, 30)
VALUATION_TOLERANCE = 0.000001  # USD per share
MODEL_SHEETS = (
    "Cover", "DCF", "Ke", "Revenue model", "Income statement", "Balance sheet",
    "Cash flow", "Equity", "Schedules",
)


@dataclass(frozen=True)
class ValuationReplay:
    discount_date: date
    modeled_usd_per_share: float
    saved_usd_per_share: float
    sensitivity_cells_checked: int
    max_sensitivity_difference_usd_per_share: float


@dataclass(frozen=True)
class GDReplay:
    workbook_sha256: str
    as_of: date
    forecast_end: date
    cost_of_equity: float
    terminal_growth: float
    intrinsic: ValuationReplay
    twelve_month: ValuationReplay
    statement_checks: tuple[Check, ...]
    known_issues: tuple[str, ...]


@dataclass(frozen=True)
class CachedWorkbook:
    sheets: dict[str, tuple[tuple[object, ...], ...]]
    epoch: datetime

    def value(self, sheet: str, address: str) -> object:
        row, column = coordinate_to_tuple(address)
        try:
            return self.sheets[sheet][row - 1][column - 1]
        except (KeyError, IndexError) as exc:
            raise ValueError(f"Missing required workbook cell {sheet}!{address}.") from exc

    def number(self, sheet: str, address: str) -> float:
        value = self.value(sheet, address)
        if type(value) not in (int, float) or not isfinite(value):
            raise ValueError(f"Expected a cached number in {sheet}!{address}, got {value!r}.")
        return float(value)

    def day(self, sheet: str, address: str) -> date:
        value = self.value(sheet, address)
        if type(value) in (int, float) and isfinite(value):
            value = from_excel(value, self.epoch)
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        raise ValueError(f"Expected a cached date in {sheet}!{address}, got {value!r}.")


def read_baseline_bytes(path: Path) -> bytes:
    contents = path.read_bytes()
    digest = sha256(contents).hexdigest()
    if digest != BASELINE_SHA256:
        raise ValueError(
            "The workbook does not match the approved March 30, 2026 GD V4 copy "
            f"(SHA-256 {digest}). Use the original portfolio copy for this replay."
        )
    return contents


def read_cached_workbook(contents: bytes) -> CachedWorkbook:
    workbook = load_workbook(BytesIO(contents), data_only=True, read_only=True, keep_links=False)
    try:
        sheets = {sheet: tuple(workbook[sheet].values) for sheet in MODEL_SHEETS}
        return CachedWorkbook(sheets, workbook.epoch)
    finally:
        workbook.close()


def read_baseline(path: Path) -> CachedWorkbook:
    return read_cached_workbook(read_baseline_bytes(path))


def replay_horizon(
    workbook: CachedWorkbook,
    cash_flows: tuple[ForecastCashFlow, ...],
    discount_date: date,
    cost_of_equity: float,
    terminal_growth: float,
    shares: float,
    target_cell: str,
    cover_cell: str,
    header_row: int,
) -> ValuationReplay:
    modeled = value_fcfe(cash_flows, discount_date, cost_of_equity, terminal_growth, shares)
    saved = workbook.number("DCF", target_cell)
    cover = workbook.number("Cover", cover_cell)
    if max(abs(modeled - saved), abs(modeled - cover)) > VALUATION_TOLERANCE:
        raise ValueError(f"DCF!{target_cell} or Cover!{cover_cell} disagrees with the FCFE replay.")

    differences = []
    matched_base = False
    for row in range(header_row + 1, header_row + 8):
        rate = workbook.number("DCF", f"B{row}")
        for column in "CDEFGHI":
            growth = workbook.number("DCF", f"{column}{header_row}")
            grid_value = value_fcfe(cash_flows, discount_date, rate, growth, shares)
            cached = workbook.number("DCF", f"{column}{row}")
            differences.append(abs(grid_value - cached))
            if isclose(rate, cost_of_equity) and isclose(growth, terminal_growth):
                matched_base = True
                if abs(cached - modeled) > VALUATION_TOLERANCE:
                    raise ValueError(f"DCF!{column}{row} disagrees with the base-case valuation.")

    maximum = max(differences)
    if not matched_base or maximum > VALUATION_TOLERANCE:
        raise ValueError(f"DCF sensitivity grid below row {header_row} failed to reconcile.")
    return ValuationReplay(discount_date, modeled, saved, len(differences), maximum)


def replay_gd(path: Path) -> GDReplay:
    workbook = read_baseline(path)
    as_of = workbook.day("Cover", "E11")
    forward = workbook.day("DCF", "G4")
    if as_of != BASELINE_DATE or as_of != workbook.day("DCF", "G3"):
        raise ValueError("Cover and DCF must both use the approved March 30 valuation date.")
    if forward != as_of + timedelta(days=365):
        raise ValueError("The saved 12-month discount date disagrees with the workbook convention.")

    for row in range(14, 20):
        if workbook.number("Cover", f"I{row}") != 2:
            raise ValueError(f"Cover!I{row} is not set to the saved base case.")

    cash_flows = tuple(
        ForecastCashFlow(
            period_end=workbook.day("DCF", f"{dcf_column}14"),
            fcfe_millions=workbook.number("Cash flow", f"{cash_flow_column}48"),
        )
        for dcf_column, cash_flow_column in zip("DEFGHI", "GHIJKL", strict=True)
    )
    cost_of_equity = workbook.number("Ke", "D32")
    terminal_growth = workbook.number("DCF", "B19")
    if not isclose(cost_of_equity, workbook.number("DCF", "B3")):
        raise ValueError("DCF cost of equity disagrees with the cost-of-equity schedule.")

    checks = tuple(check_statements(workbook.number))
    failed_checks = [check.name for check in checks if not check.passed]
    if failed_checks:
        raise ValueError(f"Statement reconciliation failed for {', '.join(failed_checks)}.")

    return GDReplay(
        workbook_sha256=BASELINE_SHA256,
        as_of=as_of,
        forecast_end=cash_flows[-1].period_end,
        cost_of_equity=cost_of_equity,
        terminal_growth=terminal_growth,
        intrinsic=replay_horizon(
            workbook, cash_flows, as_of, cost_of_equity, terminal_growth,
            shares=workbook.number("DCF", "I3"), target_cell="B24", cover_cell="E18", header_row=26,
        ),
        twelve_month=replay_horizon(
            workbook, cash_flows, forward, cost_of_equity, terminal_growth,
            shares=workbook.number("DCF", "I4"), target_cell="B46", cover_cell="E19", header_row=48,
        ),
        statement_checks=checks,
        known_issues=(
            "Live comps depend on a vendor add-in and are outside the valuation replay.",
            "The terminal-value caption says 2030. The cash flows extend through 2031.",
            "Forecast operating-expense detail differs from the displayed total in all six years.",
            "The saved quote has an unverified observation date. No upside or rating is issued.",
        ),
    )

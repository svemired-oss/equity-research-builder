"""Build GD forecasts from historical values and explicitly supplied assumptions."""

from dataclasses import dataclass, replace
from datetime import date
from math import isfinite
from pathlib import Path

from openpyxl.utils.cell import coordinate_to_tuple, get_column_letter

from .gd_inputs import Cell, GDInputs, GuidedOverride, InputSource
from .gd_projection_types import ForecastFormula
from .gd_replay import replay_horizon
from .gd_revenue import revenue_formulas
from .gd_schedules import equity_formulas, schedules_formulas
from .gd_statements import balance_formulas, cash_flow_formulas, income_formulas, operating_expense_gap
from .gd_workbook import load_workbook_inputs
from .reconciliation import check_statements
from .valuation import ForecastCashFlow, value_fcfe


FIRST_FORECAST_COLUMN = 7  # G
FORECAST_YEARS = 6
FORECAST_TOLERANCE = 1e-6
UNAVAILABLE_FORMULAS = {("Revenue model", 85)}  # Forward backlog inputs are blank.
@dataclass(frozen=True)
class ForecastYear:
    period_end: date
    revenue_millions: float
    operating_earnings_millions: float
    net_earnings_millions: float
    closing_cash_millions: float
    fcfe_millions: float
    operating_expense_detail_gap_millions: float


@dataclass(frozen=True)
class ProjectionReport:
    as_of: date
    source: InputSource
    override_log: tuple[GuidedOverride, ...]
    years: tuple[ForecastYear, ...]
    formula_cells_matched: int | None
    statement_period_checks: int
    sensitivity_cells_matched: int | None
    assumptions_used: int
    intrinsic_usd_per_share: float
    twelve_month_usd_per_share: float
    notes: tuple[str, ...]


def _address(cell: Cell) -> str:
    sheet, row, index = cell
    return f"{sheet}!{get_column_letter(FIRST_FORECAST_COLUMN + index)}{row}"


@dataclass(frozen=True)
class ForecastPeriod:
    book: "ForecastBook"
    index: int

    @property
    def cost_of_equity(self) -> float:
        return self.book.inputs.cost_of_equity

    @property
    def dividend_payout_ratio(self) -> float:
        return self.book.inputs.dividend_payout_ratio

    def get(self, sheet: str, row: int, offset: int = 0) -> float:
        return self.book.value((sheet, row, self.index + offset))

    def input(self, sheet: str, row: int) -> float:
        return self.book.assumption((sheet, row, self.index))

    def selector(self, row: int) -> int:
        choice = self.book.inputs.scenarios.get(row)
        if type(choice) is not int or choice not in (1, 2, 3):
            raise ValueError(f"Cover!I{row} must select Bull (1), Base (2), or Bear (3).")
        return choice


class ForecastBook:
    """Resolve only modeled forecast formulas, historical values, and literal inputs."""

    def __init__(self, inputs: GDInputs, formulas: dict[str, dict[int, ForecastFormula]]):
        self.inputs = inputs
        self.formulas = formulas
        self.used_assumptions: set[Cell] = set()
        self.used_history: set[Cell] = set()
        self._values: dict[Cell, float] = {}
        self._active: set[Cell] = set()

    def assumption(self, cell: Cell) -> float:
        value = self.inputs.assumptions.get(cell)
        if type(value) not in (int, float) or not isfinite(value):
            raise ValueError(f"Missing or invalid forecast assumption at {_address(cell)}.")
        self.used_assumptions.add(cell)
        return value

    def value(self, cell: Cell) -> float:
        sheet, row, index = cell
        if index < -5 or index >= FORECAST_YEARS:
            raise ValueError(f"Period index {index} is outside the GD model horizon for {sheet} row {row}.")
        if index < 0:
            if cell not in self.inputs.history:
                raise ValueError(f"Missing historical input at {_address(cell)}.")
            self.used_history.add(cell)
            return self.inputs.history[cell]
        if cell in self._values:
            return self._values[cell]

        formula = self.formulas[sheet].get(row)
        if formula is None:
            return self.assumption(cell)
        if cell in self._active:
            raise ValueError(f"Circular forecast dependency at {_address(cell)}.")

        self._active.add(cell)
        try:
            result = formula(ForecastPeriod(self, index))
            if not isfinite(result):
                raise ValueError(f"Nonfinite forecast result at {_address(cell)}.")
        except ZeroDivisionError as exc:
            raise ValueError(f"Forecast denominator is zero at {_address(cell)}.") from exc
        finally:
            self._active.remove(cell)
        self._values[cell] = result
        return result

    def number(self, sheet: str, address: str) -> float:
        row, column = coordinate_to_tuple(address)
        return self.value((sheet, row, column - FIRST_FORECAST_COLUMN))


def _formulas() -> dict[str, dict[int, ForecastFormula]]:
    return {
        "Revenue model": revenue_formulas(),
        "Income statement": income_formulas(),
        "Balance sheet": balance_formulas(),
        "Cash flow": cash_flow_formulas(),
        "Schedules": schedules_formulas(),
        "Equity": equity_formulas(),
    }


def _forecast_report(book: ForecastBook) -> ProjectionReport:
    inputs = book.inputs
    checks = check_statements(book.number)
    failed = [check.name for check in checks if not check.passed]
    if failed:
        raise ValueError(f"Projected statement reconciliation failed for {', '.join(failed)}.")

    years = tuple(
        ForecastYear(
            period_end=period_end,
            revenue_millions=book.value(("Income statement", 9, index)),
            operating_earnings_millions=book.value(("Income statement", 17, index)),
            net_earnings_millions=book.value(("Income statement", 26, index)),
            closing_cash_millions=book.value(("Cash flow", 56, index)),
            fcfe_millions=book.value(("Cash flow", 48, index)),
            operating_expense_detail_gap_millions=operating_expense_gap(ForecastPeriod(book, index)),
        )
        for index, period_end in enumerate(inputs.forecast_ends)
    )
    cash_flows = tuple(ForecastCashFlow(year.period_end, year.fcfe_millions) for year in years)
    intrinsic = value_fcfe(
        cash_flows, inputs.as_of, inputs.cost_of_equity, inputs.terminal_growth,
        inputs.opening_shares_millions,
    )
    target = value_fcfe(
        cash_flows, inputs.twelve_month_date, inputs.cost_of_equity, inputs.terminal_growth,
        book.value(("Equity", 21, 0)),
    )
    used_targets = {_address(cell) for cell in book.used_assumptions}
    for override in inputs.override_log:
        if "!" in override.target and not override.target.startswith("Cover!") and override.target not in used_targets:
            raise ValueError(f"Guided target {override.target} was unused in this forecast.")

    return ProjectionReport(
        as_of=inputs.as_of,
        source=inputs.source,
        override_log=inputs.override_log,
        years=years,
        formula_cells_matched=None,
        statement_period_checks=sum(check.compared for check in checks),
        sensitivity_cells_matched=None,
        assumptions_used=len(book.used_assumptions),
        intrinsic_usd_per_share=intrinsic,
        twelve_month_usd_per_share=target,
        notes=(
            "Forward backlog is outside this GD model's forecast scope.",
            "The expense detail gap is shown separately from the segment-based operating total.",
        ),
    )


def run_projection(inputs: GDInputs) -> ProjectionReport:
    """Calculate the standalone projection using the supplied dated inputs."""
    return _forecast_report(ForecastBook(inputs, _formulas()))


def required_history_cells(inputs: GDInputs) -> frozenset[str]:
    """Trace the historical cells used by this projection and its reconciliations."""
    book = ForecastBook(inputs, _formulas())
    _forecast_report(book)
    return frozenset(_address(cell) for cell in book.used_history)


def project_gd(path: Path) -> ProjectionReport:
    """Rebuild and verify the approved workbook's saved results."""
    formulas = _formulas()
    fixture = load_workbook_inputs(path, tuple(formulas))
    inputs, saved = fixture.inputs, fixture.saved
    book = ForecastBook(inputs, formulas)

    matched = 0
    for cell in sorted(fixture.forecast_formula_cells):
        sheet, row, index = cell
        if (sheet, row) in UNAVAILABLE_FORMULAS:
            continue
        if row not in formulas[sheet]:
            raise ValueError(f"Missing forecast implementation for {_address(cell)}.")
        address = f"{get_column_letter(FIRST_FORECAST_COLUMN + index)}{row}"
        expected = saved.number(sheet, address)
        actual = book.value(cell)
        if abs(actual - expected) > FORECAST_TOLERANCE:
            raise ValueError(f"Forecast mismatch at {_address(cell)}. Built {actual}, expected {expected}.")
        matched += 1

    report = _forecast_report(book)
    flows = tuple(ForecastCashFlow(year.period_end, year.fcfe_millions) for year in report.years)
    intrinsic = replay_horizon(
        saved, flows, inputs.as_of, inputs.cost_of_equity, inputs.terminal_growth,
        shares=inputs.opening_shares_millions, target_cell="B24", cover_cell="E18", header_row=26,
    )
    target = replay_horizon(
        saved, flows, inputs.twelve_month_date, inputs.cost_of_equity, inputs.terminal_growth,
        shares=book.value(("Equity", 21, 0)), target_cell="B46", cover_cell="E19", header_row=48,
    )
    return replace(
        report,
        formula_cells_matched=matched,
        sensitivity_cells_matched=intrinsic.sensitivity_cells_checked + target.sensitivity_cells_checked,
    )

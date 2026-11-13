"""Extract model inputs and separate comparison data from the approved GD workbook."""

from dataclasses import dataclass, replace
from datetime import timedelta
from io import BytesIO
from math import isclose, isfinite
from pathlib import Path

from openpyxl import load_workbook

from .gd_inputs import Cell, GDInputs, InputSource
from .gd_replay import BASELINE_DATE, BASELINE_SHA256, CachedWorkbook, read_baseline_bytes, read_cached_workbook


FIRST_FORECAST_COLUMN = 7  # G
FORECAST_YEARS = 6

# Fiscal-year headings are presentation, and the DoD budget row is context only.
YEAR_HEADER_ROWS = {
    "Income statement": {5, 36},
    "Balance sheet": {5, 54},
    "Cash flow": {4},
    "Equity": {5},
    "Schedules": {5, 56, 101},
}
CONTEXT_ROWS = {("Revenue model", 77)}


@dataclass(frozen=True)
class WorkbookFixture:
    inputs: GDInputs
    saved: CachedWorkbook
    forecast_formula_cells: set[Cell]


def _forecast_structure(contents: bytes, sheets: tuple[str, ...]) -> tuple[dict[Cell, float], set[Cell]]:
    assumptions = {}
    formula_cells = set()
    workbook = load_workbook(BytesIO(contents), read_only=True, data_only=False, keep_links=False)
    try:
        for sheet in sheets:
            year_headers = YEAR_HEADER_ROWS.get(sheet, ())
            rows = workbook[sheet].iter_rows(
                min_col=FIRST_FORECAST_COLUMN, max_col=FIRST_FORECAST_COLUMN + FORECAST_YEARS - 1
            )
            for row, cells in enumerate(rows, 1):
                if row in year_headers or (sheet, row) in CONTEXT_ROWS:
                    continue
                for index, cell in enumerate(cells):
                    value = cell.value
                    key = (sheet, row, index)
                    if cell.data_type == "f":
                        formula_cells.add(key)
                    elif type(value) in (int, float) and isfinite(value):
                        assumptions[key] = float(value)
    finally:
        workbook.close()
    return assumptions, formula_cells


def load_workbook_inputs(path: Path, sheets: tuple[str, ...]) -> WorkbookFixture:
    """Read the March baseline once and isolate forecast caches for comparison only."""
    contents = read_baseline_bytes(path)
    saved = read_cached_workbook(contents)
    assumptions, formula_cells = _forecast_structure(contents, sheets)
    history = {
        (sheet, row, index): float(value)
        for sheet in sheets
        for row, cells in enumerate(saved.sheets[sheet], 1)
        if row not in YEAR_HEADER_ROWS.get(sheet, ())
        for index, value in enumerate(cells[1:6], -5)
        if type(value) in (int, float) and isfinite(value)
    }

    as_of = saved.day("DCF", "G3")
    twelve_month_date = saved.day("DCF", "G4")
    if as_of != BASELINE_DATE or saved.day("Cover", "E11") != as_of:
        raise ValueError("Cover and DCF must both use the approved March 30 valuation date.")
    if twelve_month_date != as_of + timedelta(days=365):
        raise ValueError("The 12-month discount date disagrees with the workbook convention.")

    cost_of_equity = saved.number("Ke", "D32")
    if not isclose(cost_of_equity, saved.number("DCF", "B3")):
        raise ValueError("DCF cost of equity disagrees with the cost-of-equity schedule.")

    source = InputSource(
        kind="archived_workbook",
        citation=f"{path.name}, approved March 30 GD workbook. Historical B:F values and G:L forecast literals.",
        available_on=as_of,
        sha256=BASELINE_SHA256,
    )
    control_citations = {
        "cost_of_equity": "Ke!D32 and DCF!B3, archived cost of equity",
        "dividend_payout_ratio": "Equity!G10:L10, embedded 40% payout assumption",
        "terminal_growth": "DCF!B19, archived terminal growth assumption",
        "opening_shares_millions": "DCF!I3, archived opening share count in millions",
    }
    inputs = GDInputs(
        history=history,
        assumptions=assumptions,
        scenarios={row: int(saved.number("Cover", f"I{row}")) for row in range(14, 20)},
        cost_of_equity=cost_of_equity,
        dividend_payout_ratio=0.40,
        as_of=as_of,
        forecast_ends=tuple(saved.day("DCF", f"{column}14") for column in "DEFGHI"),
        twelve_month_date=twelve_month_date,
        terminal_growth=saved.number("DCF", "B19"),
        opening_shares_millions=saved.number("DCF", "I3"),
        source=source,
        provenance={
            key: replace(source, citation=f"{path.name}, {citation}.")
            for key, citation in control_citations.items()
        },
    )
    return WorkbookFixture(inputs, saved, formula_cells)

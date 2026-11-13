"""Prepare an archived GD report only after its editable workbook agrees."""

from dataclasses import dataclass, replace
from datetime import date
from hashlib import sha256
from io import BytesIO
from math import isclose
from pathlib import Path
from zipfile import BadZipFile

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException
from openpyxl.worksheet.formula import DataTableFormula

from .gd_inputs import MODEL_INPUT_SHEETS, SELECTORS, GDInputs, cell_address, load_guided_overrides
from .gd_projection import FORECAST_TOLERANCE, UNAVAILABLE_FORMULAS, ForecastBook, ProjectionReport, _formulas, run_projection
from .gd_replay import MODEL_SHEETS, CachedWorkbook
from .gd_workbook import WorkbookFixture, load_workbook_inputs
from .valuation import ForecastCashFlow, value_fcfe


@dataclass(frozen=True)
class GDHistoricalYear:
    period_end: date
    revenue_millions: float
    operating_earnings_millions: float
    net_earnings_millions: float
    closing_cash_millions: float


@dataclass(frozen=True)
class GDSegmentYear:
    period_end: date
    segment: str
    revenue_millions: float
    operating_earnings_millions: float

@dataclass(frozen=True)
class GDReportScenario:
    name: str
    selector: int
    projection: ProjectionReport


@dataclass(frozen=True)
class GDSensitivity:
    cost_of_equity_rates: tuple[float, ...]
    terminal_growth_rates: tuple[float, ...]
    values_usd_per_share: tuple[tuple[float | None, ...], ...]


@dataclass(frozen=True)
class GDWorkbookParity:
    workbook_sha256: str
    history_cells_matched: int
    forecast_cells_matched: int
    sensitivity_cells_matched: int


@dataclass(frozen=True)
class GDReportData:
    inputs: GDInputs
    projection: ProjectionReport
    history: tuple[GDHistoricalYear, ...]
    scenarios: tuple[GDReportScenario, ...]
    parity: GDWorkbookParity
    sensitivity: GDSensitivity
    segments: tuple[GDSegmentYear, ...]
    forward_shares_millions: float


def _read_export(contents: bytes, *, data_only: bool) -> CachedWorkbook:
    try:
        workbook = load_workbook(BytesIO(contents), data_only=data_only, read_only=True, keep_links=False)
        try:
            sheets = {sheet: tuple(workbook[sheet].values) for sheet in (*MODEL_SHEETS, "Run inputs")}
            return CachedWorkbook(sheets, workbook.epoch)
        finally:
            workbook.close()
    except (BadZipFile, InvalidFileException, KeyError) as exc:
        raise ValueError("The report requires the generated archived GD Excel workbook.") from exc


def _expect(workbook: CachedWorkbook, sheet: str, address: str, expected: object) -> None:
    if type(expected) in (int, float):
        actual = workbook.number(sheet, address)
        matches = isclose(actual, expected, rel_tol=0, abs_tol=FORECAST_TOLERANCE)
    elif isinstance(expected, date):
        actual = workbook.day(sheet, address)
        matches = actual == expected
    else:
        actual = workbook.value(sheet, address)
        matches = actual == expected
    if not matches:
        raise ValueError(f"Excel parity failed at {sheet}!{address}. Expected {expected!r}, found {actual!r}.")


def _require_formula(formulas: CachedWorkbook, sheet: str, address: str) -> None:
    value = formulas.value(sheet, address)
    if not isinstance(value, str) or not value.startswith("="):
        raise ValueError(f"The editable Excel formula is missing at {sheet}!{address}.")


def _verify_inputs(workbook: CachedWorkbook, formulas: CachedWorkbook, inputs: GDInputs) -> None:
    _expect(workbook, "Run inputs", "B4", inputs.source.sha256)
    _expect(formulas, "Cover", "E11", inputs.as_of)
    for sheet, address in (("Cover", "E11"), ("DCF", "G3"), ("Run inputs", "B2")):
        _expect(workbook, sheet, address, inputs.as_of)
    _expect(workbook, "DCF", "G4", inputs.twelve_month_date)
    for column, period_end in zip("DEFGHI", inputs.forecast_ends, strict=True):
        for row in (14, 38):
            _expect(workbook, "DCF", f"{column}{row}", period_end)
    _require_formula(formulas, "DCF", "G3")

    for values in (inputs.history, inputs.assumptions):
        for cell, expected in sorted(values.items()):
            sheet, address = cell_address(cell).rsplit("!", 1)
            _expect(workbook, sheet, address, expected)
    for address, selector in SELECTORS.items():
        _expect(workbook, "Cover", address.split("!", 1)[1], inputs.scenarios[selector])
    override_ke = any(entry.target == "cost_of_equity" for entry in inputs.override_log)
    for sheet, address, expected in (
        ("Run inputs", "B8", inputs.cost_of_equity if override_ke else None),
        ("Run inputs", "B9", inputs.dividend_payout_ratio),
        ("Ke", "D32", inputs.cost_of_equity),
        ("DCF", "B3", inputs.cost_of_equity),
        ("DCF", "B19", inputs.terminal_growth),
        ("DCF", "I3", inputs.opening_shares_millions),
    ):
        _expect(workbook, sheet, address, expected)
    for row, entry in enumerate(inputs.override_log, 28):
        for column, expected in zip("ABCD", (entry.target, entry.before, entry.value, entry.reason), strict=True):
            _expect(workbook, "Run inputs", f"{column}{row}", expected)
    if not inputs.override_log:
        _expect(workbook, "Run inputs", "A28", "Original archived assumptions.")


def _verify_forecast(
    workbook: CachedWorkbook, formulas: CachedWorkbook, fixture: WorkbookFixture,
    book: ForecastBook, projection: ProjectionReport, forward_shares: float,
) -> int:
    matched = 0
    for cell in sorted(fixture.forecast_formula_cells):
        sheet, address = cell_address(cell).rsplit("!", 1)
        _require_formula(formulas, sheet, address)
        if cell[:2] in UNAVAILABLE_FORMULAS:
            _expect(workbook, sheet, address, "n.a.")
        else:
            _expect(workbook, sheet, address, book.value(cell))
            matched += 1
    for sheet, address, expected in (
        ("DCF", "I4", forward_shares),
        ("DCF", "B24", projection.intrinsic_usd_per_share),
        ("DCF", "B46", projection.twelve_month_usd_per_share),
        ("Cover", "E18", projection.intrinsic_usd_per_share),
        ("Cover", "E19", projection.twelve_month_usd_per_share),
    ):
        _require_formula(formulas, sheet, address)
        _expect(workbook, sheet, address, expected)
    for address in ("E15", "E20", "E21", "E22", "E23"):
        _expect(workbook, "Cover", address, "n.a.")
    return matched


def _verify_grid(
    workbook: CachedWorkbook, formulas: CachedWorkbook, flows: tuple[ForecastCashFlow, ...],
    rate: float, discount_date: date, shares: float, header: int,
) -> GDSensitivity:
    anchor_address = f"C{header + 1}"
    anchor = formulas.value("DCF", anchor_address)
    if (
        not isinstance(anchor, DataTableFormula)
        or anchor.ref != f"C{header + 1}:I{header + 7}"
        or anchor.dt2D not in (True, "1")
        or (anchor.r1, anchor.r2) != ("B19", "B3")
    ):
        raise ValueError(f"The native two-variable sensitivity table is missing or modified at DCF!{anchor_address}.")

    growth_rates = tuple(0.005 * step for step in range(1, 8))
    discount_rates = tuple(rate + 0.005 * step for step in range(3, -4, -1))
    for column, growth in zip("CDEFGHI", growth_rates, strict=True):
        _expect(workbook, "DCF", f"{column}{header}", growth)
    grid = []
    for row, discount_rate in enumerate(discount_rates, header + 1):
        _expect(workbook, "DCF", f"B{row}", discount_rate)
        values = []
        for column, growth in zip("CDEFGHI", growth_rates, strict=True):
            value = value_fcfe(flows, discount_date, discount_rate, growth, shares) if discount_rate > growth else None
            _expect(workbook, "DCF", f"{column}{row}", value if value is not None else "n.a.")
            values.append(value)
        grid.append(tuple(values))
    return GDSensitivity(discount_rates, growth_rates, tuple(grid))


def load_gd_report_data(template: Path, workbook: Path, *, guided: Path | None = None) -> GDReportData:
    """Tie the archived report's model and inputs to the saved editable workbook."""
    fixture = load_workbook_inputs(template, MODEL_INPUT_SHEETS)
    inputs = load_guided_overrides(guided, fixture.inputs) if guided is not None else fixture.inputs
    projection = run_projection(inputs)
    book = ForecastBook(inputs, _formulas())
    forward_shares = book.number("Equity", "G21")
    contents = workbook.read_bytes()
    cached = _read_export(contents, data_only=True)
    formulas = _read_export(contents, data_only=False)
    _verify_inputs(cached, formulas, inputs)
    forecast_matched = _verify_forecast(cached, formulas, fixture, book, projection, forward_shares)
    flows = tuple(ForecastCashFlow(year.period_end, year.fcfe_millions) for year in projection.years)
    intrinsic_grid = _verify_grid(cached, formulas, flows, inputs.cost_of_equity, inputs.as_of, inputs.opening_shares_millions, 26)
    forward_grid = _verify_grid(cached, formulas, flows, inputs.cost_of_equity, inputs.twelve_month_date, forward_shares, 48)
    sensitivity_matched = sum(len(row) for grid in (intrinsic_grid, forward_grid) for row in grid.values_usd_per_share)

    first_forecast_year = inputs.forecast_ends[0].year
    history = tuple(
        GDHistoricalYear(
            date(first_forecast_year + index, 12, 31),
            book.value(("Income statement", 9, index)),
            book.value(("Income statement", 17, index)),
            book.value(("Income statement", 26, index)),
            book.value(("Balance sheet", 8, index)),
        )
        for index in range(-5, 0)
    )
    segments = tuple(
        GDSegmentYear(
            date(first_forecast_year + index, 12, 31), name,
            book.value(("Revenue model", revenue_row, index)),
            book.value(("Revenue model", earnings_row, index)),
        )
        for index in (-1, 0, 5)
        for name, revenue_row, earnings_row in (
            ("Aerospace", 60, 69), ("Marine Systems", 61, 70),
            ("Combat Systems", 62, 71), ("Technologies", 63, 72),
        )
    )
    remaining_overrides = tuple(entry for entry in inputs.override_log if entry.target not in SELECTORS)
    scenarios = tuple(
        GDReportScenario(
            name, selector,
            run_projection(replace(inputs, scenarios=dict.fromkeys(inputs.scenarios, selector), override_log=remaining_overrides)),
        )
        for name, selector in (("Bull segment case", 1), ("Bear segment case", 3))
    )
    return GDReportData(
        inputs=inputs,
        projection=replace(projection, formula_cells_matched=forecast_matched, sensitivity_cells_matched=sensitivity_matched),
        history=history,
        scenarios=scenarios,
        parity=GDWorkbookParity(sha256(contents).hexdigest(), len(inputs.history), forecast_matched, sensitivity_matched),
        sensitivity=intrinsic_grid,
        segments=segments,
        forward_shares_millions=forward_shares,
    )

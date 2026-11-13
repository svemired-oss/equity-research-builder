"""Build and audit the formula-driven workbook for GD's dated public-data model."""

import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import date
from hashlib import sha256
from io import BytesIO
from math import isclose, isfinite
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from openpyxl import load_workbook
from openpyxl.formula import Tokenizer

from .gd_cash_forecast import GDQ4CashForecast, forecast_gd_q4_cash, load_gd_cash_outlook
from .gd_comparables import GDComparables, run_gd_comparables
from .gd_evidence import GDHistoryEvidence, load_gd_history_evidence
from .gd_financing import GDFinancing, load_gd_financing
from .gd_forward_statements import GDForwardStatements, forecast_gd_statements
from .gd_fresh import audit_gd_history
from .gd_horizon import GDHorizonInputs, GDHorizonValuation, load_gd_horizon_inputs, value_gd_horizon
from .gd_inputs import InputSource
from .gd_interim import GDInterimEvidence, load_gd_interim
from .gd_operating_forecast import (
    CASES, GDOperatingForecast, forecast_gd_operations, load_gd_operating_assumptions, load_gd_segment_evidence,
)
from .gd_replay import read_baseline_bytes
from .gd_research import GDResearch, run_gd_research
from .gd_run_inputs import MarketInputs, load_market_inputs
from .gd_statement_inputs import load_gd_statement_assumptions, load_gd_statement_evidence
from .gd_valuation import GDValuation, GDValuationInputs, load_gd_valuation_inputs, value_gd


DATE_FORMAT = "mmm d, yyyy"
PRICE_FORMAT = '"$"#,##0.00;("$"#,##0.00);"-"'
PCT_FORMAT = '0.00%;(0.00%);"-"'
MILLIONS_FORMAT = '#,##0.0;(#,##0.0);"-"'
MULTIPLE_FORMAT = '0.00"x"'
SELECTOR = "E13"
SHEETS = (
    "Cover", "DCF", "Revenue model", "Income statement", "Balance sheet", "Cash flow", "Equity",
    "PP&E", "Debt", "NWC", "Comps", "Operating inputs", "Statement inputs", "Valuation inputs", "Sources", "Checks",
)
CellValue = str | int | float | bool | date | None


@dataclass
class WorkbookPlan:
    cells: dict[str, dict[str, tuple[CellValue, str | None, str | None]]] = field(
        default_factory=lambda: {sheet: {} for sheet in SHEETS},
    )
    expected: dict[str, dict[str, dict[str, CellValue]]] = field(default_factory=lambda: {case: {} for case in CASES})

    def put(self, sheet: str, address: str, value: CellValue, *, kind: str | None = None, fmt: str | None = None) -> None:
        if not re.fullmatch(r"[A-Z]{1,3}[1-9][0-9]*", address):
            raise ValueError(f"Expected a cell address, received {sheet}!{address}.")
        if type(value) is date:
            value, fmt = (value - date(1899, 12, 30)).days, fmt or DATE_FORMAT
        if isinstance(value, float) and not isfinite(value):
            raise ValueError(f"Nonfinite Excel input at {sheet}!{address}.")
        self.cells.setdefault(sheet, {})[address] = value, kind, fmt

    def expect(self, sheet: str, address: str, value: CellValue, *, case: str = "base") -> None:
        if type(value) is date:
            value = (value - date(1899, 12, 30)).days
        self.expected[case].setdefault(sheet, {})[address] = value


@dataclass(frozen=True)
class FreshWorkbookData:
    history: GDHistoryEvidence
    interim: GDInterimEvidence
    operations: GDOperatingForecast
    cash_forecast: GDQ4CashForecast
    financing: GDFinancing
    statements: dict[str, GDForwardStatements]
    valuation_inputs: GDValuationInputs
    horizon_inputs: GDHorizonInputs
    valuations: dict[str, GDValuation]
    targets: dict[str, GDHorizonValuation]
    market: MarketInputs
    comps: GDComparables
    research: GDResearch


def _data(as_of: date, inputs_dir: Path) -> FreshWorkbookData:
    def dated(name: str) -> Path:
        return inputs_dir / f"gd-{name}-{as_of.isoformat()}.json"

    history = load_gd_history_evidence(dated("history"))
    market = load_market_inputs(dated("market"))
    audit_gd_history(history, market)
    interim = load_gd_interim(dated("interim"))
    operations = forecast_gd_operations(interim, history, load_gd_segment_evidence(dated("segment-evidence")),
                                        load_gd_operating_assumptions(dated("operating-assumptions")))
    cash = forecast_gd_q4_cash(interim, load_gd_cash_outlook(dated("cash-outlook")))
    financing = load_gd_financing(dated("financing"))
    evidence = load_gd_statement_evidence(dated("statement-evidence"))
    assumptions = load_gd_statement_assumptions(dated("statement-assumptions"))
    statements = {case: forecast_gd_statements(interim, operations, cash, financing, evidence, assumptions, case=case)
                  for case in CASES}
    valuation_inputs = load_gd_valuation_inputs(dated("valuation"))
    horizon_inputs = load_gd_horizon_inputs(dated("horizon"))
    valuations = {case: value_gd(model, market, valuation_inputs) for case, model in statements.items()}
    targets = {case: value_gd_horizon(model, valuations[case], horizon_inputs) for case, model in statements.items()}
    return FreshWorkbookData(
        history, interim, operations, cash, financing, statements, valuation_inputs, horizon_inputs, valuations, targets,
        market, run_gd_comparables(as_of, inputs_dir), run_gd_research(as_of, inputs_dir),
    )


def _cover(book: WorkbookPlan, data: FreshWorkbookData, case: str) -> None:
    def put(address: str, value: CellValue, kind: str | None = None, fmt: str | None = None) -> None:
        book.put("Cover", address, value, kind=kind, fmt=fmt)

    put("C5", "GENERAL DYNAMICS Corporation", "title")
    put("C6", "NYSE: GD  ·  Aerospace & Defense", "subtitle")
    for row, label, value, fmt in (
        (11, "Valuation date", "='Statement inputs'!B6", DATE_FORMAT),
        (12, "Market close date", "=DCF!B4", DATE_FORMAT),
        (13, "Operating case", case.title(), None),
        (14, "12-month horizon", "=DCF!B39", DATE_FORMAT),
        (15, "Model rating", '=IF(ISNUMBER(E23),IF(E23>=\'Valuation inputs\'!B14,"Buy",IF(E23<=\'Valuation inputs\'!B15,"Sell","Hold")),"")', None),
        (17, "Reference close", "=DCF!B5", PRICE_FORMAT),
        (18, "Intrinsic value", "=DCF!B24", PRICE_FORMAT),
        (19, "12-month target", "=DCF!B46", PRICE_FORMAT),
        (20, "Intrinsic premium to close", '=IF(ISNUMBER(E18),E18/E17-1,"")', PCT_FORMAT),
        (21, "12-month price return", "=DCF!B47", PCT_FORMAT),
        (22, "Modeled dividend contribution", '=IF(AND(ISNUMBER(E23),ISNUMBER(E21)),E23-E21,"")', PCT_FORMAT),
        (23, "12-month total return", "=DCF!B48", PCT_FORMAT),
    ):
        put(f"D{row}", label, "section" if row == 23 else None)
        put(f"E{row}", value, "input" if row == 13 else "section" if row == 23 else "total", fmt)
    put("H11", "Trailing peer valuation", "section")
    for row, label, value, fmt in (
        (13, "Median EV / EBIT", "=Comps!B4", MULTIPLE_FORMAT),
        (14, "Median P / E", "=Comps!B5", MULTIPLE_FORMAT),
        (17, "EV / EBIT implied spot", "=Comps!B6", PRICE_FORMAT),
        (18, "P / E implied spot", "=Comps!B7", PRICE_FORMAT),
    ):
        put(f"H{row}", label)
        put(f"I{row}", value, "total", fmt)
    put("H20", "Valuation inputs", "section")
    put("H21", "Cost of equity")
    put("I21", "=DCF!B6", "total", PCT_FORMAT)
    put("H22", "Terminal growth")
    put("I22", "=DCF!B7", "total", PCT_FORMAT)
    put("D26", "Change the operating case in the yellow cell. Blue numbers are editable assumptions.", "note")
    put("D27", "The source tabs record the filing cutoff, analyst assumptions and data limits.", "note")
    for name, valuation in data.valuations.items():
        target = data.targets[name]
        for address, expected in (
            ("E13", name.title()), ("E15", data.research.inputs.rating_policy.rate(target.total_return)),
            ("E17", valuation.close.price), ("E18", valuation.intrinsic_value_per_share), ("E19", target.price_target),
            ("E20", valuation.premium_to_reference_close), ("E21", target.price_return), ("E23", target.total_return),
            ("I17", data.comps.implied_price_ev_to_ebit), ("I18", data.comps.implied_price_price_to_earnings),
        ):
            book.expect("Cover", address, expected, case=name)


def _sources(book: WorkbookPlan, data: FreshWorkbookData) -> None:
    sheet = "Sources"
    book.put(sheet, "A1", "Sources and modeling conventions", kind="title")
    for cell, label in zip("ABCDE", ("Data used", "Available", "Source type", "Citation", "Public link"), strict=True):
        book.put(sheet, f"{cell}4", label, kind="header")
    historical = {}
    for address, source in data.history.provenance.items():
        if source.kind != "sec_filing" or not source.url:
            continue
        column = address.rsplit("!", 1)[1][0]
        year = data.history.historical_years["BCDEF".index(column)]
        historical.setdefault(year, InputSource("sec_filing", f"GD original FY{year} Form 10-K", source.available_on, source.url))
    base = data.statements["base"]
    entries = [
        *((f"FY{year} actuals", source) for year, source in sorted(historical.items())),
        ("October 4 interim financials", data.interim.filing),
        ("October earnings release", data.interim.release),
        ("FY2026 capex and cash conversion", data.cash_forecast.outlook.source),
        ("Revenue driver outlook", data.operations.assumptions.delivery_source),
        *((f"{key.replace('_', ' ').title()} forecast", settings.source) for key, settings in data.operations.assumptions.segments.items()),
        *((f"Statement {key}", source) for key, source in base.assumptions.sources.items()),
        ("GD reference close", data.research.reference_close.source),
        ("Treasury yield", data.valuation_inputs.risk_free_rate.source),
        ("Equity risk premium", data.valuation_inputs.equity_risk_premium.source),
        ("Industry beta", data.valuation_inputs.unlevered_beta.source),
        *((f"Valuation {key.replace('_', ' ')}", source) for key, source in data.valuation_inputs.sources.items()),
        *((f"FY{row.year} seasonality", row.source) for row in data.horizon_inputs.seasonality),
        *((f"Note due {note.date.isoformat()}", note.source) for note in data.horizon_inputs.maturities),
        ("Dividend timing", data.horizon_inputs.dividend_source),
    ]
    for peer in data.comps.peers:
        company = peer.evidence
        entries.extend((
            (f"{company.ticker} FY2025", company.annual_source),
            (f"{company.ticker} Q3 filing", company.interim_source),
            (f"{company.ticker} reference close", company.close.source),
        ))
    for row, (label, source) in enumerate(entries, start=5):
        source_type = source.kind.replace("_", " ").capitalize()
        for column, value in zip("ABCDE", (label, source.available_on, source_type, source.citation, source.url), strict=True):
            book.put(sheet, f"{column}{row}", value, kind="note" if column in "DE" else "actual")

    row = len(entries) + 7
    for name, observations in (("Catalysts", data.research.inputs.catalysts), ("Risks", data.research.inputs.risks)):
        book.put(sheet, f"A{row}", name, kind="section")
        row += 1
        for observation in observations:
            for column, value in (("A", observation.title), ("B", observation.source.available_on),
                                  ("D", observation.detail), ("E", observation.source.url)):
                book.put(sheet, f"{column}{row}", value, kind="note")
            row += 1
        row += 1


def _checks(book: WorkbookPlan) -> None:
    sheet = "Checks"
    book.put(sheet, "A1", "Model reconciliation", kind="title")
    book.put(sheet, "A2", "USD millions unless otherwise specified. Zero indicates reconciliation.", kind="note")
    book.put(sheet, "A3", "Operating case")
    book.put(sheet, "B3", "=Cover!E13")
    for row, label in ((6, "Revenue matches segment model"), (7, "Operating earnings match segment model"),
                       (8, "Net earnings match cash flow"), (9, "Closing cash matches balance sheet"),
                       (10, "Cash roll-forward"), (11, "Assets less liabilities and equity"),
                       (13, "Spot sensitivity center, USD per share"),
                       (14, "Target sensitivity center, USD per share")):
        book.put(sheet, f"A{row}", label)
    for column, year in zip("GHIJKL", range(2026, 2032), strict=True):
        book.put(sheet, f"{column}5", year, kind="header")
        for row, formula in (
            (6, f"='Income statement'!{column}9-'Revenue model'!{column}64"),
            (7, f"='Income statement'!{column}17-'Revenue model'!{column}74"),
            (8, f"='Income statement'!{column}26-'Cash flow'!{column}3"),
            (9, f"='Cash flow'!{column}56-'Balance sheet'!{column}8"),
            (10, f"='Cash flow'!{column}55+'Cash flow'!{column}53-'Cash flow'!{column}56"),
            (11, f"='Balance sheet'!{column}22-'Balance sheet'!{column}47"),
        ):
            book.put(sheet, f"{column}{row}", formula, fmt="0.00")
            for case in CASES:
                book.expect(sheet, f"{column}{row}", 0, case=case)
    for row, matrix, headline in ((13, "F115", "B24"), (14, "F126", "B46")):
        formula = f'=IF(AND(ISNUMBER(DCF!{matrix}),ISNUMBER(DCF!{headline})),DCF!{matrix}-DCF!{headline},"Unavailable")'
        book.put(sheet, f"B{row}", formula, fmt="0.00")
        for case in CASES:
            book.expect(sheet, f"B{row}", 0, case=case)


def _compact_schedules(book: WorkbookPlan) -> None:
    # Keep the original schedule row numbers in the formula builders. Remove the
    # space they occupied inside GD V4's combined schedule when exporting separate tabs.
    first_rows = {"Debt": 79, "NWC": 110}
    cell_reference = re.compile(r"(\$?[A-Z]{1,3}\$?)([1-9][0-9]*)$")

    def address_for(sheet: str, address: str) -> str:
        first_row = first_rows.get(sheet)
        match = cell_reference.fullmatch(address)
        if first_row is None or match is None or int(match[2]) < first_row:
            return address
        return f"{match[1]}{int(match[2]) - first_row + 7}"

    def update_formula(sheet: str, formula: str) -> str:
        tokens = Tokenizer(formula)
        for token in tokens.items:
            if token.type != "OPERAND" or token.subtype != "RANGE":
                continue
            prefix, separator, addresses = token.value.rpartition("!")
            referenced_sheet = prefix.strip("'") if separator else sheet
            token.value = prefix + separator + ":".join(address_for(referenced_sheet, address)
                                                         for address in addresses.split(":"))
        return tokens.render()

    for sheet, records in book.cells.items():
        book.cells[sheet] = {
            address_for(sheet, address): (
                update_formula(sheet, value) if isinstance(value, str) and value.startswith("=") else value, kind, fmt,
            )
            for address, (value, kind, fmt) in records.items()
        }
    for case in book.expected.values():
        for sheet in first_rows.keys() & case.keys():
            case[sheet] = {address_for(sheet, address): value for address, value in case[sheet].items()}


def build_fresh_workbook_plan(
    template: Path, as_of: date, inputs_dir: Path, case: str = "base", *, data: FreshWorkbookData | None = None,
) -> dict:
    """Use the verified GD V4 design and the current Python model to prepare workbook formulas."""
    from .gd_fresh_excel_revenue import build_revenue
    from .gd_fresh_excel_statements import build_statements
    from .gd_fresh_excel_valuation import build_valuation

    if case not in CASES:
        raise ValueError(f"Unknown GD workbook operating case {case!r}.")
    baseline = read_baseline_bytes(template)
    template_workbook = load_workbook(BytesIO(baseline), read_only=True, keep_links=False)
    try:
        cover = template_workbook["Cover"]
        palette = {
            "navy": "#" + cover["D23"].fill.fgColor.rgb[-6:],
            "pale": "#" + cover["E21"].fill.fgColor.rgb[-6:],
            "editable": "#" + cover["I14"].fill.fgColor.rgb[-6:],
            "font": cover["E5"].font.name,
        }
    finally:
        template_workbook.close()
    data = data if data is not None else _data(as_of, inputs_dir)
    if data.interim.as_of != as_of:
        raise ValueError("Workbook data must use the requested cutoff.")
    book = WorkbookPlan()
    _cover(book, data, case)
    build_revenue(book, data)
    build_statements(book, data)
    build_valuation(book, data)
    _sources(book, data)
    _checks(book)
    _compact_schedules(book)
    return {
        "case": case, "palette": palette, "cells": book.cells, "expected": book.expected,
        "template_sha256": sha256(baseline).hexdigest(),
    }


def _verify(path: Path, plan: dict, case: str) -> int:
    expected = plan["expected"][case]
    workbook = load_workbook(path, read_only=True, data_only=True, keep_links=False)
    formulas = load_workbook(path, read_only=True, data_only=False, keep_links=False)
    try:
        if workbook.sheetnames != list(plan["cells"]) or workbook["Cover"][SELECTOR].value != case.title():
            raise ValueError("Workbook sheet layout or selected operating case differs from the generated model.")
        count = 0
        for sheet in workbook.sheetnames:
            pending = dict(expected.get(sheet, {}))
            for row in workbook[sheet]:
                for cell in row:
                    if cell.data_type == "e":
                        raise ValueError(f"Excel error in {sheet}!{cell.coordinate}: {cell.value}.")
                    if not hasattr(cell, "coordinate") or cell.coordinate not in pending:
                        continue
                    address = cell.coordinate
                    want = pending.pop(address)
                    got = cell.value
                    if isinstance(got, date) and isinstance(want, (int, float)):
                        got = (date(got.year, got.month, got.day) - date(1899, 12, 30)).days
                    if type(want) in (int, float):
                        matches = type(got) in (int, float) and isclose(got, want, rel_tol=1e-10, abs_tol=1e-6)
                    else:
                        matches = got == want or (want is None and got == "")
                    if not matches:
                        raise ValueError(f"{sheet}!{address} saved {got!r}, expected {want!r} for the {case} case.")
                    count += 1
            if any(want is not None for want in pending.values()):
                raise ValueError(f"Saved workbook is missing expected {sheet} cells.")

        for sheet, records in plan["cells"].items():
            required = {address for address, (value, _, _) in records.items()
                        if isinstance(value, str) and value.startswith("=")}
            for row in formulas[sheet]:
                for cell in row:
                    if cell.data_type == "f":
                        required.discard(cell.coordinate)
                        if isinstance(cell.value, str) and ("[" in cell.value or "TODAY(" in cell.value.upper()):
                            raise ValueError(f"Unexpected external link or moving date at {sheet}!{cell.coordinate}.")
            if required:
                raise ValueError(f"Missing editable formulas on {sheet}: {', '.join(sorted(required)[:6])}.")
    finally:
        workbook.close()
        formulas.close()
    with ZipFile(path) as package:
        if any(name.startswith(("xl/externalLinks/", "xl/webextensions/")) for name in package.namelist()):
            raise ValueError("The workbook must recalculate without an external link or vendor add-in.")
    return count


def verify_fresh_gd_workbook(path: Path, template: Path, as_of: date, inputs_dir: Path, case: str = "base") -> int:
    """Check a saved workbook against the selected dated Python forecast."""
    return _verify(path, build_fresh_workbook_plan(template, as_of, inputs_dir, case), case)


def export_fresh_gd_workbook(
    template: Path, output: Path, as_of: date, inputs_dir: Path, node: Path, node_modules: Path,
    *, case: str = "base", preview_dir: Path | None = None,
) -> int:
    """Author in Artifact Tool, verify the saved formula caches, and publish the workbook."""
    if output.suffix.lower() != ".xlsx" or output.exists():
        raise ValueError("Use a new .xlsx filename for the fresh model.")
    plan = build_fresh_workbook_plan(template, as_of, inputs_dir, case)
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent) as directory:
        stage = Path(directory)
        specification = stage / "model.json"
        specification.write_text(json.dumps(plan, allow_nan=False), encoding="utf-8")
        workbook = stage / "model.xlsx"
        script = Path(__file__).with_name("gd_fresh_excel.mjs")
        command = [str(node), str(script), str(specification), str(workbook), str(node_modules)]
        if preview_dir is not None:
            command.append(str(preview_dir))
        try:
            subprocess.run(command, check=True, capture_output=True, text=True, encoding="utf-8", errors="replace")
        except subprocess.CalledProcessError as exc:
            raise ValueError(f"Workbook authoring failed. {exc.stderr.strip() or exc.stdout.strip()}") from exc
        count = _verify(workbook, plan, case)
        if output.exists():
            raise ValueError("The output filename was created during generation. Use a new filename.")
        workbook.rename(output)
    return count

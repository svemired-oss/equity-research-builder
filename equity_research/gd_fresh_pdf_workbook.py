"""Verify the exact fresh Excel snapshot used to prepare a research PDF."""

from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from io import BytesIO
from math import isclose, isfinite
from pathlib import Path
import re
from zipfile import ZipFile

from openpyxl import load_workbook
from openpyxl.formula.tokenizer import Tokenizer, TokenizerError
from openpyxl.utils.datetime import WINDOWS_EPOCH, to_excel

from .gd_replay import CachedWorkbook


_OPTIONAL_SHEET_QUOTES = re.compile(r"^'([A-Za-z_][A-Za-z0-9_.]*)'!")
_EXTERNAL_PARTS = ("xl/externalLinks/", "xl/webextensions/", "xl/queryTables/")


@dataclass(frozen=True)
class VerifiedPDFWorkbook:
    sha256: str
    cells_matched: int
    _cache: CachedWorkbook

    def value(self, sheet: str, addr: str) -> object:
        return self._cache.value(sheet, addr)

    def number(self, sheet: str, addr: str) -> float:
        return self._cache.number(sheet, addr)

    def day(self, sheet: str, addr: str) -> date:
        return self._cache.day(sheet, addr)


def _formula_key(formula: str, location: str) -> tuple[tuple[str, str, str], ...]:
    try:
        tokens = Tokenizer(formula).items
    except TokenizerError as exc:
        raise ValueError(f"Cannot verify the formula at {location}.") from exc
    key = []
    for token in tokens:
        value = token.value
        if (token.type, token.subtype) == ("OPERAND", "RANGE"):
            if "[" in value or "]" in value:
                raise ValueError(f"External or unsupported range reference at {location}.")
            # Native Excel omits quotes around simple sheet names when it saves.
            value = _OPTIONAL_SHEET_QUOTES.sub(r"\1!", value).upper()
        elif (token.type, token.subtype) == ("FUNC", "OPEN"):
            value = value.upper()
            if value in {"TODAY(", "NOW("}:
                raise ValueError(f"Moving valuation date at {location}.")
        key.append((token.type, token.subtype, value))
    return tuple(key)


def _matches(saved: object, expected: object, *, cached: bool = False) -> bool:
    if type(expected) in (int, float):
        if isinstance(saved, date):
            saved = to_excel(saved, WINDOWS_EPOCH)
        return type(saved) in (int, float) and isfinite(saved) and (
            isclose(saved, expected, rel_tol=1e-10, abs_tol=1e-6) if cached else saved == expected
        )
    if expected in ("", None) and saved in ("", None):
        return True
    return type(saved) is type(expected) and saved == expected


def _verify_formula_view(workbook, cells: dict) -> set[tuple[str, str]]:
    matched = set()
    for sheet, planned in cells.items():
        worksheet = workbook[sheet]
        worksheet.reset_dimensions()
        pending = {
            address: value for address, (value, _, _) in planned.items() if value is not None and value != ""
        }
        for row in worksheet:
            for cell in row:
                if cell.value is None or cell.value == "":
                    continue
                address = cell.coordinate
                location = f"{sheet}!{address}"
                if address not in pending:
                    raise ValueError(f"Unexpected workbook content at {location}.")
                expected = pending.pop(address)
                formula_required = isinstance(expected, str) and expected.startswith("=")
                if formula_required:
                    if (cell.data_type != "f" or not isinstance(cell.value, str)
                            or _formula_key(cell.value, location) != _formula_key(expected, location)):
                        raise ValueError(f"Workbook formula differs from the dated plan at {location}.")
                elif cell.data_type in ("e", "f") or not _matches(cell.value, expected):
                    raise ValueError(f"Workbook input or label differs from the dated plan at {location}.")
                matched.add((sheet, address))
        if pending:
            raise ValueError(f"Missing workbook formula or input at {sheet}!{next(iter(pending))}.")
    return matched


def _read_cache(workbook, expected: dict) -> tuple[CachedWorkbook, set[tuple[str, str]]]:
    matched, sheets = set(), {}
    for sheet in workbook.sheetnames:
        worksheet = workbook[sheet]
        worksheet.reset_dimensions()
        pending = dict(expected.get(sheet, {}))
        values = []
        for row in worksheet:
            values.append(tuple(cell.value for cell in row))
            for cell in row:
                if cell.data_type == "e":
                    raise ValueError(f"Excel error in {sheet}!{cell.coordinate}: {cell.value}.")
                address = getattr(cell, "coordinate", None)
                if address not in pending:
                    continue
                wanted = pending.pop(address)
                if not _matches(cell.value, wanted, cached=True):
                    raise ValueError(f"Saved workbook value differs from the dated plan at {sheet}!{address}.")
                matched.add((sheet, address))
        if any(wanted is not None and wanted != "" for wanted in pending.values()):
            raise ValueError(f"Missing saved workbook value in {sheet}.")
        sheets[sheet] = tuple(values)
    return CachedWorkbook(sheets, workbook.epoch), matched


def _expected_caches(plan: dict, case: str) -> dict:
    expected = plan["expected"][case]
    dcf, cover = expected.get("DCF", {}), expected.get("Cover", {})
    cover_cells = plan["cells"].get("Cover", {})
    cover_dates = {"E11": "B3", "E12": "B4", "E14": "B39"}
    extra = {display: dcf[anchor] for display, anchor in cover_dates.items() if anchor in dcf and display in cover_cells}
    if "E22" in cover_cells and all(type(cover.get(key)) in (int, float) for key in ("E21", "E23")):
        extra["E22"] = cover["E23"] - cover["E21"]
    return {**expected, "Cover": {**cover, **extra}}


def load_verified_pdf_workbook(workbook: Path, plan: dict, case: str) -> VerifiedPDFWorkbook:
    """Verify a GD workbook on the report's selected operating case."""
    if case not in plan["expected"]:
        raise ValueError(f"Unknown PDF operating case {case!r}.")
    cells = plan["cells"]
    _, kind, fmt = cells["Cover"]["E13"]
    selected = {**cells, "Cover": {**cells["Cover"], "E13": (case.title(), kind, fmt)}}
    return load_verified_workbook(workbook, selected, _expected_caches(plan, case))


def load_verified_workbook(workbook: Path, cells: dict, expected: dict) -> VerifiedPDFWorkbook:
    """Verify literal inputs, formulas and expected caches from one byte snapshot."""
    contents = workbook.read_bytes()
    with ZipFile(BytesIO(contents)) as package:
        if any(name.startswith(_EXTERNAL_PARTS) or name == "xl/connections.xml" for name in package.namelist()):
            raise ValueError("The report workbook contains an external connection or add-in.")
    formulas = load_workbook(BytesIO(contents), read_only=True, data_only=False, keep_links=False)
    try:
        if formulas.sheetnames != list(cells) or formulas.epoch != WINDOWS_EPOCH:
            raise ValueError("The workbook sheet layout or date system differs from the dated plan.")
        if formulas.defined_names:
            raise ValueError("The report workbook contains unexpected defined names.")
        formula_matches = _verify_formula_view(formulas, cells)
    finally:
        formulas.close()
    cached = load_workbook(BytesIO(contents), read_only=True, data_only=True, keep_links=False)
    try:
        snapshot, cache_matches = _read_cache(cached, expected)
    finally:
        cached.close()
    return VerifiedPDFWorkbook(sha256(contents).hexdigest(), len(formula_matches | cache_matches), snapshot)

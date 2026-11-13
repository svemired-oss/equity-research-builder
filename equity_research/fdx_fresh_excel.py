"""Export and verify the formula-driven workbook for FedEx's dated research."""

from datetime import date
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

from .fdx_current import run_current_fdx
from .fdx_current_valuation import run_fdx_valuation
from .fdx_forward_statements import run_fdx_statements
from .fdx_fresh_excel_evidence import add_fdx_comparables, add_fdx_sources
from .fdx_statements import WorkbookPlan
from .gd_fresh_pdf_workbook import VerifiedPDFWorkbook, load_verified_workbook


CASES = ("bear", "base", "bull")
SHEETS = ("Cover", "Assumptions", "DCF", "Comps", "Revenue", "Income statement", "Balance sheet",
          "Cash flow", "PP&E", "Debt schedule", "Checks", "Actuals", "Sources")
PALETTE = {"navy": "#003366", "pale": "#D6E4F0", "editable": "#FFF2CC", "font": "Calibri"}


def build_fresh_fdx_workbook_plan(as_of: date, inputs_dir: Path, *, case: str = "base",
                                 guided: Path | None = None) -> dict:
    """Assemble one workbook and independent formula results for all operating cases."""
    from .fdx_fresh_excel_statements import add_fdx_forecast
    from .fdx_fresh_excel_valuation import add_fdx_valuation

    if case not in CASES:
        raise ValueError(f"Unknown FedEx operating case {case!r}.")
    current = run_current_fdx(as_of, inputs_dir, guided=guided)
    valuation = run_fdx_valuation(as_of, inputs_dir, guided=guided)
    statements = {name: run_fdx_statements(as_of, inputs_dir, case=name, guided=guided) for name in CASES}

    plans = {}
    for name in CASES:
        plan = WorkbookPlan(cells={sheet: {} for sheet in SHEETS})
        add_fdx_forecast(plan, inputs_dir, as_of, current, statements, name)
        add_fdx_valuation(plan, inputs_dir, as_of, valuation, statements, name)
        add_fdx_comparables(plan, inputs_dir, as_of, valuation)
        add_fdx_sources(plan, inputs_dir, as_of, valuation)
        plans[name] = plan
    selected = plans[case]
    if tuple(selected.cells) != SHEETS or any(not cells for cells in selected.cells.values()):
        raise ValueError("The FedEx workbook layout is incomplete.")
    for name, plan in plans.items():
        cells = {**plan.cells, "Cover": {**plan.cells["Cover"], "B5": selected.cells["Cover"]["B5"]}}
        if cells != selected.cells or plan.cells["Cover"]["B5"][0] != name.title():
            raise ValueError("All operating cases must use the same workbook and a single case selector.")
    return {
        "case": case,
        "palette": PALETTE,
        "cells": selected.cells,
        "expected": selected.expected,
        "scenarioExpected": {name: plan.expected for name, plan in plans.items()},
        "scenarioControls": {name: name.title() for name in CASES},
        "recalculationProbe": {
            "case": "base",
            "input": {"sheet": "Assumptions", "address": "I54", "delta": 0.005},
            "changed": [{"sheet": "Revenue", "address": "I13"}, {"sheet": "Cover", "address": "B10"}],
            "unchanged": [{"sheet": "Actuals", "address": "B7"}],
        },
    }


def load_verified_fdx_workbook(workbook: Path, as_of: date, inputs_dir: Path, *, case: str = "base",
                               guided: Path | None = None) -> VerifiedPDFWorkbook:
    """Verify a single workbook byte snapshot and return its values and fingerprint."""
    plan = build_fresh_fdx_workbook_plan(as_of, inputs_dir, case=case, guided=guided)
    return load_verified_workbook(workbook, plan["cells"], plan["expected"])


def verify_fresh_fdx_workbook(workbook: Path, as_of: date, inputs_dir: Path, *, case: str = "base",
                              guided: Path | None = None) -> int:
    """Compare saved formulas, inputs and calculations against the dated model."""
    return load_verified_fdx_workbook(workbook, as_of, inputs_dir, case=case, guided=guided).cells_matched


def export_fresh_fdx_workbook(
    output: Path, as_of: date, inputs_dir: Path, node: Path, node_modules: Path, *,
    case: str = "base", guided: Path | None = None, preview_dir: Path | None = None,
) -> int:
    """Author the workbook in Artifact Tool and verify it before publishing."""
    if output.suffix.lower() != ".xlsx" or output.exists():
        raise ValueError("Choose a new .xlsx filename for the FedEx workbook.")
    plan = build_fresh_fdx_workbook_plan(as_of, inputs_dir, case=case, guided=guided)
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent) as directory:
        stage = Path(directory)
        specification = stage / "model.json"
        specification.write_text(json.dumps(plan, allow_nan=False), encoding="utf-8")
        workbook = stage / "model.xlsx"
        command = [str(node), str(Path(__file__).with_suffix(".mjs")), str(specification), str(workbook), str(node_modules)]
        if preview_dir is not None:
            command.append(str(preview_dir))
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if result.returncode:
            raise ValueError(f"Workbook authoring failed. {result.stderr.strip() or result.stdout.strip()}")
        count = load_verified_workbook(workbook, plan["cells"], plan["expected"]).cells_matched
        if output.exists():
            raise ValueError("The output filename was created during generation. Use a new filename.")
        workbook.rename(output)
    return count

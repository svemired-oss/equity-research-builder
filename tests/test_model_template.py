"""Offline coverage of the detailed operating template."""

import json
from pathlib import Path
from shutil import which

from openpyxl import load_workbook
import pytest

from equity_research.model_template import SHEETS, export_operating, workbook_plan
from equity_research.operating_research import prepare_operating_model, revenue_candidates

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def model():
    data = json.loads((FIXTURES / "operating.json").read_text())
    detail = json.loads((FIXTURES / "template_detail.json").read_text())
    packet = data["packet"]
    packet["actuals"]["periods"][-1]["metrics"].update(detail)
    return prepare_operating_model(packet, data["treasury"], data["quote"],
                                   data["selection"], revenue_candidates(packet))


def test_template_retains_filed_detail_and_sources(model):
    plan = workbook_plan(model)
    cells = plan["cells"]
    assert tuple(cells)[:len(SHEETS)] == SHEETS
    assert cells["Balance Sheet"]["L6"][0] == 15
    assert cells["Balance Sheet"]["L29"][0] == 60
    assert cells["Balance Sheet"]["L31"][0] == 1
    assert cells["Balance Sheet"]["B4"][0] == "='Cash Flow Statement'!B25"
    assert cells["Cash Flow Statement"]["B25"][0] == "=SUM(B24,B10)"
    for tab, records in cells.items():
        for address, (value, kind, _) in records.items():
            if kind == "input" or isinstance(value, (float, int)):
                target = plan["inputSources"][tab][address]
                assert cells["Sources"][target][0] == f"{tab}!{address}"


@pytest.mark.skipif(not which("soffice"), reason="LibreOffice is installed in CI")
def test_detailed_template_recalculates_and_exports(model, tmp_path):
    workbook, report = export_operating(model, tmp_path)
    assert report.read_bytes().startswith(b"%PDF")
    cached = load_workbook(workbook, read_only=True, data_only=True)
    try:
        for col in "BCDEF":
            for row in range(4, 11):
                assert cached["Checks"][f"{col}{row}"].value == pytest.approx(0, abs=1e-6)
        for address in ("J4", "K4", "L4", "B11", "B12"):
            assert cached["Checks"][address].value == pytest.approx(0, abs=1e-6)
        revenue = cached["Income Statement"]["B4"].value
        assert cached["Balance Sheet"]["B6"].value == pytest.approx(15 * revenue / 120)
        assert cached["Balance Sheet"]["B29"].value > 60
        assert cached["Cover"]["B7"].value == pytest.approx(model["scenarios"]["Base"].value_per_share)
    finally:
        cached.close()
    formula_book = load_workbook(workbook)
    try:
        margin = formula_book["Assumptions"]["B3"]
        assert margin.hyperlink.location.startswith("'Sources'!")
        assert margin.font.color.rgb.endswith("154DA1")
        assert formula_book["Cover"]["B7"].font.color.rgb.endswith("000000")
    finally:
        formula_book.close()


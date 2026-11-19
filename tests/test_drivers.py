"""Offline filing fixtures with synthetic economics and no external inputs."""
import json
from pathlib import Path
from shutil import which

import pytest

from equity_research.drivers import FALLBACK, PLUGINS, collect, choose, forecast
from equity_research.operating_research import prepare_operating_model, revenue_candidates
from equity_research.model_template import export_operating

FIX = Path(__file__).parent/"fixtures"


def inputs(kind):
    data = json.loads((FIX/"operating.json").read_text())
    rows = json.loads((FIX/"drivers.json").read_text())[kind]
    rows = [["Fiscal year", "2023", "2024", "2025"], *rows]
    html = "<table>"+"".join("<tr>"+"".join(f"<td>{v}</td>" for v in row)+"</tr>" for row in rows)+"</table>"
    class Client:
        def get_text(self, url):
            return html
    collect(data["packet"], Client())
    data["selection"].update(driver_type=kind, driver_passage="annual-table-1" if kind != FALLBACK else "none")
    return data


@pytest.mark.parametrize("kind", PLUGINS)
def test_filing_driver(kind):
    data = inputs(kind)
    driver = choose(data["packet"], data["selection"])
    assert driver["type"] == kind
    if kind != FALLBACK:
        assert len(forecast(driver)) == 5
        assert forecast(driver)[0] > 120
        assert driver["years"] == ["2023", "2024", "2025"]


def test_missing_period_and_mismatched_xbrl_stop_physical_driver():
    data = inputs("volume_yield")
    packet = data["packet"]
    passage = packet["driver_passages"]["annual-table-1"]
    passage["rows"][1][1] = "99"
    assert choose(packet, data["selection"])["type"] == FALLBACK
    del passage["rows"][1][-1]
    assert choose(packet, data["selection"])["type"] == FALLBACK


def test_first_year_disclosed_driver_guidance():
    data = inputs("units_price")
    packet = data["packet"]
    rows = packet["driver_passages"]["annual-table-1"]["rows"]
    for row, cell in zip(rows, ("FY2026 guidance", "14", ""), strict=True):
        row.append(cell)
    driver = choose(packet, data["selection"])
    assert driver["factors"][0]["guidance"] == 14
    assert forecast(driver)[0] == pytest.approx(14*10*(1+.015*(1-.5)))


@pytest.mark.skipif(not which("soffice"), reason="Requires LibreOffice")
@pytest.mark.parametrize("kind", ["stores_sales", "volume_yield"])
def test_review_formula_path(kind, tmp_path):
    data = inputs(kind)
    packet = data["packet"]
    model = prepare_operating_model(packet, data["treasury"], data["quote"], data["selection"], revenue_candidates(packet))
    workbook, pdf = export_operating(model, tmp_path)
    from openpyxl import load_workbook
    from pypdf import PdfReader
    book = load_workbook(workbook, read_only=True, data_only=True)
    try:
        assert book["Income Statement"]["B4"].value == pytest.approx(model["revenue"][0])
        assert model["driver"]["type"] == kind
    finally:
        book.close()
    assert kind in " ".join(p.extract_text() for p in PdfReader(pdf).pages)


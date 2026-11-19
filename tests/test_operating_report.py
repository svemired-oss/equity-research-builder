from io import BytesIO
from shutil import which

import pytest
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen.canvas import Canvas

from equity_research import operating_report
from equity_research.model_template import export_operating
from equity_research.report_evidence import annual_passages
from test_market_valuation import fixture_model


def test_business_and_risk_passages_are_scoped():
    html = (
        "<p>Item 1. Business</p><p>Our customers compete in numerous industry markets across the world.</p>"
        "<p>Item 1A. Risk Factors</p><p>Customer demand and component supply could materially affect operating results.</p>"
        "<p>Item 2. Properties</p><p>We sell office property in unrelated international markets.</p>"
    )
    result = annual_passages(html)
    assert "customers compete" in result["industry"][0]
    assert "component supply" in result["risk"][0]
    assert all("property" not in text for passages in result.values() for text in passages)


@pytest.mark.skipif(not which("soffice"), reason="Requires LibreOffice")
def test_pdf_names_every_workbook_segment_and_rejects_wrong_number(tmp_path):
    model = fixture_model()
    model["report_evidence"] = [{"kind":"catalyst","filed_on":"2026-06-15",
                                "text":"The company announced a July 2026 delivery schedule.",
                                "url":"https://www.sec.gov/Archives/edgar/data/123/000000012326000001/release.htm"}]
    workbook, pdf = export_operating(model,tmp_path,report_writer=lambda sheet:[2,1,3])
    sheet = operating_report.fact_sheet(workbook.read_bytes())
    reader = PdfReader(pdf)
    text = " ".join(" ".join(p.extract_text().split()) for p in reader.pages)
    for index in range(sheet["segments"]):
        assert sheet["facts"][f"segment_{index}"]["value"] in text
    assert "2026-06-15" in text
    operating_report.verify_report(workbook.read_bytes(),pdf.read_bytes())

    # Stamp a number that occurs nowhere in the fact sheet onto the actual PDF.
    buffer = BytesIO()
    canvas = Canvas(buffer)
    canvas.drawString(75, 36, "Revenue will reach $987654321.56 million.")
    canvas.save()
    buffer.seek(0)
    writer = PdfWriter(clone_from=reader)
    writer.pages[-1].merge_page(PdfReader(buffer).pages[0])
    bad_report = BytesIO()
    writer.write(bad_report)
    with pytest.raises(ValueError,match="unmatched claim.*987654321"):
        operating_report.verify_report(workbook.read_bytes(),bad_report.getvalue())


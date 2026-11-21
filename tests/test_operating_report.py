from io import BytesIO
from shutil import which

import pytest
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen.canvas import Canvas

from equity_research import operating_report
from equity_research.model_template import export_operating
from equity_research.report_evidence import annual_passages
from test_market_valuation import fixture_model


def test_business_industry_and_risk_passages_follow_the_stage_4a_rules():
    p = lambda text: f"<p>{text}</p>"
    html = "".join((
        p("Item 1. Business."), p("General"),
        p("When used in this report, the terms we and our mean Example Corp and its subsidiaries, collectively, "
          "unless the context indicates otherwise."),
        p("Our purpose is to delight every customer, and our values guide everything we do across the whole company."),
        p("Example Corp designs and sells industrial pumps and valves through two segments, Fluid Systems and Flow "
          "Controls, to municipal and industrial customers in 40 countries."),
        p("Sustainability"),
        p("We aim to reach net-zero emissions by 2040 and publish an annual ESG report describing our progress on "
          "environmental and social commitments."),
        p("Competition"),
        p("The pump industry is fragmented and competitive. We compete with global manufacturers and regional "
          "specialists on price, efficiency, service coverage and delivery times."),
        p("Item 1A. Risk Factors"), p("RISKS RELATED TO OUR OPERATIONS"),
        p("Fluctuations in foreign currency exchange rates could adversely affect our financial results."),
        p("A slowdown in municipal water infrastructure spending could reduce demand for our pumps and valves."),
        p("Delays at our Fluid Systems foundries could limit pump deliveries to industrial customers."),
        p("Price competition from regional pump specialists could reduce our margins on municipal orders."),
        p("Loss of service coverage in key countries could weaken demand for Flow Controls valves."),
        p("Failure to win efficiency certifications for our pumps may reduce sales to industrial customers."),
        p("Municipal budgets depend on tax receipts and bond issuance, which vary with local economic conditions and "
          "political priorities, and orders can be deferred for several quarters when budgets tighten."),
        p("Item 2. Properties"), p("We sell office property in unrelated international markets near our headquarters."),
    ))
    result = annual_passages(html)
    business = [text for _, text in result["business"]]
    assert business[0].startswith("Example Corp designs and sells industrial pumps")
    assert not any(word in " ".join(business) for word in ("purpose", "the terms", "net-zero"))
    assert result["industry"] == [("Competition", "The pump industry is fragmented and competitive. We compete with "
                                   "global manufacturers and regional specialists on price, efficiency, service coverage "
                                   "and delivery times.")]
    risks = [text for _, text in result["risk"]]
    # The five most specific headings, in filing order: the generic currency risk and the body paragraph are left out.
    assert len(risks) == 5 and risks[0].startswith("A slowdown in municipal water infrastructure")
    assert not any("foreign currency" in r or "Municipal budgets" in r for r in risks)
    assert all("property" not in text for rows in result.values() for _, text in rows)


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


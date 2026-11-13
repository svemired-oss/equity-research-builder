"""Render a dated General Dynamics research report from a matched Excel run."""

from dataclasses import dataclass
from io import BytesIO
from math import isclose
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from xml.sax.saxutils import escape

from pypdf import PdfReader
from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfgen.canvas import Canvas
from reportlab.platypus import Paragraph, Table, TableStyle

from .gd_report_data import GDReportData, load_gd_report_data


NAVY = HexColor("#133153")
INK = HexColor("#15283D")
MUTED = HexColor("#465970")
BLUE = HexColor("#3974A3")
GOLD = HexColor("#C28B32")
RULE = HexColor("#DAE3EB")
PALE = HexColor("#EEF4F8")
ACCENT = HexColor("#A33839")
MARGIN = 42
WIDTH, HEIGHT = letter
BODY = WIDTH - 2 * MARGIN
PAGE_COUNT = 4

SOURCES = (
    ("General Dynamics FY2025 Form 10-K", "Jan 30, 2026", "https://www.sec.gov/Archives/edgar/data/40533/000004053326000006/gd-20251231.htm"),
    ("GD FY2025 results and aerospace supplemental data", "Jan 28, 2026", "https://www.sec.gov/Archives/edgar/data/40533/000004053326000003/gd-20251231exhibit991.htm"),
    ("GD Full-Year 2025 Highlights and Outlook Update", "Jan 28, 2026", "https://s22.q4cdn.com/891946778/files/doc_financials/2025/q4/2025-Highlights-Outlook.pdf#page=8"),
    ("GAMA 2025 year-end aircraft shipment report", "Feb 18, 2026", "https://gama.aero/wp-content/uploads/2025ShipmentReport_02-18-2026.pdf"),
    ("GAO Columbia-class submarine assessment", "Sep 30, 2024", "https://www.gao.gov/products/gao-24-107732"),
    ("GD quarterly dividend declaration", "Mar 9, 2026", "https://investorrelations.gd.com/news/press-release-details/2026/General-Dynamics-Board-Declares-Dividend/default.aspx"),
)

MANAGEMENT_OUTLOOK = {
    "Aerospace": "~$13.6bn / ~14.0%",
    "Marine Systems": "$17.3-$17.7bn / ~7.3%",
    "Combat Systems": "$9.6-$9.7bn / ~14.1%",
    "Technologies": "~$13.8bn / ~9.2%",
}


@dataclass(frozen=True)
class GDPDFExport:
    output: Path
    pages: int
    data: GDReportData


def _paragraph(text: str, *, size: float = 10, leading: float = 14, color=INK) -> Paragraph:
    return Paragraph(text, ParagraphStyle("Body", fontName="Helvetica", fontSize=size, leading=leading, textColor=color))


class _Page:
    def __init__(
        self, canvas: Canvas, number: int, date_label: str, section: str, title: str,
        *, footer: str = "Archived research model", page_count: int = PAGE_COUNT,
        identity: str = "GENERAL DYNAMICS   /   NYSE: GD", accent=ACCENT,
    ):
        self.canvas = canvas
        self.number = number
        canvas.setStrokeColor(accent)
        canvas.setLineWidth(1.5)
        canvas.line(MARGIN, HEIGHT - 34, WIDTH - MARGIN, HEIGHT - 34)
        canvas.setFillColor(NAVY)
        canvas.setFont("Helvetica-Bold", 9)
        canvas.drawString(MARGIN, HEIGHT - 54, identity)
        canvas.setFont("Helvetica", 9)
        canvas.drawRightString(WIDTH - MARGIN, HEIGHT - 54, section.upper())
        canvas.setFont("Helvetica-Bold", 29)
        canvas.drawString(MARGIN, HEIGHT - 95, title)
        canvas.setStrokeColor(RULE)
        canvas.setLineWidth(0.6)
        canvas.line(MARGIN, 43, WIDTH - MARGIN, 43)
        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica", 8)
        canvas.drawString(MARGIN, 28, f"{date_label}  /  {footer}")
        canvas.drawRightString(WIDTH - MARGIN, 28, f"{number} / {page_count}")
        self.y = HEIGHT - 110

    def paragraph(self, text: str, *, small: bool = False, gap: float = 9) -> None:
        item = _paragraph(text, size=8.5 if small else 10, leading=11.5 if small else 14, color=MUTED if small else INK)
        _, height = item.wrap(BODY, HEIGHT)
        item.drawOn(self.canvas, MARGIN, self.y - height)
        self.y -= height + gap

    def heading(self, text: str) -> None:
        self.canvas.setFillColor(NAVY)
        self.canvas.setFont("Helvetica-Bold", 13)
        self.canvas.drawString(MARGIN, self.y - 12, text)
        self.y -= 22

    def table(self, rows: list, widths: list, *, extra: tuple = (), align: str = "RIGHT") -> None:
        item = Table(rows, colWidths=widths, hAlign="LEFT")
        item.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
            ("TEXTCOLOR", (0, 1), (-1, -1), INK),
            ("FONTSIZE", (0, 0), (-1, -1), 8.6),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (1, 0), (-1, -1), align),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [white, PALE]),
            ("TOPPADDING", (0, 0), (-1, -1), 5.5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5.5),
            ("LINEBELOW", (0, -1), (-1, -1), 0.6, RULE),
            *extra,
        ]))
        _, height = item.wrap(BODY, HEIGHT)
        item.drawOn(self.canvas, MARGIN, self.y - height)
        self.y -= height + 12

    def finish(self) -> None:
        if self.y < 50:
            raise ValueError(f"Report page {self.number} exceeds its printable area.")
        self.canvas.showPage()


def _usd(value: float) -> str:
    return f"${value:,.2f}"


def _date_label(value) -> str:
    return f"{value:%B} {value.day}, {value.year}"


def _margin(earnings: float, revenue: float) -> str:
    return f"{earnings / revenue:.1%}" if revenue else "n.a."


def _revenue_chart(page: _Page, data: GDReportData) -> None:
    page.heading("Revenue trajectory")
    periods = (*data.history, *data.projection.years)
    values = [period.revenue_millions / 1000 for period in periods]
    canvas = page.canvas
    left, top, height = MARGIN + 32, page.y - 15, 108
    bottom = top - height
    width = BODY - 38
    step = width / len(values)
    ceiling = max(80, 20 * (int(max(values) / 20) + 1))
    for level in range(0, ceiling + 1, ceiling // 4):
        y = bottom + height * level / ceiling
        canvas.setStrokeColor(RULE)
        canvas.setLineWidth(0.45)
        canvas.line(left, y, left + width, y)
        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica", 8)
        canvas.drawRightString(left - 7, y - 2.5, str(level))
    for index, (period, value) in enumerate(zip(periods, values, strict=True)):
        x = left + (index + 0.5) * step
        bar_height = height * value / ceiling
        actual = index < len(data.history)
        canvas.setFillColor(NAVY if actual else BLUE)
        canvas.rect(x - 14, bottom, 28, bar_height, stroke=0, fill=1)
        canvas.setFillColor(INK)
        canvas.setFont("Helvetica-Bold", 8.5)
        canvas.drawCentredString(x, bottom + bar_height + 5, f"{value:.2f}")
        canvas.setFont("Helvetica", 8.5)
        canvas.drawCentredString(x, bottom - 14, f"{period.period_end.year}{'A' if actual else 'E'}")
    page.y = bottom - 29
    page.paragraph("USD billions. A denotes reported actuals and E denotes the selected workbook forecast. [1, 7]", small=True)


def _investment_page(canvas: Canvas, data: GDReportData, date_label: str) -> None:
    page = _Page(canvas, 1, date_label, "Investment view", "General Dynamics")
    page.paragraph(f"Archived equity research as of {date_label}. USD per share unless stated.")
    measures = (
        ("Intrinsic value", _usd(data.projection.intrinsic_usd_per_share)),
        ("12-month model target", _usd(data.projection.twelve_month_usd_per_share)),
        ("Investment rating", "Unavailable"),
    )
    card_width = (BODY - 20) / 3
    for index, (label, value) in enumerate(measures):
        x = MARGIN + index * (card_width + 10)
        canvas.setFillColor(NAVY if index == 0 else PALE)
        canvas.rect(x, page.y - 65, card_width, 65, stroke=0, fill=1)
        canvas.setFillColor(white if index == 0 else NAVY)
        canvas.setFont("Helvetica", 9)
        canvas.drawString(x + 12, page.y - 18, label)
        canvas.setFont("Helvetica-Bold", 23 if index < 2 else 18)
        canvas.drawString(x + 12, page.y - 46, value)
    page.y -= 77
    page.paragraph("The archived quote lacks a verified observation date. Rating, upside and total return are unavailable. [7]", small=True)
    page.heading("Investment thesis")
    page.paragraph("<b>Submarine backlog supports Marine growth.</b> Marine ended FY2025 with $52.34bn in backlog. "
                   "Higher submarine production can lift revenue, with the margin outcome dependent on labor and supplier execution. [1, 5]")
    page.paragraph("<b>Gulfstream volume and mix matter for Aerospace.</b> Deliveries rose to 158 from 136 in FY2025. "
                   "Management projected roughly 160 units and a 14% Aerospace margin for FY2026. Large-cabin mix and service activity matter for the margin trajectory. [1, 2, 3]")
    first, last = data.projection.years[0], data.projection.years[-1]
    page.paragraph(f"<b>Cash conversion drives the valuation.</b> Modeled free cash flow to equity is "
                   f"${first.fcfe_millions / 1000:.2f}bn in FY2026 and ${last.fcfe_millions / 1000:.2f}bn in FY2031. "
                   "Contract timing, working capital and investment determine how much operating growth turns into equity cash flow. [1, 7]")
    _revenue_chart(page, data)
    page.finish()


def _operating_page(canvas: Canvas, data: GDReportData, date_label: str) -> None:
    page = _Page(canvas, 2, date_label, "Financial outlook", "Operating model")
    page.paragraph("GD spans business aviation, shipbuilding, land combat systems and government technology. "
                   "The U.S. government accounted for 68% of FY2025 revenue. GAMA recorded 854 worldwide business-jet shipments in 2025. "
                   "These markets expose GD to distinct procurement and delivery cycles. [1, 4]")
    page.heading("Segment economics and guidance")
    by_year = {(line.period_end.year, line.segment): line for line in data.segments}
    rows = [["Segment", "2025A", "2026 issuer outlook", "2026E", "2031E"]]
    for segment, guidance in MANAGEMENT_OUTLOOK.items():
        figures = []
        for year in (2025, 2026, 2031):
            line = by_year[year, segment]
            margin = _margin(line.operating_earnings_millions, line.revenue_millions)
            figures.append(f"${line.revenue_millions / 1000:.2f}bn / {margin}")
        rows.append([segment, figures[0], guidance, *figures[1:]])
    page.table(rows, [105, 93, 132, 99, 99])
    page.paragraph("Each cell presents revenue and operating margin. Issuer outlook is management's January 2026 estimate. "
                   "The workbook uses delivery, pricing and service drivers for Aerospace and segment growth and margin assumptions for defense. [1, 3, 7]", small=True)
    page.heading("Consolidated forecast")
    actual = data.history[-1]
    years = (actual, *data.projection.years)
    rows = [["USD millions", "2025A", *(f"{year.period_end.year}E" for year in data.projection.years)]]
    for label, attribute in (
        ("Revenue", "revenue_millions"), ("Operating earnings", "operating_earnings_millions"),
        ("Net earnings", "net_earnings_millions"), ("Closing cash", "closing_cash_millions"),
    ):
        rows.append([label, *(f"{getattr(year, attribute):,.0f}" for year in years)])
    rows.insert(3, ["Operating margin", *(_margin(year.operating_earnings_millions, year.revenue_millions) for year in years)])
    rows.append(["Modeled FCFE", "n.a.", *(f"{year.fcfe_millions:,.0f}" for year in data.projection.years)])
    page.table(rows, [115, *([(BODY - 115) / 7] * 7)])
    cagr = (years[-1].revenue_millions / actual.revenue_millions) ** (1 / (years[-1].period_end.year - actual.period_end.year)) - 1
    page.paragraph(f"The forecast implies {cagr:.1%} annualized revenue growth from FY2025 to FY2031. GD reported FY2025 operating "
                   "cash flow of $5.120bn and cash capex of $1.161bn. The resulting $3.959bn is the issuer's free cash flow measure. "
                   "The valuation uses modeled FCFE. [1, 2, 7]", small=True)
    page.finish()


def _valuation_page(canvas: Canvas, data: GDReportData, date_label: str) -> None:
    page = _Page(canvas, 3, date_label, "DCF and scenarios", "Valuation")
    inputs, projection = data.inputs, data.projection
    rate, growth = inputs.cost_of_equity, inputs.terminal_growth
    last = projection.years[-1]
    pv_total = projection.intrinsic_usd_per_share * inputs.opening_shares_millions
    terminal = last.fcfe_millions * (1 + growth) / (rate - growth)
    pv_terminal = terminal / (1 + rate) ** ((last.period_end - inputs.as_of).days / 365)
    page.paragraph(f"The FCFE DCF uses a {rate:.2%} cost of equity, {growth:.2%} terminal growth and {inputs.opening_shares_millions:,.2f}m opening shares. "
                   f"It prorates the first remaining fiscal year and discounts on an actual-days/365 basis. The 12-month target revalues the same forecast "
                   f"at {_date_label(inputs.twelve_month_date)} with {data.forward_shares_millions:,.2f}m modeled shares. [7]")
    page.table([
        ["Present value of forecast FCFE", "Present value of terminal FCFE", "Total equity value"],
        [f"${(pv_total - pv_terminal) / 1000:,.2f}bn", f"${pv_terminal / 1000:,.2f}bn", f"${pv_total / 1000:,.2f}bn"],
    ], [BODY / 3] * 3, align="CENTER")
    if pv_total > 0:
        page.paragraph(f"Terminal value supplies {pv_terminal / pv_total:.1%} of modeled equity value. Discount rate and long-run growth dominate the sensitivity. [7]", small=True)
    page.heading("Operating cases")
    cases = {case.selector: case.projection for case in data.scenarios}
    selected = "Base" if all(value == 2 for value in inputs.scenarios.values()) and not inputs.override_log else "Selected"
    rows = [["Case", "FY2026 revenue", "FY2026 op. margin", "Intrinsic", "12-month target"]]
    for name, case in (("Bull", cases[1]), (selected, projection), ("Bear", cases[3])):
        year = case.years[0]
        rows.append([name, f"${year.revenue_millions / 1000:.2f}bn", _margin(year.operating_earnings_millions, year.revenue_millions),
                     _usd(case.intrinsic_usd_per_share), _usd(case.twelve_month_usd_per_share)])
    page.table(rows, [95, 115, 118, 90, 110])
    page.paragraph("Bull and bear apply the workbook's six revenue and margin switches for Marine, Combat and Technologies. "
                   "Aerospace, discount rate and terminal growth are held fixed. Cases carry no probabilities. [7]", small=True)
    page.heading("Intrinsic value sensitivity (USD per share)")
    grid = data.sensitivity
    rows = [["Cost of equity", *(f"{value:.1%}" for value in grid.terminal_growth_rates)]]
    highlights = []
    for row, (discount, values) in enumerate(zip(grid.cost_of_equity_rates, grid.values_usd_per_share, strict=True), 1):
        rows.append([f"{discount:.2%}", *(f"${value:,.0f}" if value is not None else "n.a." for value in values)])
        for column, long_run_growth in enumerate(grid.terminal_growth_rates, 1):
            if isclose(discount, rate, abs_tol=1e-10) and isclose(long_run_growth, growth, abs_tol=1e-10):
                highlights.append(("BACKGROUND", (column, row), (column, row), HexColor("#F5E2B9")))
                highlights.append(("FONTNAME", (column, row), (column, row), "Helvetica-Bold"))
    page.table(rows, [91, *([(BODY - 91) / 7] * 7)], extra=tuple(highlights), align="CENTER")
    page.paragraph("Columns show terminal growth. The selected inputs are highlighted when both appear in the grid. "
                   "Values are unavailable where terminal growth equals or exceeds the discount rate. [7]", small=True)
    page.finish()


def _evidence_page(canvas: Canvas, data: GDReportData, date_label: str, workbook: Path) -> None:
    page = _Page(canvas, 4, date_label, "Monitoring and evidence", "Catalysts and risks")
    rows = [["Watch", "Catalyst or milestone", "Risk to monitor"]]
    for label, catalyst, risk in (
        ("Marine", "Watch backlog conversion and progress toward ~7.3% FY2026 Marine margin. [1, 3]",
         "Skilled labor, supplier capacity and Columbia-class schedule slippage can raise costs. [1, 5]"),
        ("Aerospace", "Track ~160 FY2026 deliveries, large-cabin mix and service activity. [1, 3]",
         "Delivery timing, certification and mix can reduce revenue recognition or operating margin. [1]"),
        ("Cash and contracts", "Track cash conversion after capex. The March dividend declaration was $1.59 per share. [2, 6]",
         "Fixed-price work represented 51% of FY2025 U.S. government revenue. Cost overruns can reduce cash conversion. [1]"),
    ):
        rows.append([label, _paragraph(catalyst, size=8.8, leading=12), _paragraph(risk, size=8.8, leading=12)])
    page.table(rows, [94, 217, 217], align="LEFT")
    page.heading("FY2025 reported backlog of $118.05bn")
    funded_width = BODY * 93541 / 118046
    for x, width, label, color in (
        (MARGIN, funded_width, "Funded $93.54bn", NAVY),
        (MARGIN + funded_width, BODY - funded_width, "Unfunded $24.51bn", GOLD),
    ):
        canvas.setFillColor(color)
        canvas.rect(x, page.y - 24, width, 24, stroke=0, fill=1)
        canvas.setFillColor(white if color == NAVY else INK)
        canvas.setFont("Helvetica-Bold", 8.4)
        canvas.drawCentredString(x + width / 2, page.y - 15, label)
    page.y -= 33
    page.paragraph("Management expected roughly 35% of the year-end backlog to convert to revenue in FY2026. "
                   "The workbook's forward backlog inputs are unavailable. [1, 7]", small=True)
    page.heading("Workbook checks and limits")
    parity = data.parity
    gap = max(abs(year.operating_expense_detail_gap_millions) for year in data.projection.years)
    page.paragraph(f"Matched {parity.history_cells_matched:,} historical values, {parity.forecast_cells_matched:,} forecast results and {parity.sensitivity_cells_matched} Excel sensitivity values. "
                   f"The largest forecast expense-detail gap is ${gap:,.2f}m. The archived FY2025 capex input is $1,163m versus $1,161m in the filing. "
                   "The archived FCFE bridge uses net earnings and balance-sheet changes. Dated peer multiples are unavailable. [1, 7]", small=True)
    page.heading("Sources")
    for index, (label, date, url) in enumerate(SOURCES, 1):
        page.paragraph(f'[{index}] <link href="{url}" color="#215E90">{escape(label)}</link> ({date}).', small=True, gap=3)
    page.paragraph(f"[7] {escape(workbook.name)}, selected archived run. Cover, Revenue model, Income statement, Balance sheet, Cash flow, DCF and Run inputs.", small=True, gap=3)
    page.paragraph(f"Paired workbook fingerprint (SHA-256) {parity.workbook_sha256}", small=True, gap=3)
    if data.projection.override_log:
        page.paragraph("Guided adjustments and their reasons are recorded in the paired workbook's Run inputs sheet. [7]", small=True, gap=3)
    page.finish()


def _render_pdf(data: GDReportData, workbook: Path) -> bytes:
    buffer = BytesIO()
    date_label = _date_label(data.inputs.as_of)
    canvas = Canvas(buffer, pagesize=letter, pageCompression=1)
    canvas.setTitle("General Dynamics Archived Equity Research")
    canvas.setSubject(f"Archived valuation as of {date_label}")
    canvas.setCreator("AI Equity Research Builder")
    _investment_page(canvas, data, date_label)
    _operating_page(canvas, data, date_label)
    _valuation_page(canvas, data, date_label)
    _evidence_page(canvas, data, date_label, workbook)
    canvas.save()
    return buffer.getvalue()


def _verify_pdf(content: bytes, data: GDReportData) -> int:
    pdf = PdfReader(BytesIO(content))
    if len(pdf.pages) != PAGE_COUNT:
        raise ValueError("The research report is missing an expected page.")
    pages = [page.extract_text() for page in pdf.pages]
    financial_values = set(pages[1].split())
    valuation_values = set(pages[2].split())
    projection = data.projection
    values = (_usd(projection.intrinsic_usd_per_share), _usd(projection.twelve_month_usd_per_share))
    if any(value not in pages[0] or value not in pages[2] for value in values):
        raise ValueError("The PDF valuation does not match the workbook run.")
    for year in projection.years:
        for amount in (year.revenue_millions, year.operating_earnings_millions, year.net_earnings_millions,
                       year.closing_cash_millions, year.fcfe_millions):
            if f"{amount:,.0f}" not in financial_values:
                raise ValueError(f"The PDF is missing a forecast value for FY{year.period_end.year}.")
    for scenario in data.scenarios:
        if any(_usd(value) not in pages[2] for value in (scenario.projection.intrinsic_usd_per_share,
                                                       scenario.projection.twelve_month_usd_per_share)):
            raise ValueError("The PDF is missing an operating case valuation.")
    for row in data.sensitivity.values_usd_per_share:
        for value in row:
            if (f"${value:,.0f}" if value is not None else "n.a.") not in valuation_values:
                raise ValueError("The PDF is missing an intrinsic sensitivity value.")
    if any(_date_label(data.inputs.as_of) not in page for page in pages) or "Unavailable" not in pages[0]:
        raise ValueError("The PDF is missing its archived cutoff or rating status.")
    return len(pdf.pages)


def export_gd_pdf(template: Path, workbook: Path, output: Path, *, guided: Path | None = None) -> GDPDFExport:
    """Create a new PDF after checking its Excel partner and rendered figures."""
    if output.suffix.lower() != ".pdf":
        raise ValueError("The research report destination must have a .pdf extension.")
    if output.exists() or output.is_symlink():
        raise FileExistsError("Choose a new PDF output filename.")
    data = load_gd_report_data(template, workbook, guided=guided)
    content = _render_pdf(data, workbook)
    pages = _verify_pdf(content, data)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(dir=output.parent, suffix=".pdf", delete=False) as staged:
            temporary = Path(staged.name)
            staged.write(content)
        if os.name == "nt":
            os.rename(temporary, output)
        else:
            os.link(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return GDPDFExport(output, pages, data)

"""Publish a dated GD research PDF from a verified fresh workbook."""

from dataclasses import dataclass, field
from datetime import date
from io import BytesIO
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from xml.sax.saxutils import escape, quoteattr

from pypdf import PdfReader
from pypdf.errors import PdfReadError
from reportlab.lib.colors import HexColor, white
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen.canvas import Canvas

from .gd_fresh_excel import FreshWorkbookData, _data, build_fresh_workbook_plan
from .gd_fresh_pdf_charts import revenue_chart, target_range_chart
from .gd_fresh_pdf_workbook import VerifiedPDFWorkbook, load_verified_pdf_workbook
from .gd_pdf import BODY, INK, MARGIN, NAVY, PALE, _Page, _date_label, _paragraph, _usd


PAGE_COUNT = 5
MARK = HexColor("#F5E6C9")
GAMA = "https://gama.aero/wp-content/uploads/2025ShipmentReport_02_26.pdf"
GAO = "https://files.gao.gov/reports/GAO-26-109068/index.html"
SEGMENTS = (
    ("Aerospace", 10, 20),
    ("Marine Systems", 26, 33),
    ("Combat Systems", 39, 46),
    ("Technologies", 51, 57),
)


@dataclass(frozen=True)
class FreshGDPDFExport:
    output: Path
    pages: int
    workbook_sha256: str
    workbook_cells_matched: int


@dataclass(frozen=True)
class _Research:
    data: FreshWorkbookData
    workbook: VerifiedPDFWorkbook
    workbook_name: str
    as_of: date
    case: str


@dataclass
class _ContentChecks:
    rows: dict[int, list[str]] = field(default_factory=dict)
    urls: set[str] = field(default_factory=set)


class _ReportPage(_Page):
    def __init__(self, canvas, number, report, section, title, checks):
        super().__init__(canvas, number, _date_label(report.as_of), section, title,
                         footer="Equity research", page_count=PAGE_COUNT)
        self.checks = checks

    def require(self, text: str) -> None:
        self.checks.rows.setdefault(self.number, []).append(text)

    def table(self, rows, widths, *, extra=(), align="RIGHT", compact=False):
        for row in rows:
            if all(isinstance(value, str) for value in row):
                self.require(" ".join(row))
        if compact:
            extra = (
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                *extra,
            )
        super().table(rows, widths, extra=extra, align=align)

    def link(self, name: str, url: str) -> str:
        self.checks.urls.add(url)
        return f'<link href={quoteattr(url)} color="#215E90">{escape(name)}</link>'


def _bn(value: float) -> str:
    return f"${value / 1000:,.2f}bn"


def _pct(value: float, *, signed: bool = False) -> str:
    return f"{value:+.2%}" if signed else f"{value:.2%}"


def _card_row(page: _ReportPage, measures: tuple[tuple[str, str], ...]) -> None:
    gap = 8
    width = (BODY - gap * (len(measures) - 1)) / len(measures)
    canvas = page.canvas
    for index, (label, value) in enumerate(measures):
        x = MARGIN + index * (width + gap)
        canvas.setFillColor(NAVY if index == 0 else PALE)
        canvas.rect(x, page.y - 59, width, 59, stroke=0, fill=1)
        canvas.setFillColor(white if index == 0 else NAVY)
        canvas.setFont("Helvetica", 8.5)
        canvas.drawString(x + 10, page.y - 17, label)
        canvas.setFont("Helvetica-Bold", 19)
        canvas.drawString(x + 10, page.y - 41, value)
        page.require(f"{label} {value}")
    page.y -= 70


def _investment_page(canvas: Canvas, report: _Research, checks: _ContentChecks) -> None:
    page = _ReportPage(canvas, 1, report, "Investment view", "General Dynamics", checks)
    wb, data = report.workbook, report.data
    selected = data.research.scenarios[report.case]
    close, spot, target, total = (wb.number("Cover", f"E{row}") for row in (17, 18, 19, 23))
    rating = wb.value("Cover", "E15")
    close_on = _date_label(wb.day("Cover", "E12"))
    horizon = _date_label(wb.day("Cover", "E14"))
    page.paragraph(f"{report.case.title()} case at {_date_label(report.as_of)}. Reference close of {_usd(close)} "
                   f"on {close_on}. Target horizon {horizon}. [8, 12]")
    _card_row(page, (("Model rating", rating), ("Intrinsic value", _usd(spot)),
                     ("12-month target", _usd(target)), ("Total return", _pct(total, signed=True))))

    page.heading("Investment thesis")
    page.paragraph("<b>Higher naval workload supports growth.</b> July awards expanded Electric Boat's submarine program. "
                   "Revenue conversion depends on funded work, skilled labor and supplier output. About one-third of "
                   "Marine Systems' October backlog was funded. [1, 5, 7]")
    page.paragraph("<b>Gulfstream volume and mix drive Aerospace.</b> GD delivered 119 aircraft through the first nine "
                   "months of FY2026 and reported a 1.4x Aerospace book-to-bill. Deliveries, cabin mix and aftermarket "
                   "service growth drive the model's segment revenue. [1, 2, 12]")
    page.paragraph(f"<b>Cash conversion sets the value.</b> The {report.case} case forecasts "
                   f"{_bn(selected.annual[0].statement_fcfe_millions)} of FY2027 cash flow to equity after scheduled "
                   f"note repayments. Terminal cash flows account for {wb.number('DCF', 'B23'):.0%} of modeled spot equity value. [1, 12]")
    target_range_chart(page, {key: scenario.price_target for key, scenario in data.research.scenarios.items()}, close)
    buy, sell = wb.number("Valuation inputs", "B14"), wb.number("Valuation inputs", "B15")
    dividends = wb.number("DCF", "B49") + wb.number("DCF", "B50")
    page.paragraph(f"The model uses a {_pct(buy, signed=True)} Buy threshold and a {_pct(sell, signed=True)} Sell threshold "
                   f"for 12-month total return, with Hold between. The selected return includes {_usd(dividends)} of "
                   "projected dividend entitlement per share. [10, 12]", small=True)
    page.finish()


def _operations_page(canvas: Canvas, report: _Research, checks: _ContentChecks) -> None:
    page = _ReportPage(canvas, 2, report, "Operating model", "Business and industry", checks)
    wb, data = report.workbook, report.data
    page.paragraph("<b>Business aviation.</b> GAMA recorded 854 worldwide business-jet shipments in 2025, up 11.8%. "
                   "GD generally recognizes aircraft revenue upon delivery and acceptance. Its services business earns "
                   "revenue from aircraft maintenance and other support. [1, 3, 6]")
    page.paragraph("<b>Defense.</b> U.S. government customers represented 68% of FY2025 sales. Fixed-price contracts were "
                   "51% of that government revenue, making cost execution important. GAO cited Navy data showing Virginia-class "
                   "production was running at one submarine per year in June 2025 against a two-per-year goal. [3, 7]")
    page.heading("Segment revenue and operating margin")
    rows = [["Segment", "FY2025A", "9M FY2026A", "FY2026E", "FY2031E"]]
    for name, revenue_row, margin_row in SEGMENTS:
        rows.append([name, *(
            f"{_bn(wb.number('Revenue model', f'{column}{revenue_row}'))} / {wb.number('Revenue model', f'{column}{margin_row}'):.1%}"
            for column in "FOGL"
        )])
    page.table(rows, [116, 103, 103, 103, 103])
    deliveries_ytd, deliveries_q4, first_delivery, last_delivery = (
        wb.number("Revenue model", f"{col}13") for col in "OPHL"
    )
    page.paragraph(f"GD's October FY2026 outlook was $56.5-$56.8bn of revenue, about 10.5% operating margin and "
                   f"$17.20-$17.30 diluted EPS. The model starts from {deliveries_ytd:,.0f} reported nine-month Gulfstream "
                   f"deliveries and {deliveries_q4:,.0f} estimated Q4 deliveries. The {report.case} case models "
                   f"{first_delivery:,.0f} deliveries in FY2027 and {last_delivery:,.0f} in FY2031. [1, 2, 4, 12]", small=True)
    actuals = tuple((year, wb.number("Income statement", f"{col}9") / 1000)
                    for year, col in zip(data.history.historical_years, "BCDEF", strict=True))
    scenarios = {
        name: ((report.as_of.year, case.fy2026_revenue_millions / 1000),
               *((year.year, year.revenue_millions / 1000) for year in case.annual))
        for name, case in data.research.scenarios.items()
    }
    revenue_chart(page, actuals, scenarios, report.case)
    page.finish()


def _financial_page(canvas: Canvas, report: _Research, checks: _ContentChecks) -> None:
    page = _ReportPage(canvas, 3, report, "Financial outlook", "Financial forecasts", checks)
    wb = report.workbook
    page.paragraph(f"Selected {report.case} case. FY2026 combines reported results through October 4 with the modeled "
                   "fourth quarter. The forecast pays scheduled notes through FY2031 and assumes no new note issuance. [1, 12]")
    page.heading("Linked statement summary")
    start = report.as_of.year - 1
    rows = [["USD millions", *(f"{year}{'A' if year == start else 'E'}" for year in range(start, start + 7))]]
    for label, sheet, row in (
        ("Revenue", "Income statement", 9), ("Operating earnings", "Income statement", 17),
        ("Operating margin", "Income statement", 29), ("Net earnings", "Income statement", 26),
        ("Cash from operations", "Cash flow", 12), ("Cash capex", "Cash flow", 16),
        ("Operating cash less capex", "Cash flow", 47), ("Statement FCFE", "Cash flow", 48),
        ("Closing cash", "Balance sheet", 8), ("Closing carrying debt", "Balance sheet", 33),
    ):
        cells = []
        for col in "FGHIJKL":
            if row == 48 and col in "FG":
                cells.append("n.a.")
            else:
                value = wb.number(sheet, f"{col}{row}")
                cells.append(f"{value:.1%}" if label == "Operating margin" else f"{value:,.0f}")
        rows.append([label, *cells])
    page.table(rows, [124, *([(BODY - 124) / 7] * 7)])
    page.paragraph("Operating cash less capex follows GD's free cash flow definition. Statement FCFE also reflects "
                   "net note borrowing and finance lease principal. Comparable full-year FCFE is unavailable for FY2025 and FY2026. "
                   "The valuation uses the remaining FY2026 period "
                   "after the cutoff, with compensation and cash adjustments below. [1, 4, 12]", small=True)
    page.heading("From cash flow to value")
    p = lambda address: wb.number("DCF", address)
    page.table([
        ["Spot equity bridge", "USD billions"],
        ["PV of forecast equity cash flows", _bn(p("B18"))],
        ["PV of terminal equity cash flows", _bn(p("B21"))],
        ["Net nonoperating cash at cutoff", _bn(p("B17"))],
        ["Common equity value", _bn(p("B22"))],
    ], [BODY - 126, 126])
    page.paragraph(f"The spot DCF uses the 52 days after the valuation date and discounts FY2027-FY2031 at period end. "
                   f"That stub falls from ${p('G29'):,.2f}m of statement FCFE to ${p('G33'):,.2f}m of valuation cash flow "
                   "after stock compensation and the adjustment for surplus cash interest. The modeled opening cash reserve "
                   "is 4% of sales. [9, 12]", small=True)
    page.paragraph("The 12-month DCF revalues the remaining cash flows at the target date. It uses historical Q4 timing "
                   "for operating cash and capex, and discounts the $500m November 2027 note on its contractual maturity date. "
                   "The projected November dividend entitlement is reflected in total return. [10, 12]", small=True)
    page.finish()


def _valuation_page(canvas: Canvas, report: _Research, checks: _ContentChecks) -> None:
    page = _ReportPage(canvas, 4, report, "DCF and trading comparables", "Valuation and scenarios", checks)
    wb, data = report.workbook, report.data
    page.paragraph(f"The DCF uses a {wb.number('DCF', 'B6'):.2%} cost of equity and "
                   f"{wb.number('DCF', 'B7'):.2%} perpetual growth. The forecast normalizes terminal reinvestment "
                   "and assumes refinancing of surviving notes. Rates and financing policy are shared across the cases. [9, 12]",
                   small=True)
    page.heading("Operating cases and 12-month returns")
    rows = [["Case", "FY27 Gulfstream jets", "FY27 op. margin", "Intrinsic", "12-month target", "Total return"]]
    for name, case in data.research.scenarios.items():
        period = case.annual[0]
        rows.append([name.title(), f"{period.gulfstream_deliveries}", f"{period.operating_margin:.2%}",
                     _usd(case.intrinsic_value_per_share), _usd(case.price_target), _pct(case.total_return, signed=True)])
    selected_row = tuple(data.research.scenarios).index(report.case) + 1
    page.table(rows, [64, 92, 98, 80, 102, 92], compact=True,
               extra=(("BACKGROUND", (0, selected_row), (-1, selected_row), MARK),))
    page.paragraph("The bear case assumes a slower Gulfstream ramp and weaker defense growth and margins. The bull "
                   "case assumes faster production growth, stronger mix and better margins. "
                   "Alternative cases are calculated from the verified workbook inputs. [12]",
                   small=True)

    page.heading("Trailing peer comparison")
    multiple = lambda value: f"{value:.2f}x" if isinstance(value, (int, float)) else "n.a."
    rows = [["Company", "EV / EBIT", "P/E", "GD spot on EV / EBIT", "GD spot on P/E"]]
    for row in range(12, 17):
        rows.append([str(wb.value("Comps", f"A{row}")), multiple(wb.value("Comps", f"N{row}")),
                     multiple(wb.value("Comps", f"O{row}")), "", ""])
    rows.append(["Peer median", multiple(wb.value("Comps", "B4")), multiple(wb.value("Comps", "B5")),
                 *(_usd(wb.number("Comps", f"B{row}")) if wb.value("Comps", f"B{row}") not in (None, "") else "n.a."
                   for row in (6, 7))])
    page.table(rows, [105, 76, 68, 147, 132], compact=True,
               extra=(("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),))
    page.paragraph("The peer medians use November 6 closes and reported trailing results. The implied prices are spot "
                   "cross-checks. Contract charges, pension accounting and business mix affect comparability. [11, 12]",
                   small=True)

    page.heading("12-month target sensitivity")
    rows = [["Cost of equity", *(f"{wb.number('DCF', f'{col}122'):.1%}" for col in "CDEFGHI")]]
    for row in range(123, 130):
        values = [wb.value("DCF", f"{col}{row}") for col in "CDEFGHI"]
        rows.append([f"{wb.number('DCF', f'B{row}'):.2%}",
                     *(_usd(value) if isinstance(value, (int, float)) else "n.a." for value in values)])
    page.table(rows, [94, *([(BODY - 94) / 7] * 7)], align="CENTER", compact=True,
               extra=(("BACKGROUND", (4, 4), (4, 4), MARK), ("FONTNAME", (4, 4), (4, 4), "Helvetica-Bold")))
    page.paragraph("USD per share. Columns show terminal growth. The shaded cell matches the selected target. "
                   "The workbook also includes the spot sensitivity grid. [12]", small=True, gap=2)
    page.finish()


def _evidence_page(canvas: Canvas, report: _Research, checks: _ContentChecks) -> None:
    page = _ReportPage(canvas, 5, report, "Monitoring and evidence", "Catalysts, risks and sources", checks)
    wb, data = report.workbook, report.data
    research = data.research.inputs
    q3 = data.interim
    annual = data.statements[report.case].evidence.sources["annual"]
    quote = data.research.reference_close.source
    cash = data.cash_forecast.outlook.source
    award = next(item.source for item in research.catalysts if item.title == "Columbia and Virginia production")
    numbers = {q3.filing.url: 1, q3.release.url: 2, award.url: 5}

    def observation(item):
        marker = f" [{numbers[item.source.url]}]" if item.source.url in numbers else ""
        detail = item.detail.translate(str.maketrans("\u2010\u2011\u2012\u2013\u2014", "-----"))
        return _paragraph(f"<b>{escape(item.title)}</b><br/>{escape(detail)}{marker}", size=8.5, leading=11.2)

    rows = [["Catalysts", "Risks"]]
    for index in range(max(len(research.catalysts), len(research.risks))):
        rows.append([observation(group[index]) if index < len(group) else "" for group in (research.catalysts, research.risks)])
    page.table(rows, [BODY / 2, BODY / 2], align="LEFT",
               extra=(("VALIGN", (0, 1), (-1, -1), "TOP"),))
    page.paragraph("The workbook records an operating cash reserve, an estimated cash timing bridge and modeled dividend "
                   "dates. Current option overhang and potential Electric Boat investigation losses remain unquantified. [1, 3, 10, 12]",
                   small=True, gap=5)
    page.heading("Source register")

    def source(number, entries):
        page.paragraph(f"[{number}] " + " | ".join(page.link(label, url) for label, url in entries), small=True, gap=2)

    source(1, (("GD Form 10-Q for the period ended October 4, 2026", q3.filing.url),))
    source(2, (("GD third-quarter earnings release, October 30, 2026", q3.release.url),))
    source(3, (("GD FY2025 Form 10-K", annual.url),))
    source(4, (("October earnings call transcript", cash.url),))
    source(5, (("Electric Boat submarine awards, July 29, 2026", award.url),))
    source(6, (("GAMA 2025 aircraft shipment report", GAMA),))
    source(7, (("GAO Navy shipbuilding testimony, GAO-26-109068", GAO),))
    source(8, (("GD November 6 market close", quote.url),))
    valuation_inputs = data.valuation_inputs
    source(9, (("Treasury 10-year yield", valuation_inputs.risk_free_rate.source.url),
               ("Implied equity risk premium", valuation_inputs.equity_risk_premium.source.url),
               ("Industry beta", valuation_inputs.unlevered_beta.source.url)))
    horizon = data.horizon_inputs
    season_sources = tuple(dict.fromkeys(row.source.url for row in horizon.seasonality))
    source(10, (*((f"{note.date:%b %Y} note", note.source.url) for note in horizon.maturities),
                ("Dividend history", horizon.dividend_source.url),
                *((f"Cash seasonality {year}", url) for year, url in zip(("2023-24", "2025"), season_sources, strict=True))))
    for peer in data.comps.peers:
        evidence = peer.evidence
        source(11, ((f"{evidence.ticker} 10-K", evidence.annual_source.url), ("10-Q", evidence.interim_source.url),
                    ("November 6 close", evidence.close.source.url)))
    page.paragraph(f"[12] Paired workbook {escape(report.workbook_name)}. "
                   f"Checked {wb.cells_matched:,} saved cells against the dated model.", small=True, gap=2)
    page.paragraph(f"Workbook SHA-256 {wb.sha256}", small=True, gap=0)
    page.require(wb.sha256)
    page.finish()


def _load_research(template: Path, workbook: Path, as_of: date, inputs_dir: Path, case: str) -> _Research:
    data = _data(as_of, inputs_dir)
    plan = build_fresh_workbook_plan(template, as_of, inputs_dir, case, data=data)
    verified = load_verified_pdf_workbook(workbook, plan, case)
    return _Research(data, verified, workbook.name, as_of, case)


def _render_pdf(report: _Research) -> tuple[bytes, _ContentChecks]:
    buffer = BytesIO()
    canvas = Canvas(buffer, pagesize=letter, pageCompression=1)
    canvas.setTitle(f"General Dynamics Equity Research - {_date_label(report.as_of)}")
    canvas.setSubject(f"{report.case.title()} case and 12-month equity valuation")
    canvas.setCreator("AI Equity Research Builder")
    canvas.setKeywords(f"workbook-sha256:{report.workbook.sha256}")
    checks = _ContentChecks()
    for render in (_investment_page, _operations_page, _financial_page, _valuation_page, _evidence_page):
        render(canvas, report, checks)
    canvas.save()
    return buffer.getvalue(), checks


def _normalized(value: str) -> str:
    return " ".join(value.split())


def _verify_pdf(content: bytes, report: _Research, checks: _ContentChecks) -> tuple[str, ...]:
    try:
        pdf = PdfReader(BytesIO(content))
    except PdfReadError as exc:
        raise ValueError("The report must be a readable PDF.") from exc
    if len(pdf.pages) != PAGE_COUNT:
        raise ValueError("The report must contain all five research pages.")
    digest = report.workbook.sha256
    if pdf.metadata is None or pdf.metadata.get("/Keywords") != f"workbook-sha256:{digest}":
        raise ValueError("The PDF fingerprint does not identify the supplied workbook.")
    texts = []
    urls = set()
    for number, page in enumerate(pdf.pages, 1):
        text = _normalized(page.extract_text())
        if f"{number} / {PAGE_COUNT}" not in text or _date_label(report.as_of) not in text:
            raise ValueError(f"The report is missing the cutoff or footer on page {number}.")
        for required in checks.rows.get(number, ()):
            if _normalized(required) not in text:
                raise ValueError(f"The PDF differs from the workbook-backed report on page {number}: {required[:110]}")
        texts.append(text)
        for annotation in page.get("/Annots") or ():
            action = annotation.get_object().get("/A")
            if action is not None and action.get("/S") == "/URI":
                urls.add(str(action.get("/URI")))
    if urls != checks.urls:
        raise ValueError("PDF source links disagree with the dated report.")
    return tuple(texts)


def verify_fresh_gd_pdf(
    template: Path, workbook: Path, pdf: Path, as_of: date, inputs_dir: Path, *, case: str = "base",
) -> FreshGDPDFExport:
    """Verify an existing report's content, citations and workbook identity."""
    report = _load_research(template, workbook, as_of, inputs_dir, case)
    expected, checks = _render_pdf(report)
    actual_text = _verify_pdf(pdf.read_bytes(), report, checks)
    if actual_text != _verify_pdf(expected, report, checks):
        raise ValueError("The PDF research text differs from the dated workbook-backed report.")
    return FreshGDPDFExport(pdf, PAGE_COUNT, report.workbook.sha256, report.workbook.cells_matched)


def export_fresh_gd_pdf(
    template: Path, workbook: Path, output: Path, as_of: date, inputs_dir: Path, *, case: str = "base",
) -> FreshGDPDFExport:
    """Verify the Excel partner and publish a new research PDF."""
    if output.suffix.lower() != ".pdf":
        raise ValueError("The research report destination must have a .pdf extension.")
    if output.exists() or output.is_symlink():
        raise FileExistsError("Choose a new PDF output filename.")
    report = _load_research(template, workbook, as_of, inputs_dir, case)
    content, checks = _render_pdf(report)
    _verify_pdf(content, report, checks)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(dir=output.parent, suffix=".pdf", delete=False) as staged:
            temporary = Path(staged.name)
            staged.write(content)
        if os.name == "nt":
            temporary.rename(output)
        else:
            os.link(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return FreshGDPDFExport(output, PAGE_COUNT, report.workbook.sha256, report.workbook.cells_matched)

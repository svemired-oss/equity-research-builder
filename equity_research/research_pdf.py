"""Layout, snapshot verification and safe publication for dated research PDFs."""

from dataclasses import dataclass, field
from io import BytesIO
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from xml.sax.saxutils import escape, quoteattr

from pypdf import PdfReader
from pypdf.errors import PdfReadError
from reportlab.lib.colors import white
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen.canvas import Canvas

from .gd_pdf import BODY, MARGIN, NAVY, PALE, _Page, _date_label


@dataclass
class _ContentChecks:
    text: dict[int, list[str]] = field(default_factory=dict)
    links: dict[int, set[str]] = field(default_factory=dict)


class _ReportPage(_Page):
    def __init__(self, canvas, number, report, section, title, checks, *, page_count, identity, accent):
        super().__init__(canvas, number, _date_label(report.as_of), section, title, footer="Equity research",
                         page_count=page_count, identity=identity, accent=accent)
        self.accent = accent
        self.checks = checks

    def require(self, value):
        self.checks.text.setdefault(self.number, []).append(value)

    def table(self, rows, widths, *, extra=(), align="RIGHT"):
        for row in rows:
            self.require(" ".join(row))
        super().table(rows, widths, align=align, extra=(
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            *extra,
        ))

    def source_group(self, number, label, links):
        items = []
        for name, url in links:
            self.checks.links.setdefault(self.number, set()).add(url)
            items.append(f'<link href={quoteattr(url)} color={quoteattr(self.accent.hexval().replace("0x", "#"))}>{escape(name)}</link>')
        self.paragraph(f"<b>[{number}] {label}</b> &nbsp; {' / '.join(items)}", small=True, gap=7)


def _bn(value):
    return f"{'-' if value < 0 else ''}${abs(value) / 1000:,.2f}bn"


def _pct(value, *, signed=False):
    return f"{value:+.2%}" if signed else f"{value:.2%}"


def _cards(page, measures):
    width = (BODY - 24) / 4
    c = page.canvas
    for index, (label, value) in enumerate(measures):
        x = MARGIN + index * (width + 8)
        c.setFillColor(page.accent if index == 0 else PALE)
        c.rect(x, page.y - 62, width, 62, stroke=0, fill=1)
        c.setFillColor(white if index == 0 else NAVY)
        c.setFont("Helvetica", 8.5)
        c.drawString(x + 10, page.y - 17, label)
        c.setFont("Helvetica-Bold", 21)
        c.drawString(x + 10, page.y - 43, value)
        page.require(f"{label} {value}")
    page.y -= 75


def _make_pdf(report, company, pages):
    buffer = BytesIO()
    canvas = Canvas(buffer, pagesize=letter, pageCompression=1, invariant=1)
    canvas.setTitle(f"{company} Equity Research - {_date_label(report.as_of)}")
    canvas.setSubject(f"{report.case.title()} operating case and 12-month valuation")
    canvas.setCreator("AI Equity Research Builder")
    canvas.setKeywords(f"workbook-sha256:{report.workbook.sha256}")
    checks = _ContentChecks()
    for render in pages:
        render(canvas, report, checks)
    canvas.save()
    return buffer.getvalue(), checks


def _normalized(text):
    return " ".join(text.split())


def _inspect_pdf(content, report, checks, page_count):
    try:
        pdf = PdfReader(BytesIO(content))
    except PdfReadError as exc:
        raise ValueError("The report must be a readable PDF.") from exc
    if len(pdf.pages) != page_count:
        raise ValueError(f"The report must contain all {page_count} research pages.")
    if pdf.metadata is None or pdf.metadata.get("/Keywords") != f"workbook-sha256:{report.workbook.sha256}":
        raise ValueError("The PDF fingerprint does not identify the supplied workbook.")
    snapshots = []
    for number, page in enumerate(pdf.pages, 1):
        text = _normalized(page.extract_text())
        if f"{number} / {page_count}" not in text or _date_label(report.as_of) not in text:
            raise ValueError(f"The report is missing its date or footer on page {number}.")
        for required in checks.text.get(number, ()):
            if _normalized(required) not in text:
                raise ValueError(f"The PDF differs from the workbook-backed report on page {number}: {required[:110]}")
        links = []
        for annotation in page.get("/Annots") or ():
            item = annotation.get_object()
            action = item.get("/A")
            if action is not None and action.get("/S") == "/URI":
                links.append((tuple(item["/Rect"]), str(action["/URI"])))
        if {url for _, url in links} != checks.links.get(number, set()):
            raise ValueError(f"PDF source links disagree with the dated report on page {number}.")
        # Include vector drawing instructions so edited charts cannot pass a text-only check.
        snapshots.append((text, page.get_contents().get_data(), tuple(links)))
    return tuple(snapshots)


def _result(report, checks, page_count):
    return {"workbook_sha256": report.workbook.sha256, "workbook_checks": report.workbook.cells_matched,
            "page_count": page_count, "text_checks": sum(map(len, checks.text.values())),
            "source_links": sum(map(len, checks.links.values()))}


def _publish_pdf(content, output):
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

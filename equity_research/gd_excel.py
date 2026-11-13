"""Export the archived GD model while preserving its native Excel package."""

from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from math import isfinite
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape
from zipfile import ZipFile

from openpyxl.utils.cell import get_column_letter
from openpyxl.utils.datetime import to_excel

from .gd_inputs import MODEL_INPUT_SHEETS, GDInputs, load_guided_overrides
from .gd_projection import ForecastBook, ProjectionReport, UNAVAILABLE_FORMULAS, _formulas, run_projection
from .gd_replay import read_baseline_bytes
from .gd_workbook import load_workbook_inputs
from .valuation import ForecastCashFlow, value_fcfe


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
CELL_PATTERN = re.compile(rb'<c\b[^>]*\br="([A-Z]+[0-9]+)"[^>]*?(?:/>|>.*?</c>)', re.DOTALL)


@dataclass(frozen=True)
class GDExcelExport:
    output: Path
    projection: ProjectionReport
    forecast_cells_cached: int
    sensitivity_cells_cached: int
    frozen_comps_cells: int


def _tag(name: str) -> str:
    return f"{{{MAIN_NS}}}{name}"


def _numeric_text(value: float) -> str:
    if not isfinite(value):
        raise ValueError("An Excel numeric cache must be finite.")
    if value == 0:
        return "0"
    return repr(value)


def _body(value: object, formula: str | None = None) -> tuple[str, str]:
    prefix = f"<f>{escape(formula)}</f>" if formula is not None else ""
    if value is None:
        return "", prefix
    if type(value) in (int, float):
        return "", prefix + f"<v>{_numeric_text(float(value))}</v>"
    if formula is not None:
        return ' t="str"', prefix + f"<v>{escape(str(value))}</v>"
    return ' t="inlineStr"', f'<is><t xml:space="preserve">{escape(str(value))}</t></is>'


class _Worksheet:
    """Replace selected cell fragments without serializing the worksheet root."""

    def __init__(self, contents: bytes):
        self.contents = contents
        self.original = {match[1].decode(): match[0] for match in CELL_PATTERN.finditer(contents)}
        self.replacements: dict[str, bytes] = {}

    def set(self, address: str, value: object, *, formula: str | None = None, cache_only: bool = False) -> None:
        original = self.replacements.get(address, self.original.get(address))
        if original is None:
            raise ValueError(f"Required template cell {address} is missing.")
        opening = original[:original.index(b">") + 1].rstrip(b">/").decode()
        opening = re.sub(r'\s(?:t|cm|vm)="[^"]*"', "", opening)
        cell_type, body = _body(value, formula)
        if cache_only:
            existing = re.search(rb"<f\b[^>]*?(?:/>|>.*?</f>)", original, re.DOTALL)
            if existing is not None:
                cell_type = ' t="str"' if isinstance(value, str) else ""
                body = existing[0].decode() + f"<v>{escape(str(value))}</v>"
        self.replacements[address] = f"{opening}{cell_type}>{body}</c>".encode()

    def export(self) -> bytes:
        return CELL_PATTERN.sub(lambda match: self.replacements.get(match[1].decode(), match[0]), self.contents)

    def restyle(self, address: str, style: int) -> None:
        cell = self.replacements.get(address, self.original.get(address))
        if cell is None or re.search(rb'\ss="[^"]*"', cell) is None:
            raise ValueError(f"Required styled template cell {address} is missing.")
        self.replacements[address] = re.sub(rb'\ss="[^"]*"', f' s="{style}"'.encode(), cell, count=1)


def _append_date_style(package: dict[str, bytes]) -> int:
    contents = package["xl/styles.xml"]
    number_formats = re.search(rb'(<numFmts\b[^>]*>)(.*?)(</numFmts>)', contents, re.DOTALL)
    if number_formats is None:
        raise ValueError("The original Excel number formats are missing.")
    format_ids = re.findall(rb'<numFmt\b[^>]*numFmtId="(\d+)"', number_formats[2])
    date_format_id = max([163, *(int(value) for value in format_ids)]) + 1
    date_format = f'<numFmt numFmtId="{date_format_id}" formatCode="yyyy"/>'.encode()
    number_header = re.sub(rb'count="\d+"', f'count="{len(format_ids) + 1}"'.encode(), number_formats[1], count=1)
    number_replacement = number_header + number_formats[2] + date_format + number_formats[3]
    contents = contents[:number_formats.start()] + number_replacement + contents[number_formats.end():]
    cell_formats = re.search(rb'(<cellXfs\b[^>]*>)(.*?)(</cellXfs>)', contents, re.DOTALL)
    if cell_formats is None:
        raise ValueError("The original Excel cell formats are missing.")
    formats = list(re.finditer(rb'<xf\b[^>]*?(?:/>|>.*?</xf>)', cell_formats[2], re.DOTALL))
    clone = formats[163][0]
    clone = re.sub(rb'numFmtId="[^"]*"', f'numFmtId="{date_format_id}"'.encode(), clone, count=1)
    if b'applyNumberFormat="' in clone:
        clone = re.sub(rb'applyNumberFormat="[^"]*"', b'applyNumberFormat="1"', clone, count=1)
    else:
        clone = clone.replace(b"<xf ", b'<xf applyNumberFormat="1" ', 1)
    header = re.sub(rb'count="\d+"', f'count="{len(formats) + 1}"'.encode(), cell_formats[1], count=1)
    replacement = header + cell_formats[2] + clone + cell_formats[3]
    package["xl/styles.xml"] = contents[:cell_formats.start()] + replacement + contents[cell_formats.end():]
    return len(formats)


def _sheet_paths(package: dict[str, bytes]) -> dict[str, str]:
    relations = {
        element.get("Id"): element.get("Target")
        for element in ET.fromstring(package["xl/_rels/workbook.xml.rels"])
    }
    sheets = ET.fromstring(package["xl/workbook.xml"]).find(_tag("sheets"))
    return {
        sheet.get("name"): "xl/" + relations[sheet.get(f"{{{REL_NS}}}id")]
        for sheet in sheets
    }


def _comps_snapshot(package: dict[str, bytes], worksheets: dict[str, _Worksheet]) -> int:
    shared = ["".join(item.itertext()) for item in ET.fromstring(package["xl/sharedStrings.xml"])]
    snapshot = {
        cell.get("r"): cell
        for cell in ET.fromstring(worksheets["Comps no formuals"].contents).iter(_tag("c"))
    }
    frozen = 0
    for cell in ET.fromstring(worksheets["Comps"].contents).iter(_tag("c")):
        formula = cell.find(_tag("f"))
        vendor = formula is not None and "RDP" in (formula.text or "")
        if not vendor and cell.get("t") != "e":
            continue
        address = cell.get("r")
        source = snapshot.get(address)
        value = source.findtext(_tag("v")) if source is not None else None
        if value is None or source.get("t") == "e":
            raise ValueError(f"The archived comps snapshot has no usable value for {address}.")
        if source.get("t") == "s":
            value = shared[int(value)]
        elif source.get("t") not in ("str", "inlineStr"):
            value = float(value)
        worksheets["Comps"].set(address, value, cache_only=not vendor)
        frozen += int(vendor)
    return frozen


def _dcf_caches(worksheet: _Worksheet, inputs: GDInputs, book: ForecastBook, report: ProjectionReport) -> int:
    flows = tuple(ForecastCashFlow(year.period_end, year.fcfe_millions) for year in report.years)
    rate, growth = inputs.cost_of_equity, inputs.terminal_growth
    worksheet.set("B3", rate, cache_only=True)
    worksheet.set("B19", growth)
    worksheet.set("A4", "Number of shares in millions:")
    worksheet.set("H3", "As of")
    worksheet.set("A6", "Archived quote")
    worksheet.set("C6", "Original date label 22-Oct-2025 (unverified)")
    worksheet.set("G3", to_excel(datetime.combine(inputs.as_of, datetime.min.time())), formula="'Cover'!E11")
    worksheet.set("G4", to_excel(datetime.combine(inputs.twelve_month_date, datetime.min.time())), cache_only=True)
    worksheet.set("G5", to_excel(datetime.combine(inputs.twelve_month_date, datetime.min.time())) + 366, cache_only=True)
    worksheet.set("I4", book.value(("Equity", 21, 0)), cache_only=True)
    worksheet.set("I5", book.value(("Equity", 21, 1)), cache_only=True)
    worksheet.set("B8", report.intrinsic_usd_per_share, cache_only=True)
    worksheet.set("B9", report.twelve_month_usd_per_share, cache_only=True)
    worksheet.set("B26", report.intrinsic_usd_per_share, cache_only=True)
    worksheet.set("B48", report.twelve_month_usd_per_share, cache_only=True)

    for discount_date, shares, time_row, flow_row, pv_row, terminal_row, grid_row in (
        (inputs.as_of, inputs.opening_shares_millions, 15, 16, 17, 21, 26),
        (inputs.twelve_month_date, book.value(("Equity", 21, 0)), 39, 40, 41, 43, 48),
    ):
        discounted = []
        for column, flow in zip("DEFGHI", flows, strict=True):
            years = max((flow.period_end - discount_date).days / 365, 0)
            present_value = flow.fcfe_millions * min(years, 1) / (1 + rate) ** years
            worksheet.set(f"{column}{time_row}", years, cache_only=True)
            worksheet.set(f"{column}{flow_row}", flow.fcfe_millions, cache_only=True)
            worksheet.set(f"{column}{pv_row}", present_value, cache_only=True)
            if time_row == 39:
                worksheet.set(f"{column}38", to_excel(datetime.combine(flow.period_end, datetime.min.time())), cache_only=True)
            discounted.append(present_value)
        terminal = flows[-1].fcfe_millions * (1 + growth) / (rate - growth)
        terminal_pv = terminal / (1 + rate) ** ((flows[-1].period_end - discount_date).days / 365)
        equity_value = sum(discounted) + terminal_pv
        worksheet.set(f"A{terminal_row}", f"Terminal Value in {flows[-1].period_end.year}", formula='"Terminal Value in "&YEAR(I14)')
        worksheet.set(f"B{terminal_row}", terminal, formula=f'IF(B3>B19,I{flow_row}*(1+B19)/(B3-B19),"n.a.")')
        worksheet.set(f"B{terminal_row + 1}", terminal_pv, formula=f'IF(ISNUMBER(B{terminal_row}),B{terminal_row}/(1+B3)^I{time_row},"n.a.")')
        worksheet.set(f"B{terminal_row + 2}", equity_value, formula=f'IF(ISNUMBER(B{terminal_row + 1}),SUM(D{pv_row}:I{pv_row})+B{terminal_row + 1},"n.a.")')
        share_cell = "I3" if terminal_row == 21 else "I4"
        worksheet.set(f"B{terminal_row + 3}", equity_value / shares, formula=f'IF(ISNUMBER(B{terminal_row + 2}),B{terminal_row + 2}/{share_cell},"n.a.")')

        for column_index, column in enumerate("CDEFGHI"):
            grid_growth = 0.005 * (column_index + 1)
            worksheet.set(f"{column}{grid_row}", grid_growth, cache_only=True)
        for offset in range(7):
            grid_rate = rate + (3 - offset) * 0.005
            worksheet.set(f"B{grid_row + 1 + offset}", grid_rate, cache_only=True)
            for column_index, column in enumerate("CDEFGHI"):
                grid_growth = 0.005 * (column_index + 1)
                value = "n.a." if grid_rate <= grid_growth else value_fcfe(flows, discount_date, grid_rate, grid_growth, shares)
                worksheet.set(f"{column}{grid_row + 1 + offset}", value, cache_only=True)
    return 98


def _run_inputs_sheet(inputs: GDInputs, report: ProjectionReport, book: ForecastBook, template: Path) -> bytes:
    rows: dict[int, list[tuple[str, str]]] = {}
    merges = ["A1:H1", "B3:H3", "B4:H4", "C8:H8", "C9:H9"]

    def cell(address: str, value: object, style: int = 0, formula: str | None = None) -> None:
        cell_type, body = _body(value, formula)
        row = int(re.search(r"\d+", address)[0])
        rows.setdefault(row, []).append((address, f'<c r="{address}" s="{style}"{cell_type}>{body}</c>'))

    cell("A1", "GD archived run inputs and review", 34)
    cell("A2", "Archived valuation date")
    cell("B2", to_excel(datetime.combine(inputs.as_of, datetime.min.time())), 362)
    cell("A3", "Original reference")
    cell("B3", template.name)
    cell("A4", "SHA-256")
    cell("B4", inputs.source.sha256)
    cell("A8", "Optional cost of equity")
    overridden = any(entry.target == "cost_of_equity" for entry in inputs.override_log)
    cell("B8", inputs.cost_of_equity if overridden else None, 49)
    cell("C8", "Blank uses the original Ke calculation. A numeric entry overrides it.")
    cell("A9", "Dividend payout ratio")
    cell("B9", inputs.dividend_payout_ratio, 49)
    cell("C9", "Editable proportion of net earnings paid as dividends.")
    notes = (
        "Archived model using the approved March 30, 2026 workbook and its assumptions.",
        "The quote observation date is unverified. Rating and return outputs are unavailable.",
        "Vendor comps inputs are frozen from the original values-only tab. Their dates are unverified.",
        "Forward backlog inputs are absent. Forward backlog totals display n.a.",
        "The original operating expense detail differs from the segment-based reported total.",
        "DCF sensitivities retain the original fixed growth axes and native Excel data tables.",
        "Excel recalculates the financial model when inputs change.",
    )
    for row, note in enumerate(notes, 12):
        cell(f"A{row}", note)
        merges.append(f"A{row}:H{row}")
    cell("A20", "Forecast review (USD millions)", 34)
    for index, year in enumerate(report.years):
        column = get_column_letter(index + 2)
        original_column = get_column_letter(index + 7)
        cell(f"{column}21", year.period_end.year, 344)
        cell(f"{column}22", round(book.number("Balance sheet", f"{original_column}22") - book.number("Balance sheet", f"{original_column}47"), 8), 119,
             f"ROUND('Balance sheet'!{original_column}22-'Balance sheet'!{original_column}47,8)")
        cell(f"{column}23", round(book.number("Balance sheet", f"{original_column}8") - book.number("Cash flow", f"{original_column}56"), 8), 119,
             f"ROUND('Balance sheet'!{original_column}8-'Cash flow'!{original_column}56,8)")
        cell(f"{column}24", year.operating_expense_detail_gap_millions, 119,
             f"'Income statement'!{original_column}15-SUM('Income statement'!{original_column}12:{original_column}14)")
    cell("A22", "Assets less liabilities and equity")
    cell("A23", "Balance sheet less ending cash")
    cell("A24", "Operating expense detail gap")
    cell("A26", "Guided overrides", 34)
    for address, label in (("A27", "Target"), ("B27", "Original"), ("C27", "Applied"), ("D27", "Reason")):
        cell(address, label, 50)
    if not inputs.override_log:
        cell("A28", "Original archived assumptions.")
        merges.append("A28:H28")
    for row, override in enumerate(inputs.override_log, 28):
        cell(f"A{row}", override.target)
        cell(f"B{row}", override.before)
        cell(f"C{row}", override.value)
        cell(f"D{row}", override.reason)
        merges.append(f"D{row}:H{row}")
    row_xml = "".join(
        f'<row r="{row}" ht="24" customHeight="1">'
        + "".join(fragment for _, fragment in sorted(values))
        + "</row>"
        for row, values in sorted(rows.items())
    )
    merge_xml = "".join(f'<mergeCell ref="{area}"/>' for area in merges)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<worksheet xmlns="{MAIN_NS}"><dimension ref="A1:H{max(rows)}"/>'
        '<sheetViews><sheetView workbookViewId="0" showGridLines="0"><pane ySplit="9" topLeftCell="A10" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
        '<sheetFormatPr defaultRowHeight="24"/><cols><col min="1" max="1" width="38" customWidth="1"/>'
        '<col min="2" max="8" width="18" customWidth="1"/></cols>'
        f'<sheetData>{row_xml}</sheetData><mergeCells count="{len(merges)}">{merge_xml}</mergeCells>'
        '<conditionalFormatting sqref="B22:G24"><cfRule type="expression" dxfId="1" priority="1">'
        '<formula>ABS(B22)&gt;0.000001</formula></cfRule></conditionalFormatting>'
        '<pageMargins left="0.3" right="0.3" top="0.5" bottom="0.5" header="0.2" footer="0.2"/>'
        '<pageSetup orientation="landscape" fitToWidth="1" fitToHeight="0"/></worksheet>'
    ).encode()


def _append_sheet(package: dict[str, bytes], contents: bytes) -> None:
    workbook = package["xl/workbook.xml"]
    root = ET.fromstring(workbook)
    sheets = root.find(_tag("sheets"))
    sheet_id = max(int(sheet.get("sheetId")) for sheet in sheets) + 1
    relationships = ET.fromstring(package["xl/_rels/workbook.xml.rels"])
    relation_id = f"rId{max(int(item.get('Id')[3:]) for item in relationships) + 1}"
    name = f"sheet{len(sheets) + 1}.xml"
    package[f"xl/worksheets/{name}"] = contents
    entry = f'<sheet name="Run inputs" sheetId="{sheet_id}" r:id="{relation_id}"/>'.encode()
    workbook = workbook.replace(b"</sheets>", entry + b"</sheets>")
    calc = re.search(rb"<calcPr\b[^>]*/>", workbook)
    if calc is None:
        raise ValueError("The template calculation settings are missing.")
    settings = calc[0][:-2]
    settings = re.sub(rb'\s(?:calcMode|fullCalcOnLoad|forceFullCalc)="[^"]*"', b"", settings)
    workbook = workbook.replace(calc[0], settings + b' calcMode="auto" fullCalcOnLoad="1" forceFullCalc="1"/>')
    package["xl/workbook.xml"] = workbook
    relationship = f'<Relationship Id="{relation_id}" Type="{REL_NS}/worksheet" Target="worksheets/{name}"/>'.encode()
    package["xl/_rels/workbook.xml.rels"] = package["xl/_rels/workbook.xml.rels"].replace(b"</Relationships>", relationship + b"</Relationships>")
    content_type = f'<Override PartName="/xl/worksheets/{name}" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'.encode()
    package["[Content_Types].xml"] = package["[Content_Types].xml"].replace(b"</Types>", content_type + b"</Types>")
    application = package["docProps/app.xml"]
    application = re.sub(rb"(<HeadingPairs>.*?<vt:i4>)\d+(</vt:i4>)", lambda match: match[1] + str(len(sheets) + 1).encode() + match[2], application, count=1, flags=re.DOTALL)
    application = re.sub(rb'(<TitlesOfParts><vt:vector size=")\d+("[^>]*>)(.*?)(</vt:vector>)', lambda match: match[1] + str(len(sheets) + 1).encode() + match[2] + match[3] + b"<vt:lpstr>Run inputs</vt:lpstr>" + match[4], application, count=1, flags=re.DOTALL)
    package["docProps/app.xml"] = application


def export_gd_excel(template: Path, output: Path, *, guided: Path | None = None) -> GDExcelExport:
    """Create a new formula-driven archived workbook using the approved source."""
    template, output = Path(template), Path(output)
    if output.suffix.lower() != ".xlsx":
        raise ValueError("The Excel destination must have an .xlsx extension.")
    if template.resolve() == output.resolve() or output.exists() or output.is_symlink():
        raise ValueError("Choose a new output path. Existing workbooks are never overwritten.")
    contents = read_baseline_bytes(template)
    fixture = load_workbook_inputs(template, MODEL_INPUT_SHEETS)
    inputs = load_guided_overrides(guided, fixture.inputs) if guided is not None else fixture.inputs
    report = run_projection(inputs)
    book = ForecastBook(inputs, _formulas())
    with ZipFile(BytesIO(contents)) as original:
        entries = original.infolist()
        package = {entry.filename: original.read(entry) for entry in entries}
    paths = _sheet_paths(package)
    worksheets = {name: _Worksheet(package[path]) for name, path in paths.items()}
    date_style = _append_date_style(package)

    for (sheet, row, index), value in inputs.assumptions.items():
        worksheets[sheet].set(f"{get_column_letter(index + 7)}{row}", value)
    forecast_count = 0
    for cell in sorted(fixture.forecast_formula_cells):
        sheet, row, index = cell
        column = get_column_letter(index + 7)
        if (sheet, row) in UNAVAILABLE_FORMULAS:
            worksheets[sheet].set(f"{column}{row}", "n.a.", formula=f'IF(COUNT({column}81:{column}84)=4,SUM({column}81:{column}84),"n.a.")')
            continue
        formula = f"-{column}9*'Run inputs'!$B$9" if sheet == "Equity" and row == 10 else None
        worksheets[sheet].set(f"{column}{row}", book.value(cell), formula=formula, cache_only=formula is None)
        forecast_count += 1

    for row, selector in inputs.scenarios.items():
        worksheets["Cover"].set(f"I{row}", selector)
    worksheets["Cover"].set("E11", to_excel(datetime.combine(inputs.as_of, datetime.min.time())))
    worksheets["Cover"].set("D9", "Archived model")
    worksheets["Cover"].set("D17", "Unverified archived quote")
    for address in ("E15", "E20", "E21", "E22", "E23"):
        worksheets["Cover"].set(address, "n.a.")
    worksheets["Cover"].set("E18", report.intrinsic_usd_per_share, cache_only=True)
    worksheets["Cover"].set("E19", report.twelve_month_usd_per_share, cache_only=True)
    worksheets["Ke"].set("D32", inputs.cost_of_equity, formula="IF(ISNUMBER('Run inputs'!B8),'Run inputs'!B8,SUMPRODUCT(D29:D31,E29:E31))")
    sensitivity_count = _dcf_caches(worksheets["DCF"], inputs, book, report)
    for column in "DEFGHI":
        worksheets["DCF"].restyle(f"{column}14", date_style)
        worksheets["DCF"].restyle(f"{column}38", date_style)
    frozen = _comps_snapshot(package, worksheets)
    for name, sheet in worksheets.items():
        package[paths[name]] = sheet.export()
    _append_sheet(package, _run_inputs_sheet(inputs, report, book, template))

    removed = {name for name in package if name.startswith("xl/webextensions/") or name == "xl/calcChain.xml"}
    for name in removed:
        del package[name]
    for name in ("_rels/.rels", "xl/_rels/workbook.xml.rels"):
        package[name] = re.sub(rb'<Relationship\b[^>]*(?:webextension[^>]*|calcChain[^>]*)/>', b"", package[name])
    package["[Content_Types].xml"] = re.sub(rb'<Override\b[^>]*(?:/webextensions/[^>]*|/calcChain.xml[^>]*)/>', b"", package["[Content_Types].xml"])

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(suffix=".xlsx", dir=output.parent, delete=False) as handle:
            temporary = Path(handle.name)
        with ZipFile(temporary, "w") as destination:
            existing = set()
            for entry in entries:
                if entry.filename in package:
                    destination.writestr(entry, package[entry.filename])
                    existing.add(entry.filename)
            for name in package.keys() - existing:
                destination.writestr(name, package[name])
        if os.name == "nt":
            # Windows rename is atomic and fails if another writer created output.
            os.rename(temporary, output)
        else:
            os.link(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return GDExcelExport(output, report, forecast_count, sensitivity_count, frozen)

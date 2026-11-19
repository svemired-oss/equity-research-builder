"""Render the existing workbook plan with a local spreadsheet engine."""

import json
from math import ceil, isclose, isfinite
from pathlib import Path
from shutil import which
from subprocess import run
from tempfile import TemporaryDirectory
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from openpyxl import Workbook, load_workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation


def render_workbook(plan, output, node=None, modules=None, *, previews=None):
    if node is not None:
        specification = output.parent / "workbook-plan.json"
        specification.write_text(json.dumps(plan, allow_nan=False), encoding="utf-8")
        renderer = Path(__file__).with_name("fdx_fresh_excel.mjs")
        command = [str(node), str(renderer), str(specification), str(output), str(modules)]
        if previews is not None:
            command.append(str(previews))
        run(command, check=True, timeout=120)
        return
    if previews is not None:
        raise ValueError("Workbook image previews require the optional Node spreadsheet runtime.")
    office = which("soffice")
    if not office:
        raise ValueError("Install LibreOffice Calc and put soffice on PATH.")
    book = _author(plan)
    with TemporaryDirectory(prefix="calc-", dir=output.parent) as directory:
        stage = Path(directory)
        for case, expected in plan["scenarioExpected"].items():
            if case != plan["case"]:
                _calculate(book, plan, case, expected, office, stage).close()
        probe = plan.get("recalculationProbe")
        if probe:
            case = probe.get("case", plan["case"])
            expected = plan["expected"] if case == plan["case"] else plan["scenarioExpected"][case]
            before = _calculate(book, plan, case, expected, office, stage)
            change = probe["input"]
            cell = book[change["sheet"]][change["address"]]
            original = cell.value
            cell.value += change["delta"]
            after = _calculate(book, plan, case, probe.get("expected", {}), office, stage)
            try:
                for key, stable in (("changed", False), ("unchanged", True)):
                    for point in probe[key]:
                        sheet, address = point["sheet"], point["address"]
                        value = after[sheet][address].value
                        if (not stable and (type(value) not in (int, float) or not isfinite(value))
                                or _matches(value, before[sheet][address].value) != stable):
                            raise ValueError(f"Assumption edit failed the workbook check at {sheet}!{address}.")
            finally:
                cell.value = original
                before.close()
                after.close()
        _calculate(book, plan, plan["case"], plan["expected"], office, stage).close()
        _save_caches(stage / "model.xlsx", stage / "recalculated" / "model.xlsx", output)


def _author(plan):
    book = Workbook()
    book.remove(book.active)
    palette = plan.get("palette", {})
    navy = palette.get("navy", "#17365D").lstrip("#")
    pale = palette.get("pale", "#DDEBF7").lstrip("#")
    editable = palette.get("editable", "#FFF2CC").lstrip("#")
    face = palette.get("font", "Calibri")
    widths = {"Cover": [39, 21, 20, 20, 19], "Comps": [48, 21, 21, 21, 21],
              "Sources": [42, 18, 80, 70], "Checks": [53], "Assumptions": [46], "Actuals": [48]}
    for name, records in plan["cells"].items():
        sheet = book.create_sheet(name)
        sheet.sheet_view.showGridLines = False
        sheet.sheet_format.defaultRowHeight = 16
        sheet.sheet_format.defaultColWidth = 16
        column_widths = widths.get(name, [43])
        for index, width in enumerate(column_widths, 1):
            sheet.column_dimensions[get_column_letter(index)].width = width
        for address, (value, kind, number_format) in records.items():
            cell = sheet[address]
            cell.value = value
            color = {"title": navy, "section": "FFFFFF", "header": navy, "input": "154DA1",
                     "total": navy, "note": "4E5D6B", "subtitle": "4E5D6B"}.get(kind, "172332")
            if cell.data_type == "f" and "!" in value and kind == "text":
                color = "17653C"
            if cell.data_type == "f" and plan.get("formulaColor"):
                color = plan["formulaColor"]
            source = plan.get("inputSources", {}).get(name, {}).get(address)
            if source:
                from openpyxl.worksheet.hyperlink import Hyperlink

                cell.hyperlink = Hyperlink(ref=address, location=f"'Sources'!{source}",
                                           tooltip="Open input source and basis")
                color = "154DA1"
            elif name == "Sources" and isinstance(value, str) and value.startswith("https://"):
                cell.hyperlink = value
            cell.font = Font(name=face, size=16 if kind == "title" else 10, color=color,
                             bold=kind in ("title", "section", "header", "total"), italic=kind == "subtitle")
            fill = {"section": navy, "header": pale, "input": "F1F6FF", "total": "EEF4F8"}.get(kind)
            if fill:
                cell.fill = PatternFill("solid", fgColor=fill)
            width = column_widths[cell.column - 1] if cell.column <= len(column_widths) else 16
            wrap = cell.data_type == "s" and bool(value) and len(value) > width and (
                cell.column == 1 or kind in ("note", "subtitle", "header") or name == "Sources")
            cell.alignment = Alignment(vertical="center", wrap_text=wrap,
                                       horizontal="center" if kind == "header" and cell.column > 1 else None)
            if wrap or kind == "title":
                height = sheet.row_dimensions[cell.row]
                height.height = max(height.height or 16, 27 if kind == "title" else 7 + 14 * ceil(len(value) / width))
            if number_format:
                cell.number_format = number_format
            elif type(value) in (int, float) or (cell.data_type == "f" and kind != "header"):
                cell.number_format = '#,##0.0;(#,##0.0);"-"'
            if kind == "check" and cell.data_type == "f":
                sheet.conditional_formatting.add(address, FormulaRule(
                    formula=[f"IFERROR(ABS({address})>0.01,TRUE)"],
                    fill=PatternFill("solid", fgColor="FCE8E6")))
        if name in ("Cover", "DCF", "Comps"):
            sheet.sheet_properties.tabColor = navy
        if name in ("Comps", "Sources"):
            sheet.freeze_panes = "A6" if name == "Comps" else "A5"
    selector = book["Cover"]["B5"]
    selector.fill = PatternFill("solid", fgColor=editable)
    options = ",".join(plan["scenarioControls"].values())
    validation = DataValidation(type="list", formula1=f'"{options}"')
    book["Cover"].add_data_validation(validation)
    validation.add(selector)
    return book


def _matches(value, expected):
    if type(expected) in (int, float):
        return type(value) in (int, float) and isclose(value, expected, rel_tol=1e-10, abs_tol=1e-6)
    return value == expected or (expected is None and value == "")


def _calculate(book, plan, case, expected, office, stage):
    book["Cover"]["B5"] = plan["scenarioControls"][case]
    source = stage / "model.xlsx"
    destination = stage / "recalculated" / source.name
    destination.unlink(missing_ok=True)
    book.save(source)
    result = run([office, f"-env:UserInstallation={(stage / 'profile').as_uri()}", "--headless",
                  "--convert-to", "xlsx:Calc MS Excel 2007 XML", "--outdir", str(destination.parent), str(source)],
                 capture_output=True, text=True, errors="replace", timeout=60)
    if result.returncode or not destination.is_file():
        raise ValueError(f"LibreOffice could not calculate the workbook. {result.stderr.strip()}")
    calculated = load_workbook(destination, data_only=True)
    try:
        for sheet in calculated:
            for row in sheet.iter_rows():
                for cell in row:
                    if cell.data_type == "e":
                        raise ValueError(f"Workbook formula error at {sheet.title}!{cell.coordinate}: {cell.value}")
        for name, checks in expected.items():
            for address, value in checks.items():
                actual = calculated[name][address].value
                if not _matches(actual, value):
                    raise ValueError(f"{case} {name}!{address} calculated {actual!r}, expected {value!r}.")
    except Exception:
        calculated.close()
        raise
    return calculated


def _save_caches(source, calculated, output):
    # Keep the authored Excel formulas and formatting. Copy only the results
    # from Calc, which can rewrite formula expressions on export.
    namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    ET.register_namespace("", namespace)
    tag = lambda name: f"{{{namespace}}}{name}"
    with ZipFile(source) as original, ZipFile(calculated) as cache, ZipFile(output, "w") as final:
        for entry in original.infolist():
            data = original.read(entry)
            if entry.filename.startswith("xl/worksheets/sheet") and entry.filename.endswith(".xml"):
                root, computed = ET.fromstring(data), ET.fromstring(cache.read(entry.filename))
                results = {cell.get("r"): cell for cell in computed.iter(tag("c"))}
                strings = None
                for cell in root.iter(tag("c")):
                    if cell.find(tag("f")) is None:
                        continue
                    address = cell.get("r")
                    calc = results.get(address)
                    cached = None if calc is None else calc.find(tag("v"))
                    if cached is None:
                        raise ValueError(f"LibreOffice did not calculate {entry.filename} {address}.")
                    value = cell.find(tag("v"))
                    kind = calc.get("t", "n")
                    if kind == "s":
                        if strings is None:
                            strings = ET.fromstring(cache.read("xl/sharedStrings.xml"))
                        value.text = "".join(t.text or "" for t in strings[int(cached.text)].iter(tag("t")))
                        kind = "str"
                    else:
                        value.text = cached.text
                    cell.set("t", kind)
                data = ET.tostring(root, encoding="utf-8")
            final.writestr(entry, data)

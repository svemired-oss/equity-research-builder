"""Place dated issuer calculations on the shared workbook spine."""

from dataclasses import replace
from pathlib import Path
import re
from tempfile import TemporaryDirectory

from openpyxl import load_workbook
from openpyxl.formula.tokenizer import Tokenizer

from . import ordered_sheets
from ..workbook_renderer import render_workbook


ALIASES = {
    "GD":{"Revenue model":"Revenue Build","Operating inputs":"Assumptions"},
    "FDX":{"Revenue":"Revenue Build"},
    "NEM":{"Operations":"Revenue Build"},
}
STATEMENTS = {"Income statement":"Income Statement","Balance sheet":"Balance Sheet",
              "Cash flow":"Cash Flow Statement","PP&E":"Schedules"}
PRICE = '"$"#,##0.00'
PCT = "0.00%"
POLICIES = {
    "GD": {"selector":"E13","close":"E17","spot":"E18","target":"E19",
           "rates":("DCF!B6","'Valuation inputs'!B6","'Valuation inputs'!B7","DCF!B15"),
           "spot_grid":"C112:I118","target_grid":"C123:I129",
           "method":"Levered free cash flow and cost of equity"},
    "FDX":{"selector":"B5","close":"B4","spot":"B9","target":"B10",
           "rates":("DCF!B27","DCF!B7","DCF!B8","DCF!B23"),
           "spot_grid":"C62:I68","target_grid":"C74:I80",
           "method":"Continuing-operations FCFF and market-weighted WACC"},
    "NEM":{"selector":"B5","close":"B4","spot":"B9","target":"B10",
           "rates":("DCF!B8","DCF!B19","DCF!B20","DCF!B24"),
           "spot_grid":"B11:F15","target_grid":"B20:F24",
           "method":"Finite mine-life NAV and market-weighted WACC"},
}


def _rename(value, names):
    """Translate only Excel operand tokens. Leave labels and string constants intact."""
    if not isinstance(value,str) or not value.startswith("="):
        return value
    tokens = Tokenizer(value)
    for token in tokens.items:
        if (token.type,token.subtype) != ("OPERAND","RANGE") or "!" not in token.value:
            continue
        def rename(match):
            old = match["sheet"].strip("'").replace("''","'")
            return f"'{names[old]}'!" if old in names else match[0]
        token.value = re.sub(r"(?P<sheet>'(?:[^']|'')+'|[A-Za-z_][A-Za-z0-9_.]*)!",rename,token.value)
        # Excel accepts a sheet qualifier on both ends of a same-sheet range.
        # Calc needs the single-qualifier form when reading OOXML.
        token.value = re.sub(r"^('(?:[^']|'')+'|[A-Za-z_][A-Za-z0-9_.]*)!(\$?[A-Z]+\$?\d+):\1!",
                             r"\1!\2:",token.value)
    return tokens.render()


def reference_plan(ticker, original):
    names = {**STATEMENTS,**ALIASES[ticker]}
    config = POLICIES[ticker]
    cells = {names.get(tab,tab):{address:(_rename(value,names),kind,fmt)
                                for address,(value,kind,fmt) in records.items()}
             for tab,records in original["cells"].items()}
    def put(tab, address, value, kind="text", fmt=None):
        cells.setdefault(tab,{})[address]=(value,kind,fmt)
    put("WACC","A1","Cost of capital","title")
    put("WACC","A2",config["method"])
    for row,label in enumerate(("Required discount rate","Treasury rate","Equity risk premium","Levered beta"),4):
        put("WACC",f"A{row}",label)
        put("WACC",f"B{row}","="+config["rates"][row-4],fmt=PCT if row!=7 else "0.00")
    spot_grid,target_grid=(config[key] for key in ("spot_grid","target_grid"))
    source = "Sensitivity" if ticker=="NEM" else "DCF"
    if "Sensitivity" not in cells:
        put("Sensitivity","A1","Dated valuation sensitivities","title")
        put("Sensitivity","A2","Rates, inputs and source grids are on the DCF tab.")
        for first,grid,label in ((5,spot_grid,"Spot value per share"),(15,target_grid,"Target price per share")):
            put("Sensitivity",f"A{first-1}",label,"section")
            first_col, start = grid.split(":")[0][0],int(grid.split(":")[0][1:])
            for r in range(7):
                for c in range(7):
                    dest = f"{chr(66+c)}{first+r}"
                    origin = f"{chr(ord(first_col)+c)}{start+r}"
                    put("Sensitivity",dest,f'=IF(ISNUMBER(DCF!{origin}),DCF!{origin},"")',fmt=PRICE)
    put("Football Field","A1","Dated valuation ranges","title")
    for col,label in zip("ABCD",("Method","Low","High","Reference"),strict=True):
        put("Football Field",f"{col}4",label,"header")
    for row,label,grid,key in ((5,"Spot sensitivity",spot_grid,"spot"),(6,"Target sensitivity",target_grid,"target")):
        put("Football Field",f"A{row}",label)
        put("Football Field",f"B{row}",f"=MIN('{source}'!{grid})",fmt=PRICE)
        put("Football Field",f"C{row}",f"=MAX('{source}'!{grid})",fmt=PRICE)
        put("Football Field",f"D{row}",f"=Cover!{config[key]}",fmt=PRICE)
    put("Football Field","A7","Reference close")
    put("Football Field","D7",f"=Cover!{config['close']}",fmt=PRICE)
    put("Football Field","A9","Fundamental methods and coverage vary by issuer. Sources and inputs retain their dated basis.")
    def remap_checks(values):
        return {names.get(tab,tab):records for tab,records in values.items()}
    if ticker=="GD":
        from ..gd_fresh_pdf_workbook import _expected_caches
        scenarios={case:remap_checks(_expected_caches(original,case)) for case in ("bear","base","bull")}
    else:
        scenarios={case:remap_checks(checks) for case,checks in original["scenarioExpected"].items()}
    probe=original.get("recalculationProbe")
    if probe:
        probe={**probe,"input":{**probe["input"],"sheet":names.get(probe["input"]["sheet"],probe["input"]["sheet"])}}
        for key in ("changed","unchanged"):
            probe[key]=[{**item,"sheet":names.get(item["sheet"],item["sheet"])} for item in probe[key]]
    return {**original,"cells":ordered_sheets(cells),"expected":scenarios[original["case"]],
            "scenarioExpected":scenarios,"scenarioControls":{case:case.title() for case in scenarios},
            "caseSelector":config["selector"],"formulaColor":"000000","recalculationProbe":probe,
            "preserveLiteralNumbers":True,
            "referenceSheetAliases":names}


def load_reference(ticker, workbook, original):
    from ..gd_fresh_pdf_workbook import _expected_caches, load_verified_workbook
    # Older published files remain readable for the dated parity comparison.
    reader = load_workbook(workbook,read_only=True,keep_links=False)
    try:
        canonical = "Revenue Build" in reader
    finally:
        reader.close()
    if not canonical:
        return load_verified_workbook(workbook,original["cells"],
                                      _expected_caches(original,original["case"]) if ticker=="GD" else original["expected"])
    plan=reference_plan(ticker,original)
    snapshot=load_verified_workbook(workbook,plan["cells"],plan["expected"])
    cached=snapshot._cache
    aliases={old:cached.sheets[new] for old,new in plan["referenceSheetAliases"].items()}
    return replace(snapshot,_cache=replace(cached,sheets={**cached.sheets,**aliases}))


def export_reference(ticker, output, original):
    """Render once through the shared headless engine and publish only verified bytes."""
    if output.suffix.lower()!=".xlsx" or output.exists():
        raise ValueError("Choose a new .xlsx filename.")
    output.parent.mkdir(parents=True,exist_ok=True)
    with TemporaryDirectory(dir=output.parent) as directory:
        workbook=Path(directory)/"model.xlsx"
        render_workbook(reference_plan(ticker,original),workbook)
        verified=load_reference(ticker,workbook,original)
        if output.exists():
            raise ValueError("The workbook filename already exists.")
        workbook.rename(output)
    return verified.cells_matched


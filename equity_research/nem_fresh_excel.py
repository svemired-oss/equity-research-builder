"""Generate and verify the editable Newmont research model for a dated snapshot."""

from datetime import date
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

from openpyxl.utils.datetime import to_excel

from .fdx_statements import PCT, USD, WorkbookPlan
from .gd_fresh_pdf_workbook import VerifiedPDFWorkbook, load_verified_workbook
from .nem_current import _evidence
from .nem_forward_statements import run_nem_statements
from .nem_fresh_excel_mines import add_nem_mines
from .nem_fresh_excel_nav import add_nem_nav
from .nem_fresh_excel_operations import labels, sheet_title
from .nem_fresh_excel_statements import BALANCE, add_nem_statements
from .nem_fresh_excel_operations import add_nem_operations
from .nem_nav import run_nem_nav


CASES = ("bear", "base", "bull")
SHEETS = ("Cover", "Assumptions", "DCF", "Sensitivity", "Comps", "Income statement", "Balance sheet", "Cash flow", "Operations",
          "Prices", "PP&E", "Debt schedule", "Working capital", "Mine cash", "Mine plan", "Checks", "Actuals", "Sources")
DAY = "mmm d, yyyy"
MULTIPLE = '0.00"x"'


def _cover_and_assumptions(plan, nav, statements, costs, prices, value, case):
    cover, tab = "Cover", "Assumptions"
    sheet_title(plan, cover, "Newmont Corporation | NYSE: NEM", "Finite mine-life valuation and financial model")
    sheet_title(plan, tab, "Newmont model assumptions", "Blue inputs are editable. Select Bear, Base or Bull on the cover.")
    labels(plan, cover, {3: "Valuation cutoff (pre-open)", 4: "Reference close", 5: "Selected case", 6: "Input mode", 7: "Reference close date",
                        8: "Nominal WACC", 9: "Spot NAV per share", 10: "12-month target per share", 11: "12-month total return",
                        12: "Model recommendation", 13: "Peer median implied spot value", 14: "Target date", 15: "12-month dividends per share"},
           totals=(9,10,11,12))
    selected = nav["scenarios"][case]
    plan.set(cover, "B5", case.title(), "input")
    for row, formula, result, fmt in (
        (3,"Assumptions!B3",to_excel(nav["as_of"]),DAY), (4,"Assumptions!B4",nav["reference_close"]["price_usd"],USD),
        (6,"Assumptions!B6",nav["mode"],None), (7,"Assumptions!B7",to_excel(date.fromisoformat(nav["reference_close"]["observed_on"])),DAY),
        (8,"DCF!B8",nav["cost_of_capital"]["wacc"],PCT), (9,"DCF!B9",selected["spot"]["value_per_share"],USD),
        (10,"DCF!B10",selected["target"]["value_per_share"],USD), (11,"DCF!B11",selected["total_return"],PCT),
        (12,"DCF!B12",selected["rating"],None), (13,"Comps!B10",nav["comparables"]["median_implied_nem_spot_usd"],USD),
        (14,"Assumptions!B14",to_excel(nav["horizon"]),DAY), (15,"DCF!B15",selected["dividends_per_share"],USD),
    ):
        plan.calc(cover, f"B{row}", formula, result, fmt=fmt, kind="total" if row in (9,10,11,12) else "")
    for row, text in (
        (17,"Published comparison at the November 12 snapshot"),
        (24,"The live valuation follows the selected case and workbook inputs. Published cases match the dated research PDF."),
        (25,"Mine plan contains editable reserves and production assumptions. Prices contains the forecast benchmarks."),
        (26,"Use the paired research PDF for the investment thesis and catalysts. Sources records dated evidence and model limitations."),
    ):
        plan.set(cover, f"A{row}", text, "section" if row == 17 else "note")
    for col, name in zip("BCD", CASES, strict=True):
        outcome = nav["scenarios"][name]
        plan.set(cover, f"{col}18", name.title(), "header")
        for row, amount, fmt in ((19,outcome["spot"]["value_per_share"],USD),(20,outcome["target"]["value_per_share"],USD),
                                 (21,outcome["total_return"],PCT),(22,outcome["rating"],None)):
            plan.set(cover, f"{col}{row}", amount, "actual", fmt)
    labels(plan, cover, {19:"Spot NAV per share",20:"12-month target per share",21:"12-month total return",22:"Model recommendation"})
    assumptions = statements["operating_forecast"]["assumptions"]
    sense = costs["sensitivities"]
    for row, label, amount, fmt, kind in (
        (3,"Valuation cutoff (pre-open)",to_excel(nav["as_of"]),DAY,"actual"),
        (4,"Reference close (USD)",nav["reference_close"]["price_usd"],USD,"actual"),
        (6,"Input mode",nav["mode"],None,"actual"),
        (7,"Reference close date",to_excel(date.fromisoformat(nav["reference_close"]["observed_on"])),DAY,"actual"),
        (14,"Target date",to_excel(nav["horizon"]),DAY,"actual"), (16,"Long-term nominal escalation",value["inflation"]["value"],PCT,"input"),
        (25,"Gold-linked royalty and production tax slope",sense["gold_related_royalty_production_tax_and_profit_sharing_cost_change_usd_per_ounce"]/sense["gold_price_change_usd_per_ounce"],PCT,"input"),
        (26,"Pounds per metric tonne",prices["pounds_per_metric_tonne"],"#,##0.000", "actual"),
        (27,"Peñasquito silver stream share",prices["silver_stream"]["share_of_production"],PCT,"input"),
        (28,"Peñasquito silver stream cash price",prices["silver_stream"]["cash_price_usd"],USD,"input"),
        (29,"FY2027 attributable gold growth",assumptions["fy_2027.attributable_gold_growth"],PCT,"input"),
        (30,"FY2027 Peñasquito co-product change",assumptions["fy_2027.penasquito_coproduct_change"],PCT,"input"),
        (31,"FY2027 unit CAS growth",assumptions["fy_2027.unit_cas_growth"],PCT,"input"),
        (32,"Income and mining tax proxy",costs["guidance_2026"]["full_year"]["adjusted_income_and_mining_tax_rate"],PCT,"input"),
    ):
        plan.set(tab,f"A{row}",label)
        plan.set(tab,f"B{row}",amount,kind,fmt)
    plan.set(tab,"A5","Selected case")
    plan.calc(tab,"B5","Cover!B5",case.title())
    plan.set(tab,"A17","Share of Q4 remaining at cutoff")
    plan.calc(tab,"B17","(DATE(YEAR(B3)+1,1,1)-B3)/(DATE(YEAR(B3)+1,1,1)-DATE(YEAR(B3),10,1))",
              statements["cutoff"]["calendar_days_remaining"]/92,fmt=PCT)
    plan.set(tab,"A19","Case assumptions and active selection","section")
    for col,name in zip("CDE",CASES,strict=True):
        plan.set(tab,f"{col}18",name.title(),"header")
    for row,label,key,fmt in ((20,"Gold benchmark shift per ounce","gold_shift_usd",USD),
                              (21,"Other metal benchmark shift","coproduct_price_shift",PCT),
                              (22,"Site unit cost shift","site_cost_shift",PCT)):
        plan.set(tab,f"A{row}",label)
        for col,name in zip("CDE",CASES,strict=True):
            plan.set(tab,f"{col}{row}",nav["scenarios"][name][key],"input",fmt)
        plan.calc(tab,f"B{row}",f"INDEX(C{row}:E{row},1,MATCH($B$5,$C$18:$E$18,0))",selected[key],fmt=fmt)
    plan.set(tab,"A23","Active nominal WACC")
    plan.calc(tab,"B23","DCF!B26",nav["cost_of_capital"]["wacc"],fmt=PCT)
    plan.set(tab,"A34","Guided input history","section")
    overrides = statements["operating_forecast"]["override_log"]
    if overrides:
        for col,label in zip("ABCD",("Driver","Default","Guided","Reason"),strict=True):
            plan.set(tab,f"{col}35",label,"header")
        for row,override in enumerate(overrides,36):
            for col,key in zip("ABCD",("target","before","value","reason"),strict=True):
                plan.set(tab,f"{col}{row}",override[key],"note" if col=="D" else "actual")
    else:
        plan.set(tab,"A35","Dated automatic inputs are in use.","note")


def _comps(plan, nav, statements, packet):
    sheet = "Comps"
    comps = nav["comparables"]
    sheet_title(plan,sheet,"Gold miner trading comparables","Unadjusted November 11 closes and trailing reported diluted EPS")
    for col,value in zip("ABCDE",("Reported earnings","NEM",*comps["peers"]),strict=True):
        plan.set(sheet,f"{col}4",value,"header")
    labels(plan,sheet,{6:"Unadjusted close (USD)",7:"Trailing reported diluted EPS",8:"Trailing reported P/E",9:"Peer median P/E",
                       10:"Peer implied NEM spot value",13:"Q4 2025 reported diluted EPS",14:"9M2026 reported diluted EPS"},totals=(8,10))
    subject = statements["financing_evidence"]["actuals"]
    plan.set(sheet,"B13",subject["q4_2025"]["diluted_eps_usd"],"actual",USD)
    plan.set(sheet,"B14",subject["ytd_2026"]["diluted_eps_usd"],"actual",USD)
    plan.calc(sheet,"B6","Assumptions!B4",nav["reference_close"]["price_usd"],fmt=USD)
    for col,(ticker,peer) in zip("CDE",comps["peers"].items(),strict=True):
        record = packet["peers"][ticker]
        for row,key in ((6,"close_usd"),(13,"q4_2025_eps"),(14,"ytd_2026_eps")):
            plan.set(sheet,f"{col}{row}",record[key],"actual",USD)
        plan.calc(sheet,f"{col}7",f"SUM({col}13:{col}14)",peer["reported_ltm_diluted_eps_usd"],fmt=USD)
        plan.calc(sheet,f"{col}8",f"{col}6/{col}7",peer["price_to_earnings"],fmt=MULTIPLE)
        plan.calc(sheet,f"{col}10",f"{col}8*$B$7",peer["implied_nem_spot_usd"],fmt=USD)
    plan.calc(sheet,"B7","SUM(B13:B14)",comps["subject_ltm_eps_usd"],fmt=USD)
    plan.calc(sheet,"B8","B6/B7",comps["subject_price_to_earnings"],fmt=MULTIPLE)
    plan.calc(sheet,"B9","MEDIAN(C8:E8)",comps["median_price_to_earnings"],fmt=MULTIPLE)
    plan.calc(sheet,"B10","B9*B7",comps["median_implied_nem_spot_usd"],fmt=USD,kind="total")
    plan.set(sheet,"A17",comps["basis"],"note")


def _checks_and_sources(plan, nav):
    tab = "Checks"
    sheet_title(plan,tab,"Model reconciliation","USD millions unless specified. Differences above 0.01 are highlighted.")
    for col,label in zip("ABCD",("Control","Q4 2026E","FY2026E","FY2027E"),strict=True):
        plan.set(tab,f"{col}4",label,"header")
    plan.set(tab,"A15","Valuation and mine controls","section")
    for col,dest,prior in (("E","B","D"),("F","C","D"),("G","D","E")):
        for row,label,expr in (
            (6,"Balance sheet assets less liabilities and equity",f"'Balance sheet'!{col}{BALANCE['total_assets']}-'Balance sheet'!{col}{BALANCE['total_liabilities_and_equity']}"),
            (7,"Closing cash statement to balance sheet",f"'Cash flow'!{col}27-'Balance sheet'!{col}6"),
            (8,"Income attributable to Newmont",f"'Income statement'!{col}29+'Income statement'!{col}30-'Income statement'!{col}31"),
        ):
            plan.set(tab,f"A{row}",label)
            plan.calc(tab,f"{dest}{row}",expr,0,kind="check")
        if col == "F":
            plan.set(tab,"A9","FY2026 cash including restricted cash roll")
            plan.calc(tab,"C9","'Cash flow'!F44+'Cash flow'!F28-'Cash flow'!F45",0,kind="check")
            continue
        for row,label,expr in (
            (10,"Net PP&E additions and depreciation",f"'PP&E'!{col}6+'PP&E'!{col}9-'PP&E'!{col}10-'PP&E'!{col}11"),
            (11,"Reclamation and remediation roll",f"'PP&E'!{col}13-'PP&E'!{col}14+'PP&E'!{col}15-'PP&E'!{col}16"),
            (12,"Operating cash roll",f"'Cash flow'!{col}24+SUM('Cash flow'!{col}13,'Cash flow'!{col}19,'Cash flow'!{col}23)-'Cash flow'!{col}27"),
            (13,"Operating statement to mine sales",f"'Income statement'!{col}6-Operations!{col}9"),
        ):
            plan.set(tab,f"A{row}",label)
            plan.calc(tab,f"{dest}{row}",expr,0,kind="check")
    for row,label,expr in ((16,"Maximum post-2027 reserve cap overrun","MAX('Mine cash'!B16:AG16)"),
                          (17,"Spot equity NAV and diluted shares","DCF!B13/'Debt schedule'!G21-Cover!B9"),
                          (18,"Target equity NAV and diluted shares","DCF!B14/'Debt schedule'!G21-Cover!B10")):
        plan.set(tab,f"A{row}",label)
        plan.calc(tab,f"B{row}",expr,0,kind="check")
    tab="Sources"
    sheet_title(plan,tab,"Sources and model assumptions")
    for col,label in zip("ABCD",("Evidence group","Available on","Source / locator","URL"),strict=True):
        plan.set(tab,f"{col}4",label,"header")
    row=5
    for group, entries in nav["source_register"].items():
        for source in entries.values():
            plan.set(tab,f"A{row}",group.replace("_"," ").replace("-"," ").title())
            observed = source.get("available_on",source.get("date"))
            if observed:
                plan.set(tab,f"B{row}",to_excel(date.fromisoformat(observed)),"actual",DAY)
            plan.set(tab,f"C{row}",source["title"]+(". "+source["locator"] if source.get("locator") else ""),"note")
            plan.set(tab,f"D{row}",source["url"])
            row+=1
    plan.set(tab,f"A{row}","Material assumptions and missing data","section")
    for row, limitation in enumerate(nav["limitations"],row+1):
        plan.set(tab,f"A{row}",limitation,"note")


def build_fresh_nem_workbook_plan(as_of, inputs_dir, *, case="base", guided=None):
    if case not in CASES:
        raise ValueError(f"Unknown Newmont scenario {case!r}.")
    nav = run_nem_nav(as_of, inputs_dir, guided=guided)
    statements = run_nem_statements(as_of, inputs_dir, guided=guided)
    data = {name: _evidence(inputs_dir,name,as_of) for name in
            ("production","prices","costs","capital","mine-life","nav-capital","nav-prices","nav-valuation","comps")}
    plans = {}
    for name in CASES:
        plan = WorkbookPlan(cells={sheet:{} for sheet in SHEETS})
        _cover_and_assumptions(plan,nav,statements,data["costs"],data["prices"],data["nav-valuation"],name)
        rows = add_nem_operations(plan,statements,data["production"],data["prices"],data["costs"],nav["scenarios"][name],case=name)
        add_nem_statements(plan,statements,data["nav-capital"],data["capital"],data["costs"],rows,case=name)
        inputs = {**data["nav-valuation"],"world_bank_2028_prices":data["nav-prices"]["world_bank_2028_prices"],
                  "royalty_slope":plan.cells["Assumptions"]["B25"][0]}
        add_nem_mines(plan,nav,statements,data["mine-life"],data["nav-capital"],data["prices"],inputs,case=name)
        add_nem_nav(plan,nav,statements,data["nav-capital"],inputs,case=name)
        _comps(plan,nav,statements,data["comps"])
        _checks_and_sources(plan,nav)
        plans[name]=plan
    selected=plans[case]
    for name, plan in plans.items():
        if any(not cells for cells in plan.cells.values()):
            raise ValueError("The Newmont workbook contains an empty worksheet.")
        for sheet, cells in selected.cells.items():
            if cells != plan.cells[sheet] and (sheet != "Cover" or any(
                record != plan.cells[sheet].get(address) for address,record in cells.items() if address != "B5"
            )):
                raise ValueError(f"The Newmont {name} case changed the shared worksheet {sheet}.")
    return {"case":case,"cells":selected.cells,"expected":selected.expected,"scenarioExpected":{name:plan.expected for name,plan in plans.items()},
            "scenarioControls":{name:name.title() for name in CASES},
            "palette":{"navy":"#15344B","pale":"#DCE8F0","editable":"#FFF2CC","font":"Calibri"},
            "recalculationProbe":{"case":"base","input":{"sheet":"Prices","address":"G21","delta":200},
                                  "changed":[{"sheet":"Operations","address":"G9"},{"sheet":"Cover","address":"B10"}],
                                  "unchanged":[{"sheet":"Actuals","address":"B6"}]}}


def load_verified_nem_workbook(workbook: Path, as_of: date, inputs_dir: Path, *, case="base", guided=None) -> VerifiedPDFWorkbook:
    plan=build_fresh_nem_workbook_plan(as_of,inputs_dir,case=case,guided=guided)
    return load_verified_workbook(workbook,plan["cells"],plan["expected"])


def export_fresh_nem_workbook(output: Path, as_of: date, inputs_dir: Path, node: Path, node_modules: Path, *,
                              case="base", guided=None, preview_dir=None):
    if output.suffix.lower() != ".xlsx" or output.exists():
        raise ValueError("Choose a new .xlsx filename for the Newmont model.")
    plan=build_fresh_nem_workbook_plan(as_of,inputs_dir,case=case,guided=guided)
    output.parent.mkdir(parents=True,exist_ok=True)
    with TemporaryDirectory(dir=output.parent) as directory:
        stage=Path(directory)
        specification=stage/"model.json"
        specification.write_text(json.dumps(plan,allow_nan=False),encoding="utf-8")
        workbook=stage/"model.xlsx"
        command=[str(node),str(Path(__file__).with_name("fdx_fresh_excel.mjs")),str(specification),str(workbook),str(node_modules)]
        if preview_dir is not None:
            command.append(str(preview_dir))
        result=subprocess.run(command,capture_output=True,text=True,encoding="utf-8",errors="replace")
        if result.returncode:
            raise ValueError(f"Workbook authoring failed. {result.stderr.strip() or result.stdout.strip()}")
        verified=load_verified_workbook(workbook,plan["cells"],plan["expected"])
        if output.exists():
            raise ValueError("The output filename was created during generation. Use a new filename.")
        workbook.rename(output)
    return verified.cells_matched

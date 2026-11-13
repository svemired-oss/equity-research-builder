"""Export and verify the editable FedEx portfolio replay."""

from dataclasses import replace
from datetime import datetime
from io import BytesIO
import json
from math import isclose
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

from openpyxl import load_workbook
from openpyxl.utils.datetime import to_excel

from .fdx_financial_inputs import load_fdx_financial_inputs
from .fdx_replay import SERVICE_ROWS
from .fdx_revenue import forecast_archived_fdx_revenue
from .fdx_statements import PCT, USD, WorkbookPlan, add_fdx_statements
from .fdx_valuation import build_fdx_valuation
from .gd_fresh_pdf_workbook import _formula_key
from .gd_replay import CachedWorkbook


SHEETS = ("Cover", "DCF", "Comps", "Revenue", "Operating expenses", "Income statement", "Balance sheet",
          "Derived cash flow", "CapEx", "Debt schedule", "Checks", "Sources")
SOURCE_SHEETS = ("DCF", "Ke", "Comps NF", "Revenue", "Operating expenses", "Income statement", "Balance sheet",
                 "CapEx", "Debt schedule")
SERVICE_NAMES = ("U.S. Priority packages", "U.S. Deferred packages", "U.S. Ground packages",
                 "International export packages", "International domestic packages", "U.S. express freight",
                 "International express freight", "LTL priority shipments", "LTL economy shipments")
SCENARIO_YIELD = 0.025
ANNUAL_2025 = "https://www.sec.gov/Archives/edgar/data/1048911/000104891125000011/fdx-20250531.htm"
ANNUAL_2023 = "https://www.sec.gov/Archives/edgar/data/1048911/000095017023033201/fdx-20230531.htm"
QUARTER = "https://www.sec.gov/Archives/edgar/data/1048911/000104891125000044/fdx-20250831.htm"
TRANSITION = "https://www.sec.gov/Archives/edgar/data/1048911/000104891126000105/fdx-20260531.htm"


def _saved_source(contents):
    with BytesIO(contents) as source:
        workbook = load_workbook(source, read_only=True, data_only=True, keep_links=False)
        try:
            return CachedWorkbook({name: tuple(workbook[name].values) for name in SOURCE_SHEETS}, workbook.epoch)
        finally:
            workbook.close()


def _revenue(plan, inputs, cached, priority_yield=None):
    sheet = "Revenue"
    plan.set(sheet, "A1", "FEDEX CORPORATION  |  REVENUE", "title")
    plan.set(sheet, "A2", "USD millions except operating drivers. Daily volumes in thousands and yield in USD.", "subtitle")
    for row, label in {4: "Federal Express", 5: "FedEx Freight", 6: "Other and eliminations", 7: "Segment revenue total",
                       12: "U.S. Priority", 13: "U.S. Deferred", 14: "U.S. Ground", 18: "International export",
                       19: "International domestic", 23: "U.S. express freight", 25: "International express freight",
                       30: "Federal Express total", 32: "FedEx Freight total", 34: "Other and eliminations",
                       36: "Consolidated revenue", 41: "OPERATING DRIVERS", 43: "Operating days", 74: "Federal Express other revenue",
                       86: "Daily service revenue (USD thousands)", 87: "Annualized service revenue",
                       88: "Other and eliminations", 89: "Driver-based consolidated revenue",
                       94: "VOLUME AND YIELD ASSUMPTIONS", 153: "Q1 FY26 other revenue, annualized in FY26E",
                       154: "Growth in other and eliminations"}.items():
        plan.set(sheet, f"A{row}", label, "section" if row in (41, 94) else "total" if row in (7, 36, 89) else "")
    for name, (volume, rate, daily, volume_growth, yield_growth) in zip(SERVICE_NAMES, SERVICE_ROWS.values(), strict=True):
        plan.set(sheet, f"A{volume}", name + " / day")
        plan.set(sheet, f"A{rate}", "Revenue per unit (USD)")
        plan.set(sheet, f"A{daily}", "Daily revenue (USD thousands)")
        plan.set(sheet, f"A{volume_growth}", name + " volume growth")
        plan.set(sheet, f"A{yield_growth}", "Yield growth")
    for period in inputs.historical:
        c = period.revenue_column
        plan.set(sheet, f"{c}1", f"FY{period.fiscal_end.year}A", "header")
        for row, amount in zip((4, 5, 6), period.segment_revenue_millions.values(), strict=True):
            plan.set(sheet, f"{c}{row}", amount)
        plan.calc(sheet, f"{c}7", f"SUM({c}4:{c}6)", period.income_statement[5], kind="total")
        for row in (43, *(r for spec in SERVICE_ROWS.values() for r in spec[:2])):
            value = cached.value(sheet, f"{c}{row}")
            if type(value) in (int, float):
                plan.set(sheet, f"{c}{row}", value)
    plan.set(sheet, "H1", "Q1 FY26A", "header")
    for row, value in ((4, 19116), (5, 2257), (6, 871)):
        plan.set(sheet, f"H{row}", value)
    plan.calc(sheet, "H7", "SUM(H4:H6)", 22244, kind="total")
    for period in inputs.revenue_path:
        c = period.source_column
        plan.set(sheet, f"{c}1", f"FY{period.fiscal_end.year}E", "header")
        plan.set(sheet, f"{c}43", period.assumptions.operating_days, "input", "0")
        plan.set(sheet, f"{c}74", period.assumptions.express_other_millions, "input")
    results = []
    last = None
    for i, (c, prev, period) in enumerate(zip("JKLMN", "GJKLM", inputs.revenue_path, strict=True)):
        assumptions = period.assumptions
        if last is not None:
            assumptions = replace(assumptions, services={name: replace(
                item, prior_daily_volume_thousands=last.services[name].daily_volume_thousands,
                prior_yield_usd=last.services[name].yield_usd,
            ) for name, item in assumptions.services.items()})
        elif priority_yield is not None:
            assumptions = replace(assumptions, services={**assumptions.services, "us_priority": replace(
                assumptions.services["us_priority"], yield_growth=priority_yield,
            )})
        result = forecast_archived_fdx_revenue(assumptions)
        for name, (volume, rate, daily, volume_growth, yield_growth) in SERVICE_ROWS.items():
            drivers = assumptions.services[name]
            service = result.services[name]
            plan.set(sheet, f"{c}{volume_growth}", drivers.volume_growth, "input", PCT)
            plan.set(sheet, f"{c}{yield_growth}", drivers.yield_growth, "input", PCT)
            plan.calc(sheet, f"{c}{volume}", f"{prev}{volume}*(1+{c}{volume_growth})", service.daily_volume_thousands)
            plan.calc(sheet, f"{c}{rate}", f"{prev}{rate}*(1+{c}{yield_growth})", service.yield_usd, fmt=USD)
            plan.calc(sheet, f"{c}{daily}", f"{c}{volume}*{c}{rate}", service.daily_revenue_thousands)
        for row, (name, (_, _, daily, _, _)) in zip((12, 13, 14, 18, 19, 23, 25), SERVICE_ROWS.items()):
            plan.calc(sheet, f"{c}{row}", f"{c}{daily}*{c}43/1000", result.services[name].revenue_millions)
        if i == 0:
            plan.calc(sheet, f"{c}153", "H6", 871)
            other_formula = f"{c}153*4"
        else:
            plan.set(sheet, f"{c}154", period.other_revenue_growth, "input", PCT)
            other_formula = f"{prev}88*(1+{c}154)"
        total = result.total_revenue_millions
        express = result.express_revenue_millions
        freight = result.freight_segment_revenue_millions
        other = assumptions.other_and_eliminations_millions
        for row, formula, expected in (
            (30, f"SUM({c}12:{c}14,{c}18:{c}19,{c}23,{c}25,{c}74)", express),
            (32, f"SUM({c}79,{c}83)*{c}43/1000", freight), (34, f"{c}88", other),
            (4, f"{c}30", express), (5, f"{c}32", freight), (6, f"{c}34", other),
            (7, f"SUM({c}4:{c}6)", total), (36, f"{c}89", total),
            (86, "SUM(" + ",".join(f"{c}{spec[2]}" for spec in SERVICE_ROWS.values()) + ")", result.daily_revenue_thousands),
            (87, f"{c}86*{c}43/1000", result.annualized_service_revenue_millions),
            (88, other_formula, other), (89, f"SUM({c}87:{c}88,{c}74)", total),
        ):
            plan.calc(sheet, f"{c}{row}", formula, expected, kind="total" if row in (7, 36, 89) else "")
        results.append(result)
        last = result
    return results


def _cover(plan, valuation, forecast, revenue):
    plan.set("Cover", "A1", "FEDEX CORPORATION  |  NYSE: FDX", "title")
    plan.set("Cover", "A2", "Portfolio model replay", "subtitle")
    plan.set("Cover", "A4", "OCTOBER 22, 2025  |  PRE-OPEN VALUATION", "section")
    plan.set("Cover", "A6", "Historical exercise on the May fiscal year. FY27–30 retain FedEx Freight as a counterfactual.", "note")
    plan.set("Cover", "A8", "Blue cells are editable assumptions. Use the Sources tab for dates and scope.", "note")
    plan.set("Cover", "A16", "VALUATION SUMMARY", "section")
    controls = (
        (17, "Oct 21 reference close", "DCF!B6", valuation.quote_price, USD),
        (18, "Intrinsic value", "DCF!B24", valuation.spot.value_per_share, USD),
        (19, "12-month modeled target", "DCF!B46", valuation.horizon.value_per_share, USD),
        (20, "12-month price change", "DCF!I9", valuation.horizon_price_return, PCT),
        (21, "FY26E revenue (USDm)", "'Income statement'!H5", revenue[0].total_revenue_millions, None),
        (22, "FY26E net income (USDm)", "'Income statement'!H27", forecast[0].net_income, None),
    )
    for row, title, formula, expected, fmt in controls:
        plan.set("Cover", f"A{row}", title, "total")
        plan.calc("Cover", f"B{row}", formula, expected, kind="total", fmt=fmt)


def _sources(plan, valuation, digest):
    sheet = "Sources"
    plan.set(sheet, "A1", "SOURCES AND MODELING BASIS", "title")
    for col, title in zip("ABCD", ("Evidence or input", "Period or date", "Source", "Use and limitations"), strict=True):
        plan.set(sheet, f"{col}4", title, "header")
    entries = (
        ("Portfolio FedEx workbook", "Legacy May fiscal year", "FedEx final.xlsx (private reference)",
         "Source of volume and yield, segment cost, balance sheet, capex, financing and share assumptions. Historical figures follow the reference presentation. Base driver inputs are directly editable."),
        ("Reference SHA-256", "Pinned to portfolio copy", digest, "The generator verifies the private file before loading any inputs."),
        ("FY2023 Form 10-K", "May 31, 2023", ANNUAL_2023, "FY2021–23 cash flows and historical statements. FY2021–22 revenue combines predecessor Express and Ground."),
        ("FY2025 Form 10-K", "May 31, 2025", ANNUAL_2025, "FY2023–25 cash flow, reported balance sheet, debt maturities and financing leases (Notes 7–8), expected intangible amortization (Note 5)."),
        ("FY2026 Q1 Form 10-Q", "August 31, 2025", QUARTER, "Q1 actuals and the September 16 shares outstanding used for spot valuation. FY26 forecast shares start from rounded FY25 year-end issued less treasury shares."),
        ("FY2026 Form 10-K", "May 31, 2026", TRANSITION, "Subsequent context only. The seven-month transition to December reporting and Freight separation are outside the forecast model."),
        ("Reference market close", "October 21, 2025", valuation.quote_source, "The portfolio DCF labels $242.84 as the October 22 close. The quote is the preceding session close for the frozen October 22 pre-open replay."),
        ("Revenue alignment", "FY2026E–FY2030E", "FedEx final.xlsx · Revenue!J:N", "Both operating views use one common operating-days driver. This removes a $544.2m FY2026 discrepancy. Rounded historical volume and yield need not reproduce booked revenue."),
        ("Other and eliminations", "Q1 FY26 and projections", QUARTER, "The portfolio forecast annualizes the $871m reported Q1 contribution. Later growth rates are archived assumptions."),
        ("Cash flow, PP&E and borrowing", "FY2026E–FY2030E", "FedEx final.xlsx and FY2025 10-K", "Capex adds to gross property. Depreciation and intangible amortization roll carrying values separately. Forecast excludes unmodeled disposals, financing fees and foreign exchange effects. Finance lease principal uses scheduled payments and the disclosed average discount rate as a proxy."),
        ("Debt classification", "FY2030E", "FY2025 10-K, Note 7", "The maturity table groups the full FY2031 amount within thereafter. FY2030 closing debt is shown as unclassified. Its current portion is unavailable."),
        ("Forecast retirement income", "FY2026E–FY2030E", ANNUAL_2025, "Annual proxy is the mean of FY2023–25 retirement income less annual noncash mark-to-market gains. Forecast assumes no new OCI, acquisitions or impairment events."),
        ("Forecast cash adjustments", "FY2026E–FY2030E", "FedEx final.xlsx · Balance sheet", "Operating lease assets and liabilities roll by the same modeled additions and principal reduction. Archived APIC trend serves as a stock compensation proxy. Forecast dividends use ending shares and EPS uses average shares as proxies."),
        ("Archived valuation parameters", "Observation dates unavailable", "FedEx final.xlsx · Ke and DCF", "Cost of equity and terminal growth are archive inputs. The DCF prorates first-year FCFE by remaining days, removes net borrowing from terminal cash and floors terminal reinvestment at D&A. No separate opening excess cash credit."),
        ("Frozen peer snapshot", "Observation dates unavailable", "FedEx final.xlsx · Comps NF", "Archived quotes and forward consensus feed standalone peer values. Use only as a historical illustration. The source vendor add-in is unnecessary."),
    )
    for row, entry in enumerate(entries, 5):
        for col, value in zip("ABCD", entry, strict=True):
            plan.set(sheet, f"{col}{row}", value, "note" if col == "D" else "")


def _checks(plan):
    sheet = "Checks"
    plan.set(sheet, "A1", "MODEL RECONCILIATION", "title")
    plan.set(sheet, "A2", "USD millions. Each difference should be zero.", "subtitle")
    labels = {5: "Assets less liabilities and equity", 6: "Closing cash less balance sheet cash",
              7: "Debt schedule less balance sheet debt", 8: "Capex schedule net PP&E less balance sheet net PP&E",
              9: "Segment revenue less income statement revenue", 10: "Consolidated expense schedule less income statement",
              11: "Opening cash + net cash flow less closing cash", 12: "FCFE less (CFO + signed capex + net borrowing)"}
    for row, label in labels.items():
        plan.set(sheet, f"A{row}", label)
    for i, (col, b, f, p, r, o, inc) in enumerate(zip("BCDEF", "GHIJK", "CDEFG", "FGHIJ", "JKLMN", "EFGHI", "HIJKL", strict=True)):
        plan.set(sheet, f"{col}4", f"FY{2026 + i}E", "header")
        bs = f"'Balance sheet'!{b}"
        cf = f"'Derived cash flow'!{f}"
        formulas = (
            f"{bs}25-{bs}49", f"{cf}57-{bs}6", f"'Debt schedule'!{b}10-SUM({bs}28,{bs}34)",
            f"CapEx!{p}20-{bs}19", f"Revenue!{r}7-'Income statement'!{inc}5",
            f"'Operating expenses'!{o}90-'Income statement'!{inc}16", f"{cf}56+{cf}54-{cf}57",
            f"{cf}59-SUM({cf}13,{cf}16,{cf}25)",
        )
        for row, formula in zip(labels, formulas, strict=True):
            plan.calc(sheet, f"{col}{row}", formula, 0, kind="check", fmt="0.00")
    plan.set(sheet, "A14", "Spot sensitivity center less intrinsic value")
    plan.set(sheet, "A15", "Target sensitivity center less modeled target")
    plan.calc(sheet, "B14", "DCF!F30-DCF!B24", 0, kind="check", fmt="0.00")
    plan.calc(sheet, "B15", "DCF!F52-DCF!B46", 0, kind="check", fmt="0.00")


def _build_model(inputs, cached, priority_yield=None):
    plan = WorkbookPlan(cells={name: {} for name in SHEETS})
    revenues = _revenue(plan, inputs, cached, priority_yield)
    forecast = add_fdx_statements(plan, inputs, cached, revenues)
    terminal = forecast[-1]
    valuation = build_fdx_valuation(
        cached, fcfe_millions=tuple(year.fcfe for year in forecast), terminal_net_borrowing_millions=terminal.borrowing,
        terminal_da_millions=terminal.depreciation, terminal_capex_millions=terminal.capex, horizon_shares_millions=forecast[0].shares,
    )
    plan.cells.update(valuation.cells)
    plan.expected.update(valuation.expected)
    _cover(plan, valuation, forecast, revenues)
    _checks(plan)
    _sources(plan, valuation, inputs.workbook_sha256)
    return plan, valuation


def build_fdx_workbook_plan(template: Path):
    """Calculate the base and FY2026 U.S. Priority yield-change verification cases."""
    snapshot = template.read_bytes()
    inputs = load_fdx_financial_inputs(snapshot)
    cached = _saved_source(snapshot)
    plan, valuation = _build_model(inputs, cached)
    scenario, changed = _build_model(inputs, cached, SCENARIO_YIELD)
    if changed.spot.value_per_share <= valuation.spot.value_per_share:
        raise ValueError("Higher U.S. Priority yield must raise the modeled value on the archived assumptions.")
    return {
        "cells": plan.cells, "expected": plan.expected, "scenarioExpected": scenario.expected,
        "scenarioControl": {"sheet": "Revenue", "address": "J98", "value": SCENARIO_YIELD},
    }


def _verify_saved(path, plan):
    count = 0
    for data_only in (False, True):
        workbook = load_workbook(path, read_only=True, data_only=data_only, keep_links=True)
        try:
            if tuple(workbook.sheetnames) != SHEETS or workbook._external_links:
                raise ValueError("Unexpected workbook sheets or external links.")
            for sheet in workbook:
                planned = plan["cells"][sheet.title]
                expected_values = plan["expected"].get(sheet.title, {})
                remaining = set(expected_values if data_only else planned)
                for row in sheet.iter_rows():
                    for cell in row:
                        if cell.value is None:
                            continue
                        actual = cell.value
                        address = cell.coordinate
                        if cell.data_type == "e" or address not in planned:
                            raise ValueError(f"Unexpected cell or formula error in {sheet.title}!{address}.")
                        if address not in remaining:
                            continue
                        expected = expected_values[address] if data_only else planned[address][0]
                        if isinstance(actual, datetime) and type(expected) in (int, float):
                            actual = to_excel(actual, workbook.epoch)
                        if not data_only and isinstance(expected, str) and expected.startswith("="):
                            location = f"{sheet.title}!{address}"
                            matches = cell.data_type == "f" and _formula_key(actual, location) == _formula_key(expected, location)
                        elif type(expected) in (int, float):
                            matches = type(actual) in (int, float) and isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-6)
                        else:
                            matches = actual == expected
                        if not matches:
                            raise ValueError(f"{sheet.title}!{address} returned {actual!r}, expected {expected!r}.")
                        remaining.remove(address)
                        if data_only:
                            count += 1
                if remaining:
                    raise ValueError(f"Missing required cells in {sheet.title}: {', '.join(sorted(remaining)[:8])}.")
        finally:
            workbook.close()
    return count


def verify_fdx_workbook(template: Path, workbook: Path) -> int:
    """Verify saved formulas, literal inputs, and calculation caches against the replay."""
    return _verify_saved(workbook, build_fdx_workbook_plan(template))


def export_fdx_workbook(template: Path, output: Path, node: Path, node_modules: Path,
                        *, preview_dir: Path | None = None) -> int:
    """Author and validate the model before publishing a new output file."""
    if output.suffix.lower() != ".xlsx" or output.exists():
        raise ValueError("Choose a new .xlsx output filename.")
    plan = build_fdx_workbook_plan(template)
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent) as directory:
        stage = Path(directory)
        specification = stage / "model.json"
        specification.write_text(json.dumps(plan, allow_nan=False), encoding="utf-8")
        workbook = stage / "model.xlsx"
        command = [str(node), str(Path(__file__).with_suffix(".mjs")), str(specification), str(workbook), str(node_modules)]
        if preview_dir is not None:
            command.append(str(preview_dir))
        process = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if process.returncode:
            raise ValueError(f"Workbook authoring failed. {process.stderr.strip() or process.stdout.strip()}")
        count = _verify_saved(workbook, plan)
        workbook.rename(output)
    return count

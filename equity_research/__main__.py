"""Run equity forecasts and verify the reference models from the command line."""

import argparse
from dataclasses import asdict, is_dataclass
from datetime import date
import json
from os import environ
from pathlib import Path
import sys

from .gd_inputs import MODEL_INPUT_SHEETS, load_guided_overrides, load_inputs, save_inputs
from .gd_projection import ProjectionReport, project_gd, run_projection
from .gd_replay import GDReplay, replay_gd
from .gd_workbook import load_workbook_inputs


def print_projection(report: ProjectionReport) -> None:
    print(f"GD forecast as of {report.as_of:%B %d, %Y}.")
    print(f"Input source  {report.source.citation}")
    print(f"Used {report.assumptions_used} annual inputs. Passed {report.statement_period_checks} reconciliation comparisons.")
    if report.formula_cells_matched is not None:
        print(
            f"Matched {report.formula_cells_matched:,} workbook forecast cells "
            f"and {report.sensitivity_cells_matched} sensitivity cells."
        )
    for override in report.override_log:
        print(f"Guided override {override.target} ({override.before:g} -> {override.value:g}). {override.reason}")
    print("\nForecast in USD millions")
    print(f"{'FY':<6} {'Revenue':>12} {'Op. earnings':>13} {'Net earnings':>13} {'Closing cash':>13} {'FCFE':>12}")
    for year in report.years:
        print(
            f"{year.period_end.year:<6} {year.revenue_millions:>12,.2f} "
            f"{year.operating_earnings_millions:>13,.2f} {year.net_earnings_millions:>13,.2f} "
            f"{year.closing_cash_millions:>13,.2f} {year.fcfe_millions:>12,.2f}"
        )
    print(f"\nIntrinsic value       ${report.intrinsic_usd_per_share:,.2f} per share")
    print(f"12-month target       ${report.twelve_month_usd_per_share:,.2f} per share")
    for note in report.notes:
        print(f"- {note}")


def print_replay(report: GDReplay) -> None:
    print(f"GD baseline passed for {report.as_of:%B %d, %Y}.")
    print(f"Intrinsic value       ${report.intrinsic.modeled_usd_per_share:,.2f} per share")
    print(f"12-month target       ${report.twelve_month.modeled_usd_per_share:,.2f} per share")
    grid_cells = report.intrinsic.sensitivity_cells_checked + report.twelve_month.sensitivity_cells_checked
    print(f"Sensitivity grids    {grid_cells} cells matched")
    for check in report.statement_checks:
        print(f"PASS {check.name} ({check.compared} periods)")
    print("\nWorkbook issues carried forward to the prototype")
    for issue in report.known_issues:
        print(f"- {issue}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run or verify the equity research models.")
    commands = parser.add_subparsers(dest="command", required=True)

    verify = commands.add_parser("verify-workbook", help="Verify the approved March 30 GD V4 workbook")
    verify.add_argument("workbook", type=Path, help="Path to the original portfolio GD V4.xlsx")
    verify.add_argument("--project", action="store_true", help="Rebuild the forecast from its inputs")
    verify.add_argument("--json", action="store_true", help="Print the validation result as JSON")

    export = commands.add_parser("export-inputs", help="Create a private GD input file from the approved workbook")
    export.add_argument("workbook", type=Path, help="Path to the original portfolio GD V4.xlsx")
    export.add_argument("output", type=Path, help="Path for the private input JSON")

    run = commands.add_parser("run", help="Run the GD forecast using a standalone input file")
    run.add_argument("inputs", type=Path, help="Dated GD input JSON")
    run.add_argument("--guided", type=Path, help="JSON file of forecast overrides and reasons")
    run.add_argument("--json", action="store_true", help="Print the forecast result as JSON")

    collect = commands.add_parser("collect-gd", help="Retrieve dated GD filing and earnings-release evidence")
    collect.add_argument("--as-of", required=True, type=date.fromisoformat, help="Evidence cutoff in YYYY-MM-DD")
    collect.add_argument("--compare-inputs", type=Path, help="Compare sourced history against a private GD input file")

    prepare = commands.add_parser("prepare-gd", help="Validate and assemble a fresh GD input file")
    prepare.add_argument("--evidence", required=True, type=Path, help="JSON output of collect-gd")
    prepare.add_argument("--assumptions", required=True, type=Path, help="Explicit forecast assumption JSON")
    prepare.add_argument("--market", required=True, type=Path, help="Dated market input JSON")
    prepare.add_argument("--output", required=True, type=Path, help="New GD input JSON path")

    refresh = commands.add_parser("verify-gd-refresh", help="Check current GD interim evidence and the guidance bridge")
    refresh.add_argument("interim", type=Path, help="Dated GD nine-month evidence JSON")
    refresh.add_argument("market", type=Path, help="Market inputs with a separate valuation close")
    refresh.add_argument("--cash-bridge", type=Path, help="Cited cash, debt and share movements for both windows")
    refresh.add_argument("--annual-history", type=Path, help="Dated annual evidence to check against the refresh")

    history = commands.add_parser("verify-gd-history", help="Reconcile five fiscal years of GD actuals and check input coverage")
    history.add_argument("evidence", type=Path, help="Dated GD annual evidence JSON")
    history.add_argument("market", type=Path, help="Market inputs with the preceding year-end closing price")

    cash_forecast = commands.add_parser("forecast-gd-cash", help="Estimate FY and Q4 cash and test timing after the cutoff")
    cash_forecast.add_argument("interim", type=Path, help="Dated GD nine-month evidence JSON")
    cash_forecast.add_argument("outlook", type=Path, help="Dated cash guidance and analyst timing cases JSON")
    cash_forecast.add_argument("--bridge", metavar="CASE", help="Print cash bridge inputs for the named timing case")
    cash_forecast.add_argument("--financing", type=Path, help="Add dated financing assumptions to the selected bridge case")

    operations = commands.add_parser("forecast-gd-operations", help="Project GD product revenue and segment earnings through FY2031")
    operations.add_argument("interim", type=Path, help="Dated GD nine-month evidence JSON")
    operations.add_argument("history", type=Path, help="Dated GD annual evidence JSON")
    operations.add_argument("segment_evidence", type=Path, help="Filed GD product actuals and dated segment guidance JSON")
    operations.add_argument("assumptions", type=Path, help="GD forward operating assumptions JSON")

    statements = commands.add_parser("forecast-gd-statements", help="Build fresh GD financial statements and annual FCFE")
    statements.add_argument("--as-of", required=True, type=date.fromisoformat, help="Dated evidence cutoff in YYYY-MM-DD")
    statements.add_argument("--inputs-dir", type=Path, default=Path("inputs"), help="Directory containing the dated GD inputs")

    gd_valuation = commands.add_parser("value-gd", help="Value fresh GD cash flows and calculate DCF sensitivities")
    gd_valuation.add_argument("--as-of", required=True, type=date.fromisoformat, help="Dated evidence cutoff in YYYY-MM-DD")
    gd_valuation.add_argument("--inputs-dir", type=Path, default=Path("inputs"), help="Directory containing the dated GD inputs")

    gd_comparables = commands.add_parser("compare-gd", help="Compare GD and its peers using dated reported financials")
    gd_comparables.add_argument("--as-of", required=True, type=date.fromisoformat, help="Dated evidence cutoff in YYYY-MM-DD")
    gd_comparables.add_argument("--inputs-dir", type=Path, default=Path("inputs"), help="Directory containing the dated GD inputs")

    gd_horizon = commands.add_parser("target-gd", help="Calculate the dated GD 12-month price target and total return")
    gd_horizon.add_argument("--as-of", required=True, type=date.fromisoformat, help="Dated evidence cutoff in YYYY-MM-DD")
    gd_horizon.add_argument("--inputs-dir", type=Path, default=Path("inputs"), help="Directory containing the dated GD inputs")

    for scenario_parser in (statements, gd_valuation, gd_horizon):
        scenario_parser.add_argument("--case", choices=("bear", "base", "bull"), default="base", help="Operating case")

    gd_research = commands.add_parser("research-gd", help="Build a dated GD scenario valuation and model recommendation")
    gd_research.add_argument("--as-of", required=True, type=date.fromisoformat, help="Dated evidence cutoff in YYYY-MM-DD")
    gd_research.add_argument("--inputs-dir", type=Path, default=Path("inputs"), help="Directory containing the dated GD inputs")

    fdx = commands.add_parser("verify-fdx-revenue", help="Verify the portfolio FedEx revenue build and yield sensitivity")
    fdx.add_argument("workbook", type=Path, help="Path to the original portfolio FedEx final.xlsx")
    fdx.add_argument(
        "--priority-yield-growth", type=float, default=0.025,
        help="Scenario annual U.S. Priority yield growth as a decimal (default 0.025)",
    )
    fdx.add_argument("--json", action="store_true", help="Print the validation result as JSON")

    fdx_current = commands.add_parser("forecast-fdx-current", help="Reconcile current FedEx actuals and estimate the calendar Q4 bridge")
    fdx_current.add_argument("--as-of", required=True, type=date.fromisoformat, help="Dated evidence cutoff in YYYY-MM-DD")
    fdx_current.add_argument("--inputs-dir", type=Path, default=Path("inputs"), help="Directory with the dated FedEx evidence")
    fdx_current.add_argument("--guided", type=Path, help="JSON file containing service growth or corporate revenue overrides")
    fdx_current.add_argument("--json", action="store_true", help="Print all drivers, checks, limitations and sources")

    fdx_statements = commands.add_parser("forecast-fdx-statements", help="Estimate Q4 cash and project FedEx's December-year financial statements")
    fdx_statements.add_argument("--as-of", required=True, type=date.fromisoformat, help="Dated evidence cutoff in YYYY-MM-DD")
    fdx_statements.add_argument("--inputs-dir", type=Path, default=Path("inputs"), help="Directory with the dated FedEx evidence")
    fdx_statements.add_argument("--case", choices=("bear", "base", "bull"), default="base", help="Operating assumptions")
    fdx_statements.add_argument("--guided", type=Path, help="Optional guided Q4 revenue overrides")
    fdx_statements.add_argument("--json", action="store_true", help="Print complete linked statements and source references")

    fdx_valuation = commands.add_parser("value-fdx", help="Value dated FedEx continuing operations and compare peers")
    fdx_valuation.add_argument("--as-of", required=True, type=date.fromisoformat, help="Dated evidence cutoff in YYYY-MM-DD")
    fdx_valuation.add_argument("--inputs-dir", type=Path, default=Path("inputs"), help="Directory with the dated FedEx evidence")
    fdx_valuation.add_argument("--guided", type=Path, help="Optional guided Q4 revenue overrides")
    fdx_valuation.add_argument("--json", action="store_true", help="Print the DCF, peer valuations, sensitivities and sources")

    fdx_export = commands.add_parser("export-fdx-excel", help="Export the editable FedEx portfolio replay")
    fdx_verify = commands.add_parser("verify-fdx-excel", help="Verify saved FedEx formulas and calculations")
    for fdx_command in (fdx_export, fdx_verify):
        fdx_command.add_argument("template", type=Path, help="Path to the original FedEx final.xlsx")
        fdx_command.add_argument("workbook", type=Path, help="Path to the generated workbook")
    fdx_export.add_argument("--node", required=True, type=Path, help="Bundled Node.js executable")
    fdx_export.add_argument("--node-modules", required=True, type=Path, help="Bundled node_modules directory")
    fdx_export.add_argument("--preview-dir", type=Path, help="Optional directory for visual review renders")

    fdx_fresh_excel = commands.add_parser("export-fdx-fresh-excel", help="Export the dated, editable FedEx workbook")
    fdx_fresh_excel.add_argument("--node", required=True, type=Path, help="Bundled Node.js executable")
    fdx_fresh_excel.add_argument("--node-modules", required=True, type=Path, help="Bundled node_modules directory")
    fdx_fresh_excel.add_argument("--preview-dir", type=Path, help="Optional directory for visual review renders")
    fdx_verify_fresh_excel = commands.add_parser("verify-fdx-fresh-excel", help="Check saved FedEx formulas and values")
    fdx_fresh_pdf = commands.add_parser("export-fdx-fresh-pdf", help="Export dated FedEx research matched to the workbook")
    fdx_verify_fresh_pdf = commands.add_parser("verify-fdx-fresh-pdf", help="Verify the dated FedEx research and its exact workbook partner")
    for fdx_artifact in (fdx_fresh_excel, fdx_verify_fresh_excel, fdx_fresh_pdf, fdx_verify_fresh_pdf):
        fdx_artifact.add_argument("workbook", type=Path, help="Path to the dated FedEx workbook")
        fdx_artifact.add_argument("--as-of", required=True, type=date.fromisoformat, help="Dated evidence cutoff in YYYY-MM-DD")
        fdx_artifact.add_argument("--inputs-dir", type=Path, default=Path("inputs"), help="Directory with the dated FedEx evidence")
        fdx_artifact.add_argument("--case", choices=("bear", "base", "bull"), default="base", help="Selected operating case")
        fdx_artifact.add_argument("--guided", type=Path, help="Optional guided Q4 revenue overrides")
    for fdx_report in (fdx_fresh_pdf, fdx_verify_fresh_pdf):
        fdx_report.add_argument("pdf", type=Path, help="Path to the paired FedEx research PDF")

    nem = commands.add_parser("verify-nem-revenue", help="Verify the portfolio Newmont commodity build and gold sensitivity")
    nem.add_argument("workbook", type=Path, help="Path to the original portfolio Newmont mining NV.xlsx")
    nem.add_argument(
        "--gold-price-change", type=float, default=200.0,
        help="Scenario change in gold price in USD per ounce (default 200)",
    )
    nem.add_argument("--json", action="store_true", help="Print the validation result as JSON")

    nem_current = commands.add_parser("forecast-nem-current", help="Build the dated Newmont mine and metal operating forecast")
    nem_current.add_argument("--as-of", required=True, type=date.fromisoformat, help="Dated evidence cutoff in YYYY-MM-DD")
    nem_current.add_argument("--inputs-dir", type=Path, default=Path("inputs"), help="Directory with the dated Newmont evidence")
    nem_current.add_argument("--guided", type=Path, help="Optional JSON overrides for prices, production and unit cost")
    nem_current.add_argument("--json", action="store_true", help="Print mine-level volumes, sales, costs, checks and sources")

    nem_statements = commands.add_parser("forecast-nem-statements", help="Link Newmont's mine forecast, statements and cash schedules")
    nem_statements.add_argument("--as-of", required=True, type=date.fromisoformat, help="Dated evidence cutoff in YYYY-MM-DD")
    nem_statements.add_argument("--inputs-dir", type=Path, default=Path("inputs"), help="Directory with the dated Newmont evidence")
    nem_statements.add_argument("--guided", type=Path, help="Optional guided mine and commodity assumptions")
    nem_statements.add_argument("--json", action="store_true", help="Print linked financials, schedules, checks and sources")

    nem_nav = commands.add_parser("value-nem", help="Value Newmont's finite mine lives and compare trading multiples")
    nem_nav.add_argument("--as-of", required=True, type=date.fromisoformat, help="Dated evidence cutoff in YYYY-MM-DD")
    nem_nav.add_argument("--inputs-dir", type=Path, default=Path("inputs"), help="Directory with the dated Newmont evidence")
    nem_nav.add_argument("--guided", type=Path, help="Optional guided mine and commodity assumptions")
    nem_nav.add_argument("--json", action="store_true", help="Print the NAV, mine cash flows, sensitivity grids and sources")

    nem_excel = commands.add_parser("export-nem-fresh-excel", help="Export the dated, editable Newmont workbook")
    nem_excel.add_argument("--node", type=Path, required=True, help="Bundled Node.js executable")
    nem_excel.add_argument("--node-modules", type=Path, required=True, help="Bundled node_modules directory")
    nem_excel.add_argument("--preview-dir", type=Path, help="Optional directory for visual review renders")
    nem_verify_excel = commands.add_parser("verify-nem-fresh-excel", help="Verify saved Newmont formulas and calculations")
    nem_pdf = commands.add_parser("export-nem-fresh-pdf", help="Export Newmont research paired with the dated workbook")
    nem_verify_pdf = commands.add_parser("verify-nem-fresh-pdf", help="Verify Newmont research and its workbook partner")
    for nem_artifact in (nem_excel, nem_verify_excel, nem_pdf, nem_verify_pdf):
        nem_artifact.add_argument("workbook", type=Path, help="Path to the dated Newmont workbook")
        nem_artifact.add_argument("--as-of", required=True, type=date.fromisoformat, help="Evidence cutoff in YYYY-MM-DD")
        nem_artifact.add_argument("--inputs-dir", type=Path, default=Path("inputs"), help="Dated evidence directory")
        nem_artifact.add_argument("--case", choices=("bear", "base", "bull"), default="base")
        nem_artifact.add_argument("--guided", type=Path, help="Optional guided input file")
    for nem_report in (nem_pdf, nem_verify_pdf):
        nem_report.add_argument("pdf", type=Path, help="Path to the paired Newmont research PDF")

    demo = commands.add_parser("build-research-demo", help="Stage the verified portfolio research packages for the website")
    demo.add_argument("--root", type=Path, default=Path.cwd(), help="Project directory containing the dated outputs")
    demo.add_argument("--destination", type=Path, default=Path("web/dist"), help="Static website directory")
    serve = commands.add_parser("serve-research", help="Open the recruiter demo and optional guided generation service")
    serve.add_argument("--root", type=Path, default=Path.cwd(), help="Project directory containing the web assets and evidence")
    serve.add_argument("--port", type=int, default=8000, help="Local HTTP port")
    serve.add_argument("--node", type=Path, help="Node.js executable for guided workbook export")
    serve.add_argument("--node-modules", type=Path, help="Node.js modules containing the spreadsheet runtime")

    excel = commands.add_parser("export-gd-excel", help="Export an editable model using the approved GD template")
    excel.add_argument("template", type=Path, help="Path to the original portfolio GD V4.xlsx")
    excel.add_argument("output", type=Path, help="New Excel output filename")
    excel.add_argument("--guided", type=Path, help="JSON file of forecast overrides and reasons")
    excel.add_argument("--json", action="store_true", help="Print the export result as JSON")

    fresh_excel = commands.add_parser("export-gd-fresh-excel", help="Export the dated, editable GD workbook")
    fresh_excel.add_argument("template", type=Path, help="Path to the approved GD V4.xlsx")
    fresh_excel.add_argument("output", type=Path, help="New Excel output filename")
    fresh_excel.add_argument("--node", required=True, type=Path, help="Bundled Node.js executable")
    fresh_excel.add_argument("--node-modules", required=True, type=Path, help="Bundled node_modules directory")
    fresh_excel.add_argument("--preview-dir", type=Path, help="Optional directory for visual review renders")

    verify_fresh_excel = commands.add_parser("verify-gd-fresh-excel", help="Compare saved GD Excel formulas and values with Python")
    verify_fresh_excel.add_argument("template", type=Path, help="Path to the approved GD V4.xlsx")
    verify_fresh_excel.add_argument("workbook", type=Path, help="Path to the saved fresh workbook")
    for workbook_parser in (fresh_excel, verify_fresh_excel):
        workbook_parser.add_argument("--as-of", required=True, type=date.fromisoformat)
        workbook_parser.add_argument("--inputs-dir", type=Path, default=Path("inputs"))
        workbook_parser.add_argument("--case", choices=("bear", "base", "bull"), default="base")

    pdf = commands.add_parser("export-gd-pdf", help="Create an archived GD research PDF matched to an Excel export")
    pdf.add_argument("template", type=Path, help="Path to the original portfolio GD V4.xlsx")
    pdf.add_argument("workbook", type=Path, help="Path to the matching editable GD Excel export")
    pdf.add_argument("output", type=Path, help="New research PDF filename")
    pdf.add_argument("--guided", type=Path, help="The same forecast overrides used for the Excel export")

    fresh_pdf = commands.add_parser("export-gd-fresh-pdf", help="Create a dated GD report verified against the fresh workbook")
    verify_fresh_pdf = commands.add_parser("verify-gd-fresh-pdf", help="Verify a dated GD PDF and its exact workbook partner")
    for report_parser in (fresh_pdf, verify_fresh_pdf):
        report_parser.add_argument("template", type=Path, help="Path to the approved GD V4.xlsx")
        report_parser.add_argument("workbook", type=Path, help="Path to the saved fresh GD workbook")
        report_parser.add_argument("pdf", type=Path, help="Research PDF path")
        report_parser.add_argument("--as-of", required=True, type=date.fromisoformat)
        report_parser.add_argument("--inputs-dir", type=Path, default=Path("inputs"))
        report_parser.add_argument("--case", choices=("bear", "base", "bull"), default="base")

    argv = sys.argv[1:]
    # Preserve the original invocation with the workbook path as the first argument.
    if argv and argv[0] not in commands.choices and argv[0] not in ("-h", "--help"):
        argv = ["verify-workbook", *argv]
    args = parser.parse_args(argv)

    try:
        if args.command == "value-fdx":
            from .fdx_current_valuation import run_fdx_valuation

            report = run_fdx_valuation(args.as_of, args.inputs_dir, guided=args.guided)
            if args.json:
                print(json.dumps(report, default=lambda value: asdict(value) if is_dataclass(value) else str(value),
                                 indent=2, allow_nan=False))
            else:
                scenarios = report["scenarios"]
                base = scenarios["base"]
                print(f"FedEx valuation as of {args.as_of:%B %d, %Y} ({report['mode']}).")
                print(f"Reference close ${report['reference_close_usd']:.2f} on {report['quote_date']:%B %d, %Y}.")
                print(f"WACC {report['cost_of_capital'].wacc:.2%} | Terminal growth {base.spot.terminal.growth:.2%} "
                      f"| Target date {report['horizon']:%B %d, %Y}")
                print(f"\n{'Case':<7} {'Spot DCF':>10} {'Target':>10} {'Total return':>14} {'Rating':>8}")
                for name, case in scenarios.items():
                    print(f"{name.title():<7} ${case.spot.value_per_share:>9,.2f} ${case.target.value_per_share:>9,.2f} "
                          f"{case.total_return:>14.2%} {case.recommendation:>8}")
                ups = report["comparables"].ups_adjusted
                print(f"\nUPS issuer-adjusted spot cross-check ${ups.implied_ev_ebit_usd:,.2f} on EV/EBIT "
                      f"and ${ups.implied_pe_usd:,.2f} on P/E.")
                print(f"Passed {len(report['checks']):,} valuation reconciliation checks.")
            return 0
        if args.command == "forecast-fdx-statements":
            from .fdx_forward_statements import run_fdx_statements

            report = run_fdx_statements(args.as_of, args.inputs_dir, case=args.case, guided=args.guided)
            if args.json:
                print(json.dumps(report, default=lambda value: asdict(value) if is_dataclass(value) else str(value),
                                 indent=2, allow_nan=False))
            else:
                q4_cash = report["q4"].cash_flow
                cutoff = report["cutoff"]
                print(f"FedEx continuing-operations statements as of {args.as_of:%B %d, %Y} ({args.case}).")
                print(f"Q4 operating cash excluding customer pass-throughs ${q4_cash['cash_from_operations']:,.0f}m")
                print(f"Q4 continuing capex ${-q4_cash['capex']:,.0f}m")
                print(f"Cutoff cash before operating reserve ${cutoff['cash_before_operating_reserve_millions']:,.0f}m")
                print(f"Remaining Q4 unlevered free cash flow ${cutoff['remaining_unlevered_free_cash_flow_millions']:,.0f}m")
                print(f"\n{'Year':<6} {'Revenue':>12} {'Op. income':>12} {'Unlevered FCF':>15}  USDm")
                for year in report["years"]:
                    print(f"{year.period_end.year:<6} {year.income_statement['revenue']:>12,.0f} "
                          f"{year.income_statement['operating_income']:>12,.0f} {year.cash_flow['unlevered_free_cash_flow']:>15,.0f}")
                print(f"\nPassed {len(report['checks']):,} statement and financing checks.")
            return 0
        if args.command == "forecast-fdx-current":
            from .fdx_current import run_current_fdx

            report = run_current_fdx(args.as_of, args.inputs_dir, guided=args.guided)
            if args.json:
                print(json.dumps(report, default=lambda value: asdict(value) if is_dataclass(value) else str(value),
                                 indent=2, allow_nan=False))
            else:
                q4 = report["q4_revenue"]
                low, high = report["guidance_bridge"]["q4_adjusted_eps_guidance"]
                print(f"FedEx continuing operations as of {args.as_of:%B %d, %Y} ({report['mode']}).")
                print(f"Q4 revenue estimate: ${q4['revenue_millions'] / 1000:,.2f}B")
                print(f"CY2026 revenue estimate: ${report['calendar_year']['forecast_revenue_millions'] / 1000:,.2f}B")
                print(f"June–December transition revenue estimate: ${report['transition_period']['forecast_revenue_millions'] / 1000:,.2f}B")
                print(f"Issuer Q4 adjusted EPS assumption: ${low:.2f}–${high:.2f}")
                print(f"Passed {len(report['checks']):,} reconciliation and cross-source checks.")
                print("Valuation requires further inputs. Use --json for the drivers, sources and remaining gaps.")
            return 0
        if args.command in ("export-fdx-excel", "verify-fdx-excel"):
            from .fdx_excel import export_fdx_workbook, verify_fdx_workbook

            if args.command == "export-fdx-excel":
                count = export_fdx_workbook(args.template, args.workbook, args.node, args.node_modules, preview_dir=args.preview_dir)
            else:
                count = verify_fdx_workbook(args.template, args.workbook)
            print(f"Verified {count:,} saved FedEx results against Python.")
            return 0
        if args.command in ("export-fdx-fresh-excel", "verify-fdx-fresh-excel"):
            from .fdx_fresh_excel import export_fresh_fdx_workbook, verify_fresh_fdx_workbook

            if args.command == "export-fdx-fresh-excel":
                count = export_fresh_fdx_workbook(
                    args.workbook, args.as_of, args.inputs_dir, args.node, args.node_modules, preview_dir=args.preview_dir,
                    case=args.case, guided=args.guided,
                )
            else:
                count = verify_fresh_fdx_workbook(args.workbook, args.as_of, args.inputs_dir, case=args.case, guided=args.guided)
            print(f"Verified {count:,} saved FedEx workbook cells against the dated Python model.")
            return 0
        if args.command in ("export-fdx-fresh-pdf", "verify-fdx-fresh-pdf"):
            from .fdx_fresh_pdf import export_fdx_fresh_pdf, verify_fdx_fresh_pdf

            action = export_fdx_fresh_pdf if args.command == "export-fdx-fresh-pdf" else verify_fdx_fresh_pdf
            result = action(args.workbook, args.pdf, as_of=args.as_of, inputs_dir=args.inputs_dir, case=args.case, guided=args.guided)
            print(f"Verified {result['page_count']} PDF pages and {result['workbook_checks']:,} workbook cells.")
            print(f"Workbook SHA-256 {result['workbook_sha256']}")
            return 0
        if args.command in ("export-nem-fresh-excel", "verify-nem-fresh-excel"):
            from .nem_fresh_excel import export_fresh_nem_workbook, load_verified_nem_workbook

            if args.command == "export-nem-fresh-excel":
                count = export_fresh_nem_workbook(args.workbook, args.as_of, args.inputs_dir, args.node, args.node_modules,
                                                  case=args.case, guided=args.guided, preview_dir=args.preview_dir)
            else:
                count = load_verified_nem_workbook(args.workbook, args.as_of, args.inputs_dir,
                                                  case=args.case, guided=args.guided).cells_matched
            print(f"Verified {count:,} saved Newmont workbook cells against the dated Python model.")
            return 0
        if args.command in ("export-nem-fresh-pdf", "verify-nem-fresh-pdf"):
            from .nem_fresh_pdf import export_nem_fresh_pdf, verify_nem_fresh_pdf

            action = export_nem_fresh_pdf if args.command == "export-nem-fresh-pdf" else verify_nem_fresh_pdf
            result = action(args.workbook, args.pdf, as_of=args.as_of, inputs_dir=args.inputs_dir, case=args.case, guided=args.guided)
            print(f"Verified {result['page_count']} PDF pages and {result['workbook_checks']:,} workbook cells.")
            print(f"Workbook SHA-256 {result['workbook_sha256']}")
            return 0
        if args.command == "build-research-demo":
            from .web_catalog import package_static_demo

            catalog = package_static_demo(args.root, args.destination)
            print(f"Staged {len(catalog['snapshots'])} verified research packages in {args.destination}.")
            return 0
        if args.command == "serve-research":
            from .web_api import serve_research

            serve_research(args.root, port=args.port, node=args.node, node_modules=args.node_modules)
            return 0
        if args.command in ("export-gd-fresh-pdf", "verify-gd-fresh-pdf"):
            from .gd_fresh_pdf import export_fresh_gd_pdf, verify_fresh_gd_pdf

            action = export_fresh_gd_pdf if args.command == "export-gd-fresh-pdf" else verify_fresh_gd_pdf
            result = action(args.template, args.workbook, args.pdf, args.as_of, args.inputs_dir, case=args.case)
            print(f"Verified {result.pages} PDF pages and {result.workbook_cells_matched:,} workbook cells.")
            print(f"Workbook SHA-256 {result.workbook_sha256}")
            return 0
        if args.command == "export-gd-fresh-excel":
            from .gd_fresh_excel import export_fresh_gd_workbook

            count = export_fresh_gd_workbook(
                args.template, args.output, args.as_of, args.inputs_dir, args.node, args.node_modules,
                case=args.case, preview_dir=args.preview_dir,
            )
            print(f"Verified {count} saved cells against Python. Wrote {args.output}.")
            return 0
        if args.command == "verify-gd-fresh-excel":
            from .gd_fresh_excel import verify_fresh_gd_workbook

            count = verify_fresh_gd_workbook(args.workbook, args.template, args.as_of, args.inputs_dir, args.case)
            print(f"Verified {count} saved cells against Python for the {args.case} case.")
            return 0
        if args.command == "forecast-gd-statements":
            from .gd_forward_statements import run_gd_statements

            result = run_gd_statements(args.as_of, args.inputs_dir, case=args.case)
            print(json.dumps(asdict(result), default=str, indent=2, allow_nan=False))
            return 0
        if args.command == "value-gd":
            from .gd_valuation import run_gd_valuation

            result = run_gd_valuation(args.as_of, args.inputs_dir, case=args.case)
            print(json.dumps(asdict(result), default=str, indent=2, allow_nan=False))
            return 0
        if args.command == "compare-gd":
            from .gd_comparables import run_gd_comparables

            result = run_gd_comparables(args.as_of, args.inputs_dir)
            print(json.dumps(asdict(result), default=str, indent=2, allow_nan=False))
            return 0
        if args.command == "target-gd":
            from .gd_horizon import run_gd_horizon

            result = run_gd_horizon(args.as_of, args.inputs_dir, case=args.case)
            print(json.dumps(asdict(result), default=str, indent=2, allow_nan=False))
            return 0
        if args.command == "research-gd":
            from .gd_research import run_gd_research

            result = run_gd_research(args.as_of, args.inputs_dir)
            print(json.dumps(asdict(result), default=str, indent=2, allow_nan=False))
            return 0
        if args.command == "forecast-gd-operations":
            from .gd_evidence import load_gd_history_evidence
            from .gd_interim import load_gd_interim
            from .gd_operating_forecast import forecast_gd_operations, load_gd_operating_assumptions, load_gd_segment_evidence

            result = forecast_gd_operations(
                load_gd_interim(args.interim), load_gd_history_evidence(args.history),
                load_gd_segment_evidence(args.segment_evidence), load_gd_operating_assumptions(args.assumptions),
            )
            print(json.dumps(asdict(result), default=str, indent=2, allow_nan=False))
            return 0
        if args.command == "forecast-gd-cash":
            from .gd_cash_forecast import forecast_gd_q4_cash, load_gd_cash_outlook
            from .gd_financing import apply_gd_financing, load_gd_financing
            from .gd_inputs import SCHEMA_VERSION
            from .gd_interim import load_gd_interim

            if args.financing is not None and args.bridge is None:
                raise ValueError("Select a cash timing case with --bridge when supplying --financing.")
            interim = load_gd_interim(args.interim)
            result = forecast_gd_q4_cash(interim, load_gd_cash_outlook(args.outlook))
            output = asdict(result)
            if args.bridge is not None:
                case = result.timing_cases.get(args.bridge)
                if case is None:
                    raise ValueError(f"Unknown timing case {args.bridge!r}. Choose from {', '.join(result.timing_cases)}.")
                cash_inputs = case.cash_inputs
                if args.financing is not None:
                    cash_inputs = apply_gd_financing(interim, cash_inputs, load_gd_financing(args.financing))
                output = {"schema_version": SCHEMA_VERSION, "ticker": "GD", "currency": "USD", **asdict(cash_inputs)}
            print(json.dumps(output, default=str, indent=2, allow_nan=False))
            return 0
        if args.command == "verify-gd-history":
            from .gd_evidence import load_gd_history_evidence
            from .gd_fresh import audit_gd_history
            from .gd_run_inputs import load_market_inputs

            result = audit_gd_history(load_gd_history_evidence(args.evidence), load_market_inputs(args.market))
            print(json.dumps(asdict(result), default=str, indent=2, allow_nan=False))
            return 0 if result.history_ready else 2
        if args.command == "verify-gd-refresh":
            from .gd_cash_bridge import load_gd_cash_inputs
            from .gd_evidence import load_gd_history_evidence
            from .gd_interim import assess_gd_refresh, load_gd_interim
            from .gd_run_inputs import load_market_inputs

            cash_inputs = load_gd_cash_inputs(args.cash_bridge) if args.cash_bridge else None
            annual_history = load_gd_history_evidence(args.annual_history) if args.annual_history else None
            result = assess_gd_refresh(
                load_gd_interim(args.interim), load_market_inputs(args.market), cash_inputs, annual_history=annual_history,
            )
            print(json.dumps(asdict(result), default=str, indent=2, allow_nan=False))
            return 0
        if args.command == "export-gd-pdf":
            from .gd_pdf import export_gd_pdf

            result = export_gd_pdf(args.template, args.workbook, args.output, guided=args.guided)
            print(f"{result.pages}-page GD report saved to {result.output}.")
            parity = result.data.parity
            print(f"Matched {parity.forecast_cells_matched:,} forecast results and {parity.sensitivity_cells_matched} Excel sensitivity values.")
            return 0
        if args.command == "export-gd-excel":
            from .gd_excel import export_gd_excel

            result = export_gd_excel(args.template, args.output, guided=args.guided)
            if args.json:
                print(json.dumps(asdict(result), default=str, indent=2, allow_nan=False))
            else:
                print(f"Editable GD model saved to {result.output}.")
                print(f"Valuation date             {result.projection.as_of.isoformat()}")
                print(f"Intrinsic value            ${result.projection.intrinsic_usd_per_share:,.2f} per share")
                print(f"12-month target            ${result.projection.twelve_month_usd_per_share:,.2f} per share")
                print(f"Modeled forecast cells     {result.forecast_cells_cached:,}")
                print(f"Native sensitivity cells   {result.sensitivity_cells_cached}")
                print("The Run inputs sheet records sources, guided changes, and outstanding model issues.")
            return 0
        if args.command == "value-nem":
            from .nem_nav import run_nem_nav

            report = run_nem_nav(args.as_of, args.inputs_dir, guided=args.guided)
            if args.json:
                print(json.dumps(report, default=lambda value: asdict(value) if is_dataclass(value) else str(value),
                                 indent=2, allow_nan=False))
            else:
                print(f"Newmont NAV as of {args.as_of:%B %d, %Y} ({report['mode']}).")
                print(f"Reference close ${report['reference_close']['price_usd']:.2f}. WACC {report['cost_of_capital']['wacc']:.2%}.")
                print(f"{'Case':<7} {'Spot NAV':>10} {'Target':>10} {'Total return':>14} {'Rating':>8}")
                for name, row in report["scenarios"].items():
                    print(f"{name.title():<7} ${row['spot']['value_per_share']:>9,.2f} ${row['target']['value_per_share']:>9,.2f} "
                          f"{row['total_return']:>14.2%} {row['rating']:>8}")
                comps = report["comparables"]
                print(f"Peer median {comps['median_price_to_earnings']:.2f}x trailing reported EPS implies ${comps['median_implied_nem_spot_usd']:,.2f} spot.")
                print(f"Passed {len(report['checks']):,} valuation reconciliation checks.")
            return 0
        if args.command == "forecast-nem-statements":
            from .nem_forward_statements import run_nem_statements

            report = run_nem_statements(args.as_of, args.inputs_dir, guided=args.guided)
            if args.json:
                print(json.dumps(report, default=lambda value: asdict(value) if is_dataclass(value) else str(value),
                                 indent=2, allow_nan=False))
            else:
                print(f"Newmont linked statements as of {args.as_of:%B %d, %Y} ({report['mode']}).")
                print(f"{'Period':<12} {'Sales $m':>11} {'Parent NI $m':>14} {'Cash capex $m':>14} {'Core FCFF $m':>14} {'Cash $m':>12}")
                for key, label in (("q4_2026", "Q4 2026E"), ("fy_2027", "FY2027E")):
                    period = report["periods"][key]
                    income, cash = period.income_statement, period.cash_flow
                    print(f"{label:<12} {income['sales']:>11,.0f} {income['net_income_attributable_to_newmont']:>14,.0f} "
                          f"{cash['cash_capex']:>14,.0f} {cash['core_consolidated_unlevered_free_cash_flow']:>14,.0f} {cash['closing_cash']:>12,.0f}")
                cutoff = report["cutoff"]
                print(f"Pre-open estimated cash before reserve ${cutoff['cash_before_operating_reserve_millions']:,.0f}m.")
                print(f"Pre-open estimated cash if the NGM contribution had settled ${cutoff['cash_if_ngm_already_paid_millions']:,.0f}m.")
                print(f"Passed {len(report['checks']):,} statement and capital checks in addition to the operating forecast checks.")
            return 0
        if args.command == "forecast-nem-current":
            from .nem_current import run_current_nem

            report = run_current_nem(args.as_of, args.inputs_dir, guided=args.guided)
            if args.json:
                print(json.dumps(report, default=lambda value: asdict(value) if is_dataclass(value) else str(value),
                                 indent=2, allow_nan=False))
            else:
                print(f"Newmont operating forecast as of {args.as_of:%B %d, %Y} ({report['mode']}).")
                print(f"{'Period':<12} {'Attr. gold koz':>14} {'Gold sold koz':>14} {'Sales $m':>12} {'CAS $m':>12}")
                for period, result in report["periods"].items():
                    gold = result["metals"]["gold"]["volume"]
                    print(f"{period:<12} {gold.attributable_gold_produced:>14,.0f} {gold.sold:>14,.0f} "
                          f"{result['sales_millions']:>12,.0f} {result['cas_millions']:>12,.0f}")
                print(f"Passed {len(report['checks']):,} source, volume, price and cost reconciliations.")
            return 0
        if args.command == "verify-nem-revenue":
            from .nem_replay import replay_nem

            replay = replay_nem(args.workbook, gold_price_change_usd=args.gold_price_change)
            if args.json:
                print(json.dumps(asdict(replay), default=str, indent=2, allow_nan=False))
            else:
                base = replay.base.total_revenue_millions
                scenario = replay.scenario.total_revenue_millions
                print(f"Newmont reference forecast for FY ended {replay.base.period_end:%B %d, %Y}.")
                print(f"Matched {replay.cells_matched} saved cells.")
                print(f"Gold price                 ${replay.gold_price_base_usd_per_oz:,.2f} -> ${replay.gold_price_scenario_usd_per_oz:,.2f} per oz")
                print(f"Base revenue               ${base:,.2f} million")
                print(f"Scenario revenue           ${scenario:,.2f} million ({scenario - base:+,.2f} million)")
                print(f"Base production cost       ${replay.base.total_production_cost_millions:,.2f} million")
                print(f"Scenario production cost   ${replay.scenario.total_production_cost_millions:,.2f} million")
                for issue in replay.known_issues:
                    print(f"- {issue}")
            return 0
        if args.command == "verify-fdx-revenue":
            from .fdx_replay import replay_fdx

            replay = replay_fdx(args.workbook, priority_yield_growth=args.priority_yield_growth)
            if args.json:
                print(json.dumps(asdict(replay), default=str, indent=2, allow_nan=False))
            else:
                base = replay.base.total_revenue_millions
                scenario = replay.scenario.total_revenue_millions
                print(f"FedEx legacy revenue forecast for FY ended {replay.base.period_end:%B %d, %Y}.")
                print(f"Matched {replay.cells_matched} saved cells with {replay.base.operating_days} operating days.")
                print(f"U.S. Priority yield growth  {replay.priority_yield_growth_base:.2%} -> {replay.priority_yield_growth_scenario:.2%}")
                print(f"Base revenue               ${base:,.2f} million")
                print(f"Scenario revenue           ${scenario:,.2f} million ({scenario - base:+,.2f} million)")
                print(f"Workbook segment total     ${replay.saved_segment_total_millions:,.2f} million")
                print(f"Segment total less driver total  {replay.segment_gap_millions:+,.2f} million")
                for issue in replay.known_issues:
                    print(f"- {issue}")
            return 0
        if args.command == "prepare-gd":
            from .gd_evidence import load_gd_history_evidence
            from .gd_fresh import prepare_gd_inputs
            from .gd_run_inputs import load_forecast_assumptions, load_market_inputs

            if args.output.exists():
                raise FileExistsError(f"Choose a new input filename. {args.output} already exists.")
            result = prepare_gd_inputs(
                load_gd_history_evidence(args.evidence), load_forecast_assumptions(args.assumptions),
                load_market_inputs(args.market),
            )
            if result.inputs is None:
                print(json.dumps({"projection_ready": False, "missing": result.missing}, indent=2))
                return 2
            save_inputs(args.output, result.inputs)
            print(f"Validated GD inputs saved to {args.output}.")
            return 0
        if args.command == "collect-gd":
            from .gd_evidence import collect_gd_evidence
            from .gd_history_checks import compare_gd_history
            from .sec_client import SECClient

            user_agent = environ.get("SEC_USER_AGENT", "")
            if not user_agent.strip():
                raise ValueError("Set SEC_USER_AGENT to the project name and its contact email before requesting EDGAR.")
            reference = load_inputs(args.compare_inputs) if args.compare_inputs else None
            evidence = collect_gd_evidence(SECClient(user_agent), as_of=args.as_of)
            result = asdict(evidence)
            if reference is not None:
                result["private_comparison"] = asdict(compare_gd_history(
                    evidence.history, evidence.historical_years, as_of=args.as_of, reference=reference,
                ))
            print(json.dumps(result, default=str, indent=2, allow_nan=False))
            return 0
        if args.command == "export-inputs":
            if args.output.exists():
                raise FileExistsError(f"Choose a new input filename. {args.output} already exists.")
            fixture = load_workbook_inputs(args.workbook, MODEL_INPUT_SHEETS)
            run_projection(fixture.inputs)
            save_inputs(args.output, fixture.inputs)
            print(f"Private GD inputs saved to {args.output}.")
            return 0
        if args.command == "run":
            inputs = load_inputs(args.inputs)
            if args.guided:
                inputs = load_guided_overrides(args.guided, inputs)
            report = run_projection(inputs)
        else:
            report = project_gd(args.workbook) if args.project else replay_gd(args.workbook)
    except (ValueError, OSError) as exc:
        company = "FedEx" if "fdx" in args.command.split("-") else "Newmont" if "nem" in args.command.split("-") else "GD"
        print(f"{company} run failed. {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(asdict(report), default=str, indent=2))
    elif isinstance(report, ProjectionReport):
        print_projection(report)
    else:
        print_replay(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""FedEx operating and three-statement formulas for the December fiscal year."""

from datetime import date

from openpyxl.utils.datetime import to_excel

from .fdx_current import SERVICES, SEGMENTS
from .fdx_forward_operations import GROUPS, SERVICE_SEGMENTS, YEARS
from .fdx_forward_statements import _dated_inputs


CASES = ("bear", "base", "bull")
FORECAST = "EFGHI"
PCT = '0.00%;(0.00%);"-"'
USD = '"$"#,##0.00;("$"#,##0.00);"-"'
DAY = "mmm d, yyyy"
M = '#,##0.0;(#,##0.0);"-"'
SERVICES_LABELS = ("U.S. Priority package", "U.S. Deferred package", "U.S. Ground package",
                   "U.S. express freight", "International Priority package", "International Economy package",
                   "International domestic package", "International Priority freight", "International Economy freight")
SEGMENT_LABELS = ("Express U.S. Domestic", "Express International", "Corporate, other and eliminations")


def _labels(plan, sheet, entries, *, totals=(), sections=()):
    for row, label in entries.items():
        kind = "section" if row in sections else "total" if row in totals else ""
        plan.set(sheet, f"A{row}", label, kind)


def _sheet(plan, name, title, subtitle):
    plan.set(name, "A1", title, "title")
    plan.set(name, "A2", subtitle, "subtitle")


def _periods(plan, sheet, selected, *, q4=True):
    plan.set(sheet, "A3", "Case selected")
    plan.calc(sheet, "B3", "Cover!$B$5", selected.title())
    if q4:
        plan.set(sheet, "D5", "Q4 2026E", "header")
    for column, year in zip(FORECAST, YEARS, strict=True):
        plan.set(sheet, f"{column}5", year, "header", '0"E"')


def _input(plan, sheet, address, value, fmt=None):
    plan.set(sheet, address, value, "input", fmt)


def _active(plan, row, label, table, fmt, selected):
    _labels(plan, "Assumptions", {row: label, row + 1: "Bear", row + 2: "Base", row + 3: "Bull"}, totals=(row,))
    for offset, case in enumerate(CASES, 1):
        for col, value in zip(FORECAST, table[case], strict=True):
            _input(plan, "Assumptions", f"{col}{row + offset}", value, fmt)
    for index, col in enumerate(FORECAST):
        pick = f'IF($B$3="Bear",{col}{row + 1},IF($B$3="Base",{col}{row + 2},{col}{row + 3}))'
        plan.calc("Assumptions", f"{col}{row}", pick, table[selected][index], fmt=fmt)


def _actuals(plan, data):
    sheet = "Actuals"
    _sheet(plan, sheet, "Historical financials", "USD millions except per-share data. Calendar income statements reflect continuing operations.")
    income = data["income_statements"]
    period_keys = ("2024", "2025", "2025_q3", "2025_q4", "2026_q3", "2026_ytd")
    fields = {
        7: "revenue", 8: "salaries_and_benefits", 9: "purchased_transportation", 10: "rentals_and_landing_fees",
        11: "fuel", 12: "maintenance_and_repairs", 13: "other_operating_expenses", 14: "depreciation_amortization",
        15: "operating_expenses", 16: "operating_income", 17: "net_interest", 18: "other_nonoperating_income",
        19: "short_term_investment_gain_loss", 20: "pretax_income", 21: "income_taxes", 22: "continuing_net_income",
        23: "discontinued_net_income", 24: "net_income", 25: "continuing_diluted_eps", 26: "dividends_declared_per_share",
    }
    _labels(plan, sheet, {r: key.replace("_", " ").capitalize() for r, key in fields.items()},
            totals=(7, 15, 16, 22, 24))
    for col, key, label in zip("BCDEFG", period_keys, ("2024A", "2025A", "Q3 2025A", "Q4 2025A", "Q3 2026A", "9M 2026A"), strict=True):
        item = income[key]
        plan.set(sheet, f"{col}5", label, "header")
        plan.set(sheet, f"{col}6", to_excel(date.fromisoformat(item["period_end"])), "header", DAY)
        for row, field in fields.items():
            if item.get(field) is not None:
                plan.set(sheet, f"{col}{row}", item[field], fmt=USD if row in (25, 26) else M)
        plan.set(sheet, f"{col}27", item["source_id"], "note")
    _labels(plan, sheet, {30: "Reported balance sheet", 31: "Cash and cash equivalents", 32: "Receivables",
                         33: "Spare parts, supplies and fuel", 34: "Prepaids and other", 35: "Gross PP&E", 36: "Total assets",
                         37: "Total liabilities", 38: "Total equity"}, sections=(30,), totals=(36, 37, 38))
    for col, key in zip("BCD", ("2025-12-31", "2026-05-31", "2026-09-30"), strict=True):
        item = data["balance_sheets"][key]
        plan.set(sheet, f"{col}30", to_excel(date.fromisoformat(key)), "header", DAY)
        for row, field in zip(range(31, 39), ("cash", "receivables", "spare_parts_supplies_and_fuel",
                              "prepaids_and_other", "gross_ppe", "total_assets", "total_liabilities", "total_equity"), strict=True):
            if item.get(field) is not None:
                plan.set(sheet, f"{col}{row}", item[field])
        plan.calc(sheet, f"{col}37", f"{col}36-{col}38", item["total_liabilities_and_equity"] - item["total_equity"])
        plan.set(sheet, f"{col}39", item["source_id"], "note")
    cf_fields = {44: "opening_cash", 45: "cash_from_operations", 46: "capital_expenditures",
                 47: "cash_from_investing", 48: "cash_from_financing", 49: "effect_of_exchange_rates",
                 50: "closing_cash"}
    _labels(plan, sheet, {42: "Reported consolidated cash flow (mixed basis)", **{
        row: key.replace("_", " ").capitalize() for row, key in cf_fields.items()}}, sections=(42,), totals=(50,))
    for col, key, label in zip("BCD", ("2025_ytd", "2026_ytd", "2026_transition_through_q3"),
                               ("9M 2025A", "9M 2026A", "Jun–Sep 2026A"), strict=True):
        cf = data["cash_flows"][key]
        plan.set(sheet, f"{col}43", label, "header")
        for row, key in cf_fields.items():
            plan.set(sheet, f"{col}{row}", cf[key])
        plan.set(sheet, f"{col}51", cf["source_id"], "note")


def _assumptions(plan, docs, current, report, selected):
    sheet = "Assumptions"
    _sheet(plan, sheet, "Forecast assumptions", "Blue values are editable. Change the active case on Cover.")
    _periods(plan, sheet, selected)
    raw = docs["forward-operations"]
    params = docs["statement-assumptions"]
    financing = docs["financing"]
    cash = report["q4_operating_cash_bridge"]
    q4 = report["q4"]
    q3 = docs["actuals"]["income_statements"]["2026_q3"]
    financing_fields = {
        8: ("Notes coupon rate", financing["opening_debt"]["reported_notes_weighted_coupon"], PCT),
        9: ("Finance lease discount rate", financing["finance_lease_discount_rate_proxy"], PCT),
        10: ("Normalized tax rate", params["normalized_tax_rate"]["value"], PCT),
        11: ("Quarterly dividend per share", financing["dividends"]["forecast_quarterly_per_share"], USD),
        12: ("Basic shares outstanding (millions)", current["capital_inputs"]["fdx_shares"] / 1e6, M),
        13: ("Dilutive share awards (millions)", params["dilutive_stock_awards_millions"]["value"], M),
        14: ("Annual voluntary pension funding", params["annual_pension_contribution_millions"]["value"], M),
        15: ("Long-run cash capex / revenue", params["capex_target_share_of_revenue"]["value"], PCT),
        16: ("Q3 stock compensation", params["q3_stock_compensation_millions"]["value"], M),
        17: ("Gross property retirement rate", report["estimated_gross_property_retirement_rate"], PCT),
        18: ("CY2026 tax-normalized Q4 EBIT proxy", q4.income_statement["operating_income"], M),
        19: ("Q4 adjusted net income midpoint", q4.income_statement["net_income"], M),
        20: ("Q4 average diluted share guidance", q4.income_statement["diluted_shares_millions"], M),
        21: ("Q4 voluntary pension funding", cash.fourth_quarter.voluntary_pension_contribution_millions, M),
        22: ("CY2026 guidance operating cash flow", cash.calendar_2026_guidance_cfo_millions, M),
        23: ("Reported nine-month CFO (mixed basis)", cash.reported_nine_month_cfo_millions, M),
        24: ("September customer tariff payable", cash.september_customer_refund_obligation_millions, M),
        25: ("December customer tariff payable estimate", cash.year_end_customer_refund_obligation_millions, M),
        26: ("Continuing cash capex guidance", cash.calendar_2026_guidance_continuing_capex_millions, M),
        27: ("Continuing nine-month cash capex", cash.continuing_nine_month_capex_millions, M),
        28: ("October Supply Chain cash proceeds", cash.fourth_quarter.supply_chain_sale_cash_millions, M),
        29: ("October cash dividend payment", financing["dividends"]["october_1_paid_cash_millions"], M),
        30: ("Q4 calendar days", cash.fourth_quarter.calendar_days, "0"),
        31: ("Q4 calendar days through Nov. 11", cash.through_cutoff.calendar_days, "0"),
        32: ("New notes issued per period", financing["funding_assumptions"]["new_note_proceeds_millions_per_period"], M),
        33: ("New finance leases per period", financing["funding_assumptions"]["new_finance_lease_funding_millions_per_period"], M),
        34: ("CY2025 revenue", docs["actuals"]["income_statements"]["2025"]["revenue"], M),
        35: ("CY2026 fuel share of sales growth", raw["fuel_normalization"]["cy25_sales_growth_contribution"], PCT),
        36: ("Fuel growth removed from opening base", raw["fuel_normalization"]["fraction_removed_from_2027_base"], PCT),
        37: ("2025 corporate Q4 revenue share", report["operating_forecast"].opening.corporate_other_annualization_share, PCT),
    }
    _labels(plan, sheet, {7: "Shared inputs"}, sections=(7,))
    for row, (label, value, fmt) in financing_fields.items():
        plan.set(sheet, f"A{row}", label)
        _input(plan, sheet, f"B{row}", value, fmt)
    _labels(plan, sheet, {39: "Capital and operating calendar", 40: "Operating days",
                          41: "Scheduled note principal", 42: "Contractual finance lease cash payments",
                          43: "May fiscal-year intangible amortization", 44: "Calendar-year capex / sales",
                          45: "Stock compensation / sales", 46: "Depreciation / average gross PP&E",
                          47: "Calendar-year intangible amortization", 48: "Q4 operating cash excl. customer tariffs"},
            sections=(39,))
    _input(plan, sheet, "D40", current["q4_revenue"]["operating_days"], "0")
    _input(plan, sheet, "C43", params["may_fiscal_amortization_millions"]["values"]["2027"], M)
    plan.calc(sheet, "D47", "C43*3/12", q4.supporting_schedules["intangible_amortization"])
    plan.calc(sheet, "B45", "B16/Actuals!F7", params["q3_stock_compensation_millions"]["value"] / q3["revenue"], fmt=PCT)
    plan.calc(sheet, "B46", "(Actuals!G14+'Income statement'!D9-C43)/AVERAGE(Actuals!B35,'PP&E'!D11)",
              report["depreciation_rate_on_average_gross_property"], fmt=PCT)
    plan.calc(sheet, "B48", "B22-B23+B24-B25", cash.fourth_quarter.operating_cfo_excluding_tariffs_millions)
    for col, year in zip("D" + FORECAST, range(2026, 2032), strict=True):
        _input(plan, sheet, f"{col}41", financing["note_principal_millions"][str(year)], M)
        _input(plan, sheet, f"{col}42", financing["finance_lease_cash_payments_millions"][str(year)], M)
    annual_capex = cash.calendar_2026_guidance_continuing_capex_millions
    sales = current["calendar_year"]["forecast_revenue_millions"]
    rate26 = annual_capex / sales
    capex_target = params["capex_target_share_of_revenue"]["value"]
    for i, (col, period) in enumerate(zip(FORECAST, report["years"], strict=True)):
        year = period.period_end.year
        _input(plan, sheet, f"{col}40", report["operating_forecast"].years[i].operating_days, "0")
        _input(plan, sheet, f"{col}43", params["may_fiscal_amortization_millions"]["values"][str(year)], M)
        nxt = FORECAST[min(i + 1, 4)]
        plan.calc(sheet, f"{col}47", f"({col}43*5+{nxt}43*7)/12", period.supporting_schedules["intangible_amortization"])
        expressions = ("$B$26/Revenue!$D$13", "AVERAGE(E44,$B$15)", "$B$15")
        rates = (rate26, (rate26 + capex_target) / 2, capex_target)
        plan.calc(sheet, f"{col}44", expressions[min(i, 2)], rates[min(i, 2)], fmt=PCT)

    _labels(plan, sheet, {50: "Annual volume and yield drivers"}, sections=(50,))
    for i, (service, label) in enumerate(zip(SERVICES, SERVICES_LABELS, strict=True)):
        for k, field in enumerate(("volume_growth", "yield_growth")):
            row = 52 + 8 * i + 4 * k
            group = SERVICE_SEGMENTS[service]
            case_field = f"{field}_adjustments"
            base = raw["base_service_assumptions"][service][field]
            table = {case: tuple(a + b for a, b in zip(base, raw["cases"][case][case_field][group], strict=True)) for case in CASES}
            _active(plan, row, f"{label} {field.replace('_', ' ')}", table, PCT, selected)

    segment_rows = {segment: 126 + i * 8 for i, segment in enumerate(SEGMENTS)}
    for segment, label in zip(SEGMENTS, SEGMENT_LABELS, strict=True):
        row = segment_rows[segment]
        for offset, field, description in ((0, "other_revenue_growth", "other revenue growth"), (4, "operating_margins", "operating margin")):
            if offset == 4 and segment == SEGMENTS[-1]:
                table = {case: raw["cases"][case]["corporate_operating_income_millions"] for case in CASES}
                _active(plan, row + offset, "Corporate operating income", table, M, selected)
                continue
            table = {case: raw["cases"][case][field][segment] for case in CASES}
            _active(plan, row + offset, f"{label} {description}", table, PCT, selected)


def _revenue(plan, docs, current, report, selected):
    sheet = "Revenue"
    _sheet(plan, sheet, "Revenue and operating income", "USD millions. Volume per operating day in thousands. Yield in USD per unit.")
    _periods(plan, sheet, selected)
    plan.set(sheet, "B5", "CY2026 base", "header")
    plan.set(sheet, "C5", "CY2026 normalized", "header")
    forecast = report["operating_forecast"]
    opening = forecast.opening
    q4 = current["q4_revenue"]
    ops = docs["operations"]["periods"]
    q3s = tuple(ops[f"2026q{i}"] for i in (1, 2, 3))
    q4_prior = ops["2025q4"]
    raw = docs["forward-operations"]
    revenue_rows = {
        6: "Operating days", 7: "Express U.S. Domestic revenue", 8: "Express International revenue",
        9: "Corporate, other and eliminations", 10: "Quarter or normalized revenue total",
        12: "CY2026 nine-month continuing revenue", 13: "Consolidated calendar-year revenue",
        16: "Express U.S. Domestic operating margin", 17: "Express International operating margin",
        18: "Express U.S. Domestic operating income", 19: "Express International operating income",
        20: "Corporate and unallocated operating income", 21: "Continuing operating income", 22: "Operating margin",
        24: "Fuel surcharge removed", 25: "Domestic fuel weighting", 26: "International fuel weighting",
        28: "Service revenue, volume and yield",
    }
    _labels(plan, sheet, revenue_rows, totals=(10, 13, 21, 22), sections=(28,))
    days_ytd = sum(q["operating_days"] for q in q3s)
    plan.set(sheet, "K6", "Nine-month operating days", "note")
    plan.set(sheet, "L6", days_ytd, fmt="0")
    plan.set(sheet, "K7", "Prior Q4 operating days", "note")
    plan.set(sheet, "L7", q4_prior["operating_days"], fmt="0")
    plan.calc(sheet, "B6", "L6+D6", opening.operating_days, fmt="0")
    plan.calc(sheet, "C6", "B6", opening.operating_days, fmt="0")
    plan.calc(sheet, "D6", "Assumptions!D40", q4["operating_days"], fmt="0")
    plan.calc(sheet, "D12", "Actuals!G7", current["calendar_year"]["reported_revenue_millions"])
    plan.calc(sheet, "D13", "SUM(D10,D12)", current["calendar_year"]["forecast_revenue_millions"], kind="total")
    plan.calc(sheet, "B24", "Assumptions!B34*Assumptions!B35*Assumptions!B36", opening.fuel_surcharge_removed_millions)
    weights = []
    for i, seg in enumerate(SEGMENTS[:2]):
        row = 25 + i
        prior = sum(ops[f"2025q{q}"]["segment_revenue_millions"][seg] for q in (1, 2, 3, 4))
        weight = raw["fuel_normalization"]["segment_weights_by_cy25_revenue"][seg]
        plan.set(sheet, f"L{row}", prior)
        _input(plan, sheet, f"M{row}", weight, PCT)
        plan.calc(sheet, f"B{row}", f"L{row}*M{row}", prior * weight)
        weights.append(prior * weight)
    plan.set(sheet, "L24", "CY2025 segment sales", "header")
    plan.set(sheet, "M24", "Fuel weight", "header")
    plan.set(sheet, "L28", "Prior Q4 inputs", "header")
    plan.set(sheet, "M28", "2026 nine-month inputs", "header")
    segment_service_rows = {segment: [] for segment in GROUPS}
    for i, (service, label) in enumerate(zip(SERVICES, SERVICES_LABELS, strict=True)):
        r = 30 + i * 6
        segment = SERVICE_SEGMENTS[service]
        segment_service_rows[segment].append(r)
        _labels(plan, sheet, {r: f"{label} revenue", r + 1: "Daily volume (thousands)", r + 2: "Yield (USD per unit)",
                             r + 3: "Volume growth", r + 4: "Yield growth"}, totals=(r,))
        base = opening.services[service]
        final = q4["services"][service]
        prior = q4_prior["services"][service]
        total_units = sum(q["services"][service]["daily_volume_thousands"] * q["operating_days"] for q in q3s)
        weighted_yield = sum(q["services"][service]["daily_volume_thousands"] * q["operating_days"] *
                             q["services"][service]["yield_usd"] for q in q3s)
        for offset, key in enumerate(("revenue_millions", "daily_volume_thousands", "yield_usd")):
            plan.set(sheet, f"L{r + offset}", prior[key])
        for offset, amount in enumerate((sum(q["services"][service]["revenue_millions"] for q in q3s), total_units, weighted_yield)):
            plan.set(sheet, f"M{r + offset}", amount)
        _input(plan, sheet, f"D{r + 3}", final.volume_growth, PCT)
        _input(plan, sheet, f"D{r + 4}", final.yield_growth, PCT)
        plan.calc(sheet, f"D{r}", f"L{r}*(1+D{r + 3})*(1+D{r + 4})*D$6/$L$7", final.revenue_millions, kind="total")
        plan.calc(sheet, f"D{r + 1}", f"L{r + 1}*(1+D{r + 3})", final.daily_volume_thousands)
        plan.calc(sheet, f"D{r + 2}", f"L{r + 2}*(1+D{r + 4})", final.yield_usd, fmt=USD)
        plan.calc(sheet, f"B{r}", f"SUM(M{r},D{r})", base.revenue_millions)
        plan.calc(sheet, f"B{r + 1}", f"(M{r + 1}+D{r + 1}*D$6)/B$6", base.daily_volume_thousands)
        plan.calc(sheet, f"B{r + 2}", f"(M{r + 2}+D{r + 1}*D$6*D{r + 2})/(B{r + 1}*B$6)", base.yield_usd, fmt=USD)
        allocation = 25 + SEGMENTS.index(segment)
        fuel_share = f"$B$24*($B${allocation}/SUM($B$25:$B$26))/$B${95 + SEGMENTS.index(segment)}"
        plan.calc(sheet, f"C{r}", f"B{r}*(1-{fuel_share})", base.normalized_revenue_millions)
        plan.calc(sheet, f"C{r + 1}", f"B{r + 1}", base.daily_volume_thousands)
        plan.calc(sheet, f"C{r + 2}", f"B{r + 2}*(1-{fuel_share})", base.normalized_yield_usd, fmt=USD)
        for prev, col, year in zip("C" + FORECAST[:-1], FORECAST, forecast.years, strict=True):
            result = year.services[service]
            plan.calc(sheet, f"{col}{r + 3}", f"Assumptions!{col}{52 + i * 8}", result.volume_growth, fmt=PCT)
            plan.calc(sheet, f"{col}{r + 4}", f"Assumptions!{col}{56 + i * 8}", result.yield_growth, fmt=PCT)
            plan.calc(sheet, f"{col}{r}", f"{prev}{r}*(1+{col}{r + 3})*(1+{col}{r + 4})*{col}$6/{prev}$6", result.revenue_millions, kind="total")
            plan.calc(sheet, f"{col}{r + 1}", f"{prev}{r + 1}*(1+{col}{r + 3})", result.daily_volume_thousands)
            plan.calc(sheet, f"{col}{r + 2}", f"{prev}{r + 2}*(1+{col}{r + 4})", result.yield_usd, fmt=USD)

    for i, (seg, label) in enumerate(zip(SEGMENTS, SEGMENT_LABELS, strict=True)):
        r = 88 + i
        _labels(plan, sheet, {r: f"{label} other revenue"} if i < 2 else {r: f"{label} revenue"})
        other_q4 = q4["domestic_and_international_other_revenue_millions"][seg] if i < 2 else q4["segment_revenue_millions"][seg]
        ytd = sum(q["other_revenue_millions"][seg] if i < 2 else q["segment_revenue_millions"][seg] for q in q3s)
        plan.set(sheet, f"M{r}", ytd)
        _input(plan, sheet, f"D{r}", other_q4, M)
        plan.calc(sheet, f"B{r}", f"SUM(M{r},D{r})", ytd + other_q4)
        normalized = f"B{r}" if i < 2 else f"D{r}/Assumptions!$B$37"
        plan.calc(sheet, f"C{r}", normalized, opening.normalized_other_revenue_millions[seg])
        for prev, col, year in zip("C" + FORECAST[:-1], FORECAST, forecast.years, strict=True):
            plan.calc(sheet, f"{col}{r}", f"{prev}{r}*(1+Assumptions!{col}{126 + i * 8})", year.other_revenue_millions[seg])
        if i < 2:
            service_rows = segment_service_rows[seg]
            _labels(plan, sheet, {95 + i: f"{label} booked CY2026 service revenue"})
            plan.calc(sheet, f"B{95 + i}", "SUM(" + ",".join(f"B{j}" for j in service_rows) + ")",
                      sum(opening.services[k].revenue_millions for k in GROUPS[seg]))
        else:
            service_rows = ()
        for col in "BCD" + FORECAST:
            detail = ",".join(f"{col}{j}" for j in (*service_rows, r))
            if col == "B":
                value = opening.segment_revenue_millions[seg]
            elif col == "C":
                value = opening.normalized_other_revenue_millions[seg] + sum(
                    opening.services[k].normalized_revenue_millions for k in GROUPS.get(seg, ()))
            elif col == "D":
                value = q4["segment_revenue_millions"][seg]
            else:
                value = forecast.years[FORECAST.index(col)].segment_revenue_millions[seg]
            plan.calc(sheet, f"{col}{7 + i}", f"SUM({detail})", value)
    for col, amount in zip("BCD", (opening.revenue_millions, opening.normalized_revenue_millions, q4["revenue_millions"]), strict=True):
        plan.calc(sheet, f"{col}10", f"SUM({col}7:{col}9)", amount, kind="total")
    plan.calc(sheet, "D21", "Assumptions!B18", report["q4"].income_statement["operating_income"], kind="total")
    plan.calc(sheet, "D22", "D21/D10", report["q4"].income_statement["operating_income"] / q4["revenue_millions"], fmt=PCT)
    for col, year in zip(FORECAST, forecast.years, strict=True):
        for row, formula, value, fmt in (
            (6, f"Assumptions!{col}40", year.operating_days, "0"),
            (13, f"SUM({col}7:{col}9)", year.revenue_millions, M),
            (16, f"Assumptions!{col}130", year.segment_operating_margin[SEGMENTS[0]], PCT),
            (17, f"Assumptions!{col}138", year.segment_operating_margin[SEGMENTS[1]], PCT),
            (18, f"{col}7*{col}16", year.segment_operating_income_millions[SEGMENTS[0]], M),
            (19, f"{col}8*{col}17", year.segment_operating_income_millions[SEGMENTS[1]], M),
            (20, f"Assumptions!{col}146", year.segment_operating_income_millions[SEGMENTS[2]], M),
            (21, f"SUM({col}18:{col}20)", year.operating_income_millions, M),
            (22, f"{col}21/{col}13", year.operating_margin, PCT),
        ):
            plan.calc(sheet, f"{col}{row}", formula, value, fmt=fmt, kind="total" if row in (13, 21, 22) else "")


ASSET_FIELDS = {
    18: ("Operating lease assets", "operating_lease_assets"),
    19: ("Goodwill and other operating assets", "goodwill_and_other_operating_assets"),
    20: ("Cumulative modeled pension funding", "incremental_pension_funding"),
    21: ("Retained Freight investment", "retained_freight_investment"),
    22: ("InPost investment", "inpost_investment"),
    23: ("Debt securities", "debt_securities"),
    24: ("Other equity securities", "other_equity_securities"),
    25: ("Supply Chain held-for-sale assets", "supply_chain_held_for_sale"),
}
LIABILITY_FIELDS = {
    33: ("Operating lease liabilities", "operating_leases"), 34: ("Self-insurance", "self_insurance"),
    35: ("Deferred tax", "deferred_tax"), 36: ("Other noncurrent liabilities", "other_noncurrent_liabilities"),
    37: ("Supply Chain held-for-sale liabilities", "supply_chain_held_for_sale"),
}
EQUITY_FIELDS = {
    43: ("Common stock", "common_stock"), 44: ("Additional paid-in capital", "additional_paid_in_capital"),
    45: ("Retained earnings", "retained_earnings"), 46: ("Accumulated other comprehensive loss", "accumulated_other_comprehensive_loss"),
    47: ("Treasury stock", "treasury_stock"),
}


def _period_headers(plan, selected):
    specs = (
        ("Income statement", "Income statement", "USD millions except per-share amounts. Q4 2026 uses issuer adjusted earnings guidance."),
        ("Balance sheet", "Balance sheet", "USD millions. Analytical balances exclude customer tariff cash and the matching obligation."),
        ("Cash flow", "Cash flow and unlevered free cash flow", "USD millions. Cash outflows are signed negatives."),
        ("PP&E", "Property and intangible assets", "USD millions. Retirement proxy assumes fully depreciated assets."),
        ("Debt schedule", "Debt and finance leases", "USD millions. Operating leases remain in operating costs."),
        ("Checks", "Statement reconciliation", "USD millions except per-share items. Differences above $0.01m are flagged."),
    )
    for sheet, title, note in specs:
        _sheet(plan, sheet, title, note)
        _periods(plan, sheet, selected)
    _labels(plan, "Income statement",
            {7: "Revenue", 8: "Operating expenses excluding D&A", 9: "Depreciation and amortization", 10: "Operating income",
             11: "Operating margin", 13: "Interest expense", 14: "Other nonoperating income", 15: "Income before taxes",
             16: "Income taxes", 17: "Continuing net income", 19: "Basic shares (millions)", 20: "Diluted shares (millions)",
             21: "Continuing diluted EPS"}, totals=(7, 10, 15, 17, 21))
    for col in "BC":
        plan.set("Income statement", f"{col}5", "CY2025A" if col == "B" else "9M 2026A", "header")
    _labels(plan, "Balance sheet",
            {6: "Assets", 7: "Cash before operating reserve", 8: "Receivables", 9: "Spare parts and supplies",
             10: "Operating prepaids", 11: "Trade and accrued liabilities", 12: "Net operating working capital",
             15: "Net property", 16: "Net intangible assets", **{r: field[0] for r, field in ASSET_FIELDS.items()},
             26: "Total assets", 28: "Liabilities", 29: "Trade and accrued liabilities", 30: "Notes carrying value",
             31: "Finance lease obligations", 32: "Dividends payable", **{r: field[0] for r, field in LIABILITY_FIELDS.items()},
             38: "Total liabilities", 40: "Estimated current financing debt", 41: "Estimated noncurrent financing debt",
             42: "Stockholders' equity", **{r: field[0] for r, field in EQUITY_FIELDS.items()}, 48: "Total equity",
             49: "Total liabilities and equity"}, totals=(12, 26, 38, 48, 49), sections=(6, 28, 42))
    plan.set("Balance sheet", "C5", "Sep. 2026 opening", "header")
    _labels(plan, "Cash flow",
            {6: "Operating activities", 7: "Continuing net income", 8: "Depreciation and amortization",
             9: "Stock compensation", 10: "Working capital release", 11: "Voluntary pension funding",
             12: "Cash from operations", 14: "Investing activities", 15: "Cash capex", 16: "Supply Chain sale proceeds",
             17: "Cash from investing", 19: "Financing activities", 20: "Note principal paid", 21: "Finance lease principal paid",
             22: "Note issuance proceeds", 23: "Cash dividends paid", 24: "Cash from financing", 26: "Opening cash",
             27: "Net change in cash", 28: "Closing cash before operating reserve", 30: "Unlevered cash flow",
             31: "Operating cash after capex", 32: "NOPAT", 33: "Noncash lease-financed investment",
             34: "Unlevered free cash flow"}, totals=(12, 17, 24, 28, 34), sections=(6, 14, 19, 30))
    _labels(plan, "PP&E",
            {7: "Opening gross property", 8: "Cash capital expenditures", 9: "Finance lease additions",
             10: "Gross property retired", 11: "Closing gross property", 13: "Opening accumulated depreciation",
             14: "Property depreciation", 15: "Closing accumulated depreciation", 16: "Net property",
             18: "Opening intangible assets", 19: "Intangible amortization", 20: "Closing intangible assets",
             22: "Total depreciation and amortization"}, totals=(11, 15, 16, 20, 22))
    _labels(plan, "Debt schedule",
            {7: "Opening notes", 8: "Scheduled note principal paid", 9: "New notes issued", 10: "Closing notes",
             11: "Notes interest", 13: "Opening finance leases", 14: "Contractual finance lease payments",
             15: "Finance lease interest", 16: "Finance lease principal paid", 17: "New finance leases",
             18: "Closing finance leases", 20: "Total debt and finance leases", 21: "Total financing interest",
             22: "Interest-rate year fraction", 24: "Scheduled note principal next year", 25: "Estimated lease principal next year",
             26: "Estimated current financing debt", 27: "Estimated noncurrent financing debt"}, totals=(10, 18, 20, 21, 26, 27))
    _labels(plan, "Checks",
            {7: "Assets less liabilities and equity", 8: "Cash roll-forward difference", 9: "Debt roll-forward difference",
             10: "Net property roll-forward difference", 11: "FCFF from statement cash flow difference",
             12: "Retained earnings roll-forward difference", 13: "Revenue to driver totals difference",
             15: "Q4 operating cash to guidance difference", 16: "Q4 net income to midpoint difference",
             17: "Q4 cutoff bridge to year-end cash difference", 18: "Spot sensitivity midpoint difference",
             19: "Target sensitivity midpoint difference"})


def _history_links(plan, data, opening):
    sheet = "Income statement"
    for col, source, key in (("B", "C", "2025"), ("C", "G", "2026_ytd")):
        raw = data["income_statements"][key]
        link = lambda row: f"Actuals!{source}{row}"
        rows = {
            7: (link(7), raw["revenue"]), 8: (f"{link(15)}-{link(14)}", raw["operating_expenses"] - raw["depreciation_amortization"]),
            9: (link(14), raw["depreciation_amortization"]), 10: (link(16), raw["operating_income"]),
            11: (f"{col}10/{col}7", raw["operating_income"] / raw["revenue"]), 13: (link(17), raw["net_interest"]),
            14: (f"SUM({link(18)},{link(19)})", raw["other_nonoperating_income"] + raw["short_term_investment_gain_loss"]),
            15: (link(20), raw["pretax_income"]), 16: (link(21), raw["income_taxes"]),
            17: (link(22), raw["continuing_net_income"]), 21: (link(25), raw["continuing_diluted_eps"]),
        }
        for row, (expr, value) in rows.items():
            plan.calc(sheet, f"{col}{row}", expr, value, fmt=PCT if row == 11 else USD if row == 21 else M)
    sheet = "Balance sheet"
    wc = opening.working_capital
    rows = {7: opening.cash_before_reserve, 8: wc.receivables, 9: wc.spare_parts_and_supplies,
            10: wc.operating_prepaids, 11: wc.trade_and_accrued_liabilities, 15: opening.property.net_property,
            16: opening.property.intangibles, 30: opening.notes, 31: opening.finance_leases,
            32: opening.dividends_payable}
    rows.update({row: getattr(opening, group)[field] for group, fields in (
        ("other_assets", ASSET_FIELDS), ("other_liabilities", LIABILITY_FIELDS), ("equity", EQUITY_FIELDS))
        for row, (_, field) in fields.items()})
    for row, amount in rows.items():
        plan.set(sheet, f"C{row}", amount)
    for row, formula, expected in (
        (12, "SUM(C8:C10)-C11", wc.net), (26, "SUM(C7:C10,C15:C25)", opening.assets),
        (29, "C11", wc.trade_and_accrued_liabilities), (38, "SUM(C29:C37)", opening.liabilities),
        (48, "SUM(C43:C47)", sum(opening.equity.values())),
        (49, "SUM(C38,C48)", opening.liabilities + sum(opening.equity.values())),
    ):
        plan.calc(sheet, f"C{row}", formula, expected)
    plan.set("PP&E", "C5", "Sep. 2026 opening", "header")
    plan.set("PP&E", "C11", opening.property.gross)
    plan.set("PP&E", "C15", opening.property.accumulated_depreciation)
    plan.calc("PP&E", "C16", "C11-C15", opening.property.net_property)
    plan.calc("PP&E", "C20", "'Balance sheet'!C16", opening.property.intangibles)
    plan.set("Debt schedule", "C5", "Sep. 2026 opening", "header")
    plan.calc("Debt schedule", "C10", "'Balance sheet'!C30", opening.notes)
    plan.calc("Debt schedule", "C18", "'Balance sheet'!C31", opening.finance_leases)


def _forecast_period(plan, col, prior, period, opening, *, q4):
    c, p = col, prior
    b = f"'Balance sheet'!{c}"
    pb = f"'Balance sheet'!{p}"
    is_ = f"'Income statement'!{c}"
    cf = f"'Cash flow'!{c}"
    pp = f"'PP&E'!{c}"
    d = f"'Debt schedule'!{c}"
    a = f"Assumptions!{c}"
    s = "Assumptions!$B$"
    ins, cash, balance, debt, support = period.income_statement, period.cash_flow, period.balance_sheet, period.debt_schedule, period.supporting_schedules
    wc = balance.working_capital
    fx = {
        "Income statement": {
            7: (f"Revenue!{c}{10 if q4 else 13}", ins["revenue"]), 8: (f"{c}7-{c}10-{c}9", ins["operating_expenses_excluding_da"]),
            9: (f"{pp}22", ins["depreciation_and_amortization"]), 10: (f"Revenue!{c}21", ins["operating_income"]),
            11: (f"{c}10/{c}7", ins["operating_income"] / ins["revenue"]),
            13: (f"-{d}21", ins["interest_expense"]),
            14: (f"{s}19/(1-{s}10)-{c}10-{c}13" if q4 else "0", ins["other_core_nonoperating_income"]),
            15: (f"SUM({c}10,{c}13:{c}14)", ins["income_before_taxes"]), 16: (f"{c}15*{s}10", ins["income_taxes"]),
            17: (f"{c}15-{c}16", ins["net_income"]), 19: (f"{s}12", ins["basic_shares_millions"]),
            20: (f"{s}20" if q4 else f"{c}19+{s}13", ins["diluted_shares_millions"]),
            21: (f"{c}17/{c}20", ins["diluted_eps_proxy"]),
        },
        "Balance sheet": {
            7: (f"{cf}28", balance.cash_before_reserve), 8: ("Actuals!B32*'Income statement'!D7/Actuals!E7" if q4 else f"{p}8*{is_}7/Revenue!{p}13", wc.receivables),
            9: ("Actuals!B33*'Income statement'!D7/Actuals!E7" if q4 else f"{p}9*{is_}7/Revenue!{p}13", wc.spare_parts_and_supplies),
            10: ("Actuals!B34*'Income statement'!D7/Actuals!E7" if q4 else f"{p}10*{is_}7/Revenue!{p}13", wc.operating_prepaids),
            11: (f"SUM({c}8:{c}10)-({p}12-({s}48-({s}19+{is_}9+{cf}9+{cf}11)))" if q4
                 else f"{p}11*{is_}7/Revenue!{p}13", wc.trade_and_accrued_liabilities),
            12: (f"SUM({c}8:{c}10)-{c}11", wc.net),
            15: (f"{pp}16", balance.property.net_property), 16: (f"{pp}20", balance.property.intangibles),
            26: (f"SUM({c}7:{c}10,{c}15:{c}25)", balance.assets),
            29: (f"{c}11", wc.trade_and_accrued_liabilities), 30: (f"{d}10", balance.notes), 31: (f"{d}18", balance.finance_leases),
            32: (f"{p}32+{is_}19*{s}11*{1 if q4 else 4}+{cf}23", balance.dividends_payable),
            38: (f"SUM({c}29:{c}37)", balance.liabilities),
            44: (f"{p}44+{cf}9", balance.equity["additional_paid_in_capital"]),
            45: (f"{p}45+{is_}17-{is_}19*{s}11*{1 if q4 else 4}" + (f"+{cf}16-{p}25+{p}37" if q4 else ""),
                 balance.equity["retained_earnings"]),
            48: (f"SUM({c}43:{c}47)", sum(balance.equity.values())),
            49: (f"SUM({c}38,{c}48)", balance.liabilities + sum(balance.equity.values())),
        },
        "Cash flow": {
            7: (f"{is_}17", cash["net_income"]), 8: (f"{is_}9", cash["depreciation_and_amortization"]),
            9: (f"{s}16" if q4 else f"{is_}7*{s}45", cash["stock_compensation"]),
            10: (f"{pb}12-{b}12", cash["working_capital_cash_release"]),
            11: (f"-{s}{21 if q4 else 14}", cash["voluntary_pension_contribution"]),
            12: (f"SUM({c}7:{c}11)", cash["cash_from_operations"]),
            15: (f"-{pp}8", cash["capex"]), 16: (f"{s}28" if q4 else "0", cash["supply_chain_sale_proceeds"]),
            17: (f"SUM({c}15:{c}16)", cash["cash_from_investing"]),
            20: (f"-{d}8", cash["note_principal_paid"]), 21: (f"-{d}16", cash["finance_lease_principal_paid"]),
            22: (f"{d}9", cash["note_issuance_proceeds"]),
            23: (f"-{s}29" if q4 else f"-({pb}32+{is_}19*{s}11*3)", cash["dividends_paid"]),
            24: (f"SUM({c}20:{c}23)", cash["cash_from_financing"]), 26: (f"{pb}7", cash["opening_cash"]),
            27: (f"SUM({c}12,{c}17,{c}24)", cash["net_cash_change"]), 28: (f"SUM({c}26:{c}27)", cash["closing_cash"]),
            31: (f"SUM({c}12,{c}15)", cash["operating_cash_after_capex"]),
            32: (f"{is_}10*(1-{s}10)", cash["nopat"]), 33: (f"-{d}17", -debt.new_finance_leases),
            34: (f"SUM({c}32:{c}33,{c}8,{c}10:{c}11,{c}15)", cash["unlevered_free_cash_flow"]),
        },
        "PP&E": {
            7: (f"{p}11", opening.property.gross),
            8: (f"{s}26-{s}27" if q4 else f"{is_}7*{a}44", -cash["capex"]),
            9: (f"{d}17", debt.new_finance_leases),
            10: (f"{c}7*{s}17" + (f"*{s}30/365" if q4 else ""), support["gross_property_retired"]),
            11: (f"SUM({c}7:{c}9)-{c}10", balance.property.gross), 13: (f"{p}15", opening.property.accumulated_depreciation),
            14: ("Actuals!F14*Actuals!E14/Actuals!D14-D19" if q4 else f"AVERAGE({c}7,{c}11)*{s}46", support["property_depreciation"]),
            15: (f"SUM({c}13:{c}14)-{c}10", balance.property.accumulated_depreciation),
            16: (f"{c}11-{c}15", balance.property.net_property), 18: (f"{p}20", opening.property.intangibles),
            19: (f"{a}47", support["intangible_amortization"]),
            20: (f"{c}18-{c}19", balance.property.intangibles), 22: (f"SUM({c}14,{c}19)", ins["depreciation_and_amortization"]),
        },
        "Debt schedule": {
            7: (f"{p}10", debt.opening_notes), 8: (f"{a}41", debt.note_principal_paid), 9: (f"{s}32", debt.new_notes),
            10: (f"{c}7-{c}8+{c}9", debt.closing_notes),
            11: (f"AVERAGE({c}7,{c}10)*{s}8*{c}22", debt.note_interest),
            13: (f"{p}18", debt.opening_finance_leases), 14: (f"{a}42", debt.finance_lease_interest + debt.finance_lease_principal_paid),
            15: (f"{s}9*{c}22*({c}13+({c}17-{c}14)/2)/(1-{s}9*{c}22/2)", debt.finance_lease_interest),
            16: (f"{c}14-{c}15", debt.finance_lease_principal_paid),
            17: (f"{s}33", debt.new_finance_leases), 18: (f"{c}13-{c}16+{c}17", debt.closing_finance_leases),
            20: (f"SUM({c}10,{c}18)", support["total_financing_debt"]), 21: (f"SUM({c}11,{c}15)", debt.interest),
            22: (f"{s}30/365" if q4 else "1", 92 / 365 if q4 else 1),
        },
    }
    for row, (_, field) in ASSET_FIELDS.items():
        expr = f"{p}{row}"
        if row == 20:
            expr += f"-{cf}11"
        if row == 25 and q4:
            expr = f"IF({cf}16>0,0,{p}25)"
        fx["Balance sheet"][row] = expr, balance.other_assets[field]
    for row, (_, field) in LIABILITY_FIELDS.items():
        expr = f"IF({cf}16>0,0,{p}37)" if row == 37 and q4 else f"{p}{row}"
        fx["Balance sheet"][row] = expr, balance.other_liabilities[field]
    for row in (43, 46, 47):
        fx["Balance sheet"][row] = f"{p}{row}", balance.equity[EQUITY_FIELDS[row][1]]
    nxt = chr(ord(c) + 1)
    if support["estimated_current_financing_debt"] is not None:
        next_payment = f"Assumptions!{nxt}42"
        fx["Debt schedule"].update({
            24: (f"Assumptions!{nxt}41", support["scheduled_note_principal_next_year"]),
            25: (f"{next_payment}-{s}9*({c}18-{next_payment}/2)/(1-{s}9/2)", support["estimated_finance_lease_principal_next_year"]),
            26: (f"SUM({c}24:{c}25)", support["estimated_current_financing_debt"]),
            27: (f"{c}20-{c}26", support["estimated_noncurrent_financing_debt"]),
        })
        fx["Balance sheet"].update({
            40: (f"{d}26", support["estimated_current_financing_debt"]),
            41: (f"{d}27", support["estimated_noncurrent_financing_debt"]),
        })
    for sheet, rows in fx.items():
        for row, (formula, value) in rows.items():
            fmt = PCT if (sheet, row) in (("Income statement", 11), ("Debt schedule", 22)) else USD if (sheet, row) == ("Income statement", 21) else M
            plan.calc(sheet, f"{c}{row}", formula, value, fmt=fmt)
    q4_equity = f"+{cf}16-{pb}25+{pb}37" if q4 else ""
    checks = {
        7: f"{b}26-{b}49", 8: f"{b}7-{cf}26-{cf}27",
        9: f"{d}20-SUM({d}7,{d}13,{cf}20:{cf}22,{d}17)",
        10: f"{pp}16-'PP&E'!{p}16-{pp}8-{pp}9+{pp}14",
        11: f"{cf}34-({cf}31+{cf}32-{cf}7-{cf}9+{cf}33)",
        12: f"{b}45-({pb}45+{is_}17-{is_}19*{s}11*{1 if q4 else 4}{q4_equity})",
        13: f"{is_}7-Revenue!{c}{10 if q4 else 13}",
    }
    if q4:
        checks.update({15: f"{cf}12-{s}48", 16: f"{is_}17-{s}19",
                       17: f"{cf}28-(DCF!B35+({cf}31+SUM({cf}20:{cf}22))*(1-DCF!B30))"})
    for row, expr in checks.items():
        plan.calc("Checks", f"{c}{row}", expr, 0, kind="check", fmt="0.00")


def add_fdx_forecast(plan, inputs_dir, as_of, current, statements_by_case, selected):
    """Add the case assumptions, historical source view, and single active forecast build."""
    names = ("actuals", "operations", "forward-operations", "financing", "statement-assumptions")
    docs = {name: _dated_inputs(inputs_dir, name, as_of) for name in names}
    report = statements_by_case[selected]
    _actuals(plan, docs["actuals"])
    _assumptions(plan, docs, current, report, selected)
    _revenue(plan, docs, current, report, selected)
    _period_headers(plan, selected)
    opening = report["opening_september_balance"]
    _history_links(plan, docs["actuals"], opening)
    for col, prior, period in zip("D" + FORECAST, "CD" + FORECAST[:-1], (report["q4"], *report["years"]), strict=True):
        _forecast_period(plan, col, prior, period, opening, q4=col == "D")
        opening = period.balance_sheet

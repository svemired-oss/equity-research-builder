"""Editable finite-life mine production and unlevered cash schedules for Newmont."""

from math import fsum

from openpyxl.utils.cell import get_column_letter

from .fdx_current import _num
from .fdx_statements import PCT, USD
from .nem_current import BASE_METALS, METALS, _million_quantity, _value
from .nem_forward_statements import FY27
from .nem_nav import _annual_pacing, _long_cash, _owned_fraction, _prices_for_year, _rate, _sustaining_allocations, _working_net


PLAN = "Mine plan"
CASH = "Mine cash"
NUMBER = '#,##0.00;(#,##0.00);"-"'
VOLUME_UNITS = {"gold": "koz", "silver": "Moz", **dict.fromkeys(BASE_METALS, "kt")}
DETAIL_ROWS = {
    "base_sales": "Cash sales before case shocks",
    "sales": "Cash sales",
    "baseline_cas": "CAS at FY2027 unit costs",
    "base_cas_with_case": "CAS after inflation and cost shock",
    "royalty": "Incremental gold-linked royalties and taxes",
    "cas": "Total CAS",
    "opening_tax_basis": "Opening depreciation basis",
    "depreciation": "Depreciation",
    "closing_tax_basis": "Closing depreciation basis",
    "base_ebit": "EBIT before case shocks",
    "ebit": "EBIT",
    "tax": "Cash income and mining tax",
    "nopat": "Operating profit after tax",
    "sustaining": "Sustaining capital",
    "development": "Development capital",
    "working_capital": "Closing trade working capital",
    "cash_release": "Change in trade working capital, cash impact",
    "fcff": "Unlevered mine cash flow",
}
TOTAL_ROWS = {
    6: ("Cash sales", "sales"),
    7: ("Costs applicable to sales", "cas"),
    8: ("Depreciation", "depreciation"),
    9: ("EBIT", "ebit"),
    10: ("Cash income and mining tax", "tax"),
    11: ("Operating profit after tax", "nopat"),
    12: ("Sustaining capital", "sustaining"),
    13: ("Development capital", "development"),
    14: ("Trade working capital, cash impact", "cash_release"),
    15: ("Unlevered mine cash flow", "fcff"),
}
PRICE_ROWS = {
    metal: (21 + 2 * index, 22 + 2 * index)
    for index, metal in enumerate(METALS)
}


def mine_cash_rows(nav):
    """Return the annual mine cash row addresses used by the NAV sensitivity."""
    rows = {}
    start = 34
    for site, reserve_plan in nav["operating_mine_plans"].items():
        labels = tuple(f"{metal}_quantity" for metal in reserve_plan["reserve_controls"]) + tuple(DETAIL_ROWS)
        rows[site] = {label: start + offset for offset, label in enumerate(labels, 1)}
        start += len(labels) + 2
    return rows


def _operations_rows(operations):
    rows = {}
    start = 35
    for site, mine in operations.items():
        for metal in mine["volumes"]:
            rows[site, metal] = start
            start += 8
    return rows


def _plan_controls(plan, statements, prices, valuation, capital):
    operating = statements["operating_forecast"]
    period = statements["periods"][FY27]
    capex = statements["capital_schedule"]["periods"][FY27]
    income = period.income_statement
    working = _working_net(period.inventory_and_working_capital["closing"])
    stream = prices["silver_stream"]
    gold = operating["modeled_prices"][FY27]["gold"]
    corporate = -fsum(income[key] for key in ("exploration", "advanced_projects", "general_administrative", "other_expense_net"))
    plan.set(PLAN, "A1", "MINE PRODUCTION AND VALUATION INPUTS", "title")
    plan.set(PLAN, "A2", "Volume plans use the consolidation basis for mine cash. Sources and ownership appear by mine.", "subtitle")
    rows = {
        5: ("Pounds per metric tonne", prices["pounds_per_metric_tonne"], "'Assumptions'!B26", NUMBER),
        6: ("Gold-linked royalty and production-tax slope", valuation["royalty_slope"], "'Assumptions'!B25", PCT),
        7: ("Cash income and mining tax proxy", income["effective_tax_rate"], "'Cash flow'!G36", PCT),
        8: ("FY2027 closing mine depreciation basis", capex.closing_ppem_millions, "'PP&E'!G11", USD),
        9: ("FY2027 sustaining cash capex", capex.sustaining_cash_capex_millions, "'PP&E'!G7", USD),
        10: ("FY2027 trade working capital", working, "'Working capital'!G15", USD),
        11: ("FY2027 corporate cash expense basis", corporate, "-SUM('Income statement'!G11:G14)", USD),
        12: ("FY2027 gold gross-to-benchmark ratio", gold.gross_usd / gold.benchmark_usd, "'Prices'!G14/'Prices'!G21", PCT),
        13: ("Silver production subject to stream", stream["share_of_production"], "'Assumptions'!B27", PCT),
        14: ("FY2027 contractual silver cash price ($/oz)", stream["cash_price_usd"], "'Assumptions'!B28", USD),
        15: ("Annual silver stream price inflation cap", stream["annual_inflation_cap"], None, PCT),
        16: ("Annual sustaining cost escalation above inflation", _num(capital["sustaining"], "annual_escalation"), None, PCT),
        17: ("Sustaining allocation weight on 9M2026 spend", _value(capital["sustaining"], "site_weight_blend_9m_2026"), None, PCT),
        18: ("Merian minority economic interest", _value(valuation["merian"], "nci_share"), None, PCT),
    }
    for row, (label, value, expression, fmt) in rows.items():
        plan.set(PLAN, f"A{row}", label)
        if expression is None:
            plan.set(PLAN, f"B{row}", value, "input", fmt)
        else:
            plan.calc(PLAN, f"B{row}", expression, value, fmt=fmt)

    for col, label in zip("ABCDEF", ("Metal", "2028 World Bank", "FY27 source benchmark", "FY27 benchmark",
                                     "FY27 gross realized", "FY27 treatment adjustment"), strict=True):
        plan.set(PLAN, f"{col}20", label, "header")
    for index, metal in enumerate(METALS):
        row = 21 + index
        near = operating["modeled_prices"][FY27][metal]
        historical_benchmark = prices["metals"][metal]["annual_benchmark_2027"]["price_usd"]
        out = _value(valuation["world_bank_2028_prices"], metal)
        plan.set(PLAN, f"A{row}", f"{metal.title()} ({'$/t' if metal in BASE_METALS else '$/oz'} World Bank)")
        plan.set(PLAN, f"B{row}", out, "input", USD)
        plan.set(PLAN, f"C{row}", historical_benchmark, "input", USD)
        plan.calc(PLAN, f"D{row}", f"'Prices'!G{21 + index}", near.benchmark_usd, fmt=USD)
        plan.calc(PLAN, f"E{row}", f"'Prices'!G{14 + index}", near.gross_usd, fmt=USD)
        plan.calc(PLAN, f"F{row}", f"'Prices'!G{42 + index}", near.treatment_usd, fmt=USD)


def _site_plan(plan, nav, statements, life, capital, sustaining, columns):
    operating = statements["operating_forecast"]
    operations = operating["periods"][FY27]["mines"]
    forecast = nav["operating_mine_plans"]
    operations_rows = _operations_rows(operations)
    fy26 = operating["periods"]["fy_2026"]["mines"]
    capex = statements["capital_schedule"]["periods"][FY27]
    evidence = capital["sustaining"]["site_weights"]
    old_total = fsum(_value(row, "reported_fy_2025_millions") for row in evidence.values())
    recent_total = fsum(_value(row, "reported_9m_2026_millions") for row in evidence.values())
    for year, col in columns.items():
        plan.set(PLAN, f"{col}29", year, "header", '0"E"')
    plan.set(PLAN, "A29", "Fiscal year", "header")
    ranges_end = next(reversed(columns.values()))
    next_row = 31
    sites = {}
    for site, mine in forecast.items():
        record = life["sites"][site]
        site_actual = operations[site]
        start = next_row
        end = mine["model_production_end_year"]
        fraction = _owned_fraction(operating["mine_register"][site])
        row = {"site_cas": start + 3, "site_dep": start + 4, "site_sustaining": start + 5, "development": start + 6}
        sites[site] = row
        plan.set(PLAN, f"A{start}", record["name"], "section")
        plan.set(PLAN, f"A{start + 1}", "Last modeled production year")
        plan.set(PLAN, f"B{start + 1}", end, "input", "0")
        plan.set(PLAN, f"C{start + 1}", record["basis_note"], "note")
        plan.set(PLAN, f"A{start + 2}", "Reserve to consolidation fraction")
        plan.set(PLAN, f"B{start + 2}", fraction, "input", PCT)
        plan.set(PLAN, f"C{start + 2}", ", ".join(record["source_ids"]), "note")
        controls = (("site_cas", "FY2027 mine CAS", 4, fsum(x.cas_millions for x in site_actual["economics"].values())),
                    ("site_dep", "FY2027 mine depreciation", 7, fsum(capex.depreciation_by_site_and_metal[site].values())))
        for key, label, offset, expected in controls:
            plan.set(PLAN, f"A{row[key]}", label)
            cells = ",".join(f"'Operations'!G{operations_rows[site, metal] + offset}" for metal in site_actual["volumes"])
            plan.calc(PLAN, f"B{row[key]}", f"SUM({cells})", expected, fmt=USD)
        plan.set(PLAN, f"A{row['site_sustaining']}", "FY2027 allocated sustaining capital")
        plan.set(PLAN, f"C{row['site_sustaining']}", _value(evidence[site], "reported_fy_2025_millions"), "input", USD)
        plan.set(PLAN, f"D{row['site_sustaining']}", _value(evidence[site], "reported_9m_2026_millions"), "input", USD)
        plan.calc(PLAN, f"B{row['site_sustaining']}",
                  f"$B$9*((1-$B$17)*C{row['site_sustaining']}/$B$26+$B$17*D{row['site_sustaining']}/$B$27)",
                  sustaining[site], fmt=USD)
        plan.set(PLAN, f"A{row['development']}", "Development cash capex")
        next_row += 7
        for metal, control in mine["reserve_controls"].items():
            metal_row = next_row
            next_row += 17
            row[metal] = {"plan": metal_row + 14, "check": metal_row + 16}
            plan.set(PLAN, f"A{metal_row}", f"{record['name']} · {metal.title()} ({VOLUME_UNITS[metal]})", "section")
            reserve = record["reserves"][metal]
            explicit = {int(year): entry[metal] for year, entry in record["production_profile"].items() if metal in entry}
            pacing = _annual_pacing(record, metal, set(range(2028, end + 1)) - explicit.keys())
            cap = control["reserve_cap_applied"]
            sold = site_actual["volumes"][metal].sold
            produced = site_actual["volumes"][metal].produced
            near_row = operations_rows[site, metal]
            documented = record.get("documented_lom_recovered", {}).get(metal)
            available = max(0, control["recovered_at_2025_end"] - control["modeled_2026_and_2027_production"])
            requested = {
                year: item["value"] * produced if item["kind"] == "multiplier_2027" else item["value"] / fraction
                for year, item in explicit.items()
            }
            request_total = fsum(requested.values())
            explicit_scale = min(1, available / request_total) if cap and request_total else 1.0
            settings = (
                (1, "Contained reserves, attributable", reserve["contained"], None),
                (2, "Metallurgical recovery", reserve["recovery"], None),
                (3, "Documented recovered amount, attributable", documented, None),
                (4, "Recoverable reserve cap applies (1 = yes)", int(cap), None),
                (5, "Recoverable reserve, consolidation basis", control["recovered_at_2025_end"],
                 f"IF(ISNUMBER(B{metal_row + 3}),B{metal_row + 3},B{metal_row + 1}*B{metal_row + 2})/$B${start + 2}"),
                (6, "FY2026 and FY2027 depletion", fy26[site]["volumes"][metal].produced + produced,
                 f"SUM('Operations'!F{near_row}:G{near_row})"),
                (7, "Opening remaining reserves", available, f"MAX(0,B{metal_row + 5}-B{metal_row + 6})"),
                (8, "FY2027 production", produced, f"'Operations'!G{near_row}"),
                (9, "FY2027 CAS per payable unit", site_actual["economics"][metal].cas_usd_per_unit,
                 f"'Operations'!G{near_row + 4}/('Operations'!G{near_row + 1}*{_quantity_factor(metal, '$B$5')})"),
                (10, "FY2027 depreciation per native volume",
                 capex.depreciation_by_site_and_metal[site][metal] / sold,
                 f"'Operations'!G{near_row + 7}/'Operations'!G{near_row + 1}"),
            )
            for offset, label, value, expression in settings:
                addr = f"B{metal_row + offset}"
                plan.set(PLAN, f"A{metal_row + offset}", label)
                fmt = PCT if offset == 2 else NUMBER
                if expression is not None:
                    plan.calc(PLAN, addr, expression, value, fmt=fmt)
                elif value is not None:
                    plan.set(PLAN, addr, value, "input", fmt)
            if not explicit:
                profile_label = "Editable reserve pacing weight"
            elif len(explicit) < end - 2027:
                profile_label = "Editable technical quantity or pacing weight"
            elif all(item["kind"] == "multiplier_2027" for item in explicit.values()):
                profile_label = "Editable share of FY2027 production"
            else:
                profile_label = "Editable technical quantity, attributable"
            for offset, label in ((11, profile_label),
                                  (12, "Technical quantity, consolidation basis"),
                                  (13, "Active reserve pacing weight"),
                                  (14, "Modeled production"), (15, "Closing recoverable reserves"),
                                  (16, "Reserve overrun, if cap applies")):
                plan.set(PLAN, f"A{metal_row + offset}", label, "total" if offset == 14 else "")
            row[metal]["unit_cas"] = metal_row + 9
            row[metal]["unit_dep"] = metal_row + 10
            tech_range = f"$B${metal_row + 12}:${ranges_end}${metal_row + 12}"
            pace_range = f"$B${metal_row + 13}:${ranges_end}${metal_row + 13}"
            if not cap:
                plan.set(PLAN, f"C{metal_row + 4}", "Existing-asset or leach profiles determine output.", "note")
            plan.set(PLAN, f"C{metal_row + 8}", record.get("profile_note", "Reserve-paced production, limited to FY2027 capacity."), "note")
            cap_flag, opening_reserves, initial_volume = (f"$B${metal_row + offset}" for offset in (4, 7, 8))
            limit = f"$B${start + 1}"
            tech_scale = f"IF(AND({cap_flag}=1,SUM({tech_range})>{opening_reserves}),{opening_reserves}/SUM({tech_range}),1)"
            planned_technical = f"SUM({tech_range})*{tech_scale}"
            previous_remaining = available
            previous_col = None
            for year, col in columns.items():
                item = explicit.get(year)
                raw = f"{col}{metal_row + 11}"
                plan.set(PLAN, raw, item["value"] if item else pacing.get(year, 1.0 if cap else 0.0), "input", NUMBER)
                scale = initial_volume if item and item["kind"] == "multiplier_2027" else f"1/$B${start + 2}"
                tech = f"IF({col}$29<={limit},MAX(0,{raw})*{scale},0)" if item else "0"
                weight = "0" if item or not cap else f"IF({col}$29<={limit},MAX(0,{raw}),0)"
                plan.calc(PLAN, f"{col}{metal_row + 12}", tech, requested.get(year, 0), fmt=NUMBER)
                plan.calc(PLAN, f"{col}{metal_row + 13}", weight, pacing.get(year, 0), fmt=NUMBER)
                remaining = opening_reserves if previous_col is None else f"{previous_col}{metal_row + 15}"
                pace_cell = f"{col}{metal_row + 13}"
                pace = (f"MIN(MAX(0,{opening_reserves}-{planned_technical})*{pace_cell}/SUM({pace_range}),"
                        f"{initial_volume}*{pace_cell}/MAX({pace_range}))")
                amount = f"{col}{metal_row + 12}*{tech_scale}" if item else f"IF({pace_cell}>0,{pace},0)"
                volume = mine["annual"].get(year, {}).get(metal, 0.0)
                plan.calc(PLAN, f"{col}{metal_row + 14}", f"IF({cap_flag}=1,MIN({remaining},{amount}),{amount})", volume,
                          kind="total", fmt=NUMBER)
                if cap:
                    previous_remaining = max(0, previous_remaining - volume)
                plan.calc(PLAN, f"{col}{metal_row + 15}", f"IF({cap_flag}=1,MAX(0,{remaining}-{col}{metal_row + 14}),0)",
                          previous_remaining if cap else 0, fmt=NUMBER)
                planned = requested.get(year, 0) * explicit_scale
                if year in pacing and pacing[year] > 0:
                    planned = min(max(0, available - request_total * explicit_scale) * pacing[year] / fsum(pacing.values()),
                                  produced * pacing[year] / max(pacing.values()))
                near_overrun = f"$B${metal_row + 6}-$B${metal_row + 5}"
                expected_overrun = (max(0, planned - (previous_remaining + volume),
                                        control["near_term_production_above_recovered_reserve"]) if cap else 0)
                plan.calc(PLAN, f"{col}{metal_row + 16}", f"IF({cap_flag}=1,MAX(0,{near_overrun},{amount}-{remaining}),0)",
                          expected_overrun, kind="check", fmt=NUMBER)
                previous_col = col
    for row, col, label, amount in (
        (26, "C", "FY2025 site sustaining allocation basis", old_total),
        (27, "D", "9M2026 site sustaining allocation basis", recent_total),
    ):
        plan.set(PLAN, f"A{row}", label)
        refs = ",".join([f"{col}19", *(f"{col}{site['site_sustaining']}" for site in sites.values())])
        plan.calc(PLAN, f"B{row}", f"SUM({refs})", amount, fmt=USD)
    return sites


def _quantity_factor(metal, pounds):
    if metal in BASE_METALS:
        return f"{pounds}/1000"
    return "0.001" if metal == "gold" else "1"


def _sheet_sum(col, rows):
    return f"SUM({','.join(f'{col}{row}' for row in rows)})"


def _cash_prices(plan, statements, prices, valuation, columns, shocks):
    operating = statements["operating_forecast"]
    inflation = _rate(valuation, "inflation")
    curve = valuation["world_bank_2028_prices"]
    for metal, (active_row, base_row) in PRICE_ROWS.items():
        for row, label in ((active_row, "active case"), (base_row, "before case shocks")):
            plan.set(CASH, f"A{row}", f"{metal.title()} cash realized price ({label})")
    for year, col in columns.items():
        factor = (1 + inflation) ** (year - 2027)
        plan.calc(CASH, f"{col}20", f"(1+'Assumptions'!$B$16)^({col}$4-2027)", factor, fmt=NUMBER)
        active = _prices_for_year(prices, curve, operating, year, inflation, *shocks[:2])
        neutral = _prices_for_year(prices, curve, operating, year, inflation, 0, 0)
        for index, metal in enumerate(METALS):
            row = 21 + index
            bench = f"'{PLAN}'!$B${row}"
            if metal in BASE_METALS:
                bench += f"/'{PLAN}'!$B$5"
            guided = f"{bench}+'{PLAN}'!$D${row}-'{PLAN}'!$C${row}"
            price_escalation = f"{col}$20/(1+'Assumptions'!$B$16)"
            treatment = f"'{PLAN}'!$F${row}*{price_escalation}"
            before = f"'{PLAN}'!$D${row}"
            gross = f"'{PLAN}'!$E${row}"
            for rownum, use_shock in zip(PRICE_ROWS[metal], (True, False), strict=True):
                market = f"({guided})*{price_escalation}"
                if use_shock:
                    shift = " + 'Assumptions'!$B$20" if metal == "gold" else ""
                    coproduct = "*(1+'Assumptions'!$B$21)" if metal != "gold" else ""
                    market = f"({guided}{shift}){coproduct}*{price_escalation}"
                if metal == "silver":
                    stream_share = f"'{PLAN}'!$B$13"
                    stream_price = f"'{PLAN}'!$B$14"
                    escalated = f"{stream_price}*(1+MIN('Assumptions'!$B$16,'{PLAN}'!$B$15))^({col}$4-2027)"
                    formula = (f"{gross}/((1-{stream_share})*{before}+{stream_share}*MIN({before},{stream_price}))"
                               f"*((1-{stream_share})*{market}+{stream_share}*MIN({market},{escalated}))+{treatment}")
                else:
                    formula = f"{market}*{gross}/{before}+{treatment}"
                plan.calc(CASH, f"{col}{rownum}", formula, (active if use_shock else neutral)[metal], fmt=USD)


def _add_site_cash(plan, nav, statements, prices, valuation, columns, sites, flows, detail, shocks):
    operating = statements["operating_forecast"]
    operations = operating["periods"][FY27]["mines"]
    expenses = statements["periods"][FY27].income_statement
    base_capital = statements["capital_schedule"]["periods"][FY27]
    working = _working_net(statements["periods"][FY27].inventory_and_working_capital["closing"])
    tax_rate = expenses["effective_tax_rate"]
    inflation = _rate(valuation, "inflation")
    base_gold = operating["modeled_prices"][FY27]["gold"].net_usd
    pounds = prices["pounds_per_metric_tonne"]
    royalty_slope = valuation["royalty_slope"]
    curve = valuation["world_bank_2028_prices"]
    cas_by_site = {site: fsum(x.cas_millions for x in operations[site]["economics"].values()) for site in sites}
    dep_by_site = {site: fsum(base_capital.depreciation_by_site_and_metal[site].values()) for site in sites}
    portfolio_cas = fsum(cas_by_site.values())
    portfolio_dep = fsum(dep_by_site.values())
    cash_rows = mine_cash_rows(nav)
    for row, label, keys, value in (
        (32, "FY2027 site CAS basis", "site_cas", portfolio_cas),
        (33, "FY2027 site depreciation basis", "site_dep", portfolio_dep),
    ):
        plan.set(CASH, f"A{row}", label)
        refs = ",".join(f"'{PLAN}'!B{source[keys]}" for source in sites.values())
        plan.calc(CASH, f"B{row}", f"SUM({refs})", value, fmt=USD)
    previous_col = None
    prior_basis = {site: base_capital.closing_ppem_millions * dep_by_site[site] / portfolio_dep for site in sites}
    prior_working = {site: working * cas_by_site[site] / portfolio_cas for site in sites}
    for site, rows in cash_rows.items():
        plan.set(CASH, f"A{min(rows.values()) - 1}", operating["mine_register"][site]["name"], "section")
        for metal in operations[site]["economics"]:
            plan.set(CASH, f"A{rows[f'{metal}_quantity']}", f"{metal.title()} produced and sold ({VOLUME_UNITS[metal]})")
        for key, label in DETAIL_ROWS.items():
            plan.set(CASH, f"A{rows[key]}", label, "total" if key == "fcff" else "")

    for year, col in columns.items():
        factor = (1 + inflation) ** (year - 2027)
        market = _prices_for_year(prices, curve, operating, year, inflation, *shocks[:2])
        neutral = _prices_for_year(prices, curve, operating, year, inflation, 0, 0)
        totals = dict.fromkeys(DETAIL_ROWS, 0.0)
        for site, rows in cash_rows.items():
            actual = detail[site][year]
            drivers = sites[site]
            commodity_cash = operations[site]["economics"]
            base_cost = fsum(line.cas_usd_per_unit * _million_quantity(metal, actual["production_and_sales"].get(metal, 0), pounds)
                             for metal, line in commodity_cash.items())
            base_sales = fsum(neutral[metal] * _million_quantity(metal, actual["production_and_sales"].get(metal, 0), pounds)
                              for metal in commodity_cash)
            royalty = (market["gold"] - base_gold * factor) * royalty_slope * actual["production_and_sales"].get("gold", 0) / 1000
            base_royalty = (neutral["gold"] - base_gold * factor) * royalty_slope * actual["production_and_sales"].get("gold", 0) / 1000
            references = {}
            for metal in commodity_cash:
                quantity_cell = f"{col}{rows[f'{metal}_quantity']}"
                plan.calc(CASH, quantity_cell, f"'{PLAN}'!{col}{drivers[metal]['plan']}",
                          actual["production_and_sales"].get(metal, 0), fmt=NUMBER)
                references[metal] = (quantity_cell, _quantity_factor(metal, f"'{PLAN}'!$B$5"))
            sales = "+".join(f"{cell}*{conversion}*{col}${PRICE_ROWS[metal][0]}" for metal, (cell, conversion) in references.items())
            before_sales = "+".join(f"{cell}*{conversion}*{col}${PRICE_ROWS[metal][1]}" for metal, (cell, conversion) in references.items())
            cas_formula = "+".join(f"{cell}*{conversion}*'{PLAN}'!$B${drivers[metal]['unit_cas']}"
                                   for metal, (cell, conversion) in references.items())
            depreciation = "+".join(f"{cell}*'{PLAN}'!$B${drivers[metal]['unit_dep']}" for metal, (cell, _) in references.items())
            cell = {key: f"{col}{row}" for key, row in rows.items()}
            opening = prior_basis[site]
            ending = opening + actual["sustaining_cash_capex_millions"] + actual["development_cash_capex_millions"] - actual["depreciation_proxy_millions"]
            ebit = actual["cash_sales_millions"] - actual["cas_millions"] - actual["depreciation_proxy_millions"]
            tax = max(0, ebit) * tax_rate
            close_working = prior_working[site] - actual["working_capital_release_millions"]
            initial_basis = f"'{PLAN}'!$B$8*'{PLAN}'!$B${drivers['site_dep']}/$B$33"
            initial_working = f"'{PLAN}'!$B$10*'{PLAN}'!$B${drivers['site_cas']}/$B$32"
            prior = lambda key, initial: f"{previous_col}{rows[key]}" if previous_col else initial
            capex = actual["development_cash_capex_millions"]
            plan.set(PLAN, f"{col}{drivers['development']}", capex, "input", USD)
            values = {
                "base_sales": (before_sales, base_sales),
                "sales": (sales, actual["cash_sales_millions"]),
                "baseline_cas": (cas_formula, base_cost),
                "base_cas_with_case": (f"{cell['baseline_cas']}*{col}$20*(1+'Assumptions'!$B$22)", base_cost * factor * (1 + shocks[2])),
                "royalty": (f"({col}$21-'Prices'!$G$7*{col}$20)*'{PLAN}'!$B$6*{cell['gold_quantity']}/1000", royalty),
                "cas": (f"{cell['base_cas_with_case']}+{cell['royalty']}", actual["cas_millions"]),
                "opening_tax_basis": (prior("closing_tax_basis", initial_basis), opening),
                "depreciation": (f"MIN({depreciation},SUM({cell['opening_tax_basis']},{cell['sustaining']},{cell['development']}))",
                                 actual["depreciation_proxy_millions"]),
                "closing_tax_basis": (f"SUM({cell['opening_tax_basis']},{cell['sustaining']},{cell['development']})-{cell['depreciation']}", ending),
                "base_ebit": (f"{cell['base_sales']}-{cell['baseline_cas']}*{col}$20"
                              f"-({col}$22-'Prices'!$G$7*{col}$20)*'{PLAN}'!$B$6*{cell['gold_quantity']}/1000-{cell['depreciation']}",
                              base_sales - base_cost * factor - base_royalty - actual["depreciation_proxy_millions"]),
                "ebit": (f"{cell['sales']}-{cell['cas']}-{cell['depreciation']}", ebit),
                "tax": (f"MAX(0,{cell['ebit']})*'{PLAN}'!$B$7", tax),
                "nopat": (f"{cell['ebit']}-{cell['tax']}", ebit - tax),
                "sustaining": (f"'{PLAN}'!$B${drivers['site_sustaining']}*{cell['baseline_cas']}/'{PLAN}'!$B${drivers['site_cas']}*{col}$20"
                               f"*(1+'{PLAN}'!$B$16)^({col}$4-2027)", actual["sustaining_cash_capex_millions"]),
                "development": (f"'{PLAN}'!{col}{drivers['development']}", capex),
                "working_capital": (f"'{PLAN}'!$B$10*{cell['baseline_cas']}/$B$32*{col}$20", close_working),
                "cash_release": (f"{prior('working_capital', initial_working)}-{cell['working_capital']}", actual["working_capital_release_millions"]),
                "fcff": (f"SUM({cell['nopat']},{cell['depreciation']},{cell['cash_release']})-{cell['sustaining']}-{cell['development']}",
                         actual["core_cash_millions"]),
            }
            for key, (expression, value) in values.items():
                plan.calc(CASH, cell[key], expression, value, kind="total" if key == "fcff" else "", fmt=USD)
                totals[key] += value
            prior_basis[site] = ending
            prior_working[site] = close_working
        for row, (_, key) in TOTAL_ROWS.items():
            plan.calc(CASH, f"{col}{row}", _sheet_sum(col, [rows[key] for rows in cash_rows.values()]),
                      totals[key], kind="total" if row == 15 else "", fmt=USD)
        caps = [f"'{PLAN}'!{col}{metal['check']}" for driver in sites.values() for metal in driver.values() if isinstance(metal, dict)]
        max_overrun = max(plan.expected[PLAN][ref.split("!")[-1]] for ref in caps)
        plan.calc(CASH, f"{col}16", f"MAX({','.join(caps)})", max_overrun, kind="check", fmt=NUMBER)
        plan.calc(CASH, f"{col}17", f"{col}{cash_rows['merian']['fcff']}*'{PLAN}'!$B$18",
                  flows[year].merian_minority_cash_millions, fmt=USD)
        site_cas = _sheet_sum(col, [rows["baseline_cas"] for rows in cash_rows.values()])
        plan.calc(CASH, f"{col}18", f"({site_cas}/$B$32)*{col}$20*('{PLAN}'!$B$11*(1-'{PLAN}'!$B$7)+'{PLAN}'!$B$19)",
                  flows[year].corporate_cash_millions, fmt=USD)
        previous_col = col


def add_nem_mines(plan, nav, statements, life, capital, prices, valuation, *, case):
    """Add the linked Mine plan and Mine cash worksheets to a WorkbookPlan."""
    selected = nav["scenarios"][case]
    shocks = tuple(selected[key] for key in ("gold_shift_usd", "coproduct_price_shift", "site_cost_shift"))
    reserve_plans = nav["operating_mine_plans"]
    sustaining = _sustaining_allocations(capital, statements, reserve_plans, [])
    annual_flows, detail = _long_cash(statements, reserve_plans, capital, valuation, prices, sustaining,
                                      nav["closure_payments_from_september"], valuation["royalty_slope"], *shocks)
    flows = {entry.date.year: entry for entry in annual_flows}
    columns = {year: get_column_letter(index) for index, year in enumerate(flows, 2)}
    _plan_controls(plan, statements, prices, valuation, capital)
    sources = capital["sustaining"]["site_weights"]["corporate_and_other"]
    plan.set(PLAN, "A19", "FY2027 allocated corporate sustaining capital")
    plan.set(PLAN, "C19", _value(sources, "reported_fy_2025_millions"), "input", USD)
    plan.set(PLAN, "D19", _value(sources, "reported_9m_2026_millions"), "input", USD)
    plan.calc(PLAN, "B19", "$B$9*((1-$B$17)*C19/$B$26+$B$17*D19/$B$27)", sustaining["corporate_and_other"], fmt=USD)
    sites = _site_plan(plan, nav, statements, life, capital, sustaining, columns)
    plan.set(CASH, "A1", "MINE CASH FLOW", "title")
    plan.set(CASH, "A2", "USD millions except volumes and prices. Operating mine cash excludes corporate and pooled closure costs.", "subtitle")
    plan.set(CASH, "A4", "Fiscal year", "header")
    for year, col in columns.items():
        plan.calc(CASH, f"{col}4", f"'{PLAN}'!{col}29", year, kind="header", fmt='0"E"')
    for row, (label, _) in TOTAL_ROWS.items():
        plan.set(CASH, f"A{row}", label, "total" if row == 15 else "")
    for row, label in ((16, "Maximum reserve overrun (native units)"), (17, "Merian minority cash"),
                       (18, "Corporate operating and sustaining cash"), (20, "Nominal escalation since 2027")):
        plan.set(CASH, f"A{row}", label)
    _cash_prices(plan, statements, prices, valuation, columns, shocks)
    _add_site_cash(plan, nav, statements, prices, valuation, columns, sites, flows, detail, shocks)

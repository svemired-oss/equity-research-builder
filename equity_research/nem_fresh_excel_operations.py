"""Translate Newmont's physical production and price drivers into Excel formulas."""

from .fdx_statements import PCT, USD
from .nem_current import METALS, _million_quantity


def labels(plan, sheet, entries, *, totals=()):
    for row, label in entries.items():
        plan.set(sheet, f"A{row}", label, "total" if row in totals else "")


def sheet_title(plan, sheet, title, subtitle=None):
    plan.set(sheet, "A1", title, "title")
    if subtitle:
        plan.set(sheet, "A2", subtitle, "subtitle")


def periods(plan, sheet, case):
    for col, value in zip("BDEFG", ("FY2025A", "9M2026A", "Q4 2026E", "FY2026E", "FY2027E"), strict=True):
        plan.set(sheet, f"{col}4", value, "header")
    plan.set(sheet, "F3", "Selected case", "note")
    plan.calc(sheet, "G3", "Cover!B5", case.title())


def add_nem_operations(plan, statements, production, prices, costs, scenario, *, case):
    op = statements["operating_forecast"]
    result = op["periods"]
    capital = statements["capital_schedule"]["periods"]
    shifts = (scenario["gold_shift_usd"], scenario["coproduct_price_shift"], scenario["site_cost_shift"])
    pounds = prices["pounds_per_metric_tonne"]
    stream = prices["silver_stream"]
    price_rows = {metal: index for index, metal in enumerate(METALS)}
    sheet_title(plan, "Prices", "Commodity prices", "Gold and silver in USD per ounce. Copper, lead and zinc in USD per pound.")
    periods(plan, "Prices", case)
    for i, metal in enumerate(METALS):
        for base, title in ((7, "Unshocked net realized"), (14, "Unshocked gross realized"), (21, "Unshocked benchmark"),
                            (28, "Historical gross realization factor"), (35, "Noncash stream amortization"),
                            (42, "Treatment and refining"), (49, "Active net realized"), (56, "Active benchmark")):
            plan.set("Prices", f"A{base+i}", f"{metal.title()} {title.lower()}")
        raw = prices["metals"][metal]
        for col, key in (("E", "q4_2026"), ("G", "fy_2027")):
            p = op["modeled_prices"][key][metal]
            basis = f"{col}{21+i}"
            anchor = raw["q3_benchmark"]["price_usd"]
            historical_basis = anchor
            if metal == "silver":
                basis = f"({basis}*(1-Assumptions!$B$27)+Assumptions!$B$27*MIN({basis},Assumptions!$B$28))"
                historical_basis = anchor * (1 - stream["share_of_production"]) + stream["share_of_production"] * min(anchor, stream["cash_price_usd"])
            factor = raw["q3_2026"]["gross_usd"] / historical_basis
            active = p.benchmark_usd + shifts[0] if metal == "gold" else p.benchmark_usd * (1 + shifts[1])
            cash_change = (active - p.benchmark_usd) * factor * (1 - stream["share_of_production"] if metal == "silver" else 1)
            plan.set("Prices", f"{col}{21+i}", p.benchmark_usd, "input", USD)
            plan.set("Prices", f"{col}{28+i}", factor, "actual", PCT)
            plan.set("Prices", f"{col}{35+i}", p.streaming_amortization_usd, "input", USD)
            plan.set("Prices", f"{col}{42+i}", p.treatment_usd, "input", USD)
            plan.calc("Prices", f"{col}{14+i}", f"{basis}*{col}{28+i}", p.gross_usd, fmt=USD)
            plan.calc("Prices", f"{col}{7+i}", f"{col}{14+i}+{col}{35+i}+{col}{42+i}", p.net_usd, fmt=USD)
            expr = f"{col}{21+i}+Assumptions!$B$20" if metal == "gold" else f"{col}{21+i}*(1+Assumptions!$B$21)"
            plan.calc("Prices", f"{col}{56+i}", expr, active, fmt=USD)
            silver = "*(1-Assumptions!$B$27)" if metal == "silver" else ""
            plan.calc("Prices", f"{col}{49+i}", f"{col}{7+i}+({col}{56+i}-{col}{21+i})*{col}{28+i}{silver}", p.net_usd + cash_change, fmt=USD)

    sheet = "Operations"
    sheet_title(plan, sheet, "Mine production, shipments and costs", "Consolidated unless specified. Gold koz, silver Moz and base metals kt. Financials in USD millions.")
    periods(plan, sheet, case)
    labels(plan, sheet, {7: "Attributable gold production (koz)", 8: "Consolidated gold sold (koz)", 9: "Consolidated sales",
                         10: "Costs applicable to sales", 11: "Depreciation and amortization", 12: "Noncash silver stream revenue",
                         13: "Sales before scenario", 14: "CAS before scenario", 15: "Corporate and other CAS",
                         16: "Corporate depreciation", 17: "Implied Q4 gold shipments", 18: "Q4 gold shipment weights",
                         19: "Implied Q4 site CAS", 20: "Q4 site CAS weights", 21: "Implied Q4 site depreciation",
                         22: "Q4 site depreciation weights", 23: "FY2027 Nevada Gold Mines gold change (koz)",
                         24: "FY2026 managed growth site weight (koz)", 25: "FY2026 gold price embedded in cost base"}, totals=(7, 9, 10, 11))
    plan.set(sheet, "A33", "Production and costs by site and metal", "section")
    rows = {(site, metal): 35 + 8 * index for index, (site, metal) in enumerate(
        (site, metal) for site, data in result["fy_2027"]["mines"].items() for metal in data["volumes"])}
    operating = [(key, row) for key, row in rows.items() if key[1] in result["fy_2027"]["mines"][key[0]]["economics"]]
    gold_rows = [row for (site, metal), row in operating if metal == "gold"]
    q4 = result["q4_2026"]
    ytd = result["ytd_2026"]
    growth_sites = production["forward_2027"]["gold_growth_sites"]
    guidance = production["guidance"]["fy_2026"]
    cost_guidance = costs["guidance_2026"]
    q4_capital = capital["q4_2026"]
    q4_weights = q4_da_weights = q4_gold_weights = 0.0
    active_deltas = {}
    for (site, metal), row in rows.items():
        company_site = op["mine_register"][site]["name"]
        summary = result["fy_2027"]["mines"][site]
        op_site = metal in summary["economics"]
        labels(plan, sheet, {row+offset: f"{company_site} · {metal} · {label}" for offset, label in enumerate(
            ("production", "shipments", "attributable", "sales before scenario", "CAS before scenario", "active sales", "active CAS", "D&A"))
            if (op_site and offset != 2) or (metal == "gold" and offset == 2)})
        actuals = ytd["mines"][site]
        actual = actuals["volumes"][metal]
        guidance_site = production["mine_rows"][site]["guidance_2026"][metal]
        historical = op["historical_mine_volumes"]["fy_2025"][site][metal]
        for col, vol in (("B", historical), ("D", actual)):
            for offset, value in enumerate((vol.produced, vol.sold, vol.attributable_gold_produced)):
                if value is not None:
                    plan.set(sheet, f"{col}{row+offset}", value, "actual")
        if op_site:
            line = actuals["economics"][metal]
            hist_da = costs["reported_nine_months"]["sites"][site]["metals"][metal]["depreciation_millions"]
            for offset, amount in ((3, line.sales_millions), (4, line.cas_millions), (7, hist_da)):
                plan.set(sheet, f"D{row+offset}", amount, "actual")
            for offset in (5, 6):
                plan.calc(sheet, f"D{row+offset}", f"D{row+offset-2}", line.sales_millions if offset == 5 else line.cas_millions)
            plan.set(sheet, f"I{row}", guidance_site["produced"], "input")
            qvol = q4["mines"][site]["volumes"][metal]
            raw_weight = qvol.produced * actual.sold / actual.produced
            q4_cost_weight = line.cas_millions * qvol.sold / actual.sold
            q4_da_weight = hist_da * qvol.sold / actual.sold
            q4_weights += q4_cost_weight
            q4_da_weights += q4_da_weight
            if metal == "gold":
                q4_gold_weights += raw_weight
            plan.calc(sheet, f"I{row+1}", f"E{row}*D{row+1}/D{row}", raw_weight)
            plan.calc(sheet, f"I{row+4}", f"D{row+4}*E{row+1}/D{row+1}", q4_cost_weight)
            plan.calc(sheet, f"I{row+7}", f"D{row+7}*E{row+1}/D{row+1}", q4_da_weight)
        if metal == "gold":
            plan.set(sheet, f"I{row+2}", guidance_site["attributable_produced"], "input")
        for col, key in (("E", "q4_2026"), ("F", "fy_2026"), ("G", "fy_2027")):
            period = result[key]["mines"][site]
            vol = period["volumes"][metal]
            if op_site:
                p = op["modeled_prices"].get(key, {}).get(metal)
                econ = period["economics"][metal]
                output = f"I{row}-D{row}" if col == "E" else f"D{row}+E{row}"
                sold = f"I{row+1}*$E$17/$E$18" if metal == "gold" else f"I{row+1}"
                if col == "F":
                    sold = f"D{row+1}+E{row+1}"
                elif col == "G":
                    output = f"F{row}"
                    if metal == "gold" and site == "ngm":
                        output += "+$G$23"
                    elif metal == "gold" and site in growth_sites:
                        output += f"+($F$7*Assumptions!$B$29-$G$23)*F{row}/$G$24"
                    elif site == "penasquito" and metal in production["forward_2027"]["lower_coproduct_metals"]:
                        output += "*(1+Assumptions!$B$30)"
                    sold = f"F{row+1}*G{row}/F{row}"
                plan.calc(sheet, f"{col}{row}", output, vol.produced)
                plan.calc(sheet, f"{col}{row+1}", sold, vol.sold)
                idx = price_rows[metal]
                unit = f"{col}{row+1}/1000" if metal == "gold" else f"{col}{row+1}" if metal == "silver" else f"{col}{row+1}*Assumptions!$B$26/1000"
                sale = f"{unit}*Prices!{col}{7+idx}" if col != "F" else f"D{row+3}+E{row+3}"
                base_cas = f"I{row+4}*$E$19/$E$20" if col == "E" else f"D{row+4}+E{row+4}"
                da = f"I{row+7}*$E$21/$E$22" if col == "E" else f"D{row+7}+E{row+7}"
                if col == "G":
                    base_cas = f"F{row+4}/F{row+1}*G{row+1}*(1+Assumptions!$B$31)"
                    if metal == "gold":
                        base_cas += f"+(Prices!G7-$G$25)*Assumptions!$B$25*{unit}"
                    da = f"F{row+7}*G{row+1}/F{row+1}"
                plan.calc(sheet, f"{col}{row+3}", sale, econ.sales_millions)
                plan.calc(sheet, f"{col}{row+4}", base_cas, econ.cas_millions)
                plan.calc(sheet, f"{col}{row+7}", da, (capital[key].depreciation_by_site_and_metal[site][metal] if col != "F"
                                                       else hist_da + q4_capital.depreciation_by_site_and_metal[site][metal]))
                sale_delta = cost_delta = 0.0
                if col == "F":
                    sale, cost = f"D{row+5}+E{row+5}", f"D{row+6}+E{row+6}"
                    sale_delta, cost_delta = active_deltas[site, metal, "E"]
                else:
                    sale = f"{unit}*Prices!{col}{49+idx}"
                    cost = f"{col}{row+4}*(1+Assumptions!$B$22)"
                    change = shifts[0] * p.gross_usd / p.benchmark_usd if metal == "gold" else p.gross_usd * shifts[1]
                    if metal == "silver":
                        change = p.benchmark_usd * shifts[1] * (1-stream["share_of_production"]) * p.gross_usd / (
                            p.benchmark_usd*(1-stream["share_of_production"]) + stream["share_of_production"]*min(p.benchmark_usd, stream["cash_price_usd"]))
                    sale_delta = _million_quantity(metal, vol.sold, pounds) * change
                    cost_delta = econ.cas_millions * shifts[2]
                    if metal == "gold":
                        cost += f"+({sale}-{col}{row+3})*Assumptions!$B$25"
                        cost_delta += sale_delta * plan.cells["Assumptions"]["B25"][0]
                active_deltas[site, metal, col] = sale_delta, cost_delta
                plan.calc(sheet, f"{col}{row+5}", sale, econ.sales_millions + sale_delta)
                plan.calc(sheet, f"{col}{row+6}", cost, econ.cas_millions + cost_delta)
            if metal == "gold":
                if col == "E":
                    expr = f"I{row+2}-D{row+2}"
                elif col == "F":
                    expr = f"D{row+2}+E{row+2}"
                elif op_site:
                    expr = f"F{row+2}+G{row}-F{row}"
                else:
                    expr = f"F{row+2}"
                plan.calc(sheet, f"{col}{row+2}", expr, vol.attributable_gold_produced)

    def total(c, addresses):
        return "SUM(" + ",".join(f"{c}{address}" for address in addresses) + ")"

    plan.set(sheet, "I17", guidance["gold"]["sold"], "input")
    plan.set(sheet, "I19", cost_guidance["full_year"]["gross_cas_millions"], "input")
    plan.set(sheet, "I21", cost_guidance["full_year"]["depreciation_millions"], "input")
    plan.set(sheet, "I24", costs["sensitivities"]["gold_price_anchor_usd_per_ounce"], "input", USD)
    ngm = production["forward_2027"]["ngm_operator_outlook"]
    target_ngm = (ngm["low_full_mine_koz"]+ngm["high_full_mine_koz"])/2*op["mine_register"]["ngm"]["ownership"]
    plan.set(sheet, "I23", target_ngm, "input")
    plan.calc(sheet, "G23", f"I23-F{rows['ngm','gold']}", target_ngm-result["fy_2026"]["mines"]["ngm"]["volumes"]["gold"].produced)
    plan.calc(sheet, "G24", total("F", [rows[site,"gold"] for site in growth_sites]),
              sum(result["fy_2026"]["mines"][site]["volumes"]["gold"].produced for site in growth_sites))
    plan.calc(sheet, "G25", f"({total('D',[r+3 for r in gold_rows])}*1000+E17*I24)/I17", op["cost_baseline_gold_price_usd_per_ounce"], fmt=USD)
    for cell, expression, value in (("E17", "I17-D8", q4["metals"]["gold"]["volume"].sold),
                                    ("E18", total("I", [r+1 for r in gold_rows]), q4_gold_weights),
                                    ("E19", "I19-D10", q4["cas_millions"]), ("E20", total("I", [r+4 for _,r in operating]), q4_weights),
                                    ("E21", "I21-D11-E16", q4_capital.depreciation_millions-q4_capital.corporate_depreciation_millions),
                                    ("E22", total("I", [r+7 for _,r in operating]), q4_da_weights)):
        plan.calc(sheet, cell, expression, value)
    plan.set(sheet, "D16", costs["reported_nine_months"]["corporate_and_other"]["depreciation_millions"], "actual")
    plan.calc(sheet, "E16", "D16/3", q4_capital.corporate_depreciation_millions)
    plan.calc(sheet, "F16", "D16+E16", capital["fy_2027"].corporate_depreciation_millions)
    plan.calc(sheet, "G16", "F16", capital["fy_2027"].corporate_depreciation_millions)
    plan.set(sheet, "D15", ytd["corporate_and_other_cas_millions"], "actual")
    plan.calc(sheet, "F15", "D15", ytd["corporate_and_other_cas_millions"])
    for col, key in (("D", "ytd_2026"), ("E", "q4_2026"), ("F", "fy_2026"), ("G", "fy_2027")):
        p = result[key]
        sale_delta = sum(active_deltas.get((*pair,col),(0,0))[0] for pair,_ in operating)
        cost_delta = sum(active_deltas.get((*pair,col),(0,0))[1] for pair,_ in operating)
        for row, expression, value in (
            (7, total(col, [r+2 for (_,metal),r in rows.items() if metal=="gold"]), p["metals"]["gold"]["volume"].attributable_gold_produced),
            (8, total(col, [r+1 for r in gold_rows]), p["metals"]["gold"]["volume"].sold),
            (9, total(col, [r+5 for _,r in operating]), p["sales_millions"]+sale_delta),
            (10, total(col, [r+6 for _,r in operating])+f"+{col}15", p["cas_millions"]+cost_delta),
            (11, total(col, [r+7 for _,r in operating])+f"+{col}16", statements["historical_statements"]["income_statements"]["ytd_2026"]["depreciation_amortization"]*-1 if col=="D"
             else plan.expected[sheet]["D11"]+plan.expected[sheet]["E11"] if col=="F" else capital[key].depreciation_millions),
            (13, total(col, [r+3 for _,r in operating]), p["sales_millions"]),
            (14, total(col, [r+4 for _,r in operating])+f"+{col}15", p["cas_millions"]),
        ):
            plan.calc(sheet, f"{col}{row}", expression, value, kind="total" if row in (7,9,10,11) else "")
        if col == "D":
            plan.set(sheet, "D12", p["noncash_stream_amortization_millions"], "actual")
        else:
            silver_row = rows["penasquito", "silver"]
            expression = "D12+E12" if col=="F" else f"{col}{silver_row+1}*Prices!{col}{35+price_rows['silver']}"
            plan.calc(sheet, f"{col}12", expression, p["noncash_stream_amortization_millions"])
    plan.set(sheet, "I34", "Site target and weights", "header")
    return rows

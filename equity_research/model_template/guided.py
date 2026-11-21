"""Guided input catalog and independent checks for the edited workbook."""
from math import isclose, isfinite, prod
import re

from ..fcff_valuation import DatedFCFF, EquityBridge, TerminalAssumptions, discount_fcff
from ..operating_research import ResearchGap


ASSUMPTIONS = {
    3: ("Margins", .031, .79, "Bounded to a positive recurring operating margin below reported sales."),
    4: ("Margins", .001, .499, "Use a positive effective tax rate below half of pretax earnings."),
    5: ("Capex and working capital", 0, .50, "D&A must be nonnegative and below half of revenue."),
    6: ("Capex and working capital", 0, .60, "Capital investment as a share of revenue."),
    9: ("Terminal", .005, .04, "Gordon growth on final-year FCFE; must stay below the cost of equity and WACC across the sensitivity grid."),
    10: ("Terminal", .05, .50, "Incremental ROIC for forecast reinvestment and the ROIC-based terminal check."),
    13: ("Drivers", 0, 1, "Fraction of the historical growth gap carried forward each year."),
    15: ("Drivers", 0, 0, "Enter positive USD millions within three times the automated anchor and above filed year-to-date sales."),
    16: ("Margins", 0, 1, "Cash dividends from zero to all positive net income."),
    17: ("Margins", 0, .15, "Absolute margin shift. Both scenarios must retain a positive EBIT margin."),
    18: ("Capex and working capital", 350, 380, "Day-count convention for working-capital display. Ratios stay unchanged."),
    20: ("Capex and working capital", 0, 0, "Cumulative annual debt addition on top of the debt / EBITDA target, USD millions. The limit is opening annual revenue."),
    21: ("Capex and working capital", 0, 0, "Cumulative annual debt reduction against the debt / EBITDA target, USD millions. Projected debt cannot turn negative."),
    22: ("Margins", 0, 0, "Annual repurchases in USD millions. Projected outstanding shares must remain positive."),
    26: ("WACC", 0, 1, "Minimum total return for a Buy."),
    27: ("WACC", -1, 0, "Maximum total return for a Sell, below the Buy threshold."),
    29: ("Terminal", 0, 1, "Relative terminal valuation difference above which the cross-check is flagged."),
    30: ("Capex and working capital", .25, 3, "Multiplier on filed operating current assets per dollar of sales."),
    31: ("Capex and working capital", .25, 3, "Multiplier on filed operating current liabilities per dollar of sales."),
    42: ("Margins", 0, 0, "Annual interest income in USD millions, within opening annual revenue."),
    43: ("Margins", 0, 0, "Annual equity-method income in USD millions, within opening annual revenue."),
    44: ("Margins", 0, 0, "Annual other nonoperating income in USD millions, within opening annual revenue."),
    35: ("Drivers", 0, .05, "Bull and bear revenue growth change in every forecast year, as a decimal."),
    37: ("Drivers", -.05, .15, "Nominal revenue growth reached in year 5; years 2-5 fade linearly from the 3-year CAGR."),
}
FORMATS = {"0.00%": "decimal", "0%": "decimal"}


def fields(plan, model):
    """Derive field IDs and provenance from the same inputs written to Excel."""
    result = []
    def add(target, tab, address, label, group, low, high, explanation, value=None, unit=None):
        cell = plan["cells"][tab][address]
        default = cell[0] if value is None else value
        if type(default) not in (int,float):
            return
        source = plan.get("inputSources",{}).get(tab,{}).get(address)
        evidence = plan["cells"]["Sources"] if source else {}
        ref = source[1:] if source else ""
        result.append({"target":target,"label":label,"group":group,"min":low,"max":high,
                       "default":default,"unit":unit or FORMATS.get(cell[2],"number"),
                       "explanation":explanation,"sheet":tab,"cell":address,
                       "basis":evidence.get("D"+ref,[""])[0],"source":evidence.get("C"+ref,[None])[0] or None})

    identifiers={3:"margin",4:"tax",5:"da",6:"capex",9:"growth",10:"roic",13:"convergence",
                 15:"revenue_anchor",16:"payout",17:"margin_shift",18:"wc_days",20:"debt_issued",
                 21:"debt_repaid",22:"buybacks",25:"dcf_weight",26:"buy_threshold",27:"sell_threshold",
                 29:"terminal_gap_threshold",30:"wc_assets",31:"wc_liabilities",42:"interest_income",
                 43:"equity_method_income",44:"other_income",37:"long_run_growth",
                 35:"growth_step"}
    for row,(group,low,high,explanation) in ASSUMPTIONS.items():
        address = f"B{row}"
        cells = plan["cells"]["Assumptions"]
        if address not in cells:
            continue
        default = cells[address][0]
        if row == 15 and type(default) in (int,float):
            low, high = max(.001,model.get("filed_ytd_revenue",0)), default*3
        if row in (20,21,22):
            high = model["revenue"][0] if row!=21 else max(model["debt"],model["revenue"][0])
        if row in (42,43,44):
            low, high = -model["metrics"]["revenue"], model["metrics"]["revenue"]
        unit = "days" if row == 18 else "USD millions" if row in (15,20,21,22,42,43,44) else "decimal"
        add(identifiers[row],"Assumptions",address,cells[f"A{row}"][0],group,low,high,explanation,unit=unit)

    for row,target,label,low,high,explanation,value in (
        (3,"risk_free","Risk-free rate",0,.15,"Treasury yield scenario in decimal form.",model["cost"].risk_free_rate),
        (4,"erp","Equity risk premium",.005,.2,"Positive risk premium above the risk-free rate.",model["cost"].equity_risk_premium),
        (5,"beta","Effective levered beta",0,5,"Override the risk coefficient while preserving the historical beta evidence.",model["cost"].levered_beta),
        (6,"debt_cost","Pretax cost of debt",.001,.35,"Positive debt cost applied to both WACC and the interest schedule.",model["cost"].pretax_cost_of_debt),
    ):
        add(target,"WACC",f"B{row}",label,"WACC",low,high,explanation,value,
            "number" if row==5 else "decimal")
    revenue = plan["cells"]["Revenue Build"]
    for address, (value,kind,fmt) in revenue.items():
        if kind != "input" or address[0] not in "KM" or type(value) not in (float,int):
            continue
        label = revenue["A"+address[1:]][0]
        percentage = "%" in label
        if address.startswith("K"):
            add(f"driver_{address}","Revenue Build",address,label+" | Long-run growth","Drivers",
                -.10 if percentage else -.25,.10 if percentage else .35,
                "Long-run change in a bounded rate. Forecast utilization or conversion cannot exceed 100%." if percentage else
                "Long-run annual growth must allow a positive operating quantity.",unit="decimal")
        else:
            low, high = ((-.75,.75) if "growth" in label else (.001,1)) if percentage else (.001,value*3)
            add(f"driver_{address}","Revenue Build",address,label+" | First forecast year","Drivers",low,high,
                "Guided replacement for the disclosed first-year driver. Quantities and prices must stay positive.",
                unit="decimal" if percentage else "number")
    return result


def apply(plan, model, overrides):
    catalog = {field["target"]:field for field in fields(plan,model)}
    matched = {}
    trail = {"A1":["Changes from automated case","title",None]}
    for column, label in zip("ABCDE",("Input","Automated","Guided","Reason","Workbook input"),strict=True):
        trail[f"{column}3"]=[label,"header",None]
    for row,item in enumerate(overrides,4):
        target,value,reason=(item[key] for key in ("target","value","reason"))
        field=catalog.get(target)
        if (field is None or target in matched or type(value) not in (int,float) or not isfinite(value)
                or not field["min"]<=value<=field["max"] or not isinstance(reason,str)
                or not 1<=len(reason.strip())<=240 or not reason.isprintable() or reason.lstrip().startswith("=")):
            raise ResearchGap(f"Invalid Guided input {target}. {field['explanation'] if field else ''}")
        matched[target]=value
        tab, address = field["sheet"],field["cell"]
        record = plan["cells"][tab][address]
        fmt = "0.00%" if field["unit"]=="decimal" else record[2]
        record[:] = [value,"input",fmt]
        location=f"'{tab}'!{address}"
        for col, entry, style, form in (
            ("A",field["label"],"text",None),("B",field["default"],"text",fmt),
            ("C","="+location,"text",fmt),("D",reason,"note",None),("E",location,"text",None),
        ):
            trail[f"{col}{row}"]=[entry,style,form]
        links = plan["inputSources"].setdefault(tab,{})
        audit = plan["cells"]["Sources"]
        source_row = max(int(re.sub(r"^[A-Z]+","", key)) for key in audit)+1
        audit[f"A{source_row}"]=[f"Guided | {location}","text",None]
        audit[f"B{source_row}"]=["="+location,"text",fmt]
        audit[f"D{source_row}"]=[f"Analyst assumption. {reason}","note",None]
        links[address]=f"A{source_row}"
    if not matched:
        raise ResearchGap("Submit at least one Guided change.")
    effective=lambda key:matched.get(key,catalog[key]["default"])
    if effective("margin_shift")>=effective("margin"):
        raise ResearchGap("Case margin shift must be smaller than the base EBIT margin.")
    if effective("da")>effective("capex"):
        raise ResearchGap("D&A as a share of revenue cannot exceed first-year capex as a share of revenue.")
    if "buy_threshold" in catalog and effective("sell_threshold")>=effective("buy_threshold"):
        raise ResearchGap("The Sell threshold must be below the Buy threshold.")
    plan["cells"]["Guided changes"]=trail
    # Freeze the unaffected checks and recompute all valuation expectations on the revised case.
    original_dcf = plan["expected"]["DCF"]
    plan["expected"]={tab:values for tab,values in plan["expected"].items() if tab in ("Checks","Comps")}
    plan["expected"]["DCF"] = {address:original_dcf[address] for address in ("B6","B8","B12","B105") if address in original_dcf}
    for case in plan["scenarioExpected"]:
        plan["scenarioExpected"][case]={"Checks":plan["scenarioExpected"][case]["Checks"]}
    scenarios = {}
    def validate(cached,case):
        scenarios[case]=verify_recalculation(cached,case,model,matched)
        if case=="Base":
            for name,row in (("Bear",8),("Bull",9)):
                value=cached["Report Evidence"][f"C{row}"].value
                if not isclose(value,scenarios[name],abs_tol=1e-6,rel_tol=1e-10):
                    raise ResearchGap(f"The report {name} case does not match its scenario recalculation.")
    return validate


def verify_recalculation(book,case,model,changes):
    from ..operating_valuation import CostOfCapitalInputs, calculate_cost_of_capital

    def get(tab,cell):
        return book[tab][cell].value
    def equal(tab,cell,expected):
        actual=get(tab,cell)
        if type(expected) in (int,float):
            good=type(actual) in (float,int) and isclose(actual,expected,abs_tol=1e-6,rel_tol=1e-10)
        else:
            good=actual==expected
        if not good:
            raise ResearchGap(f"Guided verification failed at {tab}!{cell}.")
    a=lambda row:get("Assumptions",f"B{row}")
    close=get("Cover","B6")
    dcf=lambda row:get("DCF",f"B{row}")
    equity=close*dcf(12)
    tax=a(4)
    risk,erp,beta,debt_cost=(get("WACC",f"B{i}") for i in (3,4,5,6))
    cost=calculate_cost_of_capital(CostOfCapitalInputs(model["as_of"],risk,erp,beta,equity,debt_cost,dcf(8),tax,0,0))
    equal("WACC","B9",cost.wacc)
    ke=cost.cost_of_equity
    equal("DCF","B101",ke)
    growth,roic=a(9),a(10)
    if growth+.01>=min(cost.wacc-.01,ke-.01,roic):
        raise ResearchGap("Terminal growth must leave at least one percentage point of room on both sides of the "
                          "cost of equity and WACC sensitivity grids.")
    base_margin=a(3)
    sign={"Base":0,"Bear":-1,"Bull":1}[case]
    shift=sign*a(17)
    growth_shift=sign*a(35)
    da,capex,convergence=a(5),a(6),a(13)
    periods=model["dates"]
    stub=model["stub"]
    wc=a(7)*a(30)-a(8)*a(31)
    previous_wc=model["metrics"]["revenue"]*(a(7)-a(8))
    opening_wc=previous_wc
    terminal_capex=da+base_margin*(1-tax)*growth/roic-growth*wc

    # Rebuild revenue from each actual history and the edited driver block.
    driver=model["driver"]
    filed=model["sales"]
    latest=filed.copy()
    recent=model.get("comparable_revenue_interim")
    prior=recent["prior"] if recent else model["old_sales"]
    current=recent["current"] if recent else latest
    rates={k:current[k]/prior[k]-1 for k in latest}
    has_physical=driver["type"]!="segment_growth"
    if has_physical:
        start=2*(4+len(latest))+5
        factors=driver["factors"]
        actuals=[f["history"][-1] for f in factors]
        guide=[changes.get(f"driver_M{start+i}",f.get("guidance")) for i,f in enumerate(factors,1)]
        targets=[changes.get(f"driver_K{start+i}",f["terminal"]) for i,f in enumerate(factors,1)]
        factor_rates=[(f["history"][-1]/f["history"][0])**.5-1 if "%" not in f["label"] else 0 for f in factors]
        stores=driver["type"]=="stores_sales"
        if stores:
            factor_rates[1]=actuals[2]
        used=2 if stores else len(factors)
        other=driver["other"][-1] if driver["other"] else 0
        other_rate=(other/driver["other"][0])**.5-1 if other else 0
    anchor=a(15)
    scale=1
    base_segments=[]
    for i in range(5):
        if has_physical:
            for j in range(used):
                level=actuals[j]*(1+targets[j]+(factor_rates[j]-targets[j])*convergence**(i+1))
                if i==0 and stores and j==1 and guide[2] is not None:
                    level=actuals[j]*(1+guide[2])
                actuals[j]=guide[j] if i==0 and guide[j] is not None else level
                if actuals[j]<=0 or ("%" in factors[j]["label"] and actuals[j]>1):
                    raise ResearchGap(f"Guided {factors[j]['label']} is outside its feasible range.")
            other*=1+growth+(other_rate-growth)*convergence**(i+1)
            raw=prod(actuals[:used])+other
            if i==0 and type(anchor) in (float,int):
                scale=anchor/raw
            mix=sum(filed.values())
            base_segments.append({k:raw*scale*v/mix for k,v in filed.items()})
        else:
            # Year 1 fades halfway (convergence) toward terminal growth; years 2-5 use the Stage 2d fade rows.
            rate=(lambda k:growth+(rates[k]-growth)*convergence) if i==0 else (lambda k,r=a(37+i):r)
            latest={k:v*(1+rate(k)) for k,v in latest.items()}
            if i==0 and type(anchor) in (float,int):
                factor=anchor/sum(latest.values())
                latest={k:v*factor for k,v in latest.items()}
            base_segments.append(dict(latest))
    from ..fcfe_valuation import FCFEInputs, case_revenues, value_fcfe
    revenue_path=case_revenues(base_segments,filed,growth_shift) if growth_shift else [sum(x.values()) for x in base_segments]
    flows=[]
    debt=model["debt"]
    shares=dcf(12)
    payout=[]
    other_income=a(33)
    capex_path=[]
    interest=[]
    net_borrowing=[]
    for i,(column,period) in enumerate(zip("BCDEF",periods,strict=True)):
        sales=revenue_path[i]
        if sales<=0:
            raise ResearchGap("Guided drivers must produce positive forecast sales.")
        equal("Income Statement",column+"4",sales)
        if i==0 and sales<model.get("filed_ytd_revenue",0):
            raise ResearchGap("First-year Guided revenue is below filed year-to-date sales.")
        ebit=sales*(base_margin+shift)
        ratio=capex+(terminal_capex-capex)*i/4
        capex_path.append(ratio)
        investment=sales*ratio
        fcff=ebit*(1-tax)+sales*da-investment-(sales*wc-previous_wc)
        flow=fcff*(stub if i==0 else 1)
        equal("Cash Flow Statement",column+"12",flow)
        flows.append(DatedFCFF(period,flow))
        if i==0:
            debt+=get("Schedules","B40")
            cutoff_debt=debt
        # Closing debt holds debt / EBITDA at the last actual year's level plus any cumulative analyst change.
        closing=a(45)*(ebit+sales*da)+(i+1)*(a(20)-a(21))
        year_interest=debt*debt_cost
        payout.append(max(0,(ebit-year_interest+other_income)*(1-tax))*a(16))
        equal("Schedules",column+"23",payout[-1])
        net_borrowing.append(closing-debt)
        interest.append(year_interest)
        debt=closing
        shares-=a(22)/a(23)
        if debt< -1e-8 or shares<=0:
            raise ResearchGap("Projected debt cannot be negative and common shares must remain positive.")
        equal("Schedules",column+"18",debt)
        equal("Schedules",column+"26",shares)
        previous_wc=sales*wc
    terminal=TerminalAssumptions(ebit*(1-tax),growth,roic)
    bridge=EquityBridge(model["as_of"],dcf(6),dcf(7),dcf(8),dcf(10),dcf(9),0,dcf(12),
                        immaterial_minority_deficit=bool(model.get("immaterial_nci")) and dcf(9)<0)
    result=discount_fcff(flows,model["as_of"],cost.wacc,terminal,bridge)
    equal("DCF","B15",result.value_per_share)
    inputs=FCFEInputs(model["metrics"]["revenue"],opening_wc,wc,tax,da,tuple(capex_path),tuple(interest),other_income,
                      cutoff_debt,debt_cost,tuple(net_borrowing),stub,tuple(p.discount_years for p in result.periods),
                      growth,roic,bridge.adjustment_millions+dcf(8)-dcf(6)-dcf(7),bridge.diluted_shares_millions,
                      a(45),a(20)-a(21))
    fcfe=value_fcfe(inputs,revenue_path,base_margin+shift,ke)
    equal("DCF","B123",fcfe.value_per_share)
    equal("Cover","B7",fcfe.value_per_share)
    if case=="Base":
        from dataclasses import replace
        from ..valuation_summary import implied_growth
        discount=[(1+ke)**-period.discount_years for period in result.periods]
        terminal_slope=revenue_path[-1]*(1+growth)/(ke-growth)*discount[-1]
        annual_slope=sum(sales*factor*(stub if i==0 else 1) for i,(sales,factor) in enumerate(
            zip(revenue_path,discount,strict=True)))
        sensitivity=(1-tax)*(annual_slope+terminal_slope)/inputs.shares
        equal("DCF","B36",base_margin+(close-fcfe.value_per_share)/sensitivity)
        for i in range(5):
            for j,col in enumerate("BCDEF"):
                fair=value_fcfe(inputs,revenue_path,base_margin,ke+(i-2)*.005,growth+(j-2)*.0025)
                equal("Sensitivity",f"{col}{28+i}",fair.value_per_share)
        if "valuation_summary" in model:
            revised={**model,"fcfe_inputs":inputs,"cost_of_equity":ke,"margin":base_margin,"revenue":revenue_path}
            implied=implied_growth(revised)
            equal("DCF","B38",implied if implied is not None else "Unavailable")
            multiple=model["valuation_comps"]["summary"].get("EV / EBITDA") if model["valuation_comps"] else None
            if multiple:
                exit_value=revenue_path[-1]*(base_margin+da)*multiple["quartiles"][1]
                gap=abs(exit_value/result.terminal_value_millions-1)
                equal("DCF","B88",exit_value)
                equal("DCF","B89",gap)
                equal("DCF","B90","Review" if gap>a(29) else "Within threshold")
                exit_fair=(result.enterprise_value_millions-result.terminal_present_value_millions+
                           exit_value*(1+cost.wacc)**-result.periods[-1].discount_years
                           +bridge.adjustment_millions)/bridge.diluted_shares_millions
                equal("DCF","B91",exit_fair)
    if "valuation_summary" in model:
        equal("Cover","B14",result.value_per_share)
        dividend=(payout[0]*stub+payout[1]*(1-stub))/bridge.diluted_shares_millions
        projected=bridge.diluted_shares_millions-a(22)/a(23)
        target=(fcfe.equity_value*(1+ke)-dividend*bridge.diluted_shares_millions-a(22))/projected
        total=(target+dividend)/close-1
        equal("Cover","B15",dividend)
        equal("Cover","B16",target)
        equal("Cover","B17",total)
        equal("Cover","B9","Buy" if total>=a(26) else "Sell" if total<=a(27) else "Hold")
    return fcfe.value_per_share

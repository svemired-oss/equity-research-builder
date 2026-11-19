"""Editable Automotive and GM Financial models, combined on one filed balance date."""

from .gm_valuation import value_gm
from .specialty_export import PCT, PRICE, _Workbook, _export, _sources


_LIMITS = ("This scenario uses September closing balances and subsequently published guidance. Both businesses use the same "
           "equity date and September parent share count. Subsequent share repurchases, current market valuation, and eligible "
           "peer valuations require separate review. The lender capital ratio is an economic proxy.")


def export_gm(split, assumptions, treasury, folder, node, modules):
    v = value_gm(split, assumptions)
    a, f = v.automotive, v.finance
    e = split["finance_equity"]
    x = split["automotive_release"]
    b = x["balance"]
    r = split["automotive_revenue_drivers"]
    units = split["automotive_wholesale"]
    w = _Workbook("General Motors Company", a.as_of, "Automotive and GM Financial", "USD millions, except per share")
    w.column("Cover", "A", 7, ("Scenario value per common share", "Automotive equity", "GM Financial common equity",
                               "Combined common equity", "Filed period-end parent shares (millions)"))
    w.column("Cover", "B", 7, (("=B10/B11", "total", PRICE), "=SUM(Forecast!B15:G15)+DCF!B7+DCF!B8",
                               "=Lender!B17", "=SUM(B8:B9)", "=Actuals!B23"))
    w.column("Cover", "A", 15, ((_LIMITS, "note", None),))
    w.check("Cover", B7=v.value_per_share, B8=a.equity_value_millions, B9=f.common_equity_value, B10=v.common_equity_millions)
    w.column("Assumptions", "A", 1, ("Automotive and lender assumptions", f"Treasury observation {treasury['date']}", "", "Automotive premium",
                                     "Treasury yield", "Automotive WACC", "Lender premium", "Lender cost of equity", "Terminal growth",
                                     "Terminal marginal Automotive ROIC", "Long-run unit growth", "Required Automotive liquidity / sales",
                                     "Tax rate"))
    rates = (assumptions.wacc-treasury["rate"], treasury["rate"], "=SUM(B4:B5)", assumptions.finance_cost_of_equity-treasury["rate"],
             "=B5+B7", assumptions.terminal_growth, assumptions.terminal_roic, assumptions.terminal_volume_growth,
             assumptions.liquidity_share_of_revenue, assumptions.tax_rate)
    w.column("Assumptions", "B", 4, tuple((value, "text" if row in (5, 6, 8) else "input", PCT)
                                          for row, value in enumerate(rates, 4)))
    w.column("Actuals", "A", 1, ("Filed auto operating inputs", "USD millions and thousands of vehicles", "", "Wholesale units",
                                 "Vehicles and parts sales", "Used vehicle sales", "Services and other sales", "Total Automotive sales",
                                 "Adjusted Automotive EBIT YTD", "Automotive D&A YTD", "Operating cash flow YTD", "Capex YTD",
                                 "OCF guidance low", "OCF guidance high", "Capex guidance low", "Capex guidance high",
                                 "Prior Q4 net interest", "Cash and marketable securities", "Sector debt", "Pensions and postretirement",
                                 "Noncash operating current assets", "Nondebt current liabilities", "Ending GM common shares (millions)",
                                 "Current debt elimination", "Consolidated Automotive debt", "GM Financial debt"))
    products = ("vehicles_and_parts", "used_vehicles", "services_and_other")
    annual_net = x["annual_interest_expense_millions"]-x["annual_interest_income_millions"]
    prior_ytd_net = x["prior_ytd_interest_expense_millions"]-x["prior_ytd_interest_income_millions"]
    w.column("Actuals", "B", 3, ("Trailing sales / guidance / balance", units["trailing"], *(r["trailing"][p] for p in products),
                                 "=SUM(B5:B7)", "", "", "", "", *x["annual_ocf_guidance_millions"], *x["annual_capex_guidance_millions"],
                                 annual_net-prior_ytd_net, b["cash"]+b["securities"], b["current_debt"]+b["long_debt"],
                                 b["pensions"]+b["postretirement"], b["current_assets"]-b["cash"]-b["securities"],
                                 b["current_liabilities"]-b["current_debt"], x["ending_parent_shares_millions"],
                                 b["current_debt_eliminations"], split["debt"]["automotive_debt_millions"],
                                 split["debt"]["finance_debt_millions"]))
    w.column("Actuals", "C", 3, ("Current YTD", units["current_ytd"], *(r["current_ytd"][p] for p in products), "=SUM(C5:C7)",
                                 x["ytd_adjusted_auto_ebit_millions"], x["ytd_da_millions"], x["ytd_ocf_millions"], x["ytd_capex_millions"]))
    w.column("Actuals", "D", 3, ("Prior YTD", units["prior_ytd"], *(r["prior_ytd"][p] for p in products), "=SUM(D5:D7)"))
    w.column("Actuals", "A", 28, ("Historical consolidated revenue", "Period end", "Automotive", "GM Financial", "Consolidated"))
    for col, line in zip("BCD", split["revenue"], strict=True):
        w.column("Actuals", col, 29, (line["end"], line["automotive_revenue_millions"], line["finance_revenue_millions"], line["total_millions"]))
    w.column("Actuals", "A", 34, ("Lender filing inputs", "Net receivables", "Net leased vehicles", "Common capital proxy",
                                  "Trailing common earnings", "Preferred liquidation claim"))
    w.column("Actuals", "B", 35, (e["earning_assets"]["net_receivables_millions"], e["earning_assets"]["leased_vehicles_millions"],
                                  e["common_capital_proxy_millions"], e["trailing_common_earnings_millions"], e["preferred_liquidation_millions"]))
    w.column("Actuals", "C", 35, (e["prior_earning_assets"]["net_receivables_millions"], e["prior_earning_assets"]["leased_vehicles_millions"]))
    w.column("Actuals", "A", 41, ("Automotive forecast ratios", "Adjusted EBIT / sales", "D&A / sales", "Working capital / sales",
                                  "Current capex / trailing sales", "Normalized terminal capex / sales"))
    w.column("Actuals", "B", 42, ("=C9/C8", "=C10/C8", "=(B21-B22)/B8", "=AVERAGE(B15:B16)/B8",
                                  "=B43+B42*(1-Assumptions!B13)*Assumptions!B9/Assumptions!B10-B44*Assumptions!B9"))
    w.column("Forecast", "A", 1, ("Automotive forecast", "USD millions", "Period", "Wholesale vehicles, thousands", "Vehicles and parts per unit",
                                   "Used vehicles", "Services and other", "Revenue", "EBIT", "D&A", "Capex",
                                   "Working capital investment", "Unlevered cash flow", "ACT/365 years", "Discounted cash flow",
                                   "Cash from operations guidance less YTD", "GM Financial distributions received", "After-tax net interest"))
    w.column("Forecast", "B", 3, ("Q4 stub", "", "", "", "", "", "", "",
                                  "=AVERAGE(Actuals!B15:B16)-Actuals!C12", "", "=B16-B11-B17+B18",
                                  a.periods[0].discount_years, "=B13/(1+Assumptions!B6)^B14",
                                  "=AVERAGE(Actuals!B13:B14)-Actuals!C11", "=Lender!B10/4", "=Actuals!B17*(1-Assumptions!B13)"))
    for i, (col, row) in enumerate(zip("CDEFG", v.forecast, strict=True), 1):
        prev = "BCDEF"[i-1]
        volume = "Actuals!B4" if i == 1 else f"{prev}4"
        mix = "Actuals!B5*1000/Actuals!B4" if i == 1 else f"{prev}5"
        used = "Actuals!B6" if i == 1 else f"{prev}6"
        services = "Actuals!B7" if i == 1 else f"{prev}7"
        sales = "Actuals!B8" if i == 1 else f"{prev}8"
        g = "Assumptions!$B$9"
        vg = "Assumptions!$B$11"
        mg = f"((1+{g})/(1+{vg})-1)"
        w.column("Forecast", col, 3, ((str(row.end.year), "header", None),
            f"={volume}*(1+{vg}+(Actuals!C4/Actuals!D4-1-{vg})*0.5^{i})",
            f"=({mix})*(1+{mg}+((Actuals!C5/Actuals!C4)/(Actuals!D5/Actuals!D4)-1-{mg})*0.5^{i})",
            f"={used}*(1+{g}+(Actuals!C6/Actuals!D6-1-{g})*0.5^{i})",
            f"={services}*(1+{g}+(Actuals!C7/Actuals!D7-1-{g})*0.5^{i})",
            f"={col}4*{col}5/1000+SUM({col}6:{col}7)", f"={col}8*Actuals!B42", f"={col}8*Actuals!B43",
            f"={col}8*(Actuals!B45+(Actuals!B46-Actuals!B45)*{(i-1)/4})", f"=({col}8-{sales})*Actuals!B44",
            f"={col}9*(1-Assumptions!B13)+{col}10-{col}11-{col}12", a.periods[i].discount_years,
            f"={col}13/(1+Assumptions!B6)^{col}14"))
        w.check("Forecast", **{f"{col}8": row.revenue_millions, f"{col}13": row.fcff_millions})
    w.check("Forecast", B13=a.periods[0].cash_flow.amount_millions)
    w.column("Lender", "A", 1, ("GM Financial common equity", "USD millions", "Years after September close", "Net receivables",
                                 "Leased vehicles", "Earning assets", "Common earnings", "Opening capital", "Closing capital",
                                 "Common distributions", "Discounted distributions", "Residual income", "Discounted residual income",
                                 "", "Terminal distributions PV", "Terminal residual income PV", "Common equity from distributions",
                                 "Common equity from residual income", "Common capital percentage", "Common earnings yield"))
    g = "Assumptions!$B$9"
    ke = "Assumptions!$B$8"
    for i, (col, prev) in enumerate(zip("BCDEF", "ABCDE", strict=True), 1):
        loans = "Actuals!B35" if i == 1 else f"{prev}4"
        leases = "Actuals!B36" if i == 1 else f"{prev}5"
        old_assets = f"({loans}+{leases})"
        old_common = "Actuals!B37" if i == 1 else f"{prev}9"
        w.column("Lender", col, 3, (i, f"={loans}*(1+{g}+(Actuals!B35/Actuals!C35-1-{g})*0.5^{i})",
                                    f"={leases}*(1+{g}+(Actuals!B36/Actuals!C36-1-{g})*0.5^{i})", f"=SUM({col}4:{col}5)",
                                    f"=AVERAGE({old_assets},{col}6)*$B$20", f"={old_common}", f"={col}6*$B$19",
                                    f"={col}7-{col}9+{col}8", f"={col}10/(1+{ke})^{col}3", f"={col}7-{col}8*{ke}",
                                    f"={col}12/(1+{ke})^{col}3"))
    terminal_income = f"F6*(1+{g}/2)*B20"
    w.column("Lender", "B", 15, (f"=({terminal_income}-F9*{g})/({ke}-{g})/(1+{ke})^F3",
                                 f"=({terminal_income}-F9*{ke})/({ke}-{g})/(1+{ke})^F3",
                                 "=SUM(B11:F11)+B15", "=Actuals!B37+SUM(B13:F13)+B16",
                                 "=Actuals!B37/SUM(Actuals!B35:B36)", "=Actuals!B38/(SUM(Actuals!B35:C36)/2)"))
    w.column("DCF", "A", 1, ("Automotive terminal value", "USD millions", "", "Next-year NOPAT", "Net reinvestment",
                              "Terminal FCFF", "Discounted terminal value", "Automotive equity adjustment", "Required liquidity"))
    w.column("DCF", "B", 4, ("=Forecast!G9*(1-Assumptions!B13)*(1+Assumptions!B9)", "=B4*Assumptions!B9/Assumptions!B10", "=B4-B5",
                            "=B6/(Assumptions!B6-Assumptions!B9)/(1+Assumptions!B6)^Forecast!G14",
                            "=MAX(0,Actuals!B18-B9)-Actuals!B19-Actuals!B20", "=Actuals!B8*Assumptions!B12"))
    debt_residual = b["current_debt"]+b["long_debt"]+b["current_debt_eliminations"]-split["debt"]["automotive_debt_millions"]
    w.column("Checks", "A", 1, ("GM model checks", "USD millions", "", "Lender distribution less residual equity",
                                "Automotive debt residual (±1 at filing precision)", "Latest segment revenues less consolidated revenue"))
    w.column("Checks", "B", 4, ("=Lender!B17-Lender!B18", "=Actuals!B19+Actuals!B24-Actuals!B25",
                                 "=SUM(Actuals!D30:D31)-Actuals!D32"))
    w.check("Checks", B4=0, B5=debt_residual,
            B6=split["revenue"][-1]["automotive_revenue_millions"]+split["revenue"][-1]["finance_revenue_millions"]-split["revenue"][-1]["total_millions"])
    links = (("GM annual filing", r["sources"][0]), ("GM interim filing", r["sources"][1]), ("GM guidance supplement", x["source"]),
             ("GM Financial annual filing", e["sources"][0]), ("GM Financial prior interim filing", e["sources"][1]),
             ("GM Financial current interim filing", e["sources"][2]), ("Treasury yield", treasury["url"]))
    _sources(w, links, _LIMITS)
    report = {"company": "General Motors Company | GM", "method": "Automotive and captive lender research pilot",
              "headline": f"Combined scenario ${v.value_per_share:,.2f} per common share", "values": {"B7": v.value_per_share,
              "B8": a.equity_value_millions, "B9": f.common_equity_value}, "compact": True, "sources": links,
              "chart": ("Automotive revenue, USD billions", [row.revenue_millions/1000 for row in v.forecast]),
              "chart_labels": [str(row.end.year) for row in v.forecast],
              "sections": (("Investment view | Review", "Wholesale volume and vehicle mix drive Automotive earnings. Losses, funding costs and "
                            "required equity influence lender distributions. Lower incentives and stronger credit would help the combined case."),
                           ("Capital structure", "Automotive cash flow removes projected GM Financial dividends. The separate lender valuation uses "
                            "common equity after the preferred claim. Automotive sector debt retains matching intercompany lender assets. "
                            f"The reconciliation after eliminations differs by ${abs(debt_residual):,.0f}M at the filing's precision.")),
              "text": (f"Automotive contributes ${a.equity_value_millions/1000:,.1f}B at {a.wacc:.2%} WACC. GM Financial contributes "
                       f"${f.common_equity_value/1000:,.1f}B at {f.cost_of_equity:.2%} cost of equity.",
                       "Automotive uses the filed full-year cash flow and capex guidance for the remaining quarter. Later cash flows use volume, "
                       "revenue per vehicle, capex and working capital. GM Financial forecasts receivables and leased vehicles separately.", _LIMITS)}
    return _export(w.plan(.005), folder, node, modules, "GM", a.as_of, report)

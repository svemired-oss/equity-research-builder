"""Export foreign operating statements, local-currency DCF and a reconciled report."""

from dataclasses import replace

from .operating_research import operating_rating
from .sap_valuation import SAP_GUIDES, foreign_defaults, prepare_foreign_operating
from .specialty_export import PCT, PRICE, _Workbook, _export


def sap_workbook(model):
    e, a, dcf = model["evidence"], model["assumptions"], model["dcf"]
    ticker, currency = model["ticker"], model["currency"]
    periods = "Half-year" if ticker == "HLN" else "Quarterly"
    working_assets = "Operating receivables and inventory" if ticker == "HLN" else "Trade and other receivables"
    units = f"{currency} millions"
    q, cap = e["interim"]["periods"][0], model["capital"]
    w = _Workbook(e["company"], dcf.as_of, f"{currency} discounted cash flow", units + " unless stated")
    w.column("Cover", "A", 7, (f"Enterprise value ({currency}m)", f"Common equity ({currency}m)", f"{currency} per ordinary share",
                              "USD per ADS", "U.S. close (USD)", "Indicative return vs. U.S. close",
                              "Present value of terminal value", "Terminal share of enterprise value"))
    pv = "SUM(Forecast!B34:G34)"
    terminal = "Forecast!G12*(1+Assumptions!B5)*(1-Assumptions!B5/Assumptions!B6)"
    bridge = "Actuals!B11-Actuals!B19-Actuals!B20"
    w.column("Cover", "B", 7, (
        f"={pv}+B13", f"=B7+{bridge}", ("=B8/Actuals!B21", "total", "0.00"),
        ("=B9*Actuals!B22*Actuals!B23", "total", PRICE), ("=Actuals!B24", "text", PRICE),
        ("=B10/B11-1", "text", PCT), f"={terminal}/(Assumptions!B10-Assumptions!B5)/(1+Assumptions!B10)^Forecast!G33",
        ("=B13/B7", "text", PCT)))
    w.check("Cover", B7=dcf.enterprise_value_millions, B8=dcf.equity_value_millions,
            B9=dcf.value_per_share, B10=model["usd_per_ads"], B11=e["market_quote"]["close"],
            B13=dcf.terminal_present_value_millions)
    w.column("Cover", "A", 16, ((f"USD close and FX on {model['market_date']}. The DCF uses the filed balance date.",
                                "note", None),))
    if e.get("comparables"):
        _add_foreign_comps(w, model)
    w.column("Assumptions", "A", 1, ("Forecast assumptions", "Blue values are editable", "",
                                    "WACC premium over benchmark", "Terminal growth", "Terminal incremental ROIC",
                                    "Annual retention of excess growth", "Operating margin adjustment",
                                    "Dated local government benchmark", f"{currency} WACC", "Operating margin", "Effective tax rate",
                                    "Depreciation / revenue", "Cash capex / revenue", "Annualized finance cost",
                                    "Share of year remaining at balance date", "Year-four cash capex / revenue"))
    values = (a.wacc_spread, a.terminal_growth, a.terminal_roic, a.growth_retention, a.margin_shift,
              e["yield_benchmark"]["rate"], "=SUM(B4,B9)", "=Actuals!B5/Actuals!B4+B8",
              "=1-Actuals!B7/Actuals!B6", "=Actuals!B8/Actuals!B4", "=Actuals!B9/Actuals!B4")
    w.column("Assumptions", "B", 4, [(x, "input" if i < 5 else "text", PCT) for i,x in enumerate(values)])
    target = e["revenue_scenario"].get("capex_target", "=B14")
    w.column("Assumptions", "B", 15, ("=Actuals!B10/(1-B16)", (model["stub_years"], "text", PCT),
                                       (target, "input" if isinstance(target, float) else "text", PCT)))
    w.column("Actuals", "A", 1, (f"{periods} IFRS and market evidence", units + " unless stated", "", "Filed balance-date YTD revenue",
                                "YTD operating profit", "YTD pretax income", "YTD net income", "YTD depreciation and amortization",
                                "YTD cash capex", "YTD finance cost", "Cash and equivalents", "PP&E and intangibles",
                                working_assets, "Trade and other payables", "Separately identified contract liabilities", "Total equity",
                                "Total assets", "Total liabilities", "Gross financial liabilities", "Noncontrolling interests",
                                (f"{periods} diluted shares (millions)"), f"USD per {currency}", "Ordinary shares per ADS", "U.S. close (USD)",
                                "Opening operating working capital", "Other assets", "Other liabilities", "Other financial assets"))
    w.column("Actuals", "B", 4, (
        *(q["income"][key] for key in ("revenue", "operating_income", "pretax_income", "net_income")),
        q["cash_flow"]["depreciation"], -q["cash_flow"]["capital_expenditures"], cap["finance_cost_ytd"],
        q["balance"]["cash"], cap["ppe_intangibles"], cap["receivables"], cap["payables"], cap["contract_liabilities"],
        q["balance"]["equity"], q["balance"]["assets"], q["balance"]["liabilities"], cap["financial_liabilities"],
        cap["minority"], cap["diluted_shares"], (e["fx"]["usd_per_unit"], "text", "0.0000"),
        e["listing"]["ordinary_shares_per_listed_unit"], (e["market_quote"]["close"], "text", PRICE),
        "=B13-B14-B15", "=B17-B11-B12-B13", "=B18-B19-B14-B15", cap["other_financial_assets"]))
    w.column("Actuals", "A", 31, ("Revenue line", *(d["label"] for d in e["revenue_scenario"]["drivers"])))
    for column, key, label in (("B","prior_q4","Prior Q4"), ("C","ytd_current","Current YTD"), ("D","ytd_comparative","Prior YTD")):
        w.column("Actuals", column, 31, ((label, "header", None), *(d[key] for d in e["revenue_scenario"]["drivers"])))
    w.column("Actuals", "E", 31, (("Comparable growth", "header", None), *( (f"=C{i}/D{i}-1","text",PCT) for i in range(32,36))))
    historical_fields = ("revenue","operating_income","pretax_income","net_income","operating_cash","investing_cash",
                         "financing_cash","capital_expenditures","assets","liabilities","equity","cash")
    w.column("History", "A", 1, ("Audited IFRS history", units, "Fiscal year ended",
                                 *(key.replace("_"," ").capitalize() for key in historical_fields)))
    for col, row in zip("BCD", model["annual"], strict=True):
        w.column("History", col, 3, ((row["period_end"],"header",None),
                                     *(row["metrics"][k]/1e6 if k in row["metrics"] else "Unavailable" for k in historical_fields)))
    w.column("Forecast", "A", 1, ("Operating forecast and statements", units, "Period ending",
                                  *(d["label"] for d in e["revenue_scenario"]["drivers"]),
                                  "Full-year revenue", "Period revenue", "EBIT", "Income tax expense", "NOPAT", "Finance expense",
                                  "Net income", "Depreciation and amortization", "Capex", working_assets,
                                  "Operating payables and contract liabilities", "Operating working capital", "Change in working capital",
                                  "Cash from operations", "Cash from investing", "Net change in cash", "Ending cash",
                                  "PP&E and intangibles", "Other assets", "Total assets", "Financial liabilities",
                                  "Other liabilities", "Total liabilities", "Total equity", "Free cash flow to firm",
                                  "Discount years", "Present value of FCFF"))
    w.column("Checks","A",1,("Financial and valuation checks",units + " unless stated","Period ending",
                              "Assets less liabilities and equity","Cash roll-forward","Fixed asset roll-forward",
                              "FCFF vs. operating cash and interest"))
    for step,(col, row, flow) in enumerate(zip("BCDEFG", model["years"], dcf.periods, strict=True)):
        prev = "ABCDEF"[step]
        header = (row["end"].isoformat(), "header", None)
        revenue = tuple(
            f"=Actuals!C{i}+Actuals!B{i}*(1+Actuals!E{i})" if step == 0 else
            f"={prev}{r}*(1+Assumptions!$B$5+(Actuals!$E${i}-Assumptions!$B$5)*Assumptions!$B$7^{step})"
            for r,i in zip(range(4,8),range(32,36),strict=True))
        wc, cash, ppe, equity = ("Actuals!$B$25","Actuals!$B$11","Actuals!$B$12","Actuals!$B$16") if not step else (
            f"{prev}19",f"{prev}24",f"{prev}25",f"{prev}31")
        w.column("Forecast",col,3,(header,*revenue,f"=SUM({col}4:{col}7)",f"={col}8-Actuals!B4" if not step else f"={col}8",
            f"={col}9*Assumptions!$B$11", f"=({col}10-{col}13)*Assumptions!$B$12", f"={col}10*(1-Assumptions!$B$12)",
            "=Assumptions!$B$15*Assumptions!$B$16" if not step else "=Assumptions!$B$15", f"={col}10-{col}11-{col}13",
            f"={col}9*Assumptions!$B$13",
            f"={col}9*(Assumptions!$B$14+(Assumptions!$B$17-Assumptions!$B$14)*MIN({step}/4,1))",
            f"={col}8/$B$8*Actuals!$B$13", f"={col}8/$B$8*SUM(Actuals!$B$14:$B$15)",f"={col}17-{col}18",
            f"={col}19-{wc}",f"={col}14+{col}15-{col}20",f"=-{col}16",f"=SUM({col}21:{col}22)",f"={cash}+{col}23",
            f"={ppe}+{col}16-{col}15","=Actuals!$B$26",f"=SUM({col}24:{col}26)+{col}17","=Actuals!$B$19","=Actuals!$B$27",
            f"=SUM({col}28:{col}29)+{col}18", f"={equity}+{col}14",f"={col}12+{col}15-{col}16-{col}20",
            flow.discount_years,f"={col}32/(1+Assumptions!$B$10)^{col}33"))
        w.column("Checks",col,3,(header,*((x,"check",None) for x in (
            f"=Forecast!{col}27-Forecast!{col}30-Forecast!{col}31",
            f"=Forecast!{col}24-{('Forecast!' if step else '') + cash}-Forecast!{col}23",
            f"=Forecast!{col}25-{('Forecast!' if step else '') + ppe}-Forecast!{col}16+Forecast!{col}15",
            f"=Forecast!{col}32-Forecast!{col}21-Forecast!{col}22-Forecast!{col}13*(1-Assumptions!$B$12)"))))
        for rr,key in ((9,"revenue"),(10,"ebit"),(14,"net_income"),(16,"capex"),(19,"wc"),(24,"cash"),(25,"ppe"),
                       (27,"assets"),(30,"liabilities"),(31,"equity"),(32,"fcff")):
            w.check("Forecast", **{f"{col}{rr}":row[key]})
        w.check("Forecast", **{f"{col}34":flow.present_value_millions})
        w.check("Checks", **{f"{col}{i}":0 for i in range(4,8)})
    w.expected["Sensitivity"] = {}
    w.column("Sensitivity","A",1,(f"{currency} per ordinary share","Updated by model formulas","WACC / terminal growth"))
    for col, growth_step in zip("BCD",(-.005,0,.005),strict=True):
        w.column("Sensitivity",col,3,((f"=Assumptions!$B$5+({growth_step})","header",PCT),))
        for r,wacc_step in enumerate((-.005,0,.005),4):
            w.column("Sensitivity","A",r,((f"=Assumptions!$B$10+({wacc_step})","header",PCT),))
            flows = "+".join(f"Forecast!{c}32/(1+$A{r})^Forecast!{c}33" for c in "BCDEFG")
            tv = (f"Forecast!$G$12*(1+{col}$3)*(1-{col}$3/Assumptions!$B$6)"
                  f"/($A{r}-{col}$3)/(1+$A{r})^Forecast!$G$33")
            w.column("Sensitivity",col,r,(f"=({flows}+{tv}+{bridge})/Actuals!$B$21",))
            w.check("Sensitivity",**{f"{col}{r}":model["sensitivity"][r-4]["BCD".index(col)]})
    links = sap_sources(model)
    w.column("Sources","A",1,("Sources and methodology","Evidence links", "", *(label for label,_ in links)))
    w.column("Sources","B",4,tuple(url for _,url in links))
    notes = _METHOD + (
        "The denominator is a weighted-average diluted share proxy. Other financial assets receive no bridge credit.",
        f"{currency} WACC uses the dated local government yield plus an editable analyst spread.",
        "Haleon uses June financial statements, September product sales and the filed capex outlook." if ticker == "HLN" else
        "Management's constant-currency outlook and reported-currency estimates use different measurement bases.",
    )
    for i, line in enumerate(notes, 6 + len(links)):
        w.column("Sources","A",i,((line,"note",None),))
    changes = model.get("overrides", ())
    if changes:
        default = foreign_defaults(ticker)
        w.column("Guided changes","A",1,("Guided forecast changes","Original filing and market evidence", "",
                                        *(SAP_GUIDES[row["target"]][0] for row in changes)))
        for col, label in zip("BCD",("Base","Selected","Reason"),strict=True):
            w.column("Guided changes",col,3,((label,"header",None),))
        for i,row in enumerate(changes,4):
            target = row["target"]
            assumption_row = 4 + list(SAP_GUIDES).index(target)
            w.column("Guided changes","B",i,((getattr(default,target),"text",PCT),))
            w.column("Guided changes","C",i,((f"=Assumptions!B{assumption_row}","text",PCT),))
            w.column("Guided changes","D",i,(row["reason"],))
    return w.plan(.005)


_METHOD = (
    "The bridge uses reported cash, mapped financial liabilities, book minority interests and diluted shares.",
    "Forecasts hold financing claims and other balances flat and retain modeled earnings. Dividends and buybacks are outside the forecast.",
    "Operating cash flow treats share-based compensation as an operating cost. Working capital uses the operating balances shown on Actuals.",
)


def sap_sources(model):
    e = model["evidence"]
    currency = model["currency"]
    extra = (("Q3 product sales and outlook",e["revenue_scenario"]["source"]),) if model["ticker"] == "HLN" else ()
    home = (("London ordinary-share listing",e["home_listing"]["url"]),) if e.get("home_listing") else ()
    comps = e.get("comparables")
    peer_links = tuple((f"{row['ticker']} annual report",row["filings"][0]) for row in comps["rows"]) if comps else ()
    return (("Annual IFRS report and ADS ratio",e["listing"]["source"]), ("Interim IFRS statements and outlook",e["interim"]["source"]),
            (f"U.S. close on {model['market_date']}",e["market_quote"]["url"]),
            (f"USD per {currency} on {e['fx']['date']}", e["fx"]["url"]),
            (f"{currency} benchmark on {e['yield_benchmark']['date']}",e["yield_benchmark"]["url"])) + extra + home + peer_links


def _add_foreign_comps(workbook, model):
    comps = model["evidence"]["comparables"]
    w = workbook
    w.expected["Comps"] = {}
    w.column("Cover", "A", 15, ("Annual peer value per U.S. ADS",))
    w.column("Cover", "B", 15, (("=Comps!B12", "total", PRICE),))
    w.check("Cover", B15=comps["value"])
    w.column("Comps", "A", 1, ("Comparable company P/E", comps["basis"], f"U.S. market close on {comps['quote_date']}"))
    for col, label in zip("ABCDE", ("Ticker", "Fiscal period end", "USD close", "Diluted EPS (USD)", "Price / earnings"), strict=True):
        w.column("Comps", col, 4, ((label, "header", None),))
    for i, row in enumerate(comps["rows"], 5):
        values = (row["ticker"], row["fiscal_end"], (row["close"], "text", PRICE),
                  (row["eps"], "text", PRICE), (f"=C{i}/D{i}", "total", "0.0x"))
        for col, value in zip("ABCDE", values, strict=True):
            w.column("Comps", col, i, (value,))
        w.check("Comps", **{f"E{i}": row["multiple"]})
        w.column("Comps", "A", i + 10, ((f"{row['ticker']}. {row['reason']}", "note", None),))
    w.column("Comps", "A", 9, ("Median peer P/E", "Subject diluted EPS per ordinary share",
                               "Subject diluted EPS per USD ADS", "Peer-implied value per USD ADS"))
    w.column("Comps", "B", 9, ("=MEDIAN(E5:E7)", comps["subject_eps_local"],
                               ("=B10*Actuals!B22*Actuals!B23","text",PRICE), ("=B9*B11","total",PRICE)))
    w.check("Comps", B9=comps["median"], B11=comps["subject_eps_usd"], B12=comps["value"])


def export_sap(model, folder, node, modules, *, previews=None):
    dcf, usd, e = model["dcf"], model["usd_per_ads"], model["evidence"]
    a = model["assumptions"]
    ticker, currency = model["ticker"], model["currency"]
    cases = [prepare_foreign_operating(e, replace(a, margin_shift=a.margin_shift + delta), ticker=ticker)["usd_per_ads"]
             for delta in (-.02,.02)]
    close = e["market_quote"]["close"]
    upside = usd / close - 1
    comps = e.get("comparables")
    peer_note = None
    if comps:
        names = ", ".join(row["ticker"] for row in comps["rows"])
        peer_note = (f"Peer-implied value USD {comps['value']:.2f} per ADS. {names} trade at a "
                     f"median {comps['median']:.1f}x diluted annual earnings using the same U.S. closing session. "
                     "Differences in fiscal year ends and product mix limit the comparison.")
        if usd > 0 and abs(comps["value"] / usd - 1) >= .5:
            peer_note += " The large gap warrants a review of the peer mix and the DCF's margin and reinvestment assumptions."
    overrides = model.get("overrides", ())
    guided = (("Guided assumptions", " ".join(f"{SAP_GUIDES[row['target']][0]} set to {row['value']:.2%}. "
                                             f"Reason given: {row['reason']}" for row in overrides)),) if overrides else ()
    headline = f"DCF {currency} {dcf.value_per_share:.2f} per ordinary share | USD {usd:.2f} per ADS"
    forecasts = ("September product sales update the annual forecast. June statutory margins and capital inform the DCF. "
                 "Cash capex moves toward the filed medium-term sales ratio." if ticker == "HLN" and
                 "capex_target" in e["revenue_scenario"] else
                 "Each reported revenue line fades toward terminal growth.")
    thesis = (
        "Oral health growth reflects branded innovation. Respiratory demand is seasonal, and pricing, category mix and retailer execution affect margins.",
        "Watch oral health growth, cough and cold demand, new product launches and rising capex. Leverage and currency translation add risk.",
    ) if ticker == "HLN" else (
        "Cloud growth and contract liabilities support recurring revenue as customers migrate from licenses and support. Sector competition raises execution risk.",
        "Watch cloud conversion, backlog and migration pace. Key risks include margin compression, acquisitions, renewals and currency translation.",
    )
    report = dict(company=e["company"], method=f"{ticker} equity research | {currency} DCF",
                  headline=headline, values={"B9":dcf.value_per_share,"B10":usd,"B11":e["market_quote"]["close"]},
                  chart=(f"Full-year revenue | {currency} billions", [row["revenue"]/1000 for row in model["years"][1:]]),
                  chart_labels=[str(row["end"].year) for row in model["years"][1:]],
                  chart_currency=currency + " ", compact=True, sections=(
                      ("Investment thesis", thesis[0]),
                      ("Catalysts and risks", thesis[1]),
                      ("Bull and bear cases", f"A two-percentage-point EBIT margin decrease gives USD {cases[0]:.2f} per ADS. "
                       f"A two-point increase gives USD {cases[1]:.2f}." +
                       ("" if peer_note else " Comparable-company valuation is pending.")),
                  ) + guided, text=(f"The model uses a {dcf.wacc:.2%} {currency} discount rate and {dcf.terminal.growth_rate:.2%} terminal growth. "
                           + forecasts,
                           f"The model indicates {operating_rating(upside)} at the USD {close:.2f} close on {model['market_date']} "
                           f"({upside:+.1%} to DCF value). The ADS conversion uses that day's USD per {currency} rate and the filed depositary ratio.",
                           "The model uses the filed balance sheet and a weighted-average diluted share proxy. "
                           "The later U.S. share price and FX are comparison inputs. Subsequent financing and equity changes require review."),
                  sources=sap_sources(model), peer_note=peer_note, peer_checks={"B12": comps["value"]} if comps else None)
    suffix = "guided-equity-research" if overrides else "equity-research"
    return _export(sap_workbook(model),folder,node,modules,ticker,dcf.as_of,report,previews=previews,suffix=suffix)

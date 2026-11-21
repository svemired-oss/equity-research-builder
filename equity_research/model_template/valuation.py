"""Linked Phase 4 workbook diagnostics for the shared operating template."""

from ..peer_selection import MIN_PEERS
from ..valuation_summary import BUY, SELL, TERMINAL_GAP, GROWTH_MIN, GROWTH_MAX
from ..valuation_comps import METRICS, SIZE_HIGH, SIZE_LOW
from .equity import BRIDGE, EQUITY, G, KE, SHARES, VALUE, YEARS

PCT = "0.00%"
PRICE = '"$"0.00'


def _labels(book, tab, items):
    for row, label in items.items():
        book.put(tab, f"A{row}", label)


def _wacc(book, market):
    tab = "WACC"
    erp, debt = market["erp"], market["debt"]
    book.input(tab, "B4", erp["value"], erp["url"], f"{erp['date']} | {erp['basis']}", PCT)
    book.put(tab, "A1", "Weighted average cost of capital | FCFF cross-check", "title")
    book.sources.pop((tab, "B5"), None)
    _labels(book,tab,{11:"Cost of equity",12:"After-tax cost of debt",13:"Equity weight",14:"Debt weight",
                      17:"Interest expense, USD millions",18:"Average carrying debt, USD millions",21:"Effective tax rate"})
    book.formulas(tab,"B",{11:"B3+B4*B5",12:"B6*(1-B21)",13:"B7/SUM(B7:B8)",
                           14:"B8/SUM(B7:B8)",21:"Assumptions!B4"})
    for row in (11,12,13,14,21):
        book.cells[tab][f"B{row}"][2] = PCT
    book.put(tab, "A5", "Beta (selected on the Cost of Equity tab)")
    if "interest" in debt:
        book.input(tab,"B17",debt["interest"],debt["url"],debt["date"])
        book.input(tab,"B18",debt["average_debt"],debt["prior_url"],f"{debt.get('debt_note', 'Average of opening and closing funded debt')}; {debt['url']}")
        book.put(tab,"B6","=B17/B18",fmt=PCT)
        for address in ("A10","B10"):
            book.cells[tab].pop(address,None)
        book.sources.pop((tab,"B10"),None)
    else:
        book.input(tab,"B10",debt["spread"],debt["url"],f"{debt['date']} | {debt['basis']}",PCT)
    # Read by the report, so the PDF states the same basis as the workbook.
    book.put(tab,"A40","Pretax debt cost basis")
    book.put(tab,"B40",debt["basis"].rstrip(".") + ".")


def _comps(book, comps):
    tab = "Comps"
    book.cells[tab] = {}
    book.expected[tab] = {}
    for key in [k for k in book.sources if k[0] == tab]:
        del book.sources[key]
    _labels(book,tab,{1:"Comparable valuation | cross-check only, never part of the target",
                      12:"Peer selection market-cap band",14:"Implied value across available methods",
                      16:"First quartile per share",17:"Third quartile per share",20:"Peer multiple distributions"})
    book.input(tab,"B12",SIZE_LOW,basis="Analyst peer size eligibility floor used at collection")
    book.input(tab,"C12",SIZE_HIGH,basis="Analyst peer size eligibility ceiling used at collection")
    headers = ("Issuer","Period end","Close","EV","LTM sales","LTM EBITDA","Diluted EPS","EV/EBITDA","EV/Sales","P/E")
    for col,label in zip("ABCDEFGHIJ",headers,strict=True):
        book.put(tab,f"{col}4",label,"header")
    if comps and comps.get("subject"):
        book.put(tab,"A2",f"P/E basis: {comps.get('pe_basis','LTM GAAP')}. EV metrics use LTM.")
        for row, record in enumerate((comps["subject"],*comps["rows"]),5):
            book.put(tab,f"A{row}",record.get("ticker","Subject"))
            book.put(tab,f"B{row}",record["end"])
            for col,key in zip("CDEFG",("close","ev","sales","ebitda","eps"),strict=True):
                if record[key] is not None:
                    forward = record.get("forward_eps") if key == "eps" and comps.get("pe_basis") == "Forward GAAP NTM" else None
                    url = forward["url"] if forward else record["quote_url"] if key == "close" else record["filings"][0]
                    description = f"{forward['date']} | filed NTM GAAP EPS guidance" if forward else (
                        f"{record['end']} | {comps['basis']}; filings: " + ", ".join(record["filings"]))
                    book.input(tab,f"{col}{row}",record[key],url,description)
            for col, numerator, denom in (("H","D","F"),("I","D","E"),("J","C","G")):
                book.put(tab,f"{col}{row}",f'=IF(AND(ISNUMBER({denom}{row}),{denom}{row}>0,{numerator}{row}>0),{numerator}{row}/{denom}{row},"N/A")')
        subject = comps["subject"]
        book.input(tab,"B18",subject["adjustment"], subject["filings"][-1],"Subject EV-to-equity bridge")
        book.input(tab,"B19",subject["shares"], subject["filings"][-1],"Ending common shares, millions")
        for row, (metric, source, denominator) in enumerate(zip(METRICS,"HIJ","FEG",strict=True),22):
            book.put(tab,f"A{row}",metric)
            for col, fraction in zip("BCD",(1,2,3),strict=True):
                book.put(tab,f"{col}21",("Q1","Median","Q3")[fraction-1],"header")
                book.put(tab,f"{col}{row}",f'=IF(COUNT({source}6:{source}10)>={MIN_PEERS},QUARTILE({source}6:{source}10,{fraction}),"N/A")')
                implied = f"{col}{row}*{denominator}$5"
                if metric != "P/E":
                    implied = f"({implied}+$B$18)/$B$19"
                book.put(tab,f"{col}{row+5}",f'=IF(AND(ISNUMBER({col}{row}),ISNUMBER({denominator}$5),{denominator}$5>0),{implied},"N/A")',fmt=PRICE)
            book.put(tab,f"A{row+5}",f"{metric} implied price")
        for i,(ticker,reason) in enumerate(comps["excluded"],34):
            book.put(tab,f"A{i}",ticker)
            book.put(tab,f"B{i}",reason,"note")
    else:
        book.put(tab,"A3",(comps or {}).get("reason","Comparable evidence unavailable."))
    for row,column in ((14,"C"),(16,"B"),(17,"D")):
        book.put(tab,f"B{row}",f'=IF(COUNT({column}27:{column}29),MEDIAN({column}27:{column}29),"Insufficient peers")',fmt=PRICE)
    book.expected[tab]["B14"] = comps["value"] if comps and comps["value"] is not None else "Insufficient peers"
    selection = comps.get("selection") if comps else None
    if selection:
        book.put(tab,"A50","Peer selection | AI proposals kept only when they meet the SIC and size rules","section")
        book.put(tab,"A51",selection["rules"],"note")
        for col,label in zip("ABCD",("Peer","SIC","Tier","Market cap / subject"),strict=True):
            book.put(tab,f"{col}52",label,"header")
        for row,peer in enumerate(selection["peers"],53):
            for col,value in zip("ABCD",(peer["ticker"],peer["sic"],peer["tier"],peer["market_cap_ratio"]),strict=True):
                book.put(tab,f"{col}{row}",value,fmt='0.00"x"' if col=="D" else None)
        row = 53 + len(selection["peers"]) + 1
        book.put(tab,f"A{row}","Proposals rejected by the rules","section")
        for row,(ticker,reason) in enumerate(selection["rejected"],row+1):
            book.put(tab,f"A{row}",ticker)
            book.put(tab,f"B{row}",reason,"note")


def _reverse(book, model):
    tab = "DCF"
    _labels(book,tab,{38:"Market-implied five-year revenue CAGR at base EBIT margin (FCFE)",
                      42:"Reverse DCF bisection",43:"Search bounds are analyst assumptions."})
    book.input(tab,"B44",GROWTH_MIN,basis="Analyst lower bound on implied growth",fmt=PCT)
    book.input(tab,"C44",GROWTH_MAX,basis="Analyst upper bound on implied growth",fmt=PCT)
    for row in range(46,84):
        if row <= 47:
            book.put(tab,f"D{row}","=$B$44" if row == 46 else "=$C$44")
        else:
            if row == 48:
                lo,hi="$B$44","$C$44"
            else:
                lo,hi=f'IF(E{row-1}<Cover!$B$6,D{row-1},B{row-1})',f'IF(E{row-1}<Cover!$B$6,C{row-1},D{row-1})'
            book.put(tab,f"B{row}","="+lo)
            book.put(tab,f"C{row}","="+hi)
            book.put(tab,f"D{row}",f"=AVERAGE(B{row}:C{row})")
        for i,(col,forecast_col) in enumerate(zip("FGHIJ","BCDEF",strict=True),1):
            previous=f"'Income Statement'!$L$4*(1+$D{row})^{i-1}"
            sales=f"({previous})*(1+$D{row})"
            capex=f"Schedules!{forecast_col}5/'Income Statement'!{forecast_col}4"
            opening_wc = "Schedules!$L$12" if i==1 else f"({previous})*Assumptions!$B$32"
            cash=(f"({sales})*(SUM(Assumptions!$B$3,Assumptions!$B$11)*(1-Assumptions!$B$4)+Assumptions!$B$5-{capex})"
                  f"-(({sales})*Assumptions!$B$32-{opening_wc})"
                  f"+('Income Statement'!{forecast_col}$14-'Income Statement'!{forecast_col}$6)*(1-Assumptions!$B$4)")
            borrowing=(f"'Cash Flow Statement'!{forecast_col}$18-'Cash Flow Statement'!{forecast_col}$19"
                       + ("-Schedules!$B$40" if i==1 else ""))
            book.put(tab,f"{col}{row}",f"=(({cash})"+("*Assumptions!$B$12" if i==1 else "")
                     +f"+{borrowing})/(1+$B${KE})^{forecast_col}${YEARS}")
        # Gordon growth on this row's final-year FCFE; J is already discounted by the same factor.
        terminal=f"J{row}*(1+$B${G})/($B${KE}-$B${G})"
        book.put(tab,f"E{row}",f"=(SUM(F{row}:J{row})+{terminal}+{BRIDGE})/$B${SHARES}")
    book.put(tab,"B38",'=IF(AND(E46<=Cover!B6,E47>=Cover!B6),D83,"Unavailable")',fmt=PCT)
    value = model["valuation_summary"]["implied_cagr"]
    book.expected[tab]["B38"] = value if value is not None else "Unavailable"


def add_valuation(book, model):
    _wacc(book,model["valuation_market"])
    comps = model["valuation_comps"]
    _comps(book,{**comps,"selection":model.get("peer_selection")} if comps else comps)
    _reverse(book,model)
    tab = "Assumptions"
    assumptions = ((26,"Buy total-return threshold",BUY),(27,"Sell total-return threshold",SELL),
                   (29,"Gordon / exit terminal value gap threshold (FCFF cross-check)",TERMINAL_GAP))
    for row, label, value in assumptions:
        book.put(tab,f"A{row}",label)
        book.input(tab,f"B{row}",value,basis="Analyst valuation policy",fmt=PCT)
    _labels(book,"DCF",{85:"FCFF cross-check | terminal value against the peer exit multiple",
                        86:"Gordon terminal enterprise value (FCFF)",87:"Peer exit EBITDA multiple",
                        88:"Exit terminal enterprise value",89:"Terminal value divergence",
                        90:"Terminal cross-check",91:"Exit-multiple FCFF value per share"})
    book.formulas("DCF","B",{
        86:"B16*(1+B3)^F20",87:'IF(ISNUMBER(Comps!C27),Comps!C22,"Unavailable")',
        88:"IF(ISNUMBER(B87),SUM('Income Statement'!F5,Schedules!F6)*B87,\"Unavailable\")",
        89:'IF(ISNUMBER(B88),ABS(B88/B86-1),"Unavailable")',
        90:'IF(ISNUMBER(B89),IF(B89>Assumptions!B29,"Review","Within threshold"),"Unavailable")',
        91:'IF(ISNUMBER(B88),(SUM(B22:F22)+B88/(1+B3)^F20+B6+B7-SUM(B8:B10))/B12,"Unavailable")',
    })
    book.cells["DCF"]["B89"][2] = PCT
    _labels(book,"Cover",{13:"Peer multiples value (cross-check only)",14:"FCFF at WACC value (cross-check only)",
                          15:"Next 12 months dividends per share",16:"12-month price target",
                          17:"Expected 12-month total return",18:"FCFE / FCFF cross-check gap"})
    book.formulas("Cover","B",{
        13:"Comps!B14",
        14:"DCF!B15",
        15:"(Schedules!B23*Assumptions!B12+Schedules!C23*(1-Assumptions!B12))/DCF!B12",
        16:f"(DCF!B{EQUITY}*(1+DCF!B{KE})-B15*DCF!B12-Assumptions!B22)/Schedules!B26",
        17:"(B16+B15)/B6-1",
        18:"ABS(B7/B14-1)",
        9:'IF(B17>=Assumptions!B26,"Buy",IF(B17<=Assumptions!B27,"Sell","Hold"))',
    })
    book.put("Cover","A12","The target re-dates the FCFE equity value one year at the cost of equity, deducts the year's "
             "dividends and buybacks, and divides by the projected share count one year out (Schedules row 26).")
    for r in range(13,19):
        book.cells["Cover"][f"B{r}"][2] = PRICE if r <= 16 else PCT
    diagnostics = model["valuation_summary"]
    book.expected.setdefault("Cover",{}).update({"B9":diagnostics["rating"],
        "B13":diagnostics["peer"] if diagnostics["peer"] is not None else "Insufficient peers"})
    for row,key in ((14,"fcff"),(15,"dividends"),(16,"target"),(17,"return"),(18,"fcff_gap")):
        book.expected["Cover"][f"B{row}"] = diagnostics[key]
    for row,key in ((88,"exit_value"),(89,"terminal_gap"),(91,"exit_per_share")):
        value = diagnostics[key]
        book.expected["DCF"][f"B{row}"] = value if value is not None else "Unavailable"
    _labels(book,"Football Field",{7:"Peer multiples interquartile range (cross-check)",8:"52-week trading range",9:"Current price"})
    for c,r in (("B",16),("C",17),("D",14)):
        book.put("Football Field",f"{c}7",f"=Comps!B{r}",fmt=PRICE)
    prices = model["valuation_market"].get("range")
    if prices:
        for c,key in (("B","low"),("C","high")):
            book.input("Football Field",f"{c}8",prices[key],prices["url"],
                       f"52-week split-adjusted intraday {key} through {prices['date']}",PRICE)
    else:
        book.put("Football Field","B8","Unavailable")
    for c in "BCD":
        book.put("Football Field",f"{c}9","=Cover!B6",fmt=PRICE)
    for index,c in enumerate("EFGHIJKLMNOPQRSTUVW"):
        book.put("Football Field",f"{c}12",f"=MIN(B5:B9)+(MAX(C5:C9)-MIN(B5:B9))*{index}/18","header",PRICE)
        for row, source in ((16,8),(17,9)):
            book.put("Football Field",f"A{row}",f"=A{source}")
            mark = (f'ABS({c}$12-$B9)<=(MAX(C5:C9)-MIN(B5:B9))/36' if source==9
                    else f'AND(ISNUMBER($B8),{c}$12>=$B8,{c}$12<=$C8)')
            book.put("Football Field",f"{c}{row}",f'=IF({mark},"■","")')


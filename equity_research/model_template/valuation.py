"""Linked Phase 4 workbook diagnostics for the shared operating template."""

from ..valuation_summary import BLEND, BUY, SELL, DISAGREEMENT, TERMINAL_GAP, GROWTH_MIN, GROWTH_MAX
from ..valuation_comps import METRICS, SIZE_HIGH, SIZE_LOW

PCT = "0.00%"
PRICE = '"$"0.00'


def _labels(book, tab, items):
    for row, label in items.items():
        book.put(tab, f"A{row}", label)


def _wacc(book, market):
    tab = "WACC"
    erp, beta, debt = (market[k] for k in ("erp","beta","debt"))
    book.input(tab, "B4", erp["value"], erp["url"], f"{erp['date']} | {erp['basis']}", PCT)
    book.cite(tab, "B5", beta["url"], f"{beta['date']} | {beta['basis']}")
    _labels(book,tab,{11:"Cost of equity",12:"After-tax cost of debt",13:"Equity weight",14:"Debt weight",
                      15:"Raw regression beta",16:"Matched weekly returns",17:"Interest expense, USD millions",
                      18:"Average carrying debt, USD millions",19:"Blume market anchor",
                      20:"Blume regression weight",21:"Effective tax rate"})
    book.formulas(tab,"B",{11:"B3+B4*B5",12:"B6*(1-B21)",13:"B7/SUM(B7:B8)",
                           14:"B8/SUM(B7:B8)",21:"Assumptions!B4"})
    for row in (11,12,13,14,21):
        book.cells[tab][f"B{row}"][2] = PCT
    if "raw" in beta:
        book.input(tab,"B15", beta["raw"],beta["url"],
                   f"{beta['start']} to {beta['date']} | Regression vs {beta['index_url']}")
        book.input(tab,"B16", beta["weeks"],beta["index_url"],"Matched completed weekly returns")
        book.input(tab,"B19",1,basis="Blume adjustment, market beta anchor")
        book.input(tab,"B20",2/3,basis="Blume adjustment, weight on observed beta",fmt=PCT)
        book.put(tab,"B5","=B15*B20+B19*(1-B20)")
        book.sources.pop((tab,"B5"), None)
    else:
        rows = []
        for row, (symbol, item) in enumerate(beta["peers"].items(),23):
            book.put(tab,f"A{row}",f"{symbol} adjusted beta")
            book.input(tab,f"B{row}",item["value"],item["url"],f"{item['date']} | {item['basis']}")
            rows.append(row)
        book.put(tab,"B5",f"=MEDIAN(B{rows[0]}:B{rows[-1]})")
        book.sources.pop((tab,"B5"), None)
    if "interest" in debt:
        book.input(tab,"B17",debt["interest"],debt["url"],debt["date"])
        book.input(tab,"B18",debt["average_debt"],debt["prior_url"],f"Average of opening and closing funded debt; {debt['url']}")
        book.put(tab,"B6","=B17/B18",fmt=PCT)
        for address in ("A10","B10"):
            book.cells[tab].pop(address,None)
        book.sources.pop((tab,"B10"),None)
    else:
        book.input(tab,"B10",debt["spread"],debt["url"],f"{debt['date']} | {debt['basis']}",PCT)


def _comps(book, comps):
    tab = "Comps"
    book.cells[tab] = {}
    book.expected[tab] = {}
    for key in [k for k in book.sources if k[0] == tab]:
        del book.sources[key]
    _labels(book,tab,{1:"Comparable valuation",12:"Peer selection market-cap band",14:"Implied value across available methods",
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
                book.put(tab,f"{col}{row}",f'=IF(COUNT({source}6:{source}10)>=2,QUARTILE({source}6:{source}10,{fraction}),"N/A")')
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
        book.put(tab,f"B{row}",f'=IF(COUNT({column}27:{column}29),MEDIAN({column}27:{column}29),"Unavailable")',fmt=PRICE)
    book.expected[tab]["B14"] = comps["value"] if comps and comps["value"] is not None else "Unavailable"


def _reverse(book, model):
    tab = "DCF"
    _labels(book,tab,{38:"Market-implied five-year revenue CAGR at base EBIT margin",
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
                  f"-(({sales})*Assumptions!$B$32-{opening_wc})")
            book.put(tab,f"{col}{row}",f"=({cash})"+("*Assumptions!$B$12" if i==1 else "")+f"/(1+$B$3)^{forecast_col}$20")
        terminal=f"'Income Statement'!$L$4*(1+D{row})^5*SUM(Assumptions!$B$3,Assumptions!$B$11)*(1-Assumptions!$B$4)*(1+$B$4)*(1-$B$4/$B$5)/($B$3-$B$4)/(1+$B$3)^$F$20"
        book.put(tab,f"E{row}",f"=(SUM(F{row}:J{row})+{terminal}+$B$6+$B$7-SUM($B$8:$B$10))/$B$12")
    book.put(tab,"B38",'=IF(AND(E46<=Cover!B6,E47>=Cover!B6),D83,"Unavailable")',fmt=PCT)
    value = model["valuation_summary"]["implied_cagr"]
    book.expected[tab]["B38"] = value if value is not None else "Unavailable"


def add_valuation(book, model):
    _wacc(book,model["valuation_market"])
    _comps(book,model["valuation_comps"])
    _reverse(book,model)
    tab = "Assumptions"
    assumptions = ((25,"DCF weight in blended fair value",BLEND),(26,"Buy total-return threshold",BUY),
                   (27,"Sell total-return threshold",SELL),(28,"DCF / comps disagreement threshold",DISAGREEMENT),
                   (29,"Gordon / exit terminal value gap threshold",TERMINAL_GAP))
    for row, label, value in assumptions:
        book.put(tab,f"A{row}",label)
        book.input(tab,f"B{row}",value,basis="Analyst valuation policy",fmt=PCT)
    _labels(book,"DCF",{86:"Gordon terminal enterprise value",87:"Peer exit EBITDA multiple",
                        88:"Exit terminal enterprise value",89:"Terminal value divergence",
                        90:"Terminal cross-check",91:"Exit DCF fair value per share"})
    book.formulas("DCF","B",{
        86:"B16*(1+B3)^F20",87:'IF(ISNUMBER(Comps!C27),Comps!C22,"Unavailable")',
        88:"IF(ISNUMBER(B87),SUM('Income Statement'!F5,Schedules!F6)*B87,\"Unavailable\")",
        89:'IF(ISNUMBER(B88),ABS(B88/B86-1),"Unavailable")',
        90:'IF(ISNUMBER(B89),IF(B89>Assumptions!B29,"Review","Within threshold"),"Unavailable")',
        91:'IF(ISNUMBER(B88),(SUM(B22:F22)+B88/(1+B3)^F20+B6+B7-SUM(B8:B10))/B12,"Unavailable")',
    })
    book.cells["DCF"]["B89"][2] = PCT
    _labels(book,"Cover",{13:"Peer fair value",14:"Blended present value",15:"Next 12 months dividends per share",
                          16:"12-month price target",17:"Expected 12-month total return",18:"DCF / comps disagreement"})
    book.formulas("Cover","B",{
        13:"Comps!B14",
        14:'IF(ISNUMBER(B13),B7*Assumptions!B25+B13*(1-Assumptions!B25),"Unavailable")',
        15:(f"(Schedules!B23*Assumptions!B12+Schedules!C23*(1-Assumptions!B12))/DCF!B12"
            if model["payout_ratio"] is not None else '"Unavailable"'),
        16:'IF(COUNT(B14:B15)=2,B14*(1+WACC!B11)-B15,"Unavailable")',
        17:'IF(ISNUMBER(B16),(B16+B15)/B6-1,"Unavailable")',
        18:'IF(AND(ISNUMBER(B13),B13>0),ABS(B7/B13-1),"Unavailable")',
        9:'IF(COUNT(B16:B18)<3,"Review",IF(B18>Assumptions!B28,"Review",IF(B17>=Assumptions!B26,"Buy",IF(B17<=Assumptions!B27,"Sell","Hold"))))',
    })
    for r in range(13,19):
        book.cells["Cover"][f"B{r}"][2] = PRICE if r <= 16 else PCT
    diagnostics = model["valuation_summary"]
    book.expected["Cover"] = {"B9":diagnostics["rating"]}
    for row,key in ((14,"blended"),(15,"dividends"),(16,"target"),(17,"return"),(18,"gap")):
        value = diagnostics[key]
        if row == 17 and diagnostics["target"] is None:
            value = None
        book.expected["Cover"][f"B{row}"] = value if value is not None else "Unavailable"
    for row,key in ((88,"exit_value"),(89,"terminal_gap"),(91,"exit_per_share")):
        value = diagnostics[key]
        book.expected["DCF"][f"B{row}"] = value if value is not None else "Unavailable"
    _labels(book,"Football Field",{7:"Comparable interquartile range",8:"52-week trading range",9:"Current price"})
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


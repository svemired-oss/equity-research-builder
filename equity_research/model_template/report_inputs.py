"""Write reportable scenario results and source evidence into the workbook."""

def add_report_inputs(book, model, revenue_total):
    tab = "Report Evidence"
    for row, label in ((1,"Operating report evidence"), (2,"Issuer ticker"), (3,"Valuation cutoff"),
                       (4,"Revenue-line count"), (5,"Revenue method"),(6,"Method basis"),
                       (8,"Bear | EBIT margin change in percentage points"),
                       (9,"Bull | EBIT margin change in percentage points")):
        book.put(tab,f"A{row}",label)
    book.input(tab,"B2",model["ticker"],basis="Resolved SEC issuer")
    book.input(tab,"B3",str(model["as_of"]),basis="Valuation cutoff used in this workbook")
    book.put(tab,"B4",f"=COUNTA('Revenue Build'!A4:A{revenue_total-1})")
    driver = model["driver"]
    driver_labels = {
        "segment_growth":"Segment revenue growth",
        "units_price":"Units and selling price",
        "volume_yield":"Volume and yield",
        "production_price":"Production and realized price",
        "backlog_burn":"Backlog and conversion",
        "users_arpu":"Subscribers and revenue per subscriber",
        "stores_sales":"Stores, sales per store and comparable sales",
        "capacity_utilization_price":"Capacity, utilization and price",
    }
    book.input(tab,"B5",driver_labels[driver["type"]],driver.get("url"),"Verified model driver or documented fallback")
    book.input(tab,"B6",driver["reason"],driver.get("url"),"Driver verification outcome")
    book.put(tab,"C7","Fair value per share")
    exposure = "+".join(f"'Income Statement'!{col}4/(1+DCF!B3)^DCF!{col}20" +
                         ("*Assumptions!B12" if col == "B" else "") for col in "BCDEF")
    terminal = "'Income Statement'!F4*(1+DCF!B4)*(1-DCF!B4/DCF!B5)/(DCF!B3-DCF!B4)/(1+DCF!B3)^DCF!F20"
    book.put(tab,"B11",f"=(1-Assumptions!B4)*({exposure}+{terminal})/DCF!B12")
    book.put(tab,"A11","DCF sensitivity to a unit of EBIT margin")
    for row, case, sign in ((8,"Bear","-"),(9,"Bull","+")):
        book.put(tab,f"B{row}",f"={sign}Assumptions!B17*100")
        book.put(tab,f"C{row}",f"=DCF!B15+(B{row}/100-Assumptions!B11)*B11")
        book.expected.setdefault(tab,{})[f"C{row}"] = model["scenarios"][case].value_per_share
    for col,label in zip("ABCD",("Evidence type","Filed date","Verbatim passage","Filing URL"),strict=True):
        book.put(tab,f"{col}14",label,"header")
    for row, evidence in enumerate(model.get("report_evidence",()),15):
        for col,key in zip("ABCD",("kind","filed_on","text","url"),strict=True):
            value = evidence[key]
            if not isinstance(value,str) or value.lstrip().startswith(("=","@")):
                raise ValueError("Invalid workbook report evidence.")
            book.put(tab,f"{col}{row}",value,"note")


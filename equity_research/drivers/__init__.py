"""Filing-backed annual operating drivers. All monetary products are USD millions."""
from html.parser import HTMLParser
from math import isclose, isfinite, prod
import re

# Each factor defines its accepted filing label, units and analyst long-run growth proxy.
# Percentage factors are bounded levels with zero long-run growth.
PLUGINS = {
    "units_price": (("units sold (millions)", .02), ("average selling price (USD per unit)", .015)),
    "volume_yield": (("annual package volume (millions)", .02), ("revenue per package (USD)", .02)),
    "production_price": (("production sold (million barrels)", .01), ("realized price (USD per barrel)", .02)),
    "backlog_burn": (("opening backlog (USD millions)", .03), ("backlog conversion rate (%)", 0)),
    "users_arpu": (("average subscribers (millions)", .02), ("annual revenue per subscriber (USD)", .02)),
    "stores_sales": (("average company-operated stores", .015), ("sales per store (USD millions)", .02),
                     ("comparable store sales growth (%)", 0)),
    "segment_growth": (),
    "capacity_utilization_price": (("annual capacity (million units)", .025), ("capacity utilization (%)", 0),
                                   ("realized price (USD per unit)", .015)),
}
FALLBACK = "segment_growth"

# Match only equivalent units and periods. Daily traffic, monthly ARPU and
# period-end store counts require a different conversion or a time bridge.
ALIASES = {
    "units sold (millions)": ("unit sales (millions)", "units shipped (in millions)"),
    "average selling price (USD per unit)": ("average selling price ($ per unit)",),
    "annual package volume (millions)": ("total packages delivered (millions)",),
    "revenue per package (USD)": ("yield ($ per package)", "revenue per package ($)"),
    "average subscribers (millions)": ("average paid subscribers (in millions)",),
    "average company-operated stores": ("average company-operated coffeehouses",),
    "comparable store sales growth (%)": ("comparable coffeehouse sales growth (%)",),
}


def labels(label):
    return {s.casefold() for s in (label, *ALIASES.get(label, ()))}


class Tables(HTMLParser):
    """Keep cell and row boundaries so each numeric observation has a column header."""
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.tables, self.stack = [], []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.stack.append([])
        elif self.stack and tag == "tr":
            self.stack[-1].append([])
        elif self.stack and self.stack[-1] and tag in ("td", "th"):
            self.stack[-1][-1].append("")

    def handle_data(self, data):
        if self.stack and self.stack[-1] and self.stack[-1][-1]:
            self.stack[-1][-1][-1] += data

    def handle_endtag(self, tag):
        if tag == "table" and self.stack:
            self.tables.append([[" ".join(cell.split()) for cell in row] for row in self.stack.pop()])


def collect(packet, client):
    """Cache candidate passages from the most recent annual filing for deterministic replay."""
    annual = packet["actuals"]["periods"][-1]
    url = annual["filing"]["url"]
    tables = Tables(client.get_text(url)).tables
    passages = {}
    eligible = set().union(*(labels(label) for factors in PLUGINS.values() for label, _ in factors))
    for i, rows in enumerate(tables):
        if any(row and row[0].casefold() in eligible for row in rows):
            passages[f"annual-table-{i+1}"] = {"rows": rows, "url": url, "end": annual["period_end"]}
    packet["driver_passages"] = passages


def _number(cell, percent):
    match = re.fullmatch(r"(-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*(%)?", cell)
    if not match or (match[2] and not percent):
        raise ValueError("ambiguous numeric cell")
    value = float(match[1].replace(",", ""))
    return value / 100 if percent else value


def extract(packet, kind, passage):
    fallback = {"type": FALLBACK, "reason": "Fewer than three verified comparable annual driver periods."}
    if kind == FALLBACK or not passage:
        return fallback
    periods = packet["actuals"]["periods"][-3:]
    years = [p["period_end"][:4] for p in periods]
    if len(set(years)) != 3 or passage["end"] != periods[-1]["period_end"] or passage["url"] != periods[-1]["filing"]["url"]:
        return fallback
    rows = passage["rows"]
    def header_year(text):
        found = re.fullmatch(r"(?:FY\s*|[A-Za-z]{3,9}\.?\s+\d{1,2},?\s+)?(20\d{2})", text, re.I)
        return found[1] if found else ""
    headers = [row for row in rows if all([header_year(c) for c in row].count(y) == 1 for y in years)]
    if len(headers) != 1 or any(re.search(r"quarter|months ended [^y]|unaudited", " ".join(r), re.I) for r in rows):
        return fallback
    columns = [[header_year(c) for c in headers[0]].index(y) for y in years]
    guidance = [i for i, c in enumerate(headers[0]) if re.fullmatch(
        rf"(?:FY\s*)?{int(years[-1])+1}\s+guidance", c, re.I)]
    factors = []
    for label, terminal in PLUGINS[kind]:
        matched = [r for r in rows if r and r[0].casefold() in labels(label)]
        if len(matched) != 1:
            return fallback
        try:
            history = [_number(matched[0][c], "%" in label) for c in columns]
        except (IndexError, ValueError):
            return fallback
        if not all(isfinite(v) and (v > -1 if "growth" in label else v > 0)
                   and ("rate (%)" not in label and "utilization" not in label or v <= 1) for v in history):
            return fallback
        target = None
        if len(guidance) == 1 and guidance[0] < len(matched[0]) and matched[0][guidance[0]] not in ("", "—"):
            try:
                target = _number(matched[0][guidance[0]], "%" in label)
            except ValueError:
                return fallback
            if not isfinite(target) or target <= (-1 if "growth" in label else 0) or (
                    "%" in label and "growth" not in label and target > 1):
                return fallback
        factors.append({"label": label, "history": history, "terminal": terminal, "guidance": target})
    active = factors[:2] if kind == "stores_sales" else factors
    remainder = []
    for i, period in enumerate(periods):
        value = prod(f["history"][i] for f in active)
        actual = period["metrics"]["revenue"]/1e6
        if not isclose(value, actual, rel_tol=0, abs_tol=min(1, actual*.001)):
            matches = {f["dimensions"][0]["member"] for f in packet.get("operating_facts", {}).get("observations", [])
                       if f.get("metric") in ("segment_revenue", "product_or_service_revenue")
                       and f.get("unit") == "USD" and f.get("period_end") == period["period_end"]
                       and f.get("period_start") == period.get("period_start") and len(f.get("dimensions", [])) == 1
                       and isclose(value, f["value"]/1e6, rel_tol=0, abs_tol=min(1, value*.001))}
            if len(matches) != 1 or value >= actual or not matches.intersection(
                    {cell for row in rows for cell in row}):
                return {**fallback, "reason": "Operating-driver product does not reconcile to a matched revenue line in annual XBRL."}
            member = matches.pop()
            if remainder and member != remainder[0][0]:
                return fallback
            remainder.append((member, actual-value))
    if remainder and len(remainder) != 3:
        return fallback
    return {"type": kind, "factors": factors, "url": passage["url"], "years": years,
            "other": [amount for _, amount in remainder],
            "reason": "Three annual products reconcile to " + (remainder[0][0] if remainder else "consolidated revenue") + "."}


def choose(packet, selection):
    if packet.get("acquisition_revenue_basis"):
        return {"type": FALLBACK, "reason": "The acquisition requires the disclosed combined revenue basis."}
    kind = selection.get("driver_type", FALLBACK)
    return extract(packet, kind, packet.get("driver_passages", {}).get(selection.get("driver_passage"))) if kind in PLUGINS else {
        "type": FALLBACK, "reason": "Unknown business driver."}


def forecast(driver, terminal_growth=.025):
    paths = []
    for index, f in enumerate(driver["factors"]):
        if driver["type"] == "stores_sales" and index == 2:
            break
        values = f["history"]
        growth = ((values[-1]/values[0])**.5-1 if "%" not in f["label"] else 0)
        if driver["type"] == "stores_sales" and index == 1:
            comp = driver["factors"][2]
            growth = comp["history"][-1]
        path = [values[-1]]
        for year in range(1, 6):
            value = path[-1]*(1+f["terminal"]+(growth-f["terminal"])*.5**year)
            if year == 1 and driver["type"] == "stores_sales" and index == 1 and comp["guidance"] is not None:
                value = path[-1]*(1+comp["guidance"])
            path.append(f["guidance"] if year == 1 and f["guidance"] is not None else value)
        paths.append(path[1:])
    result = [prod(year) for year in zip(*paths, strict=True)]
    if driver["other"]:
        first, _, latest = driver["other"]
        rate = (latest/first)**.5-1
        for i in range(5):
            latest *= 1+terminal_growth+(rate-terminal_growth)*.5**(i+1)
            result[i] += latest
    return result


def formula_block(book, model):
    driver = model["driver"]
    if driver["type"] == FALLBACK:
        return
    tab = "Revenue Build"
    total = 4 + len(model["lines"])
    start = 2*total + 5
    book.put(tab, f"A{start}", driver["type"].replace("_", " ").title(), "section")
    for col, year in zip("HIJ", driver["years"], strict=True):
        book.put(tab, f"{col}{start}", year+" actual", "header")
    book.put(tab, f"K{start}", "Long-run annual growth proxy", "header")
    book.put(tab, f"L{start}", "Historical growth basis", "header")
    book.put(tab, f"M{start}", "Filed first-year driver guidance", "header")
    rows = []
    for index, factor in enumerate(driver["factors"], 1):
        r = start+index
        book.put(tab, f"A{r}", factor["label"])
        for c, v in zip("HIJ", factor["history"], strict=True):
            book.input(tab, f"{c}{r}", v, driver["url"], factor["label"], "0.00%" if "%" in factor["label"] else None)
        if factor["guidance"] is not None:
            book.input(tab, f"M{r}", factor["guidance"], driver["url"], "First forecast fiscal year guidance",
                       "0.00%" if "%" in factor["label"] else None)
        if driver["type"] == "stores_sales" and index == 3:
            continue
        book.input(tab, f"K{r}", factor["terminal"], basis=f"Analyst long-run proxy for {driver['type']} | {factor['label']}", fmt="0.00%")
        growth = f"=(J{r}/H{r})^0.5-1" if "%" not in factor["label"] else "=0"
        if driver["type"] == "stores_sales" and index == 2:
            growth = f"=J{r+1}"
        book.put(tab, f"L{r}", growth, fmt="0.00%")
        for y, (col, prev) in enumerate(zip("BCDEF","JBCDE"), 1):
            expression = f"{prev}{r}*(1+$K{r}+($L{r}-$K{r})*Assumptions!$B$13^{y})"
            if y == 1:
                if driver["type"] == "stores_sales" and index == 2:
                    expression = f"IF(ISNUMBER(M{r+1}),{prev}{r}*(1+M{r+1}),{expression})"
                expression = f"IF(ISNUMBER(M{r}),M{r},{expression})"
            book.put(tab, f"{col}{r}", "="+expression)
        rows.append(r)
    r = start+len(driver["factors"])+1
    other = r+1
    if driver["other"]:
        book.put(tab, f"A{other}", "Remaining filed revenue | segment growth")
        for col, value in zip("HIJ", driver["other"], strict=True):
            book.input(tab, f"{col}{other}", value, driver["url"], "Annual consolidated sales less reconciled operating line")
        for i, (col, prev) in enumerate(zip("BCDEF","JBCDE"), 1):
            book.put(tab, f"{col}{other}", f"={prev}{other}*(1+Assumptions!$B$9+(($J{other}/$H{other})^0.5-1-Assumptions!$B$9)*Assumptions!$B$13^{i})")
    book.put(tab, f"A{r}", "Operating driver revenue before guidance")
    for c in "BCDEF":
        book.put(tab, f"{c}{r}", "="+ "*".join(f"{c}{n}" for n in rows)
                 + (f"+{c}{other}" if driver["other"] else ""))
        for n in range(4,total):
            book.put(tab, f"{c}{n}", f"={c}{r}*IF(ISNUMBER(Assumptions!$B$15),Assumptions!$B$15/$B${r},1)*$H{n}/$H${total}")


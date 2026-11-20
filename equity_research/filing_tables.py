"""Read displayed SEC filing table rows when flattened XBRL facts are ambiguous."""

from html.parser import HTMLParser
import re


class FilingRows(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.rows = []
        self.row = self.cell = None
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = []
        if tag in {"td", "th"} and self.row is not None:
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in {"td", "th"} and self.cell is not None and self.row is not None:
            cell = " ".join("".join(self.cell).split())
            if cell and cell != "$":
                self.row.append(cell)
            self.cell = None
        if tag == "tr":
            if self.row:
                self.rows.append(self.row)
            self.row = self.cell = None


def _amount(text):
    text = text.strip().replace("$", "").replace(",", "")
    if text in ("—", "–", "-"):
        return 0.0
    return float(text) if re.fullmatch(r"\d+(?:\.\d+)?", text) else None


def _without_inventory_detail(section):
    headings = [i for i, row in enumerate(section) if len(row) == 1 and row[0].casefold().rstrip(":") == "inventories"]
    if len(headings) != 1:
        return section
    start = headings[0]
    totals = [i for i, row in enumerate(section) if row[0].casefold() == "total inventories" and i > start]
    if len(totals) != 1:
        return section
    end = totals[0]
    detail = [_amount(row[1]) for row in section[start+1:end] if len(row) > 1]
    total = _amount(section[end][1]) if len(section[end]) > 1 else None
    if None in (total, *detail) or len(detail) != end-start-1 or abs(sum(detail)-total) > .5:
        return section
    return section[:start] + section[end:]


def reconciled_current_cash(html, current_assets, existing_cash):
    """Select a cash and securities pair only after matching the printed subtotal."""
    if not current_assets:
        return None
    eligible_securities = {"short-term investments", "short-term marketable securities",
                           "marketable securities", "short-term debt securities"}
    matches = set()
    rows = FilingRows(html).rows
    for i, total in enumerate(rows):
        if total[0].casefold() != "total current assets" or len(total) < 3:
            continue
        window = rows[max(i-20, 0):i]
        starts = [index for index, row in enumerate(window) if row[0].casefold().rstrip(":") == "current assets"]
        if not starts:
            continue
        section = _without_inventory_detail(window[starts[-1]+1:])
        if any(len(line) != len(total) for line in section):
            continue
        items = {line[0].casefold(): _amount(line[1]) for line in section}
        displayed = _amount(total[1])
        if not displayed or items.get("cash and cash equivalents") is None:
            continue
        if len(items) != len(section) or any(value is None for value in items.values()):
            continue
        if any(("investment" in label or "marketable securit" in label) and label not in eligible_securities
               for label in items):
            continue
        tolerance = 1e-6 + .5 / (10 ** len(total[1].partition(".")[2]))
        if abs(sum(items.values())-displayed) > tolerance:
            continue
        scale = next((unit for unit in (1, 1000, 1_000_000) if abs(displayed*unit-current_assets) <= 1), None)
        if scale is None:
            continue
        cash = items["cash and cash equivalents"]*scale
        if existing_cash is not None and abs(cash-existing_cash) > tolerance*scale:
            continue
        securities = sum(items[label] for label in eligible_securities & items.keys())*scale
        matches.add((cash, securities))
    return matches.pop() if len(matches) == 1 else None

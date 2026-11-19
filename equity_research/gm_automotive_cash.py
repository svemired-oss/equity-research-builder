"""Read GM Automotive cash disclosures that are outside the tagged statements."""

from math import isclose
import re

from .filing_tables import FilingRows as _Rows
from .operating_research import ResearchGap
from .sec_guidance import _ReleaseText


def _dated_row(rows, label, dates, *, before=None):
    matches = []
    for index, row in enumerate(rows):
        if not re.fullmatch(re.escape(label) + r"(?:\([a-z]\))?", row[0], re.I):
            continue
        context = rows[max(0, index - 8):index]
        if not any(header[:len(dates)] == dates for header in context):
            continue
        if before and not any(item[0].startswith(before) for item in rows[index + 1:index + 6]):
            continue
        values = row[1:len(dates) + 1]
        if len(values) == len(dates) and all(re.fullmatch(r"(?:\d[\d,]*\.\d|\(\d[\d,]*\.\d\)|—)", value)
                                             for value in values):
            matches.append(tuple(0. if value == "—" else float(value.replace(",", "").replace("(", "-").rstrip(")"))
                                 for value in values))
    if len(matches) != 1:
        raise ResearchGap(f"GM's MD&A did not uniquely identify a dated Automotive row for {label}.")
    return matches[0]


def automotive_cash_evidence(annual_html, interim_html, *, annual_end, interim_end, finance):
    """Build a trailing cash-flow reference, preserving the MD&A's rounded precision."""
    annual = _Rows(annual_html).rows
    interim = _Rows(interim_html).rows
    prior_end = interim_end.replace(year=interim_end.year - 1)
    annual_dates = [str(annual_end.year), str(annual_end.year - 1)]
    interim_dates = [f"{end:%B} {end.day}, {end.year}" for end in (interim_end, prior_end)]
    operating = "Net automotive cash provided by (used in) operating activities"
    investing = "Net automotive cash provided by (used in) investing activities"

    def trailing(label, *, before=None):
        full, _ = _dated_row(annual, label, annual_dates, before=before)
        current, prior = _dated_row(interim, label, interim_dates, before=before)
        return round((full + current - prior) * 1000)

    operating_cash = trailing(operating)
    capex = -trailing("Capital expenditures", before=investing)
    dividends = finance["trailing_common_distributions_millions"]
    instant_dates = [f"{end:%B} {end.day}, {end.year}" for end in (interim_end, annual_end)]
    cash, _ = _dated_row(interim, "Automotive cash and cash equivalents", instant_dates)
    securities, _ = _dated_row(interim, "Marketable debt securities", instant_dates)
    total, _ = _dated_row(interim, "Automotive cash, cash equivalents, and marketable debt securities", instant_dates)
    if capex <= 0 or dividends < 0 or min(cash, securities) < 0 or not isclose(cash + securities, total, abs_tol=.151):
        raise ResearchGap("GM's Automotive capex or liquidity disclosures did not reconcile.")
    return {
        "operating_cash_flow_millions": operating_cash,
        "capex_millions": capex,
        "finance_dividends_millions": dividends,
        "after_capex_ex_finance_dividends_millions": operating_cash - capex - dividends,
        "cash_and_securities_millions": round(total * 1000),
        "disclosure_rounding_millions": 100,
    }


def _whole_number(cell):
    if not re.fullmatch(r"(?:\d[\d,]*|\(\d[\d,]*\))", cell):
        raise ResearchGap("GM's earnings supplement contains an unexpected numeric format.")
    return int(cell.replace(",", "").replace("(", "-").rstrip(")"))


def wholesale_volume(annual_html, interim_html, annual_end, interim_end):
    """Read published GMNA and GMI wholesale unit totals, in thousands."""
    def totals(html, periods):
        rows = _Rows(html).rows
        matches = []
        for index in range(4, len(rows)):
            if [row[0] for row in rows[index-2:index+1]] != ["GMNA", "GMI", "Total"]:
                continue
            if not any(header == periods for header in rows[index-4:index-2]):
                continue
            counts = [tuple(_whole_number(row[column]) for column in range(1, len(row), 3))
                      for row in rows[index-2:index+1]
                      if len(row) == 1 + 3 * len(periods) and row[-1] == "%"]
            if len(counts) == 3 and all(abs(a + b - total) <= 1 for a, b, total in zip(*counts, strict=True)):
                matches.append(counts[-1])
        if len(matches) != 1:
            raise ResearchGap("GM's dated Automotive wholesale volume totals were missing or ambiguous.")
        return matches[0]

    year = annual_end.year
    annual = totals(annual_html, [str(year - step) for step in range(3)])
    prior_interim = interim_end.replace(year=interim_end.year-1)
    interim = totals(interim_html, [f"{day:%B} {day.day}, {day.year}" for day in
                                    (interim_end, prior_interim, interim_end, prior_interim)])
    return {"full_year": annual[0], "current_ytd": interim[2], "prior_ytd": interim[3],
            "trailing": annual[0] + interim[2] - interim[3]}


def completed_repurchase_program(interim_html, *, balance_date, filed_on):
    """Recover the rounded post-quarter purchase amount when completion is disclosed."""
    event = re.compile(
        r"under our existing share repurchase program by \$[\d.]+ billion to an aggregate of "
        r"\$[\d.]+ billion, which we fully utilized in ([A-Z][a-z]+) (20\d{2})"
    )
    completion = {match.groups() for paragraph in _ReleaseText(interim_html).blocks
                  for match in event.finditer(paragraph)}
    if len(completion) != 1:
        return None
    month, year = completion.pop()
    if (month, int(year)) != (filed_on.strftime("%B"), filed_on.year) or filed_on <= balance_date:
        return None
    display_month = balance_date.strftime("%B")
    label = f"{display_month} 1, {balance_date.year} through {display_month} {balance_date.day}, {balance_date.year}"
    values = {row[-1] for row in _Rows(interim_html).rows if row[0] == label and len(row) == 5}
    if len(values) != 1:
        return None
    amount = re.fullmatch(r"\$([\d.]+) billion", values.pop())
    if not amount:
        return None
    return {"approximate_purchases_millions": float(amount[1])*1000, "completion_month": f"{month} {year}",
            "known_by": filed_on.isoformat()}


def automotive_release_evidence(release, *, interim_end, annual_end):
    """Extract exact Automotive balances and guidance from a dated GM earnings supplement."""
    rows = _Rows(release).rows
    current, annual = (f"{end:%B} {end.day}, {end.year}" for end in (interim_end, annual_end))
    ytd_header = f"Nine Months Ended {current}"
    if interim_end.month != 9 or not any(row[0] == ytd_header for row in rows):
        raise ResearchGap("A matching third-quarter GM earnings supplement is required for these guidance tables.")
    headers = [i for i, row in enumerate(rows[:-1]) if row == [current, annual]
               and rows[i + 1][:4] == ["Automotive", "GM Financial", "Reclassifications/Eliminations", "Combined"]]
    if len(headers) != 1:
        raise ResearchGap("GM's release did not uniquely identify the dated Automotive balance sheet.")
    start = headers[0] + 2
    balance_columns = len(rows[headers[0]+1])
    ends = [i for i in range(start, min(start + 70, len(rows))) if rows[i][0] == "Total Liabilities and Equity"]
    if len(ends) != 1:
        raise ResearchGap("GM's earnings release is missing the full Automotive balance sheet.")
    balance = rows[start:ends[0]+1]

    def value(label, *, after=None, column=1):
        scope = balance
        if after:
            index = next((i for i, item in enumerate(balance) if item[0] == after and len(item) == 1), None)
            if index is None:
                raise ResearchGap(f"GM's Automotive balance sheet is missing {after}.")
            scope = balance[index + 1:index + 2]
        matches = [row[column] for row in scope if len(row) == balance_columns + 1
                   and re.fullmatch(re.escape(label) + r"(?:\([a-z]\))?", row[0])]
        if len(matches) != 1:
            raise ResearchGap(f"GM's Automotive balance sheet did not uniquely disclose {label}.")
        return _whole_number(matches[0])

    financials = {key: value(label) for key, label in {
        "cash": "Cash and cash equivalents", "securities": "Marketable debt securities",
        "current_assets": "Total current assets", "current_liabilities": "Total current liabilities",
        "net_ppe": "Property, net", "assets": "Total Assets", "liabilities": "Total Liabilities",
        "equity": "Total Equity", "pensions": "Pensions",
        "postretirement": "Postretirement benefits other than pensions",
        "nci": "Noncontrolling interests",
    }.items()}
    financials["current_debt"] = value("Automotive", after="Short-term debt and current portion of long-term debt")
    financials["current_debt_eliminations"] = value("Automotive", after="Short-term debt and current portion of long-term debt",
                                                    column=3)
    financials["long_debt"] = value("Automotive", after="Long-term debt")
    if abs(financials["assets"] - financials["liabilities"] - financials["equity"]) > 1:
        raise ResearchGap("GM's Automotive sector balance sheet did not reconcile.")

    def guidance(label):
        fiscal_header = f"Year Ending December 31, {interim_end.year}"
        matches = [row[1] for i, row in enumerate(rows) if row[0] == label and len(row) == 3
                   and [fiscal_header] in rows[max(0, i - 6):i]]
        if len(matches) != 1:
            raise ResearchGap(f"GM's release did not uniquely identify updated guidance for {label}.")
        band = re.fullmatch(r"\$?\s*(\d+\.\d)-(\d+\.\d)", matches[0])
        if band is None or float(band[1]) > float(band[2]):
            raise ResearchGap(f"GM's updated guidance range for {label} has an unexpected format.")
        return tuple(float(amount) * 1000 for amount in band.groups())

    annual_ocf = guidance("Net automotive cash provided by operating activities")
    annual_capex = guidance("Less: Capital expenditures")
    period_labels = ["Three Months Ended", "Nine Months Ended"]
    ytd_tables = [i for i, row in enumerate(rows[:-3]) if row == period_labels
                  and rows[i+1][0] == current and rows[i+2][0] == "Net automotive cash provided by operating activities"
                  and rows[i+3][0] == "Less: Capital expenditures"]
    if len(ytd_tables) != 1:
        raise ResearchGap("GM's earnings release did not uniquely disclose exact Automotive year-to-date cash flows.")
    ytd_index = ytd_tables[0]
    prior = interim_end.replace(year=interim_end.year-1)
    earnings_dates = [current, f"{prior:%B} {prior.day}, {prior.year}"] * 2
    earnings = [i for i in range(12, len(rows)-7) if rows[i] == ["Operating segments"]
                and rows[i+1][0] == "GM North America (GMNA)" and rows[i+7][0] == "EBIT-adjusted"
                and earnings_dates in rows[i-12:i]]
    if len(earnings) != 1:
        raise ResearchGap("GM's release did not uniquely identify the segment EBIT reconciliation.")
    index = earnings[0]
    gm_financial = _whole_number(rows[index+4][3])
    total_ebit = _whole_number(rows[index+7][3])
    cruise = rows[index+3][3]
    if cruise not in ("—", "�", "0"):
        raise ResearchGap("Cruise EBIT needs a separate reconciled adjustment.")
    depreciation = [row for i, row in enumerate(rows) if row[0] == "Depreciation and amortization"
                    and len(row) == 10 and [ytd_header] in rows[max(0, i-3):i]]
    if len(depreciation) != 1:
        raise ResearchGap("GM's release did not uniquely disclose Automotive depreciation and amortization.")
    return {"balance": financials, "annual_ocf_guidance_millions": annual_ocf,
            "annual_capex_guidance_millions": annual_capex,
            "ytd_ocf_millions": _whole_number(rows[ytd_index+2][3]),
            "ytd_capex_millions": -_whole_number(rows[ytd_index+3][3]),
            "ytd_da_millions": _whole_number(depreciation[0][5]),
            "ytd_adjusted_auto_ebit_millions": total_ebit - gm_financial}

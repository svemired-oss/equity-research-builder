"""Reconcile terms of Oracle's concurrent note and common-stock offerings."""

from datetime import date
from decimal import Decimal
import re
from urllib.parse import urlsplit

from .gd_releases import _Page
from .sec_capital_events import _DATE, _MONTHS
from .sec_client import filing_document_url


_CIK = "1341439"
_FORMS = {"424B5", "424B2"}
_SHELF = r"333-\d+"
_NUMBER = r"\d{1,3}(?:,\d{3})+|\d+"
_CLOSING_SHELF = re.compile(
    rf"automatic shelf registration statement on Form S-3 \(File No\.\s*({_SHELF})\)"
    rf".{{0,250}}?related preliminary prospectus supplement dated ({_DATE}) and (?:a )?prospectus supplement dated ({_DATE})",
    re.IGNORECASE,
)
_REGISTRATION = re.compile(rf"\bRegistration No\.\s*({_SHELF})\b", re.IGNORECASE)
_PRELIMINARY = re.compile(r"\b(?:preliminary prospectus supplement|subject to completion)\b", re.IGNORECASE)
_DELIVERY = re.compile(
    rf"underwriters expect to deliver the (?:shares of common stock|Notes) to purchasers.{{0,220}}?on or about ({_DATE})",
    re.IGNORECASE,
)
_COVER_COMMON = re.compile(rf"\bWe are offering ({_NUMBER}) shares of our common stock\b", re.IGNORECASE)
_COVER_NOTE = re.compile(rf"\$({_NUMBER})\s+(\d+(?:\.\d+)?)% Notes due (20\d{{2}})\b", re.IGNORECASE)
_MATURITY = re.compile(rf"\b(?:the\s+)?(20\d{{2}}) Notes will mature on ({_DATE})\b", re.IGNORECASE)
_COUPONS = re.compile(
    rf"\bInterest on ((?:the\s+)?20\d{{2}} Notes.*?) will be payable semi-annually on "
    rf"({'|'.join(_MONTHS)}) (\d{{1,2}}) and ({'|'.join(_MONTHS)}) (\d{{1,2}}), commencing ({_DATE})\b",
    re.IGNORECASE,
)
_EQUITY_EXPENSES = re.compile(
    r"We estimate that our offering expenses, excluding underwriting discounts, will be approximately \$(\d+(?:\.\d+)?) million\b",
    re.IGNORECASE,
)
_NOTE_EXPENSES = re.compile(
    r"We estimate that our total expenses for this offering, other than the underwriting discount, will be approximately \$(\d+(?:\.\d+)?) million\b",
    re.IGNORECASE,
)
_PRO_FORMA_SHARES = re.compile(rf"\bCommon Stock to Be Outstanding After This Offering ({_NUMBER}) shares\b", re.IGNORECASE)
_OPENING_SHARES = re.compile(
    rf"will be outstanding after this offering is based on ({_NUMBER}) shares outstanding as of ({_DATE})",
    re.IGNORECASE,
)
_MILLION = 1_000_000


def _int(text: str) -> int:
    return int(text.replace(",", ""))


def _day(text: str) -> date:
    month, day, year = text.replace(",", "").split()
    return date(int(year), _MONTHS.index(month.capitalize()) + 1, int(day))


def _date_label(day: date) -> str:
    return f"{_MONTHS[day.month - 1]} {day.day}, {day.year}"


def _source(row: dict) -> dict:
    return {field: row[field] for field in ("accession", "filed_on", "url")}


def _page(client, row: dict) -> _Page:
    source = row["url"]
    name = urlsplit(source).path.rsplit("/", 1)[-1]
    if not name.lower().endswith((".htm", ".html")) or source != filing_document_url(_CIK, row["accession"], name):
        raise ValueError("Expected the canonical Oracle SEC filing URL.")
    return _Page(client.get_text(source))


def _table_rows(table):
    return [tuple(text for cell in row if (text := cell.text) and text != "$") for row in table.rows]


def _heading(text: str) -> str:
    return "".join(text.casefold().split())


def _find_value(values, error: str):
    distinct = set(values)
    if len(distinct) != 1:
        raise ValueError(error)
    return distinct.pop()


def _common_terms(page: _Page, closed: dict, balance: date) -> dict:
    shares = _find_value((_int(match[1]) for match in _COVER_COMMON.finditer(page.text[:2500])),
                         "No unique offered common-share count in the prospectus cover.")
    if shares != closed["shares_issued"]:
        raise ValueError("The offered common-share count differs from the closing 8-K.")
    amounts = {}
    labels = {
        "Price to Public": "gross_proceeds_usd",
        "Proceeds, before expenses, to Oracle Corporation": "proceeds_before_expenses_usd",
    }
    for table in page.tables:
        rows = _table_rows(table)
        if ("Per Share", "Total") not in rows:
            continue
        for row in rows:
            if len(row) == 3 and row[0] in labels and re.fullmatch(_NUMBER, row[2]):
                amounts.setdefault(labels[row[0]], set()).add(_int(row[2]))
    gross = _find_value(amounts.get("gross_proceeds_usd", ()), "No unique gross common proceeds.")
    proceeds = _find_value(amounts.get("proceeds_before_expenses_usd", ()), "No unique common proceeds before expenses.")
    if not 0 < proceeds <= gross:
        raise ValueError("Common proceeds failed the gross-to-net relationship.")
    result = {"shares_issued": shares, "gross_proceeds_usd": gross, "proceeds_before_expenses_usd": proceeds}
    if expense := _EQUITY_EXPENSES.search(page.text):
        expense_usd = int(Decimal(expense[1]) * _MILLION)
        if 0 <= expense_usd < proceeds:
            result["estimated_expenses_usd"] = expense_usd
            result["estimated_net_proceeds_usd"] = proceeds - expense_usd
    opening, pro_forma = _OPENING_SHARES.search(page.text), _PRO_FORMA_SHARES.search(page.text)
    if opening and pro_forma and _day(opening[2]) == balance:
        starting_shares, adjusted_shares = _int(opening[1]), _int(pro_forma[1])
        if starting_shares + shares != adjusted_shares:
            raise ValueError("Prospectus pro forma common shares do not reconcile to the issuance.")
        result.update(balance_date_basic_shares=starting_shares, pro_forma_basic_shares=adjusted_shares)
    return result


def _note_cover_proceeds(page: _Page, count: int) -> int:
    totals = []
    for table in page.tables:
        rows = _table_rows(table)
        if not any(any(_heading(cell) == "proceedsbeforeexpenses,tous" for cell in row) for row in rows):
            continue
        amounts = [
            tuple(map(_int, row[1:])) for row in rows
            if len(row) == 4 and row[0] == "Total" and all(re.fullmatch(_NUMBER, cell) for cell in row[1:])
        ]
        if len(amounts) != count + 1:
            continue
        if tuple(sum(row[col] for row in amounts[:-1]) for col in range(3)) != amounts[-1]:
            continue
        if any(gross - discount != proceeds for gross, discount, proceeds in amounts):
            continue
        totals.append(amounts[-1][2])
    return _find_value(totals, "No unique reconciled note proceeds table.")


def _capitalization(page: _Page, balance: date, tranches: tuple) -> dict:
    date_text = re.escape(_date_label(balance))
    if not re.search(rf"summary of our consolidated capitalization as of {date_text} on an actual", page.text, re.I):
        raise ValueError("The capitalization table does not refer to the reported balance date.")
    tables = []
    for table in page.tables:
        rows = _table_rows(table)
        if not any(len(row) == 4 and row[0].startswith("(in millions") and row[1:3] == ("Actual", "As Adjusted")
                   and _heading(row[3]) == "proformaasadjusted" for row in rows):
            continue
        rows = [row for row in rows if len(row) == 4 and all(re.fullmatch(_NUMBER, value) for value in row[2:])]
        debt = [tuple(map(_int, row[1:])) for row in rows if row[0] == "Total debt" and re.fullmatch(_NUMBER, row[1])]
        if len(debt) != 1:
            continue
        actual, adjusted, pro_forma = debt[0]
        delta = adjusted - actual
        if delta <= 0 or adjusted != pro_forma:
            continue
        net_amounts = {}
        for year, _, principal in tranches:
            row = [row for row in rows if re.fullmatch(rf"{year} Notes offered hereby(?:\s*\(1\))?", row[0])]
            if len(row) == 1 and row[0][1] in ("—", "–", "-", "0") and row[0][2] == row[0][3]:
                net = _int(row[0][2]) * _MILLION
                if 0 < net <= principal:
                    net_amounts[year] = net
        if len(net_amounts) != len(tranches) or sum(net_amounts.values()) != delta * _MILLION:
            continue
        tables.append((actual, adjusted, delta, tuple(net_amounts.items())))
    actual, adjusted, delta, per_note = _find_value(tables, "No unique reconciliation of carrying debt to issued notes.")
    return {
        "balance_date_debt_usd": actual * _MILLION,
        "as_adjusted_debt_usd": adjusted * _MILLION,
        "estimated_carrying_debt_increase_usd": delta * _MILLION,
        "tranches": [
            {"maturity_year": year, "coupon_percent": float(coupon), "principal_usd": principal,
             "estimated_carrying_value_usd": dict(per_note)[year]}
            for year, coupon, principal in tranches
        ],
    }


def _debt_terms(page: _Page, closed: dict, balance: date) -> dict:
    tranches = tuple(sorted({
        (int(match[3]), Decimal(match[2]), _int(match[1])) for match in _COVER_NOTE.finditer(page.text[:2500])
    }))
    closed_tranches = tuple(sorted(
        (row["maturity_year"], Decimal(str(row["coupon_percent"])), row["principal"]) for row in closed["tranches"]
    ))
    principal = sum(row[2] for row in tranches)
    if not tranches or tranches != closed_tranches or principal != closed["principal"]:
        raise ValueError("Prospectus note tranches do not match the closing 8-K.")
    proceeds = _note_cover_proceeds(page, len(tranches))
    if not 0 < proceeds <= principal:
        raise ValueError("Note proceeds failed the stated principal relationship.")
    result = {"principal_usd": principal, "proceeds_before_expenses_usd": proceeds, **_capitalization(page, balance, tranches)}
    _note_payment_dates(page.text[:12_000], result["tranches"])
    if expenses := _NOTE_EXPENSES.search(page.text):
        result["estimated_expenses_before_reimbursements_usd"] = int(Decimal(expenses[1]) * _MILLION)
    return result


def _note_payment_dates(cover: str, tranches: list[dict]) -> None:
    maturities, coupons = {}, {}
    for match in _MATURITY.finditer(cover):
        try:
            day = _day(match[2])
        except ValueError:
            continue
        if day.year == int(match[1]):
            maturities.setdefault(day.year, set()).add(day)
    for match in _COUPONS.finditer(cover):
        years = re.findall(r"(20\d{2}) Notes\b", match[1], re.IGNORECASE)
        try:
            first = _day(match[6])
            days = tuple(_day(f"{month} {day}, 2024").strftime("%m-%d")
                         for month, day in ((match[2], match[3]), (match[4], match[5])))
        except ValueError:
            continue
        if first.strftime("%m-%d") not in days:
            continue
        for year in years:
            coupons.setdefault(int(year), set()).add((first, days))
    for note in tranches:
        year = note["maturity_year"]
        maturity = maturities.get(year, set())
        if len(maturity) == 1:
            note["matures_on"] = next(iter(maturity)).isoformat()
        payments = coupons.get(year, set())
        if len(payments) == 1:
            first, days = next(iter(payments))
            if first.year < year or (first.year == year and "matures_on" in note
                                     and first <= date.fromisoformat(note["matures_on"])):
                note.update(first_coupon_on=first.isoformat(), coupon_month_days=list(days))


def collect_oracle_offerings(client, filings, capital_bridge: dict, *, as_of: date) -> dict:
    """Source one verified closing's terms and keep the cutoff cash bridge open."""
    result = {
        "status": "missing",
        "bridge_status": "not_reconciled",
        "balance_date": capital_bridge.get("balance_date"),
        "as_of": as_of.isoformat(),
        "currency": "USD",
        "equity": None,
        "debt": None,
        "missing": [],
        "limitations": [
            "The offering terms do not reconcile activity through the valuation cutoff.",
            "Actual expenses, cash receipts, cash use, other share movements and other debt movements require separate evidence.",
            "Pro forma balance-sheet amounts and estimated expenses retain their distinct bases.",
        ],
    }
    if not result["balance_date"]:
        result["missing"].append("No opening balance date was supplied.")
        return result
    balance = date.fromisoformat(result["balance_date"])
    by_kind = {}
    for event in capital_bridge["events"]:
        closed, filed = date.fromisoformat(event["closed_on"]), date.fromisoformat(event["filed_on"])
        if balance < closed <= filed <= as_of:
            by_kind.setdefault(event["kind"], []).append(event)
    equity, debt = by_kind.get("common_stock_issuance", []), by_kind.get("debt_issuance", [])
    if (len(equity) != 1 or len(debt) != 1
            or any(equity[0][field] != debt[0][field] for field in ("accession", "filed_on", "closed_on", "url"))):
        result["missing"].append("No unambiguous concurrent stock and note closing from a single Oracle 8-K.")
        return result
    try:
        closing = _page(client, equity[0])
        shelf, pricing = _find_value(
            ((match[1], _day(match[3])) for match in _CLOSING_SHELF.finditer(closing.text)
             if _day(match[2]) <= _day(match[3])),
            "No unique shelf number and pricing date in the closing 8-K.",
        )
        closed_on = date.fromisoformat(equity[0]["closed_on"])
        closing_filed = date.fromisoformat(equity[0]["filed_on"])
        if not balance < pricing <= closed_on:
            raise ValueError("The final prospectus date does not match the closing window.")
    except (ValueError, OSError) as exc:
        result["missing"].append(f"Oracle closing evidence unavailable. {exc}")
        return result
    result.update(shelf=shelf, priced_on=pricing.isoformat(), closed_on=closed_on.isoformat(), closing_source=_source(equity[0]))
    prospects = {form: {} for form in _FORMS}
    for row in filings:
        if row["form"] not in _FORMS:
            continue
        if pricing <= date.fromisoformat(row["filed_on"]) <= closing_filed:
            prospects[row["form"]][row["accession"]] = row
    for form, output, event, terms in (("424B5", "equity", equity[0], _common_terms), ("424B2", "debt", debt[0], _debt_terms)):
        matching = []
        for row in prospects[form].values():
            try:
                page = _page(client, row)
                header = page.text[:1500]
                registration = _REGISTRATION.search(header)
                delivery = _DELIVERY.search(page.text[:8000])
                if (not registration or registration[1] != shelf or _PRELIMINARY.search(header)
                        or not delivery or _day(delivery[1]) != closed_on):
                    continue
                matching.append({**terms(page, event, balance), "source": _source(row)})
            except (ValueError, OSError) as exc:
                result["missing"].append(f"{row['accession']}: {form} offering terms unavailable. {exc}")
        if len(matching) == 1:
            result[output] = matching[0]
        else:
            result["missing"].append(f"No unique final Oracle {form} supplement matched the verified closing.")
    if result["equity"] and result["debt"]:
        result["status"] = "available"
    elif result["equity"] or result["debt"]:
        result["status"] = "partial"
    return result

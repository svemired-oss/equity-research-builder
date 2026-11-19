"""Read narrow, explicit closing disclosures from post-balance SEC current reports."""

from datetime import date
from decimal import Decimal
import re

from .sec_guidance import _ReleaseText


_MONTHS = "January February March April May June July August September October November December".split()
_DATE = rf"(?:{'|'.join(_MONTHS)})\s+[1-9]\d?,\s+20\d{{2}}"
_COUNT = r"(?:[1-9]\d{0,2}(?:,\d{3})+|[1-9]\d*)"
_FILING = re.compile(
    r"https://www\.sec\.gov/Archives/edgar/data/[1-9]\d*/(?P<accession>\d{18})/"
    r"[A-Za-z0-9_][A-Za-z0-9_.-]*\.html?"
)
_SECTION = re.compile(r"\bItem\s+([1-9]\.[0-9]{2})\b", re.I)
_TABLE_BLOCK = re.compile(r"<(\/?)(?:table|tbody|thead|tr|td|th)(?=[\s>])", re.I)
_FINANCING_ITEMS = {"1.01", "2.03", "3.02", "8.01"}
_REPORT_LIMIT = 12
_READ_LIMIT = 512_000
_NOTE_ISSUANCE = re.compile(
    rf"(?:^|(?<=[.!?])\s+)On\s+(?P<closed>{_DATE}),\s+[^.!?]{{1,110}}?\s+"
    r"(?:consummated|completed)\s+the\s+issuance\s+and\s+sale\s+of\s+",
    re.I,
)
_NOTE = re.compile(
    rf"(?:US\s*)?\$\s*(?P<amount>{_COUNT}(?:\.\d+)?)\s*(?P<scale>million|billion)?\s*"
    r"(?:aggregate\s+)?principal\s+amount\s+of\s+(?:(?:its|the)\s+)?"
    r"(?P<coupon>\d{1,2}(?:\.\d+)?)\s*%\s+(?:senior\s+)?(?:unsecured\s+)?notes?\s+due\s+"
    r"(?P<maturity>20\d{2})\b",
    re.I,
)
_NEXT_NOTE = re.compile(r"(?:,\s*(?:and\s+)?|\s+and\s+)", re.I)
_END_NOTES = re.compile(r"\s*(?:$|[.(]|pursuant\s+to\b)", re.I)
_COMMON_OFFERED = re.compile(
    rf"\bagreed\s+to\s+issue\s+and\s+sell\s+(?P<shares>{_COUNT})\s+shares\s+of\s+"
    r"(?:(?:its|the\s+company's|our)\s+)?common\s+stock\b",
    re.I,
)
_COMMON_CLOSED = re.compile(
    rf"[.!?]\s+(?:the\s+)?(?:common\s+stock\s+)?offering\s+closed\s+on\s+(?P<closed>{_DATE})\b",
    re.I,
)
_COMMON_ISSUED = re.compile(
    rf"(?:^|(?<=[.!?])\s+)On\s+(?P<closed>{_DATE}),\s+[^.!?]{{1,110}}?\s+"
    rf"issued\s+and\s+sold\s+(?P<shares>{_COUNT})\s+shares\s+of\s+"
    r"(?:(?:its|the\s+company's|our)\s+)?common\s+stock\b",
    re.I,
)
_QUALIFIED = re.compile(r"\b(?:up\s+to|subject\s+to\s+closing|if\s+completed|could|would|secondary\s+offering|selling\s+stockholders?)\b", re.I)
_DENIAL = re.compile(r"^(?:403 Forbidden|404 Not Found|Access Denied|Your Request Originates)\b", re.I)
_SCALE = {"million": 10**6, "billion": 10**9}
_MONEY = rf"\$\s*(?P<amount>{_COUNT}(?:\.\d+)?)\s*(?P<scale>million|billion)?"
_PROCEEDS = re.compile(r"\bnet\s+proceeds[^.!?]{0,100}?" + _MONEY, re.I)
_REPAID = re.compile(
    rf"(?:^|(?<=[.!?])\s+)On\s+(?P<closed>{_DATE}),\s+[^.!?]{{1,100}}?\brepaid\s+"
    + _MONEY + r"\s+(?:(?:of|in)\s+)?(?:outstanding\s+|aggregate\s+)?principal\b", re.I)
_REPURCHASED = re.compile(
    rf"(?:^|(?<=[.!?])\s+)On\s+(?P<closed>{_DATE}),\s+[^.!?]{{1,100}}?\brepurchased\s+"
    rf"(?P<shares>{_COUNT}(?:\.\d+)?)\s*(?P<share_scale>million|billion)?\s+shares\b"
    r"[^.!?]{0,120}?\bfor\s+(?:an?\s+aggregate\s+(?:purchase\s+)?price\s+of\s+)?"
    + _MONEY, re.I)


def _money(match):
    value = Decimal(match["amount"].replace(",", "")) * _SCALE.get((match["scale"] or "").lower(), 1)
    return int(value) if value == value.to_integral_value() else None


def _net_proceeds(block):
    values = {_money(match) for match in _PROCEEDS.finditer(block)}
    return next(iter(values)) if len(values) == 1 else None


def _closing_date(match) -> date:
    month, day, year = match["closed"].replace(",", "").split()
    return date(int(year), _MONTHS.index(month.capitalize()) + 1, int(day))


def _debt_issuance(block, start):
    tranches = []
    end = start.end()
    while (note := _NOTE.match(block, end)) is not None:
        face = Decimal(note["amount"].replace(",", "")) * _SCALE.get((note["scale"] or "").lower(), 1)
        if face != face.to_integral_value():
            return None
        tranches.append({"principal": int(face), "coupon_percent": float(note["coupon"]),
                         "maturity_year": int(note["maturity"])})
        end = note.end()
        delimiter = _NEXT_NOTE.match(block, end)
        if delimiter is None:
            break
        end = delimiter.end()
    if not tranches or not _END_NOTES.match(block, end):
        return None
    return {
        "kind": "debt_issuance", "closed_on": _closing_date(start).isoformat(),
        "principal": sum(row["principal"] for row in tranches), "currency": "USD",
        "currency_basis": "Dollar amount in Form 8-K", "tranches": tranches,
        "evidence": block[start.start():end].strip(),
    }


def _common_issuance(block):
    issued = _COMMON_ISSUED.search(block)
    if issued:
        return issued, issued, block[issued.start():issued.end()].strip()
    offered = _COMMON_OFFERED.search(block)
    closed = _COMMON_CLOSED.search(block, offered.end()) if offered else None
    if closed and closed.start() - offered.end() <= 260:
        return offered, closed, block[offered.start():closed.end()]
    return None


def _read_events(html):
    # Current reports often present item headings in table cells.
    text = _TABLE_BLOCK.sub(r"<\1div", html[:_READ_LIMIT])
    blocks = _ReleaseText(text).blocks
    if not blocks or _DENIAL.search(blocks[0]):
        raise ValueError("No readable SEC filing body.")
    item = None
    for block in blocks:
        section = _SECTION.match(block)
        if section:
            item = section[1]
        if item not in _FINANCING_ITEMS or _QUALIFIED.search(block):
            continue
        for start in _NOTE_ISSUANCE.finditer(block):
            try:
                if debt := _debt_issuance(block, start):
                    if (proceeds := _net_proceeds(block)) is not None:
                        debt["net_proceeds"] = proceeds
                    yield debt
            except ValueError:
                continue
        if (common := _common_issuance(block)) is not None:
            share_match, closing_match, evidence = common
            try:
                event = {"kind": "common_stock_issuance", "closed_on": _closing_date(closing_match).isoformat(),
                         "shares_issued": int(share_match["shares"].replace(",", "")), "evidence": evidence, "currency": "USD"}
                if (proceeds := _net_proceeds(block)) is not None:
                    event["net_proceeds"] = proceeds
                yield event
            except ValueError:
                continue
        for repaid in _REPAID.finditer(block):
            amount = _money(repaid)
            if amount is not None:
                yield {"kind": "debt_repayment", "closed_on": _closing_date(repaid).isoformat(),
                       "principal": amount, "cash_paid": amount, "currency": "USD",
                       "evidence": repaid[0], "basis": "Disclosed principal repayment, excluding unquantified interest or fees"}
        for repurchased in _REPURCHASED.finditer(block):
            shares = Decimal(repurchased["shares"].replace(",", "")) * _SCALE.get(
                (repurchased["share_scale"] or "").lower(), 1)
            amount = _money(repurchased)
            if amount is not None and shares == shares.to_integral_value():
                yield {"kind": "share_repurchase", "closed_on": _closing_date(repurchased).isoformat(),
                       "shares_repurchased": int(shares), "cash_paid": amount, "currency": "USD",
                       "evidence": repurchased[0]}


def _event_key(event):
    tranches = tuple(sorted((row["maturity_year"], row["coupon_percent"], row["principal"])
                            for row in event.get("tranches", [])))
    return (event["kind"], event["closed_on"], event.get("shares_issued"), tranches,
            event.get("principal"), event.get("cash_paid"), event.get("shares_repurchased"))


def collect_capital_events(client, current_reports, *, balance_date: str | None, as_of: date) -> dict:
    """Return closure evidence without estimating cash, carrying debt, or current shares."""
    result = {"balance_date": balance_date, "status": "not_reconciled" if balance_date else "balance_unavailable",
              "events": [], "missing": [], "unparsed_reports": []}
    if not balance_date:
        return result
    balance = date.fromisoformat(balance_date)
    eligible = [row for row in current_reports if balance < date.fromisoformat(row["filed_on"]) <= as_of]
    eligible.sort(key=lambda row: (row["filed_on"], row["accession"]), reverse=True)
    events, seen = {}, set()
    for filing in eligible:
        accession = filing["accession"]
        if accession in seen:
            continue
        if len(seen) == _REPORT_LIMIT:
            result["missing"].append("Older current reports were outside the financing extraction limit.")
            break
        seen.add(accession)
        if filing["form"] not in {"8-K", "8-K/A"}:
            result["unparsed_reports"].append(accession)
            continue
        source = filing["url"]
        canonical = _FILING.fullmatch(source)
        if not canonical or canonical["accession"] != accession.replace("-", ""):
            result["missing"].append(f"{accession}: expected a same-accession SEC filing document.")
            continue
        try:
            records = list(_read_events(client.get_text(source)))
        except (OSError, ValueError):
            result["missing"].append(f"{accession}: SEC filing unavailable for financing extraction.")
            continue
        closed = [row for row in records if balance_date < row["closed_on"] <= filing["filed_on"]]
        if not closed:
            result["unparsed_reports"].append(accession)
        for event in closed:
            events.setdefault((accession, _event_key(event)), {**event, "filed_on": filing["filed_on"],
                                                 "accession": accession, "url": source})
    result["events"] = list(events.values())
    return result

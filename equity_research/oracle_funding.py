"""Read Oracle's published lease and borrowing payment schedules."""

from datetime import date
from math import fsum
import re

from .gd_releases import _Page


_YEAR = re.compile(r"(Remainder of )?[Ff]iscal (20\d{2})$")
_NUMBER = re.compile(r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)$")


def _rows(table):
    rows = []
    for row in table.rows:
        cells = []
        for cell in row:
            value = cell.text
            if not value or value == "$":
                continue
            if value == ")" and cells and cells[-1].startswith("("):
                cells[-1] += value
            else:
                cells.append(value)
        rows.append(tuple(cells))
    return rows


def _numeric_row(row, width):
    return len(row) == width + 1 and all(_NUMBER.fullmatch(value) for value in row[1:])


def _numbers(row):
    return tuple(int(value.replace(",", "")) for value in row[1:])


def _dated_rows(rows):
    return [(int(match[2]), bool(match[1]), _numbers(row)) for row in rows if (match := _YEAR.fullmatch(row[0]))]


def _consecutive(periods, first_fiscal_year, first_is_stub):
    return (
        bool(periods) and periods[0][:2] == (first_fiscal_year, first_is_stub)
        and not any(stub for _, stub, _ in periods[1:])
        and all(following[0] == prior[0] + 1 for prior, following in zip(periods, periods[1:]))
    )


def _lease_table(page, period_end):
    first_year = period_end.year + (period_end.month >= 6 or (period_end.month, period_end.day) == (5, 31))
    first_is_stub = (period_end.month, period_end.day) != (5, 31)
    schedules = set()
    for table in page.tables:
        rows = _rows(table)
        if not any(row == ("Operating Leases", "Finance Leases") for row in rows):
            continue
        amounts = [
            (row[0], *(value[1:-1] if value.startswith("(") and value.endswith(")") else value for value in row[1:]))
            if row and row[0] == "Less: imputed interest" else row
            for row in rows
        ]
        amounts = [row for row in amounts if _numeric_row(row, 2)]
        if len(amounts) < 6:
            continue
        if [row[0] for row in amounts[-4:]] != [
            "Thereafter", "Total lease payments", "Less: imputed interest", "Total lease liability",
        ]:
            continue
        periods = _dated_rows(amounts[:-4])
        if len(periods) != len(amounts) - 4 or not _consecutive(periods, first_year, first_is_stub):
            continue
        thereafter, total, interest, liability = map(_numbers, amounts[-4:])
        if any(sum(row[2][i] for row in periods) + thereafter[i] != total[i]
               or total[i] - interest[i] != liability[i] for i in range(2)):
            continue
        schedules.add((tuple(periods), thereafter, total, interest, liability))
    if len(schedules) != 1:
        raise ValueError("No unique reconciled lease maturity table for the reported period.")
    periods, thereafter, total, interest, liability = next(iter(schedules))
    return {
        "periods": [{"fiscal_year": year, "is_remainder": stub, "operating_lease_payments_millions": values[0],
                     "finance_lease_payments_millions": values[1]} for year, stub, values in periods],
        "thereafter_millions": {"operating": thereafter[0], "finance": thereafter[1]},
        "total_payments_millions": {"operating": total[0], "finance": total[1]},
        "imputed_interest_millions": {"operating": interest[0], "finance": interest[1]},
        "carrying_liabilities_millions": {"operating": liability[0], "finance": liability[1]},
    }


def _debt_table(page, period_end, face_amount):
    schedules = set()
    for table in page.tables:
        amounts = [row for row in _rows(table) if _numeric_row(row, 1)]
        if len(amounts) < 6 or [row[0] for row in amounts[-2:]] != ["Thereafter", "Total"]:
            continue
        periods = _dated_rows(amounts[:-2])
        if len(periods) != len(amounts) - 2 or not _consecutive(periods, period_end.year + 1, False):
            continue
        (thereafter,), (total,) = map(_numbers, amounts[-2:])
        if sum(row[2][0] for row in periods) + thereafter == total and total == face_amount:
            schedules.add((tuple(periods), thereafter, total))
    if len(schedules) != 1:
        raise ValueError("No unique reconciled annual borrowing principal maturity table.")
    periods, thereafter, total = next(iter(schedules))
    return {
        "periods": [{"fiscal_year": year, "principal_millions": values[0]} for year, _, values in periods],
        "thereafter_millions": thereafter,
        "total_principal_millions": total,
    }


def _check_reported_liabilities(schedule, period):
    metrics = period["metrics"]
    for lease_type in ("operating", "finance"):
        current = metrics[f"{lease_type}_lease_current"]
        noncurrent = metrics[f"{lease_type}_lease_noncurrent"]
        if current is None or noncurrent is None:
            raise ValueError(f"The reported {lease_type} lease liability is unavailable for reconciliation.")
        if abs(schedule["carrying_liabilities_millions"][lease_type] - (current + noncurrent) / 1_000_000) > 1:
            raise ValueError(f"The {lease_type} lease schedule differs from the reported balance.")


def collect_oracle_funding_schedules(client, packet: dict) -> dict:
    """Return dated schedules on their own accounting and reporting bases."""
    annual = packet["actuals"]["periods"][-1:]
    latest = packet["interim_actuals"]["periods"][-1:] or annual
    result = {
        "status": "missing", "leases": None, "annual_debt_principal": None, "missing": [],
        "limitations": [
            "Lease payments are undiscounted and include interest. The schedule only covers commenced leases at the reporting date.",
            "The annual debt schedule is gross principal before subsequent repayments, issuances and refinancing.",
        ],
    }
    pages = {}
    for key, periods in (("leases", latest), ("annual_debt_principal", annual)):
        if not periods:
            result["missing"].append(f"No eligible reporting period for {key}.")
            continue
        period = periods[0]
        filing = period["filing"]
        url = filing["url"]
        try:
            if url not in pages:
                pages[url] = _Page(client.get_text(url))
            end = date.fromisoformat(period["period_end"])
            if key == "leases":
                schedule = _lease_table(pages[url], end)
                _check_reported_liabilities(schedule, period)
            else:
                source = period["sources"].get("debt_instrument_face_amount", {})
                face_amount = period["metrics"].get("debt_instrument_face_amount")
                if face_amount is None or source.get("accession") != filing["accession"]:
                    raise ValueError("No same-filing total face amount for the borrowing principal schedule.")
                schedule = _debt_table(pages[url], end, face_amount / 1_000_000)
            result[key] = {**schedule, "period_end": period["period_end"], "source": filing}
        except (ValueError, OSError) as exc:
            result["missing"].append(f"{key}: {exc}")
    if result["leases"] or result["annual_debt_principal"]:
        result["status"] = "partial" if result["missing"] else "available"
    return result


def oracle_issued_note_payments(offering: dict, *, as_of: date, through: date) -> tuple[dict, ...]:
    """Schedule contractual coupons and maturities for the matched November issuance."""
    if type(as_of) is not date or type(through) is not date or through < as_of:
        raise ValueError("Supply a valid payment window.")
    if offering.get("status") != "available":
        raise ValueError("Verified note offering terms are required.")
    closed = date.fromisoformat(offering["closed_on"])
    if as_of < closed:
        raise ValueError("The note offering had not closed by the payment-window cutoff.")
    debt = offering["debt"]
    if sum(tranche["principal_usd"] for tranche in debt["tranches"]) != debt["principal_usd"]:
        raise ValueError("The issued tranches do not reconcile to total principal.")
    payments = {}
    for tranche in debt["tranches"]:
        if not {"matures_on", "first_coupon_on", "coupon_month_days"} <= tranche.keys():
            raise ValueError("The verified note terms lack maturity or coupon payment dates.")
        maturity = date.fromisoformat(tranche["matures_on"])
        coupon_date = date.fromisoformat(tranche["first_coupon_on"])
        if not closed < coupon_date <= maturity:
            raise ValueError("The first coupon must fall after closing and by maturity.")
        scheduled = {(int(month), int(day)) for month, day in
                     (label.split("-") for label in tranche["coupon_month_days"])}
        if (len(scheduled) != 2 or (coupon_date.month, coupon_date.day) not in scheduled
                or (maturity.month, maturity.day) not in scheduled):
            raise ValueError("Missing or invalid semiannual coupon calendar.")
        prior_coupon = closed
        for year in range(coupon_date.year, min(maturity, through).year + 1):
            for month, day in sorted(scheduled):
                payment_date = date(year, month, day)
                if not coupon_date <= payment_date <= min(maturity, through):
                    continue
                # Both issue and coupon dates are the 4th. The signed terms use 30/360.
                if prior_coupon.day > 28 or payment_date.day > 28:
                    raise ValueError("Unsupported day for the Oracle note coupon convention.")
                elapsed_days = ((payment_date.year - prior_coupon.year) * 360
                                + (payment_date.month - prior_coupon.month) * 30
                                + payment_date.day - prior_coupon.day)
                face = tranche["principal_usd"] / 1_000_000
                coupon = face * tranche["coupon_percent"] / 100 * elapsed_days / 360
                prior_coupon = payment_date
                if payment_date > as_of:
                    payments.setdefault(payment_date, []).append((coupon, face if payment_date == maturity else 0))
    return tuple({"payment_date": day.isoformat(), "coupon_millions": fsum(row[0] for row in amounts),
                  "principal_millions": fsum(row[1] for row in amounts)} for day, amounts in sorted(payments.items()))


def check_existing_lease_cash(leases: dict, elapsed, forecast) -> list[dict]:
    """Check cash-flow inputs against the payments on leases already in service."""
    filed = {row["fiscal_year"]: row for row in leases["periods"]}
    checks = []
    for index, period in enumerate(forecast):
        year = period.period_end.year
        if year not in filed:
            continue
        source = filed[year]
        intervals = (elapsed, period) if index == 0 else (period,)
        operating = fsum(item.operating_lease.cash_rent_millions for item in intervals)
        finance_principal = fsum(item.debt.finance_lease_principal_paid_millions for item in intervals)
        total_interest = fsum(item.debt.cash_interest_millions for item in intervals)
        # Total cash interest gives only an upper bound on finance lease interest.
        # Use it to reject impossible payments, without implying a verified split.
        for name, modeled, minimum in (
            ("operating_cash_rent", operating, source["operating_lease_payments_millions"]),
            ("finance_principal_plus_all_cash_interest", finance_principal + total_interest,
             source["finance_lease_payments_millions"]),
        ):
            if modeled < minimum - 1:
                raise ValueError(f"FY{year} {name} falls below the filed payments for existing leases.")
            checks.append({"fiscal_year": year, "check": name, "modeled_millions": modeled,
                           "filed_minimum_millions": minimum})
    return checks

"""Map GD's explicitly disclosed annual segment history to its model rows."""

import re
from collections import defaultdict
from datetime import date
from math import isfinite
from typing import TYPE_CHECKING

from .gd_actuals import SourcedValue
from .gd_inputs import InputSource
from .gd_releases import _Page

if TYPE_CHECKING:
    from .filing_xbrl import FilingInstance


SEGMENT_AXIS = "us-gaap:StatementBusinessSegmentsAxis"
CONSOLIDATION_AXIS = "srt:ConsolidationItemsAxis"
OPERATING_SEGMENTS = (CONSOLIDATION_AXIS, "us-gaap:OperatingSegmentsMember")
CORPORATE = (CONSOLIDATION_AXIS, "us-gaap:CorporateNonSegmentMember")
REVENUE = "us-gaap:RevenueFromContractWithCustomerExcludingAssessedTax"
EARNINGS = "us-gaap:OperatingIncomeLoss"

# Label, member, and workbook rows for the segment build and its summary aliases.
SEGMENTS = (
    ("Aerospace", "gd:AerospaceMember", (10, 60), (19, 69)),
    ("Marine Systems", "gd:MarineSystemsMember", (26, 61), (31, 70)),
    ("Combat Systems", "gd:CombatSystemsMember", (39, 62), (45, 71)),
    ("Technologies", "gd:TechnologiesMember", (51, 63), (56, 72)),
)

# Exact labels from GD's major products and services tables. A singular/plural
# difference in the weapons label and the abbreviation IT occur across filings.
PRODUCT_GROUPS = (
    (("aircraft manufacturing",), ("aircraft services",), ("total aerospace",)),
    (("nuclear-powered submarines",), ("surface ships",), ("repair and other services",), ("total marine systems",)),
    (("military vehicles",), ("weapon systems and munitions", "weapons systems and munitions"),
     ("engineering and other services",), ("total combat systems",)),
    (("it services", "information technology (it) services"), ("c5isr solutions", "c5isr* solutions"),
     ("total technologies",)),
)
PRODUCT_ROWS = ((8, 9), (23, 24, 25), (36, 37, 38), (49, 50))


def _period(year: int, column: str, filing_date: date) -> tuple[date, date]:
    if column not in ("B", "C", "D", "E", "F"):
        raise ValueError("GD segment history must use a historical column B through F.")
    start, end = date(year, 1, 1), date(year, 12, 31)
    if filing_date < end:
        raise ValueError("Annual segment history must be from a filing available at or after the fiscal year-end.")
    return start, end


def _emit(
    values: set[float], rows: tuple[int, ...], label: str, column: str,
    start: date, end: date, accession: str, filing_date: date, url: str, taxonomy: str, tag: str,
    facts: dict[str, SourcedValue], missing: dict[str, str],
) -> None:
    if len(values) == 1:
        source = InputSource(
            kind="sec_filing",
            citation=f"General Dynamics Form 10-K, FY{end.year} {label}. Accession {accession}.",
            available_on=filing_date,
            url=url,
        )
        value = SourcedValue(next(iter(values)), source, end, accession, "10-K", taxonomy, tag, start)
        facts.update((f"Revenue model!{column}{row}", value) for row in rows)
        return
    reason = "Conflicting" if values else "Unavailable"
    missing.update((f"Revenue model!{column}{row}", f"{reason} FY{end.year} {label} in filing {accession}.") for row in rows)


def map_gd_segment_history(
    instance: "FilingInstance", *, year: int, column: str, accession: str, filing_date: date,
) -> tuple[dict[str, SourcedValue], dict[str, str]]:
    """Read annual USD facts in GD's disclosed segment and corporate contexts."""
    start, end = _period(year, column, filing_date)
    amounts = defaultdict(set)
    for fact in instance.facts:
        if fact.start == start and fact.end == end and fact.unit == "USD" and isfinite(fact.value):
            amounts[(fact.concept, fact.dimensions)].add(fact.value / 1_000_000)

    facts: dict[str, SourcedValue] = {}
    missing: dict[str, str] = {}
    mappings = []
    for label, member, revenue_rows, earnings_rows in SEGMENTS:
        segment = (SEGMENT_AXIS, member)
        contexts = ((segment,), (OPERATING_SEGMENTS, segment))
        mappings.extend((
            (f"{label} revenue", contexts, revenue_rows, REVENUE),
            (f"{label} operating earnings", contexts, earnings_rows, EARNINGS),
        ))
    corporate_contexts = (((SEGMENT_AXIS, "us-gaap:CorporateMember"),), (CORPORATE,))
    mappings.append(("Corporate operating earnings", corporate_contexts, (73,), EARNINGS))
    for label, contexts, rows, concept in mappings:
        values: set[float] = set()
        scopes = []
        for context in contexts:
            observed = amounts.get((concept, tuple(sorted(context))))
            if observed:
                values.update(observed)
                scopes.append(", ".join(f"{axis} = {member}" for axis, member in context))
        citation = f"{label} ({concept}, {' or '.join(scopes)})" if scopes else label
        _emit(
            values, rows, citation, column,
            start, end, accession, filing_date, instance.url, "us-gaap", concept.split(":", 1)[1], facts, missing,
        )
    return facts, missing


def _label(text: str) -> str:
    return re.sub(r"\s*\([a-z]\)$", "", text.casefold())


def _rows(html: str) -> tuple[tuple[tuple[str, ...], ...], ...]:
    tables = []
    for table in _Page(html).tables:
        rows = []
        for cells in table.rows:
            row = tuple(text for cell in cells if (text := cell.text) and text != "$")
            if row:
                rows.append(row)
        tables.append(tuple(rows))
    return tuple(tables)


def _years(row: tuple[str, ...]) -> tuple[int, ...]:
    if not row or _label(row[0]) != "year ended december 31" or not all(
        re.fullmatch(r"20[0-9]{2}", cell) for cell in row[1:]
    ):
        return ()
    return tuple(int(cell) for cell in row[1:])


def _usd_millions(text: str) -> int:
    if text in ("—", "–", "-"):
        return 0
    negative = text.startswith("(") and text.endswith(")")
    unsigned = text[1:-1].strip() if negative else text
    if re.fullmatch(r"(?:\d{1,3}(?:,\d{3})+|\d+)", unsigned):
        number = int(unsigned.replace(",", ""))
        return -number if negative else number
    raise ValueError(f"Unexpected USD millions in GD's annual table: {text!r}.")


def _matrix(rows: tuple[tuple[str, ...], ...], width: int) -> tuple[tuple[int, ...], ...]:
    if any(len(row) != width + 1 for row in rows):
        return ()
    try:
        return tuple(tuple(_usd_millions(value) for value in row[1:]) for row in rows)
    except ValueError:
        return ()


def _record_products(
    table: tuple[tuple[str, ...], ...], year: int, target: dict[int, set[float]],
) -> None:
    for header, row in enumerate(table):
        years = _years(row)
        if not 2 <= len(years) <= 3 or year not in years or any(
            current - previous != 1 for current, previous in zip(years, years[1:])
        ):
            continue
        column = years.index(year)
        # The annual report repeats these groups in Item 1 and the revenue note.
        for group, rows, segment in zip(PRODUCT_GROUPS, PRODUCT_ROWS, SEGMENTS, strict=True):
            for start in range(header + 1, len(table) - len(group) + 1):
                block = table[start:start + len(group)]
                if not all(_label(record[0]) in labels for record, labels in zip(block, group, strict=True)):
                    continue
                matrix = _matrix(block, len(years))
                if not matrix or any(sum(cells[:-1]) != cells[-1] for cells in zip(*matrix)):
                    continue
                row_groups = tuple((row,) for row in rows) + (segment[2],)
                for workbook_rows, amounts in zip(row_groups, matrix, strict=True):
                    for workbook_row in workbook_rows:
                        target[workbook_row].add(float(amounts[column]))


def _record_segment_note(
    table: tuple[tuple[str, ...], ...], year: int, target: dict[int, set[float]],
) -> None:
    labels = (*(_label(segment[0]) for segment in SEGMENTS), "corporate", "total")
    for header, row in enumerate(table[:-1]):
        headings = tuple(map(_label, row))
        with_costs = headings == ("revenue", "other segment items", "operating earnings")
        with_government_revenue = (
            len(headings) == 3 and headings[:2] == ("revenue", "operating earnings")
            and headings[2].replace(" ", "") == "revenuefromu.s.government"
        )
        if not (with_costs or with_government_revenue):
            continue
        years = _years(table[header + 1])
        if len(years) != 9 or years[:3] * 3 != years or year not in years[:3] or any(
            current - previous != 1 for current, previous in zip(years[:2], years[1:3])
        ):
            continue
        block = table[header + 2:header + 8]
        if tuple(_label(record[0]) for record in block) != labels:
            continue
        matrix = _matrix(block, 9)
        if not matrix or any(sum(cells[:-1]) != cells[-1] for cells in zip(*matrix)):
            continue
        if with_costs and any(
            values[i] + values[i + 3] != values[i + 6] for values in matrix for i in range(3)
        ):
            continue
        column = years.index(year)
        earnings_column = column + (6 if with_costs else 3)
        for amounts, segment in zip(matrix[:4], SEGMENTS, strict=True):
            for rows, value in ((segment[2], amounts[column]), (segment[3], amounts[earnings_column])):
                for row_number in rows:
                    target[row_number].add(float(value))
        target[73].add(float(matrix[4][earnings_column]))


def map_gd_segment_tables(
    html: str, url: str, *, year: int, column: str, accession: str, filing_date: date,
) -> tuple[dict[str, SourcedValue], dict[str, str]]:
    """Read unambiguous annual disclosures from GD's official 10-K tables."""
    start, end = _period(year, column, filing_date)
    amounts: dict[int, set[float]] = defaultdict(set)
    for table in _rows(html):
        _record_products(table, year, amounts)
        _record_segment_note(table, year, amounts)

    facts: dict[str, SourcedValue] = {}
    missing: dict[str, str] = {}
    mappings = [
        ((row,), f"{labels[0]} revenue", "annual product and service revenue table")
        for group, rows in zip(PRODUCT_GROUPS, PRODUCT_ROWS, strict=True)
        for labels, row in zip(group[:-1], rows, strict=True)
    ]
    for label, _, revenue_rows, earnings_rows in SEGMENTS:
        mappings.extend((
            (revenue_rows, f"{label} revenue", "annual segment revenue table"),
            (earnings_rows, f"{label} operating earnings", "annual segment operating earnings table"),
        ))
    mappings.append(((73,), "Corporate operating earnings", "annual segment operating earnings table"))
    for rows, label, tag in mappings:
        _emit(amounts.get(rows[0], set()), rows, label, column, start, end, accession, filing_date,
              url, "issuer_table", tag, facts, missing)
    return facts, missing

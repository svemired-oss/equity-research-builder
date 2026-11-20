"""Evidence-ranked revenue bases for filings whose tagged lines do not reconcile directly.

Tried in order, only after the exact reconciliation in ``revenue_candidates`` fails:

1. Exact partitions found inside one dimensional population. Gross (operating
   segments qualified) and net segment facts are evaluated separately, as are facts
   of different revenue concepts. Nested parent and child members resolve to the
   finest partition only when every other reconciling partition is a strict
   coarsening of it. One reportable segment may pair with the filed corporate line.
2. Filed lines plus one derived reconciling line, consolidated revenue less the sum
   of the filed lines. The line must have the same sign in both years and be no
   more than 5% of consolidated revenue in each year.
3. Consolidated revenue as a single line.

Every fallback candidate carries a ``basis`` note for the workbook's method basis.
"""

from itertools import combinations
from math import isclose

_GROUPS = ("segment_revenue", "product_or_service_revenue")
_OPERATING_SEGMENTS = ("srt:ConsolidationItemsAxis", "us-gaap:OperatingSegmentsMember")
_CORPORATE = "CorporateNonSegmentMember"
RECONCILING_LINE = "OtherAndReconcilingRevenueMember"
CONSOLIDATED_LINE = "ConsolidatedRevenue"
RESIDUAL_LIMIT = .05
_TOLERANCE = 1  # USD millions, matching the exact reconciliation.
_MAX_SEARCH_MEMBERS = 16
_MAX_MATCHES = 64


def _member(dimension):
    return dimension["member"].rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def _observations(prior, latest, facts):
    """Yield (group, population, concept, end, member, value) in USD millions."""
    ends = (prior["period_end"], latest["period_end"])
    for fact in facts:
        group = fact.get("metric")
        if (group not in (*_GROUPS, "corporate_nonsegment_revenue") or fact.get("unit") != "USD"
                or fact.get("accession") != latest["filing"]["accession"] or fact.get("period_end") not in ends
                or not isinstance(fact.get("dimensions"), list)):
            continue
        dimensions = [item for item in fact["dimensions"] if (item.get("axis"), item.get("member")) != _OPERATING_SEGMENTS]
        if len(dimensions) != 1:
            continue
        population = "gross" if len(dimensions) != len(fact["dimensions"]) else "net"
        member = _CORPORATE if group == "corporate_nonsegment_revenue" else _member(dimensions[0])
        yield group, population, fact.get("concept"), fact["period_end"], member, fact["value"] / 1e6


def _pools(prior, latest, facts, concept):
    """Return (group, label, old, new, corporate) pools in decreasing evidence order."""
    rows = list(_observations(prior, latest, facts))
    concepts = [concept] if concept else []
    concepts += sorted({row[2] for row in rows if row[2] and row[2] != concept})
    variants = [(c, p) for c in (*concepts, None) for p in ("net", "gross", None)]
    pools = []
    for group in _GROUPS:
        for wanted_concept, wanted_population in variants:
            values = {}
            corporate = {}
            for row_group, population, row_concept, end, member, value in rows:
                if wanted_concept is not None and row_concept != wanted_concept:
                    continue
                if row_group == "corporate_nonsegment_revenue":
                    corporate.setdefault(end, set()).add(value)
                    continue
                if row_group != group or (wanted_population is not None and population != wanted_population):
                    continue
                values.setdefault(member, {}).setdefault(end, set()).add(value)
            old, new = {}, {}
            ambiguous = False
            for member, by_end in values.items():
                before, after = by_end.get(prior["period_end"]), by_end.get(latest["period_end"])
                if not before or not after:
                    continue  # Present in one year only. A reconciling line can absorb it.
                if len(before) != 1 or len(after) != 1:
                    ambiguous = True
                    break
                old[member], new[member] = next(iter(before)), next(iter(after))
            if ambiguous or not old or any(value <= 0 for rows_ in (old, new) for value in rows_.values()):
                continue
            pair = None
            if group == "segment_revenue":
                before, after = corporate.get(prior["period_end"], set()), corporate.get(latest["period_end"], set())
                if len(before) == len(after) == 1 and min(before | after) > 0:
                    pair = (next(iter(before)), next(iter(after)))
                    # A corporate segment member already presents the same amounts.
                    if any(isclose(old[key], pair[0], abs_tol=_TOLERANCE) and isclose(new[key], pair[1], abs_tol=_TOLERANCE)
                           for key in old):
                        pair = None
            label = " ".join(filter(None, (wanted_concept and wanted_concept.rpartition(":")[2],
                                           "facts", wanted_population == "gross" and "before intersegment eliminations")))
            label = "" if label == "facts" else label
            pools.append((group, label, old, new, pair))
    return pools


def _close(left, right):
    return isclose(left, right, rel_tol=0, abs_tol=_TOLERANCE)


def _covers(parents, children, old, new):
    """True when the children split exactly into one group per parent in both years."""
    if not parents:
        return not children
    head, rest = parents[0], parents[1:]
    for size in range(1, len(children) + 1):
        for combo in combinations(children, size):
            if (_close(old[head], sum(old[key] for key in combo)) and _close(new[head], sum(new[key] for key in combo))
                    and _covers(rest, [key for key in children if key not in combo], old, new)):
                return True
    return False


def _exact_partition(old, new, prior_total, latest_total):
    keys = sorted(old)
    if not 2 <= len(keys) <= _MAX_SEARCH_MEMBERS:
        return None
    matched = []
    for size in range(2, len(keys) + 1):
        for combo in combinations(keys, size):
            if _close(sum(old[key] for key in combo), prior_total) and _close(sum(new[key] for key in combo), latest_total):
                matched.append(combo)
                if len(matched) > _MAX_MATCHES:
                    return None
    if not matched:
        return None
    finest = max(matched, key=len)
    if sum(len(combo) == len(finest) for combo in matched) > 1:
        return None
    for combo in matched:
        if combo is finest:
            continue
        parents = sorted(set(combo) - set(finest))
        children = sorted(set(finest) - set(combo))
        if not parents or not _covers(parents, children, old, new):
            return None
    return finest, len(matched) > 1


def _describe(group):
    return "segment" if group == "segment_revenue" else "product or service"


def fallback_revenue_candidates(prior, latest, facts, concept=None):
    prior_total, latest_total = (period["metrics"].get("revenue") for period in (prior, latest))
    if prior_total is None or latest_total is None or prior_total <= 0 or latest_total <= 0:
        return {}
    prior_total, latest_total = prior_total / 1e6, latest_total / 1e6
    pools = _pools(prior, latest, facts, concept)
    exact, residual = {}, {}
    for group, label, old, new, corporate in pools:
        if group in exact:
            continue
        lines = [(old, new, False)]
        if corporate:
            lines.insert(0, ({**old, _CORPORATE: corporate[0]}, {**new, _CORPORATE: corporate[1]}, True))
        for before, after, with_corporate in lines:
            found = _exact_partition(before, after, prior_total, latest_total)
            if found is None:
                continue
            keys, nested = found
            notes = [f"Filed {_describe(group)} revenue reconciles to consolidated revenue in both years"]
            if label:
                notes.append(f"using {label}")
            if nested:
                notes.append("after resolving nested parent and child lines to the most detailed partition")
            if with_corporate and _CORPORATE in keys:
                notes.append("with the filed corporate line")
            exact[group] = {"prior": {key: before[key] for key in keys}, "latest": {key: after[key] for key in keys},
                            "basis": " ".join(notes) + "."}
            break
        if group in exact or group in residual:
            continue
        for before, after, _ in lines:
            if len(before) < 2 and _CORPORATE not in before:
                continue
            gaps = (prior_total - sum(before.values()), latest_total - sum(after.values()))
            if any(_close(gap, 0) for gap in gaps) or (gaps[0] > 0) != (gaps[1] > 0):
                continue
            if any(abs(gap) > RESIDUAL_LIMIT * total for gap, total in zip(gaps, (prior_total, latest_total), strict=True)):
                continue
            share = max(abs(gap) / total for gap, total in zip(gaps, (prior_total, latest_total), strict=True))
            residual[group] = {
                "prior": {**before, RECONCILING_LINE: gaps[0]}, "latest": {**after, RECONCILING_LINE: gaps[1]},
                "basis": (f"Analyst proxy: filed {_describe(group)} revenue plus a derived reconciling line equal to "
                          f"consolidated revenue less the filed lines, at most {share:.1%} of revenue. "
                          "It captures corporate, hedging, elimination and other unallocated amounts."),
            }
            break
    if exact:
        return exact
    if residual:
        return residual
    return {"consolidated_revenue": {
        "prior": {CONSOLIDATED_LINE: prior_total}, "latest": {CONSOLIDATED_LINE: latest_total},
        "basis": ("Analyst proxy: filed business-line revenue did not reconcile to consolidated revenue in two "
                  "comparable years. The model forecasts consolidated revenue as one line."),
    }}

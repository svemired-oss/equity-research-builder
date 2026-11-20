"""Evidence conditions for operating-company balance and cutoff adjustments."""

from datetime import date
from math import isclose, isfinite

from .operating_research import ResearchGap


def assume_absent_balances(period):
    """Apply explicitly labeled zero assumptions only after an absolute $1M balance check."""
    metrics = period["metrics"]
    assets = metrics.get("total_assets")
    equity = metrics.get("total_equity", metrics.get("equity_attributable_to_parent"))
    if equity is None:
        equity = metrics.get("equity_attributable_to_parent")
    liabilities = metrics.get("total_liabilities")
    if liabilities is None and metrics.get("liabilities_and_equity") is not None and equity is not None:
        liabilities = metrics["liabilities_and_equity"] - equity
    if any(type(value) not in (int, float) or not isfinite(value) for value in (assets, liabilities, equity)):
        return
    if not isclose(assets, liabilities + equity, rel_tol=0, abs_tol=1_000_000):
        return
    zero_current_term_debt(period)
    optional = ["finance_lease_current", "finance_lease_noncurrent", "commercial_paper"]
    if period.get("reported_debt_basis"):
        optional = ["commercial_paper"]
    if metrics.get("short_term_borrowings") is None and metrics.get("commercial_paper") is None:
        optional.append("short_term_borrowings")
    if all(metrics.get(key) is None for key in ("short_term_investments", "available_for_sale_debt_securities_current")):
        optional.append("short_term_investments")
    issues = period.get("metric_issues", {})
    for key in optional:
        if metrics.get(key) is not None:
            continue
        if issues.get(key) and not issues[key].startswith("not_reported"):
            raise ResearchGap(f"Cannot assume zero for conflicted {key.replace('_', ' ')}.")
        if key.startswith("finance_lease") and period.get("combined_unallocated_leases"):
            raise ResearchGap("Combined operating and finance leases require a separate finance lease balance.")
        metrics[key] = 0
        note = (f"Analyst assumption: {key.replace('_', ' ')} is zero because it was not reported separately. "
                f"The filed {period['period_end']} balance reconciles within USD 1 million. This is a proxy.")
        period.setdefault("operating_bridge_notes", []).append(note)
        period.setdefault("sources", {})[key] = {
            **period["filing"], "unit": "USD", "period_end": period["period_end"],
            "kind": "analyst_assumption", "value": 0, "basis": note,
        }


_COMPARATIVE_DEBT = {
    "current_debt": ("DebtCurrent", "ShortTermBorrowings", "NotesPayableCurrent", "LongTermDebtCurrent",
                     "DebtCurrentAndFinanceLeaseLiabilityCurrent"),
    "long_term_debt": ("LongTermDebtNoncurrent", "LongTermDebt", "LongTermNotesAndLoans", "LongTermNotesPayable",
                       "LongTermDebtAndCapitalLeaseObligations"),
}


def _annual_debt(annual, key):
    metrics = annual["metrics"]
    if metrics.get(key) is not None:
        return metrics[key]
    if key == "current_debt" and metrics.get("current_portion_long_term_debt") is not None:
        return metrics["current_portion_long_term_debt"] + (metrics.get("short_term_borrowings") or 0)
    return None


def recover_comparative_debt(period, annual, instance):
    """Fill interim debt from the filing's own year-end comparative column.

    A 10-Q presents the prior fiscal year-end beside the interim balance. A concept
    whose year-end value in that filing matches the audited annual debt total is the
    issuer's face-of-balance-sheet debt line on the same basis. Its interim value is
    then used. Ambiguous or conflicting matches leave the gap in place. Returns notes.
    """
    notes = []
    metrics = period["metrics"]
    issues = period.get("metric_issues", {})
    interim_end = date.fromisoformat(period["period_end"])
    year_end = date.fromisoformat(annual["period_end"])
    if year_end >= interim_end:
        return notes
    facts = {}
    for fact in instance.facts:
        if fact.start is None and not fact.dimensions and fact.unit == "USD" and fact.end in (year_end, interim_end):
            facts.setdefault((fact.concept, fact.end), set()).add(fact.value)
    for key, concepts in _COMPARATIVE_DEBT.items():
        if metrics.get(key) is not None:
            continue
        if key == "current_debt" and any(metrics.get(name) is not None for name in (
                "current_portion_long_term_debt", "current_portion_long_term_debt_and_finance_leases")):
            continue
        if issues.get(key) and not issues[key].startswith("not_reported"):
            continue
        target = _annual_debt(annual, key)
        if type(target) not in (int, float) or not isfinite(target) or target < 0:
            continue
        tolerance = max(1_000_000, .001 * target)
        matches = {}
        for concept in concepts:
            prior, current = (facts.get((f"us-gaap:{concept}", day)) for day in (year_end, interim_end))
            if not prior or not current or len(prior) != 1 or len(current) != 1:
                continue
            if isclose(next(iter(prior)), target, rel_tol=0, abs_tol=tolerance):
                matches[concept] = (next(iter(prior)), next(iter(current)))
        if len({value for _, value in matches.values()}) != 1:
            continue
        concept, (prior_value, value) = next(iter(matches.items()))
        if value < 0:
            continue
        metrics[key] = value
        label = key.replace("_", " ")
        note = (f"Filing crosswalk: interim {label} uses us-gaap:{concept}. The same filing reports "
                f"USD {prior_value / 1e6:,.0f} million for {annual['period_end']}, which matches the annual "
                f"{label} of USD {target / 1e6:,.0f} million.")
        notes.append(note)
        period.setdefault("sources", {})[key] = {
            **period["filing"], "url": instance.url, "taxonomy": "us-gaap", "tag": concept, "unit": "USD",
            "period_end": period["period_end"], "derivation": "year_end_comparative_crosswalk",
            "comparative": {"period_end": annual["period_end"], "value": prior_value, "annual_value": target},
            "basis": note,
        }
        issues.pop(key, None)
    return notes


def recover_instance_balances(period, instance):
    """Fill interim balances that Company Facts omits from the 10-Q's own XBRL instance.

    Combined utility filings can leave the parent's interim balance sheet out of
    Company Facts. Only undimensioned USD instants at the balance date are read,
    with the normalizer's tag order. A metric with conflicting values stays missing,
    and a recorded conflict is never overwritten. Returns notes.
    """
    from .sec_financials import _INSTANT_METRICS

    metrics = period["metrics"]
    issues = period.setdefault("metric_issues", {})
    end = date.fromisoformat(period["period_end"])
    values = {}
    for fact in instance.facts:
        if fact.start is None and not fact.dimensions and fact.unit == "USD" and fact.end == end:
            values.setdefault(fact.concept, set()).add(fact.value)
    filled = []
    for metric, tags in _INSTANT_METRICS.items():
        if metric == "ending_shares" or metrics.get(metric) is not None:
            continue
        if issues.get(metric) and not issues[metric].startswith("not_reported"):
            continue
        for tag in tags:
            found = values.get(f"us-gaap:{tag}")
            if not found:
                continue
            if len(found) == 1:
                metrics[metric] = next(iter(found))
                period.setdefault("sources", {})[metric] = {
                    **period["filing"], "url": instance.url, "taxonomy": "us-gaap", "tag": tag, "unit": "USD",
                    "period_end": period["period_end"], "derivation": "filing_instance",
                }
                issues.pop(metric, None)
                filled.append(metric)
            break
    if not filled:
        return []
    return [f"Filing instance: {len(filled)} interim balance-sheet facts missing from SEC Company Facts are read "
            f"directly from the {period['period_end']} 10-Q XBRL instance, including "
            + ", ".join(key.replace("_", " ") for key in filled[:4]) + ("." if len(filled) <= 4 else ", and others.")]


def apply_capital_events(packet, balance_date, as_of, cash, debt, shares, shares_date=None):
    """Roll disclosed completed movements into cash, debt and share count in millions."""
    bridge = packet.get("capital_bridge", {})
    shares_date = shares_date or balance_date
    if not balance_date <= shares_date <= as_of:
        raise ResearchGap("The common-share observation date is outside the bridge period.")
    records = {}
    for event in bridge.get("events", []):
        closed = date.fromisoformat(event["closed_on"])
        filed = date.fromisoformat(event["filed_on"])
        if closed > as_of or filed > as_of:
            raise ResearchGap("A financing event is dated after the valuation cutoff.")
        if closed <= balance_date:
            continue
        if not event.get("url") or event.get("currency", "USD") != "USD":
            raise ResearchGap("A post-balance financing event needs a dated USD filing source.")
        identity = (event["kind"], event["closed_on"], event.get("event_id"),
                    tuple((row["maturity_year"], row["coupon_percent"]) for row in event.get("tranches", [])))
        previous = records.get(identity)
        if previous and event["filed_on"] == previous["filed_on"] and any(
                previous.get(key) != event.get(key) for key in ("principal", "cash_paid", "net_proceeds", "shares_repurchased", "shares_issued")):
            raise ResearchGap("Conflicting completed financing movements persist on the same filing date.")
        if previous is None or event["filed_on"] > previous["filed_on"]:
            records[identity] = event
    movements = []
    for event in sorted(records.values(), key=lambda row: row["closed_on"]):
        kind = event["kind"]
        def amount(key):
            value = event.get(key)
            if type(value) not in (int, float) or not isfinite(value) or value < 0:
                raise ResearchGap(f"Completed {kind.replace('_', ' ')} needs verified {key.replace('_', ' ')}.")
            return value / 1e6
        cash_change = debt_change = share_change = 0
        if kind == "debt_issuance":
            debt_change, cash_change = amount("principal"), amount("net_proceeds")
        elif kind == "debt_repayment":
            debt_change, cash_change = -amount("principal"), -amount("cash_paid")
        elif kind == "share_repurchase":
            share_change, cash_change = -amount("shares_repurchased"), -amount("cash_paid")
        elif kind == "common_stock_issuance":
            share_change, cash_change = amount("shares_issued"), amount("net_proceeds")
        else:
            raise ResearchGap(f"Post-balance {kind} requires a supported equity-bridge treatment.")
        if date.fromisoformat(event["closed_on"]) <= shares_date:
            share_change = 0
        movements.append({**event, "cash_change": cash_change, "debt_change": debt_change,
                          "share_change": share_change})
        cash += cash_change
        debt += debt_change
        shares += share_change
    if min(cash, debt) < 0 or shares <= 0:
        raise ResearchGap("Completed financing events produce negative cash/debt or nonpositive shares.")
    return cash, debt, shares, movements


def recover_alternative_cash(period, instance):
    """Tie a cash alternative to the same filing's opening, closing and cash-flow change."""
    if period["metrics"].get("cash_and_equivalents") is not None or not period.get("period_start"):
        return
    from datetime import timedelta
    start = date.fromisoformat(period["period_start"])
    end = date.fromisoformat(period["period_end"])
    facts = [fact for fact in instance.facts if fact.unit == "USD" and not fact.dimensions]
    alternatives = (
        "us-gaap:CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        "us-gaap:CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsIncludingDisposalGroupAndDiscontinuedOperations",
        "us-gaap:CashAndDueFromBanks",
    )
    changes = {
        "us-gaap:CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsPeriodIncreaseDecreaseIncludingExchangeRateEffect",
        "us-gaap:CashAndCashEquivalentsPeriodIncreaseDecreaseIncludingExchangeRateEffect",
    }
    delta = {fact.value for fact in facts if fact.concept in changes and fact.start == start and fact.end == end}
    for concept in alternatives:
        opening = {fact.value for fact in facts if fact.concept == concept and fact.start is None and fact.end == start - timedelta(days=1)}
        closing = {fact.value for fact in facts if fact.concept == concept and fact.start is None and fact.end == end}
        if len(opening) != 1 or len(closing) != 1 or len(delta) != 1:
            continue
        value = next(iter(closing))
        if not isclose(value - next(iter(opening)), next(iter(delta)), rel_tol=0, abs_tol=1e6):
            continue
        restricted = {fact.value for fact in facts if fact.concept == "us-gaap:RestrictedCash"
                      and fact.start is None and fact.end == end}
        if len(restricted) > 1:
            raise ResearchGap("Conflicting restricted cash balances prevent a cash-flow reconciliation.")
        restricted_value = next(iter(restricted), 0)
        if restricted_value < 0 or restricted_value > value:
            raise ResearchGap("Restricted cash exceeds the reconciled cash aggregate.")
        available = value - restricted_value if "RestrictedCash" in concept else value
        if available < 0:
            raise ResearchGap("The reconciled cash alternative is negative.")
        period["metrics"]["cash_and_equivalents"] = available
        note = (f"Cash proxy from {concept}, reconciled to opening cash plus the filed cash-flow change "
                "within USD 1 million. Separately reported restricted cash is excluded. "
                "Any restricted cash not separately quantified remains included in this proxy.")
        period.setdefault("operating_bridge_notes", []).append(note)
        combined = {fact.value for fact in facts if fact.concept == "us-gaap:CashCashEquivalentsAndShortTermInvestments"
                    and fact.start is None and fact.end == end}
        investments = period["metrics"].get("short_term_investments")
        if (len(combined) == 1 and investments and isclose(next(iter(combined)), available, rel_tol=0, abs_tol=1e6)):
            # The filed cash-and-investments subtotal equals cash alone, so the
            # separately tagged investments sit inside cash equivalents.
            period["metrics"]["short_term_investments"] = 0
            inside = (f"Short-term investments of USD {investments / 1e6:,.0f} million are inside the cash line: the filed "
                      f"cash, cash equivalents and short-term investments subtotal equals the USD {available / 1e6:,.0f} "
                      "million cash proxy, so they are not added again.")
            period["operating_bridge_notes"].append(inside)
            period.setdefault("sources", {})["short_term_investments"] = {
                **period["filing"], "url": instance.url, "unit": "USD", "period_end": period["period_end"],
                "kind": "included_in_cash", "value": 0, "basis": inside}
        period.setdefault("sources", {})["cash_and_equivalents"] = {
            **period["filing"], "url": instance.url, "tag": concept, "unit": "USD",
            "kind": "cash_proxy", "value": available, "basis": note,
            "components": {"closing_aggregate": value, "opening_aggregate": next(iter(opening)),
                           "cash_flow_change": next(iter(delta)),
                           "excluded_restricted_cash": restricted_value if "RestrictedCash" in concept else 0},
        }
        return


# Totals that include every current and noncurrent term-debt line. LongTermDebt
# excludes separately presented short-term borrowings; the other two may include them.
_DEBT_TOTALS = (("long_term_debt_including_current", "LongTermDebt", False),
                ("notes_payable_total", "NotesPayable", True),
                ("total_debt", "DebtLongtermAndShorttermCombinedAmount", True))


def zero_current_term_debt(period):
    """Set the current portion of term debt to zero only when filed totals prove it.

    Requires a balance sheet that ties within USD 1 million (checked by the caller),
    no filed current-debt line of any kind, and a filed debt total under a different
    tag that equals noncurrent term debt (plus any separately filed short-term
    borrowings) within USD 1 million. Returns True when the zero was applied.
    """
    metrics = period["metrics"]
    issues = period.get("metric_issues", {})
    current_keys = ("current_debt", "current_portion_long_term_debt", "current_portion_long_term_debt_and_finance_leases")
    if any(metrics.get(key) is not None for key in current_keys):
        return False
    if any(issues.get(key) and not issues[key].startswith("not_reported") for key in current_keys):
        return False
    noncurrent = metrics.get("long_term_debt")
    noncurrent_tag = period.get("sources", {}).get("long_term_debt", {}).get("tag")
    if type(noncurrent) not in (int, float) or not isfinite(noncurrent) or noncurrent < 0 or not noncurrent_tag:
        return False
    short_term = sum(metrics.get(key) or 0 for key in ("short_term_borrowings", "commercial_paper"))
    for key, tag, includes_short_term in _DEBT_TOTALS:
        total = metrics.get(key)
        if type(total) not in (int, float) or not isfinite(total) or tag == noncurrent_tag:
            continue
        candidates = [noncurrent] + ([noncurrent + short_term] if includes_short_term and short_term else [])
        match = next((value for value in candidates if isclose(total, value, rel_tol=0, abs_tol=1_000_000)), None)
        if match is None:
            continue
        metrics["current_portion_long_term_debt"] = 0
        note = (f"Filed-total derivation: the current portion of term debt is zero. The filed {period['period_end']} "
                f"us-gaap:{tag} of USD {total / 1e6:,.0f} million equals noncurrent debt (us-gaap:{noncurrent_tag}) "
                + (f"plus USD {short_term / 1e6:,.0f} million of separately filed short-term borrowings " if match != noncurrent else "")
                + "within USD 1 million, and the balance sheet ties. No current-debt line is filed.")
        period.setdefault("operating_bridge_notes", []).append(note)
        period.setdefault("sources", {})["current_portion_long_term_debt"] = {
            **period["filing"], "unit": "USD", "period_end": period["period_end"], "kind": "filed_total_derivation",
            "value": 0, "basis": note, "components": {"total_tag": tag, "total": total, "noncurrent": noncurrent,
                                                       "short_term": short_term if match != noncurrent else 0},
        }
        issues.pop("current_portion_long_term_debt", None)
        return True
    return False


# Face-of-balance-sheet current liability lines used to prove that segment debt
# lines are the complete current debt. Any extra or overlapping line breaks the tie,
# so the check fails closed.
_CURRENT_LIABILITY_LINES = (
    "AccountsPayableCurrent", "AccruedLiabilitiesCurrent", "EmployeeRelatedLiabilitiesCurrent",
    "ContractWithCustomerLiabilityCurrent", "DividendsPayableCurrent", "OtherLiabilitiesCurrent",
    "TaxesPayableCurrent", "AccruedIncomeTaxesCurrent", "DeferredRevenueCurrent", "InterestPayableCurrent",
    "AccountsPayableAndAccruedLiabilitiesCurrent", "LiabilitiesOfDisposalGroupIncludingDiscontinuedOperationCurrent",
)
_SEGMENT_DEBT = (
    ("LongTermDebtAndCapitalLeaseObligationsCurrent", "LongTermDebtAndCapitalLeaseObligations",
     "current_portion_long_term_debt_and_finance_leases", "long_term_debt_and_finance_leases"),
    ("LongTermDebtCurrent", "LongTermDebtNoncurrent", "current_portion_long_term_debt", "long_term_debt"),
)


def recover_segment_debt(period, instance, annual=None):
    """Sum debt that a filing tags only by business segment, such as Caterpillar's
    Machinery, Energy & Transportation and Financial Products columns.

    One axis must carry the same members for current and noncurrent debt. The
    members' noncurrent sum must equal a consolidated noncurrent total filed for the
    same or the prior fiscal year-end, and the segment debt plus the other filed
    current-liability lines must equal total current liabilities within USD 1
    million. Returns notes; any failed check leaves the gap in place.
    """
    metrics = period["metrics"]
    issues = period.get("metric_issues", {})
    current_keys = ("current_debt", "current_portion_long_term_debt", "current_portion_long_term_debt_and_finance_leases")
    if any(metrics.get(key) is not None for key in current_keys):
        return []
    if any(issues.get(key) and not issues[key].startswith("not_reported") for key in (*current_keys, "long_term_debt")):
        return []
    end = date.fromisoformat(period["period_end"])

    def members(concept, day):
        found = {}
        for fact in instance.facts:
            if (fact.concept == f"us-gaap:{concept}" and fact.start is None and fact.unit == "USD"
                    and fact.end == day and len(fact.dimensions) == 1):
                axis, member = fact.dimensions[0]
                found.setdefault(axis, {}).setdefault(member, set()).add(fact.value)
        return {axis: {m: next(iter(v)) for m, v in rows.items()} for axis, rows in found.items()
                if all(len(v) == 1 for v in rows.values())}

    def plain(concept, day):
        values = {fact.value for fact in instance.facts if fact.concept == f"us-gaap:{concept}" and fact.start is None
                  and fact.unit == "USD" and fact.end == day and not fact.dimensions}
        return next(iter(values)) if len(values) == 1 else (None if not values else False)

    for current_tag, noncurrent_tag, current_key, noncurrent_key in _SEGMENT_DEBT:
        if plain(current_tag, end) is not None:
            continue
        current_axes, noncurrent_axes = members(current_tag, end), members(noncurrent_tag, end)
        for axis, current in current_axes.items():
            noncurrent = noncurrent_axes.get(axis)
            if not noncurrent or not set(current) <= set(noncurrent) or len(current) < 2:
                continue
            # Some filings repeat one segment under a second member name. Only the
            # current-debt members are summed, and the totals below must still tie.
            noncurrent = {member: noncurrent[member] for member in current}
            # Validate the member set against a consolidated noncurrent total.
            validated = None
            checks = [(end, metrics.get("long_term_debt"))]
            if annual is not None and annual is not period:
                checks.append((date.fromisoformat(annual["period_end"]), annual["metrics"].get("long_term_debt")))
            for day, total in checks:
                total = total if type(total) in (int, float) else plain(noncurrent_tag, day) or plain("LongTermDebtNoncurrent", day)
                if type(total) not in (int, float):
                    continue
                prior = members(noncurrent_tag, day).get(axis) or {}
                if set(current) <= set(prior) and isclose(sum(prior[m] for m in current), total, rel_tol=0, abs_tol=1_000_000):
                    validated = (day, total)
                    break
            if validated is None:
                continue
            short_term = plain("ShortTermBorrowings", end)
            short_members = members("ShortTermBorrowings", end).get(axis, {})
            if short_term is False or (short_term is None and not set(short_members) <= set(current)):
                continue
            short_value = metrics.get("short_term_borrowings")
            if short_value is None:
                short_value = short_term if short_term is not None else sum(short_members.values())
            liabilities = plain("LiabilitiesCurrent", end)
            lines = [plain(name, end) for name in _CURRENT_LIABILITY_LINES]
            if type(liabilities) not in (int, float) or any(value is False for value in lines):
                continue
            current_value = sum(current.values())
            if not isclose(liabilities, sum(value or 0 for value in lines) + short_value + current_value,
                           rel_tol=0, abs_tol=1_000_000):
                continue
            noncurrent_value = sum(noncurrent.values())
            if metrics.get("long_term_debt") is not None and not isclose(
                    metrics["long_term_debt"], noncurrent_value, rel_tol=0, abs_tol=1_000_000):
                continue
            metrics[current_key] = current_value
            metrics[noncurrent_key] = noncurrent_value
            if metrics.get("short_term_borrowings") is None:
                metrics["short_term_borrowings"] = short_value
            names = ", ".join(member.rsplit("}", 1)[-1].split(":")[-1] for member in sorted(current))
            note = (f"Segment debt sum: the filing tags {period['period_end']} debt only by {axis.split(':')[-1]} "
                    f"({names}). Current debt of USD {current_value / 1e6:,.0f} million and noncurrent debt of "
                    f"USD {noncurrent_value / 1e6:,.0f} million are the member sums of us-gaap:{current_tag} and "
                    f"us-gaap:{noncurrent_tag}. The same members sum to the consolidated noncurrent total of "
                    f"USD {validated[1] / 1e6:,.0f} million at {validated[0]}, and segment debt plus the other filed "
                    "current liabilities equals total current liabilities within USD 1 million.")
            sources = period.setdefault("sources", {})
            for key, tag, value in ((current_key, current_tag, current_value), (noncurrent_key, noncurrent_tag, noncurrent_value)):
                sources[key] = {**period["filing"], "url": instance.url, "taxonomy": "us-gaap", "tag": tag, "unit": "USD",
                                "period_end": period["period_end"], "derivation": "segment_member_sum",
                                "members": dict(sorted((m, current[m] if key == current_key else noncurrent[m]) for m in current)),
                                "value": value, "basis": note}
                issues.pop(key, None)
            return [note]
    return []


def related_party_debt(balances, concept):
    """Sum a debt concept filed only as related-party and nonrelated-party members."""
    axis = "us-gaap:RelatedPartyTransactionsByRelatedPartyAxis"
    found = {}
    for fact in balances:
        if fact.concept == concept and len(fact.dimensions) == 1 and fact.dimensions[0][0] == axis:
            found.setdefault(fact.dimensions[0][1], set()).add(fact.value)
    if set(found) != {"us-gaap:RelatedPartyMember", "us-gaap:NonrelatedPartyMember"} or any(len(v) != 1 for v in found.values()):
        return None
    return {member: next(iter(values)) for member, values in found.items()}


_DISPOSAL_ASSETS = ("AssetsOfDisposalGroupIncludingDiscontinuedOperation",
                    "AssetsOfDisposalGroupIncludingDiscontinuedOperationCurrent",
                    "DisposalGroupIncludingDiscontinuedOperationAssetsNoncurrent")
SEPARATION_MATERIALITY = .05


def separation_basis_gap(annual, interim, instance, issuer_name=None, annual_instance=None):
    """Stop when the latest 10-Q separates operations that the 10-K history includes.

    The 10-Q's prior year-end column reports discontinued-operation assets. When they
    are at least 5% of the 10-K's total assets, the filed history and the opening
    balance sheet are on a different basis from the continuing company, and a
    valuation needs pro forma continuing-operations history.
    """
    year_end = date.fromisoformat(annual["period_end"])
    interim_end = date.fromisoformat(interim["period_end"])
    if interim_end <= year_end:
        return
    total_assets = annual["metrics"].get("total_assets")
    if type(total_assets) not in (int, float) or total_assets <= 0:
        return

    def value(source, concept, day):
        values = {fact.value for fact in source.facts if fact.concept == f"us-gaap:{concept}" and fact.start is None
                  and fact.unit == "USD" and fact.end == day and not fact.dimensions}
        return max(values) if values else None

    def disposal(day, source=instance):
        total = value(source, _DISPOSAL_ASSETS[0], day)
        if total is not None:
            return total
        parts = [value(source, concept, day) for concept in _DISPOSAL_ASSETS[1:]]
        return None if all(part is None for part in parts) else sum(part or 0 for part in parts)

    restated = disposal(year_end)
    # A disposal group the 10-K already presents is part of the filed history's
    # basis. Only the amount the 10-Q newly classifies at that year-end counts.
    reported = (disposal(year_end, annual_instance) if annual_instance is not None else None) or 0
    prior = None if restated is None else restated - reported
    if prior is None or prior < SEPARATION_MATERIALITY * total_assets:
        return
    remaining = disposal(interim_end) or 0
    spin = any(word in fact.concept.rsplit("}", 1)[-1].split(":")[-1]
               for fact in instance.facts for word in ("SpinOff", "Spinoff", "Separation"))
    event = "spin-off" if spin else "separation or disposal"
    status = (f"were separated by {interim['period_end']}" if remaining < SEPARATION_MATERIALITY * prior
              else f"are still held for disposal at {interim['period_end']}")
    who = f"{issuer_name}'s" if issuer_name else "The"
    raise ResearchGap(
        f"Pro forma basis required: {who} 10-Q filed {interim['filing']['filed_on']} reports a {event}. Discontinued "
        f"operations held USD {prior / 1e6:,.0f} million of assets at {annual['period_end']} "
        f"({prior / total_assets:.0%} of total assets) and {status}. The 10-K history and opening balance sheet "
        "include those operations, so a valuation needs pro forma continuing-operations history.")


def cover_class_shares(period, facts, filing, url):
    """Use the latest cover-page share count summed across the filed common share classes.

    Issuers with several common classes (Meta, UPS, Comcast) tag the cover count only
    by class. Each class must have one value at the latest cover date. Classes are
    counted one for one, which is labeled as an analyst assumption. Returns notes.
    """
    cover_date = max(fact.end for fact in facts)
    by_class = {}
    for fact in facts:
        if fact.end == cover_date:
            by_class.setdefault(fact.dimensions[0][1], set()).add(fact.value)
    if len(by_class) < 2 or any(len(values) != 1 for values in by_class.values()):
        return []
    counts = {member: next(iter(values)) for member, values in by_class.items()}
    if any(value < 0 for value in counts.values()) or sum(counts.values()) <= 0:
        return []
    total = sum(counts.values())
    period["metrics"]["ending_shares"] = total
    period["share_count_date"] = cover_date.isoformat()
    names = ", ".join(f"{member.rsplit('}', 1)[-1].split(':')[-1].replace('Member', '')} {value / 1e6:,.1f}M"
                      for member, value in sorted(counts.items()))
    note = (f"Cover-page share count: dei:EntityCommonStockSharesOutstanding at {cover_date} from the "
            f"{filing['form']} filed {filing['filed_on']} ({names}), {total / 1e6:,.1f} million in total. "
            "Analyst assumption: the common share classes are counted one for one.")
    period.setdefault("sources", {})["ending_shares"] = {
        **filing, "url": url, "tag": "dei:EntityCommonStockSharesOutstanding", "unit": "shares",
        "period_end": cover_date.isoformat(), "members": counts, "value": total, "basis": note}
    period.get("metric_issues", {}).pop("ending_shares", None)
    return [note]

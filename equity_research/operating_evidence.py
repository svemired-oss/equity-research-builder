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
        period.setdefault("sources", {})["cash_and_equivalents"] = {
            **period["filing"], "url": instance.url, "tag": concept, "unit": "USD",
            "kind": "cash_proxy", "value": available, "basis": note,
            "components": {"closing_aggregate": value, "opening_aggregate": next(iter(opening)),
                           "cash_flow_change": next(iter(delta)),
                           "excluded_restricted_cash": restricted_value if "RestrictedCash" in concept else 0},
        }
        return

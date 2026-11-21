"""Select filing-backed forecast drivers and calculate a standard operating-company DCF."""

from calendar import monthrange
from datetime import date, timedelta
from itertools import combinations
from math import isclose, isfinite
from statistics import median
import re

from .fcff_valuation import DatedFCFF, EquityBridge, TerminalAssumptions, discount_fcff
from .operating_valuation import CostOfCapitalInputs, calculate_cost_of_capital


class ResearchGap(ValueError):
    """The evidence cannot support the standard model."""


OPERATING_GUIDES = {
    "margin": ("Base operating margin", .03, .79),
    "capex": ("First-year cash capex / revenue", 0., .60),
    "growth": ("Terminal revenue growth", .005, .04),
    "roic": ("Terminal incremental ROIC", .05, .50),
}


def _revenue_anchor(packet, fiscal_year, forecast, annual_revenue):
    guidance = packet.get("revenue_guidance", {})
    relevant = [row for row in guidance.get("observations", ())
                if row["fiscal_year"] == fiscal_year and row["currency"] == "USD"]
    conflicting = [row["filed_on"] for row in guidance.get("conflicts", ())
                   if row["fiscal_year"] == fiscal_year and row["currency"] == "USD"]
    dates = [row["filed_on"] for row in relevant] + conflicting
    if dates:
        latest = max(dates)
        if latest in conflicting:
            raise ResearchGap("The latest full-year revenue guidance contains conflicting ranges.")
        rows = [row for row in relevant if row["filed_on"] == latest]
        lowers = [row["point"] or row["lower"] for row in rows]
        uppers = [row["point"] or row["upper"] for row in rows]
        lower = max((x for x in lowers if x is not None), default=None)
        upper = min((x for x in uppers if x is not None), default=None)
        if lower is not None and upper is not None:
            return (lower + upper) / 2e6, {f"FY revenue guidance {i}": row["url"] for i, row in enumerate(rows, 1)}, (
                "First-year consolidated revenue uses the midpoint of the latest verified full-year guidance. "
                "The model allocates that total across business lines in proportion to their prior forecast mix.")
    ytd = packet.get("earnings_interim", {})
    if ytd.get("current") and ytd.get("prior"):
        current, prior = ytd["current"][-1], ytd["prior"][-1]
        now, then = (row["metrics"].get("revenue") for row in (current, prior))
        starts, ends = (tuple(date.fromisoformat(row[key]) for row in (current, prior))
                        for key in ("period_start", "period_end"))
        same_duration = abs((ends[0] - starts[0]).days - (ends[1] - starts[1]).days) <= 7
        if same_duration and now is not None and then is not None and 0 < then < annual_revenue * 1e6 and now > 0:
            elapsed_share = then / (annual_revenue * 1e6)
            target = now / 1e6 + forecast * (1 - elapsed_share)
            return target, {"Reported current YTD sales": current["filing"]["url"],
                            "Comparable prior YTD sales": prior["filing"]["url"]}, (
                "First-year sales retain reported fiscal YTD revenue and apply the historical forecast to the "
                "remaining share of the year. Segment allocation is an estimate based on the forecast mix.")
    return None, {}, "No usable consolidated full-year guidance or matched interim revenue anchor was applied."


def operating_rating(upside):
    if abs(upside) >= .5:
        return "Review"
    return "Buy" if upside >= .15 else "Sell" if upside <= -.15 else "Hold"


def verify_comparable_operating_basis(packet):
    """Require disclosed full-year pro forma sales when acquisitions invalidate the annual base."""
    from .acquisition_basis import material_acquisitions
    if material_acquisitions(packet) and not packet.get("acquisition_revenue_basis"):
        raise ResearchGap("Material acquisition needs disclosed full-year combined pro forma revenue, "
                          "or comparable acquiree revenue and revenue already included, for both annual periods.")


def revenue_candidates(packet):
    """Return complete, disjoint segment or product groupings from one annual filing."""
    periods = packet["actuals"]["periods"]
    if len(periods) < 3:
        raise ResearchGap("Three normalized USD annual statements are required.")
    acquisition = packet.get("acquisition_revenue_basis")
    if acquisition:
        prior, latest = acquisition["periods"]
        if [row["period_end"] for row in (prior, latest)] != [row["period_end"] for row in periods[-2:]]:
            raise ResearchGap("The acquisition revenue base does not match the annual comparison periods.")
        for row, historical in zip((prior, latest), periods[-2:], strict=True):
            if row["unit"] != "USD" or not row.get("url") or row["combined_revenue"] != row["reported_revenue"] + row["incremental_acquiree_revenue"]:
                raise ResearchGap("The acquisition revenue reconciliation or source is incomplete.")
            if (row["reported_revenue"] != historical["metrics"]["revenue"]
                    or row["period_start"] != historical["period_start"]
                    or not all(type(row[key]) in (int, float) and isfinite(row[key]) for key in
                               ("combined_revenue", "reported_revenue", "incremental_acquiree_revenue"))
                    or row["incremental_acquiree_revenue"] < 0):
                raise ResearchGap("The acquisition revenue base differs from its matched annual evidence.")
        return {"pro_forma_acquisition": {
            "prior": {"CombinedOperations": prior["combined_revenue"] / 1e6},
            "latest": {"CombinedOperations": latest["combined_revenue"] / 1e6},
        }}
    prior, latest = periods[-2:]
    facts = packet["operating_facts"]["observations"]
    bases = {tuple(period.get("sources", {}).get("revenue", {}).get(field) for field in ("taxonomy", "tag"))
             for period in (prior, latest)}
    # The same filing may tag both net sales and total revenue for every
    # segment. First match the consolidated accounting line used by the model.
    preferred = {}
    concept = None
    if len(bases) == 1:
        taxonomy, tag = next(iter(bases))
        if taxonomy and tag:
            concept = f"{taxonomy}:{tag}"
            preferred = _reconciled_revenue(prior, latest, (fact for fact in facts
                                           if fact.get("concept") == concept or fact["metric"] == "corporate_nonsegment_revenue"))
    candidates = {**_reconciled_revenue(prior, latest, facts), **preferred}
    if not candidates:
        from .drivers import PLUGINS, extract, FALLBACK
        if any(extract(packet, kind, passage)["type"] != FALLBACK
               for passage in packet.get("driver_passages", {}).values() for kind in PLUGINS):
            return {"verified_operating_driver": {"prior": {"Consolidated": prior["metrics"]["revenue"]/1e6},
                                                 "latest": {"Consolidated": latest["metrics"]["revenue"]/1e6}}}
        from .revenue_fallbacks import fallback_revenue_candidates
        candidates = fallback_revenue_candidates(prior, latest, facts, concept)
    if not candidates:
        raise ResearchGap("Filed product or segment revenue did not reconcile across two comparable years.")
    return candidates


def _reconciled_revenue(prior, latest, facts):
    by_group = {}
    corporate = {}
    for fact in facts:
        group = fact["metric"]
        end = fact["period_end"]
        if (group not in ("segment_revenue", "product_or_service_revenue", "corporate_nonsegment_revenue")
                or fact.get("unit") != "USD" or fact.get("accession") != latest["filing"]["accession"]
                or end not in (prior["period_end"], latest["period_end"])):
            continue
        if not isinstance(fact.get("dimensions"), list):
            raise ResearchGap("A revenue fact is missing its reporting dimensions.")
        # The intake classifier accepts this qualifier because it marks the
        # reporting population rather than a second business dimension.
        dimensions = [item for item in fact["dimensions"]
                      if (item.get("axis"), item["member"]) != ("srt:ConsolidationItemsAxis", "us-gaap:OperatingSegmentsMember")]
        if len(dimensions) != 1:
            continue
        if group == "corporate_nonsegment_revenue":
            corporate.setdefault(end, set()).add(fact["value"] / 1e6)
            continue
        member = dimensions[0]["member"].rsplit("}", 1)[-1].rsplit(":", 1)[-1]
        by_group.setdefault(group, {}).setdefault(end, {}).setdefault(member, set()).add(fact["value"] / 1e6)
    candidates = {}
    for group, data in by_group.items():
        old, new = (data.get(period["period_end"], {}) for period in (prior, latest))
        # Product/Services parent totals can overlap the itemized product lines.
        if len(new) > 2:
            old.pop("ProductMember", None)
            new.pop("ProductMember", None)
        if not 2 <= len(new) <= 16 or set(old) != set(new):
            continue
        if any(len(values) != 1 or min(values) <= 0 for rows in (old, new) for values in rows.values()):
            continue
        old, new = ({key: next(iter(values)) for key, values in rows.items()} for rows in (old, new))
        prior_total, latest_total = (period["metrics"]["revenue"] / 1e6 for period in (prior, latest))
        if isclose(sum(old.values()), prior_total, abs_tol=1) and isclose(sum(new.values()), latest_total, abs_tol=1):
            candidates[group] = {"prior": old, "latest": new}
        elif group == "segment_revenue":
            before, after = (corporate.get(period["period_end"], set()) for period in (prior, latest))
            if len(before) == len(after) == 1 and min(before | after) > 0:
                old_other, new_other = next(iter(before)), next(iter(after))
                if (isclose(sum(old.values()) + old_other, prior_total, rel_tol=0, abs_tol=1)
                        and isclose(sum(new.values()) + new_other, latest_total, rel_tol=0, abs_tol=1)
                        and "CorporateNonSegmentMember" not in old):
                    candidates[group] = {"prior": {**old, "CorporateNonSegmentMember": old_other},
                                         "latest": {**new, "CorporateNonSegmentMember": new_other}}
        elif group == "product_or_service_revenue":
            # Some filings tag parent totals and their components together.
            # Only use a subset if one grouping reconciles in both periods.
            matched = []
            for size in range(2, len(new)):
                for keys in combinations(new, size):
                    if (isclose(sum(old[key] for key in keys), prior_total, abs_tol=1)
                            and isclose(sum(new[key] for key in keys), latest_total, abs_tol=1)):
                        matched.append(keys)
                        if len(matched) > 1:
                            break
                if len(matched) > 1:
                    break
            if len(matched) == 1:
                candidates[group] = {"prior": {key: old[key] for key in matched[0]},
                                     "latest": {key: new[key] for key in matched[0]}}
    return candidates


IMMATERIAL_NCI = .005


def _read(metrics, key):
    value = metrics.get(key)
    if value is None:
        raise ResearchGap(f"Required financial fact is missing: {key.replace('_', ' ')}.")
    return value


LONG_RUN_GROWTH = .04  # docs/method-notes.md, Stage 2d: years 2-5 fade from the 3-year revenue CAGR to 4% nominal.


def normalized_operating_income(metrics):
    """Operating income with disclosed unusual items removed, USD (filed units).

    Charges: the larger of the combined restructuring and impairment provision or restructuring +
    impairments + litigation settlements (so an item inside the aggregate is not counted twice).
    Asset-sale gains are removed and losses added back, assuming they sit within operating income.
    The generic OtherCostAndExpenseOperating tag is not used: most filers (META, UPS, CSX, DAL in the
    2026-11-21 run) report recurring other operating expense under it, not unusual charges.
    """
    value = lambda key: metrics.get(key) if type(metrics.get(key)) in (int, float) and isfinite(metrics.get(key)) else 0.
    impairment = max(value("unusual_asset_impairment"), value("unusual_goodwill_and_intangible_impairment"),
                     value("unusual_goodwill_impairment") + value("unusual_intangible_impairment")
                     + value("unusual_long_lived_impairment"))
    components = value("unusual_restructuring") + impairment + value("unusual_litigation")
    charges = max(value("unusual_restructuring_and_impairment"), components, 0.)
    gain = value("unusual_asset_sale_gain")
    return _read(metrics, "operating_income") + charges - gain, charges, gain


def revenue_fade(history):
    """Three-year consolidated revenue CAGR (fewer years when the history is shorter) and the year 2-5 path."""
    years = min(3, len(history) - 1)
    if years < 1:
        raise ResearchGap("Two annual revenue periods are required for the growth fade.")
    latest, earlier = (_read(history[i]["metrics"], "revenue") for i in (-1, -1 - years))
    if latest <= 0 or earlier <= 0:
        raise ResearchGap("Positive filed revenue is required for the growth fade.")
    cagr = (latest / earlier) ** (1 / years) - 1
    return {"cagr": cagr, "years": years, "long_run": LONG_RUN_GROWTH,
            "path": [cagr + (LONG_RUN_GROWTH - cagr) * k / 3 for k in range(4)]}


def _balance(period):
    from .operating_evidence import assume_absent_balances

    assume_absent_balances(period)
    metrics = {key: value / 1e6 for key, value in period["metrics"].items() if value is not None}
    cash = _read(metrics, "cash_and_equivalents")
    investments = next((metrics[key] for key in ("short_term_investments", "available_for_sale_debt_securities_current")
                        if key in metrics), None)
    if investments is None:
        raise ResearchGap("The filing needs a verified short-term investment balance.")
    if "long_term_debt_and_finance_leases" in metrics:
        current_debt = _read(metrics, "current_portion_long_term_debt_and_finance_leases") + _read(metrics, "short_term_borrowings")
        debt = current_debt + metrics["long_term_debt_and_finance_leases"]
    else:
        current_debt = metrics.get("current_debt")
        if current_debt is None:
            current_debt = _read(metrics, "current_portion_long_term_debt") + max(
                metrics.get("commercial_paper", 0), metrics.get("short_term_borrowings", 0))
        debt = current_debt + _read(metrics, "long_term_debt")
        if "total_debt" in metrics and not isclose(debt, metrics["total_debt"], abs_tol=1):
            raise ResearchGap("Current and noncurrent debt disagree with filed total borrowing.")
        if not period.get("reported_debt_basis"):
            lease = _read(metrics, "finance_lease_current")
            current_debt += lease
            debt += lease + _read(metrics, "finance_lease_noncurrent")
    equity = metrics.get("total_equity", metrics.get("equity_attributable_to_parent"))
    assets = _read(metrics, "total_assets")
    liabilities = metrics.get("total_liabilities")
    if liabilities is None and equity is not None:
        liabilities = _read(metrics, "liabilities_and_equity") - equity
        metrics["total_liabilities"] = liabilities
    if equity is None or not isclose(assets - liabilities, equity, rel_tol=0, abs_tol=1):
        raise ResearchGap("The filed balance sheet did not reconcile.")
    total_equity = metrics.get("total_equity", equity)
    nci = metrics.get("noncontrolling_interest", 0)
    if nci < 0 and total_equity and abs(nci) < IMMATERIAL_NCI * abs(total_equity):
        period["immaterial_nci"] = True
        note = (f"Immaterial NCI at book: the filed {period['period_end']} noncontrolling interest of USD {nci:,.1f} million "
                f"is {abs(nci) / abs(total_equity):.3%} of total equity of USD {total_equity:,.0f} million, under the 0.5% "
                "threshold, so it is deducted at book value.")
        notes = period.setdefault("operating_bridge_notes", [])
        if note not in notes:
            notes.append(note)
    for key in ("noncontrolling_interest", "preferred_stock_book_value"):
        if metrics.get(key, 0) < 0 and not (key == "noncontrolling_interest" and period.get("immaterial_nci")):
            raise ResearchGap(f"Negative {key.replace('_', ' ')} requires a reviewed valuation bridge.")
        if metrics.get(key, 0):
            note = f"Book-value proxy: deduct filed {key.replace('_', ' ')} from common equity value."
            notes = period.setdefault("operating_bridge_notes", [])
            if note not in notes:
                notes.append(note)
    wc_assets = _read(metrics, "current_assets") - cash - investments
    wc_liabilities = _read(metrics, "current_liabilities") - current_debt
    return metrics, cash, investments, debt, equity, wc_assets, wc_liabilities


def _forecast_dates(history):
    """Extend a calendar or recurring weekday fiscal close after checking its history."""
    years = [date.fromisoformat(row["period_end"]) for row in history[-5:]]
    latest = years[-1]
    if all((day.month, day.day) == (latest.month, latest.day) for day in years):
        return tuple(date(latest.year+i, latest.month, latest.day) for i in range(1, 6))
    if len(years) >= 3 and all(day.weekday() == latest.weekday() for day in years):
        def month_end(year, month):
            return date(year, month, monthrange(year, month)[1])

        def last(year, month):
            end = month_end(year, month)
            return end - timedelta(days=(end.weekday() - latest.weekday()) % 7)

        if all(day.month == latest.month and day == last(day.year, latest.month) for day in years):
            return tuple(last(latest.year+i, latest.month) for i in range(1, 6))
        def nearest_month_close(year, month):
            end = month_end(year, month)
            offset = (latest.weekday() - end.weekday() + 3) % 7 - 3
            return end + timedelta(days=offset)

        # Closest-weekday issuers can straddle months. December is handled by
        # the same-month rule above to avoid guessing the fiscal-year label.
        for month in range(1, 12):
            if all(day == nearest_month_close(day.year, month) for day in years):
                return tuple(nearest_month_close(latest.year+i, month) for i in range(1, 6))
    return tuple(latest + timedelta(days=round(i * 365.25)) for i in range(1, 6))


FINANCING_MATERIALITY = .10


def _financing_start(latest, bridge_period, opening, debt, bridge_base):
    """Start forecast debt and interest at the 10-Q when financing moved materially after year-end.

    Material means funded debt or common shares changed more than 10% between the annual
    and the 10-Q balance sheets. The share count already opens at the 10-Q bridge. The
    year-1 schedule books the debt move and the fiscal YTD repurchases filed in the 10-Q.
    """
    if bridge_period["period_end"] <= latest["period_end"]:
        return None
    annual_shares = opening.get("ending_shares")
    interim_debt, interim_shares = bridge_base["debt"], bridge_base["shares"]
    debt_move = abs(interim_debt - debt) / debt if debt > 0 else (1 if interim_debt > 0 else 0)
    share_move = abs(interim_shares - annual_shares) / annual_shares if annual_shares else 0
    if max(debt_move, share_move) <= FINANCING_MATERIALITY:
        return None
    repurchases = bridge_period["metrics"].get("share_repurchases")
    source = bridge_period.get("sources", {}).get("share_repurchases", {})
    if type(repurchases) not in (int, float) or repurchases < 0 or not source.get("url"):
        repurchases, source = 0, {}
    url = bridge_period.get("operating_bridge_source") or bridge_period["filing"]["url"]
    basis = (f"funded debt moved {debt_move:.0%} and common shares moved {share_move:.0%} after the "
             f"{latest['period_end']} annual balance sheet")
    note = (f"Forecast debt, interest and share count start from the {bridge_period['period_end']} 10-Q because {basis}. "
            f"Year 1 books a USD {interim_debt - debt:,.0f} million debt change"
            + (f" and USD {repurchases / 1e6:,.0f} million of fiscal YTD repurchases filed in the 10-Q"
               if repurchases else "") + ". The opening share count is the 10-Q count.")
    return {"debt_change": interim_debt - debt, "repurchases": repurchases / 1e6,
            "repurchases_url": source.get("url") or url, "period_end": bridge_period["period_end"],
            "url": url, "debt_move": debt_move, "share_move": share_move, "basis": basis, "note": note}


def prepare_operating_model(packet, treasury, quote, selection, candidates, overrides=()):
    """Build the independently calculated case from a validated research selection."""
    issuer = packet["issuer"]
    as_of = date.fromisoformat(packet["as_of"])
    quote_day = date.fromisoformat(quote["trading_date"])
    treasury_day = date.fromisoformat(treasury["date"])
    if (quote["ticker"] != issuer["ticker"] or quote["currency"] != "USD" or quote["close"] <= 0
            or not 0 <= (as_of - quote_day).days <= 7 or not 0 <= (as_of - treasury_day).days <= 7):
        raise ResearchGap("Verified USD market inputs are required for the valuation cutoff.")
    if selection["group"] not in candidates:
        raise ResearchGap(selection["reason"] if selection["group"] == "unsupported" else "Research selected an unavailable revenue grouping.")
    history = packet["actuals"]["periods"]
    latest = history[-1]
    interim = packet["interim_actuals"]["periods"]
    opening, cash, investments, debt, equity, wc_assets, wc_liab = _balance(latest)
    bridge_period = interim[-1] if interim else latest
    bridge_metrics, bridge_cash, bridge_investments, bridge_debt, _, _, _ = _balance(bridge_period)
    shares = _read(bridge_metrics, "ending_shares")
    from .operating_evidence import apply_capital_events
    bridge_base = {"cash": bridge_cash, "debt": bridge_debt, "shares": shares}
    bridge_cash, bridge_debt, shares, movements = apply_capital_events(
        packet, date.fromisoformat(bridge_period["period_end"]), as_of, bridge_cash, bridge_debt, shares,
        date.fromisoformat(bridge_period.get("share_count_date", bridge_period["period_end"])))
    financing_start = _financing_start(latest, bridge_period, opening, debt, bridge_base) if interim else None
    close = quote["close"]
    growth, roic = .025, .15
    ratios = []
    payouts = []
    normalization = []
    for period in history[-3:]:
        m = period["metrics"]
        sales = _read(m, "revenue")
        if sales <= 0:
            raise ResearchGap("A positive revenue base is required.")
        normalized, charges, gain = normalized_operating_income(m)
        normalization.append({"period_end": period["period_end"], "url": period["filing"]["url"],
                              "revenue": sales / 1e6, "operating_income": _read(m, "operating_income") / 1e6,
                              "charges": charges / 1e6, "gain": gain / 1e6, "normalized": normalized / 1e6})
        ratios.append((normalized / sales, _read(m, "depreciation_amortization") / sales,
                       _read(m, "capex") / sales, _read(m, "income_taxes") / max(1, _read(m, "income_before_taxes"))))
        if m.get("dividends_paid") is not None and (m.get("net_income") or 0) > 0:
            payouts.append(m["dividends_paid"] / m["net_income"])
    margin, da, capex, tax = map(median, zip(*ratios))
    reported_margin = median(row["operating_income"] / row["revenue"] for row in normalization)
    if interim:
        m = bridge_period["metrics"]
        ytd_sales = _read(m, "revenue")
        if ytd_sales <= 0:
            raise ResearchGap("Positive interim revenue is required.")
        capex = max(capex, _read(m, "capex") / ytd_sales)
    capex = max(da, capex)
    defaults = {"margin": margin, "capex": capex, "growth": growth, "roic": roic}
    changes = {row["target"]: row["value"] for row in overrides}
    used = {**defaults, **changes}
    margin, capex, growth, roic = (used[key] for key in OPERATING_GUIDES)
    if not .03 < margin < .8 or not 0 < tax < .5:
        raise ResearchGap("Profitability or taxes require normalization outside the standard model.")
    market = packet.get("valuation_market")
    from .fcfe_valuation import (GROWTH_STEP, case_revenues, comparable_beta, historical_beta,
                                 regression_evidence, select_beta)
    beta_evidence = None
    beta = 1.
    if market:
        regression = regression_evidence(market)
        comparable = comparable_beta(market.get("peer_betas", {}), bridge_debt, close * shares, tax)
        beta, beta_source = select_beta(regression, comparable)
        beta_evidence = {"regression": regression, "historical": historical_beta(regression), "comparable": comparable,
                         "source": beta_source, "forward": "Unavailable: no data source",
                         "implied_erp": market["erp"].get("implied")}
    cost = CostOfCapitalInputs(as_of, treasury["rate"], market["erp"]["value"] if market else .045,
                               beta, close * shares,
                               market["debt"]["value"] if market else treasury["rate"] + .02, bridge_debt, tax, 0, 0)
    capital = calculate_cost_of_capital(cost)
    wacc, cost_of_equity = capital.wacc, capital.cost_of_equity
    nci = bridge_metrics.get("noncontrolling_interest", 0)
    affiliate_book_value = bridge_period.get("nonoperating_equity_investments", 0) / 1e6
    bridge_investments += affiliate_book_value
    preferred = bridge_metrics.get("preferred_stock_book_value", 0)
    bridge = EquityBridge(as_of, bridge_cash, bridge_investments, bridge_debt, preferred, nci, 0, shares,
                          immaterial_minority_deficit=bool(bridge_period.get("immaterial_nci")) and nci < 0)
    end = date.fromisoformat(latest["period_end"])
    dates = _forecast_dates(history)
    if growth + .01 >= min(wacc - .01, cost_of_equity - .01, roic):
        raise ResearchGap("Terminal growth leaves insufficient room for the cost of equity, WACC and ROIC sensitivity range.")
    stub = (dates[0] - as_of).days / (dates[0] - end).days
    if not 0 < stub <= 1:
        raise ResearchGap("The first forecast year has ended. A new filing or explicit interim forecast is required.")
    data = candidates[selection["group"]]
    amounts = data["latest"].copy()
    recent = data.get("comparable_interim")
    rate_prior, rate_current = (recent["prior"], recent["current"]) if recent else (data["prior"], data["latest"])
    rates = {key: rate_current[key] / rate_prior[key] - 1 for key in amounts}
    from .drivers import FALLBACK, choose, forecast
    driver = choose(packet, selection)
    if driver["type"] == FALLBACK and data.get("basis"):
        driver = {**driver, "reason": f"{driver['reason'].rstrip()} {data['basis']}".strip()}
    path = forecast(driver, growth) if driver["type"] != FALLBACK else []
    if selection["group"] == "verified_operating_driver" and not path:
        raise ResearchGap("No verified operating driver or reconciled segment basis is available.")
    fade = revenue_fade(history)
    revenues = []
    segment_path = []
    anchor_scale = 1
    target, anchor_sources, anchor_note = None, {}, ""
    for index in range(1, 6):
        if path:
            amounts = {"Consolidated": path[index-1]*anchor_scale}
        else:
            for key, rate in rates.items():
                # Year 1 fades the line's recent rate halfway to terminal growth (before any guidance anchor);
                # years 2-5 follow the consolidated fade from the 3-year CAGR to 4%.
                amounts[key] *= 1 + (growth + (rate - growth) * .5 if index == 1 else fade["path"][index - 2])
        if index == 1:
            anchor_packet = {**packet, "earnings_interim": {}} if packet.get("acquisition_revenue_basis") else packet
            target, anchor_sources, anchor_note = _revenue_anchor(anchor_packet, dates[0].year, sum(amounts.values()), opening["revenue"])
            if target is not None:
                if target <= 0 or (interim and target < _read(bridge_metrics, "revenue")):
                    raise ResearchGap("The annual forecast is inconsistent with reported revenue.")
                scale = target / sum(amounts.values())
                anchor_scale = scale
                amounts = {key: value * scale for key, value in amounts.items()}
        revenues.append(sum(amounts.values()))
        if path:
            # The workbook splits driver revenue across filed lines in proportion to the latest mix.
            mix = sum(data["latest"].values())
            segment_path.append({key: revenues[-1] * value / mix for key, value in data["latest"].items()})
        else:
            segment_path.append(dict(amounts))
    if stub < 30/366 and target is None:
        raise ResearchGap("A short first-period forecast requires matched interim actuals or filed full-year guidance.")
    wc_ratio = (wc_assets - wc_liab) / opening["revenue"]
    normalized_capex = da + margin * (1 - tax) * growth / roic - growth * wc_ratio
    capex_path = [capex + (normalized_capex - capex) * i / 4 for i in range(5)]
    margin_step = min(.03, margin * .25)
    cases = {"Bear": (-margin_step, -GROWTH_STEP), "Base": (0, 0), "Bull": (margin_step, GROWTH_STEP)}
    case_revenue = {name: (revenues if not step else case_revenues(segment_path, data["latest"], step))
                    for name, (_, step) in cases.items()}
    scenarios = {}
    for name, (shift, _) in cases.items():
        flows = []
        previous = opening["revenue"]
        for index, (period, revenue) in enumerate(zip(dates, case_revenue[name], strict=True)):
            fcff = revenue * ((margin + shift) * (1-tax) + da - capex_path[index]) - (revenue-previous) * wc_ratio
            flows.append(DatedFCFF(period, fcff * (stub if index == 0 else 1)))
            previous = revenue
        terminal = TerminalAssumptions(case_revenue[name][-1] * (margin + shift) * (1-tax), growth, roic)
        scenarios[name] = discount_fcff(flows, as_of, wacc, terminal, bridge)
    # The legacy Phase 1 exporter models margin-only cases on unshifted revenue.
    legacy_scenarios = {"Base": scenarios["Base"]}
    for name, (shift, _) in cases.items():
        if name == "Base":
            continue
        flows, previous = [], opening["revenue"]
        for index, (period, revenue) in enumerate(zip(dates, revenues, strict=True)):
            fcff = revenue * ((margin + shift) * (1-tax) + da - capex_path[index]) - (revenue-previous) * wc_ratio
            flows.append(DatedFCFF(period, fcff * (stub if index == 0 else 1)))
            previous = revenue
        legacy_scenarios[name] = discount_fcff(
            flows, as_of, wacc, TerminalAssumptions(revenues[-1] * (margin + shift) * (1-tax), growth, roic), bridge)
    legacy_implied_margin = margin + (close - scenarios["Base"].value_per_share) / (
        (legacy_scenarios["Bull"].value_per_share - scenarios["Base"].value_per_share) / margin_step)
    from .fcfe_valuation import nonoperating_carry
    nonoperating = nonoperating_carry(history)
    other_income = sum(nonoperating.values())
    latest_ebitda = (_read(latest["metrics"], "operating_income") + _read(latest["metrics"], "depreciation_amortization")) / 1e6
    if latest_ebitda <= 0:
        raise ResearchGap("Positive latest-year EBITDA is required to hold debt / EBITDA.")
    leverage = {"debt": debt, "ebitda": latest_ebitda, "ratio": debt / latest_ebitda, "period_end": latest["period_end"],
                "url": latest["filing"]["url"]}
    model_leverage = leverage["ratio"]
    buyback_years = [(period["period_end"], period["filing"]["url"], period["metrics"].get("share_repurchases"))
                     for period in history[-3:]]
    filed_buybacks = [value / 1e6 for _, _, value in buyback_years if type(value) in (int, float) and isfinite(value)]
    buyback_spend = max(0., sum(filed_buybacks) / len(buyback_years)) if buyback_years else 0.
    if close <= 0 or buyback_spend / close >= shares:
        raise ResearchGap("Projected buybacks would retire every outstanding share.")
    buyback_note = (f"Forecast buybacks are the trailing three-year average filed repurchase spend, "
                    f"USD {buyback_spend:,.0f} million a year, at the dated close; a year without a filed "
                    f"PaymentsForRepurchaseOfCommonStock fact counts as zero ({len(filed_buybacks)} of "
                    f"{len(buyback_years)} years filed). The 12-month target divides by the projected share count one year out.")
    source = latest["filing"]["url"]
    labels = {key: re.sub(r"(?<=[a-z])(?=[A-Z])", " ", key.removesuffix("Member").removesuffix("Segment")) for key in data["latest"]}
    model = {
        "ticker": issuer["ticker"], "company": issuer["name"], "as_of": as_of, "close": close, "price_date": quote_day,
        "lines": labels, "history": history, "dates": dates, "old_sales": data["prior"], "sales": data["latest"],
        "comparable_revenue_interim": recent,
        "driver": driver, "report_evidence": packet.get("report_evidence", ()),
        "margin": margin, "da": da, "capex": capex, "normalize_capex": True, "wc_assets": wc_assets/opening["revenue"],
        "wc_liabilities": wc_liab/opening["revenue"], "opening_wc_assets": wc_assets, "opening_wc_liabilities": wc_liab,
        "metrics": {**opening, "equity_attributable_to_parent": equity}, "cash": cash, "investments": investments,
        "debt": debt, "shares": shares, "bridge_cash": bridge_cash, "bridge_investments": bridge_investments,
        "bridge_preferred": preferred, "capital_movements": movements, "bridge_base": bridge_base,
        "financing_start": financing_start,
        "immaterial_nci": bool(bridge_period.get("immaterial_nci")),
        "balance_evidence": [
            {"period_end": row["period_end"], "metric": key, "value": amount,
             "source": row.get("sources", {}).get(key, row.get("bridge_filing_provenance", row["filing"]))}
            for row in ((latest, bridge_period) if bridge_period["period_end"] != latest["period_end"] else (latest,))
            for key, amount in row["metrics"].items()
            if amount is not None and key in (
                "cash_and_equivalents", "short_term_investments", "available_for_sale_debt_securities_current",
                "current_debt", "long_term_debt", "commercial_paper", "short_term_borrowings",
                "finance_lease_current", "finance_lease_noncurrent", "noncontrolling_interest",
                "preferred_stock_book_value", "ending_shares")
        ],
        "bridge_debt": bridge_debt, "bridge_nci": nci, "bridge_affiliate_book_value": affiliate_book_value,
        "other_assets": opening["total_assets"]-cash-investments-wc_assets-_read(opening,"net_ppe"),
        "other_liabilities": opening["total_liabilities"]-debt-wc_liab,
        "cost": cost, "wacc": wacc, "growth": growth, "roic": roic, "stub": stub, "scenarios": scenarios, "revenue": revenues,
        "legacy_scenarios": legacy_scenarios, "legacy_implied_margin": legacy_implied_margin, "cost_of_equity": cost_of_equity, "beta": beta_evidence,
        "capex_path": tuple(capex_path), "wc_ratio": wc_ratio, "cases": cases,
        "case_revenue": case_revenue, "segment_path": segment_path, "growth_step": GROWTH_STEP,
        "other_income": other_income, "nonoperating": nonoperating, "leverage": leverage, "revenue_fade": fade,
        "normalization": normalization, "reported_margin": reported_margin,
        "buyback_spend": buyback_spend, "buyback_years": buyback_years,
        "peer_selection": packet.get("peer_selection"),
        "margin_step": margin_step,
        "revenue_anchor": target, "guided_defaults": defaults, "overrides": list(overrides),
        "filed_ytd_revenue": _read(bridge_metrics, "revenue") if interim else 0,
        "payout_ratio": min(1., max(0., median(payouts))) if len(payouts) == 3 else None,
        "sources": {"Annual financials and revenue lines": source, "Latest balance sheet": bridge_period["filing"]["url"],
                    **({"Operating driver disclosure": driver["url"]} if path else {}),
                    f"Closing price on {quote_day}": quote["url"], f"Treasury yield on {treasury_day}": treasury["url"],
                    **{f"Balance-sheet bridge XBRL {row['period_end']}": row["operating_bridge_source"]
                       for row in (latest, bridge_period) if "operating_bridge_source" in row},
                    **({"Comparable interim revenue lines": recent["source"]} if recent else {}), **anchor_sources,
                    **({"Full-year acquisition pro forma sales": packet["acquisition_revenue_basis"]["source"]}
                       if packet.get("acquisition_revenue_basis") else {}),
                    **{f"Completed financing {index}": row["url"] for index, row in enumerate(movements, 1)}},
        "thesis": selection["thesis"], "risks": selection["risks"],
        "notes": (
            selection["reason"], f"Revenue driver: {driver['type']}. {driver['reason']}",
            anchor_note,
            *((recent["note"],) if recent else ()),
            ("Operating-driver growth fades halfway annually toward labeled analyst industry proxies. Revenue line allocations use latest filed mix. "
             "The Stage 2d year 2-5 fade does not apply to operating-driver paths."
             if path else f"Year 1 fades each line's recent growth halfway toward terminal growth before any filed guidance anchor. "
                          f"Years 2-5 fade linearly from the {fade['years']}-year consolidated revenue CAGR ({fade['cagr']:.1%}) "
                          f"in year 2 to 4% nominal in year 5 (docs/method-notes.md). Terminal growth stays 2.5%."),
            f"The base EBIT margin is the three-year median normalized operating margin ({margin:.1%}; reported {reported_margin:.1%}): "
            "disclosed impairments, restructuring and litigation settlements are added back to each year's operating income "
            "(the larger of the combined provision or the components, so nothing is counted twice) and asset-sale gains are "
            "removed, assuming they sit within operating income. Other operating charges count only through those tags: the "
            "generic us-gaap other operating expense tag is recurring cost for most filers. The three-year median effective tax rate anchors taxes.",
            " Scenario margin shifts are capped at 25% of base margin or three percentage points, whichever is smaller. Bull and bear cases also add or subtract 0.5 percentage points to every revenue line's annual growth rate in every forecast year.",
            "Terminal value is Gordon growth on the final explicit year's FCFE: FCFE year 5 x (1 + g) / (cost of equity - g), "
            "with g = 2.5% as a labeled analyst assumption. Reinvestment is in the forecast cash flows; the ROIC-based "
            "terminal value (15% incremental ROIC, debt growing at g) is shown on the DCF tab as a check only. "
            "A final forecast year with capex below D&A is flagged, not changed.",
            ("Cost of equity uses CAPM with the 6.5% Damodaran historical equity risk premium (docs/method-notes.md); "
             "the Damodaran implied ERP is shown for reference. The historical beta is a five-year monthly regression "
             "against the S&P 500 (36 monthly returns required) and the comparable beta is the mean of peer betas "
             "unlevered at each peer's D/E and tax rate, relevered at the subject's. Forward beta is unavailable: no "
             "consensus source. Historical and comparable betas are weighted 50/50; the available one is used alone "
             "when the other is missing." if market else ""),
            buyback_note,
            "D&A uses the annual median ratio. Initial capex uses the greater of the annual median, available interim ratio and D&A ratio. It converges linearly to capex consistent with terminal reinvestment, adjusting for working capital.",
            ("The primary DCF discounts FCFE (net income plus D&A, less capex and working-capital investment, plus net "
             "borrowing from the debt schedule) at the CAPM cost of equity on the Cost of Equity tab. The beta "
             "weights the five-year monthly regression and the comparable beta 50/50. "
             "FCFF at WACC is shown on the DCF tab as a cross-check only."
             if market else "Legacy WACC uses a 4.5% ERP, beta of 1.0 and a debt spread of 2 points."),
            "Interest income, equity-method income and other nonoperating income are each carried forward at their own "
            "three-year filed median and shown on the forecast income statement. Because their income is in FCFE, cash, "
            "short-term investments and equity-method investments are not added to the FCFE value (value = PV of FCFE + PV "
            "of terminal value, less noncontrolling interests and preferred equity, per docs/method-notes.md). The FCFF "
            "cross-check still adds cash in its enterprise-value bridge.",
            "The 12-month price target rolls the FCFE value forward one year at the cost of equity and deducts expected "
            "dividends. Peer multiples are a cross-check and never enter the target.",
            "The first DCF period prorates full-year cash flow by remaining days. The equity bridge uses the latest filed balances, short-term investments, carrying debt, and ending common shares.",
            f"Funded debt each forecast year is debt / EBITDA held at the last actual year's {model_leverage:.2f}x times forecast EBITDA; "
            "the change is net borrowing in FCFE and interest accrues on opening debt. Reverse DCF, implied margin and the margin × "
            "growth grid hold debt flows at the base path. Noncurrent residual balances are held flat. When three years of dividend payments are available, projected payouts use the median historical payout ratio. Acquisitions are outside the forecast.",
            "Where no standalone liabilities total is tagged, the model derives it from filed liabilities and equity less equity.",
            "Reverse DCF solves for a constant operating margin at the observed price, holding the rest of the base model fixed. An absolute DCF gap of at least 50% requires analyst review.",
            *dict.fromkeys(note for row in (latest, bridge_period) for note in row.get("operating_bridge_notes", ())),
            *((financing_start["note"],) if financing_start else ()),
            *dict.fromkeys(row["sources"][key]["basis"] for row in (*history, bridge_period)
                           for key in ("operating_income", "depreciation_amortization")
                           if row.get("sources", {}).get(key, {}).get("basis")),
            *(("Full-year combined pro forma revenue replaces the historical revenue growth base. "
               "The acquirer's historical median operating margin and investment ratios are analyst proxies "
               "for the combined business. Synergies and acquiree-specific margins require review.",)
              if packet.get("acquisition_revenue_basis") else ()),
            *(("Completed post-balance financing movements adjust cash, carrying debt and common shares. "
               "Issuance and repayment principal are proxies for carrying-value changes. All movements are listed in Equity bridge.",)
              if movements else ()),
        ),
    }
    attach_fcfe(model)
    if market:
        from .valuation_summary import summarize
        comps = packet.get("valuation_comps")
        from .peer_selection import MIN_PEERS
        if comps and "subject" in comps:
            # Comps are a cross-check shown only when three peers qualify for a multiple.
            count = len(comps.get("rows", ()))
            summary = {k: v for k, v in comps["summary"].items() if v["count"] >= MIN_PEERS} if count >= MIN_PEERS else {}
            medians = [v["values"][1] for v in summary.values()]
            comps = {**comps, "summary": summary, "value": median(medians) if medians else None,
                     "reason": None if medians else (f"Insufficient peers: {count} qualified, {MIN_PEERS} required."
                                                     if count < MIN_PEERS else
                                                     f"Insufficient peers: no multiple has {MIN_PEERS} valid peer values.")}
        model["valuation_market"] = market
        model["valuation_comps"] = comps
        model["valuation_summary"] = summarize(model, comps)
        model["sources"].update({"Damodaran implied ERP": market["erp"]["url"],
                                  f"Broad market benchmark ({market['beta'].get('index_basis', 'FRED S&P 500 index')})": market["beta"]["index_url"],
                                  "Beta price observations": market["beta"]["url"]})
        if market["debt"].get("url"):
            model["sources"]["Pretax debt cost"] = market["debt"]["url"]
    return model


def attach_fcfe(model):
    """FCFE at the cost of equity for each case, from the model's statement inputs and debt schedule."""
    from .fcfe_valuation import FCFEInputs, value_fcfe
    cost = model["cost"]
    tax, ke, growth, roic, stub = (cost.marginal_tax_rate, model["cost_of_equity"], model["growth"], model["roic"],
                                   model["stub"])
    debt = model["debt"] + (model.get("financing_start") or {}).get("debt_change", 0)
    base = model["scenarios"]["Base"]
    inputs = FCFEInputs(
        model["metrics"]["revenue"], model["opening_wc_assets"] - model["opening_wc_liabilities"], model["wc_ratio"],
        tax, model["da"], model["capex_path"], (debt * cost.pretax_cost_of_debt,) * 5, model["other_income"], debt,
        cost.pretax_cost_of_debt, (0.,) * 5, stub, tuple(p.discount_years for p in base.periods), growth, roic,
        # docs/method-notes.md: value = PV of FCFE + PV of TV. Interest income on cash and investments is in FCFE,
        # so cash, short-term and equity-method investments are not added again; NCI and preferred are deducted.
        base.bridge.adjustment_millions + model["bridge_debt"] - model["bridge_cash"] - model["bridge_investments"],
        model["shares"], model["leverage"]["ratio"], 0.)
    model["fcfe_inputs"] = inputs
    model["fcfe"] = {name: value_fcfe(inputs, model["case_revenue"][name], model["margin"] + shift, ke)
                     for name, (shift, _) in model["cases"].items()}
    model["capex_below_da"] = inputs.capex_path[-1] < inputs.da
    revenues = model["revenue"]
    discount = [(1 + ke) ** -t for t in inputs.discount_years]
    per_point = (1 - tax) * (
        sum(r * f * (stub if i == 0 else 1) for i, (r, f) in enumerate(zip(revenues, discount, strict=True)))
        + revenues[-1] * (1 + growth) / (ke - growth) * discount[-1]) / inputs.shares
    model["implied_margin"] = model["margin"] + (model["close"] - model["fcfe"]["Base"].value_per_share) / per_point
    return model


PASS_LOW, PASS_HIGH = .5, 2.


def verify_valuation_range(model):
    """Stop when the base FCFE value is outside 0.5x to 2x the verified close."""
    base_value = (model.get("fcfe") or model["scenarios"])["Base"].value_per_share
    close = model["close"]
    ratio = base_value / close
    if not PASS_LOW <= ratio <= PASS_HIGH:
        implied_margin = model.get("implied_margin")
        detail = (f" The model uses a {model['margin']:.1%} EBIT margin and implies a {implied_margin:.1%} "
                  "margin at the verified close with other assumptions fixed."
                  if implied_margin is not None and model.get("margin") is not None else "")
        raise ResearchGap(
            f"Valuation outside 0.5–2×, analyst review needed. The base FCFE value is ${base_value:.2f} per share "
            f"versus a ${close:.2f} close ({ratio:.2f}×).{detail}")
    return None

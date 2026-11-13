"""Finite-life, dated net asset valuation for Newmont's producing portfolio."""

from dataclasses import dataclass
from datetime import date
from math import fsum, log
from pathlib import Path
from statistics import median

from .fdx_current import _dated_source, _num, _reconcile, _sources
from .nem_current import BASE_METALS, METALS, _evidence, _million_quantity, _value, _weights
from .nem_forward_statements import FY27, Q4, run_nem_statements


@dataclass(frozen=True)
class DatedFlow:
    date: date
    mine_cash_millions: float
    corporate_cash_millions: float
    closure_cash_millions: float
    merian_minority_cash_millions: float

    @property
    def consolidated_cash_millions(self) -> float:
        return self.mine_cash_millions - self.corporate_cash_millions - self.closure_cash_millions


def _years(start, end):
    return (end - start).days / 365


def _pv(amount, start, end, rate):
    return amount / (1 + rate) ** _years(start, end)


def _rate(packet, name):
    return _num(packet[name], "value")


def _capital_cost(packet, statements, as_of, checks):
    sources = _sources(packet, as_of)
    for key in ("risk_free_rate", "equity_risk_premium", "unlevered_beta", "pretax_cost_of_debt", "tax_rate", "inflation"):
        evidence = packet[key]
        observation = date.fromisoformat(evidence["observed_on"]) if "observed_on" in evidence else None
        _dated_source(evidence["source"], sources, key, observed_on=observation)
    risk_free, erp, beta_u, cost_of_debt, tax = (
        _rate(packet, key) for key in ("risk_free_rate", "equity_risk_premium", "unlevered_beta", "pretax_cost_of_debt", "tax_rate")
    )
    if not all(0 < rate < 1 for rate in (risk_free, erp, cost_of_debt, tax)) or beta_u <= 0:
        raise ValueError("Invalid Newmont discount assumptions.")
    funding = statements["financing_evidence"]
    structure = packet["capital_structure"]
    shares = funding["opening"]["latest_shares_outstanding"] / 1e6
    close = funding["reference_close"]["price_usd"]
    for key, actual in (
        ("cash_millions", funding["opening"]["cash_millions"]),
        ("common_shares_millions", shares), ("reference_price_usd", close),
        ("debt_millions", funding["opening"]["debt_carrying_millions"]),
        ("finance_leases_millions", funding["opening"]["finance_leases_current_millions"] + funding["opening"]["finance_leases_noncurrent_millions"]),
        ("ngm_cash_obligation_millions", funding["ngm_settlement"]["cash_payment_millions"]),
    ):
        _reconcile(checks, f"Valuation capital input {key}", _value(structure, key), actual)
    equity = shares * close
    debt = _value(structure, "debt_millions") + _value(structure, "finance_leases_millions")
    beta = beta_u * (1 + (1 - tax) * debt / equity)
    ke = risk_free + beta * erp
    wacc = (ke * equity + cost_of_debt * (1 - tax) * debt) / (equity + debt)
    return {"wacc": wacc, "cost_of_equity": ke, "levered_beta": beta, "market_equity_millions": equity,
            "gross_debt_millions": debt, "after_tax_cost_of_debt": cost_of_debt * (1 - tax)}


def _owned_fraction(site):
    return site["ownership"] if site["accounting"] == "full" else 1.0


def _annual_pacing(record, metal, years):
    schedule = record.get("reserve_pacing_weights", {})
    periods = schedule.get(metal, schedule.get("all_metals"))
    if periods is None:
        return dict.fromkeys(years, 1.0)
    weights = {}
    for period in periods:
        first, _, last = period.partition("-")
        group = range(int(first), int(last or first) + 1)
        if not group or any(year not in years or year in weights for year in group):
            raise ValueError(f"Invalid reserve pacing period {period} for {record['name']} {metal}.")
        weights.update(dict.fromkeys(group, _value(periods, period) / len(group)))
    if weights.keys() != years or not any(weights.values()):
        raise ValueError(f"Incomplete reserve pacing weights for {record['name']} {metal}.")
    return weights


def _reserve_volumes(evidence, operations, checks):
    """Mine quantities stay on the consolidation basis used for operating cash flow."""
    rows = {site: record for site, record in evidence["sites"].items() if record.get("accounting") != "equity_method"}
    if evidence["units"] != {"gold": "koz", "silver": "Moz", "copper": "kt", "lead": "kt", "zinc": "kt"}:
        raise ValueError("Newmont mine reserves must use the production model's units.")
    active = {site: row for site, row in operations["periods"][FY27]["mines"].items() if row["economics"]}
    if active.keys() != rows.keys():
        raise ValueError("Mine-life evidence and the Newmont consolidated mine forecast cover different sites.")
    plans = {}
    fy26 = operations["periods"]["fy_2026"]["mines"]
    for site, latest in active.items():
        record = rows[site]
        fraction = _owned_fraction(operations["mine_register"][site])
        end = record["model_production_end_year"]
        if type(end) is not int or not 2027 <= end <= 2100:
            raise ValueError(f"Invalid finite forecast life for {site}.")
        profiles = record.get("production_profile", {})
        for year, metals in profiles.items():
            if not 2028 <= int(year) <= end or metals.keys() - latest["volumes"].keys():
                raise ValueError(f"{site} production profile falls outside its period or metals.")
        annual = {year: {} for year in range(2028, end + 1)}
        controls = {}
        for metal, volume in latest["volumes"].items():
            reserve = record["reserves"][metal]
            contained, recovery = _value(reserve, "contained"), _value(reserve, "recovery")
            if not 0 < recovery <= 1:
                raise ValueError(f"Invalid reserve recovery for {site} {metal}.")
            documented = record.get("documented_lom_recovered", {})
            recovered = (_value(documented, metal) if metal in documented else contained * recovery) / fraction
            used = volume.produced + fy26[site]["volumes"][metal].produced
            eligible = reserve.get("eligible_for_base_cap", True)
            # A smaller existing-asset profile can be used with an ineligible pooled reserve.
            explicit = {int(year): metals[metal] for year, metals in profiles.items() if metal in metals}
            if not eligible and len(explicit) != len(annual):
                raise ValueError(f"{site} {metal} needs a complete finite existing-asset profile.")
            planned = {}
            for year, item in explicit.items():
                value = _value(item, "value")
                if item["kind"] == "multiplier_2027":
                    planned[year] = value * volume.produced
                elif item["kind"] == "attributable_produced":
                    planned[year] = value / fraction
                else:
                    raise ValueError(f"Unknown mine profile for {site} {metal}.")
            available = max(0, recovered - used)
            explicit_total = fsum(planned.values())
            open_years = annual.keys() - planned.keys()
            remaining = available
            if eligible and explicit_total > available:
                planned = _weights(planned, available, f"{site} {metal} reserve-limited source profile")
            pacing = _annual_pacing(record, metal, open_years)
            capacity = max(pacing.values(), default=0.0)
            allocated = _weights(pacing, max(0.0, available - fsum(planned.values())), f"{site} {metal} pacing") if open_years else {}
            for year, amounts in annual.items():
                selected = planned[year] if year in planned else min(allocated[year], volume.produced * pacing[year] / capacity)
                if eligible:
                    selected = min(remaining, selected)
                    remaining -= selected
                amounts[metal] = selected
            forward = fsum(amounts[metal] for amounts in annual.values())
            if eligible:
                _reconcile(checks, f"{site} {metal} remaining reserve allocation", forward + remaining, available)
            controls[metal] = {"recovered_at_2025_end": recovered, "recovery_basis": "technical_plan" if metal in documented else "reserve_recovery_rate",
                               "modeled_2026_and_2027_production": used,
                               "modeled_post_2027_production": forward, "reserve_cap_applied": eligible,
                               "near_term_production_above_recovered_reserve": max(0, used - recovered) if eligible else None,
                               "explicit_profile_above_remaining_reserve": max(0, explicit_total - available) if eligible else None,
                               "unmodeled_recovered_reserve": remaining if eligible else None}
        plans[site] = {"annual": annual, "reserve_controls": controls, "model_production_end_year": end,
                       "source_ids": record["source_ids"], "basis_note": record["basis_note"]}
    return plans


def _check_sources(life, capital, outlook, valuation, as_of):
    mine_sources = _sources(life, as_of)
    for site, record in life["sites"].items():
        if not record["source_ids"]:
            raise ValueError(f"{site} needs a dated mine plan or reserve source.")
        for source in record["source_ids"]:
            _dated_source(source, mine_sources, f"{site} finite-life forecast")
    capital_sources = _sources(capital, as_of)
    for label in ("sustaining", "development", "closure"):
        for source in capital[label]["source_ids"]:
            _dated_source(source, capital_sources, label)
    for project, record in capital["development"]["projects"].items():
        for source in record["source_ids"]:
            _dated_source(source, capital_sources, project)
    _dated_source(outlook["source"], _sources(outlook, as_of), "2028 World Bank price outlook")
    expected_units = {metal: "USD/metric_tonne" if metal in BASE_METALS else "USD/troy_ounce" for metal in METALS}
    if outlook["units"] != expected_units:
        raise ValueError("Newmont long-term price quotes do not use the model's expected units.")
    value_sources = _sources(valuation, as_of)
    for label in ("capital_structure", "merian", "fourmile"):
        for source in valuation[label]["sources"]:
            _dated_source(source, value_sources, label)


def _closure(capital, statements, checks):
    row = capital["closure"]
    balance_date = date(2026, 9, 30)
    historical_cash = _value(row, "actual_9m_2026_cash_millions")
    liability = _value(row, "reported_september_2026_book_millions")
    _reconcile(checks, "Closure book liability to statements", liability,
               statements["capital_schedule"]["historical"]["closing_closure_liability_millions"])
    total = _value(row, "reported_fy_2025_undiscounted_millions")
    _reconcile(checks, "Undiscounted closure obligations by tenor",
               _value(row, "reported_fy_2025_undiscounted_current_millions") + _value(row, "reported_fy_2025_undiscounted_noncurrent_millions"),
               total)
    cash = {int(year): amount for year, amount in row["scheduled_cash_by_year_millions"].items()}
    for year, period in ((2026, Q4), (2027, FY27)):
        _reconcile(checks, f"{year} closure payments to linked statements", cash[year],
                   statements["capital_schedule"]["periods"][period].closure_cash_millions)
    _reconcile(checks, "2028 closure payment to outlook", cash[2028], statements["capital_schedule"]["fy_2028_indicative_closure_cash_millions"])
    residual = total - historical_cash - fsum(cash.values())
    _reconcile(checks, "Post-2028 nominal closure residual", residual, _value(row, "undiscounted_residual_cash_millions"))
    rate = _value(row, "calibration_accretion_rate")
    if not 0 < rate < 1 or residual <= 0:
        raise ValueError("Invalid residual Newmont closure calibration.")
    remaining_pv = liability - fsum(_pv(amount, balance_date, date(year, 12, 31), rate) for year, amount in cash.items())
    if not 0 < remaining_pv < residual:
        raise ValueError("Reported closure obligation does not support residual cash calibration.")
    effective_years = log(residual / remaining_pv) / log(1 + rate)
    latest = next((year for year in range(2030, 2101) if _years(balance_date, date(year, 12, 31)) >= effective_years), None)
    if latest is None:
        raise ValueError("Closure cash maturity exceeds the model's supported horizon.")
    early = latest - 1
    factor_early, factor_late = (_pv(1, balance_date, date(year, 12, 31), rate) for year in (early, latest))
    if not factor_late <= remaining_pv / residual <= factor_early or early < 2029:
        raise ValueError("Closure residual would overlap the independently estimated near-term cash payments.")
    early_cash = (remaining_pv - residual * factor_late) / (factor_early - factor_late)
    cash[early], cash[latest] = early_cash, residual - early_cash
    _reconcile(checks, "Modeled future undiscounted closure cash", fsum(cash.values()) + historical_cash, total)
    _reconcile(checks, "Calibrated closure present value at September 30",
               fsum(_pv(amount, balance_date, date(year, 12, 31), rate) for year, amount in cash.items()), liability)
    return cash


def _peers(inputs_dir, as_of, statements, checks):
    data = _evidence(inputs_dir, "comps", as_of)
    sources = _sources(data, as_of)
    quote = statements["financing_evidence"]["reference_close"]
    close_date = date.fromisoformat(data["close_date"])
    if close_date.isoformat() != quote["observed_on"] or close_date >= as_of:
        raise ValueError("Newmont and peers must use the same preceding closing-price date.")
    actuals = statements["financing_evidence"]["actuals"]
    subject_eps = actuals["q4_2025"]["diluted_eps_usd"] + actuals["ytd_2026"]["diluted_eps_usd"]
    peers = {}
    if set(data["peers"]) != {"AEM", "B", "KGC"}:
        raise ValueError("Newmont requires the three dated gold-miner comparisons.")
    for ticker, row in data["peers"].items():
        for key, period in (("close_source", close_date), ("q4_source", date(2025, 12, 31)), ("ytd_source", date(2026, 9, 30))):
            _dated_source(row[key], sources, f"{ticker} {key}", observed_on=period)
        eps = _value(row, "q4_2025_eps") + _value(row, "ytd_2026_eps")
        close = _value(row, "close_usd")
        if eps <= 0 or close <= 0:
            raise ValueError(f"{ticker} requires positive price and trailing earnings.")
        multiple = close / eps
        peers[ticker] = {"company": row["company"], "close_usd": close, "reported_ltm_diluted_eps_usd": eps,
                         "price_to_earnings": multiple, "implied_nem_spot_usd": multiple * subject_eps}
    multiple = median(row["price_to_earnings"] for row in peers.values())
    _reconcile(checks, "Peer median implied Newmont price", median(row["implied_nem_spot_usd"] for row in peers.values()),
               multiple * subject_eps)
    return {"peers": peers, "quote_date": close_date, "subject_ltm_eps_usd": subject_eps,
            "subject_price_to_earnings": quote["price_usd"] / subject_eps, "median_price_to_earnings": multiple,
            "median_implied_nem_spot_usd": multiple * subject_eps, "basis": data["basis"], "sources": data["sources"]}


def _sustaining_allocations(capital, statements, sites, checks):
    row = capital["sustaining"]
    evidence = row["site_weights"]
    if set(evidence) != {*sites, "corporate_and_other"}:
        raise ValueError("Newmont sustaining evidence must cover the modeled sites and corporate spending.")
    old = {site: _value(data, "reported_fy_2025_millions") for site, data in evidence.items()}
    recent = {site: _value(data, "reported_9m_2026_millions") for site, data in evidence.items()}
    _reconcile(checks, "FY2025 site sustaining capex to consolidated",
               fsum(old.values()) + _value(row, "fy_2025_divested_millions"), _value(row, "reported_fy_2025_millions"))
    _reconcile(checks, "Nine-month site sustaining capex to consolidated", fsum(recent.values()),
               statements["operating_forecast"]["historical_costs_and_capital"]["consolidated"]["sustaining_capital_accrual_millions"])
    blend = _value(row, "site_weight_blend_9m_2026")
    if blend > 1:
        raise ValueError("Sustaining capital blend must be between 0% and 100%.")
    budget = statements["capital_schedule"]["periods"][FY27].sustaining_cash_capex_millions
    before, after = _weights(old, budget, "2025 sustaining capital"), _weights(recent, budget, "2026 sustaining capital")
    amounts = {site: (1 - blend) * before[site] + blend * after[site] for site in before}
    _reconcile(checks, "FY2027 allocation of sustaining capex", fsum(amounts.values()), budget)
    return amounts


def _prices_for_year(price_packet, outlook, operating, year, inflation, gold_shift, byproduct_shift):
    """Use physical streaming economics, with contractual silver cash escalated separately."""
    pounds = price_packet["pounds_per_metric_tonne"]
    factor = (1 + inflation) ** (year - 2028)
    stream = price_packet["silver_stream"]
    share = stream["share_of_production"]
    price_stream = stream["cash_price_usd"] * (1 + min(inflation, stream["annual_inflation_cap"])) ** (year - 2027)
    prices = {}
    for metal in METALS:
        realized = operating["modeled_prices"][FY27][metal]
        before = realized.benchmark_usd
        benchmark = _value(outlook, metal)
        if metal in BASE_METALS:
            benchmark /= pounds
        # Carry a guided 2027 benchmark change into the long-term deck on the same per-unit basis.
        before_evidence = price_packet["metals"][metal]["annual_benchmark_2027"]["price_usd"]
        market = (benchmark + before - before_evidence + gold_shift if metal == "gold"
                  else (benchmark + before - before_evidence) * (1 + byproduct_shift)) * factor
        if market <= 0:
            raise ValueError(f"Newmont long-term {metal} benchmark must remain positive.")
        if metal == "silver":
            base_cash = stream["cash_price_usd"]
            realized_ratio = realized.gross_usd / ((1 - share) * before + share * min(before, base_cash))
            gross = realized_ratio * ((1 - share) * market + share * min(market, price_stream))
        else:
            gross = market * realized.gross_usd / before
        prices[metal] = gross + realized.treatment_usd * factor
    return prices


def _working_net(working):
    # Release trade balances as sites wind down. Stockpile carrying balances receive no separate terminal recovery.
    return working.trade_receivables - working.accounts_payable - working.operating_accruals


def _site_near_cash(period, operations, capital, sustaining, site, tax):
    mine = operations["periods"][period]["mines"][site]["economics"]
    expense = fsum(line.cas_millions for line in mine.values())
    sales = fsum(line.sales_millions - line.noncash_stream_amortization_millions for line in mine.values())
    dep = fsum(capital["periods"][period].depreciation_by_site_and_metal[site].values())
    capex = sustaining[site]
    if period == Q4:
        capex *= capital["periods"][Q4].sustaining_cash_capex_millions / capital["periods"][FY27].sustaining_cash_capex_millions
    return (sales - expense - dep) * (1 - tax) + dep - capex


def _near_periods(statements, capital, sustaining, valuation, prices, royalty_slope, gold_shift, byproduct_shift, cost_shift):
    operations = statements["operating_forecast"]
    tax = operations["cost_guidance"]["full_year"]["adjusted_income_and_mining_tax_rate"]
    minority = valuation["merian"]["nci_share"]
    q4_fraction = statements["cutoff"]["calendar_days_remaining"] / 92
    cave = _value(capital["development"], "fy_2027_conditional_red_chris_cash_millions")
    flows = []
    shocks = {}
    for period, fraction in ((Q4, q4_fraction), (FY27, 1)):
        source = statements["periods"][period]
        op_period = operations["periods"][period]
        capital_period = statements["capital_schedule"]["periods"][period]
        realized = operations["modeled_prices"][period]
        gold_change = gold_shift * realized["gold"].gross_usd / realized["gold"].benchmark_usd
        gold_ounces_millions = op_period["metals"]["gold"]["volume"].sold / 1000
        gold_revenue = gold_change * gold_ounces_millions
        # Preserve silver's fixed streamed cash portion when applying commodity sensitivity.
        nonstream = 1 - prices["silver_stream"]["share_of_production"]
        silver_adjusted_market_fraction = nonstream * realized["silver"].benchmark_usd
        silver_gross_slope = realized["silver"].gross_usd / (silver_adjusted_market_fraction
                             + (1 - nonstream) * min(realized["silver"].benchmark_usd, prices["silver_stream"]["cash_price_usd"]))
        silver_revenue = silver_adjusted_market_fraction * silver_gross_slope * byproduct_shift * op_period["metals"]["silver"]["volume"].sold
        other_revenue = fsum(op_period["metals"][metal]["economics"].sales_millions
                             * realized[metal].gross_usd / realized[metal].net_usd * byproduct_shift for metal in BASE_METALS)
        gross_cost = op_period["cas_millions"] * cost_shift + royalty_slope * gold_revenue
        cash_delta = (gold_revenue + silver_revenue + other_revenue - gross_cost) * (1 - tax)
        consolidated = source.cash_flow["core_consolidated_unlevered_free_cash_flow"] + cash_delta + (cave if period == FY27 else 0)
        merian_mine = op_period["mines"]["merian"]
        merian_gold_millions = merian_mine["volumes"]["gold"].sold / 1000
        merian_change = (gold_change * merian_gold_millions * (1 - royalty_slope)
                         - merian_mine["economics"]["gold"].cas_millions * cost_shift) * (1 - tax)
        merian = _site_near_cash(period, operations, statements["capital_schedule"], sustaining, "merian", tax)
        closure = capital_period.closure_cash_millions
        flows.append(DatedFlow(source.period_end, (consolidated + closure) * fraction, 0, closure * fraction,
                               (merian + merian_change) * minority * fraction))
        shocks[period] = cash_delta
    return flows, shocks


def _long_cash(statements, reserves, capital, valuation, prices, sustaining, closure, royalty_slope, gold_shift, byproduct_shift, cost_shift):
    operating = statements["operating_forecast"]
    inflation = _rate(valuation, "inflation")
    operations = operating["periods"][FY27]["mines"]
    expenses = statements["periods"][FY27].income_statement
    base_capital = statements["capital_schedule"]["periods"][FY27]
    tax = expenses["effective_tax_rate"]
    cas = {site: fsum(row.cas_millions for row in operations[site]["economics"].values()) for site in reserves}
    portfolio_cas = fsum(cas.values())
    tax_base = _weights({site: fsum(base_capital.depreciation_by_site_and_metal[site].values()) for site in reserves},
                        base_capital.closing_ppem_millions, "starting depreciation basis")
    corporate = -fsum(expenses[key] for key in ("exploration", "advanced_projects", "general_administrative", "other_expense_net"))
    projects = capital["development"]["projects"]
    pounds = prices["pounds_per_metric_tonne"]
    base_gold = operating["modeled_prices"][FY27]["gold"].net_usd
    working_base = _working_net(statements["periods"][FY27].inventory_and_working_capital["closing"])
    prior_working = _weights(cas, working_base, "working capital")
    site_cash = {site: {} for site in reserves}
    result = []
    last = max(max(closure), *(row["model_production_end_year"] + 1 for row in reserves.values()),
               *(int(year) for year in capital["development"]["base_cash_by_year_millions"]))
    for year in range(2028, last + 1):
        inflator = (1 + inflation) ** (year - 2027)
        realized = _prices_for_year(prices, valuation["world_bank_2028_prices"], operating, year, inflation, gold_shift, byproduct_shift)
        total_cost_base = 0.0
        for site, plan in reserves.items():
            amounts = plan["annual"].get(year, {})
            metal_cash = operations[site]["economics"]
            historical_volumes = operations[site]["volumes"]
            baseline_cas = 0.0
            dep = 0.0
            sales = 0.0
            for metal, base in metal_cash.items():
                amount = amounts.get(metal, 0.0)
                baseline_cas += base.cas_usd_per_unit * _million_quantity(metal, amount, pounds)
                dep += base_capital.depreciation_by_site_and_metal[site][metal] * amount / historical_volumes[metal].sold
                sales += realized[metal] * _million_quantity(metal, amount, pounds)
            activity = baseline_cas / cas[site]
            total_cost_base += baseline_cas
            mine_cost = baseline_cas * inflator * (1 + cost_shift) + (realized["gold"] - base_gold * inflator) * royalty_slope * amounts.get("gold", 0.0) / 1000
            reinvestment = sustaining[site] * activity * inflator * (1 + _num(capital["sustaining"], "annual_escalation")) ** (year - 2027)
            mine_development = fsum(
                project.get("incremental_cash_by_year_millions", {}).get(str(year), 0)
                for project in projects.values() if project["site"] == site
            )
            dep = min(dep, tax_base[site] + reinvestment + mine_development)
            tax_base[site] += reinvestment + mine_development - dep
            taxable = sales - mine_cost - dep
            nwc = working_base * baseline_cas / portfolio_cas * inflator
            cash_release = prior_working[site] - nwc
            prior_working[site] = nwc
            site_cash[site][year] = {
                "production_and_sales": amounts, "cash_sales_millions": sales, "cas_millions": mine_cost,
                "sustaining_cash_capex_millions": reinvestment, "depreciation_proxy_millions": dep,
                "development_cash_capex_millions": mine_development,
                "working_capital_release_millions": cash_release,
                "core_cash_millions": sales - mine_cost - max(0, taxable) * tax - reinvestment - mine_development + cash_release,
            }
        activity = total_cost_base / portfolio_cas
        corp_cash = corporate * activity * inflator * (1 - tax) + sustaining["corporate_and_other"] * activity * inflator
        result.append(DatedFlow(date(year, 12, 31), fsum(site[year]["core_cash_millions"] for site in site_cash.values()), corp_cash,
                                closure.get(year, 0.0), site_cash["merian"][year]["core_cash_millions"] * valuation["merian"]["nci_share"]))
    return result, site_cash


def _affiliate_marks(valuation, statements, as_of, checks):
    rows = valuation["affiliates"]
    sources = _sources(valuation, as_of)
    history = statements["historical_statements"]["schedules"]["equity_method_investments"]["2026-09-30"]
    for key, actual_key in (("lundin_gold", "lundin_gold"), ("pueblo_viejo", "pueblo_viejo")):
        _reconcile(checks, f"{key} carrying value to SEC filing", _value(rows[key], "september_carrying_millions"), history[actual_key])
    for key in ("lundin_gold", "pueblo_viejo", "lunr_royalties"):
        for source in rows[key]["sources"]:
            _dated_source(source, sources, key)
    for key in ("lundin_gold", "lunr_royalties"):
        row = rows[key]
        quote_on = date.fromisoformat(row["price_observed_on"])
        if quote_on.isoformat() != statements["financing_evidence"]["reference_close"]["observed_on"]:
            raise ValueError(f"{key} should use the same equity market date as Newmont.")
        if date.fromisoformat(row["fx_observed_on"]) > quote_on:
            raise ValueError(f"{key} cannot use FX from after the share price date.")

    lundin, lunr, pv, other = (rows[key] for key in ("lundin_gold", "lunr_royalties", "pueblo_viejo", "other_investments"))
    _dated_source(other["source"], sources, "Other investments")
    _reconcile(checks, "Marketable and other securities to SEC filing", _value(other, "marketable_and_other_carrying_millions"),
               history["noncurrent_marketable_equity_and_other"])
    _reconcile(checks, "Pueblo Viejo pro-rata shareholder debt", _value(pv, "shareholder_loan_newmont_q2_millions")
               + _value(pv, "shareholder_loan_barrick_q2_millions"), _value(pv, "shareholder_loan_total_q2_millions"))
    _reconcile(checks, "Pueblo Viejo pro-rata mine value", _value(pv, "ownership") * _value(pv, "mine_npv_100pct_millions")
               + _num(pv, "shareholder_loan_net_adjustment_millions"), _value(pv, "base_value_millions"))
    old_lunr = _value(lunr, "shares") * _value(lunr, "september_price_cad") / _value(lunr, "september_cad_per_usd") / 1e6
    residual = _value(other, "marketable_and_other_carrying_millions") - old_lunr
    if residual < 0:
        raise ValueError("The September LunR proxy exceeds the entire securities carrying value.")
    return {
        "lundin_gold": _value(lundin, "shares") * _value(lundin, "price_cad") / _value(lundin, "cad_per_usd") / 1e6,
        "lunr_royalties": _value(lunr, "shares") * _value(lunr, "price_cad") / _value(lunr, "cad_per_usd") / 1e6,
        "pueblo_viejo": _value(pv, "base_value_millions"),
        "other_investment_securities": residual,
        "nueva_union_and_norte_abierto": _value(other, "development_projects_base_millions"),
    }


def _porcupine_receivable(valuation, horizon, checks):
    row = valuation["affiliates"]["other_investments"]
    face = _value(row, "porcupine_deferred_consideration_face_millions")
    count = row["porcupine_deferred_consideration_annual_payments"]
    first = date.fromisoformat(row["porcupine_deferred_consideration_first_pay_on"])
    if type(count) is not int or count < 1 or first <= horizon:
        raise ValueError("The Porcupine receivable schedule must start after the target date.")
    payments = {first.replace(year=first.year + year): face / count for year in range(count)}
    _reconcile(checks, "Porcupine installment schedule to face value", fsum(payments.values()), face)
    return payments


def _target_bridge(statements, capital, horizon, flows, shocks, checks):
    q4, fy27 = (statements["periods"][key] for key in (Q4, FY27))
    elapsed = (horizon - fy27.period_start).days
    days = (fy27.period_end - fy27.period_start).days + 1
    if not 0 < elapsed < days:
        raise ValueError("The target must fall within the linked FY2027 forecast.")
    fraction = elapsed / days
    q4_after = statements["cutoff"]["calendar_days_remaining"] / 92
    fy27_common_dividends = fy27.financing.common_dividends
    # The recurring quarterly payments are modeled through September. The fourth is after the November horizon.
    dividend_payments = statements["financing_evidence"]["dividends"]["fy_2027_payments"]
    if dividend_payments != 4 or not date(2027, 10, 1) <= horizon < date(2027, 12, 1):
        raise ValueError("The target must follow three modeled annual dividends and precede December.")
    fy27_dividends_paid = fy27_common_dividends * 3 / 4
    cash_pre_2027 = q4.cash_flow["closing_cash"] + shocks[Q4] * q4_after
    avoided_cave = _value(capital["development"], "fy_2027_conditional_red_chris_cash_millions")
    nondividend_cash = fy27.cash_flow["net_cash_change"] + fy27_common_dividends + avoided_cave + shocks[FY27]
    gross_cash = cash_pre_2027 + nondividend_cash * fraction - fy27_dividends_paid
    expected_year_end = fy27.cash_flow["closing_cash"] + shocks[Q4] * q4_after + avoided_cave + shocks[FY27]
    _reconcile(checks, "Rolled target cash and remaining cash to FY2027 close", gross_cash + nondividend_cash * (1 - fraction)
               - (fy27_common_dividends - fy27_dividends_paid), expected_year_end)
    debt = q4.financing.closing_notes + q4.financing.closing_lease_current + q4.financing.closing_lease_noncurrent
    debt -= (fy27.financing.lease_principal + fy27.financing.note_principal) * fraction
    stock_awards = q4.cash_flow["operating_items"]["stock_awards_proxy"] * q4_after + fy27.cash_flow["operating_items"]["stock_awards_proxy"] * fraction

    def affiliate_cash(period):
        cash = period.cash_flow
        return (cash["operating_items"]["affiliate_operating_distributions_proxy"]
                + cash["investing_items"]["affiliate_return_of_capital_proxy"] + cash["investing_items"]["affiliate_expansion_contributions"])

    def nci_cash_paid(period):
        items = period.cash_flow["financing_items"]
        return items["distributions_to_noncontrolling_interests"] + items["funding_from_noncontrolling_interests"]

    # With unchanged investee marks, remove the corresponding distributions from accumulated cash.
    affiliate_cash_retained = affiliate_cash(q4) * q4_after + affiliate_cash(fy27) * fraction
    nci_cash_retained = (flows[0].merian_minority_cash_millions + flows[1].merian_minority_cash_millions * fraction
                         + nci_cash_paid(q4) * q4_after + nci_cash_paid(fy27) * fraction)
    shares = fy27.financing.diluted_shares_millions
    dividends_per_share = (q4.financing.common_dividends + fy27_dividends_paid) / fy27.financing.closing_shares_millions
    return {
        "gross_cash_millions": gross_cash, "net_cash_adjustment_millions": -stock_awards - affiliate_cash_retained - nci_cash_retained,
        "debt_and_finance_leases_millions": debt, "ngm_obligation_millions": 0, "shares_millions": shares,
        "cash_fraction_2027": fraction, "stock_awards_cash_adjustment_millions": stock_awards,
        "affiliate_cash_adjustment_millions": affiliate_cash_retained, "merian_cash_adjustment_millions": nci_cash_retained,
    }, dividends_per_share


def _nav(flows, valuation_date, bridge, affiliates, receivable, rate):
    operating_pv = fsum(_pv(row.mine_cash_millions - row.corporate_cash_millions, valuation_date, row.date, rate) for row in flows)
    closure = fsum(_pv(row.closure_cash_millions, valuation_date, row.date, rate) for row in flows)
    nci = fsum(_pv(row.merian_minority_cash_millions, valuation_date, row.date, rate) for row in flows)
    enterprise = operating_pv - closure
    receivable_pv = fsum(_pv(amount, valuation_date, payment, rate) for payment, amount in receivable.items())
    assets = fsum(affiliates.values()) + receivable_pv
    equity = fsum((enterprise, -nci, assets, bridge["gross_cash_millions"], bridge["net_cash_adjustment_millions"],
                   -bridge["debt_and_finance_leases_millions"], -bridge["ngm_obligation_millions"]))
    return {"date": valuation_date, "mine_and_corporate_pv_millions": operating_pv, "closure_pv_millions": closure,
            "consolidated_operating_nav_millions": enterprise, "merian_minority_nav_millions": nci,
            "nonoperating_investments_millions": assets, "porcupine_receivable_pv_millions": receivable_pv,
            "equity_nav_millions": equity, "value_per_share": equity / bridge["shares_millions"],
            "bridge": bridge}


def _rolled_flows(flows, fraction):
    first = flows[1]
    remaining = 1 - fraction
    return [DatedFlow(first.date, first.mine_cash_millions * remaining, first.corporate_cash_millions * remaining,
                      first.closure_cash_millions * remaining, first.merian_minority_cash_millions * remaining), *flows[2:]]


def _case(model, gold_shift, byproduct_shift, cost_shift):
    near, shocks = _near_periods(model["statements"], model["capital"], model["sustaining"], model["valuation"],
                                 model["prices"], model["royalty_slope"], gold_shift, byproduct_shift, cost_shift)
    distant, sites = _long_cash(model["statements"], model["reserves"], model["capital"], model["valuation"], model["prices"],
                                model["sustaining"], model["closure"], model["royalty_slope"], gold_shift, byproduct_shift, cost_shift)
    flows = [*near, *distant]
    target_bridge, dividends = _target_bridge(model["statements"], model["capital"], model["horizon"], flows, shocks, model["checks"])
    rolled = _rolled_flows(flows, target_bridge["cash_fraction_2027"])
    return flows, rolled, target_bridge, dividends, sites


def run_nem_nav(as_of: date, inputs_dir: Path, *, guided: Path | None = None) -> dict:
    """Calculate spot and rolled Newmont NAV, reserve depletion and sensitivities."""
    statements = run_nem_statements(as_of, inputs_dir, guided=guided)
    packets = {name: _evidence(inputs_dir, name, as_of) for name in ("mine-life", "nav-capital", "nav-prices", "nav-valuation", "prices", "costs")}
    life, capital, outlook, valuation, prices, costs = packets.values()
    _check_sources(life, capital, outlook, valuation, as_of)
    valuation = {**valuation, "world_bank_2028_prices": outlook["world_bank_2028_prices"]}
    horizon = date.fromisoformat(valuation["target_date"])
    if horizon != as_of.replace(year=as_of.year + 1):
        raise ValueError("Newmont target date must be one year after the dated cutoff.")
    checks = []
    operating = statements["operating_forecast"]
    wacc = _capital_cost(valuation, statements, as_of, checks)
    reserves = _reserve_volumes(life, operating, checks)
    sustaining = _sustaining_allocations(capital, statements, reserves, checks)
    closure = _closure(capital, statements, checks)
    marks = _affiliate_marks(valuation, statements, as_of, checks)
    receivable = _porcupine_receivable(valuation, horizon, checks)
    royalty = costs["sensitivities"]
    slope = _value(royalty, "gold_related_royalty_production_tax_and_profit_sharing_cost_change_usd_per_ounce") / _value(royalty, "gold_price_change_usd_per_ounce")
    cutoff = statements["cutoff"]
    quote = statements["financing_evidence"]["reference_close"]
    spot_bridge = {
        "gross_cash_millions": cutoff["cash_before_operating_reserve_millions"], "net_cash_adjustment_millions": 0,
        "debt_and_finance_leases_millions": cutoff["note_and_finance_lease_debt_millions"],
        "ngm_obligation_millions": cutoff["ngm_payment_modeled_after_cutoff_millions"],
        "shares_millions": statements["periods"][FY27].financing.diluted_shares_millions,
    }
    _reconcile(checks, "Cash less separately shown NGM obligation to paid timing alternative", spot_bridge["gross_cash_millions"]
               - spot_bridge["ngm_obligation_millions"], cutoff["cash_if_ngm_already_paid_millions"])
    _reconcile(checks, "Merian ownership and minority interests", _value(valuation["merian"], "nci_share")
               + operating["mine_register"]["merian"]["ownership"], 1)
    model = {"statements": statements, "capital": capital, "valuation": valuation, "prices": prices, "sustaining": sustaining,
             "reserves": reserves, "closure": closure, "royalty_slope": slope, "horizon": horizon, "checks": checks}
    scenario = valuation["scenarios"]
    gold_step, metal_step, cost_step, discount_step, threshold = (_value(scenario, key) for key in (
        "gold_price_step_usd", "byproduct_price_step", "site_cost_step", "wacc_step", "rating_threshold",
    ))
    rate = wacc["wacc"]
    if not 0 < rate - 2 * discount_step < rate + 2 * discount_step < 1 or not 0 < threshold < 1:
        raise ValueError("Invalid Newmont sensitivity or rating settings.")
    if not 0 <= _rate(valuation, "inflation") < 1 or not 0 < metal_step < 1 or not 0 < cost_step < 1 or gold_step <= 0:
        raise ValueError("Invalid inflation, commodity price or cost shocks for Newmont.")
    results = {}
    base_detail = None
    base_paths = None
    for name, steps in (("bear", (-gold_step, -metal_step, cost_step)), ("base", (0, 0, 0)),
                        ("bull", (gold_step, metal_step, -cost_step))):
        flows, rolled, bridge, dividends, sites = _case(model, *steps)
        spot = _nav(flows, as_of, spot_bridge, marks, receivable, rate)
        target = _nav(rolled, horizon, bridge, marks, receivable, rate)
        total_return = (target["value_per_share"] + dividends) / quote["price_usd"] - 1
        rating = "Buy" if total_return >= threshold else "Sell" if total_return <= -threshold else "Hold"
        results[name] = {"spot": spot, "target": target, "dividends_per_share": dividends, "total_return": total_return,
                         "rating": rating, "gold_shift_usd": steps[0], "coproduct_price_shift": steps[1], "site_cost_shift": steps[2]}
        if name == "base":
            base_paths = flows, rolled, bridge
            base_detail = sites
            _reconcile(checks, "NAV Q4 closure and FCFF to linked forecast", flows[0].consolidated_cash_millions,
                       cutoff["remaining_core_consolidated_fcff_millions"])
            _reconcile(checks, "NAV FY2027 FCFF to linked forecast and excluded contingent spend", flows[1].consolidated_cash_millions,
                       statements["periods"][FY27].cash_flow["core_consolidated_unlevered_free_cash_flow"]
                       + _value(capital["development"], "fy_2027_conditional_red_chris_cash_millions"))
    gold_shifts = tuple(gold_step * step for step in (-2, -1, 0, 1, 2))
    rates = tuple(rate + discount_step * step for step in (-2, -1, 0, 1, 2))
    grid = {key: [[] for _ in rates] for key in ("spot", "target")}
    for gold_shift in gold_shifts:
        if gold_shift == 0:
            flows, rolled, bridge = base_paths
        else:
            flows, rolled, bridge, _, _ = _case(model, gold_shift, 0, 0)
        for index, discount in enumerate(rates):
            grid["spot"][index].append(_nav(flows, as_of, spot_bridge, marks, receivable, discount)["value_per_share"])
            grid["target"][index].append(_nav(rolled, horizon, bridge, marks, receivable, discount)["value_per_share"])
    for key in grid:
        _reconcile(checks, f"Center of {key} WACC and gold grid", grid[key][2][2], results["base"][key]["value_per_share"])
    base_flows = base_paths[0]
    for year, expected in capital["development"]["base_cash_by_year_millions"].items():
        _reconcile(checks, f"{year} modeled site development spending",
                   fsum(site[int(year)]["development_cash_capex_millions"] for site in base_detail.values()), expected)
    for site, detail in base_detail.items():
        _reconcile(checks, f"{site} wind-down trade working capital release",
                   fsum(row["working_capital_release_millions"] for row in detail.values()),
                   _working_net(statements["periods"][FY27].inventory_and_working_capital["closing"])
                   * fsum(line.cas_millions for line in operating["periods"][FY27]["mines"][site]["economics"].values())
                   / operating["periods"][FY27]["cas_millions"])
    peers = _peers(inputs_dir, as_of, statements, checks)
    fourmile = valuation["fourmile"]
    project_share = _value(fourmile, "project_ownership_if_contributed")
    _reconcile(checks, "Fourmile ownership to Nevada Gold Mines basis", project_share, operating["mine_register"]["ngm"]["ownership"])
    excluded = {
        "fourmile_standalone_preliminary_npv_with_inferred_millions": project_share * _num(fourmile, "technical_npv_100pct_with_inferred_millions"),
        "fourmile_standalone_preliminary_npv_without_inferred_millions": project_share * _num(fourmile, "technical_npv_100pct_without_inferred_millions"),
        "nueva_union_and_norte_abierto_book_millions": _value(valuation["affiliates"]["other_investments"], "nueva_union_carrying_millions")
        + _value(valuation["affiliates"]["other_investments"], "norte_abierto_carrying_millions"),
    }
    return {
        "as_of": as_of, "mode": statements["mode"], "horizon": horizon, "reference_close": quote, "cost_of_capital": wacc,
        "scenarios": results, "operating_mine_plans": reserves, "base_mine_cash": base_detail,
        "long_term_benchmarks": {"year": 2028, "world_bank_prices": outlook["world_bank_2028_prices"], "units": outlook["units"],
                                 "nominal_inflation": _rate(valuation, "inflation")},
        "base_dated_cash_flows": [{"date": row.date, "consolidated_fcff_millions": row.consolidated_cash_millions,
                                  "operating_cash_before_separately_shown_costs_millions": row.mine_cash_millions,
                                  "corporate_cash_millions": row.corporate_cash_millions, "closure_cash_millions": row.closure_cash_millions,
                                  "merian_minority_cash_millions": row.merian_minority_cash_millions} for row in base_flows],
        "closure_payments_from_september": closure, "investment_marks_millions": marks, "excluded_reference_values_millions": excluded,
        "porcupine_receivable_payments_millions": {day.isoformat(): amount for day, amount in receivable.items()},
        "comparables": peers,
        "sensitivity": {"gold_shift_usd": gold_shifts, "wacc": rates, **grid}, "checks": checks,
        "source_register": {**statements["source_register"], **{name: packet["sources"] for name, packet in packets.items()},
                            "comparables": peers["sources"]},
        "limitations": (
            "Mine lives use dated technical and operator disclosures where available. Unpublished annual production uses finite reserve-based pacing. The NGM tail follows the operator's contained-gold schedule as a shape proxy. Modelled reserves and operating life exclude resource conversion and speculative extensions.",
            "2028 market prices use the World Bank deck. Metals and operating cost inputs escalate at 2% nominally thereafter, with the silver stream cash price subject to its contractual cap. Flat real prices, operating cost behavior and per-site tax shields are modeling assumptions.",
            "Post-2027 tax uses the linked 33% consolidated rate as a site-level proxy and gives no site loss carryforward credit. The model estimates depreciation from site volumes within allocated net PP&E plus reinvestment. It uses site sustaining capital adjusted for activity and the dated project spending schedule.",
            "Trade working capital is released through modeled wind-down. From 2028, payable output proxies sales volumes. The valuation credits no separate residual stockpile book value and uses finite cash flows.",
            "Closure cash is pooled. After explicit 2026–28 payments, the undiscounted FY2025 residual is dated to reproduce the September provision at the historical accretion proxy. The calibrated effective date is an analytical estimate.",
            "The Merian minority adjustment values 25% of modeled mine cash before pooled closure. Retaining all pooled closure cash in consolidated NAV is conservative where Merian's site amount is unavailable.",
            "Operating value excludes Red Chris block cave and separate Fourmile cash flows. NuevaUnión, Norte Abierto and conditional divestiture receipts receive no base value. The four scheduled Porcupine installments are valued at the model discount rate as a proxy for the note. Pueblo Viejo uses a dated operator reserve-case NPV and quoted holdings remain at cutoff marks in the target.",
            "The spot bridge credits estimated unrestricted cash and deducts the NGM payment once. The target rolls the linked cash and debt through November 12, 2027, adjusting for modeled dividends, affiliate and minority cash, stock awards and the removed contingent Red Chris spend.",
            "Scenario prices and costs apply to cash flows after the cutoff. Short-term scenario increments use a fixed volume and working-capital approximation. The reported P/E cross-check is independent of the model recommendation.",
            *life.get("notes", ()), *capital.get("limitations", ()),
        ),
    }

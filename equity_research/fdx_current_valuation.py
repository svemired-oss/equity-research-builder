"""Dated FCFF valuation for FedEx on its continuing-operations reporting basis."""

from dataclasses import dataclass
from datetime import date
from math import fsum, isfinite
from pathlib import Path

from .fdx_current import _dated_source, _item, _num, _reconcile, _sources, run_current_fdx
from .fdx_current_comps import build_fdx_comps
from .fdx_forward_statements import _dated_inputs, run_fdx_statements
from .gd_inputs import _array, _day, _text
from .gd_research import RatingPolicy


@dataclass(frozen=True)
class CostOfCapital:
    risk_free_rate: float
    equity_risk_premium: float
    unlevered_beta: float
    levered_beta: float
    market_equity_millions: float
    carrying_debt_millions: float
    equity_weight: float
    debt_weight: float
    tax_rate: float
    cost_of_equity: float
    pretax_cost_of_debt: float
    after_tax_cost_of_debt: float
    wacc: float


@dataclass(frozen=True)
class TerminalCashFlow:
    growth: float
    return_on_new_invested_capital: float
    nopat_millions: float
    reinvestment_rate: float
    reinvestment_millions: float
    pension_funding_millions: float
    unlevered_cash_flow_millions: float


@dataclass(frozen=True)
class EquityBridge:
    book_cash_before_reserve_millions: float
    reserve_millions: float
    post_cutoff_stock_compensation_millions: float
    excess_cash_millions: float
    debt_including_finance_leases_millions: float
    fdxf_millions: float
    inpost_millions: float
    debt_securities_millions: float
    other_equity_securities_millions: float
    nonoperating_assets_millions: float
    enterprise_to_equity_millions: float
    basic_shares_millions: float
    diluted_shares_millions: float


@dataclass(frozen=True)
class CashFlow:
    date: date
    amount_millions: float


@dataclass(frozen=True)
class DCF:
    date: date
    projected_cash_flows: tuple[CashFlow, ...]
    present_values: tuple[CashFlow, ...]
    terminal: TerminalCashFlow
    terminal_value_millions: float
    terminal_present_value_millions: float
    enterprise_value_millions: float
    terminal_share_of_enterprise_value: float
    bridge: EquityBridge
    equity_value_millions: float
    value_per_share: float


@dataclass(frozen=True)
class Sensitivity:
    wacc: tuple[float, ...]
    growth: tuple[float, ...]
    value_per_share: tuple[tuple[float, ...], ...]


@dataclass(frozen=True)
class HorizonCashRoll:
    elapsed_calendar_days: int
    days_in_year: int
    opening_cash_millions: float
    operating_cash_after_capex_millions: float
    new_notes_millions: float
    new_finance_leases_millions: float
    scheduled_note_principal_paid_millions: float
    modeled_finance_lease_principal_paid_millions: float
    dividends_paid_millions: float
    book_cash_millions: float
    debt_millions: float
    post_cutoff_stock_compensation_millions: float


@dataclass(frozen=True)
class FDXValuationCase:
    spot: DCF
    target: DCF
    spot_sensitivity: Sensitivity
    target_sensitivity: Sensitivity
    horizon_cash_roll: HorizonCashRoll
    modeled_dividends_per_share: float
    price_return: float
    total_return: float
    recommendation: str


def _load_discount(document, as_of):
    if (document.get("ticker"), document.get("currency")) != ("FDX", "USD"):
        raise ValueError("The discount inputs must use FDX and USD.")
    sources = _sources(document, as_of)
    values = {}
    for key in ("risk_free_rate", "equity_risk_premium", "unlevered_beta", "pretax_cost_of_debt", "tax_rate",
                "terminal_growth", "terminal_return_on_new_invested_capital"):
        row = _item(document, key, "discount inputs")
        observation = _day(_item(row, "observed_on", key), f"{key} observation")
        _dated_source(_item(row, "source", key), sources, key, observed_on=observation)
        _text(_item(row, "note", key), f"{key} basis")
        values[key] = _num(row, "value", key, minimum=0)
    if values["unlevered_beta"] <= 0:
        raise ValueError("FedEx's unlevered beta must be positive.")
    for key in values.keys() - {"unlevered_beta"}:
        if not 0 < values[key] < 1:
            raise ValueError(f"{key} must be a positive decimal rate below 100%.")
    steps = _item(document, "sensitivity_steps")
    return values, _num(steps, "wacc", "sensitivity", minimum=0.000001), _num(steps, "terminal_growth", "sensitivity", minimum=0.000001)


def _cost_of_capital(values, capital):
    equity = capital["fdx_reference_close_usd"] * capital["fdx_shares"] / 1e6
    debt = capital["reported_debt_including_finance_leases_millions"]
    tax = values["tax_rate"]
    beta = values["unlevered_beta"] * (1 + (1 - tax) * debt / equity)
    cost_equity = values["risk_free_rate"] + beta * values["equity_risk_premium"]
    cost_debt = values["pretax_cost_of_debt"] * (1 - tax)
    weight_equity, weight_debt = equity / (equity + debt), debt / (equity + debt)
    wacc = weight_equity * cost_equity + weight_debt * cost_debt
    return CostOfCapital(values["risk_free_rate"], values["equity_risk_premium"], values["unlevered_beta"], beta,
                         equity, debt, weight_equity, weight_debt, tax, cost_equity, values["pretax_cost_of_debt"], cost_debt, wacc)


def _check_debt_yield(document, as_of, risk_free, checks):
    sources = _sources(document, as_of)
    row = _item(document, "debt_cross_check")
    priced_on = _day(_item(row, "priced_on"), "note pricing date")
    _dated_source(_item(row, "source"), sources, "note terms", observed_on=priced_on)
    _dated_source(_item(row, "rating_source"), sources, "credit rating")
    spread = _num(row, "issue_spread", minimum=0)
    reoffer_yield = _num(row, "reoffer_yield", minimum=0)
    _reconcile(checks, "September note issue Treasury yield plus spread", _num(row, "benchmark_treasury_yield") + spread, reoffer_yield)
    return {"priced_on": priced_on, "issue_spread": spread, "reoffer_yield": reoffer_yield,
            "current_treasury_plus_original_issue_spread": risk_free + spread}


def _terminal(final_nopat, pension, growth, roic):
    if not 0 <= growth < roic or not isfinite(final_nopat) or final_nopat <= 0:
        raise ValueError("Terminal growth must be nonnegative, below marginal ROIC, and supported by positive NOPAT.")
    nopat = final_nopat * (1 + growth)
    reinvestment = nopat * (growth / roic)
    funding = pension * (1 + growth)
    cash = nopat - reinvestment - funding
    if not isfinite(cash) or cash <= 0:
        raise ValueError("Terminal assumptions must leave positive unlevered free cash flow.")
    return TerminalCashFlow(growth, roic, nopat, growth / roic, reinvestment, funding, cash)


def _discount(cash_flows, as_of, bridge, wacc, terminal):
    if not terminal.growth < wacc < 1:
        raise ValueError("WACC must exceed terminal growth and be below 100%.")
    if not cash_flows or as_of >= cash_flows[0].date or any(
        current.date <= prior.date for prior, current in zip(cash_flows, cash_flows[1:])
    ):
        raise ValueError("DCF cash flow dates must be ascending and strictly after the cutoff.")
    factors = tuple((1 + wacc) ** ((flow.date - as_of).days / 365) for flow in cash_flows)
    pv = tuple(CashFlow(flow.date, flow.amount_millions / factor) for flow, factor in zip(cash_flows, factors, strict=True))
    terminal_value = terminal.unlevered_cash_flow_millions / (wacc - terminal.growth)
    terminal_pv = terminal_value / factors[-1]
    enterprise = fsum((*(flow.amount_millions for flow in pv), terminal_pv))
    equity = enterprise + bridge.enterprise_to_equity_millions
    if enterprise <= 0 or not isfinite(equity) or bridge.diluted_shares_millions <= 0:
        raise ValueError("Invalid enterprise or equity bridge for the DCF.")
    return DCF(as_of, cash_flows, pv, terminal, terminal_value, terminal_pv, enterprise, terminal_pv / enterprise,
               bridge, equity, equity / bridge.diluted_shares_millions)


def _grid(flows, as_of, bridge, capital, values, terminal_nopat, pension, wacc_step, growth_step):
    rates = tuple(capital.wacc + offset * wacc_step for offset in range(-3, 4))
    growths = tuple(values["terminal_growth"] + offset * growth_step for offset in range(-3, 4))
    terminals = tuple(_terminal(terminal_nopat, pension, growth, values["terminal_return_on_new_invested_capital"]) for growth in growths)
    return Sensitivity(rates, growths, tuple(tuple(_discount(flows, as_of, bridge, rate, terminal).value_per_share
                                                  for terminal in terminals) for rate in rates))


def _note_principal_through_horizon(financing, horizon):
    basis = financing["note_principal_basis"]
    fx = basis["usd_per_eur"]
    year = horizon.year
    certificate = basis["aircraft_certificates"]
    regular = certificate["scheduled_principal_per_distribution_millions"] * sum(
        date.fromisoformat(f"{year}-{day}") < horizon for day in certificate["distribution_month_days"]
    )
    dated = (row["principal_millions"] * (fx if row["currency"] == "EUR" else 1) for row in basis["senior_note_maturities"]
             if date(year, 1, 1) <= date.fromisoformat(row["matures_on"]) < horizon)
    return fsum((regular, *dated))


def _horizon_cash(statements, financing, horizon, checks):
    year = statements["years"][0]
    start = date(year.period_end.year, 1, 1)
    full_days = (year.period_end - start).days + 1
    elapsed = (horizon - start).days
    if not 0 < elapsed < full_days or horizon < date(start.year, 10, 2) or horizon > date(start.year, 12, 1):
        raise ValueError("The 12-month horizon must follow the modeled October dividend and precede the modeled December record date.")
    fraction = elapsed / full_days
    schedule = year.debt_schedule
    paid_notes = _note_principal_through_horizon(financing, horizon)
    if not 0 <= paid_notes <= schedule.note_principal_paid + 1e-6:
        raise ValueError("Maturities through the horizon disagree with the annual debt schedule.")
    funding_to_horizon = schedule.new_notes * fraction
    leases_added_to_horizon = schedule.new_finance_leases * fraction
    lease_principal_to_horizon = schedule.finance_lease_principal_paid * fraction
    dividends_paid = -year.cash_flow["dividends_paid"]
    q4_cash = statements["q4"].balance_sheet.cash_before_reserve
    operating_cash = year.cash_flow["operating_cash_after_capex"]
    cash = q4_cash + operating_cash * fraction + funding_to_horizon - paid_notes - lease_principal_to_horizon - dividends_paid
    debt = (schedule.opening_notes + schedule.opening_finance_leases - paid_notes - lease_principal_to_horizon
            + funding_to_horizon + leases_added_to_horizon)
    _reconcile(checks, "Horizon and remaining 2027 cash to annual balance", cash + operating_cash * (1 - fraction)
               + (schedule.new_notes - funding_to_horizon) - (schedule.note_principal_paid - paid_notes)
               - (schedule.finance_lease_principal_paid - lease_principal_to_horizon), year.balance_sheet.cash_before_reserve)
    _reconcile(checks, "Horizon and remaining 2027 debt to annual balance", debt + (schedule.new_notes + schedule.new_finance_leases) * (1 - fraction)
               - (schedule.note_principal_paid - paid_notes) - (schedule.finance_lease_principal_paid - lease_principal_to_horizon),
               year.balance_sheet.notes + year.balance_sheet.finance_leases)
    cash_timing = statements["q4_operating_cash_bridge"]
    q4_sbc = statements["q4"].cash_flow["stock_compensation"] * cash_timing.after_cutoff.calendar_days / cash_timing.fourth_quarter.calendar_days
    sbc = q4_sbc + year.cash_flow["stock_compensation"] * fraction
    return HorizonCashRoll(elapsed, full_days, q4_cash, operating_cash * fraction, funding_to_horizon,
                           leases_added_to_horizon, paid_notes, lease_principal_to_horizon, dividends_paid, cash, debt, sbc)


def _equity_bridge(cash, reserve, sbc, debt, investments, shares):
    excess_cash = cash - reserve - sbc
    nonoperating = fsum(investments.values())
    return EquityBridge(
        book_cash_before_reserve_millions=cash,
        reserve_millions=reserve,
        post_cutoff_stock_compensation_millions=sbc,
        excess_cash_millions=excess_cash,
        debt_including_finance_leases_millions=debt,
        fdxf_millions=investments["fdxf"],
        inpost_millions=investments["inpost"],
        debt_securities_millions=investments["debt_securities"],
        other_equity_securities_millions=investments["other_equity_securities"],
        nonoperating_assets_millions=nonoperating,
        enterprise_to_equity_millions=excess_cash + nonoperating - debt,
        basic_shares_millions=shares[0],
        diluted_shares_millions=shares[1],
    )


def _case(statements, financing, as_of, horizon, reserve, investments, cap, values, steps, quote, policy, checks):
    cutoff, years = statements["cutoff"], statements["years"]
    first, last = years[0], years[-1]
    shares = first.income_statement["basic_shares_millions"], first.income_statement["diluted_shares_millions"]
    spot_bridge = _equity_bridge(cutoff["cash_before_operating_reserve_millions"], reserve, 0,
                                 cutoff["estimated_financing_debt_millions"], investments, shares)
    cash_roll = _horizon_cash(statements, financing, horizon, checks)
    elapsed = cash_roll.elapsed_calendar_days / cash_roll.days_in_year
    target_bridge = _equity_bridge(cash_roll.book_cash_millions, reserve, cash_roll.post_cutoff_stock_compensation_millions,
                                   cash_roll.debt_millions, investments, shares)
    annual_flows = tuple(CashFlow(period.period_end, period.cash_flow["unlevered_free_cash_flow"]) for period in years)
    spot_flows = (CashFlow(cutoff["period_end"], cutoff["remaining_unlevered_free_cash_flow_millions"]), *annual_flows)
    target_flows = (CashFlow(first.period_end, annual_flows[0].amount_millions * (1 - elapsed)), *annual_flows[1:])
    final_nopat = last.cash_flow["nopat"]
    pension = -last.cash_flow["voluntary_pension_contribution"]
    terminal = _terminal(final_nopat, pension, values["terminal_growth"], values["terminal_return_on_new_invested_capital"])
    spot = _discount(spot_flows, as_of, spot_bridge, cap.wacc, terminal)
    target = _discount(target_flows, horizon, target_bridge, cap.wacc, terminal)
    spot_grid = _grid(spot_flows, as_of, spot_bridge, cap, values, final_nopat, pension, *steps)
    target_grid = _grid(target_flows, horizon, target_bridge, cap, values, final_nopat, pension, *steps)
    _reconcile(checks, "Spot DCF sensitivity center", spot_grid.value_per_share[3][3], spot.value_per_share)
    _reconcile(checks, "12-month DCF sensitivity center", target_grid.value_per_share[3][3], target.value_per_share)
    _reconcile(checks, "2027 FCFF before and after the horizon", annual_flows[0].amount_millions * elapsed + target_flows[0].amount_millions,
               annual_flows[0].amount_millions)
    dividends = cash_roll.dividends_paid_millions / shares[0]
    price_return = target.value_per_share / quote - 1
    total_return = (target.value_per_share + dividends) / quote - 1
    return FDXValuationCase(spot, target, spot_grid, target_grid, cash_roll, dividends, price_return, total_return, policy.rate(total_return))


def _load_asset_bridge(document, capital, as_of, checks):
    if (document.get("ticker"), document.get("currency"), document.get("cutoff_basis")) != ("FDX", "USD", "pre_open"):
        raise ValueError("Use pre-open FDX asset bridge inputs in USD.")
    if _day(_item(document, "balance_sheet_date"), "asset balance sheet date") != capital["balance_sheet_date"]:
        raise ValueError("The nonoperating assets must reconcile to the latest reported balance sheet.")
    sources = _sources(document, as_of)
    reserve, freight, inpost, other = (_item(document, name) for name in ("operating_cash_reserve", "fdxf", "inpost", "other_assets"))
    for name, group in (("reserve", reserve), ("FDXF", freight), ("InPost", inpost), ("other assets", other)):
        ids = _array(_item(group, "source_ids", name), f"{name} sources")
        if not ids:
            raise ValueError(f"Missing citations for {name}.")
        for source in ids:
            _dated_source(source, sources, name)
    if _day(freight["close_date"], "Freight quote date") != capital["quote_date"] or (
        _day(freight["share_observed_on"], "Freight share observation") != capital["fdxf_share_observation_date"]
    ):
        raise ValueError("Freight share and price dates disagree across the equity bridge inputs.")
    for row, field, control in (
        (freight, "retained_shares", "fdxf_retained_shares"), (freight, "close_usd", "fdxf_reference_close_usd"),
        (freight, "carrying_value_millions", "fdxf_carrying_value_millions"), (inpost, "carrying_value_millions", "inpost_carrying_value_millions"),
        (other, "debt_securities_millions", "other_debt_securities_millions"),
        (other, "other_equity_securities_millions", "other_equity_securities_millions"),
    ):
        _reconcile(checks, f"Separate {control} cross-source reconciliation", _num(row, field), capital[control])
    _reconcile(checks, "Freight price times retained shares", _num(freight, "retained_shares") * _num(freight, "close_usd") / 1e6,
               _num(freight, "base_value_millions"))
    _reconcile(checks, "InPost transaction cost proxy", _num(inpost, "base_value_millions"), _num(inpost, "carrying_value_millions"))
    if freight["monetization_date"] is not None or freight["monetization_proceeds_millions"] is not None:
        raise ValueError("Update the cash, asset and debt roll-forward together before applying Freight monetization.")
    for field, control in (("september_2026_reported_cash_millions", "reported_cash_millions"),
                           ("september_2026_tariff_customer_cash_millions", "tariff_cash_for_customers_millions"),
                           ("september_2026_cash_net_of_customer_refunds_millions", "cash_after_customer_obligation_before_operating_reserve_millions")):
        _reconcile(checks, f"Reserve cash calibration {field}", _num(reserve, field, minimum=0), capital[control])
    for label, row, suffix, maximum in (("reserve", reserve, "share_of_revenue", 1), ("Freight", freight, "value_millions", float("inf")),
                                       ("InPost", inpost, "value_millions", float("inf"))):
        low, base, high = (_num(row, f"{case}_{suffix}", label, minimum=0) for case in ("low", "base", "high"))
        if not low <= base <= high < maximum:
            raise ValueError(f"Unordered valuation assumptions for {label}.")
    return reserve, freight, inpost, other


def _asset_sensitivity(bridge, reserve, freight, inpost, reference_revenue):
    base = bridge.value_per_share
    cash_bridge = bridge.bridge
    # Each sensitivity changes a single input with the operating DCF held fixed.
    return {
        "base_price_per_share": base,
        "operating_reserve_low_and_high": tuple(base - (_num(reserve, f"{key}_share_of_revenue") * reference_revenue
                                                        - cash_bridge.reserve_millions) / cash_bridge.diluted_shares_millions
                                                for key in ("low", "high")),
        "fdxf_value_low_and_high": tuple(base + (_num(freight, f"{key}_value_millions") - cash_bridge.fdxf_millions)
                                         / cash_bridge.diluted_shares_millions for key in ("low", "high")),
        "inpost_value_low_and_high": tuple(base + (_num(inpost, f"{key}_value_millions") - cash_bridge.inpost_millions)
                                           / cash_bridge.diluted_shares_millions for key in ("low", "high")),
    }


def _ltm_metrics(actuals, operations, current):
    prior = operations["q4_bridge"]["cy25_q4_adjusted_earnings"]
    latest = current["guidance_bridge"]
    earnings = _num(prior, "net_income_millions", minimum=0.01)
    q4_shares = earnings / _num(prior, "diluted_eps", minimum=0.01)
    reported = actuals["income_statements"]["2026_ytd"]
    q4_days = (date(2026, 1, 1) - date(2025, 10, 1)).days
    ytd_days = (date.fromisoformat(reported["period_end"]) - date.fromisoformat(reported["period_start"])).days + 1
    shares = (q4_shares * q4_days + _num(reported, "diluted_weighted_average_shares", minimum=1) * ytd_days) / (q4_days + ytd_days)
    return {
        "period_end": date.fromisoformat(reported["period_end"]),
        "ebit_millions": _num(prior, "operating_income_millions") + latest["cy26_adjusted_operating_income_ytd_sum_millions"],
        "adjusted_net_income_millions": earnings + latest["cy26_adjusted_net_income_ytd_sum_millions"],
        "estimated_weighted_diluted_shares_millions": shares,
        "eps": (earnings + latest["cy26_adjusted_net_income_ytd_sum_millions"]) / shares,
        "basis": "FedEx issuer-adjusted continuing EBIT and net income for Q4 2025 plus the first nine months of 2026. LTM EPS uses day-weighted diluted shares, with Q4 inferred from adjusted income divided by reported adjusted EPS.",
    }


def run_fdx_valuation(as_of: date, inputs_dir: Path, *, guided: Path | None = None) -> dict:
    """Value the same dated FedEx operating cases, with independent trading comparisons."""
    current = run_current_fdx(as_of, inputs_dir, guided=guided)
    capital = current["capital_inputs"]
    actuals = _dated_inputs(inputs_dir, "actuals", as_of)
    operations = _dated_inputs(inputs_dir, "operations", as_of)
    financing = _dated_inputs(inputs_dir, "financing", as_of)
    discount = _dated_inputs(inputs_dir, "discount", as_of)
    assets = _dated_inputs(inputs_dir, "valuation-bridge", as_of)
    comps = _dated_inputs(inputs_dir, "comps", as_of)
    research = _dated_inputs(inputs_dir, "research", as_of)
    values, wacc_step, growth_step = _load_discount(discount, as_of)
    cap = _cost_of_capital(values, capital)
    checks = []
    debt_yield_check = _check_debt_yield(discount, as_of, cap.risk_free_rate, checks)
    reserve, freight, inpost, other = _load_asset_bridge(assets, capital, as_of, checks)
    reference_revenue = current["calendar_year"]["forecast_revenue_millions"]
    reserve_millions = _num(reserve, "base_share_of_revenue") * reference_revenue
    investments = {"fdxf": _num(freight, "base_value_millions"), "inpost": _num(inpost, "base_value_millions"),
                   "debt_securities": _num(other, "debt_securities_millions"), "other_equity_securities": _num(other, "other_equity_securities_millions")}
    raw_policy = _item(research, "rating_policy")
    policy = RatingPolicy(_num(raw_policy, "buy_at_or_above"), _num(raw_policy, "sell_at_or_below"), _text(raw_policy["rationale"], "rating policy"))
    if not -1 <= policy.sell_at_or_below < 0 < policy.buy_at_or_above:
        raise ValueError("Supply ordered negative Sell and positive Buy thresholds.")
    horizon = as_of.replace(year=as_of.year + 1)
    quote = capital["fdx_reference_close_usd"]
    scenarios = {}
    statement_checks = {}
    for name in ("bear", "base", "bull"):
        statements = run_fdx_statements(as_of, inputs_dir, case=name, guided=guided)
        statement_checks[name] = len(statements["checks"])
        if name == "base":
            statement_sources = statements["source_register"]
            statement_limitations = statements["limitations"]
        for period in statements["years"]:
            ebit = period.income_statement["operating_income"]
            _reconcile(checks, f"{name} {period.period_end.year} DCF tax basis", period.cash_flow["nopat"], ebit * (1 - cap.tax_rate))
        scenarios[name] = _case(statements, financing, as_of, horizon, reserve_millions, investments, cap, values,
                                (wacc_step, growth_step), quote, policy, checks)
    base = scenarios["base"]
    ltm = _ltm_metrics(actuals, operations, current)
    bridge = base.spot.bridge
    comparable_values = build_fdx_comps(comps, subject_ebit_millions=ltm["ebit_millions"], subject_eps=ltm["eps"],
                                        subject_shares_millions=bridge.basic_shares_millions,
                                        subject_valuation_shares_millions=bridge.diluted_shares_millions,
                                        subject_debt_millions=bridge.debt_including_finance_leases_millions,
                                        subject_cash_millions=bridge.excess_cash_millions,
                                        subject_nonoperating_assets_millions=bridge.nonoperating_assets_millions,
                                        subject_basis=ltm["basis"], expected_close_date=capital["quote_date"], expected_close_usd=quote)
    return {
        "as_of": as_of, "horizon": horizon, "quote_date": capital["quote_date"], "reference_close_usd": quote,
        "mode": current["mode"], "override_log": current["override_log"],
        "cost_of_capital": cap, "debt_yield_cross_check": debt_yield_check, "recommendation": base.recommendation,
        "rating_policy": policy, "scenarios": scenarios, "statement_checks_by_case": statement_checks,
        "independent_asset_sensitivities": _asset_sensitivity(base.spot, reserve, freight, inpost, reference_revenue),
        "comps_subject_ltm": ltm, "comparables": comparable_values, "checks": tuple(checks),
        "source_register": {**statement_sources, "discount": discount["sources"],
                            "valuation_bridge": assets["sources"], "comparables": comps["sources"]},
        "forecast_limitations": statement_limitations,
        "method_and_limitations": (
            "FCFF uses end-of-period ACT/365 discounting and a November 12 pre-open cutoff. Base DCF determines the rating.",
            "The 12-month roll assumes uniform in-year operating cash and finance lease timing and pays note maturities on scheduled dates. It assumes four quarterly dividends at the modeled rate paid by the horizon, with the following dividend entitlement after the horizon.",
            "Use a fixed dollar operating reserve based on forecast calendar 2026 revenue. Cash received for customer tariff refunds and the corresponding obligation are outside operating working capital and excess cash.",
            "Use the same gross Freight market mark and InPost recent transaction cost proxy at spot and target. No sale or distribution timing, incremental monetization tax, or private fair value is assumed. Asset sensitivities are separate from the operating cases.",
            "Stock compensation remains an economic cost in FCFF. The target cash bridge removes the post-cutoff accounting addbacks because the forecast does not model incremental dilution. The current Q3 diluted share increment is a proxy for outstanding award dilution.",
            "Terminal FCFF grows the last forecast NOPAT, reinvests at growth divided by assumed marginal ROIC, and continues the voluntary pension funding assumption. No separate pension value is added or deducted.",
            "Peer values are spot comparisons. UPS is the closest comparison. Other business mixes and issuer-specific accounting adjustments limit comparability.",
            *assets["classification_notes"],
        ),
    }

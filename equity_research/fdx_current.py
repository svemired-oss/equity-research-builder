"""FedEx calendar-year operating bridge on the post-separation reporting basis."""

from calendar import monthrange
from dataclasses import dataclass
from datetime import date, timedelta
import json
from math import fsum, isfinite
from pathlib import Path
from urllib.parse import urlsplit

from .gd_inputs import _array, _day, _fields, _number, _object, _text


DOMESTIC = ("us_priority", "us_deferred", "us_ground", "us_express_freight")
INTERNATIONAL = ("international_priority_package", "international_economy_package", "international_domestic_package",
                 "international_priority_freight", "international_economy_freight")
SERVICES = (*DOMESTIC, *INTERNATIONAL)
SEGMENTS = ("express_us_domestic", "express_international", "corporate_other_and_eliminations")
Q3_END = date(2026, 9, 30)
Q4_END = date(2026, 12, 31)
CY_START = date(2026, 1, 1)
TRANSITION_START = date(2026, 6, 1)


@dataclass(frozen=True)
class ServiceProjection:
    volume_growth: float
    yield_growth: float
    daily_volume_thousands: float
    yield_usd: float
    revenue_millions: float


@dataclass(frozen=True)
class Override:
    target: str
    before: float
    value: float
    reason: str


@dataclass(frozen=True)
class Check:
    label: str
    difference: float
    tolerance: float


def _item(record, key, label="input"):
    record = _object(record, label)
    if key not in record:
        raise ValueError(f"Missing {label}.{key}.")
    return record[key]


def _num(record, key, label="input", *, minimum=None):
    value = _number(_item(record, key, label), f"{label}.{key}")
    if minimum is not None and value < minimum:
        raise ValueError(f"{label}.{key} must be at least {minimum:g}.")
    return value


def _load(path: Path):
    def unique_fields(pairs):
        record = {}
        for key, value in pairs:
            if key in record:
                raise ValueError(f"{path.name} repeats the key {key!r}.")
            record[key] = value
        return record

    return _object(json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=unique_fields), path.name)


def _sources(document, as_of, *, date_field="available_on"):
    sources = _object(_item(document, "sources"), "sources")
    dates = {}
    for name, row in sources.items():
        url = urlsplit(_text(_item(row, "url", name), f"{name}.url"))
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ValueError(f"{name} must cite a public HTTPS source.")
        available = _day(_item(row, date_field, name), f"{name}.{date_field}")
        if available > as_of:
            raise ValueError(f"{name} was published after the {as_of} cutoff.")
        dates[name] = available
    return dates


def _dated_source(source_id, source_dates, label, *, observed_on=None):
    source_id = _text(source_id, f"{label} source")
    if source_id not in source_dates:
        raise ValueError(f"Unknown source {source_id!r} for {label}.")
    if observed_on and source_dates[source_id] < observed_on:
        raise ValueError(f"The source for {label} predates its observation.")


def _period(record, key, dates, bounds, *, source_field="source"):
    row = _item(record, key, "periods")
    start = _day(_item(row, "period_start", key), f"{key}.period_start")
    end = _day(_item(row, "period_end", key), f"{key}.period_end")
    if (start, end) != bounds:
        raise ValueError(f"Unexpected period for {key}: {start} through {end}. Expected {bounds[0]} through {bounds[1]}.")
    _dated_source(_item(row, source_field, key), dates, key, observed_on=end)
    return row


def _reconcile(checks, label, computed, reported, tolerance=0.000001):
    difference = computed - reported
    if not isfinite(difference) or abs(difference) > tolerance:
        raise ValueError(f"{label} does not reconcile. Calculated {computed:g}, reported {reported:g}.")
    checks.append(Check(label, difference, tolerance))


def _operating_period(row, key, checks):
    services = _object(_item(row, "services", key), f"{key}.services")
    other = _object(_item(row, "other_revenue_millions", key), f"{key}.other_revenue_millions")
    segments = _object(_item(row, "segment_revenue_millions", key), f"{key}.segment_revenue_millions")
    if set(services) != set(SERVICES) or set(other) != set(SEGMENTS) or set(segments) != set(SEGMENTS):
        raise ValueError(f"{key} service and segment rosters must use the current FedEx reporting basis.")
    days = _num(row, "operating_days", key, minimum=1)
    span = (_day(row["period_end"], key) - _day(row["period_start"], key)).days + 1
    if not days.is_integer() or days > span:
        raise ValueError(f"{key}.operating_days must be a whole count within the period.")
    amounts = {}
    for service, values in services.items():
        label = f"{key}.{service}"
        volume = _num(values, "daily_volume_thousands", label, minimum=0.000001)
        yield_usd = _num(values, "yield_usd", label, minimum=0.000001)
        amount = _num(values, "revenue_millions", label, minimum=0.000001)
        # ADV and yield are displayed as rounded KPIs. Preserve the independently reported sales.
        rounding_limit = (volume * 0.005 + yield_usd * 0.5 + 0.0025) * days / 1000 + 0.5
        _reconcile(checks, f"{label} rounded KPIs to booked revenue", volume * yield_usd * days / 1000,
                   amount, tolerance=rounding_limit)
        amounts[service] = amount
    for segment, service_group in zip(SEGMENTS, (DOMESTIC, INTERNATIONAL, ()), strict=True):
        _reconcile(checks, f"{key}.{segment} service revenue", fsum(amounts[s] for s in service_group) + _num(other, segment, key),
                   _num(segments, segment, key))
    _reconcile(checks, f"{key} consolidated revenue", fsum(_num(segments, segment, key) for segment in SEGMENTS),
               _num(row, "revenue_millions", key))


def _forecast_service(prior, days, prior_days, volume_growth, yield_growth):
    volume = prior["daily_volume_thousands"] * (1 + volume_growth)
    yield_usd = prior["yield_usd"] * (1 + yield_growth)
    # Forecast from the booked revenue base so small rounding differences in historical KPIs do not compound.
    revenue = prior["revenue_millions"] * (1 + volume_growth) * (1 + yield_growth) * days / prior_days
    if not all(isfinite(amount) for amount in (volume, yield_usd, revenue)):
        raise ValueError("FedEx service growth must produce finite driver and revenue estimates.")
    return ServiceProjection(volume_growth, yield_growth, volume, yield_usd, revenue)


def _guided_rates(document, rates, corporate):
    values = {**rates, "corporate_other_and_eliminations.revenue_millions": corporate}
    overrides = []
    if document is None:
        return values, overrides
    fields = _fields(document, "guided assumptions", {"overrides"})
    seen = set()
    for item in _array(fields["overrides"], "overrides"):
        item = _fields(item, "override", {"target", "value", "reason"})
        target = _text(item["target"], "override target")
        if target not in values or target in seen:
            raise ValueError(f"Unsupported or repeated FedEx override {target!r}.")
        value = _number(item["value"], target)
        if target.endswith("growth") and value < -1:
            raise ValueError(f"{target} cannot be below -100%.")
        overrides.append(Override(target, values[target], value, _text(item["reason"], "override reason")))
        values[target] = value
        seen.add(target)
    return values, overrides


def _project_revenue(prior, recent, recent_prior, days, annual_anchor, quarter_anchor, guided):
    base = prior["services"]
    now = recent["services"]
    before = recent_prior["services"]
    rates = {
        f"{service}.{rate}": now[service][measure] / before[service][measure] - 1
        for service in SERVICES
        for rate, measure in (("volume_growth", "daily_volume_thousands"), ("yield_growth", "yield_usd"))
    }

    def project(current_rates):
        return {service: _forecast_service(base[service], days, prior["operating_days"],
                current_rates[f"{service}.volume_growth"], current_rates[f"{service}.yield_growth"]) for service in SERVICES}

    unedited = project(rates)
    other = {}
    for segment in SEGMENTS:
        prior_q4 = prior["other_revenue_millions"][segment]
        q3_yoy_change = recent["other_revenue_millions"][segment] - recent_prior["other_revenue_millions"][segment]
        q3_prior = recent_prior["other_revenue_millions"][segment]
        # Carry the absolute change for a zero or negative eliminations base.
        other[segment] = prior_q4 * (1 + q3_yoy_change / q3_prior) if q3_prior > 0 else prior_q4 + q3_yoy_change
    core = fsum(p.revenue_millions for p in unedited.values()) + fsum(other[s] for s in SEGMENTS[:2])
    corporate = annual_anchor - core
    inputs, overrides = _guided_rates(guided, rates, corporate)
    services = project(inputs)
    segment_revenue = {
        segment: fsum(services[service].revenue_millions for service in group) + other[segment]
        for segment, group in zip(SEGMENTS[:2], (DOMESTIC, INTERNATIONAL), strict=True)
    }
    segment_revenue[SEGMENTS[2]] = inputs["corporate_other_and_eliminations.revenue_millions"]
    total = fsum(segment_revenue.values())
    return {
        "operating_days": days, "services": services, "segment_revenue_millions": segment_revenue,
        "revenue_millions": total, "unadjusted_q3_growth_continuation_millions": core + other[SEGMENTS[2]],
        "domestic_and_international_other_revenue_millions": {key: other[key] for key in SEGMENTS[:2]},
        "annual_guidance_implied_revenue_millions": annual_anchor, "quarter_guidance_implied_revenue_millions": quarter_anchor,
        "rounded_guidance_difference_millions": annual_anchor - quarter_anchor,
        "corporate_other_implied_by_annual_guidance_millions": corporate,
        "difference_to_annual_guidance_millions": total - annual_anchor,
        "method": "Apply the latest same-quarter volume and yield changes to the prior Q4. Use the CY26 revenue outlook to estimate the remaining corporate and unallocated amount after the Supply Chain sale. Guided edits retain that baseline amount unless it is explicitly changed.",
    }, overrides


def _range(record, key):
    bounds = _array(_item(record, key, "guidance"), f"guidance.{key}")
    if len(bounds) != 2:
        raise ValueError(f"guidance.{key} requires two bounds.")
    low, high = (_number(value, key) for value in bounds)
    if not 0 < low <= high:
        raise ValueError(f"guidance.{key} must be positive and ordered.")
    return low, high


def _capital(data, dates, balance, as_of):
    balance_day = _day(_item(data, "balance_sheet_date"), "capital.balance_sheet_date")
    if balance_day != Q3_END:
        raise ValueError("FedEx capital bridge requires the September 30 balance sheet.")
    adjustments = _item(data, "september_30")
    sale = _item(data, "supply_chain_sale")
    sale_day = _day(_item(sale, "completed_on", "Supply Chain sale"), "Supply Chain sale date")
    if not balance_day < sale_day <= as_of:
        raise ValueError("The Supply Chain closing date must be after the balance sheet and within the cutoff.")
    _dated_source(_item(adjustments, "source"), dates, "September capital facts", observed_on=balance_day)
    _dated_source(_item(sale, "source"), dates, "Supply Chain sale", observed_on=sale_day)
    refunds = _num(adjustments, "customer_tariff_refund_obligations_millions", minimum=0)
    cash = _num(balance, "cash", "September balance sheet", minimum=refunds)
    debt = _num(balance, "current_debt", minimum=0) + _num(balance, "long_term_debt", minimum=0)
    finance_leases = _num(adjustments, "finance_lease_liability_millions", minimum=0)
    if finance_leases > debt:
        raise ValueError("Finance leases cannot exceed the debt balance that includes them.")
    hold_assets = fsum(_num(adjustments, f"supply_chain_held_for_sale_{scope}_assets_millions", minimum=0)
                       for scope in ("current", "noncurrent"))
    hold_liabilities = fsum(_num(adjustments, f"supply_chain_held_for_sale_{scope}_liabilities_millions", minimum=0)
                            for scope in ("current", "noncurrent"))
    quotes = _item(data, "quotes")
    quote_day = _day(_item(quotes, "date"), "quote date")
    if not as_of - timedelta(days=7) <= quote_day <= as_of or quote_day.weekday() > 4:
        raise ValueError("Use a recent completed trading session within the FedEx cutoff.")
    quote_sources = [_text(item, "quote source") for item in _array(_item(quotes, "source_ids"), "quote sources")]
    if len(set(quote_sources)) < 2:
        raise ValueError("The FDX and FDXF closes require dated sources.")
    for source in quote_sources:
        _dated_source(source, dates, "quote", observed_on=quote_day)
    shares = _item(data, "shares")

    def disclosed_shares(key):
        item = _item(shares, key, "shares")
        observed = _day(_item(item, "observed_on", key), f"{key} share date")
        _dated_source(_item(item, "source", key), dates, key, observed_on=observed)
        count = _num(item, "count", key, minimum=1)
        if observed > quote_day or not count.is_integer():
            raise ValueError(f"Invalid dated share count for {key}.")
        return int(count), observed

    fdx_shares, fdx_date = disclosed_shares("fdx")
    fdxf_shares, fdxf_date = disclosed_shares("fdxf_retained")
    fdx = _num(quotes, "fdx_close_usd", minimum=0.01)
    fdxf = _num(quotes, "fdxf_close_usd", minimum=0.01)
    return {
        "balance_sheet_date": balance_day, "reported_cash_millions": cash, "tariff_cash_for_customers_millions": refunds,
        "cash_after_customer_obligation_before_operating_reserve_millions": cash - refunds,
        "reported_debt_including_finance_leases_millions": debt, "finance_leases_included_millions": finance_leases,
        "operating_leases_separate_millions": _num(adjustments, "operating_lease_liability_millions", minimum=0),
        "inpost_carrying_value_millions": _num(balance, "equity_method_investment", minimum=0),
        "fdxf_carrying_value_millions": _num(balance, "short_term_investment", minimum=0),
        "other_debt_securities_millions": _num(adjustments, "debt_securities_in_prepaids_millions", minimum=0),
        "other_equity_securities_millions": _num(adjustments, "other_equity_securities_millions", minimum=0),
        "supply_chain_sale_date": sale_day, "supply_chain_net_cash_proceeds_millions": _num(sale, "net_cash_proceeds_millions", minimum=0),
        "supply_chain_held_for_sale_net_carrying_millions": hold_assets - hold_liabilities,
        "quote_date": quote_day, "fdx_reference_close_usd": fdx, "fdx_share_date": fdx_date, "fdx_shares": fdx_shares,
        "fdxf_reference_close_usd": fdxf, "fdxf_share_observation_date": fdxf_date, "fdxf_retained_shares": fdxf_shares,
        "fdxf_indicative_gross_mark_millions": fdxf * fdxf_shares / 1e6,
        "limitations": data["limitations"], "quote_note": quotes["note"],
    }


INCOME_ADDITIVE = (
    "revenue", "salaries_and_benefits", "purchased_transportation", "rentals_and_landing_fees", "depreciation_amortization",
    "fuel", "maintenance_and_repairs", "other_operating_expenses", "operating_expenses", "operating_income",
    "short_term_investment_gain_loss", "net_interest", "other_nonoperating_income", "nonoperating_income", "pretax_income",
    "income_taxes", "continuing_net_income", "discontinued_net_income", "net_income",
)
ACTUAL_SEGMENTS = (*SEGMENTS[:2], "corporate_other_eliminations")


def _quarter(year, number):
    last_month = number * 3
    return date(year, last_month - 2, 1), date(year, last_month, monthrange(year, last_month)[1])


def _reported_periods(data, dates, checks):
    rows = _item(data, "income_statements")
    bounds = {f"{year}_q{q}": _quarter(year, q) for year, last in ((2024, 4), (2025, 4), (2026, 3))
              for q in range(1, last + 1)}
    bounds.update({str(year): (date(year, 1, 1), date(year, 12, 31)) for year in (2024, 2025)})
    for year, transition_key in ((2025, "2025_transition_comparable"), (2026, "2026_transition_through_q3")):
        bounds[f"{year}_ytd"] = (date(year, 1, 1), date(year, 9, 30))
        bounds[f"{year}_june"] = (date(year, 6, 1), date(year, 6, 30))
        bounds[transition_key] = (date(year, 6, 1), date(year, 9, 30))
    periods = {key: _period(rows, key, dates, window, source_field="source_id") for key, window in bounds.items()}

    def sum_columns(total_key, keys):
        total = periods[total_key]
        for line in INCOME_ADDITIVE:
            _reconcile(checks, f"{total_key} {line} period bridge", fsum(_num(periods[key], line, key) for key in keys),
                       _num(total, line, total_key))

    for key, row in periods.items():
        if "adjusted_source_id" in row:
            _dated_source(row["adjusted_source_id"], dates, f"{key} adjusted earnings", observed_on=bounds[key][1])
        for label, parts in (("operating_expenses", INCOME_ADDITIVE[1:8]),
                             ("nonoperating_income", ("short_term_investment_gain_loss", "net_interest", "other_nonoperating_income")),
                             ("pretax_income", ("operating_income", "nonoperating_income")),
                             ("net_income", ("continuing_net_income", "discontinued_net_income"))):
            _reconcile(checks, f"{key} {label}", fsum(_num(row, name, key) for name in parts), _num(row, label, key))
        _reconcile(checks, f"{key} operating income", row["revenue"] - row["operating_expenses"], row["operating_income"])
        _reconcile(checks, f"{key} continuing income", row["pretax_income"] - row["income_taxes"], row["continuing_net_income"])
        segments = _item(row, "segments", key)
        for metric in ("revenue", "operating_income"):
            total = fsum(_num(_item(segments, segment, key), metric, segment) for segment in ACTUAL_SEGMENTS)
            _reconcile(checks, f"{key} segment {metric}", total, row[metric])
    for year in (2024, 2025):
        sum_columns(str(year), tuple(f"{year}_q{q}" for q in range(1, 5)))
    for year, transition in ((2025, "2025_transition_comparable"), (2026, "2026_transition_through_q3")):
        sum_columns(f"{year}_ytd", tuple(f"{year}_q{q}" for q in range(1, 4)))
        sum_columns(transition, (f"{year}_june", f"{year}_q3"))
    return periods, bounds


def _balance_and_cash(data, dates, windows, checks):
    balances = _item(data, "balance_sheets")
    for key in ("2024-12-31", "2025-12-31", "2026-05-31", "2026-09-30"):
        row = _item(balances, key, "balance sheets")
        if row["as_of"] != key:
            raise ValueError(f"Unexpected balance sheet date for {key}.")
        _dated_source(_item(row, "source_id", key), dates, key, observed_on=date.fromisoformat(key))
        for label, parts in (
            ("current_assets", ("cash", "receivables", "short_term_investment", "prepaids_and_other",
                                "spare_parts_supplies_and_fuel", "discontinued_current_assets")),
            ("net_ppe", ("gross_ppe", "accumulated_depreciation")),
            ("other_long_term_assets", ("operating_lease_rou_assets", "goodwill_and_other_assets",
                                        "equity_method_investment", "discontinued_noncurrent_assets")),
            ("total_assets", ("current_assets", "net_ppe", "other_long_term_assets")),
            ("current_liabilities", ("current_debt", "current_operating_lease_liabilities", "other_accrued_liabilities",
                                     "payables", "accrued_salaries_and_benefits", "discontinued_current_liabilities")),
            ("total_equity", ("common_stock", "additional_paid_in_capital", "retained_earnings",
                              "accumulated_other_comprehensive_loss", "treasury_stock")),
            ("total_liabilities_and_equity", ("current_liabilities", "long_term_debt", "other_long_term_liabilities", "total_equity")),
        ):
            _reconcile(checks, f"{key} {label}", fsum(_num(row, field, key) for field in parts), _num(row, label, key))
        _reconcile(checks, f"{key} balance sheet", row["total_assets"], row["total_liabilities_and_equity"])
    flows = _item(data, "cash_flows")
    totals = ("cash_from_operations", "cash_from_investing", "cash_from_financing", "effect_of_exchange_rates")
    for key in ("2025_ytd", "2025_transition_comparable", "2026_ytd", "2026_transition_through_q3"):
        flow = _period(flows, key, dates, windows[key], source_field="source_id")
        if flow["basis"] != "reported_consolidated_unrecast":
            raise ValueError(f"Unexpected cash flow reporting basis for {key}.")
        operating_lines = ["net_income", "depreciation_amortization", "other_noncash_items", "short_term_investment_gain_reversal",
                           "debt_extinguishment_gain_reversal", "changes_in_operating_assets_and_liabilities"]
        if "transition" in key:
            operating_lines.extend(("provision_for_uncollectible_accounts", "retirement_plan_mark_to_market_reversal"))
        for label, parts in (
            ("cash_from_operations", operating_lines),
            ("cash_from_investing", ("equity_method_investment_purchases", "capital_expenditures", "investment_purchases",
                                     "investment_sale_proceeds", "other_investing")),
            ("cash_from_financing", ("debt_proceeds", "debt_principal_paid", "stock_issuance_proceeds", "dividends_paid",
                                     "share_repurchases", "spin_off_distribution", "other_financing")),
        ):
            _reconcile(checks, f"{key} {label}", fsum(_num(flow, field, key) for field in parts), _num(flow, label, key))
        opening = _num(flow, "opening_cash_continuing", key) + _num(flow, "opening_cash_discontinued", key)
        change = fsum(_num(flow, name, key) for name in totals)
        _reconcile(checks, f"{key} cash opening basis", opening, _num(flow, "opening_cash", key))
        _reconcile(checks, f"{key} cash flow subtotal", change, _num(flow, "net_change_in_cash", key))
        _reconcile(checks, f"{key} cash flow roll-forward", opening + change, _num(flow, "closing_cash", key))
        if key.startswith("2026"):
            _reconcile(checks, f"{key} ending cash to September balance sheet", flow["closing_cash"], balances["2026-09-30"]["cash"])
    transition = flows["2026_transition_through_q3"]
    may = balances["2026-05-31"]
    _reconcile(checks, "Transition opening cash to May continuing and discontinued cash",
               may["cash"] + may["cash_of_discontinued_operations"], transition["opening_cash"])
    return balances["2026-09-30"]


def _guidance(data, actuals, periods, checks):
    guidance = _item(data, "guidance")
    full_eps = _range(guidance, "adjusted_diluted_eps")
    q4_eps = _range(guidance, "q4_adjusted_diluted_eps")
    shares = _num(guidance, "q4_average_diluted_shares_millions", minimum=1)
    bridge = _item(data, "q4_bridge")
    capex = _num(guidance, "capex_millions", minimum=0)
    capex_ytd = _num(_item(_item(actuals, "capital_spending"), "2026_ytd"), "total", minimum=0)
    _reconcile(checks, "Continuing nine-month capex cross-source check", _num(bridge, "nine_months_cy26_continuing_capex_millions"), capex_ytd)
    cash = _item(guidance, "illustrative_cash_flow_millions", "guidance")
    values = {key: _num(cash, key, "cash guidance") for key in (
        "operating_cash_flow", "capex", "asset_disposition_proceeds", "fcf",
        "voluntary_pension_contributions_addback", "ieepa_refunds_due_to_customers_adjustment", "adjusted_fcf",
    )}
    _reconcile(checks, "Illustrative issuer FCF", values["operating_cash_flow"] - values["capex"] + values["asset_disposition_proceeds"], values["fcf"])
    _reconcile(checks, "Illustrative issuer adjusted FCF", values["fcf"] + values["voluntary_pension_contributions_addback"]
               + values["ieepa_refunds_due_to_customers_adjustment"], values["adjusted_fcf"])
    q4_capex = capex - capex_ytd
    illustrative_capex = values["capex"] - capex_ytd
    if min(q4_capex, illustrative_capex) < 0:
        raise ValueError("CY2026 capex outlook is below reported continuing capex through September.")
    first_three = tuple(periods[f"2026_q{q}"] for q in (1, 2, 3))
    adjusted_ebit_ytd = fsum(_num(period, "adjusted_operating_income") for period in first_three)
    adjusted_ni_ytd = fsum(_num(period, "adjusted_net_income") for period in first_three)
    annual_shares = (_num(periods["2026_ytd"], "diluted_weighted_average_shares", minimum=1) * (Q3_END - CY_START + timedelta(days=1)).days
                     + shares * (Q4_END - Q3_END).days) / (Q4_END - CY_START + timedelta(days=1)).days
    income_range = tuple(eps * shares for eps in q4_eps)
    return {
        "q4_adjusted_eps_guidance": q4_eps, "q4_average_diluted_shares_guidance_millions": shares,
        "q4_implied_adjusted_net_income_range_millions": income_range,
        "q4_implied_adjusted_operating_income_millions": _num(guidance, "illustrative_adjusted_operating_income_millions") - adjusted_ebit_ytd,
        "q4_capex_implied_by_rounded_guidance_millions": q4_capex, "q4_capex_implied_by_issuer_illustration_millions": illustrative_capex,
        "q4_voluntary_pension_contribution_millions": _num(bridge, "q4_voluntary_pension_contributions_millions", minimum=0),
        "cy26_adjusted_eps_guidance": full_eps, "cy26_gaap_eps_guidance": _range(guidance, "gaap_diluted_eps"),
        "cy26_approximate_adjusted_eps_using_weighted_shares": tuple((adjusted_ni_ytd + v) / annual_shares for v in income_range),
        "cy26_adjusted_net_income_ytd_sum_millions": adjusted_ni_ytd,
        "cy26_adjusted_operating_income_ytd_sum_millions": adjusted_ebit_ytd,
        "cy26_issuer_cash_flow_illustration_millions": values,
        "management_anchors": {key: guidance[key] for key in ("calendar_2027", "calendar_2029_targets", "network_optimization")},
    }


def run_current_fdx(as_of: date, inputs_dir: Path, *, guided: Path | None = None) -> dict:
    """Reconcile the current snapshot and project Q4 on a calendar basis."""
    actuals, operations, capital = (_load(inputs_dir / f"fdx-{name}-{as_of.isoformat()}.json")
                                   for name in ("actuals", "operations", "capital"))
    for name, data in (("actuals", actuals), ("operations", operations), ("capital", capital)):
        if _day(_item(data, "as_of", name), f"{name}.as_of") != as_of:
            raise ValueError(f"{name} does not match the requested FedEx cutoff.")
    if (actuals.get("ticker"), actuals.get("currency"), actuals.get("monetary_units")) != ("FDX", "USD", "millions"):
        raise ValueError("The actuals snapshot must contain FedEx amounts in USD millions.")
    basis = _item(actuals, "reporting_basis")
    if (_item(basis, "fiscal_year_end") != "12-31" or _item(basis, "effective_date") != TRANSITION_START.isoformat()
            or _item(basis, "transition_period_start") != TRANSITION_START.isoformat()
            or _item(basis, "transition_period_end") != Q4_END.isoformat()
            or _item(basis, "freight_spin_off_date") != TRANSITION_START.isoformat()):
        raise ValueError("Use the December FedEx reporting basis and June–December 2026 transition dates.")
    checks = []
    actual_dates = _sources(actuals, as_of, date_field="published_on")
    operating_dates = _sources(operations, as_of)
    capital_dates = _sources(capital, as_of)
    actual_periods, actual_windows = _reported_periods(actuals, actual_dates, checks)
    balance = _balance_and_cash(actuals, actual_dates, actual_windows, checks)
    capital_inputs = _capital(capital, capital_dates, balance, as_of)
    for key, fact in (("tariff_refund_customer_obligation", "tariff_cash_for_customers_millions"),
                      ("finance_lease_liability", "finance_leases_included_millions"),
                      ("marketable_debt_securities_in_prepaids", "other_debt_securities_millions"),
                      ("other_equity_securities_in_goodwill_and_other_assets", "other_equity_securities_millions")):
        _reconcile(checks, f"September {key}", _num(balance, key), capital_inputs[fact])
    _reconcile(checks, "September operating lease balance", balance["current_operating_lease_liabilities"]
               + balance["long_term_operating_lease_liabilities"], capital_inputs["operating_leases_separate_millions"])
    _reconcile(checks, "Shares outstanding cross-source check", _num(_item(actuals, "latest_share_count"), "shares"), capital_inputs["fdx_shares"], 0)
    sale = _item(_item(actuals, "transactions"), "fedex_supply_chain_sale")
    _reconcile(checks, "Supply Chain proceeds cross-source check", _num(sale, "net_cash_proceeds_approximate"),
               capital_inputs["supply_chain_net_cash_proceeds_millions"])
    if _day(_item(sale, "date"), "Supply Chain sale date") != capital_inputs["supply_chain_sale_date"]:
        raise ValueError("The Supply Chain sale dates disagree.")
    held_for_sale = _item(sale, "held_for_sale_balances_2026_09_30")
    _reconcile(checks, "Supply Chain net held-for-sale assets cross-source check", _num(held_for_sale, "current_assets")
               + _num(held_for_sale, "noncurrent_assets") - _num(held_for_sale, "current_liabilities")
               - _num(held_for_sale, "noncurrent_liabilities"), capital_inputs["supply_chain_held_for_sale_net_carrying_millions"])

    periods = _item(operations, "periods")
    for key in ("2025_q1", "2025_q2", "2025_q3", "2025_q4", "2026_q1", "2026_q2", "2026_q3", "2025_transition_comparable", "2026_transition_through_q3"):
        op_key = f"{key[:4]}_jun_sep" if "transition" in key else key.replace("_", "")
        row = _period(periods, op_key, operating_dates, actual_windows[key])
        _operating_period(row, op_key, checks)
        for metric, actual_metric in (("revenue_millions", "revenue"), ("operating_income_millions", "operating_income")):
            _reconcile(checks, f"{op_key} {actual_metric} to statement", _num(row, metric), actual_periods[key][actual_metric])

    bridge = _item(operations, "q4_bridge")
    guide = _item(operations, "guidance")
    guide_date = _day(_item(guide, "available_on"), "guidance.available_on")
    for field in ("source", "presentation_source"):
        _dated_source(_item(guide, field), operating_dates, "guidance", observed_on=guide_date)
    if guide["period_start"] != CY_START.isoformat() or guide["period_end"] != Q4_END.isoformat():
        raise ValueError("CY2026 revenue guidance must cover January through December.")
    if bridge["period_start"] != "2026-10-01" or bridge["period_end"] != Q4_END.isoformat():
        raise ValueError("The Q4 forecast must cover October through December.")
    if tuple(bridge[name] for name in ("seasonality_period", "trend_period", "trend_comparison_period")) != ("2025q4", "2026q3", "2025q3"):
        raise ValueError("Q4 must use prior Q4 seasonality and the latest Q3 year-over-year drivers.")
    _dated_source(_item(bridge, "operating_days_source"), operating_dates, "Q4 operating days")
    days = _num(bridge, "operating_days", minimum=1)
    if not days.is_integer() or days > 92:
        raise ValueError("Projected Q4 operating days must fit within the quarter.")
    cy25 = actual_periods["2025"]["revenue"]
    _reconcile(checks, "CY2025 guidance base to recast annual revenue", cy25, _num(bridge, "cy25_revenue_millions"))
    ytd = actual_periods["2026_ytd"]["revenue"]
    transition_ytd = actual_periods["2026_transition_through_q3"]["revenue"]
    q4_annual = cy25 * (1 + _num(guide, "revenue_growth", minimum=-1)) - ytd
    q4_quarter = actual_periods["2025_q4"]["revenue"] * (1 + _num(guide, "q4_revenue_growth", minimum=-1))
    if min(q4_annual, q4_quarter) <= 0:
        raise ValueError("Guidance must imply positive Q4 sales.")
    revenue, overrides = _project_revenue(periods["2025q4"], periods["2026q3"], periods["2025q3"], int(days),
                                          q4_annual, q4_quarter, _load(guided) if guided is not None else None)
    total = revenue["revenue_millions"]
    if not isfinite(total) or total <= 0 or any(not isfinite(p.revenue_millions) for p in revenue["services"].values()):
        raise ValueError("FedEx overrides must yield finite, positive consolidated revenue.")
    earnings_cash = _guidance(operations, actuals, actual_periods, checks)
    reported_cash = _item(actuals, "cash_flows")
    cash_actuals = {key: {metric: reported_cash[key][metric] for metric in (
        "period_start", "period_end", "basis", "source_id", "opening_cash_continuing", "opening_cash_discontinued",
        "cash_from_operations", "cash_from_investing", "cash_from_financing", "effect_of_exchange_rates", "closing_cash",
    )} for key in ("2026_ytd", "2026_transition_through_q3")}
    return {
        "as_of": as_of, "mode": "guided" if guided is not None else "automated", "units": "USD millions except per-share data and stated drivers",
        "calendar_year": {"start": CY_START, "end": Q4_END, "reported_revenue_millions": ytd, "forecast_revenue_millions": ytd + total},
        "transition_period": {"start": TRANSITION_START, "end": Q4_END, "reported_revenue_millions": transition_ytd,
                              "forecast_revenue_millions": transition_ytd + total},
        "q4_revenue": revenue, "guidance_bridge": earnings_cash, "capital_inputs": capital_inputs, "reported_cash_flows": cash_actuals,
        "override_log": overrides, "checks": checks, "source_register": {name: data["sources"] for name, data in
                              (("actuals", actuals), ("operations", operations), ("capital", capital))},
        "missing_for_valuation": (
            "A continuing-operations Q4 cash bridge that resolves mixed-basis historical cash flows, tariff refund timing and October–November cash changes.",
            "Standalone Q4 Supply Chain revenue and cost removed by the October 1 sale. The automated model uses an unallocated guidance residual.",
            "FY2027 onward operating, cash conversion and capital structure forecasts on the December calendar.",
            "The operating cash reserve, appropriate retained investment values and costs, and dated discount-rate and peer valuation inputs.",
        ),
    }

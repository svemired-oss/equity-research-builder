"""Build dated Newmont production, payable sales, prices and CAS."""

from dataclasses import dataclass
from datetime import date
from math import fsum, isclose
from pathlib import Path

from .fdx_current import _dated_source, _load, _num, _reconcile, _sources
from .gd_inputs import _array, _day, _fields, _number, _text
from .nem_revenue import NEM_COMMODITIES


METALS = tuple(NEM_COMMODITIES)
BASE_METALS = ("copper", "lead", "zinc")
GROWTH_SITES = ("lihir", "boddington", "tanami")
YTD = "ytd_2026"
QUARTER = "q3_2026"


@dataclass(frozen=True)
class MineVolume:
    produced: float | None
    sold: float | None
    attributable_gold_produced: float | None


@dataclass(frozen=True)
class RealizedPrice:
    benchmark_usd: float
    gross_usd: float
    provisional_usd: float
    streaming_amortization_usd: float
    treatment_usd: float
    net_usd: float
    source: str


@dataclass(frozen=True)
class MineEconomics:
    sales_millions: float
    cas_millions: float
    sales_less_cas_millions: float
    realized_usd_per_unit: float | None
    cas_usd_per_unit: float | None
    noncash_stream_amortization_millions: float


def _evidence(folder, name, as_of):
    packet = _load(folder / f"nem-{name}-{as_of.isoformat()}.json")
    if packet.get("as_of") != as_of.isoformat() or packet.get("ticker") != "NEM":
        raise ValueError(f"Newmont {name} evidence uses a different ticker or date.")
    if name != "production" and packet.get("currency") != "USD":
        raise ValueError(f"Newmont {name} must use USD.")
    return packet


def _value(row, key):
    return _num(row, key, minimum=0)


def _optional(row, key):
    return None if row.get(key) is None else _value(row, key)


def _nonnegative_residual(annual, interim, label):
    if annual < interim:
        raise ValueError(f"{label} annual amount is below reported year-to-date production.")
    return annual - interim


def _weights(values, total, label):
    denominator = fsum(values.values())
    if denominator <= 0:
        if total == 0:
            return dict.fromkeys(values, 0.0)
        raise ValueError(f"Missing positive allocation base for {label}.")
    return {key: value * total / denominator for key, value in values.items()}


def _volume_totals(rows):
    return {
        metal: MineVolume(
            fsum(site[metal].produced for site in rows.values() if metal in site and site[metal].produced is not None),
            fsum(site[metal].sold for site in rows.values() if metal in site and site[metal].sold is not None),
            fsum(site[metal].attributable_gold_produced for site in rows.values() if metal in site) if metal == "gold" else None,
        )
        for metal in METALS
    }


def _read_volumes(packet, period, checks):
    result = {}
    for key, site in packet["mine_rows"].items():
        ownership = _value(site, "ownership")
        accounting = site["accounting"]
        if not 0 < ownership <= 1 or accounting not in ("full", "proportionate", "equity_method"):
            raise ValueError(f"Invalid Newmont interest for {key}.")
        rows = {}
        for metal, entry in site["periods"][period].items():
            if metal not in METALS:
                raise ValueError(f"Unexpected Newmont commodity {metal}.")
            produced, sold = _optional(entry, "produced"), _optional(entry, "sold")
            attributable = _value(entry, "attributable_produced") if metal == "gold" else None
            if accounting == "equity_method":
                if produced is not None or sold is not None or metal != "gold":
                    raise ValueError(f"{key} equity-method quantities must stay outside consolidated mine volumes.")
            else:
                if produced is None or sold is None:
                    raise ValueError(f"Missing reported quantity for {key} {metal} {period}.")
                if attributable is not None:
                    expected = ownership * produced if accounting == "full" else produced
                    _reconcile(checks, f"{period} {key} gold ownership", attributable, expected, tolerance=1)
            rows[metal] = MineVolume(produced, sold, attributable)
        result[key] = rows
    if period == "fy_2025":
        for key, site in packet["divested_fy_2025"].items():
            produced, sold = (_value(site["gold"], field) for field in ("produced", "sold"))
            result[key] = {"gold": MineVolume(produced, sold, produced)}

    for metal, total in _volume_totals(result).items():
        reported = packet["reported_totals"][period][metal]
        _reconcile(checks, f"{period} {metal} consolidated production", total.produced, _value(reported, "produced"))
        _reconcile(checks, f"{period} {metal} consolidated sold", total.sold, _value(reported, "sold"))
        if metal == "gold":
            _reconcile(checks, f"{period} gold attributable production", total.attributable_gold_produced, _value(reported, "attributable_produced"))
    return result


def _q4_volumes(packet, actuals, checks):
    annual = packet["guidance"]["fy_2026"]
    total_actual = _volume_totals(actuals)
    required_sales = _value(annual["gold"], "sold") - total_actual["gold"].sold
    if required_sales <= 0:
        raise ValueError("Full-year consolidated gold sales must exceed year-to-date actuals.")
    q4 = {}
    gold_sales_weights = {}
    for key, site in packet["mine_rows"].items():
        rows = {}
        for metal, guidance in site["guidance_2026"].items():
            prior = actuals[key][metal]
            attributable = _nonnegative_residual(_value(guidance, "attributable_produced"), prior.attributable_gold_produced, key) if metal == "gold" else None
            if site["accounting"] == "equity_method":
                rows[metal] = MineVolume(None, None, attributable)
                continue
            produced = _nonnegative_residual(_value(guidance, "produced"), prior.produced, f"{key} {metal}")
            if prior.produced <= 0:
                raise ValueError(f"Missing prior production base for {key} {metal} shipment proxy.")
            implied_sales = produced * prior.sold / prior.produced
            if metal == "gold":
                gold_sales_weights[key] = implied_sales
            rows[metal] = MineVolume(produced, implied_sales, attributable)
        if rows.keys() != actuals[key].keys():
            raise ValueError(f"Mine guidance coverage differs from reported metals at {key}.")
        q4[key] = rows
    for key, allocated in _weights(gold_sales_weights, required_sales, "Q4 gold shipments").items():
        row = q4[key]["gold"]
        q4[key]["gold"] = MineVolume(row.produced, allocated, row.attributable_gold_produced)
    totals = _volume_totals(q4)
    _reconcile(checks, "FY2026 consolidated gold sales guidance", totals["gold"].sold + total_actual["gold"].sold, _value(annual["gold"], "sold"))
    _reconcile(checks, "FY2026 attributable gold production guidance", totals["gold"].attributable_gold_produced
               + total_actual["gold"].attributable_gold_produced, _value(annual["gold"], "attributable_produced"))
    _reconcile(checks, "Q4 attributable gold production to rounded guidance", totals["gold"].attributable_gold_produced,
               _value(packet["guidance"]["q4_2026"]["gold"], "attributable_produced"), tolerance=1)
    for metal in METALS:
        if metal != "gold":
            _reconcile(checks, f"FY2026 {metal} production guidance", totals[metal].produced + total_actual[metal].produced,
                       _value(annual[metal], "produced"))
    return q4


def _annual_volumes(actuals, forecast):
    result = {}
    for site, rows in actuals.items():
        annual = {}
        for metal, actual in rows.items():
            extra = forecast[site][metal]
            annual[metal] = MineVolume(
                None if actual.produced is None else actual.produced + extra.produced,
                None if actual.sold is None else actual.sold + extra.sold,
                None if metal != "gold" else actual.attributable_gold_produced + extra.attributable_gold_produced,
            )
        result[site] = annual
    return result


def _forward_volumes(annual, forward, ngm_ownership, growth, other_change, checks):
    attributable = _volume_totals(annual)["gold"].attributable_gold_produced
    growth_sites = tuple(forward["gold_growth_sites"])
    if growth_sites != GROWTH_SITES:
        raise ValueError("The dated growth sites must match the October Newmont outlook.")
    ngm = forward["ngm_operator_outlook"]
    low, high = (_value(ngm, key) for key in ("low_full_mine_koz", "high_full_mine_koz"))
    if not 0 < low <= high:
        raise ValueError("Invalid Nevada Gold Mines operator range.")
    ngm_addition = (low + high) / 2 * ngm_ownership - annual["ngm"]["gold"].produced
    additions = _weights({site: annual[site]["gold"].produced for site in growth_sites}, attributable * growth - ngm_addition,
                         "2027 managed mine gold growth")
    additions["ngm"] = ngm_addition
    result = {}
    for site, rows in annual.items():
        projected = {}
        for metal, line in rows.items():
            if line.produced is None:
                projected[metal] = line
                continue
            addition = additions.get(site, 0.0) if metal == "gold" else 0.0
            scale = 1 + other_change if site == forward["lower_coproduct_site"] and metal in forward["lower_coproduct_metals"] else 1.0
            produced = line.produced * scale + addition
            if produced < 0 or line.produced <= 0:
                raise ValueError(f"Invalid 2027 production basis for {site} {metal}.")
            projected[metal] = MineVolume(produced, line.sold * (produced / line.produced),
                                         line.attributable_gold_produced + addition if metal == "gold" else None)
        result[site] = projected
    _reconcile(checks, "FY2027 modeled attributable gold growth", _volume_totals(result)["gold"].attributable_gold_produced, attributable * (1 + growth))
    return result


def _realized_prices(packet, as_of, volume_totals, assumptions, checks):
    sources = _sources(packet, as_of)
    pounds = _value(packet, "pounds_per_metric_tonne")
    if not isclose(pounds, 1000 / 0.45359237, rel_tol=1e-12):
        raise ValueError("Newmont price and volume must use the same metric tonne conversion.")
    stream = packet["silver_stream"]
    share, stream_cash = _value(stream, "share_of_production"), _value(stream, "cash_price_usd")
    if not 0 < share < 1 or stream_cash <= 0:
        raise ValueError("Invalid silver stream terms.")
    for field in ("source", "contract_source"):
        _dated_source(stream[field], sources, "silver stream")

    prices = {"q4_2026": {}, "fy_2027": {}}
    reported_sales = packet["reported_consolidated_sales_millions"]
    _dated_source(reported_sales["source"], sources, "consolidated sales")
    controls = {QUARTER: _value(reported_sales, "q3_2026"), YTD: _value(reported_sales, "nine_months_2026")}
    sales_actual = {period: {} for period in controls}
    for metal, (unit, conversion) in NEM_COMMODITIES.items():
        row = packet["metals"][metal]
        if row["sales_quantity_unit"] != unit:
            raise ValueError(f"Invalid Newmont historical sale units for {metal}.")
        for volume_period, price_period in ((QUARTER, "q3_2026"), (YTD, "nine_months_2026")):
            history = row[price_period]
            _dated_source(history["source"], sources, f"{metal} realized price")
            full_quantity = volume_totals[volume_period][metal].sold
            compare_quantity = full_quantity * pounds / 1000 if metal in BASE_METALS else full_quantity
            # Cross-unit comparisons allow the summed precision of individually rounded mine rows.
            count = 3 if metal == "copper" else 1
            tolerance = count * pounds / 2000 + 0.5 if metal in BASE_METALS else 0.0
            _reconcile(checks, f"{price_period} {metal} sold units to price disclosure",
                       _value(history, "sold_volume"), compare_quantity, tolerance=tolerance)
            _reconcile(checks, f"{price_period} {metal} price waterfall",
                       fsum(_num(history, key) for key in ("gross_usd", "provisional_usd", "streaming_usd", "treatment_usd")),
                       _value(history, "net_usd"), tolerance=0.011)
            _reconcile(checks, f"{price_period} {metal} sales waterfall",
                       fsum(_num(history, key) for key in ("gross_sales_millions", "provisional_millions", "streaming_millions", "treatment_millions")),
                       _value(history, "sales_millions"))
            quantity, net = _value(history, "sold_volume"), _value(history, "net_usd")
            tick = 0.5 if metal == "gold" else 0.005
            rounding = (quantity * tick + 0.5 * (net + tick)) * conversion + 0.5
            _reconcile(checks, f"{price_period} {metal} rounded price times sales", quantity * net * conversion,
                       _value(history, "sales_millions"), tolerance=rounding)
            sales_actual[volume_period][metal] = history

        def benchmark(key):
            quote = row[key]
            observed = _day(quote["observed_on"], f"{metal} {key}")
            _dated_source(quote["source"], sources, f"{metal} {key}", observed_on=observed)
            value = _value(quote, "quote_value")
            quote_unit = "USD/metric_tonne" if metal in BASE_METALS else "USD/troy_ounce"
            if quote["quote_unit"] != quote_unit or value <= 0:
                raise ValueError(f"Invalid {metal} quote basis at {key}.")
            _reconcile(checks, f"{metal} {key} unit conversion",
                       value / pounds if metal in BASE_METALS else value, _value(quote, "price_usd"))
            return _value(quote, "price_usd")

        def stream_basis(value):
            return (1 - share) * value + share * min(value, stream_cash) if metal == "silver" else value

        realization = _value(row["q3_2026"], "gross_usd") / stream_basis(benchmark("q3_benchmark"))
        terms = row["forecast"]
        _dated_source(terms["source"], sources, f"{metal} forecast realization terms")
        for period, key in (("q4_2026", "market_anchor"), ("fy_2027", "annual_benchmark_2027")):
            benchmark(key)
            value = assumptions[f"{period}.{metal}.benchmark_usd"]
            gross = stream_basis(value) * realization
            provisional, streaming, treatment = (_num(terms, k) for k in ("provisional_usd", "streaming_usd", "treatment_usd"))
            net = gross + provisional + streaming + treatment
            if net <= 0:
                raise ValueError(f"The {metal} benchmark must support a positive net realized price.")
            prices[period][metal] = RealizedPrice(value, gross, provisional, streaming, treatment, net, row[key]["source"])
    for period, target in controls.items():
        _reconcile(checks, f"{period} commodity revenue to filed sales", fsum(row["sales_millions"] for row in sales_actual[period].values()), target)
    return prices, sales_actual


def _assumptions(production, prices, costs, guided):
    forward = production["forward_2027"]["analyst_assumptions"]
    values = {
        "fy_2027.attributable_gold_growth": _num(forward, "attributable_gold_growth"),
        "fy_2027.penasquito_coproduct_change": _num(forward, "penasquito_coproduct_change"),
        "fy_2027.unit_cas_growth": _num(costs["analyst_assumptions"], "annual_coproduct_unit_cost_growth"),
    }
    for metal in METALS:
        for period, key in (("q4_2026", "market_anchor"), ("fy_2027", "annual_benchmark_2027")):
            values[f"{period}.{metal}.benchmark_usd"] = _value(prices["metals"][metal][key], "price_usd")
    overrides = []
    if guided is not None:
        document = _fields(_load(guided), "guided assumptions", {"overrides"})
        seen = set()
        for row in _array(document["overrides"], "guided overrides"):
            row = _fields(row, "override", {"target", "value", "reason"})
            target = _text(row["target"], "override target")
            if target not in values or target in seen:
                raise ValueError(f"Unsupported or repeated Newmont override {target!r}.")
            value = _number(row["value"], target)
            overrides.append({"target": target, "before": values[target], "value": value, "reason": _text(row["reason"], "override reason")})
            values[target] = value
            seen.add(target)
    for name, value in values.items():
        minimum = 0 if name.endswith(".benchmark_usd") else -1
        if value <= minimum:
            raise ValueError(f"Invalid Newmont assumption for {name}.")
    return values, overrides


def _unit_quantity(metal, quantity, pounds):
    return quantity * pounds / 1000 if metal in BASE_METALS else quantity


def _million_quantity(metal, quantity, pounds):
    return _unit_quantity(metal, quantity, pounds) * NEM_COMMODITIES[metal][1]


def _forecast_cas(costs, production, q4, annual, forward, fy27_gold, assumptions, checks):
    reported = costs["reported_nine_months"]
    sites = reported["sites"]
    company = reported["consolidated"]
    corporate = reported["corporate_and_other"]
    annual_guidance = costs["guidance_2026"]["full_year"]
    q4_guidance = costs["guidance_2026"]["fourth_quarter_derived"]
    base_sites = {site: {metal: row for metal, row in rows.items() if row.sold is not None} for site, rows in production.items()}
    expected_sites = {site: set(rows) for site, rows in base_sites.items() if rows}
    if expected_sites != {site: set(row["metals"]) for site, row in sites.items()}:
        raise ValueError("Newmont costs and consolidated mine sales have different site coverage.")
    cas = {(site, metal): _value(row, "cas_millions") for site, summary in sites.items() for metal, row in summary["metals"].items()}
    historical_site_sales = {(site, metal): _value(row, "sales_millions") for site, summary in sites.items() for metal, row in summary["metals"].items()}
    _reconcile(checks, "Nine-month site CAS plus corporate and other", fsum(cas.values()) + _num(corporate, "cas_millions"), _value(company, "cas_millions"))
    _reconcile(checks, "Nine-month site sales", fsum(historical_site_sales.values()), _value(company, "sales_millions"))
    for key in ("depreciation_millions", "sustaining_capital_accrual_millions", "development_capital_accrual_millions"):
        if key == "depreciation_millions":
            site_total = fsum(_value(line, key) for row in sites.values() for line in row["metals"].values())
        else:
            site_total = fsum(_value(row, key) for row in sites.values())
        _reconcile(checks, f"Nine-month site and corporate {key}", site_total + _value(corporate, key), _value(company, key))
    _reconcile(checks, "Nine-month consolidated capex from accrual to cash",
               _value(company, "capex_accrual_millions") + _num(company, "capex_cash_reconciliation_millions"), _value(company, "capex_cash_millions"))
    _reconcile(checks, "Nine-month reported by-product CAS", _value(company, "cas_millions") - _value(company, "other_metals_net_sales_millions"),
               _value(company, "gold_byproduct_cas_millions"))
    _reconcile(checks, "Nine-month reported by-product AISC", _value(company, "total_aisc_before_coproduct_credits_millions")
               - _value(company, "other_metals_sales_before_treatment_refining_millions"), _value(company, "gold_byproduct_aisc_millions"))
    for annual_key, historical_key in (("gross_cas_millions", "cas_millions"), ("consolidated_gold_sold_koz", "gold_sold_koz"),
                                       ("coproduct_revenue_credits_millions", "other_metals_net_sales_millions")):
        _reconcile(checks, f"Q4 implied guidance {annual_key}", _value(annual_guidance, annual_key) - _value(company, historical_key),
                   _value(q4_guidance, annual_key))
    _reconcile(checks, "Q4 gold sold to CAS guidance denominator", _volume_totals(q4)["gold"].sold,
               _value(q4_guidance, "consolidated_gold_sold_koz"))

    q4_weights = {}
    for (site, metal), amount in cas.items():
        sold = base_sites[site][metal].sold
        if sold <= 0:
            raise ValueError(f"Missing nine-month sold quantity to model CAS for {site} {metal}.")
        q4_weights[site, metal] = amount * q4[site][metal].sold / sold
    q4_cost = _weights(q4_weights, _value(q4_guidance, "gross_cas_millions"), "Q4 mine CAS")
    fy26_cost = {key: cas[key] + q4_cost[key] for key in cas}
    _reconcile(checks, "Q4 modeled site CAS", fsum(q4_cost.values()), _value(q4_guidance, "gross_cas_millions"))
    _reconcile(checks, "FY2026 modeled site CAS and actual corporate adjustment",
               fsum(fy26_cost.values()) + _num(corporate, "cas_millions"), _value(annual_guidance, "gross_cas_millions"))

    sensitivity = costs["sensitivities"]
    royalty_slope = _value(sensitivity, "gold_related_royalty_production_tax_and_profit_sharing_cost_change_usd_per_ounce") / _value(sensitivity, "gold_price_change_usd_per_ounce")
    ytd_gold = _value(company, "gold_sold_koz")
    annual_gold = _value(annual_guidance, "consolidated_gold_sold_koz")
    fy26_embedded_price = (fsum(value for (_, metal), value in historical_site_sales.items() if metal == "gold") * 1000
                           + (annual_gold - ytd_gold) * _value(sensitivity, "gold_price_anchor_usd_per_ounce")) / annual_gold
    gold_cas_change = (fy27_gold - fy26_embedded_price) * royalty_slope
    fy27_cost = {}
    for (site, metal), amount in fy26_cost.items():
        sold = forward[site][metal].sold
        inflation_cost = amount / annual[site][metal].sold * sold * (1 + assumptions["fy_2027.unit_cas_growth"])
        value = inflation_cost + (gold_cas_change * sold / 1000 if metal == "gold" else 0)
        if value < 0:
            raise ValueError(f"Newmont projected CAS became negative for {site} {metal}.")
        fy27_cost[site, metal] = value
    royalty_adjustment = gold_cas_change * _volume_totals(forward)["gold"].sold / 1000
    return historical_site_sales, cas, q4_cost, fy26_cost, fy27_cost, fy26_embedded_price, royalty_adjustment


def _period_economics(volumes, sales, cas, stream, pounds, corporate_cas=0):
    sites = {}
    metals = {metal: {"sales": [], "cas": [], "stream": []} for metal in METALS}
    for (site, metal), revenue in sales.items():
        units = _million_quantity(metal, volumes[site][metal].sold, pounds)
        amortization = stream.get((site, metal), 0.0)
        cost = cas[site, metal]
        sites.setdefault(site, {})[metal] = MineEconomics(revenue, cost, revenue - cost, revenue / units if units else None,
                                                         cost / units if units else None, amortization)
        for field, value in (("sales", revenue), ("cas", cost), ("stream", amortization)):
            metals[metal][field].append(value)
    totals = _volume_totals(volumes)
    summary = {}
    for metal, fields in metals.items():
        volume = totals[metal]
        revenue, cost, noncash = (fsum(fields[key]) for key in ("sales", "cas", "stream"))
        units = _million_quantity(metal, volume.sold, pounds)
        summary[metal] = {
            "volume": volume,
            "production_less_sold": volume.produced - volume.sold,
            "economics": MineEconomics(revenue, cost, revenue - cost, revenue / units, cost / units, noncash),
        }
    total_sales = fsum(sales.values())
    total_cas = fsum(cas.values()) + corporate_cas
    other_metals_sales = total_sales - summary["gold"]["economics"].sales_millions
    return {
        "mines": {site: {"volumes": lines, "economics": sites.get(site, {})} for site, lines in volumes.items()},
        "metals": summary, "sales_millions": total_sales, "cas_millions": total_cas, "corporate_and_other_cas_millions": corporate_cas,
        "sales_less_cas_millions": total_sales - total_cas,
        "gold_byproduct_cas_usd_per_ounce": (total_cas - other_metals_sales) / totals["gold"].sold * 1000,
        "noncash_stream_amortization_millions": fsum(stream.values()),
    }


def _modeled_sales(volumes, prices, pounds):
    sales, amortization = {}, {}
    for site, rows in volumes.items():
        for metal, volume in rows.items():
            if volume.sold is not None:
                units = _million_quantity(metal, volume.sold, pounds)
                price = prices[metal]
                sales[site, metal] = units * price.net_usd
                if price.streaming_amortization_usd:
                    amortization[site, metal] = units * price.streaming_amortization_usd
    return sales, amortization


def run_current_nem(as_of: date, inputs_dir: Path, *, guided: Path | None = None) -> dict:
    """Return a documented Q4 bridge and near-term operating scenario with source checks."""
    production, prices, costs = (_evidence(inputs_dir, name, as_of) for name in ("production", "prices", "costs"))
    checks = []
    volume_units = {"gold": "koz", "silver": "Moz", **dict.fromkeys(BASE_METALS, "kt")}
    if production["units"] != volume_units:
        raise ValueError("Newmont production must use koz gold, Moz silver, and metric kilotonnes for base metals.")
    prod_sources = _sources(production, as_of)
    for label, source_ids in production["period_source_ids"].items():
        if not source_ids:
            raise ValueError(f"Missing Newmont evidence for {label}.")
        for source in source_ids:
            _dated_source(source, prod_sources, label)
    forward = production["forward_2027"]
    for label, section in (("production guidance", production["guidance"]), ("2027 indications", forward),
                           ("equity-method context", production["equity_method_context"])):
        for source in section["source_ids"]:
            _dated_source(source, prod_sources, label)
    for site in production["mine_rows"].values():
        for source in site["driver_source_ids"]:
            _dated_source(source, prod_sources, site["name"])
    _dated_source(forward["ngm_operator_outlook"]["source"], prod_sources, "NGM illustrative outlook")
    cost_sources = _sources(costs, as_of)
    for label in ("reported_nine_months", "guidance_2026", "sensitivities", "analyst_assumptions"):
        _dated_source(costs[label]["source"], cost_sources, label)
    assumptions, overrides = _assumptions(production, prices, costs, guided)
    reported = {period: _read_volumes(production, period, checks) for period in ("fy_2025", QUARTER, YTD)}
    totals = {period: _volume_totals(rows) for period, rows in reported.items()}
    q4 = _q4_volumes(production, reported[YTD], checks)
    fy26 = _annual_volumes(reported[YTD], q4)
    fy27 = _forward_volumes(fy26, forward, _value(production["mine_rows"]["ngm"], "ownership"),
                            assumptions["fy_2027.attributable_gold_growth"], assumptions["fy_2027.penasquito_coproduct_change"], checks)
    modeled_prices, actual_sales = _realized_prices(prices, as_of, totals, assumptions, checks)
    pounds = _value(prices, "pounds_per_metric_tonne")
    actual_site_sales, actual_cas, q4_cost, fy26_cost, fy27_cost, cost_base_gold, royalty = _forecast_cas(
        costs, reported[YTD], q4, fy26, fy27, modeled_prices["fy_2027"]["gold"].net_usd, assumptions, checks,
    )
    for metal in METALS:
        _reconcile(checks, f"Nine-month {metal} site revenue to filed price disclosure",
                   fsum(value for (_, key), value in actual_site_sales.items() if key == metal), _value(actual_sales[YTD][metal], "sales_millions"))
    historical = costs["reported_nine_months"]
    corporate_cas = _num(historical["corporate_and_other"], "cas_millions")
    actual_stream = {("penasquito", "silver"): _value(actual_sales[YTD]["silver"], "streaming_millions")}
    q4_sales, q4_stream = _modeled_sales(q4, modeled_prices["q4_2026"], pounds)
    fy27_sales, fy27_stream = _modeled_sales(fy27, modeled_prices["fy_2027"], pounds)
    fy26_sales = {key: actual_site_sales[key] + q4_sales[key] for key in actual_site_sales}
    fy26_stream = {key: actual_stream.get(key, 0) + value for key, value in q4_stream.items()}
    periods = {
        YTD: _period_economics(reported[YTD], actual_site_sales, actual_cas, actual_stream, pounds, corporate_cas),
        "q4_2026": _period_economics(q4, q4_sales, q4_cost, q4_stream, pounds),
        "fy_2026": _period_economics(fy26, fy26_sales, fy26_cost, fy26_stream, pounds, corporate_cas),
        "fy_2027": _period_economics(fy27, fy27_sales, fy27_cost, fy27_stream, pounds),
    }
    _reconcile(checks, "FY2026 reported plus forecast sales", periods[YTD]["sales_millions"] + periods["q4_2026"]["sales_millions"],
               periods["fy_2026"]["sales_millions"])
    _reconcile(checks, "FY2026 reported plus forecast noncash stream revenue",
               periods[YTD]["noncash_stream_amortization_millions"] + periods["q4_2026"]["noncash_stream_amortization_millions"],
               periods["fy_2026"]["noncash_stream_amortization_millions"])
    return {
        "as_of": as_of, "mode": "guided" if guided else "automated", "volume_units": volume_units,
        "price_units": {metal: prices["metals"][metal]["price_unit"] for metal in METALS},
        "currency": "USD", "financial_units": "USD millions",
        "assumptions": assumptions, "override_log": overrides, "modeled_prices": modeled_prices,
        "mine_register": {key: {name: row[name] for name in ("name", "ownership", "accounting", "driver_notes", "driver_source_ids")}
                          for key, row in production["mine_rows"].items()},
        "historical_volumes": totals, "historical_mine_volumes": reported, "historical_realized_sales": actual_sales,
        "periods": periods, "production_guidance": production["guidance"], "forward_production_context": forward,
        "cost_guidance": costs["guidance_2026"], "capital_and_reclamation_context": costs["indications_2027"],
        "historical_costs_and_capital": historical, "equity_method_context": production["equity_method_context"],
        "cost_baseline_gold_price_usd_per_ounce": cost_base_gold, "fy_2027_gold_linked_cas_adjustment_millions": royalty,
        "source_register": {"production": production["sources"], "prices": prices["sources"], "costs": costs["sources"]},
        "checks": checks,
        "limitations": (
            "Q4 site production uses February annual site targets less filed nine-month actuals. The issuer confirmed the company-wide target in October.",
            "Q4 mine shipments use the nine-month sold-to-produced ratios. Gold is scaled to the issuer's 4.9Moz full-year consolidated sales input. Annual shipment ratios carry into 2027 as a proxy. Production less sold is a physical diagnostic. Inventory costs, payable recovery and inventory cash flows require their own schedule.",
            "Q3 copper is reported as both 18kt and 42Mlb. The comparison allows the combined precision of the three individually rounded site disclosures. Historical sales use filed dollars.",
            "2027 uses the Nevada Gold Mines operator midpoint. The remaining modeled gold growth is allocated to Lihir, Boddington and Tanami by 2026 output. The company did not provide updated numerical targets for those sites.",
            "Q4 site CAS is allocated using filed nine-month CAS per sold unit times modeled shipments and scaled to the implied consolidated cost envelope. 2027 escalates full-year site CAS per sold unit and adjusts gold-linked royalties from the embedded price proxy.",
            "Modeled site revenue uses a common price by metal. Site-specific concentrate pricing, treatment and refining terms, contract settlement dates and payability are unavailable.",
            "The forecast keeps minor byproduct credits embedded in CAS. Separately reported copper, silver, lead and zinc are included in sales. The by-product gold CAS measure is a supplemental KPI.",
            "Pueblo Viejo and Fruta del Norte gold enters attributable production. Their mine sales and costs are outside consolidated operations. Net purchases and resales of Pueblo Viejo gold and silver are reported in other income.",
            "Sales less CAS precedes depreciation, overhead, taxes, financing and reclamation. Mine life, consolidated capex, inventory, closure, equity-method cash and financing schedules are still needed for valuation.",
            *prices["notes"], *costs["limitations"],
        ),
    }

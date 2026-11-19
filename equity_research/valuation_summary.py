"""Independent valuation diagnostics and 12-month recommendation."""
from statistics import median

BLEND = .5
BUY = .15
SELL = -.15
DISAGREEMENT = .40
TERMINAL_GAP = .30
GROWTH_MIN, GROWTH_MAX = -.50, 1.


def implied_growth(model):
    base = model["scenarios"]["Base"]
    cost = model["cost"]
    tax = cost.marginal_tax_rate
    wc = model["wc_assets"]-model["wc_liabilities"]
    g, roic, margin, da = (model[k] for k in ("growth","roic","margin","da"))
    end_capex = da+margin*(1-tax)*g/roic-g*wc
    def value(rate):
        previous = model["metrics"]["revenue"]
        pv = 0
        for i, period in enumerate(base.periods):
            revenue = previous*(1+rate)
            capex = model["capex"]+(end_capex-model["capex"])*i/4
            flow = revenue*(margin*(1-tax)+da-capex)-(revenue-previous)*wc
            pv += flow*(model["stub"] if i == 0 else 1)/(1+model["wacc"])**period.discount_years
            previous = revenue
        terminal = previous*margin*(1-tax)*(1+g)*(1-g/roic)/(model["wacc"]-g)
        pv += terminal/(1+model["wacc"])**base.periods[-1].discount_years
        return (pv+base.bridge.adjustment_millions)/base.bridge.diluted_shares_millions
    low, high = GROWTH_MIN, GROWTH_MAX
    close = model["close"]
    if not value(low) <= close <= value(high):
        return None
    for _ in range(36):
        mid = (low+high)/2
        if value(mid) < close:
            low = mid
        else:
            high = mid
    return (low+high)/2


def summarize(model, comps):
    from .operating_valuation import calculate_cost_of_capital
    cost = calculate_cost_of_capital(model["cost"])
    base = model["scenarios"]["Base"]
    peer = comps["value"] if comps else None
    discount = 1/(1+model["wacc"])**base.periods[-1].discount_years
    ebitda = model["revenue"][-1]*(model["margin"]+model["da"])
    multiple = comps["summary"].get("EV / EBITDA") if comps else None
    exit_value = ebitda*multiple["quartiles"][1] if multiple else None
    divergence = abs(exit_value/base.terminal_value_millions-1) if exit_value is not None else None
    blend = BLEND*base.value_per_share+(1-BLEND)*peer if peer is not None else None
    payout = model["payout_ratio"]
    dividends = None if payout is None else sum(
        max(0, (sales*model["margin"]-model["debt"]*model["cost"].pretax_cost_of_debt)
            *(1-model["cost"].marginal_tax_rate))*payout*fraction/model["shares"]
        for sales,fraction in zip(model["revenue"][:2],(model["stub"],1-model["stub"]),strict=True))
    target = blend*(1+cost.cost_of_equity)-dividends if blend is not None and dividends is not None else None
    total = (target+dividends)/model["close"]-1 if target is not None else None
    gap = abs(base.value_per_share/peer-1) if peer and peer > 0 else None
    rating = ("Review" if target is None or gap is None or gap > DISAGREEMENT else
              "Buy" if total >= BUY else "Sell" if total <= SELL else "Hold")
    return {"peer": peer, "blended": blend, "target": target, "dividends": dividends, "return": total,
            "rating": rating, "gap": gap, "exit_value": exit_value, "terminal_gap": divergence,
            "exit_per_share": ((base.enterprise_value_millions-base.terminal_present_value_millions+
                                exit_value*discount+base.bridge.adjustment_millions)/model["shares"]
                               if exit_value is not None else None),
            "implied_cagr": implied_growth(model)}


"""Independent valuation diagnostics and the 12-month recommendation.

The FCFE value at the cost of equity sets the target. FCFF at WACC, the exit
multiple and peer multiples are reported as cross-checks only.
"""

BUY = .15
SELL = -.15
TERMINAL_GAP = .30
GROWTH_MIN, GROWTH_MAX = -.50, 1.


def implied_growth(model):
    """Constant five-year revenue CAGR at which the base FCFE value equals the close."""
    from dataclasses import replace
    from .fcfe_valuation import value_fcfe
    from .operating_research import ResearchGap
    inputs = model["fcfe_inputs"]
    if inputs.leverage is not None:
        # Debt flows are held at the base path, matching the workbook's reverse DCF.
        base = value_fcfe(inputs, model["revenue"], model["margin"], model["cost_of_equity"])
        inputs = replace(inputs, leverage=None, interest=base.interest, post_cutoff_net_borrowing=base.net_borrowing)
    start = inputs.opening_revenue

    def value(rate):
        revenues = [start * (1 + rate) ** (i + 1) for i in range(5)]
        return value_fcfe(inputs, revenues, model["margin"], model["cost_of_equity"]).value_per_share
    low, high = GROWTH_MIN, GROWTH_MAX
    close = model["close"]
    try:
        if not value(low) <= close <= value(high):
            return None
    except ResearchGap:
        return None
    for _ in range(36):
        mid = (low+high)/2
        if value(mid) < close:
            low = mid
        else:
            high = mid
    return (low+high)/2


def dividends_per_share(model, result):
    """Next-12-month dividends from the first two forecast years' net income at the filed payout ratio."""
    payout = model["payout_ratio"] or 0.
    stub = model["stub"]
    return sum(max(0., income) * payout * fraction for income, fraction in
               zip(result.net_income[:2], (stub, 1 - stub), strict=True)) / model["shares"]


def projected_shares(model):
    """Common shares one year out: current shares less the average buyback spend repurchased at the close."""
    return model["shares"] - model.get("buyback_spend", 0.) / model["close"]


def twelve_month_target(model, equity_value, dividends):
    """The FCFE equity value re-dated one year at the cost of equity, less the year's dividends and buybacks,
    divided by the projected share count one year out."""
    remaining = equity_value*(1+model["cost_of_equity"]) - dividends*model["shares"] - model.get("buyback_spend", 0.)
    return remaining / projected_shares(model)


def rate(total):
    return "Buy" if total >= BUY else "Sell" if total <= SELL else "Hold"


def summarize(model, comps):
    base = model["fcfe"]["Base"]
    fcff = model["scenarios"]["Base"]
    peer = comps.get("value") if comps else None
    discount = 1/(1+model["wacc"])**fcff.periods[-1].discount_years
    ebitda = model["revenue"][-1]*(model["margin"]+model["da"])
    multiple = comps["summary"].get("EV / EBITDA") if comps and peer is not None else None
    exit_value = ebitda*multiple["quartiles"][1] if multiple else None
    divergence = abs(exit_value/fcff.terminal_value_millions-1) if exit_value is not None else None
    dividends = dividends_per_share(model, base)
    target = twelve_month_target(model, base.equity_value, dividends)
    total = (target+dividends)/model["close"]-1
    return {"peer": peer, "fcff": fcff.value_per_share, "target": target, "dividends": dividends, "return": total,
            "projected_shares": projected_shares(model), "buyback_spend": model.get("buyback_spend", 0.),
            "rating": rate(total), "fcff_gap": abs(base.value_per_share/fcff.value_per_share-1),
            "peer_gap": abs(base.value_per_share/peer-1) if peer and peer > 0 else None,
            "exit_value": exit_value, "terminal_gap": divergence,
            "exit_per_share": ((fcff.enterprise_value_millions-fcff.terminal_present_value_millions+
                                exit_value*discount+fcff.bridge.adjustment_millions)/model["shares"]
                               if exit_value is not None else None),
            "implied_cagr": implied_growth(model)}

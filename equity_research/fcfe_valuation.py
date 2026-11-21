"""FCFE at the cost of equity, mirroring the linked workbook statements line by line.

The workbook is the deliverable. These functions recompute the same rows so the
renderer can confirm that every recalculated formula matches an independent value.
"""

from dataclasses import dataclass
from math import fsum, isfinite
from statistics import mean

from .operating_research import ResearchGap

GROWTH_STEP = .005
REGRESSION_MIN_MONTHS = 36
MIN_BETA_PEERS = 2
HISTORICAL_WEIGHT = .5
FORWARD_BETA = "Unavailable: no data source"


@dataclass(frozen=True)
class FCFEInputs:
    """Constant inputs shared by every case. Amounts are USD millions."""
    opening_revenue: float
    opening_wc: float
    wc_ratio: float
    tax: float
    da: float
    capex_path: tuple
    interest: tuple
    other_income: float
    closing_debt: float
    debt_cost: float
    post_cutoff_net_borrowing: tuple
    stub: float
    discount_years: tuple
    growth: float
    roic: float
    bridge_adjustment: float
    shares: float


@dataclass(frozen=True)
class FCFEResult:
    revenue: tuple
    net_income: tuple
    post_cutoff_fcfe: tuple
    present_values: tuple
    terminal_fcfe: float
    terminal_value: float
    terminal_present_value: float
    equity_value: float
    value_per_share: float
    cost_of_equity: float
    roic_terminal_fcfe: float
    roic_value_per_share: float


def year_adjustments(inputs):
    """FCFE less FCFF for each post-cutoff year: after-tax other income less interest, plus net borrowing."""
    return tuple((inputs.other_income - inputs.interest[i]) * (1 - inputs.tax) * (inputs.stub if i == 0 else 1)
                 + inputs.post_cutoff_net_borrowing[i] for i in range(5))


def roic_terminal_fcfe(inputs, final_revenue, margin, growth):
    """Check only: next-year FCFE with reinvestment set by incremental ROIC and debt growing at g."""
    nopat = final_revenue * margin * (1 - inputs.tax) * (1 + growth) * (1 - growth / inputs.roic)
    return (nopat - inputs.closing_debt * inputs.debt_cost * (1 - inputs.tax)
            + inputs.other_income * (1 + growth) * (1 - inputs.tax) + growth * inputs.closing_debt)


def value_fcfe(inputs, revenues, margin, cost_of_equity, growth=None):
    """Value one case. `margin` already includes any case shift.

    Terminal value is Gordon growth on the final explicit year's FCFE: FCFE_5 x (1 + g) / (Ke - g).
    The ROIC-based terminal value is computed alongside as a displayed check only.
    """
    growth = inputs.growth if growth is None else growth
    if cost_of_equity <= growth:
        raise ResearchGap("Terminal growth must be below the cost of equity.")
    previous_wc = inputs.opening_wc
    incomes, flows, values = [], [], []
    for i, revenue in enumerate(revenues):
        ebit = revenue * margin
        net_income = (ebit - inputs.interest[i] + inputs.other_income) * (1 - inputs.tax)
        wc = revenue * inputs.wc_ratio
        operating = net_income + revenue * inputs.da - (wc - previous_wc) - revenue * inputs.capex_path[i]
        flow = operating * (inputs.stub if i == 0 else 1) + inputs.post_cutoff_net_borrowing[i]
        incomes.append(net_income)
        flows.append(flow)
        values.append(flow / (1 + cost_of_equity) ** inputs.discount_years[i])
        previous_wc = wc
    discount = (1 + cost_of_equity) ** inputs.discount_years[-1]
    terminal = flows[-1] * (1 + growth)
    terminal_value = terminal / (cost_of_equity - growth)
    terminal_pv = terminal_value / discount
    equity = fsum((*values, terminal_pv, inputs.bridge_adjustment))
    per_share = equity / inputs.shares
    check = roic_terminal_fcfe(inputs, revenues[-1], margin, growth)
    check_value = fsum((*values, check / (cost_of_equity - growth) / discount, inputs.bridge_adjustment)) / inputs.shares
    if not isfinite(per_share):
        raise ResearchGap("The FCFE value per share is not finite.")
    return FCFEResult(tuple(revenues), tuple(incomes), tuple(flows), tuple(values), terminal,
                      terminal_value, terminal_pv, equity, per_share, cost_of_equity, check, check_value)


def case_revenues(base_segments, latest, shift):
    """Add `shift` to every segment's annual growth rate in every forecast year.

    `base_segments` is five dicts of base-case segment revenue; `latest` the filed
    latest fiscal-year segment revenue. Matches the workbook's case revenue block.
    """
    path, previous_case, previous_base = [], None, latest
    for year in base_segments:
        if previous_case is None:
            case = {key: year[key] + latest[key] * shift for key in year}
        else:
            case = {key: previous_case[key] * (year[key] / previous_base[key] + shift) for key in year}
        path.append(sum(case.values()))
        previous_case, previous_base = case, year
    return path


def comparable_beta(peer_betas, subject_debt, subject_equity, subject_tax):
    """Unlever each peer's regression beta at its own D/E and tax rate, take the mean, relever at the subject's."""
    rows, excluded = [], []
    for ticker, item in peer_betas.items():
        if "excluded" in item or item.get("value") is None:
            excluded.append([ticker, item.get("excluded", "Beta unavailable.")])
            continue
        if (item.get("months") or 0) < REGRESSION_MIN_MONTHS:
            excluded.append([ticker, f"Five-year monthly regression has {item.get('months') or 0} returns "
                                     f"({REGRESSION_MIN_MONTHS} required)."])
            continue
        unlevered = item["value"] / (1 + (1 - item["tax_rate"]) * item["debt_to_equity"])
        rows.append({"ticker": ticker, **item, "unlevered": unlevered})
    if len(rows) < MIN_BETA_PEERS:
        return {"value": None, "rows": rows, "excluded": excluded,
                "reason": f"Comparable beta unavailable: {len(rows)} peer betas, {MIN_BETA_PEERS} required."}
    middle = mean(row["unlevered"] for row in rows)
    leverage = subject_debt / subject_equity
    return {"value": middle * (1 + (1 - subject_tax) * leverage), "unlevered_mean": middle,
            "subject_debt_to_equity": leverage, "rows": rows, "excluded": excluded, "reason": None}


def historical_beta(regression):
    """The five-year monthly regression beta when it has enough matched returns, else None."""
    if regression.get("value") is not None and (regression.get("months") or 0) >= REGRESSION_MIN_MONTHS:
        return regression["value"]
    return None


def select_beta(regression, comparable):
    """Weight the historical and comparable betas 50/50; use the one available if the other is missing."""
    historical = historical_beta(regression)
    peer = comparable["value"]
    if historical is not None and peer is not None:
        return HISTORICAL_WEIGHT * historical + (1 - HISTORICAL_WEIGHT) * peer, "50/50 historical and comparable"
    if historical is not None:
        return historical, f"historical only ({comparable['reason'][0].lower() + comparable['reason'][1:]})"
    if peer is not None:
        months = regression.get("months") or 0
        return peer, f"comparable only (historical regression has {months} monthly returns, {REGRESSION_MIN_MONTHS} required)"
    months = regression.get("months") or 0
    raise ResearchGap(f"Beta unavailable: the five-year monthly regression has {months} returns "
                      f"({REGRESSION_MIN_MONTHS} required) and {comparable['reason'][0].lower() + comparable['reason'][1:]}")


def regression_evidence(market):
    """Read the subject's five-year monthly regression; earlier weekly evidence does not qualify."""
    beta = market.get("regression_beta") or market["beta"]
    if "months" not in beta:
        return {"value": None, "months": 0, "unavailable": "Cached evidence predates the five-year monthly regression.",
                "basis": "Five-year monthly regression unavailable", "url": beta.get("url"), "date": beta.get("date")}
    return beta


def other_income_carry(history):
    """Historical median of filed other nonoperating income plus equity-method income, USD millions."""
    values = []
    for period in history[-3:]:
        m = period["metrics"]
        parts = [m.get(key) for key in ("other_nonoperating_income", "equity_method_income")]
        values.append(sum(x for x in parts if type(x) in (int, float) and isfinite(x)) / 1e6)
    from statistics import median
    return median(values) if values else 0.

"""FCFE at the cost of equity, mirroring the linked workbook statements line by line.

The workbook is the deliverable. These functions recompute the same rows so the
renderer can confirm that every recalculated formula matches an independent value.
"""

from dataclasses import dataclass
from math import fsum, isfinite
from statistics import median

from .operating_research import ResearchGap

GROWTH_STEP = .005
REGRESSION_MIN_WEEKS = 90
MIN_BETA_PEERS = 2
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


def year_adjustments(inputs):
    """FCFE less FCFF for each post-cutoff year: after-tax other income less interest, plus net borrowing."""
    return tuple((inputs.other_income - inputs.interest[i]) * (1 - inputs.tax) * (inputs.stub if i == 0 else 1)
                 + inputs.post_cutoff_net_borrowing[i] for i in range(5))


def terminal_fcfe(inputs, final_revenue, margin, growth):
    """Next-year FCFE with debt growing at the terminal rate and reinvestment set by incremental ROIC."""
    nopat = final_revenue * margin * (1 - inputs.tax) * (1 + growth) * (1 - growth / inputs.roic)
    return (nopat - inputs.closing_debt * inputs.debt_cost * (1 - inputs.tax)
            + inputs.other_income * (1 + growth) * (1 - inputs.tax) + growth * inputs.closing_debt)


def value_fcfe(inputs, revenues, margin, cost_of_equity, growth=None):
    """Value one case. `margin` already includes any case shift."""
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
    terminal = terminal_fcfe(inputs, revenues[-1], margin, growth)
    terminal_value = terminal / (cost_of_equity - growth)
    terminal_pv = terminal_value / (1 + cost_of_equity) ** inputs.discount_years[-1]
    equity = fsum((*values, terminal_pv, inputs.bridge_adjustment))
    per_share = equity / inputs.shares
    if not isfinite(per_share):
        raise ResearchGap("The FCFE value per share is not finite.")
    return FCFEResult(tuple(revenues), tuple(incomes), tuple(flows), tuple(values), terminal,
                      terminal_value, terminal_pv, equity, per_share, cost_of_equity)


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
    """Unlever each peer's adjusted beta at its own D/E and tax rate, take the median, relever at the subject's."""
    rows, excluded = [], []
    for ticker, item in peer_betas.items():
        if "excluded" in item or item.get("value") is None:
            excluded.append([ticker, item.get("excluded", "Beta unavailable.")])
            continue
        unlevered = item["value"] / (1 + (1 - item["tax_rate"]) * item["debt_to_equity"])
        rows.append({"ticker": ticker, **item, "unlevered": unlevered})
    if len(rows) < MIN_BETA_PEERS:
        return {"value": None, "rows": rows, "excluded": excluded,
                "reason": f"Comparable beta unavailable: {len(rows)} peer betas, {MIN_BETA_PEERS} required."}
    middle = median(row["unlevered"] for row in rows)
    leverage = subject_debt / subject_equity
    return {"value": middle * (1 + (1 - subject_tax) * leverage), "unlevered_median": middle,
            "subject_debt_to_equity": leverage, "rows": rows, "excluded": excluded, "reason": None}


def select_beta(regression, comparable):
    weeks = regression.get("weeks") or 0
    if regression.get("value") is not None and weeks >= REGRESSION_MIN_WEEKS:
        return regression["value"], "regression"
    if comparable["value"] is not None:
        return comparable["value"], "comparable"
    raise ResearchGap(f"Beta unavailable: the regression has {weeks} weekly returns ({REGRESSION_MIN_WEEKS} required) "
                      f"and {comparable['reason'][0].lower() + comparable['reason'][1:]}")


def regression_evidence(market):
    """Read the subject regression from current or earlier cached market evidence."""
    beta = market.get("regression_beta") or market["beta"]
    if "peers" in beta and "raw" not in beta:
        return {"value": None, "weeks": 0, "unavailable": "Subject regression was unavailable at collection.",
                "basis": "Two-year weekly regression unavailable", "url": beta.get("url"), "date": beta.get("date")}
    return beta


def other_income_carry(history):
    """Historical median of filed other nonoperating income plus equity-method income, USD millions."""
    values = []
    for period in history[-3:]:
        m = period["metrics"]
        parts = [m.get(key) for key in ("other_nonoperating_income", "equity_method_income")]
        values.append(sum(x for x in parts if type(x) in (int, float) and isfinite(x)) / 1e6)
    return median(values) if values else 0.

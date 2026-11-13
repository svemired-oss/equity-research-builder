"""Combine GD operating scenarios, valuation and a dated research recommendation."""

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .gd_comparables import run_gd_comparables
from .gd_forward_statements import GDForwardStatements, run_gd_statement_cases
from .gd_horizon import GDHorizonValuation, load_gd_horizon_inputs, value_gd_horizon
from .gd_inputs import InputSource, _array, _fields, _number, _read, _source, _text
from .gd_run_inputs import DatedClose, _header, _public_url, load_market_inputs
from .gd_valuation import GDValuation, load_gd_valuation_inputs, value_gd


@dataclass(frozen=True)
class RatingPolicy:
    buy_at_or_above: float
    sell_at_or_below: float
    rationale: str

    def rate(self, total_return: float) -> str:
        total_return = _number(total_return, "modeled total return")
        if total_return >= self.buy_at_or_above:
            return "Buy"
        if total_return <= self.sell_at_or_below:
            return "Sell"
        return "Hold"


@dataclass(frozen=True)
class ResearchObservation:
    title: str
    detail: str
    source: InputSource


@dataclass(frozen=True)
class GDResearchInputs:
    as_of: date
    rating_policy: RatingPolicy
    catalysts: tuple[ResearchObservation, ...]
    risks: tuple[ResearchObservation, ...]


@dataclass(frozen=True)
class ScenarioYear:
    year: int
    revenue_millions: float
    operating_margin: float
    gulfstream_deliveries: int
    statement_fcfe_millions: float


@dataclass(frozen=True)
class GDScenarioValuation:
    fy2026_revenue_millions: float
    fy2026_net_earnings_millions: float
    q4_cfo_millions: float
    q4_capex_millions: float
    remaining_2026_statement_fcfe_millions: float
    annual: tuple[ScenarioYear, ...]
    normalized_terminal_fcfe_millions: float
    terminal_share_of_spot_value: float
    intrinsic_value_per_share: float
    price_target: float
    price_return: float
    modeled_dividend_entitlement_per_share: float
    total_return: float


@dataclass(frozen=True)
class GDResearch:
    as_of: date
    horizon: date
    reference_close: DatedClose
    recommendation: str
    cost_of_equity: float
    terminal_growth: float
    scenarios: dict[str, GDScenarioValuation]
    peer_implied_spot_price_to_earnings: float | None
    peer_implied_spot_ev_to_ebit: float | None
    cutoff_cash_millions: float
    cutoff_debt_millions: float
    cutoff_common_shares: int
    inputs: GDResearchInputs
    notes: tuple[str, ...]


def load_gd_research_inputs(path: Path) -> GDResearchInputs:
    """Load the editable rating thresholds and dated research observations."""
    item = _fields(_read(path), "GD research inputs", {
        "schema_version", "ticker", "currency", "as_of", "rating_policy", "catalysts", "risks",
    })
    as_of = _header(item)
    policy = _fields(item["rating_policy"], "rating policy", {"buy_at_or_above", "sell_at_or_below", "rationale"})
    buy = _number(policy["buy_at_or_above"], "buy threshold")
    sell = _number(policy["sell_at_or_below"], "sell threshold")
    if not -1 <= sell < 0 < buy:
        raise ValueError("The sell threshold must be in [-1, 0) and the buy threshold must be positive.")
    rating = RatingPolicy(buy, sell, _text(policy["rationale"], "rating policy rationale"))

    def observations(key: str) -> tuple[ResearchObservation, ...]:
        result = []
        for raw in _array(item[key], key):
            row = _fields(raw, key, {"title", "detail", "source"})
            title = _text(row["title"], f"{key} title")
            source = _source(row["source"], f"{title} source", as_of)
            if not _public_url(source.url) or source.kind not in {"sec_filing", "issuer_release", "issuer_call"}:
                raise ValueError(f"{title} needs a dated, public issuer source.")
            result.append(ResearchObservation(title, _text(row["detail"], f"{title} detail"), source))
        if not result or len({row.title.casefold() for row in result}) != len(result):
            raise ValueError(f"Provide distinct sourced {key}.")
        return tuple(result)

    return GDResearchInputs(as_of, rating, observations("catalysts"), observations("risks"))


def _summarize(statements: GDForwardStatements, spot: GDValuation, target: GDHorizonValuation) -> GDScenarioValuation:
    annual = tuple(ScenarioYear(
        period.period_end.year, period.income["revenue"],
        period.income["operating_earnings"] / period.income["revenue"],
        period.schedules["gulfstream_deliveries"], period.cash_flow.fcfe_millions,
    ) for period in statements.annual)
    quarter = statements.q4_2026.cash_flow
    return GDScenarioValuation(
        statements.fy2026_income["revenue"], statements.fy2026_income["net_earnings"],
        quarter.cash_from_operations, -quarter.investing["cash_capex"], statements.remaining_fcfe.fcfe_millions,
        annual, spot.terminal.fcfe_millions, spot.terminal_share_of_equity_value,
        spot.intrinsic_value_per_share, target.price_target, target.price_return,
        target.projected_paid_dividends_per_share + target.projected_unpaid_dividend_entitlement_per_share,
        target.total_return,
    )


def run_gd_research(as_of: date, inputs_dir: Path) -> GDResearch:
    """Build three linked cases and apply the policy to the base 12-month total return."""
    stamp = as_of.isoformat()
    research = load_gd_research_inputs(inputs_dir / f"gd-research-{stamp}.json")
    if research.as_of != as_of:
        raise ValueError("The research inputs must use the requested cutoff.")
    market = load_market_inputs(inputs_dir / f"gd-market-{stamp}.json")
    valuation_inputs = load_gd_valuation_inputs(inputs_dir / f"gd-valuation-{stamp}.json")
    horizon_inputs = load_gd_horizon_inputs(inputs_dir / f"gd-horizon-{stamp}.json")
    comps = run_gd_comparables(as_of, inputs_dir)
    statements = run_gd_statement_cases(as_of, inputs_dir)
    cutoff = {
        (case.cutoff_cash_millions, case.cutoff_debt_millions, case.cutoff_shares, case.cutoff_dividend_payable_millions)
        for case in statements.values()
    }
    if len(cutoff) != 1:
        raise ValueError("Operating scenarios must use common cash, debt, shares and dividend payable at the cutoff.")
    cash, debt, shares, _ = cutoff.pop()
    spot = {name: value_gd(case, market, valuation_inputs) for name, case in statements.items()}
    targets = {name: value_gd_horizon(case, spot[name], horizon_inputs) for name, case in statements.items()}
    base = targets["base"]
    if any(
        (target.reference_close, target.horizon, target.cost_of_equity, target.terminal_growth)
        != (base.reference_close, base.horizon, base.cost_of_equity, base.terminal_growth)
        for target in targets.values()
    ):
        raise ValueError("Operating scenarios must use a common market observation, horizon and DCF rates.")
    scenarios = {name: _summarize(case, spot[name], targets[name]) for name, case in statements.items()}
    return GDResearch(
        as_of, base.horizon, base.reference_close, research.rating_policy.rate(base.total_return),
        base.cost_of_equity, base.terminal_growth,
        scenarios, comps.implied_price_price_to_earnings, comps.implied_price_ev_to_ebit,
        cash, debt, shares, research, (
            "The rating uses the base-case 12-month modeled total return and the dated analyst thresholds. "
            "Peer medians provide separate trailing spot checks.",
            "The FY2026 cases pair the low, midpoint and high revenue, EPS and capex guidance inputs and use common FCF conversion. "
            "The common modeled elapsed cash and financing establish the same November 9 cutoff for every case.",
            "The cases vary Gulfstream deliveries, pricing and mix, defense revenue growth, and segment margins. "
            "Use the same forward capex ratios, debt policy, tax assumptions, cost of equity and stable growth across cases.",
            "The projected dividends and intra-quarter timing are estimates. The outstanding-option adjustment remains unestimated "
            "because a current option roll-forward was unavailable.",
        ),
    )

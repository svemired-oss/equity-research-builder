"""Project GD's product revenue, segment margins and consolidated operating earnings."""

from dataclasses import dataclass
from datetime import date
from math import fsum, isclose
from pathlib import Path

from .gd_evidence import GDHistoryEvidence
from .gd_inputs import InputSource, _day, _fields, _number, _read, _source
from .gd_interim import GDInterimEvidence, _amounts
from .gd_outlook import SEGMENTS
from .gd_run_inputs import _header
from .gd_segment_history import PRODUCT_GROUPS, PRODUCT_ROWS
from .reconciliation import TOLERANCE_MILLIONS


PRODUCTS = {segment: tuple(map(str, rows)) for segment, rows in zip(SEGMENTS.values(), PRODUCT_ROWS, strict=True)}
PRODUCT_LABELS = {str(row): labels[0] for rows, labels_group in zip(PRODUCT_ROWS, PRODUCT_GROUPS, strict=True)
                  for row, labels in zip(rows, labels_group[:-1], strict=True)}
DEFENSE = tuple(segment for segment in PRODUCTS if segment != "aerospace")
CASES = {"bear": -1, "base": 0, "bull": 1}


@dataclass(frozen=True)
class SegmentEvidence:
    as_of: date
    period_end: date
    products_ytd: dict[str, float]
    prior_products_ytd: dict[str, float]
    q3_segment_revenue: dict[str, float]
    q3_consolidated_revenue: float
    july_revenue: dict[str, float]
    july_margins: dict[str, float]
    july_deliveries: int
    july_corporate_costs: float
    sources: dict[str, InputSource]


@dataclass(frozen=True)
class GrowthPath:
    first: float
    last: float
    case_spread: float


@dataclass(frozen=True)
class SegmentAssumptions:
    growth: dict[str, GrowthPath]
    final_margin: float
    margin_spread: float
    source: InputSource


@dataclass(frozen=True)
class OperatingAssumptions:
    as_of: date
    deliveries: dict[str, tuple[int, ...]]
    delivery_source: InputSource
    segments: dict[str, SegmentAssumptions]
    corporate_cost_growth: float
    corporate_source: InputSource


@dataclass(frozen=True)
class SegmentForecast:
    revenue_millions: float
    operating_earnings_millions: float
    operating_margin: float


@dataclass(frozen=True)
class OperatingPeriod:
    products_millions: dict[str, float]
    deliveries: int
    manufacturing_revenue_per_delivery_millions: float
    segments: dict[str, SegmentForecast]
    corporate_costs_millions: float
    consolidated: SegmentForecast


@dataclass(frozen=True)
class OperatingCase:
    q4: OperatingPeriod
    october_revenue_uplift_millions: dict[str, float]
    margin_adjustment: float
    annual: dict[int, OperatingPeriod]


@dataclass(frozen=True)
class GDOperatingForecast:
    as_of: date
    interim_period_end: date
    product_labels: dict[str, str]
    evidence: SegmentEvidence
    prior_annual_product_sources: dict[str, InputSource]
    assumptions: OperatingAssumptions
    defense_uplift_weights: dict[str, float]
    cases: dict[str, OperatingCase]
    notes: tuple[str, ...]


def _positive_count(raw: object, label: str) -> int:
    value = _number(raw, label)
    if not value.is_integer() or value <= 0:
        raise ValueError(f"{label} must be a positive whole count.")
    return int(value)


def load_gd_segment_evidence(path: Path) -> SegmentEvidence:
    item = _fields(_read(path), "segment evidence", {
        "schema_version", "ticker", "currency", "as_of", "period_end", "products_ytd", "prior_products_ytd",
        "q3_segment_revenue", "q3_consolidated_revenue", "july_guidance", "sources",
    })
    as_of = _header(item)
    period_end = _day(item["period_end"], "product period end")
    current = _amounts(item["products_ytd"], "nine-month products", set(PRODUCT_LABELS))
    prior = _amounts(item["prior_products_ytd"], "prior nine-month products", set(PRODUCT_LABELS))
    q3 = _amounts(item["q3_segment_revenue"], "Q3 segment revenue", set(PRODUCTS))
    q3_total = _number(item["q3_consolidated_revenue"], "Q3 consolidated revenue")
    source_rows = _fields(item["sources"], "segment sources", {"products", "july_guidance"})
    sources = {name: _source(row, name, as_of) for name, row in source_rows.items()}
    filing, july = sources["products"], sources["july_guidance"]
    if (filing.kind != "sec_filing" or july.kind != "issuer_release"
            or not date(as_of.year, 1, 1) <= july.available_on < period_end <= filing.available_on <= as_of):
        raise ValueError("Segment evidence requires dated Q2 guidance and a Q3 filing from the forecast year.")
    outlook = _fields(item["july_guidance"], "July guidance", {"segments", "annual_deliveries", "corporate_costs"})
    segments = _fields(outlook["segments"], "July segments", set(PRODUCTS))
    values = {name: _amounts(row, name, {"revenue", "operating_margin"}) for name, row in segments.items()}
    costs = _number(outlook["corporate_costs"], "July corporate costs")
    if (min((*current.values(), *prior.values(), *q3.values())) <= 0 or costs < 0
            or any(row["revenue"] <= 0 or not 0 < row["operating_margin"] <= 1 for row in values.values())):
        raise ValueError("Segment sales and guided margins must be positive and corporate costs nonnegative.")
    return SegmentEvidence(
        as_of, period_end, current, prior, q3, q3_total, {name: row["revenue"] for name, row in values.items()},
        {name: row["operating_margin"] for name, row in values.items()},
        _positive_count(outlook["annual_deliveries"], "July deliveries"), costs, sources,
    )


def load_gd_operating_assumptions(path: Path) -> OperatingAssumptions:
    item = _fields(_read(path), "operating assumptions", {
        "schema_version", "ticker", "currency", "as_of", "deliveries", "delivery_source", "segments",
        "corporate_cost_growth", "corporate_source",
    })
    as_of = _header(item)

    def assumption_source(raw: object, label: str) -> InputSource:
        source = _source(raw, label, as_of)
        if source.kind != "model_assumption":
            raise ValueError(f"{label} must be labeled as a model assumption.")
        return source

    deliveries = {}
    for name, path_values in _fields(item["deliveries"], "delivery paths", set(CASES)).items():
        if not isinstance(path_values, list) or len(path_values) != 5:
            raise ValueError(f"{name} must supply five years of delivery assumptions.")
        deliveries[name] = tuple(_positive_count(value, f"{name} deliveries") for value in path_values)
    segments = {}
    for name, raw in _fields(item["segments"], "segment assumptions", set(PRODUCTS)).items():
        row = _fields(raw, name, {"growth", "final_margin", "margin_spread", "source"})
        drivers = {"9", "15"} if name == "aerospace" else set(PRODUCTS[name])
        growth = {}
        for driver, values in _fields(row["growth"], f"{name} growth", drivers).items():
            path = GrowthPath(**_amounts(values, f"row {driver}", {"first", "last", "case_spread"}))
            if path.case_spread < 0 or min(path.first, path.last) - path.case_spread <= -1:
                raise ValueError(f"Growth for row {driver} must exceed -100% in every case.")
            growth[driver] = path
        margin = _number(row["final_margin"], f"{name} final margin")
        spread = _number(row["margin_spread"], f"{name} margin spread")
        if spread < 0 or not -1 <= margin - spread <= margin + spread <= 1:
            raise ValueError(f"Invalid operating margin assumptions for {name}.")
        segments[name] = SegmentAssumptions(growth, margin, spread, assumption_source(row["source"], name))
    corporate_growth = _number(item["corporate_cost_growth"], "corporate cost growth")
    if corporate_growth <= -1:
        raise ValueError("Corporate cost growth must exceed -100%.")
    return OperatingAssumptions(
        as_of, deliveries, assumption_source(item["delivery_source"], "delivery source"), segments,
        corporate_growth, assumption_source(item["corporate_source"], "corporate source"),
    )


def _segment_totals(products: dict[str, float]) -> dict[str, float]:
    return {segment: fsum(products[row] for row in rows) for segment, rows in PRODUCTS.items()}


def _period(products: dict[str, float], deliveries: int, earnings: dict[str, float], costs: float) -> OperatingPeriod:
    if min(products.values()) < 0 or deliveries <= 0 or costs < 0:
        raise ValueError("Operating forecast requires nonnegative product revenue and costs, and positive deliveries.")
    revenue = _segment_totals(products)
    if min(revenue.values()) <= 0:
        raise ValueError("Each forecast segment requires positive revenue.")
    segments = {name: SegmentForecast(total, earnings[name], earnings[name] / total) for name, total in revenue.items()}
    total_sales, total_earnings = fsum(revenue.values()), fsum(earnings.values()) - costs
    return OperatingPeriod(
        products, deliveries, products["8"] / deliveries, segments, costs,
        SegmentForecast(total_sales, total_earnings, total_earnings / total_sales),
    )


def _ties(actual: float, expected: float, label: str) -> None:
    if not isclose(actual, expected, rel_tol=0, abs_tol=TOLERANCE_MILLIONS):
        raise ValueError(f"{label} does not reconcile. Calculated {actual:g}, expected {expected:g}.")


def forecast_gd_operations(
    interim: GDInterimEvidence, history: GDHistoryEvidence, evidence: SegmentEvidence, assumptions: OperatingAssumptions,
) -> GDOperatingForecast:
    as_of = interim.as_of
    source = evidence.sources["products"]
    if (not as_of == history.as_of == evidence.as_of == assumptions.as_of
            or evidence.period_end != interim.period_end or source.url != interim.filing.url
            or source.available_on != interim.filing.available_on
            or not history.historical_years or history.historical_years[-1] != as_of.year - 1):
        raise ValueError("Operating inputs must use the same cutoff, Q3 filing and preceding fiscal year.")

    current_sales = _segment_totals(evidence.products_ytd)
    ytd_earnings = {name: interim.segments[name]["operating_earnings"] for name in PRODUCTS}
    _ties(fsum(evidence.q3_segment_revenue.values()), evidence.q3_consolidated_revenue, "Q3 segment sales")
    for name, total in current_sales.items():
        _ties(total, interim.segments[name]["revenue"], f"{name} reported product sales")
        if not 0 < evidence.q3_segment_revenue[name] < total:
            raise ValueError(f"{name} Q3 sales must be within the reported nine-month amount.")
    _ties(fsum(current_sales.values()), interim.ytd["revenue"], "Nine-month revenue")
    _ties(fsum(ytd_earnings.values()) - interim.ytd["corporate_operating_costs"], interim.ytd["operating_earnings"],
          "Nine-month operating earnings")

    try:
        prior_annual = {row: history.history[f"Revenue model!F{row}"] for row in PRODUCT_LABELS}
        prior_sources = {row: history.provenance[f"Revenue model!F{row}"] for row in PRODUCT_LABELS}
        prior_total = history.history["Income statement!F9"]
    except KeyError as exc:
        raise ValueError(f"Missing preceding annual product revenue for {exc.args[0]}.") from exc
    _ties(fsum(prior_annual.values()), prior_total, "Prior fiscal year product sales")
    prior_q4 = {row: sales - evidence.prior_products_ytd[row] for row, sales in prior_annual.items()}
    if min(prior_q4.values()) < 0 or min(_segment_totals(prior_q4).values()) <= 0:
        raise ValueError("Prior-year Q4 product sales require consistent annual and nine-month data.")
    q4_units = evidence.july_deliveries - int(interim.ytd["deliveries"])
    if q4_units <= 0 or interim.ytd["deliveries"] <= 0:
        raise ValueError("July guidance and nine-month deliveries must imply a positive fourth-quarter count.")
    aerospace_q4 = {
        "8": q4_units * evidence.products_ytd["8"] / interim.ytd["deliveries"],
        "9": prior_q4["9"] * evidence.products_ytd["9"] / evidence.prior_products_ytd["9"],
    }
    july_q4 = {name: evidence.july_revenue[name] - current_sales[name] for name in DEFENSE}
    recent_gap = {name: max(0, evidence.q3_segment_revenue[name] - july_q4[name]) for name in DEFENSE}
    total_gap = fsum(recent_gap.values())
    if total_gap == 0:
        raise ValueError("No defense segment's Q3 sales exceed its implied July Q4 outlook.")
    weights = {name: gap / total_gap for name, gap in recent_gap.items()}

    def forecast_case(name: str, sign: int, fy_revenue: float) -> OperatingCase:
        q4_revenue = fy_revenue - interim.ytd["revenue"]
        residual = q4_revenue - fsum(aerospace_q4.values()) - fsum(july_q4.values())
        q4_products = dict(aerospace_q4)
        for segment in DEFENSE:
            rows = PRODUCTS[segment]
            quarterly_sales = july_q4[segment] + weights[segment] * residual
            prior_sales = fsum(prior_q4[row] for row in rows)
            q4_products.update({row: quarterly_sales * prior_q4[row] / prior_sales for row in rows})
        fy_products = {row: value + q4_products[row] for row, value in evidence.products_ytd.items()}
        segment_sales = _segment_totals(fy_products)
        costs = evidence.july_corporate_costs
        target_earnings = fy_revenue * interim.guidance.operating_margin
        july_margin_earnings = fsum(segment_sales[segment] * evidence.july_margins[segment] for segment in PRODUCTS)
        adjustment = (target_earnings + costs - july_margin_earnings) / fy_revenue
        earnings = {segment: sales * (evidence.july_margins[segment] + adjustment) for segment, sales in segment_sales.items()}
        fy = _period(fy_products, evidence.july_deliveries, earnings, costs)
        q4 = _period(q4_products, q4_units, {segment: value - ytd_earnings[segment] for segment, value in earnings.items()},
                     costs - interim.ytd["corporate_operating_costs"])
        _ties(fy.consolidated.revenue_millions, fy_revenue, "FY revenue guidance")
        _ties(fy.consolidated.operating_earnings_millions, target_earnings, "FY operating earnings guidance")
        _ties(q4.consolidated.revenue_millions + interim.ytd["revenue"], fy_revenue, "Quarter plus reported revenue")
        _ties(q4.consolidated.operating_earnings_millions + interim.ytd["operating_earnings"], target_earnings,
              "Quarter plus reported operating earnings")

        annual = {as_of.year: fy}
        margins = {segment: result.operating_margin for segment, result in fy.segments.items()}
        for offset, units in enumerate(assumptions.deliveries[name], start=1):
            prior = annual[as_of.year + offset - 1]
            products = prior.products_millions.copy()
            for settings in assumptions.segments.values():
                for row, growth in settings.growth.items():
                    rate = growth.first + (growth.last - growth.first) * ((offset - 1) / 4) + sign * growth.case_spread
                    if row == "15":
                        products["8"] = units * prior.manufacturing_revenue_per_delivery_millions * (1 + rate)
                    else:
                        products[row] *= 1 + rate
            sales = _segment_totals(products)
            operating_earnings = {}
            for segment, settings in assumptions.segments.items():
                margin = margins[segment] + (settings.final_margin - margins[segment]) * (offset / 5) + sign * settings.margin_spread
                operating_earnings[segment] = sales[segment] * margin
            annual[as_of.year + offset] = _period(
                products, units, operating_earnings, prior.corporate_costs_millions * (1 + assumptions.corporate_cost_growth),
            )
        uplift = {segment: segment_sales[segment] - evidence.july_revenue[segment] for segment in PRODUCTS}
        return OperatingCase(q4, uplift, adjustment, annual)

    low, high = interim.guidance.revenue
    revenue_targets = {"bear": low, "base": (low + high) / 2, "bull": high}
    return GDOperatingForecast(
        as_of, interim.period_end, PRODUCT_LABELS, evidence, prior_sources, assumptions, weights,
        {name: forecast_case(name, sign, revenue_targets[name]) for name, sign in CASES.items()},
        (
            "The Q4 model uses July delivery guidance, nine-month manufacturing revenue per delivery and prior Q4 services scaled by current YTD growth.",
            "The rest of the October revenue uplift goes to defense in proportion to positive gaps between reported Q3 sales and July's implied Q4. "
            "Defense products use prior-year Q4 mix.",
            "A uniform adjustment to July segment margins ties full-year operating earnings to the October consolidated margin. "
            "Corporate costs retain the July estimate.",
            "Forward growth moves linearly between the first and final assumptions. Segment margins move from the modeled FY2026 value to their final assumption. "
            "The bear case subtracts the specified spreads, and the bull case adds them. Each case uses an explicit delivery path.",
            "Manufacturing revenue per delivery is a blended proxy. Forward values are analyst scenarios.",
        ),
    )

"""Roll the dated GD valuation forward by twelve months with a new FCFE stub."""

from dataclasses import dataclass
from datetime import date
from math import fsum, isclose
from pathlib import Path
from statistics import median

from .gd_forward_statements import GDForwardStatements, run_gd_statements
from .gd_inputs import InputSource, _array, _day, _fields, _number, _read, _source
from .gd_run_inputs import DatedClose, _header, _public_url, load_market_inputs
from .gd_valuation import GDValuation, gd_sensitivity_grid, load_gd_valuation_inputs, value_gd
from .reconciliation import TOLERANCE_MILLIONS
from .valuation import FcfeDiscount, FcfeStub, ForecastCashFlow, discount_fcfe_with_stub


@dataclass(frozen=True)
class CashSeasonality:
    year: int
    annual_cfo: float
    q4_cfo: float
    annual_capex: float
    q4_capex: float
    source: InputSource


@dataclass(frozen=True)
class NoteMaturity:
    date: date
    principal_millions: float
    source: InputSource


@dataclass(frozen=True)
class DividendDates:
    record_on: date
    paid_on: date


@dataclass(frozen=True)
class GDHorizonInputs:
    as_of: date
    seasonality: tuple[CashSeasonality, ...]
    maturities: tuple[NoteMaturity, ...]
    dividend_dates: tuple[DividendDates, ...]
    dividend_source: InputSource


@dataclass(frozen=True)
class HorizonDividend:
    dates: DividendDates
    usd_per_share: float
    aggregate_cash_millions: float


@dataclass(frozen=True)
class HorizonCashBridge:
    fiscal_year_end: date
    remaining_days: int
    remaining_share_of_annual_cfo: float
    remaining_share_of_annual_capex: float
    remaining_cash_from_operations_millions: float
    remaining_capex_millions: float
    remaining_note_principal_millions: float
    remaining_lease_principal_millions: float
    remaining_dividends_millions: float
    remaining_buybacks_millions: float
    remaining_statement_fcfe_millions: float
    remaining_stock_compensation_millions: float
    remaining_after_tax_cash_interest_adjustment_millions: float
    remaining_operating_cash_investment_millions: float
    remaining_valuation_fcfe_millions: float
    projected_year_end_cash_millions: float
    projected_horizon_cash_millions: float
    operating_cash_reserve_millions: float
    dividends_payable_to_prior_holders_millions: float
    net_nonoperating_cash_millions: float
    projected_horizon_common_shares: int


@dataclass(frozen=True)
class GDHorizonValuation:
    as_of: date
    horizon: date
    case: str
    reference_close: DatedClose
    current_intrinsic_value_per_share: float
    cost_of_equity: float
    terminal_growth: float
    terminal_fcfe_millions: float
    bridge: HorizonCashBridge
    dividends: tuple[HorizonDividend, ...]
    discount: FcfeDiscount
    price_target: float
    projected_paid_dividends_per_share: float
    projected_unpaid_dividend_entitlement_per_share: float
    price_return: float
    total_return: float
    sensitivity_cost_of_equity: tuple[float, ...]
    sensitivity_terminal_growth: tuple[float, ...]
    sensitivity_value_per_share: tuple[tuple[float | None, ...], ...]
    inputs: GDHorizonInputs
    notes: tuple[str, ...]


def _public_source(raw: object, label: str, as_of: date, kind: str) -> InputSource:
    source = _source(raw, label, as_of)
    if source.kind != kind or not _public_url(source.url):
        raise ValueError(f"{label} needs a dated public {kind} source.")
    return source


def load_gd_horizon_inputs(path: Path) -> GDHorizonInputs:
    """Read the cash timing evidence, contractual note dates and modeled dividend calendar."""
    item = _fields(_read(path), "GD horizon inputs", {
        "schema_version", "ticker", "currency", "as_of", "seasonality", "maturities", "dividend_dates", "dividend_source",
    })
    as_of = _header(item)
    if as_of.month < 10:
        raise ValueError("The fourth-quarter timing model requires a fourth-quarter cutoff.")

    seasonality = []
    amount_keys = ("annual_cfo", "q4_cfo", "annual_capex", "q4_capex")
    for raw in _array(item["seasonality"], "seasonality"):
        row = _fields(raw, "seasonality year", {"year", "source", *amount_keys})
        year = row["year"]
        amounts = tuple(_number(row[key], key) for key in amount_keys)
        source = _public_source(row["source"], "seasonality source", as_of, "issuer_release")
        annual_cfo, q4_cfo, annual_capex, q4_capex = amounts
        if (type(year) is not int or not 1 <= year < as_of.year or source.available_on.year <= year
                or not 0 < q4_cfo <= annual_cfo or not 0 < q4_capex <= annual_capex):
            raise ValueError("Each seasonality year needs positive filed annual and fourth-quarter CFO and capex.")
        seasonality.append(CashSeasonality(year, *amounts, source))
    if sorted(row.year for row in seasonality) != list(range(as_of.year - 3, as_of.year)):
        raise ValueError("Supply the latest three full years of cash flow seasonality.")

    maturities = []
    for raw in _array(item["maturities"], "note maturities"):
        row = _fields(raw, "note maturity", {"date", "principal", "source"})
        day = _day(row["date"], "maturity date")
        principal = _number(row["principal"], "maturity principal")
        if day.year != as_of.year + 1 or principal <= 0:
            raise ValueError("Each maturity must have positive principal and fall in the horizon fiscal year.")
        source = _public_source(row["source"], "note source", as_of, "sec_filing")
        maturities.append(NoteMaturity(day, principal, source))
    if len({note.date for note in maturities}) != len(maturities):
        raise ValueError("Combine maturities falling on the same date.")

    dividends = []
    for raw in _array(item["dividend_dates"], "dividend dates"):
        row = _fields(raw, "dividend dates", {"record_on", "paid_on"})
        record = _day(row["record_on"], "dividend record date")
        paid = _day(row["paid_on"], "dividend payment date")
        if not record < paid or not record.year == paid.year == as_of.year + 1:
            raise ValueError("Each projected record date must precede its payment in the horizon fiscal year.")
        dividends.append(DividendDates(record, paid))
    if [row.paid_on.month for row in dividends] != [2, 5, 8, 11] or any(
        row.record_on.month != row.paid_on.month - 1 for row in dividends
    ):
        raise ValueError("Model February, May, August and November dividends with preceding-month record dates.")
    dividend_source = _public_source(item["dividend_source"], "dividend timing assumption", as_of, "model_assumption")
    return GDHorizonInputs(as_of, tuple(seasonality), tuple(maturities), tuple(dividends), dividend_source)


def _cash_at_horizon(
    statements: GDForwardStatements, valuation: GDValuation, inputs: GDHorizonInputs, horizon: date,
) -> tuple[HorizonCashBridge, tuple[HorizonDividend, ...]]:
    opening = statements.q4_2026.balance_sheet
    year = statements.annual[0]
    year_end = year.period_end
    cash = year.cash_flow
    start_q4 = date(year_end.year, 9, 30)
    if not start_q4 < horizon < year_end:
        raise ValueError("The target date must fall inside the modeled fourth quarter.")
    maturities = sorted((note.date.month, note.principal_millions) for note in inputs.maturities)
    scheduled = sorted((note.maturity.month, note.principal) for note in statements.evidence.notes if note.maturity.year == year_end.year)
    if maturities != scheduled or not isclose(
        fsum(note.principal_millions for note in inputs.maturities), -cash.financing["note_repayments"],
        rel_tol=0, abs_tol=TOLERANCE_MILLIONS,
    ):
        raise ValueError("Exact-date maturities must match Note H and the annual statement repayment.")
    identified_change = cash.fcfe_millions + cash.financing["dividends_paid"] + cash.financing["share_repurchases"]
    if (cash.financing["note_draws"] or cash.other_cash_movements or cash.cash_from_investing != cash.investing["cash_capex"]
            or not isclose(identified_change, cash.net_change_in_cash, rel_tol=0, abs_tol=TOLERANCE_MILLIONS)):
        raise ValueError("The horizon bridge needs explicit timing for note draws and other cash movements.")

    days = (year_end - horizon).days
    year_fraction = days / (year_end - statements.q4_2026.period_end).days
    q4_fraction = days / (year_end - start_q4).days
    cfo_share = median(row.q4_cfo / row.annual_cfo for row in inputs.seasonality) * q4_fraction
    capex_share = median(row.q4_capex / row.annual_capex for row in inputs.seasonality) * q4_fraction
    cfo = cash.cash_from_operations * cfo_share
    capex = -cash.investing["cash_capex"] * capex_share
    notes = fsum(note.principal_millions for note in inputs.maturities if note.date > horizon)
    lease = -cash.financing["lease_principal"] * year_fraction
    raw_fcfe = cfo - capex - notes - lease

    first_dividend = opening.liabilities["dividends_payable"]
    next_dps = year.schedules["dividend_per_share_quarterly"]
    avg_shares_millions = (opening.shares_outstanding + year.balance_sheet.shares_outstanding) / 2_000_000
    first_dates, *later_dates = inputs.dividend_dates
    dividends = (
        HorizonDividend(first_dates, first_dividend * 1_000_000 / opening.shares_outstanding, first_dividend),
        *(HorizonDividend(dates, next_dps, next_dps * avg_shares_millions) for dates in later_dates),
    )
    if not isclose(fsum(dividend.aggregate_cash_millions for dividend in dividends),
                   -cash.financing["dividends_paid"], rel_tol=0, abs_tol=TOLERANCE_MILLIONS):
        raise ValueError("The projected dividend payments must sum to the annual cash flow statement.")
    dividends_remaining = fsum(dividend.aggregate_cash_millions for dividend in dividends if dividend.dates.paid_on > horizon)
    dividends_payable = fsum(dividend.aggregate_cash_millions for dividend in dividends
                             if dividend.dates.record_on <= horizon < dividend.dates.paid_on)

    repurchases = round(year.schedules["program_shares_purchased"] * year_fraction)
    repurchase_cash = repurchases * year.schedules["repurchase_price_per_share"] / 1_000_000
    non_program_shares = round(year.schedules["net_non_program_shares"] * year_fraction)
    shares = year.balance_sheet.shares_outstanding - non_program_shares + repurchases
    closing_cash = year.balance_sheet.assets["cash"]
    horizon_cash = closing_cash - (raw_fcfe - dividends_remaining - repurchase_cash)
    if horizon_cash < 0 or shares <= 0:
        raise ValueError("Projected horizon cash and common shares must be valid.")

    annual_adjustment = valuation.adjustments[1]
    sbc = annual_adjustment.stock_compensation_millions * year_fraction
    cash_interest = annual_adjustment.after_tax_cash_interest_adjustment_millions * year_fraction
    reserve_investment = annual_adjustment.operating_cash_investment_millions * year_fraction
    ending_reserve = year.income["revenue"] * valuation.inputs.operating_cash_share
    horizon_reserve = ending_reserve - reserve_investment
    return HorizonCashBridge(
        year_end, days, cfo_share, capex_share, cfo, capex, notes, lease, dividends_remaining, repurchase_cash,
        raw_fcfe, sbc, cash_interest, reserve_investment, raw_fcfe - sbc - cash_interest - reserve_investment,
        closing_cash, horizon_cash, horizon_reserve, dividends_payable, horizon_cash - horizon_reserve - dividends_payable, shares,
    ), dividends


def value_gd_horizon(
    statements: GDForwardStatements, valuation: GDValuation, inputs: GDHorizonInputs,
) -> GDHorizonValuation:
    """Revalue the selected forecast at its first anniversary and bridge shareholder return."""
    as_of = statements.as_of
    if valuation.as_of != as_of or inputs.as_of != as_of or valuation.case != statements.case:
        raise ValueError("The horizon, current valuation and forecast must share the same cutoff and case.")
    statement_periods = [(statements.remaining_fcfe.period_end, statements.remaining_fcfe.fcfe_millions)]
    statement_periods.extend((period.period_end, period.cash_flow.fcfe_millions) for period in statements.annual)
    if statement_periods != [(row.period_end, row.statement_fcfe_millions) for row in valuation.adjustments]:
        raise ValueError("The horizon and current DCF must use the same statement cash flows.")
    horizon = as_of.replace(year=as_of.year + 1)
    bridge, dividends = _cash_at_horizon(statements, valuation, inputs, horizon)
    stub = FcfeStub(horizon, bridge.fiscal_year_end, bridge.remaining_valuation_fcfe_millions)
    annual = tuple(ForecastCashFlow(row.period_end, row.adjusted_fcfe_millions) for row in valuation.adjustments[2:])
    dated_debt = tuple(ForecastCashFlow(note.date, -note.principal_millions) for note in inputs.maturities if note.date > horizon)
    shares = bridge.projected_horizon_common_shares / 1_000_000

    def discount_at(rate: float, growth: float, terminal: float) -> FcfeDiscount:
        return discount_fcfe_with_stub(
            stub, annual, horizon, rate, growth, shares, terminal_fcfe_millions=terminal, dated_stub_flows=dated_debt,
        )

    def price_at(rate: float, growth: float, terminal: float) -> float:
        pv = discount_at(rate, growth, terminal).equity_present_value_millions
        return (pv + bridge.net_nonoperating_cash_millions) / shares

    ke, g = valuation.cost_of_equity, valuation.inputs.terminal_growth
    terminal_fcfe = valuation.terminal.fcfe_millions
    discount = discount_at(ke, g, terminal_fcfe)
    target = (discount.equity_present_value_millions + bridge.net_nonoperating_cash_millions) / shares
    rates, growth_rates = valuation.sensitivity_cost_of_equity, valuation.sensitivity_terminal_growth
    grid = gd_sensitivity_grid(statements, valuation.inputs, rates, growth_rates, price_at)
    if grid[3][3] != target:
        raise ValueError("The center horizon sensitivity cell must reproduce the price target.")

    entitled = [dividend for dividend in dividends if as_of < dividend.dates.record_on <= horizon]
    paid = fsum(dividend.usd_per_share for dividend in entitled if dividend.dates.paid_on <= horizon)
    unpaid = fsum(dividend.usd_per_share for dividend in entitled if dividend.dates.paid_on > horizon)
    price = valuation.close.price
    return GDHorizonValuation(
        as_of, horizon, valuation.case, valuation.close, valuation.intrinsic_value_per_share, ke, g, terminal_fcfe,
        bridge, dividends, discount, target, paid, unpaid, target / price - 1, (target + paid + unpaid) / price - 1,
        rates, growth_rates, grid, inputs, (
            f"Roll the same {statements.case} operating forecast, cost of equity and terminal assumptions to the 12-month date.",
            "Use the three-year median Q4 fraction of annual CFO and capex separately, then allocate uniformly within calendar Q4. "
            "Operating cash includes coupon cash timing in aggregate. Use the actual maturity date for remaining note principal.",
            "Allocate lease principal, SBC, reserve changes, surplus cash interest and net share movements by calendar days. "
            "Roll back from projected FY2027 closing cash using estimated post-horizon cash movements.",
            "Deduct unpaid dividends for holders of record before the horizon from projected cash in the ex-dividend target. "
            "Projected total return includes paid dividends and the unpaid entitlement for the reference-date buyer. "
            "The November 2026 payment belongs to earlier holders and is outside that return.",
            "The target includes stock compensation as an economic cost and uses projected common shares. "
            "A current roll-forward to value outstanding options separately was unavailable.",
            "The price target is DCF-based. Peer multiples provide separate trailing spot cross-checks.",
        ),
    )


def run_gd_horizon(as_of: date, inputs_dir: Path, *, case: str = "base") -> GDHorizonValuation:
    """Rebuild the selected case and calculate its 12-month target."""
    stamp = as_of.isoformat()
    horizon_inputs = load_gd_horizon_inputs(inputs_dir / f"gd-horizon-{stamp}.json")
    valuation_inputs = load_gd_valuation_inputs(inputs_dir / f"gd-valuation-{stamp}.json")
    market = load_market_inputs(inputs_dir / f"gd-market-{stamp}.json")
    statements = run_gd_statements(as_of, inputs_dir, case=case)
    return value_gd_horizon(statements, value_gd(statements, market, valuation_inputs), horizon_inputs)

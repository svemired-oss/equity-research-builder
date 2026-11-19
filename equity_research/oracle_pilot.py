"""Run the Oracle valuation from dated evidence and explicitly classified assumptions."""

from dataclasses import dataclass, replace
from datetime import date
from math import fsum, isclose, isfinite

from .fcff_valuation import EquityBridge, TerminalAssumptions
from .linked_forecast import ForecastPeriod, PeriodAssumptions, forecast_period
from .operating_valuation import CostOfCapitalInputs, OperatingValuation, value_linked_forecast
from .oracle_model_inputs import OracleOpeningClassifications, classify_oracle_opening_balance
from .oracle_funding import check_existing_lease_cash


@dataclass(frozen=True)
class OracleBridgeClaims:
    """Cutoff economic amounts in USD millions, under the FCFF convention."""

    customer_financing: float
    capital_payables: float
    legacy_tax_and_other_claims: float


@dataclass(frozen=True)
class OraclePilotAssumptions:
    opening_classifications: OracleOpeningClassifications
    elapsed: PeriodAssumptions
    forecast: tuple[PeriodAssumptions, ...]
    cost_of_capital: CostOfCapitalInputs
    equity_bridge: EquityBridge
    bridge_claims: OracleBridgeClaims
    terminal: TerminalAssumptions
    wacc_rates: tuple[float, ...]
    growth_rates: tuple[float, ...]


@dataclass(frozen=True)
class OraclePilotValuation:
    elapsed: ForecastPeriod
    forecast: OperatingValuation


def _match_value(value: float, expected: float, label: str, *, tolerance: float = 1e-6) -> None:
    if not isclose(value, expected, rel_tol=0, abs_tol=tolerance):
        raise ValueError(f"{label} differs from the Oracle evidence by {value - expected:,.6f} million.")


def _check_offering(capital: dict, elapsed: PeriodAssumptions) -> None:
    adjustment = capital["offering_adjustments"]
    if adjustment["status"] != "transactions_matched":
        raise ValueError("Oracle's closed capital transactions must be verified before the cutoff roll-forward.")
    movements = adjustment["known_movements"]
    # Amounts are the contribution of the matched deal. For the initial
    # pilot this input path supports one issue of each security in the window.
    _match_value(elapsed.equity.shares_sold_millions, movements["issued_common_shares"] / 1_000_000,
                 "Common shares issued")
    cash = movements["common_equity_net_proceeds_estimate_millions"]
    if cash is None:
        raise ValueError("The verified offering has no supported net equity proceeds estimate.")
    _match_value(elapsed.equity.stock_issuance_proceeds_millions, cash, "Net common offering proceeds")
    # The statement engine initializes the notes at their net proceeds. Use
    # estimated carrying as the net cash proxy, preserving gross evidence separately.
    _match_value(elapsed.debt.note_proceeds_millions, movements["notes_carrying_increase_estimate_millions"],
                 "Estimated net note issuance")


def _check_bridge(elapsed: ForecastPeriod, assumptions: OraclePilotAssumptions, opening_evidence: dict) -> None:
    bridge = assumptions.equity_bridge
    if bridge.excess_cash_millions > max(0, elapsed.closing.cash_millions) + 1e-6:
        raise ValueError("Cutoff excess cash exceeds modeled unrestricted cash. Report investments separately.")
    _match_value(bridge.diluted_shares_millions, elapsed.income_statement["ending_diluted_shares"],
                 "Cutoff diluted shares")
    claims = assumptions.bridge_claims
    if any(type(value) not in (float, int) or not isfinite(value) or value < 0 for value in vars(claims).values()):
        raise ValueError("Oracle's economic bridge claims must be finite and nonnegative.")
    _match_value(bridge.other_noncommon_claims_millions, fsum(vars(claims).values()), "Other noncommon claims")
    if elapsed.closing.customer_financing_millions > 0 and not claims.customer_financing:
        raise ValueError("Supply an economic value for outstanding customer financing.")
    if elapsed.closing.capital_payables_millions > 0 and not claims.capital_payables:
        raise ValueError("Supply an economic value for accrued capex.")
    if opening_evidence["facts"]["preferred_stock_book_value"]["value_millions"] > 0 and not bridge.preferred_equity_millions:
        raise ValueError("Supply an economic value for Oracle's preferred stock separately from common dilution.")
    if elapsed.closing.noncontrolling_interest_millions > 0 and not bridge.minority_interests_millions:
        raise ValueError("Supply an economic value for Oracle's noncontrolling interests.")
    if (opening_evidence["facts"]["income_taxes_payable_noncurrent"]["value_millions"] > 0
            and not claims.legacy_tax_and_other_claims):
        raise ValueError("Value Oracle's legacy tax payable separately. It is held fixed in the forecast statements.")


def _check_debt_payments(capital: dict, as_of: date, periods: tuple[PeriodAssumptions, ...]) -> None:
    schedule = capital.get("issued_note_payments", {})
    if schedule.get("status") != "available":
        raise ValueError("The matched note issuance needs a verified cash payment schedule.")
    previous = as_of.isoformat()
    for period in periods:
        end = period.period_end.isoformat()
        payments = [row for row in schedule["payments"] if previous < row["payment_date"] <= end]
        if period.debt.cash_interest_millions + 1e-6 < fsum(row["coupon_millions"] for row in payments):
            raise ValueError(f"Forecast cash interest through {end} is below coupons on the new notes alone.")
        if period.debt.note_repayments_millions + 1e-6 < fsum(row["principal_millions"] for row in payments):
            raise ValueError(f"Forecast repayments through {end} are below maturities on the new notes alone.")
        previous = end


def oracle_pilot_readiness(packet: dict) -> dict:
    """Summarize verified inputs and the remaining valuation work for the private pilot."""
    opening, capital = packet["opening_evidence"], packet["capital_bridge"]
    verified = [
        name for name, ready in (
            ("Five-year revenue reference case", packet["revenue_forecast"]["status"] == "reference_case"),
            ("Filing-date balance sheet", opening["status"] == "opening_evidence"),
            ("Filed acquired-intangible amortization", opening["intangible_amortization"]["status"] == "available"),
            ("Filed lease and principal schedules", capital["funding_schedules"]["status"] == "available"),
            ("Closed financing terms", capital["offering_adjustments"]["status"] == "transactions_matched"),
            ("New note payment dates", capital.get("issued_note_payments", {}).get("status") == "available"),
            ("New note pricing benchmark", capital.get("debt_pricing", {}).get("status") == "available"),
            ("Filed common award and preferred terms", capital["dilution_evidence"]["status"] == "available"),
        ) if ready
    ]
    missing = [
        "Classify capex payables, outstanding customer financing and vendor borrowing at the opening date.",
        "Estimate operating and investment movements since the last filing, and reconcile cash, debt and shares at the cutoff.",
        "Build capacity and asset ownership scenarios and link capex, depreciation and lease commencement to forecast cash.",
        "Select and document market-value capital inputs and a steady-state reinvestment rate.",
    ]
    return {"status": "awaiting_forecast_assumptions", "verified": verified, "missing": missing}


def run_oracle_pilot(packet: dict, assumptions: OraclePilotAssumptions) -> OraclePilotValuation:
    """Reconcile the elapsed period separately, then value cash flows after the cutoff."""
    if packet["issuer"]["cik"].lstrip("0") != "1341439":
        raise ValueError("The Oracle pilot requires an Oracle filing evidence packet.")
    as_of = date.fromisoformat(packet["as_of"])
    if assumptions.elapsed.period_end != as_of:
        raise ValueError("The elapsed forecast must end on the valuation cutoff.")
    opening_evidence = packet["opening_evidence"]
    opening = classify_oracle_opening_balance(opening_evidence, assumptions.opening_classifications)
    capital = packet["capital_bridge"]
    _check_offering(capital, assumptions.elapsed)
    exact_shares = capital["offering_terms"]["equity"]["balance_date_basic_shares"]
    opening = replace(opening, basic_shares_millions=exact_shares / 1_000_000)
    elapsed = forecast_period(opening, assumptions.elapsed)
    if elapsed.funding_gap_millions:
        raise ValueError(f"Cutoff cash is short by {elapsed.funding_gap_millions:,.2f} million on {as_of}.")
    _check_bridge(elapsed, assumptions, opening_evidence)

    revenue = packet["revenue_forecast"]
    interim = packet["revenue_basis"]["interim"]
    if (revenue["status"] != "reference_case" or not interim
            or len(assumptions.forecast) != 5 or len(revenue["years"]) != 5):
        raise ValueError("The five forecast periods need a validated Oracle revenue reference case.")
    reported_ytd = interim["consolidated_revenue_usd"] / 1_000_000
    for index, (year, period) in enumerate(zip(revenue["years"], assumptions.forecast, strict=True)):
        if period.period_end.isoformat() != year["period_end"]:
            raise ValueError("Forecast dates must match the Oracle fiscal revenue periods.")
        elapsed_revenue = reported_ytd + assumptions.elapsed.revenue_millions if index == 0 else 0
        _match_value(period.revenue_millions + elapsed_revenue, year["consolidated_millions"], "Forecast fiscal revenue")

    if any(period.intangible_purchases_millions for period in (assumptions.elapsed, *assumptions.forecast)):
        raise ValueError("The Oracle pilot uses the published acquired intangible schedule and needs a separate schedule for new purchases.")
    amortization = {row["fiscal_year"]: row["amount_millions"]
                    for row in opening_evidence["intangible_amortization"]["schedule"]}
    for index, period in enumerate(assumptions.forecast):
        year = period.period_end.year
        if year not in amortization:
            raise ValueError(f"FY{year} is missing from the Oracle amortization schedule.")
        elapsed_amortization = assumptions.elapsed.intangible_amortization_millions if index == 0 else 0
        _match_value(period.intangible_amortization_millions + elapsed_amortization, amortization[year],
                     "Forecast amortization of existing intangibles")

    _check_debt_payments(capital, as_of, assumptions.forecast)
    leases = capital.get("funding_schedules", {}).get("leases")
    if leases:
        if leases["period_end"] != opening_evidence["opening_date"]:
            raise ValueError("The lease payment schedule must start at the reported opening balance date.")
        check_existing_lease_cash(leases, assumptions.elapsed, assumptions.forecast)
    result = value_linked_forecast(
        elapsed.closing, assumptions.forecast, assumptions.cost_of_capital, assumptions.equity_bridge,
        assumptions.terminal, wacc_rates=assumptions.wacc_rates, growth_rates=assumptions.growth_rates,
    )
    if result.funding_gap_periods:
        period = next(period for period in result.periods if period.funding_gap_millions)
        raise ValueError(f"Forecast cash is short by {period.funding_gap_millions:,.2f} million on {period.closing.period_end}.")
    return OraclePilotValuation(elapsed, result)

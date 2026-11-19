"""Dated Oracle analyst scenario for validating the private review workflow."""

from dataclasses import replace
from datetime import date
from math import fsum

from .fcff_valuation import EquityBridge, TerminalAssumptions
from .forecast_schedules import PPEMovements
from .linked_forecast import (
    CapexFunding, CustomerFinancingFlows, DebtFlows, EquityFlows, OperatingLeaseFlows,
    PeriodAssumptions, forecast_period,
)
from .operating_valuation import CostOfCapitalInputs, calculate_cost_of_capital
from .oracle_model_inputs import OracleOpeningClassifications, classify_oracle_opening_balance
from .oracle_pilot import OracleBridgeClaims, OraclePilotAssumptions, run_oracle_pilot


def build_review_case(packet, *, common_close, risk_free, beta, equity_risk_premium, margin_shift=0):
    """Return an explicitly illustrative scenario conditional on the documented capital mix."""
    cutoff = date.fromisoformat(packet["as_of"])
    opening = packet["opening_evidence"]
    if not date(2026, 11, 13) <= cutoff <= date(2026, 11, 30) or opening["opening_date"] != "2026-08-31":
        raise ValueError("Oracle needs a new forecast calibration outside the November 13–30 window or after a new balance sheet.")
    capital = packet["capital_bridge"]
    terms = capital["offering_terms"]
    deal = capital["offering_adjustments"]["known_movements"]
    facts = opening["facts"]
    metrics = packet["interim_actuals"]["periods"][-1]["metrics"]
    dilution = capital["dilution_evidence"]
    preferred = dilution["preferred"]
    exact_shares = terms["equity"]["balance_date_basic_shares"] / 1e6
    # The quarter's $11.363bn receipt is a proxy for the opening financing
    # liability. It does not establish cumulative unpaid customer advances.
    split = OracleOpeningClassifications(6247, 11363, 0, 0, 4594, 0)
    balance = replace(classify_oracle_opening_balance(opening, split), basic_shares_millions=exact_shares)
    annual = packet["revenue_forecast"]["years"]
    elapsed_days = (cutoff - balance.period_end).days
    quarter_days = (date(2026, 11, 30) - balance.period_end).days
    q2 = next(row for row in packet["quarter_revenue_guidance"]["observations"]
              if row["metric"] == "consolidated_revenue")
    elapsed_revenue = (q2["lower_revenue_usd"] + q2["upper_revenue_usd"]) / 2e6 * elapsed_days / quarter_days
    fiscal_stub_days = (date(2027, 5, 31) - balance.period_end).days
    fraction = elapsed_days / fiscal_stub_days
    lease_rows = {row["fiscal_year"]: row for row in capital["funding_schedules"]["leases"]["periods"]}
    maturities = {row["fiscal_year"]: row["principal_millions"]
                  for row in capital["funding_schedules"]["annual_debt_principal"]["periods"]}
    amort = {row["fiscal_year"]: row["amount_millions"] for row in opening["intangible_amortization"]["schedule"]}
    capex = packet["capacity_scenarios"]["cases"]["half_customer_hardware_vendor_funded"]["fiscal_years"]
    # A separate analyst estimate for future rent recognizes the disclosed
    # uncommenced lease program. Timing and initial recognition are scenarios.
    future_rent = {2027: 1000, 2028: 4500, 2029: 11000, 2030: 16500, 2031: 16500}
    new_lease_pv = {2027: 20000, 2028: 60000, 2029: 70000, 2030: 0, 2031: 0}
    awards_millions = dilution["restricted_stock_units"]["underlying_common_shares"] / 1e6
    options = dilution["options"]
    option_millions = options["underlying_common_shares"] / 1e6 * max(
        0, 1 - options["weighted_average_strike_usd"] / common_close,
    )
    dividends = fsum(
        event["per_share_usd"] * (exact_shares if event["security"] == "common"
                                  else preferred["preferred_shares_outstanding"] / 1e6)
        for event in capital["dividends"]["events"] if event["payment_date"] <= cutoff.isoformat()
    )

    def period(end, revenue, *, elapsed=False):
        year = 2027 if elapsed else end.year
        scale = fraction if elapsed else 1 - fraction if year == 2027 else 1
        years = (end - balance.period_end).days / 365
        existing_rent = lease_rows[year]["operating_lease_payments_millions"] * scale
        rent = existing_rent + future_rent[year] * scale
        # Estimate economic additions separately from vendor-financed additions.
        # From FY29, capital spending maintains and refreshes deployed equipment.
        additional = capex[year]["economic_additions_millions"]
        vendor = capex[year]["vendor_financed_additions_millions"] * scale
        capex_maintenance = balance.ppe.gross_in_service_millions * (0.075 if year <= 2028 else 0.14) * years
        depreciation = min(balance.ppe.net_millions, balance.ppe.gross_in_service_millions / 7 * years)
        commission = (balance.ppe.construction_in_progress_millions + additional * scale) * (
            0.35 if year <= 2028 else 0.8
        )
        if not elapsed and year >= 2029:
            commission = balance.ppe.construction_in_progress_millions
        active_margin = {2027: 0.52, 2028: 0.52, 2029: 0.57, 2030: 0.58, 2031: 0.57}[year]
        if not elapsed:
            active_margin += margin_shift
        cash_interest = (balance.notes_millions * 0.052 + balance.finance_leases_millions * 0.055
                         + balance.vendor_debt_millions * 0.08) * years
        principal = max(0, maturities[year] - (4202 if year == 2027 else 0)) * scale
        if year == 2030:
            principal += 2000
        finance_paid = max(0, lease_rows[year]["finance_lease_payments_millions"] * scale
                           - balance.finance_leases_millions * 0.055 * years)
        dividends_paid = dividends if elapsed else (2 * balance.basic_shares_millions + 325) * years
        vesting = 3.0 * years * 4
        return PeriodAssumptions(
            end, revenue, revenue * active_margin, 0.19,
            balance.working_capital,
            PPEMovements(additional * scale - vendor, vendor, capex_maintenance, 0, commission,
                         depreciation, 0, 0),
            0, amort[year] * scale,
            CapexFunding(vendor, 0, 0, min(balance.capital_payables_millions, 6247 * years), 0),
            OperatingLeaseFlows(new_lease_pv[year] * scale, rent, rent,
                                min(rent, balance.operating_lease_liabilities_millions * 0.05 * years)),
            DebtFlows(deal["notes_carrying_increase_estimate_millions"] if elapsed else principal,
                      principal, 0, min(balance.vendor_debt_millions, balance.vendor_debt_millions * 0.20 * years),
                      finance_paid, cash_interest),
            CustomerFinancingFlows(
                11363 * scale if year <= 2028 else 0, 0, 0,
                min(balance.customer_financing_millions, balance.customer_financing_millions * years / 4),
            ),
            EquityFlows(
                revenue * metrics["stock_based_compensation"] / metrics["revenue"], dividends_paid,
                325 * years, dividends_paid,
                deal["common_equity_net_proceeds_estimate_millions"] if elapsed else 0, 0,
                deal["issued_common_shares"] / 1e6 if elapsed else 0, vesting, 0,
                max(0, awards_millions - vesting) + option_millions, 53 * years * 4, 53 * years * 4,
            ),
        )

    elapsed = period(cutoff, elapsed_revenue, elapsed=True)
    balance = forecast_period(balance, elapsed).closing
    closing = balance
    forecasts = []
    for index, row in enumerate(annual):
        remaining = (metrics["revenue"] / 1e6 + elapsed_revenue) if index == 0 else 0
        estimated = period(date.fromisoformat(row["period_end"]), row["consolidated_millions"] - remaining)
        forecasts.append(estimated)
        balance = forecast_period(balance, estimated).closing

    # Market debt proxy uses the August Level 2 mark, adds the new notes and
    # adjusts for modeled principal activity. Preferred uses liquidation.
    debt_value = 105700 + deal["notes_carrying_increase_estimate_millions"] - elapsed.debt.note_repayments_millions
    debt_value += closing.finance_leases_millions + closing.vendor_debt_millions
    preferred_value = preferred["aggregate_liquidation_preference_usd"] / 1e6
    cost = CostOfCapitalInputs(cutoff, risk_free, equity_risk_premium, beta,
                               closing.basic_shares_millions * common_close,
                               capital["debt_pricing"]["benchmark"]["issue_ytm_rate"], debt_value, 0.19,
                               preferred["annual_dividend_rate"], preferred_value)
    claims = OracleBridgeClaims(closing.customer_financing_millions, closing.capital_payables_millions, 12060)
    bridge = EquityBridge(cutoff, max(0, closing.cash_millions - 10000), facts["available_for_sale_debt_securities_current"]["value_millions"],
                          debt_value, preferred_value, closing.noncontrolling_interest_millions, fsum(vars(claims).values()),
                          closing.basic_shares_millions + elapsed.equity.dilutive_shares_millions)
    terminal = TerminalAssumptions(
        (forecasts[-1].ebitda_after_sbc_and_operating_rent_millions - forecasts[-1].property.depreciation_millions
         - forecasts[-1].intangible_amortization_millions) * 0.81, 0.025, 0.12,
    )
    wacc = calculate_cost_of_capital(cost).wacc
    rates = tuple(wacc + step * 0.005 for step in range(-2, 3))
    growth = tuple(terminal.growth_rate + step * 0.005 for step in range(-2, 3))
    assumptions = OraclePilotAssumptions(split, elapsed, tuple(forecasts), cost, bridge, claims, terminal, rates, growth)
    return assumptions, run_oracle_pilot(packet, assumptions)

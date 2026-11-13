"""Roll forward Newmont's capital, depreciation and closure schedules in USD millions."""

from dataclasses import dataclass
from datetime import date
from math import fsum
from pathlib import Path

from .fdx_current import _dated_source, _num, _reconcile, _sources
from .gd_inputs import _day
from .nem_current import YTD, _evidence, _value, _weights


@dataclass(frozen=True)
class NEMCapitalPeriod:
    opening_ppem_millions: float
    closing_ppem_millions: float
    opening_accrued_capex_millions: float
    closing_accrued_capex_millions: float
    sustaining_cash_capex_millions: float
    development_cash_capex_millions: float
    cash_capex_millions: float
    accrual_capex_millions: float
    capitalized_interest_millions: float
    unlevered_cash_capex_millions: float
    finance_lease_additions_millions: float
    depreciation_millions: float
    depreciation_by_site_and_metal: dict[str, dict[str, float]]
    corporate_depreciation_millions: float
    opening_closure_liability_millions: float
    closing_closure_liability_millions: float
    closing_current_closure_liability_millions: float
    closing_noncurrent_closure_liability_millions: float
    closure_cash_millions: float
    closure_expense_millions: float


def _historical_capital(annual, september, historical, checks):
    consolidated = historical["consolidated"]
    opening_ppem = _value(annual, "net_ppem_millions")
    closing_ppem = _value(september, "net_ppem_millions")
    _reconcile(checks, "FY2025 net PP&E", _value(annual, "gross_ppem_millions")
               - _value(annual, "accumulated_depreciation_millions"), opening_ppem)
    for label, data, sustaining, development, accrual, cash in (
        ("FY2025", annual, "sustaining_capex_accrual_millions", "development_capex_accrual_millions",
         "total_capex_accrual_millions", "cash_capex_millions"),
        ("9M2026", consolidated, "sustaining_capital_accrual_millions", "development_capital_accrual_millions",
         "capex_accrual_millions", "capex_cash_millions"),
    ):
        _reconcile(checks, f"{label} capital additions", _value(data, sustaining) + _value(data, development),
                   _value(data, accrual))
        _reconcile(checks, f"{label} cash capex", _value(data, accrual) + _num(data, "capex_cash_reconciliation_millions"),
                   _value(data, cash))
    for capital_type in ("sustaining", "development"):
        key = f"{capital_type}_capital_accrual_millions"
        site_total = fsum(_value(row, key) for row in historical["sites"].values())
        _reconcile(checks, f"9M2026 {capital_type} capex by site and corporate", site_total
                   + _value(historical["corporate_and_other"], key), _value(consolidated, key))

    closure = september["closure"]
    for category in ("reclamation", "remediation"):
        row = closure[category]
        _reconcile(checks, f"9M2026 {category} liability roll-forward", _value(annual, f"{category}_liability_millions")
                   + _num(row, "additions_estimate_changes_other_millions") + _num(row, "divestitures_millions")
                   - _value(row, "payments_millions") + _value(row, "accretion_millions"), _value(row, "total_millions"))
        _reconcile(checks, f"9M2026 {category} liability classification", _value(row, "current_millions")
                   + _value(row, "noncurrent_millions"), _value(row, "total_millions"))
    for tenor in ("current", "noncurrent"):
        _reconcile(checks, f"9M2026 {tenor} closure balance", fsum(_value(row, f"{tenor}_millions") for row in closure.values()),
                   _value(september, f"{tenor}_closure_liability_millions"))
    closure_cash = fsum(_value(row, "payments_millions") for row in closure.values())
    closure_accretion = fsum(_value(row, "accretion_millions") for row in closure.values())
    closure_expense = closure_accretion + fsum(_value(row, "expense_changes_and_other_millions") for row in closure.values())
    _reconcile(checks, "9M2026 closure payments", closure_cash, _value(consolidated, "reclamation_cash_millions"))
    _reconcile(checks, "9M2026 closure expense to Note 6", closure_expense, _value(september, "closure_expense_millions"))
    _reconcile(checks, "9M2026 closure expense to the income statement", closure_expense,
               _value(consolidated, "reclamation_and_remediation_expense_millions"))

    attributable_sustaining = _value(consolidated, "attributable_sustaining_capital_millions")
    attributable_development = _value(consolidated, "attributable_development_capital_millions")
    attributable_capex = attributable_sustaining + attributable_development
    if attributable_capex == 0:
        raise ValueError("Newmont cash capex basis requires positive attributable capex.")
    cash_capex = _value(consolidated, "capex_cash_millions")
    accrual_capex = _value(consolidated, "capex_accrual_millions")
    depreciation = _value(consolidated, "depreciation_millions")
    opening_accrued_capex = _value(annual, "accrued_capex_millions")
    closing_accrued_capex = _value(september, "accrued_capex_millions")
    return {
        "opening_ppem_millions": opening_ppem,
        "closing_ppem_millions": closing_ppem,
        "opening_accrued_capex_millions": opening_accrued_capex,
        "closing_accrued_capex_millions": closing_accrued_capex,
        "other_accrual_to_cash_capex_adjustment_millions": cash_capex - accrual_capex + closing_accrued_capex - opening_accrued_capex,
        "net_ppem_other_movement_millions": closing_ppem - opening_ppem - accrual_capex + depreciation,
        "accrual_capex_millions": accrual_capex,
        "cash_capex_millions": cash_capex,
        "attributable_sustaining_capex_millions": attributable_sustaining,
        "attributable_development_capex_millions": attributable_development,
        "attributable_capex_millions": attributable_capex,
        "consolidated_cash_to_attributable_capex_ratio": cash_capex / attributable_capex,
        "depreciation_millions": depreciation,
        "closure_cash_millions": closure_cash,
        "closure_accretion_millions": closure_accretion,
        "closure_expense_millions": closure_expense,
        "noncash_closure_cfo_adjustment_millions": _value(september, "noncash_closure_cfo_adjustment_millions"),
        "closing_closure_liability_millions": fsum(_value(row, "total_millions") for row in closure.values()),
        "closing_current_closure_liability_millions": _value(september, "current_closure_liability_millions"),
        "closing_noncurrent_closure_liability_millions": _value(september, "noncurrent_closure_liability_millions"),
        "yanacocha_water_treatment_cash_millions": _value(consolidated, "yanacocha_water_treatment_cash_millions"),
    }


def _forecast_depreciation(current, annual_guidance, history, checks):
    reported = current["historical_costs_and_capital"]
    periods = current["periods"]
    actual = {(site, metal): _value(row, "depreciation_millions")
              for site, lines in reported["sites"].items() for metal, row in lines["metals"].items()}
    actual_corporate = _value(reported["corporate_and_other"], "depreciation_millions")
    _reconcile(checks, "9M2026 site and corporate D&A", fsum(actual.values()) + actual_corporate,
               history["depreciation_millions"])
    q4_corporate = actual_corporate / 3
    q4_total = _value(annual_guidance, "depreciation_millions") - history["depreciation_millions"]
    if q4_total < q4_corporate:
        raise ValueError("Newmont Q4 residual D&A does not support the corporate run rate.")
    sold = {period: {(site, metal): periods[period]["mines"][site]["volumes"][metal].sold for site, metal in actual}
            for period in (YTD, "q4_2026", "fy_2026", "fy_2027")}
    for key in actual:
        if sold[YTD][key] <= 0 or sold["fy_2026"][key] <= 0:
            raise ValueError(f"Missing Newmont depreciation allocation volume for {key[0]} {key[1]}.")
    q4 = _weights({key: expense * sold["q4_2026"][key] / sold[YTD][key] for key, expense in actual.items()},
                  q4_total - q4_corporate, "Q4 site depreciation")
    _reconcile(checks, "FY2026 consolidated D&A guidance", fsum(q4.values()) + q4_corporate
               + history["depreciation_millions"], _value(annual_guidance, "depreciation_millions"))
    fy27 = {key: (expense + q4[key]) * sold["fy_2027"][key] / sold["fy_2026"][key] for key, expense in actual.items()}
    return q4, q4_corporate, fy27, actual_corporate + q4_corporate


def _roll_forward(label, checks, *, opening_ppem, opening_closure, opening_accrued_capex, sustaining_capex,
                  development_capex, capitalized_interest, lease_additions, site_depreciation,
                  corporate_depreciation, closure_cash, closure_expense, closing_current_closure):
    cash_capex = sustaining_capex + development_capex
    accrual_capex = cash_capex
    depreciation = fsum(site_depreciation.values()) + corporate_depreciation
    site_rows = {}
    for (site, metal), expense in site_depreciation.items():
        site_rows.setdefault(site, {})[metal] = expense
    closing_closure = opening_closure + closure_expense - closure_cash
    closing_ppem = opening_ppem + accrual_capex + lease_additions - depreciation
    if min(cash_capex - capitalized_interest, closing_closure - closing_current_closure, closing_ppem) < 0:
        raise ValueError(f"Inconsistent Newmont {label} capital or closure forecast.")
    _reconcile(checks, f"{label} PP&E investment and D&A", closing_ppem - opening_ppem + depreciation,
               accrual_capex + lease_additions)
    _reconcile(checks, f"{label} closure liability and payments", closing_closure - opening_closure + closure_cash,
               closure_expense)
    return NEMCapitalPeriod(
        opening_ppem_millions=opening_ppem,
        closing_ppem_millions=closing_ppem,
        opening_accrued_capex_millions=opening_accrued_capex,
        closing_accrued_capex_millions=opening_accrued_capex,
        sustaining_cash_capex_millions=sustaining_capex,
        development_cash_capex_millions=development_capex,
        cash_capex_millions=cash_capex,
        accrual_capex_millions=accrual_capex,
        capitalized_interest_millions=capitalized_interest,
        unlevered_cash_capex_millions=cash_capex - capitalized_interest,
        finance_lease_additions_millions=lease_additions,
        depreciation_millions=depreciation,
        depreciation_by_site_and_metal=site_rows,
        corporate_depreciation_millions=corporate_depreciation,
        opening_closure_liability_millions=opening_closure,
        closing_closure_liability_millions=closing_closure,
        closing_current_closure_liability_millions=closing_current_closure,
        closing_noncurrent_closure_liability_millions=closing_closure - closing_current_closure,
        closure_cash_millions=closure_cash,
        closure_expense_millions=closure_expense,
    )


def build_nem_capital_schedule(as_of: date, inputs_dir: Path, current: dict) -> dict:
    """Link reported capital and closure data to the operating forecast and management outlook."""
    if current["as_of"] != as_of:
        raise ValueError("Newmont capital evidence and operating forecast must use the same cutoff.")
    packet = _evidence(inputs_dir, "capital", as_of)
    dates = _sources(packet, as_of)
    annual, september = packet["fy_2025"], packet["september_2026"]
    for label, row, expected in (("FY2025", annual, date(2025, 12, 31)),
                                 ("September 2026", september, date(2026, 9, 30))):
        if _day(row["period_end"], f"Newmont {label} period end") != expected:
            raise ValueError(f"Unexpected Newmont capital period for {label}.")
        _dated_source(row["source"], dates, label, observed_on=expected)
    _dated_source(annual["accrued_capex_source"], dates, "Newmont FY2025 comparative accrued capex",
                  observed_on=date(2025, 12, 31))
    assumptions = packet["assumptions"]
    for label in ("assumptions", "project_context"):
        for source in packet[label]["source_ids"]:
            _dated_source(source, dates, f"Newmont capital {label}")

    checks = []
    history = _historical_capital(annual, september, current["historical_costs_and_capital"], checks)
    fy26_guidance = current["cost_guidance"]["full_year"]
    q4_guidance = current["cost_guidance"]["fourth_quarter"]
    fy27_context = current["capital_and_reclamation_context"]
    q4_site_da, q4_corporate_da, fy27_site_da, fy27_corporate_da = _forecast_depreciation(current, fy26_guidance, history, checks)
    basis = history["consolidated_cash_to_attributable_capex_ratio"]
    q4_attributable_sustaining = _value(q4_guidance, "attributable_sustaining_capital_millions")
    q4_attributable_development = _value(q4_guidance, "attributable_development_capital_millions")
    fy26_sustaining = _value(fy26_guidance, "attributable_sustaining_capital_millions")
    fy26_development = _value(fy26_guidance, "attributable_development_capital_millions")
    fy27_attributable_development = fy26_development + _value(fy27_context, "indicative_development_capital_increase_millions")
    fy26_capitalized_interest = _value(fy26_guidance, "capitalized_interest_millions")
    q4_year_share = (date(2027, 1, 1) - date(2026, 10, 1)).days / (date(2027, 1, 1) - date(2026, 1, 1)).days
    fy26_closure_cash = _value(fy26_guidance, "reclamation_cash_millions")
    annual_closure_cash_decline = _value(fy27_context, "indicative_annual_reclamation_cash_reduction_millions")
    fy27_closure_cash = fy26_closure_cash - annual_closure_cash_decline
    fy28_closure_cash = fy27_closure_cash - annual_closure_cash_decline
    fy26_accretion = _value(fy26_guidance, "reclamation_and_remediation_accretion_millions")
    q4_closure_cash = fy26_closure_cash - history["closure_cash_millions"]
    q4_yanacocha_cash = _value(fy26_guidance, "yanacocha_water_treatment_cash_millions") - history["yanacocha_water_treatment_cash_millions"]
    q4_accretion = fy26_accretion - history["closure_accretion_millions"]
    if not 0 <= q4_yanacocha_cash <= q4_closure_cash or min(fy27_closure_cash, fy28_closure_cash, q4_accretion) < 0:
        raise ValueError("Newmont closure outlook conflicts with reported spending or accretion.")

    q4 = _roll_forward(
        "Q4 2026", checks, opening_ppem=history["closing_ppem_millions"],
        opening_closure=history["closing_closure_liability_millions"], opening_accrued_capex=history["closing_accrued_capex_millions"],
        sustaining_capex=q4_attributable_sustaining * basis, development_capex=q4_attributable_development * basis,
        capitalized_interest=fy26_capitalized_interest * q4_year_share,
        lease_additions=_value(assumptions, "q4_2026_finance_lease_additions_millions"), site_depreciation=q4_site_da,
        corporate_depreciation=q4_corporate_da, closure_cash=q4_closure_cash,
        closure_expense=q4_accretion, closing_current_closure=fy27_closure_cash,
    )
    fy27 = _roll_forward(
        "FY2027", checks, opening_ppem=q4.closing_ppem_millions, opening_closure=q4.closing_closure_liability_millions,
        opening_accrued_capex=q4.closing_accrued_capex_millions, sustaining_capex=fy26_sustaining * basis,
        development_capex=fy27_attributable_development * basis,
        capitalized_interest=fy26_capitalized_interest * (1 + _num(assumptions, "fy_2027_capitalized_interest_growth", minimum=-1)),
        lease_additions=_value(assumptions, "fy_2027_finance_lease_additions_millions"), site_depreciation=fy27_site_da,
        corporate_depreciation=fy27_corporate_da, closure_cash=fy27_closure_cash,
        closure_expense=fy26_accretion * (1 + _num(assumptions, "fy_2027_accretion_growth", minimum=-1)),
        closing_current_closure=fy28_closure_cash,
    )
    return {
        "as_of": as_of,
        "periods": {"q4_2026": q4, "fy_2027": fy27},
        "historical": history,
        "fy_2025_reported": annual,
        "september_2026_reported": september,
        "project_context": packet["project_context"],
        "q4_attributable_sustaining_capex_residual_millions": fy26_sustaining - history["attributable_sustaining_capex_millions"],
        "q4_attributable_development_capex_residual_millions": fy26_development - history["attributable_development_capex_millions"],
        "q4_yanacocha_water_treatment_cash_millions": q4_yanacocha_cash,
        "fy_2028_indicative_closure_cash_millions": fy28_closure_cash,
        "checks": checks,
        "sources": packet["sources"],
        "limitations": tuple(packet["limitations"]),
    }

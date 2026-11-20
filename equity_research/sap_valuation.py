"""Local-currency operating forecasts and dated enterprise-to-ADS valuation."""

from dataclasses import dataclass, replace
from datetime import date
from math import isclose

from .fcff_valuation import DatedFCFF, EquityBridge, TerminalAssumptions, discount_fcff
from .foreign_fx import listed_unit_value
from .operating_research import ResearchGap


@dataclass(frozen=True)
class SapAssumptions:
    terminal_growth: float = .025
    terminal_roic: float = .20
    wacc_spread: float = .055
    growth_retention: float = .65
    margin_shift: float = 0.


SAP_GUIDES = {
    "wacc_spread": ("WACC premium over benchmark", .03, .10),
    "terminal_growth": ("Terminal revenue growth", .005, .035),
    "terminal_roic": ("Terminal incremental ROIC", .08, .45),
    "growth_retention": ("Annual retention of excess growth", .25, .85),
    "margin_shift": ("Operating margin adjustment", -.04, .04),
}


def foreign_defaults(ticker):
    return SapAssumptions(wacc_spread=.043) if ticker == "HLN" else SapAssumptions()


def prepare_sap(evidence, assumptions=SapAssumptions()):
    return prepare_foreign_operating(evidence, assumptions, ticker="SAP")


def prepare_foreign_operating(evidence, assumptions, *, ticker):
    if ticker not in {"SAP", "HLN"}:
        raise ResearchGap("No operating model has been reviewed for this foreign issuer.")
    sterling = ticker == "HLN"
    currency = "GBP" if sterling else "EUR"
    interim = evidence["interim"]
    scenario = evidence["revenue_scenario"]
    market = evidence["market_quote"]
    benchmark = evidence["yield_benchmark"]
    if (evidence["reporting_currency"] != currency or evidence["accounting"] != "IFRS"
            or not scenario or not interim.get("capital") or not evidence["quote_fx_same_day"]
            or not market or not benchmark or benchmark["status"] != "available"):
        raise ResearchGap(f"{ticker}'s forecast needs a verified IFRS capital bridge, a dated {currency} yield, and matching U.S. close and FX.")
    quarter = interim["periods"][0]
    balance = quarter["balance"]
    income = quarter["income"]
    cash_flow = quarter["cash_flow"]
    capital = interim["capital"]
    balance_date = date.fromisoformat(quarter["balance_end"])
    market_date = date.fromisoformat(market["trading_date"])
    if not 0 <= (market_date - balance_date).days <= (165 if sterling else 120):
        raise ResearchGap(f"The {ticker} balance sheet is too old for this market comparison.")
    if not 0 <= (market_date - date.fromisoformat(benchmark["date"])).days <= 7:
        raise ResearchGap(f"The {currency} benchmark is too old for this valuation scenario.")
    wacc = benchmark["rate"] + assumptions.wacc_spread
    if not 0 < assumptions.terminal_growth or assumptions.terminal_growth + .005 >= min(
            assumptions.terminal_roic, wacc - .005):
        raise ResearchGap(f"The {currency} valuation needs positive terminal reinvestment and WACC above growth.")
    stub_years = 1 - interim.get("months", 9) / 12
    metrics = {
        "margin": income["operating_income"] / income["revenue"] + assumptions.margin_shift,
        "tax": 1 - income["net_income"] / income["pretax_income"],
        "da": cash_flow["depreciation"] / income["revenue"],
        "capex": -cash_flow["capital_expenditures"] / income["revenue"],
        "finance_cost": capital["finance_cost_ytd"] / (1-stub_years),
        "wc": (capital["receivables"] - capital["payables"] - capital["contract_liabilities"]) / scenario["total_revenue"],
    }
    if not all(0 < metrics[key] < .60 for key in ("margin", "tax", "da", "capex")) or capital["finance_cost_ytd"] < 0:
        raise ResearchGap(f"{ticker}'s income and reinvestment ratios need review.")
    bridge = EquityBridge(balance_date, balance["cash"], 0., capital["financial_liabilities"], 0.,
                          capital["minority"], 0., capital["diluted_shares"])
    opening = {
        "cash": balance["cash"], "ppe": capital["ppe_intangibles"],
        "wc": capital["receivables"] - capital["payables"] - capital["contract_liabilities"],
        "equity": balance["equity"],
        "other_assets": balance["assets"] - balance["cash"] - capital["ppe_intangibles"] - capital["receivables"],
        "other_liabilities": balance["liabilities"] - capital["financial_liabilities"] - capital["payables"]
                             - capital["contract_liabilities"],
    }
    drivers = scenario["drivers"]
    revenue_lines = tuple(row["forecast_full_year"] for row in drivers)
    years = []
    for step in range(6):
        if step:
            revenue_lines = tuple(value * (1 + assumptions.terminal_growth
                                  + (driver["growth_ratio"] - assumptions.terminal_growth) * assumptions.growth_retention ** step)
                                  for value, driver in zip(revenue_lines, drivers, strict=True))
        days = stub_years if step == 0 else 1.
        revenue = sum(revenue_lines) - (income["revenue"] if step == 0 else 0.)
        growth_revenue = sum(revenue_lines)
        previous = years[-1] if years else opening
        wc = growth_revenue * metrics["wc"]
        change_wc = wc - previous["wc"]
        ebit = revenue * metrics["margin"]
        interest = metrics["finance_cost"] * days
        net_income = (ebit - interest) * (1 - metrics["tax"])
        nopat = ebit * (1 - metrics["tax"])
        da = revenue * metrics["da"]
        capex_target = scenario.get("capex_target", metrics["capex"])
        capex_rate = metrics["capex"] + (capex_target - metrics["capex"]) * min(step/4, 1)
        capex = revenue * capex_rate
        cfo = net_income + da - change_wc
        ppe = previous["ppe"] + capex - da
        cash = previous["cash"] + cfo - capex
        equity = previous["equity"] + net_income
        receivables = growth_revenue * capital["receivables"] / scenario["total_revenue"]
        operating_liabilities = receivables - wc
        assets = cash + ppe + receivables + opening["other_assets"]
        liabilities = capital["financial_liabilities"] + operating_liabilities + opening["other_liabilities"]
        if not isclose(assets, liabilities + equity, rel_tol=0, abs_tol=2):
            raise ResearchGap(f"Projected {ticker} assets do not reconcile to liabilities and equity.")
        years.append(dict(end=date(balance_date.year + step, 12, 31), revenue=revenue, ebit=ebit, interest=interest,
                          net_income=net_income, nopat=nopat, da=da, capex=capex, cfo=cfo, ppe=ppe, cash=cash,
                          equity=equity, assets=assets, liabilities=liabilities, wc=wc, receivables=receivables,
                          operating_liabilities=operating_liabilities, fcff=nopat+da-capex-change_wc,
                          revenue_lines=revenue_lines))
    flows = tuple(DatedFCFF(row["end"], row["fcff"]) for row in years)
    terminal = TerminalAssumptions(years[-1]["nopat"], assumptions.terminal_growth, assumptions.terminal_roic)
    dcf = discount_fcff(flows, balance_date, wacc, terminal, bridge)
    sensitivity = tuple(tuple(discount_fcff(
        flows, balance_date, wacc + w * .005, replace(terminal, growth_rate=terminal.growth_rate + g * .005), bridge
    ).value_per_share for g in (-1, 0, 1)) for w in (-1, 0, 1))
    usd = listed_unit_value(dcf.equity_value_millions, capital["diluted_shares"], evidence["listing"], evidence["fx"])
    return {"evidence": evidence, "ticker": ticker, "currency": currency, "stub_years": stub_years,
            "assumptions": assumptions, "metrics": metrics, "capital": capital, "opening": opening,
            "years": years, "dcf": dcf, "sensitivity": sensitivity, "usd_per_ads": usd, "market_date": market_date,
            "annual": evidence["annual_financials"]}

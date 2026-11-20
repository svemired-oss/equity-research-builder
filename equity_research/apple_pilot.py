"""Build a dated Apple valuation from filed product and service revenue."""

from datetime import date, timedelta
from math import isclose
from statistics import median

from .fcff_valuation import DatedFCFF, EquityBridge, TerminalAssumptions, discount_fcff
from .operating_valuation import CostOfCapitalInputs, calculate_cost_of_capital


AS_OF = date(2026, 11, 13)
CLOSE = 356.27
PRICE_URL = "https://investor.apple.com/stock-price/"
LINES = {
    "IPhoneMember": "iPhone", "MacMember": "Mac", "IPadMember": "iPad",
    "WearablesHomeandAccessoriesMember": "Wearables, Home and Accessories", "ServiceMember": "Services",
}
MARGIN_SHIFT = {"Bear": -.03, "Base": 0, "Bull": .03}


def _sales(packet, period, accession):
    result = {}
    for fact in packet["operating_facts"]["observations"]:
        if (fact["period_end"] != period or fact["accession"] != accession or
                fact["unit"] != "USD" or fact["metric"] != "product_or_service_revenue" or
                len(fact["dimensions"]) != 1):
            continue
        member = fact["dimensions"][0]["member"].rsplit("}", 1)[-1].rsplit(":", 1)[-1]
        if member in LINES:
            value = fact["value"] / 1e6
            if member in result and result[member] != value:
                raise ValueError("Conflicting Apple revenue disclosures.")
            result[member] = value
    if set(result) != LINES.keys() or any(value <= 0 for value in result.values()):
        raise ValueError("The annual Apple filing must disclose all five revenue lines.")
    return result


def prepare_apple(packet, treasury):
    if (packet["issuer"]["cik"] != "0000320193" or packet["as_of"] != AS_OF.isoformat()
            or treasury["status"] != "available" or treasury["date"] != AS_OF.isoformat()):
        raise ValueError("Apple's pilot requires the verified November 13 evidence.")
    history = packet["actuals"]["periods"]
    prior, latest = history[-2:]
    filing = latest["filing"]
    year_end = date.fromisoformat(latest["period_end"])
    # The dated investment and borrowing classifications below were reviewed
    # against this exact filing. A new filing must update that bridge.
    if filing["accession"] != "0000320193-26-000026" or year_end != date(2026, 9, 26):
        raise ValueError("The Apple balance-sheet bridge requires review after a new annual filing.")
    old = _sales(packet, prior["period_end"], filing["accession"])
    current = _sales(packet, latest["period_end"], filing["accession"])
    metrics = {key: value / 1e6 for key, value in latest["metrics"].items() if value is not None}
    for period, sales in ((prior, old), (latest, current)):
        if not isclose(sum(sales.values()), period["metrics"]["revenue"] / 1e6, abs_tol=.001):
            raise ValueError("Disclosed Apple product and service sales do not reconcile.")
    cash = metrics["cash_and_equivalents"]
    investments = metrics["short_term_investments"] + 80213
    debt = sum(metrics[key] for key in ("current_portion_long_term_debt", "long_term_debt",
                                       "commercial_paper", "finance_lease_current", "finance_lease_noncurrent"))
    shares = metrics["ending_shares"]
    equity = metrics["equity_attributable_to_parent"]
    equity_assets = metrics["total_assets"]
    liabilities = metrics["total_liabilities"]
    if not isclose(equity_assets - liabilities, equity):
        raise ValueError("Apple's opening balance sheet does not reconcile.")
    cost = CostOfCapitalInputs(AS_OF, treasury["rate"], .0441, 1.15, CLOSE * shares, .055, debt, .17, 0, 0)
    wacc = calculate_cost_of_capital(cost).wacc
    growth, roic = .025, .25
    bridge = EquityBridge(AS_OF, cash, investments, debt, 0, 0, 0, shares)
    growth_inputs = {key: (current[key] / old[key] - 1) for key in LINES}
    margin = median(p["metrics"]["operating_income"] / p["metrics"]["revenue"] for p in history[-3:])
    da = metrics["depreciation_amortization"] / metrics["revenue"]
    capex = max(da, median(p["metrics"]["capex"] / p["metrics"]["revenue"] for p in history[-3:]))
    opening_wc_assets = metrics["current_assets"] - cash - metrics["short_term_investments"]
    opening_wc_liabilities = metrics["current_liabilities"] - metrics["commercial_paper"] - metrics["current_portion_long_term_debt"]
    if metrics["finance_lease_current"] > opening_wc_liabilities:
        raise ValueError("Apple's current finance lease classification could not be reconciled.")
    opening_wc_liabilities -= metrics["finance_lease_current"]
    wc_assets = opening_wc_assets / metrics["revenue"]
    wc_liabilities = opening_wc_liabilities / metrics["revenue"]
    wc_ratio = wc_assets - wc_liabilities
    dates = tuple(date(year, 9, 30) - timedelta(days=(date(year, 9, 30).weekday() - 5) % 7)
                  for year in range(2027, 2032))
    stub = (dates[0] - AS_OF).days / (dates[0] - year_end).days
    base_revenue = []
    amounts = current.copy()
    for index in range(5):
        for key, rate in growth_inputs.items():
            amounts[key] *= 1 + (growth + (rate - growth) * .5 ** (index + 1))
        base_revenue.append(sum(amounts.values()))
    scenarios = {}
    for name, shift in MARGIN_SHIFT.items():
        flows = []
        previous = metrics["revenue"]
        for index, (end, revenue) in enumerate(zip(dates, base_revenue, strict=True)):
            fcff = revenue * ((margin + shift) * .83 + da - capex) - (revenue - previous) * wc_ratio
            flows.append(DatedFCFF(end, fcff * (stub if index == 0 else 1)))
            previous = revenue
        terminal = TerminalAssumptions(base_revenue[-1] * (margin + shift) * .83, growth, roic)
        scenarios[name] = discount_fcff(flows, AS_OF, wacc, terminal, bridge)
    values = {
        "history": history, "dates": dates, "old_sales": old, "sales": current, "growths": growth_inputs,
        "margin": margin, "da": da, "capex": capex, "wc_assets": wc_assets, "wc_liabilities": wc_liabilities,
        "opening_wc_assets": opening_wc_assets, "opening_wc_liabilities": opening_wc_liabilities,
        "metrics": metrics, "cash": cash, "investments": investments, "debt": debt, "shares": shares,
        "other_assets": equity_assets - cash - investments - opening_wc_assets - metrics["net_ppe"],
        "other_liabilities": liabilities - debt - opening_wc_liabilities,
        "cost": cost, "wacc": wacc, "growth": growth, "roic": roic, "stub": stub,
        "scenarios": scenarios, "revenue": base_revenue,
        "sources": {"Apple FY2026 10-K": filing["url"], "Apple closing price": PRICE_URL,
                    "U.S. Treasury 10-year par yield": treasury["url"],
                    "Equity risk premium reference": "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/home.htm"},
    }
    return values

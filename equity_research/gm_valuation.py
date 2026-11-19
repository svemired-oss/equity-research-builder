"""Build an Automotive FCFF valuation and a GM Financial common-equity valuation."""

from dataclasses import dataclass
from datetime import date
from math import isclose
from statistics import mean

from .captive_finance_valuation import FinanceValuation, value_captive_finance
from .fcff_valuation import DatedFCFF, EquityBridge, FCFFValuation, TerminalAssumptions, discount_fcff
from .operating_research import ResearchGap


@dataclass(frozen=True)
class AutomotiveAssumptions:
    wacc: float
    finance_cost_of_equity: float
    tax_rate: float = .21
    terminal_growth: float = .025
    terminal_roic: float = .15
    terminal_volume_growth: float = .01
    liquidity_share_of_revenue: float = .05


@dataclass(frozen=True)
class AutomotiveYear:
    end: date
    wholesale_units_thousands: float
    vehicles_and_parts_per_unit_dollars: float
    used_vehicle_sales_millions: float
    service_and_other_sales_millions: float
    revenue_millions: float
    ebit_millions: float
    da_millions: float
    capex_millions: float
    working_capital_investment_millions: float
    fcff_millions: float


@dataclass(frozen=True)
class GMValuation:
    automotive: FCFFValuation
    finance: FinanceValuation
    forecast: tuple[AutomotiveYear, ...]
    q4_ocf_millions: float
    q4_capex_millions: float
    q4_finance_dividends_millions: float
    q4_after_tax_net_interest_millions: float
    required_automotive_liquidity_millions: float
    common_equity_millions: float
    value_per_share: float


def value_gm(split, assumptions: AutomotiveAssumptions):
    """Value both components at the September balance date using latest filed evidence.

    This calculation uses a balance-date anchor for both businesses. It is a
    scenario prepared with subsequently filed earnings guidance. A live
    investment recommendation requires a reviewed bridge to the run cutoff.
    """
    evidence = split["finance_equity"]
    anchor = evidence["end"]
    if (anchor.month, anchor.day) != (9, 30):
        raise ResearchGap("The Automotive stub calculation requires the September 30 balance date.")
    a = assumptions
    g = a.terminal_growth
    if not (0 <= a.terminal_volume_growth <= g < min(a.wacc, a.finance_cost_of_equity, a.terminal_roic) < 1
            and 0 < a.tax_rate < .5 and 0 < a.liquidity_share_of_revenue < .5):
        raise ResearchGap("The Automotive discount, growth or liquidity assumptions are out of range.")
    rc = split["automotive_revenue_drivers"]
    vc = split["automotive_wholesale"]
    release = split["automotive_release"]
    balance = release["balance"]
    lender = value_captive_finance(evidence, cost_of_equity=a.finance_cost_of_equity, terminal_growth=g)
    sales = sum(rc["trailing"].values())
    current_sales = sum(rc["current_ytd"].values())
    if min(current_sales, sales, vc["current_ytd"], vc["prior_ytd"], vc["trailing"]) <= 0:
        raise ResearchGap("The Automotive forecast needs positive filed volume and revenue.")
    ebit_margin = release["ytd_adjusted_auto_ebit_millions"] / current_sales
    da_ratio = release["ytd_da_millions"] / current_sales
    q4_cfo = mean(release["annual_ocf_guidance_millions"]) - release["ytd_ocf_millions"]
    full_capex = mean(release["annual_capex_guidance_millions"])
    q4_capex = full_capex - release["ytd_capex_millions"]
    if min(q4_cfo, q4_capex, ebit_margin, da_ratio) <= 0:
        raise ResearchGap("GM's filed guidance and year-to-date cash flows do not support a positive fourth-quarter operating base.")
    # Use the same annual distribution forecast as the lender valuation, allocating
    # a quarter of it to the Automotive cash-flow stub.
    q4_dividends = lender.forecast[0].distributable_earnings / 4
    prior_quarter_interest = (release["annual_interest_expense_millions"] - release["prior_ytd_interest_expense_millions"]
                              - release["annual_interest_income_millions"] + release["prior_ytd_interest_income_millions"])
    q4_after_tax_net_interest = prior_quarter_interest * (1-a.tax_rate)
    flows = [DatedFCFF(date(anchor.year, 12, 31), q4_cfo - q4_capex - q4_dividends + q4_after_tax_net_interest)]

    volume = vc["trailing"]
    price_mix = rc["trailing"]["vehicles_and_parts"] * 1000 / volume
    used, services = (rc["trailing"][key] for key in ("used_vehicles", "services_and_other"))
    if min(price_mix, used, services, *rc["prior_ytd"].values()) <= 0:
        raise ResearchGap("The Automotive model needs positive like-for-like product revenue.")
    volume_growth = vc["current_ytd"] / vc["prior_ytd"] - 1
    mix_growth = ((rc["current_ytd"]["vehicles_and_parts"] / vc["current_ytd"]) /
                  (rc["prior_ytd"]["vehicles_and_parts"] / vc["prior_ytd"]) - 1)
    mix_terminal = (1+g)/(1+a.terminal_volume_growth)-1
    used_growth, service_growth = (rc["current_ytd"][key]/rc["prior_ytd"][key]-1
                                  for key in ("used_vehicles", "services_and_other"))
    wc_assets = balance["current_assets"] - balance["cash"] - balance["securities"]
    wc_liabilities = balance["current_liabilities"] - balance["current_debt"]
    wc_ratio = (wc_assets - wc_liabilities)/sales
    normalized_capex_ratio = da_ratio + ebit_margin*(1-a.tax_rate)*g/a.terminal_roic - wc_ratio*g
    current_capex_ratio = full_capex/sales
    forecast = []
    for index in range(1, 6):
        fade = .5**index
        volume *= 1+a.terminal_volume_growth+(volume_growth-a.terminal_volume_growth)*fade
        price_mix *= 1+mix_terminal+(mix_growth-mix_terminal)*fade
        used *= 1+g+(used_growth-g)*fade
        services *= 1+g+(service_growth-g)*fade
        revenue = volume*price_mix/1000 + used + services
        ebit = revenue*ebit_margin
        da = revenue*da_ratio
        capex = revenue*(current_capex_ratio + (normalized_capex_ratio-current_capex_ratio)*(index-1)/4)
        wc = (revenue-sales)*wc_ratio
        fcff = ebit*(1-a.tax_rate) + da - capex - wc
        end = date(anchor.year+index, 12, 31)
        forecast.append(AutomotiveYear(end, volume, price_mix, used, services, revenue, ebit, da, capex, wc, fcff))
        flows.append(DatedFCFF(end, fcff))
        sales = revenue
    cash_and_securities = balance["cash"] + balance["securities"]
    required_cash = sum(rc["trailing"].values()) * a.liquidity_share_of_revenue
    automotive_debt = balance["current_debt"]+balance["long_debt"]
    # Sector debt includes intercompany dealer obligations. The lender asset and
    # the matching Automotive debt remain in their respective sector models.
    consolidated_auto_debt = automotive_debt + balance["current_debt_eliminations"]
    if not isclose(consolidated_auto_debt, split["debt"]["automotive_debt_millions"], rel_tol=0, abs_tol=1):
        raise ResearchGap("The Automotive sector debt does not reconcile after filed intercompany eliminations.")
    # GM's adjusted EBIT already reflects minority claims. GM Financial's
    # preferred claim is already reflected in the lender's common-equity base.
    bridge = EquityBridge(anchor, max(0., cash_and_securities - required_cash), 0, automotive_debt, 0, 0,
                          balance["pensions"]+balance["postretirement"], release["ending_parent_shares_millions"])
    terminal = TerminalAssumptions(forecast[-1].ebit_millions*(1-a.tax_rate), g, a.terminal_roic)
    automotive = discount_fcff(flows, anchor, a.wacc, terminal, bridge)
    total = automotive.equity_value_millions + lender.common_equity_value
    return GMValuation(automotive, lender, tuple(forecast), q4_cfo, q4_capex, q4_dividends,
                       q4_after_tax_net_interest, required_cash, total, total/bridge.diluted_shares_millions)

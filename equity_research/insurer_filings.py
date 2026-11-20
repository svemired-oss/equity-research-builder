"""Map Progressive's underwriting and surplus disclosures to the P&C forecast."""

from datetime import date
from math import isclose
import re

from .filing_xbrl import load_filing_instance
from .insurer_valuation import UnderwritingBasis
from .operating_research import ResearchGap
from .sec_guidance import _ReleaseText


_EARNED_PREMIUMS = "PremiumsEarnedNet"


def _income(instance, start, end):
    def value(key):
        amounts = {fact.value/1e6 for fact in instance.facts if fact.start == start and fact.end == end
                   and fact.unit == "USD" and not fact.dimensions and fact.concept.rsplit("}", 1)[-1].rsplit(":", 1)[-1] == key}
        if len(amounts) != 1:
            raise ResearchGap(f"The insurer did not uniquely disclose {key} for the period ended {end}.")
        return amounts.pop()

    premiums = value(_EARNED_PREMIUMS)
    underwriting = value("PretaxUnderwritingProfitLoss")
    investment = value("NetInvestmentIncome")
    cost = value("InterestExpenseDebt") + value("NonInsuranceServiceExpenses") - value("NonInsuranceServiceRevenues")
    pretax = value("IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest")
    taxes = value("IncomeTaxExpenseBenefit")
    gains = value("RealizedInvestmentGainsLosses")
    if not isclose(underwriting+investment-cost+gains, pretax, rel_tol=0, abs_tol=1):
        raise ResearchGap("Underwriting, investment gains and financing costs did not reconcile to reported pretax income.")
    return premiums, underwriting, investment, cost, taxes, pretax


def prepare_progressive(packet, client):
    """Use filed FY plus current YTD minus matched prior YTD operating drivers."""
    cik = packet["issuer"]["cik"]
    if cik != "0000080661":
        raise ResearchGap("The insurance holding company needs a reviewed underwriting and statutory-surplus mapping.")
    annual, interim = packet["actuals"]["periods"][-1:], packet["interim_actuals"]["periods"][-1:]
    if not annual or not interim:
        raise ResearchGap("The insurance forecast requires a current 10-Q and its matching annual filing.")
    ytd_start, ytd_end = (date.fromisoformat(interim[0][key]) for key in ("period_start", "period_end"))
    annual_start, annual_end = (date.fromisoformat(annual[0][key]) for key in ("period_start", "period_end"))
    prior_start, prior_end = (item.replace(year=item.year-1) for item in (ytd_start, ytd_end))
    if (ytd_start-annual_end).days != 1 or annual_start != prior_start:
        raise ResearchGap("The insurer needs matched year-to-date periods.")
    current_source = interim[0]["filing"]
    current = load_filing_instance(client, cik, current_source["accession"])
    full_year = load_filing_instance(client, cik, annual[0]["filing"]["accession"])
    full = _income(full_year, annual_start, annual_end)
    now, prior = (_income(current, start, end) for start, end in ((ytd_start, ytd_end), (prior_start, prior_end)))
    if min(now[0], prior[0]) <= 0:
        raise ResearchGap("Comparable positive year-to-date earned premiums are required.")
    premiums, underwriting, investment, costs, taxes, pretax = (
        historical + recent - comparative for historical, recent, comparative in zip(full, now, prior, strict=True))
    source_html = client.get_text(current_source["url"])
    paragraphs = _ReleaseText(source_html).blocks
    displayed = f"{ytd_end:%B} {ytd_end.day}, {ytd_end.year}"
    surplus_pattern = re.compile(rf"As of {re.escape(displayed)}, our estimated consolidated statutory surplus was "
                                 r"\$([\d.]+) billion", re.I)
    goal_pattern = re.compile(r"(\d+(?:\.\d+)?)% companywide calendar-year underwriting profit goal", re.I)
    surpluses = {float(match[1])*1000 for paragraph in paragraphs for match in surplus_pattern.finditer(paragraph)}
    goals = {float(match[1])/100 for paragraph in paragraphs for match in goal_pattern.finditer(paragraph)}
    if len(surpluses) != 1 or len(goals) != 1 or not 0 < taxes < pretax:
        raise ResearchGap("The insurer needs a uniquely disclosed surplus, underwriting target and effective tax rate.")
    # Scale the prior-year proxy to apply the directly observed comparable YTD growth.
    # It is a calibration input rather than a separately filed trailing total.
    growth_base = premiums * prior[0] / now[0]
    normalized_taxes = (underwriting+investment-costs)*taxes/pretax
    result = UnderwritingBasis(premiums, growth_base, underwriting, investment, costs, normalized_taxes,
                               surpluses.pop(), 1-goals.pop())
    return result, ytd_end, (current_source["url"], annual[0]["filing"]["url"])

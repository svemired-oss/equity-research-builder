"""Collect matched bank earnings and parent-level capital from SEC filings."""

from dataclasses import dataclass, fields
from datetime import date
from math import isclose
import re

from .bank_valuation import BankBalance, BankIncome
from .filing_xbrl import load_filing_instance
from .gd_releases import _Page
from .operating_research import ResearchGap


_JPM = "0000019617"
_BAC = "0000070858"
_WFC = "0000072971"
BANK_CIKS = frozenset((_JPM, _BAC, _WFC))
_JPM_METHODS = ("BaselIIIStandardizedMember", "BaselIIIAdvancedMember")
_OTHER_METHODS = ("StandardizedApproachMember", "AdvancedApproachMember")
_LOANS = {
    _JPM: "FinancingReceivableExcludingAccruedInterestBeforeAllowanceForCreditLossesNetOfDeferredIncome",
    _BAC: "FinancingReceivableExcludingAccruedInterestBeforeAllowanceForCreditLoss",
    _WFC: "FinancingReceivableAndNetInvestmentInLeaseExcludingAccruedInterestBeforeAllowanceForCreditLoss",
}


@dataclass(frozen=True)
class BankRegulatoryChecks:
    tier1_capital: float
    leverage_exposure: float
    reported_slr: float
    slr_requirement: float
    average_lcr: float | None


@dataclass(frozen=True)
class BankEvidence:
    balance_date: date
    current: BankBalance
    previous: BankBalance
    trailing: BankIncome
    regulatory_method: str
    minimum_cet1: float
    sources: tuple[str, ...]
    regulatory_checks: BankRegulatoryChecks | None = None
    forecast_minimum_cet1: float | None = None
    common_shares_millions: float | None = None


def _local(expanded):
    return expanded.rsplit("}", 1)[-1].rsplit(":", 1)[-1]


def _fact(instance, start, end, concept, *, members=(), unit="USD"):
    values = {fact.value for fact in instance.facts if fact.start == start and fact.end == end and fact.unit == unit
              and _local(fact.concept) == concept
              and {_local(member) for _, member in fact.dimensions} == set(members)}
    if len(values) != 1:
        scope = ", ".join(members) if members else "consolidated"
        raise ResearchGap(f"The bank filing did not uniquely disclose {scope} {concept} at {end}.")
    return values.pop() / (1e6 if unit == "USD" else 1)


def _income(instance, start, end, cik):
    def value(concept):
        return _fact(instance, start, end, concept)

    interest = value("InterestIncomeOperating" if cik == _JPM else "InterestAndDividendIncomeOperating")
    total_cost, deposit_cost = (value(key) for key in ("InterestExpenseOperating", "InterestExpenseDeposits"))
    pretax = value("IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest")
    taxes = value("IncomeTaxExpenseBenefit")
    earnings = value("NetIncomeLossAvailableToCommonStockholdersBasic")
    other_income, expense = (value(key) for key in ("NoninterestIncome", "NoninterestExpense"))
    if cik == _BAC:
        # MD&A repeats this fact at a coarser precision. Reconcile to the
        # signed face of the income statement before choosing the exact value.
        implied = interest-total_cost+other_income-expense-pretax
        provisions = {fact.value/1e6 for fact in instance.facts
                      if fact.start == start and fact.end == end and fact.unit == "USD" and not fact.dimensions
                      and _local(fact.concept) == "FinancingReceivableExcludingAccruedInterestAndOffBalanceSheetLiabilityCreditLossProvisionReversal"
                      and isclose(fact.value/1e6, implied, rel_tol=0, abs_tol=1)}
        if len(provisions) != 1:
            raise ResearchGap("Bank of America's provision could not be reconciled to GAAP pretax earnings.")
        provision = provisions.pop()
    else:
        provision = value("ProvisionForLoanLeaseAndOtherLosses")
    result = BankIncome(interest, deposit_cost, total_cost-deposit_cost, other_income,
                        expense, provision, taxes,
                        pretax-taxes-earnings)
    if not isclose(result.pretax, pretax, rel_tol=0, abs_tol=1):
        raise ResearchGap("The bank income schedule did not reconcile to filed pretax earnings.")
    return result


def _capital(instance, end, methodology, cik):
    scope = ("ParentCompanyMember", methodology)
    if cik == _WFC:
        requirement = _fact(instance, None, end, "BankingRegulationCommonEquityTierOneRiskBasedCapitalRatioWellCapitalizedMinimum",
                            members=scope, unit="pure")
        cet1 = _fact(instance, None, end, "CommonEquityTierOneCapital", members=scope)
    else:
        requirement = _fact(instance, None, end, "BankingRegulationCommonEquityTierOneRiskBasedCapitalRatioCapitalAdequacyMinimum",
                            members=("BankHoldingCompaniesMember", methodology), unit="pure")
        cet1 = _fact(instance, None, end, "CommonEquityTier1Capital", members=scope)
    rwa = _fact(instance, None, end, "RiskWeightedAssets", members=scope)
    if not 0 < requirement < cet1/rwa < .35:
        raise ResearchGap("The filed consolidated CET1 values are inconsistent or below the capital requirement.")
    return cet1, rwa, requirement


def _balance(instance, end, capital, cik):
    cet1, rwa, _ = capital
    equity_tag = "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest" if cik == _WFC else "StockholdersEquity"
    assets, liabilities, deposits, loans, equity = (_fact(instance, None, end, concept)
                                                   for concept in ("Assets", "Liabilities", "Deposits", _LOANS[cik], equity_tag))
    return BankBalance(assets, liabilities, deposits, loans, rwa, cet1, equity)


def _regulatory_checks(instance, end, method, html, cik):
    parent = ("ParentCompanyMember",)
    exposure = _fact(instance, None, end, "TotalLeverageExposure", members=parent)
    tier1 = _fact(instance, None, end, "TierOneRiskBasedCapital", members=(*parent, method))
    slr = _fact(instance, None, end, "SupplementaryLeverageRatio", members=parent, unit="pure")
    if cik == _WFC:
        minimum = _fact(instance, None, end, "BankingRegulationSupplementaryLeverageRatioWellCapitalizedMinimum",
                        members=parent, unit="pure")
    else:
        minimum = _fact(instance, None, end, "SupplementaryLeverageRatioRequiredForCapitalAdequacyToAverageAssets",
                        members=("BankHoldingCompaniesMember", "BaselIIIMember"), unit="pure")
    if exposure <= 0 or not 0 < minimum < slr < .35 or not isclose(tier1 / exposure, slr, rel_tol=0, abs_tol=.0005):
        raise ResearchGap("The holding-company leverage exposure and capital did not reconcile to the reported SLR.")
    quarter = f"{(end.month - 1) // 3 + 1}Q{end.year % 100:02d}"
    lcrs = set()
    for table in _Page(html).tables:
        rows = [[cell.text for cell in row if cell.text.strip() and cell.text != "%"] for row in table.rows]
        expected_label = "LCR" if cik == _WFC else "Firm Liquidity coverage ratio"
        has_date = any(len(row) > 1 and re.fullmatch(rf"{end:%b}\s+{end.day},\s*{end.year}", row[1]) for row in rows[:4] if row)
        if cik == _WFC and not has_date or cik == _JPM and not any(row and row[0] == quarter for row in rows):
            continue
        for row in rows:
            if len(row) > 1 and row[0].startswith(expected_label) and re.fullmatch(r"\d{2,3}", row[1]):
                lcrs.add(int(row[1]) / 100)
    if len(lcrs) != 1:
        raise ResearchGap("The firm-wide average quarterly liquidity coverage ratio is ambiguous.")
    return BankRegulatoryChecks(tier1, exposure, slr, minimum, lcrs.pop())


def _bac_capital(html, end):
    page = _Page(html)
    stamp = f"{end:%B} {end.day}, {end.year}"
    tables = []
    for table in page.tables:
        rows = [[cell.text for cell in row if cell.text and cell.text not in ("$", "%")] for row in table.rows]
        rows = [row for row in rows if row]
        if (len(rows) >= 16 and "Bank of America Corporation Regulatory Capital under Basel 3" in rows[0]
                and stamp in rows[2] and rows[1][0].startswith("Standardized") and rows[1][1].startswith("Advanced")):
            tables.append(rows[:16])
    if len(tables) != 1:
        raise ResearchGap("Bank of America did not uniquely disclose the parent-level Basel capital table.")
    rows = {row[0]: row[1:] for row in tables[0]}

    def amounts(label, count):
        row = rows.get(label, [])
        if len(row) != count or any(not re.fullmatch(r"[\d,]+(?:\.\d+)?", part) for part in row):
            raise ResearchGap(f"The Bank of America capital table has an invalid {label} row.")
        return [float(part.replace(",", "")) for part in row]

    cet1 = amounts("Common equity tier 1 capital", 2)
    rwa = amounts("Risk-weighted assets (in billions)", 2)
    std, adv, minimum = [value/100 for value in amounts("Common equity tier 1 capital ratio", 3)]
    capital = {}
    for method, amount, risk, ratio in zip(_OTHER_METHODS, cet1, rwa, (std, adv), strict=True):
        if risk <= 0 or not (0 < minimum < ratio < .35) or not isclose(amount/(risk*1000), ratio, rel_tol=0, abs_tol=.0006):
            raise ResearchGap("Bank of America's CET1 and risk-weighted assets do not reconcile to its reported ratio.")
        capital[method] = amount, risk*1000, minimum
    tier1 = amounts("Tier 1 capital", 2)[0]
    exposure = amounts("Supplementary leverage exposure (in billions)", 1)[0]*1000
    slr, slr_min = [value/100 for value in amounts("Supplementary leverage ratio", 2)]
    if exposure <= 0 or not 0 < slr_min < slr < .35 or not isclose(tier1/exposure, slr, rel_tol=0, abs_tol=.0006):
        raise ResearchGap("Bank of America's reported leverage exposure and ratio did not reconcile.")
    bump = re.search(r"will increase the Corporation[’']s minimum CET1 capital ratio requirement by (\d+) bps under both",
                     page.text, re.I)
    forecast_minimum = minimum + int(bump[1])/10000 if bump else None
    lcrs = re.findall(rf"for the three months ended {re.escape(stamp)} and [^.]+\. "
                      r"For the same periods, the average consolidated LCR was (\d{2,3}) percent and \d{2,3} percent\.",
                      page.text, re.I)
    lcr = int(lcrs[0])/100 if len(lcrs) == 1 else None
    return capital, BankRegulatoryChecks(tier1, exposure, slr, slr_min, lcr), forecast_minimum


def prepare_bank_filings(packet, client):
    """Build matched holding-company inputs and a trailing income base."""
    cik = packet["issuer"]["cik"]
    if cik not in BANK_CIKS:
        raise ResearchGap("This bank's holding-company capital, funding and earnings tags need a reviewed mapping.")
    annual_rows = packet["actuals"]["periods"][-1:]
    ytd_rows = packet["interim_actuals"]["periods"][-1:]
    if not annual_rows or not ytd_rows:
        raise ResearchGap("A current quarterly bank statement and comparable annual report are required.")
    annual_row, ytd_row = annual_rows[0], ytd_rows[0]
    annual_start, annual_end = (date.fromisoformat(annual_row[key]) for key in ("period_start", "period_end"))
    ytd_start, ytd_end = (date.fromisoformat(ytd_row[key]) for key in ("period_start", "period_end"))
    prior_end = ytd_end.replace(year=ytd_end.year-1)
    prior_start = ytd_start.replace(year=ytd_start.year-1)
    prior = [row for row in packet["filings"]["interim"] if row["period_end"] == prior_end.isoformat()]
    if not prior or prior_start != annual_start or (ytd_start - annual_end).days != 1:
        raise ResearchGap("The bank model needs the same fiscal YTD in both years with a contiguous annual period.")
    prior_row = max(prior, key=lambda row: (row["filed_on"], row["accession"]))
    annual, interim, previous = (load_filing_instance(client, cik, filing["accession"])
                                 for filing in (annual_row["filing"], ytd_row["filing"], prior_row))
    full = _income(annual, annual_start, annual_end, cik)
    now = _income(interim, ytd_start, ytd_end, cik)
    then = _income(interim, prior_start, prior_end, cik)
    ttm = BankIncome(*(getattr(full, field.name) + getattr(now, field.name) - getattr(then, field.name)
                       for field in fields(BankIncome)))
    methods = _JPM_METHODS if cik == _JPM else _OTHER_METHODS
    html = client.get_text(ytd_row["filing"]["url"])
    forecast_minimum = None
    if cik == _BAC:
        capitals, checks, forecast_minimum = _bac_capital(html, ytd_end)
        prior_capitals, _, _ = _bac_capital(client.get_text(prior_row["url"]), prior_end)
    else:
        capitals = {method: _capital(interim, ytd_end, method, cik) for method in methods}
        prior_capitals = {method: _capital(previous, prior_end, method, cik) for method in methods}
    # Compare the aggregate dollars of headroom under both approaches.
    def headroom(method):
        capital, rwa, minimum = capitals[method]
        return capital-rwa*minimum

    method = min(methods, key=headroom)
    ending = _balance(interim, ytd_end, capitals[method], cik)
    beginning = _balance(previous, prior_end, prior_capitals[method], cik)
    if cik != _BAC:
        try:
            checks = _regulatory_checks(interim, ytd_end, method, html, cik)
        except ResearchGap:
            checks = None
    try:
        share_tag = "EntityCommonStockSharesOutstanding" if cik == _JPM else "CommonStockSharesOutstanding"
        shares = _fact(interim, None, ytd_end, share_tag, unit="shares")/1e6
    except ResearchGap:
        shares = None
    return BankEvidence(ytd_end, ending, beginning, ttm, method, capitals[method][2],
                        (annual_row["filing"]["url"], ytd_row["filing"]["url"], prior_row["url"]),
                        checks, forecast_minimum, shares if shares and shares > 0 else None)

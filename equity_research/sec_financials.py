"""Normalize consolidated, USD-denominated facts available at an SEC cutoff."""

from collections import defaultdict
from datetime import date, timedelta
from math import isfinite


# Candidate tags are ordered by coverage. A broader total takes precedence over
# a narrower disclosure in the same filing. The chosen tag remains visible.
_DURATION_METRICS = {
    "revenue": ("Revenues", "SalesRevenueNet", "RevenueFromContractWithCustomerExcludingAssessedTax",
                "RegulatedAndUnregulatedOperatingRevenue"),
    "cost_of_revenue": ("CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"),
    "gross_profit": ("GrossProfit",),
    "operating_income": ("OperatingIncomeLoss",),
    "income_before_taxes": (
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
    ),
    "domestic_income_before_taxes": ("IncomeLossFromContinuingOperationsBeforeIncomeTaxesDomestic",),
    "foreign_income_before_taxes": ("IncomeLossFromContinuingOperationsBeforeIncomeTaxesForeign",),
    "income_taxes": ("IncomeTaxExpenseBenefit",),
    "net_income": ("NetIncomeLoss",),
    "consolidated_net_income": ("ProfitLoss",),
    "net_income_attributable_to_noncontrolling_interest": ("NetIncomeLossAttributableToNoncontrollingInterest",),
    "net_income_from_continuing_operations": ("IncomeLossFromContinuingOperations",),
    "interest_expense": ("InterestExpense", "InterestAndDebtExpense", "InterestExpenseDebt", "InterestExpenseNonoperating"),
    # Components of the labeled operating-income proxy for issuers that present no operating subtotal.
    "interest_income": ("InvestmentIncomeInterest", "InvestmentIncomeInterestAndDividend"),
    "other_nonoperating_income": ("OtherNonoperatingIncomeExpense",),
    "depreciation_amortization": ("DepreciationDepletionAndAmortization", "DepreciationAndAmortization"),
    "depreciation_amortization_accretion": ("DepreciationAmortizationAndAccretionNet",),
    "depreciation": ("Depreciation", "DepreciationDepletionAndAmortizationPropertyPlantAndEquipment"),
    "amortization_of_intangibles": ("AmortizationOfIntangibleAssets",),
    "stock_based_compensation": ("ShareBasedCompensation",),
    "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities",),
    "investing_cash_flow": ("NetCashProvidedByUsedInInvestingActivities",),
    "financing_cash_flow": ("NetCashProvidedByUsedInFinancingActivities",),
    "exchange_effect_on_cash_and_restricted_cash": (
        "EffectOfExchangeRateOnCashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        "EffectOfExchangeRateOnCashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsIncludingDisposalGroupAndDiscontinuedOperations",
    ),
    "change_in_cash_and_restricted_cash": (
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsPeriodIncreaseDecreaseIncludingExchangeRateEffect",
    ),
    "capex": ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets",
              "PaymentsToAcquireOtherProductiveAssets"),
    "unpaid_capex_disclosure": ("CapitalExpendituresIncurredButNotYetPaid",),
    "dividends_paid": ("PaymentsOfDividends", "PaymentsOfDividendsCommonStock"),
    "share_repurchases": ("PaymentsForRepurchaseOfCommonStock",),
    "basic_shares": ("WeightedAverageNumberOfSharesOutstandingBasic",),
    "diluted_shares": ("WeightedAverageNumberOfDilutedSharesOutstanding",),
    "diluted_eps": ("EarningsPerShareDiluted",),
}

_INSTANT_METRICS = {
    "cash_and_equivalents": ("CashAndCashEquivalentsAtCarryingValue",),
    "restricted_cash": ("RestrictedCash",),
    "cash_and_restricted_cash": (
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsIncludingDisposalGroupAndDiscontinuedOperations",
    ),
    "short_term_investments": ("ShortTermInvestments", "MarketableSecuritiesCurrent"),
    "available_for_sale_debt_securities_current": ("AvailableForSaleSecuritiesDebtSecuritiesCurrent",),
    "receivables": ("AccountsReceivableNetCurrent", "ReceivablesNetCurrent"),
    "inventory": ("InventoryNet",),
    "prepaid_and_other_current_assets": ("PrepaidExpenseAndOtherAssetsCurrent",),
    "current_assets": ("AssetsCurrent",),
    "net_ppe": ("PropertyPlantAndEquipmentNet",),
    "gross_ppe": ("PropertyPlantAndEquipmentGross",),
    "accumulated_depreciation": ("AccumulatedDepreciationDepletionAndAmortizationPropertyPlantAndEquipment",),
    "construction_in_progress": ("ConstructionInProgressGross",),
    "operating_lease_right_of_use_assets": ("OperatingLeaseRightOfUseAsset",),
    "net_intangibles": ("IntangibleAssetsNetExcludingGoodwill",),
    "net_finite_lived_intangibles": ("FiniteLivedIntangibleAssetsNet",),
    "goodwill": ("Goodwill",),
    "deferred_income_tax_assets_net": ("DeferredIncomeTaxAssetsNet",),
    "other_noncurrent_assets": ("OtherAssetsNoncurrent",),
    "total_assets": ("Assets",),
    "accounts_payable": ("AccountsPayableCurrent",),
    "employee_related_liabilities_current": ("EmployeeRelatedLiabilitiesCurrent",),
    "deferred_revenue_current": ("ContractWithCustomerLiabilityCurrent",),
    "deferred_revenue_noncurrent": ("ContractWithCustomerLiabilityNoncurrent",),
    "current_liabilities": ("LiabilitiesCurrent",),
    "other_current_liabilities": ("OtherLiabilitiesCurrent",),
    "noncurrent_liabilities": ("LiabilitiesNoncurrent",),
    "other_noncurrent_liabilities": ("OtherLiabilitiesNoncurrent",),
    "income_taxes_payable_noncurrent": ("AccruedIncomeTaxesNoncurrent",),
    "total_liabilities": ("Liabilities",),
    "equity_attributable_to_parent": ("StockholdersEquity",),
    "total_equity": ("StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",),
    "retained_earnings": ("RetainedEarningsAccumulatedDeficit",),
    "noncontrolling_interest": ("MinorityInterest",),
    "preferred_stock_book_value": ("PreferredStockValue",),
    "liabilities_and_equity": ("LiabilitiesAndStockholdersEquity",),
    "current_debt": ("DebtCurrent",),
    "current_portion_long_term_debt": ("LongTermDebtCurrent",),
    "short_term_borrowings": ("ShortTermBorrowings",),
    "commercial_paper": ("CommercialPaper",),
    "long_term_debt": ("LongTermDebtNoncurrent",),
    # Debt totals used only for the filed interest-rate proxy in the debt cost.
    "long_term_debt_including_current": ("LongTermDebt",),
    "long_term_debt_and_lease_obligations": ("LongTermDebtAndCapitalLeaseObligations",),
    "total_debt": ("DebtLongtermAndShorttermCombinedAmount",),
    "debt_instrument_face_amount": ("DebtInstrumentFaceAmount",),
    "notes_payable_current": ("NotesPayableCurrent",),
    "long_term_notes_and_loans": ("LongTermNotesAndLoans",),
    "finance_lease_current": ("FinanceLeaseLiabilityCurrent",),
    "finance_lease_noncurrent": ("FinanceLeaseLiabilityNoncurrent",),
    "operating_lease_current": ("OperatingLeaseLiabilityCurrent",),
    "operating_lease_noncurrent": ("OperatingLeaseLiabilityNoncurrent",),
    "current_portion_long_term_debt_and_finance_leases": ("LongTermDebtAndCapitalLeaseObligationsCurrent",),
    "long_term_debt_and_finance_leases": ("LongTermDebtAndCapitalLeaseObligationsNoncurrent",),
    "ending_shares": ("CommonStockSharesOutstanding",),
}

_SHARE_METRICS = {"basic_shares", "diluted_shares", "ending_shares"}
_METRICS = _DURATION_METRICS | _INSTANT_METRICS
_FISCAL_ANCHORS = ("revenue", "operating_income", "net_income", "operating_cash_flow", "income_taxes")
_ANNUAL_FORMS = {"10-K", "10-K/A"}
_INTERIM_FORMS = {"10-Q", "10-Q/A"}
_NOT_REPORTED = "not_reported_in_eligible_annual_filings"
_NOT_REPORTED_INTERIM = "not_reported_in_eligible_interim_filings"


def _metric_unit(metric):
    if metric in _SHARE_METRICS:
        return "shares"
    return "USD/shares" if metric == "diluted_eps" else "USD"


def _date(value):
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    return None


def _eligible_filings(filings, as_of, forms):
    by_accession = {}
    for filing in filings:
        if filing.get("form") not in forms:
            continue
        filed = _date(filing.get("filed_on"))
        end = _date(filing.get("period_end"))
        accession = filing.get("accession")
        url = filing.get("url")
        if filed is None or end is None or not end <= filed <= as_of or not accession or not url:
            continue
        normalized = {
            "form": filing["form"], "accession": accession, "filed_on": filed.isoformat(),
            "period_end": end.isoformat(), "url": url,
        }
        if accession in by_accession and by_accession[accession] != normalized:
            raise ValueError(f"Conflicting filing metadata for accession {accession}.")
        by_accession[accession] = normalized
    return by_accession


def _annual_duration(start, end):
    return 335 <= (end - start).days + 1 <= 395


def _interim_duration(start, end):
    return 70 <= (end - start).days + 1 <= 310


def _collect_observations(taxonomy, by_accession, period_ends, eligible_duration):
    # Keys are (metric, fiscal end) then accession then tag. The SEC API
    # supplies entity-wide facts, but distinct durations can share an end date.
    observations = defaultdict(lambda: defaultdict(lambda: defaultdict(set)))
    for metric, tags in _METRICS.items():
        unit = _metric_unit(metric)
        for tag in tags:
            for fact in taxonomy.get(tag, {}).get("units", {}).get(unit, []):
                accession = fact.get("accn")
                filing = by_accession.get(accession)
                if not filing or fact.get("form") != filing["form"] or fact.get("filed") != filing["filed_on"]:
                    continue
                end = _date(fact.get("end"))
                if end not in period_ends or fact["end"] > filing["period_end"]:
                    continue
                start = _date(fact.get("start"))
                if metric in _DURATION_METRICS:
                    if start is None or not eligible_duration(start, end):
                        continue
                elif "start" in fact:
                    continue
                value = fact.get("val")
                if type(value) not in {int, float} or not isfinite(value):
                    continue
                observations[metric, end][accession][tag].add((start, value))
    return observations


def _period_start(end, prior_end, own_accessions, observations):
    if prior_end:
        start = prior_end + timedelta(days=1)
        if _annual_duration(start, end):
            return start, None
        if (end - prior_end).days < 335:
            return None, "nonannual_transition_period"

    # Without the previous report date, use agreement across primary statements.
    # Give facts filed for the period itself priority over comparative disclosures.
    all_support = defaultdict(set)
    own_support = defaultdict(set)
    for metric in _FISCAL_ANCHORS:
        for accession, by_tag in observations.get((metric, end), {}).items():
            for values in by_tag.values():
                for start, _ in values:
                    all_support[start].add(metric)
                    if accession in own_accessions:
                        own_support[start].add(metric)
    support = own_support or all_support
    if not support:
        return None, "annual_start_unavailable"
    strongest = max(map(len, support.values()))
    starts = [start for start, metrics in support.items() if len(metrics) == strongest]
    if len(starts) != 1:
        return None, "ambiguous_annual_start"
    return starts[0], None


def _filing_priority(filing):
    return filing["filed_on"], filing["period_end"], filing["form"].endswith("/A"), filing["accession"]


def _choose_fact(metric, end, start, observations, by_accession, not_reported):
    candidates = []
    for accession, by_tag in observations.get((metric, end), {}).items():
        for tag in _METRICS[metric]:
            values = {value for observed_start, value in by_tag.get(tag, ()) if start == observed_start}
            if values:
                candidates.append((by_accession[accession], tag, values))
                break
    if not candidates:
        return None, None, not_reported

    priority = max(_filing_priority(filing) for filing, _, _ in candidates)
    latest = [candidate for candidate in candidates if _filing_priority(candidate[0]) == priority]
    filing, tag, values = min(latest, key=lambda candidate: candidate[0]["accession"])
    if len(values) != 1 or any(values != alternative for _, _, alternative in latest):
        return None, None, "conflicting_values_in_latest_eligible_filing"
    source = {
        **filing, "taxonomy": "us-gaap", "tag": tag, "unit": _metric_unit(metric),
        "period_start": start.isoformat() if start else None, "period_end": end.isoformat(),
        "filing_period_end": filing["period_end"],
        "superseded": [{"accession": earlier["accession"], "filed_on": earlier["filed_on"],
                        "url": earlier["url"], "tag": earlier_tag, "values": sorted(earlier_values)}
                       for earlier, earlier_tag, earlier_values in candidates
                       if earlier["accession"] != filing["accession"] and earlier_values != values],
    }
    return next(iter(values)), source, None


OPERATING_INCOME_PROXY = ("Analyst proxy: the filing presents no operating income subtotal. Operating income is "
                          "income before taxes plus interest expense, less interest income and other nonoperating "
                          "income, all from the same filing.")
DEPRECIATION_ONLY_PROXY = ("Analyst proxy: the filing tags depreciation but no total D&A or separate amortization. "
                           "D&A uses filed depreciation, which may understate amortization.")


def _derive(metrics, sources, issues, name, components, *, derivation="arithmetic", basis=None):
    # A reported conflict takes priority over a component-derived substitute.
    if issues.get(name) not in {_NOT_REPORTED, _NOT_REPORTED_INTERIM}:
        return
    if any(metrics[component] is None for component, _ in components):
        return
    # Do not combine values from separate filings, whose accounting basis may
    # differ after recasts. A derived component carries its own accession.
    accessions = {sources[component]["accession"] for component, _ in components}
    if len(accessions) == 1:
        metrics[name] = sum(metrics[component] * sign for component, sign in components)
        first_source = sources[components[0][0]]
        sources[name] = {
            "unit": "USD", "derivation": derivation, "accession": first_source["accession"],
            "form": first_source["form"], "filed_on": first_source["filed_on"], "url": first_source["url"],
            "period_start": first_source["period_start"], "period_end": first_source["period_end"],
            "filing_period_end": first_source["filing_period_end"],
            "components": {component: {"coefficient": sign, "source": sources[component]} for component, sign in components},
        }
        if basis:
            sources[name]["basis"] = basis
        del issues[name]


def _check(end, metrics, name, total, components, *, same_filing_sources=None):
    keys = [total, *(metric for metric, _ in components)]
    values = [metrics[key] for key in keys]
    if any(value is None for value in values):
        return {"period_end": end, "name": name, "status": "unavailable", "difference": None, "tolerance": None}
    if same_filing_sources is not None and len({same_filing_sources[key]["accession"] for key in keys}) != 1:
        return {"period_end": end, "name": name, "status": "unavailable", "difference": None, "tolerance": None,
                "reason": "different_filing_accessions"}
    difference = metrics[total] - sum(metrics[metric] * sign for metric, sign in components)
    # Company Facts does not expose XBRL decimals. Allow up to $2 million
    # for totals rounded to whole millions, with a tighter bound at small scale.
    tolerance = min(2_000_000, max(1, 0.001 * max(map(abs, values))))
    return {
        "period_end": end, "name": name, "status": "pass" if abs(difference) <= tolerance else "fail",
        "difference": difference, "tolerance": tolerance,
    }


def _taxonomy(company_facts, cik):
    if not isinstance(cik, str) or not cik.isascii() or not cik.isdecimal() or len(cik) > 10 or int(cik) == 0:
        raise ValueError("cik must identify a nonzero SEC filer.")
    if "cik" in company_facts and str(company_facts["cik"]).lstrip("0") != cik.lstrip("0"):
        raise ValueError("Company Facts CIK does not match the requested filer.")
    return company_facts.get("facts", {}).get("us-gaap", {})


def _record_period(result, end, start, start_issue, observations, by_accession, own, not_reported):
    metrics, sources, issues = {}, {}, {}
    for metric in _METRICS:
        if metric in _DURATION_METRICS and start_issue:
            metrics[metric], issues[metric] = None, start_issue
            continue
        fact_start = start if metric in _DURATION_METRICS else None
        value, source, issue = _choose_fact(metric, end, fact_start, observations, by_accession, not_reported)
        metrics[metric] = value
        if source:
            sources[metric] = source
        else:
            issues[metric] = issue

    _derive(metrics, sources, issues, "income_before_taxes", (("domestic_income_before_taxes", 1), ("foreign_income_before_taxes", 1)))
    _derive(metrics, sources, issues, "depreciation_amortization", (("depreciation", 1), ("amortization_of_intangibles", 1)))
    if issues.get("amortization_of_intangibles") in {_NOT_REPORTED, _NOT_REPORTED_INTERIM}:
        _derive(metrics, sources, issues, "depreciation_amortization", (("depreciation", 1),),
                derivation="depreciation_only_proxy", basis=DEPRECIATION_ONLY_PROXY)
    _derive(metrics, sources, issues, "operating_income",
            (("income_before_taxes", 1), ("interest_expense", 1), ("interest_income", -1), ("other_nonoperating_income", -1)),
            derivation="operating_income_proxy", basis=OPERATING_INCOME_PROXY)
    _derive(metrics, sources, issues, "total_liabilities", (("current_liabilities", 1), ("noncurrent_liabilities", 1)))
    _derive(metrics, sources, issues, "total_equity", (("equity_attributable_to_parent", 1), ("noncontrolling_interest", 1)))
    _derive(metrics, sources, issues, "current_debt", (("current_portion_long_term_debt", 1), ("short_term_borrowings", 1)))
    _derive(metrics, sources, issues, "long_term_debt", (("total_debt", 1), ("current_debt", -1)))
    _derive(metrics, sources, issues, "total_debt", (("current_debt", 1), ("long_term_debt", 1)))
    end_iso = end.isoformat()
    independent_balance = _check(end_iso, metrics, "balance_sheet", "total_assets", (("total_liabilities", 1), ("total_equity", 1)))
    # Recover the reported residual after testing the independent identity.
    _derive(metrics, sources, issues, "total_equity", (("liabilities_and_equity", 1), ("total_liabilities", -1)))
    result["periods"].append({
        "period_end": end_iso, "period_start": start.isoformat() if start else None,
        "filing": max(own, key=lambda filing: (_filing_priority(filing), filing["accession"])),
        "metrics": metrics, "sources": sources, "metric_issues": dict(issues),
    })
    result["missing"].extend({"period_end": end_iso, "metric": metric, "reason": issue} for metric, issue in issues.items())
    result["checks"].extend((
        _check(end_iso, metrics, "assets_vs_reported_liabilities_and_equity", "total_assets", (("liabilities_and_equity", 1),)),
        independent_balance,
        _check(end_iso, metrics, "gross_profit", "gross_profit", (("revenue", 1), ("cost_of_revenue", -1))),
        _check(end_iso, metrics, "net_income_attribution", "consolidated_net_income",
               (("net_income", 1), ("net_income_attributable_to_noncontrolling_interest", 1))),
        _check(end_iso, metrics, "ppe_carrying_value", "net_ppe",
               (("gross_ppe", 1), ("accumulated_depreciation", -1)), same_filing_sources=sources),
        _check(end_iso, metrics, "cash_flow_change_in_cash_and_restricted_cash", "change_in_cash_and_restricted_cash",
               (("operating_cash_flow", 1), ("investing_cash_flow", 1), ("financing_cash_flow", 1),
                ("exchange_effect_on_cash_and_restricted_cash", 1)), same_filing_sources=sources),
    ))


def normalize_annual_financials(company_facts: dict, cik: str, as_of: date, filings: list[dict], *, years: int = 5) -> dict:
    """Return ordered 10-K periods, standardized values, evidence, and checks.

    Amounts use native USD and share counts. Expenses and payment outflows keep
    the source tag's sign. Annual comparisons reflect the latest eligible filing
    at the cutoff, including restatements, with separate evidence per metric.
    Company-specific tags, quarters, non-USD facts, and estimates are excluded.
    """
    if type(years) is not int or years < 1:
        raise ValueError("years must be a positive integer.")
    taxonomy = _taxonomy(company_facts, cik)
    by_accession = _eligible_filings(filings, as_of, _ANNUAL_FORMS)
    by_period = defaultdict(list)
    for filing in by_accession.values():
        by_period[date.fromisoformat(filing["period_end"])].append(filing)
    ends = sorted(by_period)
    selected = ends[-years:]
    observations = _collect_observations(taxonomy, by_accession, set(selected), _annual_duration)
    result = {"periods": [], "missing": [], "checks": []}
    if len(selected) < years:
        result["missing"].append({
            "period_end": None, "metric": "annual_periods", "reason": "insufficient_annual_filings",
            "requested": years, "available": len(selected),
        })
    for index, end in enumerate(ends):
        if end not in selected:
            continue
        own = by_period[end]
        prior_end = ends[index - 1] if index else None
        start, start_issue = _period_start(end, prior_end, {filing["accession"] for filing in own}, observations)
        _record_period(result, end, start, start_issue, observations, by_accession, own, _NOT_REPORTED)
    return result


def normalize_latest_interim_financials(company_facts: dict, cik: str, as_of: date, filings: list[dict]) -> dict:
    """Return the most recent 10-Q balance sheet and matching fiscal YTD flows.

    Include annual filings to determine the exact first day of the fiscal year
    and detect when the latest 10-K supersedes the available 10-Q. With no recent
    annual filing, use the unique same-filing cash flow statement period, whose
    interim reporting basis is year to date. Company Facts does not indicate a
    reported zero when the observation is absent.
    """
    taxonomy = _taxonomy(company_facts, cik)
    by_accession = _eligible_filings(filings, as_of, _INTERIM_FORMS)
    annual = _eligible_filings(filings, as_of, _ANNUAL_FORMS)
    result = {"periods": [], "missing": [], "checks": [], "status": "unavailable"}
    if not by_accession:
        result["missing"].append({"period_end": None, "metric": "interim_period", "reason": "no_eligible_interim_filing"})
        return result

    end_iso = max(filing["period_end"] for filing in by_accession.values())
    latest_annual_end = max((filing["period_end"] for filing in annual.values()), default=None)
    if latest_annual_end is not None and latest_annual_end >= end_iso:
        result["status"] = "superseded_by_annual"
        result["latest_annual_period_end"] = latest_annual_end
        result["missing"].append({"period_end": end_iso, "metric": "interim_period", "reason": "annual_period_at_or_after_latest_interim"})
        return result

    end = date.fromisoformat(end_iso)
    own = {accession: filing for accession, filing in by_accession.items() if filing["period_end"] == end_iso}
    observations = _collect_observations(taxonomy, own, {end}, _interim_duration)
    start = date.fromisoformat(latest_annual_end) + timedelta(days=1) if latest_annual_end else None
    start_issue = None
    if start is None or not _interim_duration(start, end):
        cash_flow_starts = {
            start for by_tag in observations.get(("operating_cash_flow", end), {}).values()
            for values in by_tag.values() for start, _ in values
        }
        if len(cash_flow_starts) == 1:
            start = next(iter(cash_flow_starts))
        else:
            start = None
            start_issue = "ambiguous_ytd_start" if cash_flow_starts else "ytd_start_unavailable"
    _record_period(result, end, start, start_issue, observations, own, own.values(), _NOT_REPORTED_INTERIM)
    result["status"] = "available" if any(value is not None for value in result["periods"][0]["metrics"].values()) else "unavailable"
    return result

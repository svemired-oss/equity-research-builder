"""Supplement the operating model with explicitly identified filing facts."""

from datetime import date
from math import isclose
import re

from .filing_tables import reconciled_current_cash
from .filing_xbrl import load_filing_instance
from .operating_research import ResearchGap
from .sec_guidance import _ReleaseText


def _single(facts, names):
    values = {fact.value for fact in facts if fact.concept in names}
    if len(values) > 1:
        raise ResearchGap("Conflicting facts were found in the filed balance-sheet bridge.")
    return next(iter(values), None)


def _reconcile_current_assets(balances, metrics):
    if (metrics.get("short_term_investments") is not None
            or metrics.get("available_for_sale_debt_securities_current") is not None):
        return False
    categories = (
        ("cash_and_equivalents", {"CashAndCashEquivalentsAtCarryingValue"}),
        ("receivables", {"AccountsReceivableNetCurrent", "AccountsNotesAndLoansReceivableNetCurrent", "ReceivablesNetCurrent"}),
        (None, {"OtherReceivablesNetCurrent"}),
        (None, {"ContractWithCustomerAssetCurrent", "ContractWithCustomerAssetNetCurrent"}),
        ("inventory", {"InventoryNet"}),
        ("prepaid_and_other_current_assets", {"PrepaidExpenseAndOtherAssetsCurrent", "OtherAssetsCurrent"}),
    )
    items = []
    for key, category in categories:
        amount = _single(balances, {f"us-gaap:{name}" for name in category})
        if amount is None and key is not None:
            amount = metrics.get(key)
        items.append(amount)
    observed = [item for item in items if item is not None]
    return metrics.get("current_assets") is not None and items[0] is not None and len(observed) >= 3 and isclose(
        sum(observed), metrics["current_assets"], rel_tol=0, abs_tol=1e6)


def _walmart_cash(balances, metrics):
    noncash = [_single(balances, {f"us-gaap:{tag}"}) for tag in (
        "ReceivablesNetCurrent", "InventoryNet", "PrepaidExpenseAndOtherAssetsCurrent",
    )]
    total = metrics.get("current_assets")
    if total is None or any(value is None for value in noncash):
        return None
    matched = {fact.value for fact in balances if fact.concept == "us-gaap:CashAndCashEquivalentsAtCarryingValue"
               and isclose(fact.value + sum(noncash), total, rel_tol=0, abs_tol=1e6)}
    return matched.pop() if len(matched) == 1 else None


def enrich_operating_bridge(packet, client):
    """Recover scoped facts the consolidated Company Facts mapping cannot identify."""
    issuer = packet["issuer"]
    ticker = issuer["ticker"]
    known_basis = (ticker, issuer["cik"]) in {("PM", "0001413329"), ("NVDA", "0001045810"),
                                            ("ADBE", "0000796343"), ("MCD", "0000063908"), ("HD", "0000354950"),
                                            ("KO", "0000021344"), ("WMT", "0000104169"), ("WM", "0000823768"),
                                            ("INTU", "0000896878"), ("TT", "0001466258"), ("UNP", "0000100885")}
    as_of = date.fromisoformat(packet["as_of"])
    annual = packet["actuals"]["periods"][-1]
    interim = packet["interim_actuals"]["periods"]
    if (ticker, issuer["cik"]) == ("WMT", "0000104169"):
        for historical in (*packet["actuals"]["periods"], *interim[-1:]):
            values = historical["metrics"]
            sources = historical["sources"]
            reported = values.get("depreciation_amortization_accretion")
            if values.get("depreciation_amortization") is None and reported is not None:
                values["depreciation_amortization"] = reported
                sources["depreciation_amortization"] = sources["depreciation_amortization_accretion"]
    lease_note = None
    for period in [annual, *interim[-1:]]:
        metrics = period["metrics"]
        recover_debt = metrics.get("long_term_debt") is None and metrics.get("long_term_debt_and_finance_leases") is None
        recover_cash = metrics.get("short_term_investments") is None and metrics.get("available_for_sale_debt_securities_current") is None
        recover_lease = any(metrics.get(key) is None for key in ("finance_lease_current", "finance_lease_noncurrent"))
        recover_ppe = metrics.get("net_ppe") is None
        if not (known_basis or recover_debt or recover_cash or recover_lease or recover_ppe
                or metrics.get("cash_and_equivalents") is None):
            continue
        instance = load_filing_instance(client, issuer["cik"], period["filing"]["accession"])
        end = date.fromisoformat(period["period_end"])
        facts = [fact for fact in instance.facts if fact.start is None and not fact.dimensions]
        balances = [fact for fact in facts if fact.end == end and fact.unit == "USD"]
        notes = []
        if recover_cash and (ticker, issuer["cik"]) == ("TT", "0001466258") and metrics.get("cash_and_equivalents") is None:
            # The September 10-Q uses a broader cash XBRL concept on its face.
            # The reconciled current-asset subtotal confirms the amount there.
            stated = _single(balances, {
                "us-gaap:CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsIncludingDisposalGroupAndDiscontinuedOperations",
            })
            if stated is not None and _reconcile_current_assets(balances, {**metrics, "cash_and_equivalents": stated}):
                metrics["cash_and_equivalents"] = stated
                metrics["short_term_investments"] = 0
                notes.append("The filed cash line and other reported operating asset lines reconcile to total current assets.")
        elif recover_cash and (ticker, issuer["cik"]) == ("WMT", "0000104169"):
            scoped_cash = _walmart_cash(balances, metrics)
            if scoped_cash is not None:
                metrics["cash_and_equivalents"] = scoped_cash
                metrics["short_term_investments"] = 0
                notes.append("The reported current-asset lines identify the consolidated cash balance and reconcile to total current assets.")
        elif recover_cash and ticker not in ("PM", "NVDA", "KO", "WM") and _reconcile_current_assets(balances, metrics):
            metrics["short_term_investments"] = 0
            notes.append("Current assets reconcile from the tagged balance sheet without a separate investment line.")
        if recover_ppe:
            pp_and_e = _single(balances, {
                "us-gaap:PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization",
            })
            if pp_and_e is not None:
                metrics["net_ppe"] = pp_and_e
                notes.append("Reported net property and equipment includes finance lease right-of-use assets.")
        if recover_debt:
            combined_debt = _single(balances, {"us-gaap:LongTermDebtAndCapitalLeaseObligations"})
            if combined_debt is not None and metrics.get("current_portion_long_term_debt_and_finance_leases") is not None:
                metrics["long_term_debt_and_finance_leases"] = combined_debt
                notes.append("Filed term debt includes finance leases on both the current and noncurrent lines.")
            else:
                standard_debt = _single(balances, {"us-gaap:LongTermDebt"})
                if standard_debt is not None:
                    metrics["long_term_debt"] = standard_debt
                    notes.append("Noncurrent borrowing comes from the consolidated carrying amount tagged in the filing.")
        if ticker == "PM":
            operating_assets = [_single(balances, {f"us-gaap:{concept}"}) for concept in (
                "CashAndCashEquivalentsAtCarryingValue", "AccountsReceivableNetCurrent",
                "OtherReceivablesNetCurrent", "InventoryNet", "OtherAssetsCurrent",
            )]
            if all(value is not None for value in operating_assets) and isclose(
                    sum(operating_assets), metrics["current_assets"], abs_tol=100_000):
                metrics["short_term_investments"] = 0
                notes.append("No separate short-term investments are included. The filed current assets reconcile from "
                             "cash, trade and other receivables, inventory and other current assets.")
            metrics["long_term_debt_and_finance_leases"] = _single(balances, {
                "us-gaap:LongTermDebtAndCapitalLeaseObligations",
            })
            period["use_nci_book_value"] = True
            notes.append("The valuation deducts reported noncontrolling interest at book value as a proxy. "
                         "The reported term-debt lines already include finance leases.")
        elif ticker == "NVDA":
            # The annual statement uses one custom current-securities total.
            # The interim statement separately tags debt and equity securities.
            combined = _single(balances, {f"{{http://www.nvidia.com/{end:%Y%m%d}}}MarketableSecuritiesAndEquitySecuritiesFVNI"})
            if combined is None:
                debt = _single(balances, {"us-gaap:DebtSecuritiesCurrent"})
                equity = _single(balances, {"us-gaap:EquitySecuritiesFvNi"})
                if debt is not None and equity is not None:
                    combined = debt + equity
            if combined is not None and all(metrics.get(key) is not None for key in (
                    "cash_and_equivalents", "receivables", "inventory", "prepaid_and_other_current_assets", "current_assets")):
                other_assets = sum(metrics[key] for key in (
                    "cash_and_equivalents", "receivables", "inventory", "prepaid_and_other_current_assets"))
                if not isclose(combined + other_assets, metrics["current_assets"], abs_tol=100_000):
                    raise ResearchGap("NVIDIA's current securities do not reconcile to the filed balance sheet.")
                metrics["short_term_investments"] = combined
            period["reported_debt_basis"] = True
            notes.append("The equity bridge uses reported current securities at carrying value, including securities subject "
                         "to resale restrictions. Noncurrent investments and any unquantified finance-lease debt are excluded.")
        elif (ticker, issuer["cik"]) == ("ADBE", "0000796343"):
            period["reported_debt_basis"] = True
            notes.append("The model deducts reported debt and treats disclosed operating leases as operating obligations.")
        elif (ticker, issuer["cik"]) == ("INTU", "0000896878"):
            securities = _single(balances, {"us-gaap:DebtSecuritiesAvailableForSaleExcludingAccruedInterestCurrent"})
            if securities is not None:
                metrics["short_term_investments"] = securities
                notes.append("The equity bridge uses corporate current securities and unrestricted cash. Customer funds have "
                             "a matching balance-sheet liability and remain outside the equity bridge.")
            if all(metrics.get(key) is None for key in ("finance_lease_current", "finance_lease_noncurrent")):
                period["reported_debt_basis"] = True
                notes.append("Reported borrowings enter the equity bridge. Disclosed operating leases remain in operations.")
        elif (ticker, issuer["cik"]) == ("TT", "0001466258"):
            if all(metrics.get(key) is None for key in ("finance_lease_current", "finance_lease_noncurrent")):
                period["reported_debt_basis"] = True
                notes.append("The bridge uses reported borrowings. The annual lease note describes the operating lease portfolio.")
            minority = _single(balances, {"us-gaap:MinorityInterest"})
            equity, parent = (metrics.get(key) for key in ("total_equity", "equity_attributable_to_parent"))
            if None not in (minority, equity, parent) and minority >= 0 and isclose(
                    equity, parent + minority, rel_tol=0, abs_tol=1e6):
                metrics["noncontrolling_interest"] = minority
                period["use_nci_book_value"] = True
                notes.append("The bridge deducts filed minority interests at carrying value.")
        elif (ticker, issuer["cik"]) == ("UNP", "0000100885"):
            securities = _single(balances, {"us-gaap:HeldToMaturitySecuritiesCurrent"})
            noncash = [_single(balances, {f"us-gaap:{name}"}) for name in
                       ("AccountsReceivableNetCurrent", "MaterialsSuppliesAndOther", "OtherAssetsCurrent")]
            cash = metrics.get("cash_and_equivalents")
            total = metrics.get("current_assets")
            if None not in (cash, securities, total, *noncash) and isclose(
                    cash + securities + sum(noncash), total, rel_tol=0, abs_tol=1e6):
                metrics["short_term_investments"] = securities
                notes.append("Filed short-term investments reconcile to current assets alongside cash, receivables, materials and supplies.")
            current = _single(balances, {"us-gaap:LongTermDebtAndCapitalLeaseObligationsCurrent"})
            noncurrent = _single(balances, {"us-gaap:LongTermDebtAndCapitalLeaseObligations"})
            payables = _single(balances, {"us-gaap:AccountsPayableAndAccruedLiabilitiesCurrent"})
            total = metrics.get("current_liabilities")
            if None not in (current, noncurrent, payables, total) and isclose(current + payables, total, rel_tol=0, abs_tol=1e6):
                metrics["current_portion_long_term_debt_and_finance_leases"] = current
                metrics["long_term_debt_and_finance_leases"] = noncurrent
                if metrics.get("short_term_borrowings") is None:
                    metrics["short_term_borrowings"] = 0
                period["reported_debt_basis"] = True
                notes.append("The face of the balance sheet reports current and noncurrent debt including finance leases.")
        elif (ticker, issuer["cik"]) == ("WM", "0000823768"):
            operating_current = [_single(balances, {f"us-gaap:{tag}"}) for tag in
                                 ("AccountsReceivableNetCurrent", "OtherReceivablesNetCurrent", "Supplies", "OtherAssetsCurrent")]
            cash, total = (metrics.get(key) for key in ("cash_and_equivalents", "current_assets"))
            if None not in (cash, total, *operating_current) and isclose(
                    cash + sum(operating_current), total, rel_tol=0, abs_tol=1e6):
                metrics["short_term_investments"] = 0
                notes.append("Consolidated current assets reconcile from filed cash and four operating asset lines. "
                             "Restricted funds are carried outside the cash bridge.")
            combined = _single(balances, {"us-gaap:LongTermDebtAndCapitalLeaseObligations"})
            if combined is not None and metrics.get("current_debt") is not None:
                metrics.pop("long_term_debt_and_finance_leases", None)
                metrics["long_term_debt"] = combined
                period["reported_debt_basis"] = True
                notes.append("Reported current and noncurrent debt already include finance leases.")
            minority = _single(balances, {"us-gaap:MinorityInterest"})
            equity, parent = (metrics.get(key) for key in ("total_equity", "equity_attributable_to_parent"))
            if None not in (minority, equity, parent) and minority >= 0 and isclose(
                    equity, parent + minority, rel_tol=0, abs_tol=1e6):
                metrics["noncontrolling_interest"] = minority
                period["use_nci_book_value"] = True
                notes.append("The equity bridge deducts the filed minority interest at carrying value.")
        elif (ticker, issuer["cik"]) == ("KO", "0000021344"):
            short = _single(balances, {"us-gaap:OtherShortTermInvestments"})
            marketable = _single(balances, {"us-gaap:MarketableSecurities"})
            total = _single(balances, {"us-gaap:CashCashEquivalentsAndShortTermInvestments"})
            cash = metrics.get("cash_and_equivalents")
            if None not in (short, marketable, total, cash) and isclose(cash + short, total, rel_tol=0, abs_tol=1e6):
                metrics["short_term_investments"] = short + marketable
                notes.append("The balance sheet separately presents short-term investments and current marketable securities. "
                             "The cash subtotal confirms the two lines are separate.")
            notes_payable = _single(balances, {"us-gaap:NotesAndLoansPayable"})
            if notes_payable is not None:
                metrics["short_term_borrowings"] = notes_payable
                notes.append("The filing presents loans and notes payable separately from current maturities of long-term debt.")
            noncontrolling = _single(balances, {"us-gaap:MinorityInterest"})
            equity, parent = (metrics.get(key) for key in ("total_equity", "equity_attributable_to_parent"))
            if None not in (equity, parent, noncontrolling) and noncontrolling >= 0 and isclose(
                    equity, parent + noncontrolling, rel_tol=0, abs_tol=1e6):
                metrics["noncontrolling_interest"] = noncontrolling
                period["use_nci_book_value"] = True
                notes.append("The bridge uses consolidated minority equity at reported carrying value. "
                             "This is a proxy for the value of the outside holders' claims.")
            affiliates = _single(balances, {"us-gaap:EquityMethodInvestments"})
            if affiliates is not None and affiliates >= 0:
                period["nonoperating_equity_investments"] = affiliates
                notes.append("Equity-method investments enter the bridge at reported carrying value. "
                             "Their earnings are outside consolidated operating profit.")
        elif (ticker, issuer["cik"]) == ("WMT", "0000104169"):
            current_term = metrics.get("current_portion_long_term_debt")
            noncurrent_term = metrics.get("long_term_debt")
            short = metrics.get("short_term_borrowings")
            total = metrics.get("total_debt")
            if None not in (current_term, noncurrent_term, short, total) and isclose(
                    total, current_term + noncurrent_term, rel_tol=0, abs_tol=1e6):
                metrics["total_debt"] = current_term + noncurrent_term + short
                notes.append("The filed borrowing subtotal covers term debt. The valuation also includes "
                             "the separately reported short-term borrowings.")
            redeemable = _single(balances, {"us-gaap:RedeemableNoncontrollingInterestEquityCarryingAmount"})
            equity = metrics.get("total_equity")
            parent = metrics.get("equity_attributable_to_parent")
            if redeemable is not None and equity is not None and parent is not None and equity >= parent:
                metrics["noncontrolling_interest"] = equity - parent + redeemable
                period["use_nci_book_value"] = True
                assets, liabilities = metrics.get("total_assets"), metrics.get("total_liabilities")
                if (assets is not None and liabilities is not None
                        and isclose(assets, liabilities + redeemable + equity, rel_tol=0, abs_tol=1e6)):
                    metrics["total_liabilities"] = liabilities + redeemable
                notes.append("The equity bridge deducts both reported redeemable noncontrolling interests and the "
                             "book equity attributable to other holders of consolidated subsidiaries.")
        elif (ticker, issuer["cik"]) == ("HD", "0000354950") and metrics.get("short_term_borrowings") is None:
            commercial_paper = _single(balances, {"us-gaap:CommercialPaper"})
            if commercial_paper is not None:
                metrics["short_term_borrowings"] = commercial_paper
                notes.append("Commercial paper is the separately reported short-term debt line.")
        elif (ticker, issuer["cik"]) == ("MCD", "0000063908"):
            # The annual debt note classifies maturities backed by the committed
            # revolver as long-term.
            maturity = _single(balances, {"us-gaap:LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths"})
            if maturity == 0 and metrics.get("long_term_debt") is not None:
                metrics["current_debt"] = 0
                notes.append("Current maturities supported by the line of credit are classified as long-term in the filing.")
            if period is not annual and any(metrics.get(key) is None for key in ("finance_lease_current", "finance_lease_noncurrent")):
                raise ResearchGap("McDonald's interim balance sheet combines operating and finance leases. "
                                  "The latest separate finance lease liability is needed to finish the live valuation bridge.")
        if recover_lease and not period.get("reported_debt_basis") and ticker != "PM":
            if lease_note is None:
                lease_note = any(re.search(r"\b(?:we do not have significant financing leases|"
                                           r"financ(?:e|ing) leases? (?:are|were) not (?:significant|material))\b",
                                           paragraph, re.I)
                                 for paragraph in _ReleaseText(client.get_text(annual["filing"]["url"])).blocks)
            if lease_note and all(metrics.get(key) is None for key in ("finance_lease_current", "finance_lease_noncurrent")):
                period["reported_debt_basis"] = True
                notes.append("The annual filing identifies finance leases as immaterial. The bridge uses reported borrowing "
                             "and excludes any unquantified finance lease liability.")
        if not known_basis and (metrics.get("cash_and_equivalents") is None or all(
                metrics.get(key) is None for key in ("short_term_investments", "available_for_sale_debt_securities_current"))):
            cash_bridge = reconciled_current_cash(client.get_text(instance.url), metrics.get("current_assets"),
                                                  metrics.get("cash_and_equivalents"))
            if cash_bridge is not None:
                cash, securities = cash_bridge
                reported = next((metrics[key] for key in ("short_term_investments", "available_for_sale_debt_securities_current")
                                 if metrics.get(key) is not None), None)
                if reported is None or isclose(reported, securities, rel_tol=0, abs_tol=1e6):
                    metrics["cash_and_equivalents"] = cash
                    metrics["short_term_investments"] = securities
                    notes.append("Cash and any separately presented short-term securities reconcile to the filed current-asset subtotal.")
        if metrics.get("ending_shares") is None:
            share_facts = [fact for fact in facts if fact.concept == "dei:EntityCommonStockSharesOutstanding"
                           and fact.unit == "shares" and end <= fact.end <= as_of]
            if share_facts:
                cover_date = max(fact.end for fact in share_facts)
                metrics["ending_shares"] = _single([fact for fact in share_facts if fact.end == cover_date],
                                                   {"dei:EntityCommonStockSharesOutstanding"})
                notes.append(f"The valuation uses the filing cover's {cover_date} common share count.")
        period["operating_bridge_source"] = instance.url
        period["operating_bridge_notes"] = notes

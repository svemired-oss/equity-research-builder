"""Point-in-time checks for issuer-neutral annual XBRL normalization."""

from datetime import date
import unittest

from equity_research.sec_financials import normalize_annual_financials, normalize_latest_interim_financials


def filing(accession, end, filed, form="10-K"):
    return {"accession": accession, "period_end": end, "filed_on": filed, "form": form,
            "url": f"https://www.sec.gov/filing/{accession}"}


def observation(filing, value, *, start=None, end=None):
    row = {"accn": filing["accession"], "form": filing["form"], "filed": filing["filed_on"],
           "end": end or filing["period_end"], "val": value}
    if start is not None:
        row["start"] = start
    return row


class AnnualFinancialsTests(unittest.TestCase):
    def test_non_calendar_periods_and_cutoff_for_comparative_restatement(self):
        old = filing("first", "2023-09-30", "2023-11-03")
        original = filing("second", "2024-09-28", "2024-11-01")
        latest = filing("third", "2025-09-27", "2025-10-31")
        future = filing("future", "2025-09-27", "2025-11-19", "10-K/A")
        quarter = filing("quarter", "2025-09-27", "2025-10-01", "10-Q")
        start, end = "2023-10-01", "2024-09-28"
        facts = {"cik": 1, "facts": {"us-gaap": {
            "Revenues": {"units": {"USD": [
                observation(original, 200, start=start),
                observation(latest, 202, start=start, end=end),
                observation(latest, 999, start="2024-06-30", end=end),
                observation(future, 999, start=start, end=end),
                observation(quarter, 999, start=start, end=end),
                observation(latest, 500, start="2024-09-29"),
            ]}},
            "WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": [
                observation(latest, 123, start="2024-09-29"),
            ]}},
        }}}
        result = normalize_annual_financials(facts, "0000000001", date(2025, 11, 12),
                                             [future, old, latest, quarter, original], years=2)
        first, second = result["periods"]
        self.assertEqual((first["period_end"], second["period_end"]), (end, "2025-09-27"))
        self.assertEqual((first["period_start"], second["period_start"]), (start, "2024-09-29"))
        self.assertEqual(first["metrics"]["revenue"], 202)
        self.assertEqual(first["filing"]["accession"], "second")
        self.assertEqual(first["sources"]["revenue"]["accession"], "third")
        self.assertEqual(second["sources"]["diluted_shares"]["unit"], "shares")

    def test_eligible_amendment_preserves_conflicts_and_reported_zero(self):
        original = filing("original", "2024-05-31", "2024-06-20")
        amended = filing("amended", "2024-05-31", "2024-08-23", "10-K/A")
        start = "2023-06-01"
        facts = {"facts": {"us-gaap": {
            "Revenues": {"units": {"USD": [
                observation(original, 100, start=start),
                observation(amended, 110, start=start),
                observation(amended, 115, start=start),
            ]}},
            "InventoryNet": {"units": {"USD": [observation(original, 0)]}},
            "CashAndCashEquivalentsAtCarryingValue": {"units": {"EUR": [observation(amended, 10)]}},
        }}}
        result = normalize_annual_financials(facts, "1", date(2024, 9, 1), [original, amended], years=1)
        period = result["periods"][0]
        issues = {issue["metric"]: issue["reason"] for issue in result["missing"]}
        self.assertIsNone(period["metrics"]["revenue"])
        self.assertEqual(issues["revenue"], "conflicting_values_in_latest_eligible_filing")
        self.assertEqual(period["metrics"]["inventory"], 0)
        self.assertNotIn("inventory", issues)
        self.assertIsNone(period["metrics"]["cash_and_equivalents"])

    def test_derived_values_and_balance_sheet_identity(self):
        report = filing("original", "2024-05-31", "2024-06-20")
        gaap = {}
        for tag, value in {"DebtCurrent": 10, "DebtLongtermAndShorttermCombinedAmount": 100,
                           "Assets": 500, "LiabilitiesAndStockholdersEquity": 600}.items():
            gaap[tag] = {"units": {"USD": [observation(report, value)]}}
        for tag, value in {"OperatingIncomeLoss": 30, "Depreciation": 5, "AmortizationOfIntangibleAssets": 8}.items():
            gaap[tag] = {"units": {"USD": [observation(report, value, start="2023-06-01")]}}
        result = normalize_annual_financials({"facts": {"us-gaap": gaap}}, "1", date(2024, 9, 1), [report], years=1)
        period = result["periods"][0]
        self.assertEqual(period["metrics"]["depreciation_amortization"], 13)
        self.assertEqual(period["metrics"]["long_term_debt"], 90)
        self.assertEqual(period["sources"]["long_term_debt"]["components"]["current_debt"]["coefficient"], -1)
        self.assertEqual(result["checks"][0]["status"], "fail")


class InterimFinancialsTests(unittest.TestCase):
    def test_ppe_and_cash_flow_tie_on_reported_scopes(self):
        annual = filing("year", "2026-05-31", "2026-06-25")
        quarter = filing("quarter", "2026-08-31", "2026-09-11", "10-Q")
        start = "2026-06-01"
        instant = {
            "PropertyPlantAndEquipmentGross": 153_596,
            "AccumulatedDepreciationDepletionAndAmortizationPropertyPlantAndEquipment": 25_751,
            "PropertyPlantAndEquipmentNet": 127_845,
            "ConstructionInProgressGross": 48_546,
            "CashAndCashEquivalentsAtCarryingValue": 36_369,
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsIncludingDisposalGroupAndDiscontinuedOperations": 38_934,
        }
        duration = {
            "NetCashProvidedByUsedInOperatingActivities": 23_103,
            "NetCashProvidedByUsedInInvestingActivities": -28_580,
            "NetCashProvidedByUsedInFinancingActivities": 13_111,
            "EffectOfExchangeRateOnCashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents": 11,
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalentsPeriodIncreaseDecreaseIncludingExchangeRateEffect": 7_645,
        }
        gaap = {tag: {"units": {"USD": [observation(quarter, value * 1_000_000)]}} for tag, value in instant.items()}
        gaap.update({tag: {"units": {"USD": [observation(quarter, value * 1_000_000, start=start)]}}
                     for tag, value in duration.items()})
        result = normalize_latest_interim_financials(
            {"facts": {"us-gaap": gaap}}, "1", date(2026, 11, 13), [annual, quarter],
        )
        metrics = result["periods"][0]["metrics"]
        self.assertEqual(metrics["cash_and_equivalents"], 36_369_000_000)
        self.assertEqual(metrics["cash_and_restricted_cash"], 38_934_000_000)
        self.assertEqual(metrics["construction_in_progress"], 48_546_000_000)
        checks = {check["name"]: check for check in result["checks"]}
        for name in ("ppe_carrying_value", "cash_flow_change_in_cash_and_restricted_cash"):
            self.assertEqual(checks[name]["status"], "pass")
            self.assertEqual(checks[name]["difference"], 0)

    def test_ytd_context_amendment_and_exact_balance_date(self):
        annual = filing("year", "2024-05-31", "2024-06-20")
        q1 = filing("q1", "2024-08-31", "2024-09-11", "10-Q")
        q2 = filing("q2", "2024-11-30", "2024-12-12", "10-Q")
        amended = filing("q2a", "2024-11-30", "2024-12-16", "10-Q/A")
        future = filing("future", "2025-02-28", "2025-03-15", "10-Q")
        gaap = {
            "Revenues": {"units": {"USD": [
                observation(q2, 33, start="2024-06-01"), observation(q2, 19, start="2024-09-01"),
                observation(amended, 34, start="2024-06-01"), observation(future, 50, start="2024-06-01"),
            ]}},
            "InventoryNet": {"units": {"USD": [observation(q1, 9)]}},
            "CashAndCashEquivalentsAtCarryingValue": {"units": {"USD": [observation(q2, 17)]}},
        }
        result = normalize_latest_interim_financials({"facts": {"us-gaap": gaap}}, "1", date(2024, 12, 20),
                                                     [q2, annual, future, amended, q1])
        period = result["periods"][0]
        self.assertEqual(result["status"], "available")
        self.assertEqual((period["period_start"], period["period_end"]), ("2024-06-01", "2024-11-30"))
        self.assertEqual(period["metrics"]["revenue"], 34)
        self.assertEqual(period["sources"]["revenue"]["accession"], "q2a")
        self.assertEqual(period["metrics"]["cash_and_equivalents"], 17)
        self.assertEqual(period["sources"]["cash_and_equivalents"]["accession"], "q2")
        self.assertIsNone(period["metrics"]["inventory"])

    def test_annual_supersedes_prior_interim_and_fallback_uses_cash_flow_start(self):
        annual = filing("year", "2024-05-31", "2024-06-20")
        q3 = filing("q3", "2024-02-29", "2024-03-15", "10-Q")
        gaap = {"NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [
            observation(q3, 12, start="2023-06-01"),
        ]}}}
        facts = {"facts": {"us-gaap": gaap}}
        interim = normalize_latest_interim_financials(facts, "1", date(2024, 5, 10), [q3, annual])
        self.assertEqual(interim["periods"][0]["period_start"], "2023-06-01")
        self.assertEqual(interim["periods"][0]["metrics"]["operating_cash_flow"], 12)
        superseded = normalize_latest_interim_financials(facts, "1", date(2024, 6, 25), [q3, annual])
        self.assertEqual(superseded["status"], "superseded_by_annual")
        self.assertEqual(superseded["periods"], [])

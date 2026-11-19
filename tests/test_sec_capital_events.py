from datetime import date
from unittest import TestCase

from equity_research.sec_capital_events import collect_capital_events
from equity_research.sec_client import filing_document_url


def _filing(number, *, filed="2026-11-04", form="8-K"):
    accession = f"0000123456-26-{number:06}"
    return {"accession": accession, "form": form, "filed_on": filed,
            "url": filing_document_url("123456", accession, "report.htm")}


class _Client:
    def __init__(self, documents):
        self.documents = documents
        self.requests = []

    def get_text(self, url):
        self.requests.append(url)
        if url not in self.documents:
            raise ValueError("unavailable")
        return self.documents[url]


class CapitalEventTests(TestCase):
    def test_recognizes_issuer_closing_date_and_separates_debt_principal_and_shares(self):
        report = _filing(1)
        notes = ("On November 4, 2026, Example Corp consummated the issuance and sale of "
                 "$2,000,000,000 principal amount of its 7.000% Notes due 2029 and $1.75 billion "
                 "principal amount of its 7.450% Notes due 2031 (collectively, the Notes).")
        common = ("On November 2, 2026, Example Corp entered into an agreement pursuant to which "
                  "Example Corp agreed to issue and sell 86,206,897 shares of its common stock, "
                  "par value $0.01 per share. The Common Stock Offering closed on November 4, 2026.")
        client = _Client({report["url"]: f"""<html><table><tr><td>Item&#8201;8.01</td><td>Other Events</td></tr></table>
            <p>{common}</p><p>{notes}</p><p>{notes}</p><p>Item 9.01 Financial Statements and Exhibits</p>
            <p>On November 4, 2026, Example Corp issued and sold 5,000 shares of its common stock.</p></html>"""})
        result = collect_capital_events(client, [report, report], balance_date="2026-08-31", as_of=date(2026, 11, 13))
        self.assertEqual(client.requests, [report["url"]])
        self.assertEqual(result["status"], "not_reconciled")
        self.assertEqual(result["missing"], [])
        common, debt = result["events"]
        self.assertEqual(common["shares_issued"], 86_206_897)
        self.assertEqual(common["closed_on"], "2026-11-04")
        self.assertEqual(debt["principal"], 3_750_000_000)
        self.assertEqual(debt["tranches"], [
            {"principal": 2_000_000_000, "coupon_percent": 7.0, "maturity_year": 2029},
            {"principal": 1_750_000_000, "coupon_percent": 7.45, "maturity_year": 2031},
        ])
        self.assertEqual(debt["accession"], report["accession"])
        self.assertEqual(debt["url"], report["url"])
        self.assertNotIn("net_proceeds", debt)

    def test_ignores_intended_issuance_and_nonissuer_sales(self):
        pending, insider, incomplete = (_filing(i) for i in range(1, 4))
        client = _Client({
            pending["url"]: "<p>Item 1.01</p><p>Example Corp agreed to issue and sell 3,000 shares of its "
            "common stock. The offering is expected to close on November 4, 2026.</p>",
            insider["url"]: "<p>Item 8.01 Other Events</p><p>Example Corp announced its Chair cancelled "
            "his 10b5-1 Plan to sell stock. No stock was sold.</p>",
            incomplete["url"]: "<p>Item 2.03</p><p>On November 4, 2026, Example Corp consummated the issuance and sale of "
            "$2 billion principal amount of its 7% Notes due 2029 and $1 billion of floating-rate notes.</p>",
        })
        result = collect_capital_events(client, [pending, insider, incomplete], balance_date="2026-08-31",
                                        as_of=date(2026, 11, 13))
        self.assertEqual(result["events"], [])
        self.assertCountEqual(result["unparsed_reports"], [pending["accession"], insider["accession"], incomplete["accession"]])

    def test_checks_cutoff_same_accession_and_missing_document(self):
        old = _filing(1, filed="2026-08-31")
        later = _filing(2, filed="2026-11-14")
        mismatch = {**_filing(3), "url": old["url"]}
        unavailable, future = _filing(4), _filing(5)
        client = _Client({future["url"]: "<p>Item 3.02</p><p>On November 5, 2026, Example Corp issued and sold "
                                        "5,000 shares of its common stock.</p>"})
        result = collect_capital_events(client, [old, later, mismatch, unavailable, future],
                                        balance_date="2026-08-31", as_of=date(2026, 11, 13))
        self.assertEqual(client.requests, [future["url"], unavailable["url"]])
        self.assertEqual(result["events"], [])
        self.assertEqual(result["unparsed_reports"], [future["accession"]])
        self.assertEqual(len(result["missing"]), 2)
        self.assertEqual(collect_capital_events(client, [], balance_date=None, as_of=date(2026, 11, 13))["status"],
                         "balance_unavailable")

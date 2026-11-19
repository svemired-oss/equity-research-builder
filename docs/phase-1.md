# Phase 1 evidence handling

This branch builds on the Phase 0 baseline of seven verified pairs out of 50 (14%). It keeps the same ticker cohort and existing export acceptance checks.

## Rules implemented

- Missing short-term investments, finance leases and commercial paper can receive a labeled zero assumption only when the filed assets, liabilities and equity reconcile within an absolute USD 1 million. A recorded fact conflict prevents this assumption. Known combined operating and finance lease liabilities still require separate evidence. Assumptions retain the source filing and appear in the workbook's Balance evidence and Sources sheets.
- The normalizer selects the latest eligible filing for a matching period and units, retaining superseded observations. Supplemental balance facts use the latest filing instance containing that balance date in consolidated USD facts. Conflicting values in that selected filing still stop the model.
- Alternative cash concepts require a matching opening balance, closing balance and cash-flow change in the same filing. Separately quantified restricted cash is excluded. Any unquantified restriction stays explicitly flagged in the cash proxy.
- Completed debt issuance, principal repayment, common-stock issuance and buybacks adjust the equity bridge. Debt issuance and equity issuance require net cash proceeds. Buybacks require cash and share count. Principal is a carrying-value proxy. Movements already reflected in the filed share observation are excluded from the subsequent share-count change.
- Noncontrolling and preferred interests are deducted at reported book values with proxy flags. They flow through the DCF and sensitivity formulas.
- A material acquisition requires two matching full-year combined pro forma revenue disclosures, or full-year acquiree revenue less the amount already consolidated for both periods. The existing acquirer's margin and investment ratios are flagged analyst proxies for the combined business. Unquantified synergies are excluded. Missing or conflicting disclosures stop with the required period and item.

The acquisition concept mappings use the FASB 2025 taxonomy, including `BusinessAcquisitionsProFormaRevenue`, `BusinessCombinationProFormaInformationRevenueOfAcquireeSinceAcquisitionDateActual` and `BusinessAcquisitionAxis`. [Official taxonomy](https://xbrl.fasb.org/us-gaap/2025/elts/us-gaap-2025.xsd)

An unrelated collector defect exposed by ORCL's Phase 0 result is also covered. Revenue reconciliation now filters nonrevenue observations before reading their dimensions. Eligible revenue observations still require dimensions.

## Reproduction

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

CI installs LibreOffice. Linux tests exercise actual spreadsheet recalculation, including changes to financing cash flowing through to the DCF. Hosts without LibreOffice skip those export integrations.

Collect a fresh Phase 1 benchmark in a separate checkout in the configured Codespace. Phase 0 collection failures contain failure text, so a new collection is required to test the changed retrieval rules.

```sh
/opt/venv/bin/python scripts/benchmark.py --collect --as-of 2026-11-19
/opt/venv/bin/python scripts/benchmark.py
```

The second command blocks network access and replays the collected inputs. The runner writes a failure-type summary and every ticker's actual outcome to `benchmarks/results.md`, with detailed records in `benchmarks/results.json`. Keep the original Phase 0 checkout and caches for comparison.

The Phase 1 collection and final pass rate are pending until the credentialed run completes. The inherited results file still identifies the Phase 0 baseline.

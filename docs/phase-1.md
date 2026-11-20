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

## Recorded acceptance

The owner ran fresh collection followed by the network-blocked replay in the configured Codespace on November 19, 2026 using commit `4598b5022e2e9d5d8dd0091fabdae002b6a6a784`. The pasted report records **9/50 verified pairs (18%)**, versus Phase 0 **7/50 (14%)**, with zero missing or invalid caches. FAST and HON are the two new passes; all seven earlier passes remain. [Full report](../benchmarks/results.md)

The committed Markdown and JSON are a transcription of that supplied report, with four-decimal ratios. Raw cache hashes, generated files and timing records were not supplied and are not fabricated. The private evidence caches remain in the owner Codespace; the runner uses temporary directories for generated pairs. The full 50-pair run was not independently repeated locally.

CI on the implementation commit passed all **57 tests**, including actual LibreOffice export recalculation. [Verified CI run](https://github.com/svemired-oss/equity-research-builder/actions/runs/44578508232)

| Remaining failure type | Count |
| --- | --- |
| Revenue reconciliation | 17 |
| Missing balance or financial fact | 12 |
| Acquisition or merger basis | 4 |
| Conflicting filing facts | 2 |
| Valuation guard | 2 |
| Provider forecast selection (runner category `other`) | 2 |
| Annual financial history | 1 |
| Statement reconciliation | 1 |

ORCL now gets past its dimensions exception but lacks current debt evidence. SBUX now gets past missing cash but fails the unchanged valuation guard. GD and CVX still have unresolved filed bridge conflicts. DHR, ABT and MRK now name the missing full-year acquisition revenue basis. MSFT and NEM failed provider forecast selection in this collection; this does not establish whether their subsequent model checks would pass.

No verification threshold was relaxed to obtain a passing ticker. The 18% rate measures mechanical completion of the generic operating path, not financial quality or GD V4 modeling depth. The fixed cohort and the remaining evidence failures are preserved for subsequent phases.

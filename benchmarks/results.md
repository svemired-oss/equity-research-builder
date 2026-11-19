# Generic operating model baseline

Phase 1, 2026-11-19. Transcribed from the owner-supplied Codespace collection and offline replay report at commit `4598b5022e2e9d5d8dd0091fabdae002b6a6a784`. Ratios are rounded to four decimals. Raw caches and generated pairs were not supplied or independently inspected for this transcription.

Completed 50 tickers. Verified pairs 9/50 (18.0%).
Missing or invalid caches 0. These count as failures in the full-set rate.
A complete baseline requires a recorded collection attempt for every ticker.
PASS requires the existing valuation checks, real formula workbook export, and paired PDF verification.
The rate measures mechanical completion. Economic assumptions and narrative still require analyst review.

## Remaining failures by type

| Failure type | Count |
| --- | --- |
| acquisition_or_merger_basis | 4 |
| conflicting_filing_facts | 2 |
| financial_history | 1 |
| missing_balance_fact | 12 |
| other | 2 |
| revenue_reconciliation | 17 |
| statement_reconciliation | 1 |
| valuation_guard | 2 |

## Per-ticker outcomes

| Ticker | Sector | Status | Stage | Value / price | Reason |
| --- | --- | --- | --- | --- | --- |
| NVDA | tech | PASS | verified_pair | 0.8045 |  |
| MSFT | tech | FAIL | collection | — | The research provider did not return a verified forecast selection. |
| ADBE | tech | PASS | verified_pair | 1.0801 |  |
| CRM | tech | FAIL | collection | — | Filed product or segment revenue did not reconcile across two comparable years. |
| ORCL | tech | FAIL | model_export_verification | — | ResearchGap: Required financial fact is missing: current portion long term debt. |
| SBUX | consumer | FAIL | model_export_verification | 0.2065 | ResearchGap: The standard DCF is outside 25% to 400% of the verified close. The base scenario gives $22.53 per share versus a $109.11 close. The model uses a 9.7% EBIT margin and implies a 35.6% margin at the verified close with other assumptions fixed. Review reinvestment, profit margins, and the equity bridge before issuing a valuation. |
| MCD | consumer | FAIL | collection | — | McDonald's interim balance sheet combines operating and finance leases. The latest separate finance lease liability is needed to finish the live valuation bridge. |
| PG | consumer | FAIL | model_export_verification | — | ResearchGap: Required financial fact is missing: cash and equivalents. |
| KO | consumer | PASS | verified_pair | 0.2986 |  |
| PEP | consumer | FAIL | model_export_verification | — | ResearchGap: Required financial fact is missing: current portion long term debt. |
| GD | industrials | FAIL | collection | — | Conflicting facts were found in the filed balance-sheet bridge. |
| FAST | industrials | PASS | verified_pair | 0.2851 |  |
| CAT | industrials | FAIL | model_export_verification | — | ResearchGap: Required financial fact is missing: current portion long term debt. |
| HON | industrials | PASS | verified_pair | 0.6287 |  |
| LMT | industrials | PASS | verified_pair | 0.5890 |  |
| DHR | healthcare | FAIL | collection | — | Material acquisition has missing disclosed full-year combined pro forma revenue (or acquiree revenue and revenue already included) for 2024-12-31. |
| ABT | healthcare | FAIL | collection | — | Material acquisition has missing disclosed full-year combined pro forma revenue (or acquiree revenue and revenue already included) for 2024-12-31. |
| JNJ | healthcare | FAIL | model_export_verification | — | ResearchGap: Required financial fact is missing: current portion long term debt. |
| PFE | healthcare | FAIL | collection | — | Filed product or segment revenue did not reconcile across two comparable years. |
| MRK | healthcare | FAIL | collection | — | Material acquisition has missing disclosed full-year combined pro forma revenue (or acquiree revenue and revenue already included) for 2024-12-31. |
| XOM | energy | FAIL | collection | — | Valuation requires normalized annual financials from the issuer's reporting framework. |
| CVX | energy | FAIL | collection | — | Conflicting facts were found in the filed balance-sheet bridge. |
| COP | energy | FAIL | collection | — | Filed product or segment revenue did not reconcile across two comparable years. |
| EOG | energy | FAIL | collection | — | Filed product or segment revenue did not reconcile across two comparable years. |
| SLB | energy | FAIL | model_export_verification | — | ResearchGap: Required financial fact is missing: cash and equivalents. |
| NEM | materials | FAIL | collection | — | The research provider did not return a verified forecast selection. |
| FCX | materials | FAIL | collection | — | Filed product or segment revenue did not reconcile across two comparable years. |
| NUE | materials | FAIL | model_export_verification | — | ResearchGap: Required financial fact is missing: operating income. |
| DOW | materials | FAIL | collection | — | Filed product or segment revenue did not reconcile across two comparable years. |
| APD | materials | FAIL | model_export_verification | — | ResearchGap: Required financial fact is missing: current portion long term debt. |
| NEE | utilities | FAIL | collection | — | Filed product or segment revenue did not reconcile across two comparable years. |
| DUK | utilities | FAIL | collection | — | Filed product or segment revenue did not reconcile across two comparable years. |
| SO | utilities | FAIL | collection | — | Filed product or segment revenue did not reconcile across two comparable years. |
| AEP | utilities | FAIL | model_export_verification | — | ResearchGap: The filed balance sheet did not reconcile. |
| EXC | utilities | FAIL | collection | — | Filed product or segment revenue did not reconcile across two comparable years. |
| GOOGL | communications | FAIL | collection | — | Filed product or segment revenue did not reconcile across two comparable years. |
| META | communications | FAIL | model_export_verification | — | ResearchGap: Required financial fact is missing: current portion long term debt. |
| NFLX | communications | FAIL | collection | — | Filed product or segment revenue did not reconcile across two comparable years. |
| VZ | communications | FAIL | collection | — | Filed product or segment revenue did not reconcile across two comparable years. |
| CMCSA | communications | FAIL | collection | — | Filed product or segment revenue did not reconcile across two comparable years. |
| FDX | transport | FAIL | collection | — | Filed product or segment revenue did not reconcile across two comparable years. |
| UPS | transport | FAIL | model_export_verification | — | ResearchGap: Required financial fact is missing: long term debt. |
| UNP | transport | FAIL | collection | — | Union Pacific's filing reports a pending Norfolk Southern acquisition. A current model requires stand-alone and merger cases and the financing terms. |
| CSX | transport | FAIL | collection | — | Filed product or segment revenue did not reconcile across two comparable years. |
| DAL | transport | FAIL | collection | — | Filed product or segment revenue did not reconcile across two comparable years. |
| WMT | retail | PASS | verified_pair | 0.2269 |  |
| COST | retail | PASS | verified_pair | 0.2558 |  |
| HD | retail | PASS | verified_pair | 0.5458 |  |
| TGT | retail | FAIL | model_export_verification | — | ResearchGap: Required financial fact is missing: cash and equivalents. |
| AMZN | retail | FAIL | model_export_verification | 0.2106 | ResearchGap: The standard DCF is outside 25% to 400% of the verified close. The base scenario gives $56.38 per share versus a $267.74 close. The model uses a 10.8% EBIT margin and implies a 36.2% margin at the verified close with other assumptions fixed. Review reinvestment, profit margins, and the equity bridge before issuing a valuation. |

# Generic operating model baseline

Completed 50 tickers. Verified pairs 10/50 (20.0%).
Verified pairs using non-fallback drivers: 0/10 (0.0%).
Missing or invalid caches 0. These count as failures in the full-set rate.
A complete baseline requires a recorded collection attempt for every ticker.
PASS requires the existing valuation checks, real formula workbook export, and paired PDF verification.
The rate measures mechanical completion. Economic assumptions and narrative still require analyst review.

## Remaining failures by type

| Failure type | Count |
| --- | --- |
| acquisition_or_merger_basis | 4 |
| comparable_evidence | 3 |
| conflicting_filing_facts | 2 |
| financial_history | 1 |
| financing_bridge | 1 |
| missing_balance_fact | 22 |
| other | 4 |
| statement_reconciliation | 1 |
| valuation_guard | 2 |

## Sector valuation review

Median blended present value / close among verified Phase 4 pairs.
Legacy caches and failed runs are excluded.

| Sector | Verified Phase 4 values | Median value / price | Status |
| --- | ---: | ---: | --- |
| communications | 0 | — | No evidence |
| consumer | 1 | 0.6718 | Review |
| energy | 0 | — | No evidence |
| healthcare | 0 | — | No evidence |
| industrials | 3 | 0.8802 | Within range |
| materials | 0 | — | No evidence |
| retail | 2 | 0.7315 | Within range |
| tech | 3 | 2.1236 | Review |
| transport | 1 | 1.0516 | Within range |
| utilities | 0 | — | No evidence |

## Per-ticker outcomes

| Ticker | Sector | Status | Driver | Stage | Value / price | Reason |
| --- | --- | --- | --- | --- | --- | --- |
| NVDA | tech | PASS | segment_growth | verified_pair | 2.3584 |  |
| MSFT | tech | FAIL | segment_growth | model_export_verification | — | ValueError: A verified peer valuation is required for acceptance. |
| ADBE | tech | PASS | segment_growth | verified_pair | 1.8467 |  |
| CRM | tech | PASS | segment_growth | verified_pair | 2.1236 |  |
| ORCL | tech | FAIL | Unverified | model_export_verification | — | ResearchGap: Completed common stock issuance needs verified net proceeds. |
| SBUX | consumer | FAIL | segment_growth | model_export_verification | 0.3623 | ResearchGap: The standard DCF is outside 25% to 400% of the verified close. The base scenario gives $23.84 per share versus a $104.47 close. The model uses a 9.7% EBIT margin and implies a 32.9% margin at the verified close with other assumptions fixed. Review reinvestment, profit margins, and the equity bridge before issuing a valuation. |
| MCD | consumer | FAIL | Unverified | collection | — | McDonald's interim balance sheet combines operating and finance leases. The latest separate finance lease liability is needed to finish the live valuation bridge. |
| PG | consumer | FAIL | Unverified | model_export_verification | — | ResearchGap: Required financial fact is missing: cash and equivalents. |
| KO | consumer | PASS | segment_growth | verified_pair | 0.6718 |  |
| PEP | consumer | FAIL | segment_growth | model_export_verification | 1.5057 | ValueError: PDF contains an unmatched claim near ' 2025-12-08 / Source U / \x7fExpects organic revenue, core constant currency and co'. |
| GD | industrials | FAIL | Unverified | collection | — | Conflicting facts were found in the filed balance-sheet bridge. |
| FAST | industrials | PASS | segment_growth | verified_pair | 0.4295 |  |
| CAT | industrials | FAIL | Unverified | model_export_verification | — | ResearchGap: Required financial fact is missing: current portion long term debt. |
| HON | industrials | PASS | segment_growth | verified_pair | 1.7611 |  |
| LMT | industrials | PASS | segment_growth | verified_pair | 0.8802 |  |
| DHR | healthcare | FAIL | Unverified | collection | — | Material acquisition has missing disclosed full-year combined pro forma revenue (or acquiree revenue and revenue already included) for 2024-12-31. |
| ABT | healthcare | FAIL | Unverified | collection | — | Material acquisition has missing disclosed full-year combined pro forma revenue (or acquiree revenue and revenue already included) for 2024-12-31. |
| JNJ | healthcare | FAIL | segment_growth | model_export_verification | 0.8437 | ValueError: PDF contains an unmatched claim near '2026-10-13 / Source AB / \x7fStrong operational performance results in the Company '. |
| PFE | healthcare | FAIL | Unverified | model_export_verification | — | ResearchGap: Profitability or taxes require normalization outside the standard model. |
| MRK | healthcare | FAIL | Unverified | collection | — | Material acquisition has missing disclosed full-year combined pro forma revenue (or acquiree revenue and revenue already included) for 2024-12-31. |
| XOM | energy | FAIL | Unverified | collection | — | Valuation requires normalized annual financials from the issuer's reporting framework. |
| CVX | energy | FAIL | Unverified | collection | — | Conflicting facts were found in the filed balance-sheet bridge. |
| COP | energy | FAIL | Unverified | model_export_verification | — | ResearchGap: Required financial fact is missing: long term debt. |
| EOG | energy | FAIL | Unverified | model_export_verification | — | ResearchGap: Required financial fact is missing: capex. |
| SLB | energy | FAIL | Unverified | model_export_verification | — | ResearchGap: Required financial fact is missing: cash and equivalents. |
| NEM | materials | FAIL | Unverified | model_export_verification | — | ResearchGap: Required financial fact is missing: current portion long term debt. |
| FCX | materials | FAIL | Unverified | model_export_verification | — | ResearchGap: Required financial fact is missing: capex. |
| NUE | materials | FAIL | Unverified | model_export_verification | — | ResearchGap: Required financial fact is missing: operating income. |
| DOW | materials | FAIL | Unverified | model_export_verification | — | ResearchGap: Required financial fact is missing: operating income. |
| APD | materials | FAIL | Unverified | model_export_verification | — | ResearchGap: Required financial fact is missing: current portion long term debt. |
| NEE | utilities | FAIL | Unverified | model_export_verification | — | ResearchGap: The filing needs a verified short-term investment balance. |
| DUK | utilities | FAIL | Unverified | model_export_verification | — | ResearchGap: Required financial fact is missing: revenue. |
| SO | utilities | FAIL | Unverified | model_export_verification | — | ResearchGap: Required financial fact is missing: depreciation amortization. |
| AEP | utilities | FAIL | Unverified | model_export_verification | — | ResearchGap: The filed balance sheet did not reconcile. |
| EXC | utilities | FAIL | Unverified | model_export_verification | — | ResearchGap: Required financial fact is missing: long term debt. |
| GOOGL | communications | FAIL | segment_growth | model_export_verification | — | ValueError: A verified peer valuation is required for acceptance. |
| META | communications | FAIL | Unverified | model_export_verification | — | ResearchGap: Required financial fact is missing: current portion long term debt. |
| NFLX | communications | FAIL | Unverified | model_export_verification | — | ResearchGap: Required financial fact is missing: current portion long term debt. |
| VZ | communications | FAIL | segment_growth | model_export_verification | 2.1161 | ValueError: PDF contains an unmatched claim near ' 2026-10-26 / Source U / \x7fRaised full-year 2026 guidance for key metrics for the'. |
| CMCSA | communications | FAIL | Unverified | model_export_verification | — | ResearchGap: Required financial fact is missing: long term debt. |
| FDX | transport | FAIL | Unverified | model_export_verification | — | ResearchGap: Required financial fact is missing: current portion long term debt. |
| UPS | transport | FAIL | Unverified | model_export_verification | — | ResearchGap: Required financial fact is missing: long term debt. |
| UNP | transport | FAIL | Unverified | collection | — | Union Pacific's filing reports a pending Norfolk Southern acquisition. A current model requires stand-alone and merger cases and the financing terms. |
| CSX | transport | FAIL | Unverified | model_export_verification | — | ResearchGap: Required financial fact is missing: cash and equivalents. |
| DAL | transport | PASS | segment_growth | verified_pair | 1.0516 |  |
| WMT | retail | FAIL | segment_growth | model_export_verification | — | ValueError: A verified peer valuation is required for acceptance. |
| COST | retail | PASS | segment_growth | verified_pair | 0.5193 |  |
| HD | retail | PASS | segment_growth | verified_pair | 0.9436 |  |
| TGT | retail | FAIL | Unverified | model_export_verification | — | ResearchGap: Required financial fact is missing: cash and equivalents. |
| AMZN | retail | FAIL | segment_growth | model_export_verification | — | ResearchGap: The standard DCF is outside 25% to 400% of the verified close. The base scenario gives $46.65 per share versus a $257.37 close. The model uses a 10.8% EBIT margin and implies a 39.3% margin at the verified close with other assumptions fixed. Review reinvestment, profit margins, and the equity bridge before issuing a valuation. |

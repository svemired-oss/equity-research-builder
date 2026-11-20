# Generic operating model baseline

Completed 50 tickers. Verified pairs 6/50 (12.0%).
Verified pairs using non-fallback drivers: 0/6 (0.0%).
Missing or invalid caches 0. These count as failures in the full-set rate.
A complete baseline requires a recorded collection attempt for every ticker.
PASS requires the existing valuation checks, real formula workbook export, and paired PDF verification.
The rate measures mechanical completion. Economic assumptions and narrative still require analyst review.

## Remaining failures by type

| Failure type | Count |
| --- | --- |
| acquisition_or_merger_basis | 4 |
| comparable_evidence | 4 |
| conflicting_filing_facts | 2 |
| financial_history | 1 |
| missing_balance_fact | 5 |
| revenue_reconciliation | 17 |
| valuation_guard | 11 |

## Sector valuation review

Median blended present value / close among verified Phase 4 pairs.
Legacy caches and failed runs are excluded.

| Sector | Verified Phase 4 values | Median value / price | Status |
| --- | ---: | ---: | --- |
| communications | 0 | — | No evidence |
| consumer | 1 | 0.6718 | Review |
| energy | 0 | — | No evidence |
| healthcare | 0 | — | No evidence |
| industrials | 1 | 1.0615 | Within range |
| materials | 0 | — | No evidence |
| retail | 2 | 0.7910 | Within range |
| tech | 2 | 1.8112 | Review |
| transport | 0 | — | No evidence |
| utilities | 0 | — | No evidence |

## Per-ticker outcomes

| Ticker | Sector | Status | Driver | Stage | Value / price | Reason |
| --- | --- | --- | --- | --- | --- | --- |
| NVDA | tech | PASS | segment_growth | verified_pair | 2.3584 |  |
| MSFT | tech | FAIL | segment_growth | model_export_verification | — | ValueError: A verified peer valuation is required for acceptance. |
| ADBE | tech | PASS | segment_growth | verified_pair | 1.2639 |  |
| CRM | tech | FAIL | Unverified | collection | — | Filed product or segment revenue did not reconcile across two comparable years. |
| ORCL | tech | FAIL | Unverified | model_export_verification | — | ResearchGap: Required financial fact is missing: current portion long term debt. |
| SBUX | consumer | FAIL | segment_growth | model_export_verification | 0.3623 | ResearchGap: The standard DCF is outside 25% to 400% of the verified close. The base scenario gives $23.84 per share versus a $104.47 close. The model uses a 9.7% EBIT margin and implies a 32.9% margin at the verified close with other assumptions fixed. Review reinvestment, profit margins, and the equity bridge before issuing a valuation. |
| MCD | consumer | FAIL | Unverified | collection | — | McDonald's interim balance sheet combines operating and finance leases. The latest separate finance lease liability is needed to finish the live valuation bridge. |
| PG | consumer | FAIL | Unverified | collection | — | Market WACC evidence unavailable. No recent BBB corporate spread. |
| KO | consumer | PASS | segment_growth | verified_pair | 0.6718 |  |
| PEP | consumer | FAIL | Unverified | model_export_verification | — | ResearchGap: Required financial fact is missing: current portion long term debt. |
| GD | industrials | FAIL | Unverified | collection | — | Conflicting facts were found in the filed balance-sheet bridge. |
| FAST | industrials | FAIL | segment_growth | model_export_verification | — | ValueError: A verified peer valuation is required for acceptance. |
| CAT | industrials | FAIL | Unverified | collection | — | Market WACC evidence unavailable. No recent BBB corporate spread. |
| HON | industrials | FAIL | segment_growth | model_export_verification | — | ValueError: A verified peer valuation is required for acceptance. |
| LMT | industrials | PASS | segment_growth | verified_pair | 1.0615 |  |
| DHR | healthcare | FAIL | Unverified | collection | — | Material acquisition has missing disclosed full-year combined pro forma revenue (or acquiree revenue and revenue already included) for 2024-12-31. |
| ABT | healthcare | FAIL | Unverified | collection | — | Material acquisition has missing disclosed full-year combined pro forma revenue (or acquiree revenue and revenue already included) for 2024-12-31. |
| JNJ | healthcare | FAIL | Unverified | model_export_verification | — | ResearchGap: Required financial fact is missing: current portion long term debt. |
| PFE | healthcare | FAIL | Unverified | collection | — | Filed product or segment revenue did not reconcile across two comparable years. |
| MRK | healthcare | FAIL | Unverified | collection | — | Material acquisition has missing disclosed full-year combined pro forma revenue (or acquiree revenue and revenue already included) for 2024-12-31. |
| XOM | energy | FAIL | Unverified | collection | — | Valuation requires normalized annual financials from the issuer's reporting framework. |
| CVX | energy | FAIL | Unverified | collection | — | Conflicting facts were found in the filed balance-sheet bridge. |
| COP | energy | FAIL | Unverified | collection | — | Filed product or segment revenue did not reconcile across two comparable years. |
| EOG | energy | FAIL | Unverified | collection | — | Filed product or segment revenue did not reconcile across two comparable years. |
| SLB | energy | FAIL | Unverified | collection | — | Market WACC evidence unavailable. No recent BBB corporate spread. |
| NEM | materials | FAIL | Unverified | collection | — | Market WACC evidence unavailable. No recent BBB corporate spread. |
| FCX | materials | FAIL | Unverified | collection | — | Filed product or segment revenue did not reconcile across two comparable years. |
| NUE | materials | FAIL | Unverified | model_export_verification | — | ResearchGap: Required financial fact is missing: operating income. |
| DOW | materials | FAIL | Unverified | collection | — | Filed product or segment revenue did not reconcile across two comparable years. |
| APD | materials | FAIL | Unverified | collection | — | Market WACC evidence unavailable. No recent BBB corporate spread. |
| NEE | utilities | FAIL | Unverified | collection | — | Filed product or segment revenue did not reconcile across two comparable years. |
| DUK | utilities | FAIL | Unverified | collection | — | Filed product or segment revenue did not reconcile across two comparable years. |
| SO | utilities | FAIL | Unverified | collection | — | Filed product or segment revenue did not reconcile across two comparable years. |
| AEP | utilities | FAIL | Unverified | collection | — | Market WACC evidence unavailable. No recent BBB corporate spread. |
| EXC | utilities | FAIL | Unverified | collection | — | Filed product or segment revenue did not reconcile across two comparable years. |
| GOOGL | communications | FAIL | Unverified | collection | — | Filed product or segment revenue did not reconcile across two comparable years. |
| META | communications | FAIL | Unverified | collection | — | Market WACC evidence unavailable. No recent BBB corporate spread. |
| NFLX | communications | FAIL | Unverified | collection | — | Filed product or segment revenue did not reconcile across two comparable years. |
| VZ | communications | FAIL | Unverified | collection | — | Filed product or segment revenue did not reconcile across two comparable years. |
| CMCSA | communications | FAIL | Unverified | collection | — | Filed product or segment revenue did not reconcile across two comparable years. |
| FDX | transport | FAIL | Unverified | collection | — | Filed product or segment revenue did not reconcile across two comparable years. |
| UPS | transport | FAIL | Unverified | collection | — | Market WACC evidence unavailable. No recent BBB corporate spread. |
| UNP | transport | FAIL | Unverified | collection | — | Union Pacific's filing reports a pending Norfolk Southern acquisition. A current model requires stand-alone and merger cases and the financing terms. |
| CSX | transport | FAIL | Unverified | collection | — | Filed product or segment revenue did not reconcile across two comparable years. |
| DAL | transport | FAIL | Unverified | collection | — | Filed product or segment revenue did not reconcile across two comparable years. |
| WMT | retail | FAIL | segment_growth | model_export_verification | — | ValueError: A verified peer valuation is required for acceptance. |
| COST | retail | PASS | segment_growth | verified_pair | 0.5188 |  |
| HD | retail | PASS | segment_growth | verified_pair | 1.0632 |  |
| TGT | retail | FAIL | Unverified | collection | — | Market WACC evidence unavailable. No recent BBB corporate spread. |
| AMZN | retail | FAIL | segment_growth | model_export_verification | — | ResearchGap: The standard DCF is outside 25% to 400% of the verified close. The base scenario gives $46.65 per share versus a $257.37 close. The model uses a 10.8% EBIT margin and implies a 39.3% margin at the verified close with other assumptions fixed. Review reinvestment, profit margins, and the equity bridge before issuing a valuation. |

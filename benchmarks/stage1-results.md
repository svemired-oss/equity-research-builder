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
| comparable_evidence | 5 |
| conflicting_filing_facts | 2 |
| financial_history | 1 |
| financing_bridge | 1 |
| missing_balance_fact | 8 |
| other | 5 |
| valuation_guard | 18 |

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
| retail | 1 | 0.5188 | Review |
| tech | 3 | 1.9762 | Review |
| transport | 0 | — | No evidence |
| utilities | 0 | — | No evidence |

## Per-ticker outcomes

| Ticker | Sector | Status | Driver | Stage | Value / price | Reason |
| --- | --- | --- | --- | --- | --- | --- |
| NVDA | tech | PASS | segment_growth | verified_pair | 2.3584 |  |
| MSFT | tech | FAIL | segment_growth | model_export_verification | — | ValueError: A verified peer valuation is required for acceptance. |
| ADBE | tech | PASS | segment_growth | verified_pair | 1.3603 |  |
| CRM | tech | PASS | segment_growth | verified_pair | 1.9762 |  |
| ORCL | tech | FAIL | Unverified | model_export_verification | — | ResearchGap: Completed common stock issuance needs verified net proceeds. |
| SBUX | consumer | FAIL | segment_growth | model_export_verification | 0.2923 | ResearchGap: The standard DCF is outside 25% to 400% of the verified close. The base scenario gives $23.84 per share versus a $104.47 close. The model uses a 9.7% EBIT margin and implies a 32.9% margin at the verified close with other assumptions fixed. Review reinvestment, profit margins, and the equity bridge before issuing a valuation. |
| MCD | consumer | FAIL | Unverified | collection | — | McDonald's interim balance sheet combines operating and finance leases. The latest separate finance lease liability is needed to finish the live valuation bridge. |
| PG | consumer | FAIL | Unverified | collection | — | Market WACC evidence unavailable. No recent BBB corporate spread. |
| KO | consumer | PASS | segment_growth | verified_pair | 0.6718 |  |
| PEP | consumer | FAIL | segment_growth | model_export_verification | 1.4202 | ValueError: PDF contains an unmatched claim near ' 2025-12-08 / Source X / \x7fExpects organic revenue, core constant currency and co'. |
| GD | industrials | FAIL | Unverified | collection | — | Conflicting facts were found in the filed balance-sheet bridge. |
| FAST | industrials | FAIL | segment_growth | model_export_verification | — | ValueError: A verified peer valuation is required for acceptance. |
| CAT | industrials | FAIL | Unverified | collection | — | Market WACC evidence unavailable. No recent BBB corporate spread. |
| HON | industrials | FAIL | segment_growth | model_export_verification | — | ValueError: A verified peer valuation is required for acceptance. |
| LMT | industrials | PASS | segment_growth | verified_pair | 1.0615 |  |
| DHR | healthcare | FAIL | Unverified | collection | — | Material acquisition has missing disclosed full-year combined pro forma revenue (or acquiree revenue and revenue already included) for 2024-12-31. |
| ABT | healthcare | FAIL | Unverified | collection | — | Material acquisition has missing disclosed full-year combined pro forma revenue (or acquiree revenue and revenue already included) for 2024-12-31. |
| JNJ | healthcare | FAIL | Unverified | model_export_verification | — | ResearchGap: Required financial fact is missing: operating income. |
| PFE | healthcare | FAIL | Unverified | model_export_verification | — | ResearchGap: Required financial fact is missing: operating income. |
| MRK | healthcare | FAIL | Unverified | collection | — | Material acquisition has missing disclosed full-year combined pro forma revenue (or acquiree revenue and revenue already included) for 2024-12-31. |
| XOM | energy | FAIL | Unverified | collection | — | Valuation requires normalized annual financials from the issuer's reporting framework. |
| CVX | energy | FAIL | Unverified | collection | — | Conflicting facts were found in the filed balance-sheet bridge. |
| COP | energy | FAIL | Unverified | collection | — | Market WACC evidence unavailable. No recent BBB corporate spread. |
| EOG | energy | FAIL | Unverified | collection | — | list index out of range |
| SLB | energy | FAIL | Unverified | collection | — | Market WACC evidence unavailable. No recent BBB corporate spread. |
| NEM | materials | FAIL | Unverified | collection | — | Market WACC evidence unavailable. No recent BBB corporate spread. |
| FCX | materials | FAIL | Unverified | collection | — | Market WACC evidence unavailable. No recent BBB corporate spread. |
| NUE | materials | FAIL | Unverified | collection | — | list index out of range |
| DOW | materials | FAIL | Unverified | model_export_verification | — | ResearchGap: Required financial fact is missing: operating income. |
| APD | materials | FAIL | Unverified | collection | — | The research provider did not return a verified forecast selection. Invalid research selection: reason text failed length or character checks. |
| NEE | utilities | FAIL | Unverified | collection | — | Market WACC evidence unavailable. No recent BBB corporate spread. |
| DUK | utilities | FAIL | Unverified | model_export_verification | — | ResearchGap: The filing needs a verified short-term investment balance. |
| SO | utilities | FAIL | Unverified | model_export_verification | — | ResearchGap: The filing needs a verified short-term investment balance. |
| AEP | utilities | FAIL | Unverified | collection | — | The research provider did not return a verified forecast selection. Invalid research selection: thesis text failed length or character checks. |
| EXC | utilities | FAIL | Unverified | collection | — | Market WACC evidence unavailable. No recent BBB corporate spread. |
| GOOGL | communications | FAIL | Unverified | model_export_verification | — | ResearchGap: Required financial fact is missing: depreciation amortization. |
| META | communications | FAIL | Unverified | collection | — | Market WACC evidence unavailable. No recent BBB corporate spread. |
| NFLX | communications | FAIL | Unverified | collection | — | Market WACC evidence unavailable. No recent BBB corporate spread. |
| VZ | communications | FAIL | Unverified | model_export_verification | — | ResearchGap: Required financial fact is missing: capex. |
| CMCSA | communications | FAIL | Unverified | collection | — | Market WACC evidence unavailable. No recent BBB corporate spread. |
| FDX | transport | FAIL | Unverified | collection | — | Market WACC evidence unavailable. No recent BBB corporate spread. |
| UPS | transport | FAIL | Unverified | collection | — | Market WACC evidence unavailable. No recent BBB corporate spread. |
| UNP | transport | FAIL | Unverified | collection | — | Union Pacific's filing reports a pending Norfolk Southern acquisition. A current model requires stand-alone and merger cases and the financing terms. |
| CSX | transport | FAIL | Unverified | collection | — | Market WACC evidence unavailable. No recent BBB corporate spread. |
| DAL | transport | FAIL | Unverified | collection | — | Market WACC evidence unavailable. No recent BBB corporate spread. |
| WMT | retail | FAIL | segment_growth | model_export_verification | — | ValueError: A verified peer valuation is required for acceptance. |
| COST | retail | PASS | segment_growth | verified_pair | 0.5188 |  |
| HD | retail | FAIL | segment_growth | model_export_verification | — | ValueError: A verified peer valuation is required for acceptance. |
| TGT | retail | FAIL | Unverified | collection | — | Market WACC evidence unavailable. No recent BBB corporate spread. |
| AMZN | retail | FAIL | segment_growth | model_export_verification | — | ResearchGap: The standard DCF is outside 25% to 400% of the verified close. The base scenario gives $46.72 per share versus a $257.37 close. The model uses a 10.8% EBIT margin and implies a 39.3% margin at the verified close with other assumptions fixed. Review reinvestment, profit margins, and the equity bridge before issuing a valuation. |

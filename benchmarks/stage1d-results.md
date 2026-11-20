# Generic operating model baseline

Completed 37 tickers. Verified pairs 17/37 (45.9%).
Verified pairs using non-fallback drivers: 0/17 (0.0%).
Missing or invalid caches 0. These count as failures in the full-set rate.
A complete baseline requires a recorded collection attempt for every ticker.
PASS requires the existing valuation checks, real formula workbook export, and paired PDF verification.
The rate measures mechanical completion. Economic assumptions and narrative still require analyst review.

## Remaining failures by type

| Failure type | Count |
| --- | --- |
| acquisition_or_merger_basis | 3 |
| comparable_evidence | 5 |
| conflicting_filing_facts | 1 |
| financing_bridge | 1 |
| missing_balance_fact | 5 |
| other | 2 |
| valuation_guard | 3 |

## Sector valuation review

Median blended present value / close among verified Phase 4 pairs.
Legacy caches and failed runs are excluded.

| Sector | Verified Phase 4 values | Median value / price | Status |
| --- | ---: | ---: | --- |
| communications | 3 | 0.8710 | Within range |
| consumer | 2 | 1.0460 | Within range |
| healthcare | 1 | 0.8010 | Within range |
| industrials | 2 | 1.0036 | Within range |
| materials | 0 | — | No evidence |
| retail | 3 | 1.0632 | Within range |
| tech | 3 | 1.9762 | Review |
| transport | 3 | 1.0516 | Within range |

## Per-ticker outcomes

| Ticker | Sector | Status | Driver | Stage | Value / price | Reason |
| --- | --- | --- | --- | --- | --- | --- |
| NVDA | tech | PASS | segment_growth | verified_pair | 2.3584 |  |
| MSFT | tech | FAIL | segment_growth | model_export_verification | — | ValueError: A verified peer valuation is required for acceptance. |
| ADBE | tech | PASS | segment_growth | verified_pair | 1.4226 |  |
| CRM | tech | PASS | segment_growth | verified_pair | 1.9762 |  |
| ORCL | tech | FAIL | Unverified | model_export_verification | — | ResearchGap: Completed common stock issuance needs verified net proceeds. |
| SBUX | consumer | FAIL | segment_growth | model_export_verification | 0.3623 | ResearchGap: The standard DCF is outside 25% to 400% of the verified close. The base scenario gives $23.84 per share versus a $104.47 close. The model uses a 9.7% EBIT margin and implies a 32.9% margin at the verified close with other assumptions fixed. Review reinvestment, profit margins, and the equity bridge before issuing a valuation. |
| MCD | consumer | FAIL | Unverified | collection | — | McDonald's interim balance sheet combines operating and finance leases. The latest separate finance lease liability is needed to finish the live valuation bridge. |
| PG | consumer | FAIL | segment_growth | model_export_verification | — | ValueError: A verified peer valuation is required for acceptance. |
| KO | consumer | PASS | segment_growth | verified_pair | 0.6718 |  |
| PEP | consumer | PASS | segment_growth | verified_pair | 1.4202 |  |
| GD | industrials | FAIL | Unverified | collection | — | Conflicting facts were found in the filed balance-sheet bridge. |
| FAST | industrials | FAIL | segment_growth | model_export_verification | — | ValueError: A verified peer valuation is required for acceptance. |
| CAT | industrials | PASS | segment_growth | verified_pair | 0.9436 |  |
| HON | industrials | FAIL | Unverified | collection | — | Pro forma basis required: HONEYWELL INTERNATIONAL INC's 10-Q filed 2026-10-22 reports a spin-off. Discontinued operations held USD 19,844 million of assets at 2025-12-31 (27% of total assets) and were separated by 2026-09-30. The 10-K history and opening balance sheet include those operations, so a valuation needs pro forma continuing-operations history. |
| LMT | industrials | PASS | segment_growth | verified_pair | 1.0637 |  |
| JNJ | healthcare | PASS | segment_growth | verified_pair | 0.8010 |  |
| PFE | healthcare | FAIL | Unverified | model_export_verification | — | ResearchGap: Profitability or taxes require normalization outside the standard model. |
| NEM | materials | FAIL | Unverified | model_export_verification | — | ResearchGap: Required financial fact is missing: operating income. |
| FCX | materials | FAIL | Unverified | model_export_verification | — | ResearchGap: Required financial fact is missing: capex. |
| NUE | materials | FAIL | Unverified | model_export_verification | — | ResearchGap: Required financial fact is missing: operating income. |
| DOW | materials | FAIL | Unverified | model_export_verification | — | ResearchGap: Required financial fact is missing: operating income. |
| APD | materials | FAIL | Unverified | model_export_verification | — | ResearchGap: Profitability or taxes require normalization outside the standard model. |
| GOOGL | communications | PASS | segment_growth | verified_pair | 0.8390 |  |
| META | communications | PASS | segment_growth | verified_pair | 0.8710 |  |
| NFLX | communications | FAIL | segment_growth | model_export_verification | — | ValueError: A verified peer valuation is required for acceptance. |
| VZ | communications | PASS | segment_growth | verified_pair | 1.8807 |  |
| CMCSA | communications | FAIL | segment_growth | model_export_verification | 3.3460 | ResearchGap: The standard DCF is outside 25% to 400% of the verified close. The base scenario gives $114.42 per share versus a $24.86 close. The model uses a 18.8% EBIT margin and implies a 6.7% margin at the verified close with other assumptions fixed. Review reinvestment, profit margins, and the equity bridge before issuing a valuation. |
| FDX | transport | FAIL | Unverified | collection | — | Pro forma basis required: FEDEX CORP's 10-Q filed 2026-10-28 reports a spin-off. Discontinued operations held USD 6,895 million of assets at 2026-05-31 (7% of total assets) and were separated by 2026-09-30. The 10-K history and opening balance sheet include those operations, so a valuation needs pro forma continuing-operations history. |
| UPS | transport | PASS | segment_growth | verified_pair | 1.1826 |  |
| UNP | transport | FAIL | Unverified | collection | — | Union Pacific's filing reports a pending Norfolk Southern acquisition. A current model requires stand-alone and merger cases and the financing terms. |
| CSX | transport | PASS | segment_growth | verified_pair | 0.7535 |  |
| DAL | transport | PASS | segment_growth | verified_pair | 1.0516 |  |
| WMT | retail | FAIL | segment_growth | model_export_verification | — | ValueError: A verified peer valuation is required for acceptance. |
| COST | retail | PASS | segment_growth | verified_pair | 0.5193 |  |
| HD | retail | PASS | segment_growth | verified_pair | 1.0632 |  |
| TGT | retail | PASS | segment_growth | verified_pair | 1.4006 |  |
| AMZN | retail | FAIL | segment_growth | model_export_verification | — | ResearchGap: The standard DCF is outside 25% to 400% of the verified close. The base scenario gives $46.65 per share versus a $257.37 close. The model uses a 10.8% EBIT margin and implies a 39.3% margin at the verified close with other assumptions fixed. Review reinvestment, profit margins, and the equity bridge before issuing a valuation. |

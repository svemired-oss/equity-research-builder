# Generic operating model benchmark

Completed 37 tickers. Verified pairs 16/37 (43.2%).
Verified pairs using non-fallback drivers: 0/16 (0.0%).
Missing or invalid caches 0. These count as failures in the full-set rate.
A complete baseline requires a recorded collection attempt for every ticker.
PASS requires the valuation checks (base FCFE value / price between 0.5x and 2x), real formula workbook export, and paired PDF verification.
The rate measures mechanical completion. Economic assumptions and narrative still require analyst review.

## Remaining failures by type

| Failure type | Count |
| --- | --- |
| acquisition_or_merger_basis | 3 |
| conflicting_filing_facts | 1 |
| financing_bridge | 1 |
| missing_balance_fact | 5 |
| other | 2 |
| valuation_guard | 9 |

## Sector valuation review

Median base FCFE value at the cost of equity / close among verified pairs.
Failed runs are excluded. A pass already requires value / price between 0.5x and 2x.

| Sector | Verified values | Median value / price | Status |
| --- | ---: | ---: | --- |
| communications | 2 | 0.6663 | Review |
| consumer | 3 | 0.8634 | Within range |
| healthcare | 1 | 0.5671 | Review |
| industrials | 2 | 0.7814 | Within range |
| materials | 0 | — | No evidence |
| retail | 2 | 0.9091 | Within range |
| tech | 3 | 0.7332 | Within range |
| transport | 3 | 1.0086 | Within range |

## Stage 2 gate

- Verified pairs: 16/37 (gate: 15 or more) — met.
- Sectors with verified values inside 0.7-1.4: 5/7 — not met.
- Sectors without any verified value: materials.

## Valuation detail for verified pairs

| Ticker | FCFE value / price | FCFF / price | Cost of equity | Beta basis | Peer tier | Peers | Comps peers | Rating |
| --- | ---: | ---: | ---: | --- | --- | --- | ---: | --- |
| NVDA | 0.7332 | 0.7279 | 12.02% | regression | 3-digit SIC | AMD AVGO | 2 | Sell |
| ADBE | 1.1962 | 1.1748 | 9.42% | regression | 3-digit SIC | INTU CRM NOW ADSK ORCL | 4 | Buy |
| CRM | 0.6678 | 0.5595 | 9.96% | regression | 3-digit SIC | ORCL NOW INTU | 2 | Sell |
| PG | 0.8634 | 0.8239 | 6.90% | regression | 3-digit SIC | CL | 1 | Hold |
| KO | 0.6487 | 0.5292 | 6.75% | regression | 3-digit SIC | PEP KDP MNST | 2 | Sell |
| PEP | 1.1363 | 1.1981 | 6.83% | regression | 3-digit SIC | KO MNST KDP STZ | 2 | Buy |
| CAT | 0.5874 | 0.4986 | 10.09% | regression | 2-digit SIC fallback | DE | 1 | Sell |
| LMT | 0.9755 | 0.9527 | 7.30% | regression | 2-digit SIC fallback | RKLB RTX GD BA | 3 | Hold |
| JNJ | 0.5671 | 0.6822 | 6.72% | regression | 3-digit SIC | PFE MRK ABBV BMY LLY | 5 | Sell |
| META | 0.6112 | 0.5920 | 11.19% | regression | 3-digit SIC | GOOGL MSFT CRM ORCL | 3 | Sell |
| NFLX | 0.7213 | 0.7098 | 9.08% | regression | none | none | 0 | Sell |
| UPS | 1.0086 | 1.0123 | 8.71% | regression | 3-digit SIC | JBHT ODFL | 2 | Hold |
| CSX | 0.6160 | 0.5634 | 8.30% | regression | 3-digit SIC | UNP NSC | 2 | Sell |
| DAL | 1.1535 | 1.2374 | 9.66% | regression | 3-digit SIC | AAL UAL LUV | 3 | Buy |
| HD | 0.7629 | 0.7451 | 8.21% | regression | 2-digit SIC fallback | LOW FAST | 2 | Sell |
| TGT | 1.0553 | 1.0576 | 7.41% | regression | 3-digit SIC | DLTR DG COST BJ | 4 | Hold |

## Per-ticker outcomes

| Ticker | Sector | Status | Driver | Stage | Value / price | Reason |
| --- | --- | --- | --- | --- | --- | --- |
| NVDA | tech | PASS | segment_growth | verified_pair | 0.7332 |  |
| MSFT | tech | FAIL | segment_growth | model_export_verification | 0.4557 | ResearchGap: Valuation outside 0.5–2×, analyst review needed. The base FCFE value is $220.78 per share versus a $484.52 close (0.46×). The model uses a 45.6% EBIT margin and implies a 93.0% margin at the verified close with other assumptions fixed. |
| ADBE | tech | PASS | segment_growth | verified_pair | 1.1962 |  |
| CRM | tech | PASS | segment_growth | verified_pair | 0.6678 |  |
| ORCL | tech | FAIL | Unverified | model_export_verification | — | ResearchGap: Completed common stock issuance needs verified net proceeds. |
| SBUX | consumer | FAIL | segment_growth | model_export_verification | 0.3403 | ResearchGap: Valuation outside 0.5–2×, analyst review needed. The base FCFE value is $35.09 per share versus a $103.13 close (0.34×). The model uses a 9.7% EBIT margin and implies a 30.0% margin at the verified close with other assumptions fixed. |
| MCD | consumer | FAIL | Unverified | collection | — | McDonald's interim balance sheet combines operating and finance leases. The latest separate finance lease liability is needed to finish the live valuation bridge. |
| PG | consumer | PASS | segment_growth | verified_pair | 0.8634 |  |
| KO | consumer | PASS | segment_growth | verified_pair | 0.6487 |  |
| PEP | consumer | PASS | segment_growth | verified_pair | 1.1363 |  |
| GD | industrials | FAIL | Unverified | collection | — | Conflicting facts were found in the filed balance-sheet bridge. |
| FAST | industrials | FAIL | segment_growth | model_export_verification | 0.3925 | ResearchGap: Valuation outside 0.5–2×, analyst review needed. The base FCFE value is $19.86 per share versus a $50.61 close (0.39×). The model uses a 20.2% EBIT margin and implies a 50.6% margin at the verified close with other assumptions fixed. |
| CAT | industrials | PASS | segment_growth | verified_pair | 0.5874 |  |
| HON | industrials | FAIL | Unverified | collection | — | Pro forma basis required: HONEYWELL INTERNATIONAL INC's 10-Q filed 2026-10-22 reports a spin-off. Discontinued operations held USD 19,844 million of assets at 2025-12-31 (27% of total assets) and were separated by 2026-09-30. The 10-K history and opening balance sheet include those operations, so a valuation needs pro forma continuing-operations history. |
| LMT | industrials | PASS | segment_growth | verified_pair | 0.9755 |  |
| JNJ | healthcare | PASS | segment_growth | verified_pair | 0.5671 |  |
| PFE | healthcare | FAIL | Unverified | model_export_verification | — | ResearchGap: Profitability or taxes require normalization outside the standard model. |
| NEM | materials | FAIL | Unverified | model_export_verification | — | ResearchGap: Required financial fact is missing: operating income. |
| FCX | materials | FAIL | Unverified | model_export_verification | — | ResearchGap: Required financial fact is missing: capex. |
| NUE | materials | FAIL | Unverified | model_export_verification | — | ResearchGap: Required financial fact is missing: operating income. |
| DOW | materials | FAIL | Unverified | model_export_verification | — | ResearchGap: Required financial fact is missing: operating income. |
| APD | materials | FAIL | Unverified | model_export_verification | — | ResearchGap: Profitability or taxes require normalization outside the standard model. |
| GOOGL | communications | FAIL | segment_growth | model_export_verification | 0.4935 | ResearchGap: Valuation outside 0.5–2×, analyst review needed. The base FCFE value is $130.93 per share versus a $265.29 close (0.49×). The model uses a 32.0% EBIT margin and implies a 64.9% margin at the verified close with other assumptions fixed. |
| META | communications | PASS | segment_growth | verified_pair | 0.6112 |  |
| NFLX | communications | PASS | segment_growth | verified_pair | 0.7213 |  |
| VZ | communications | FAIL | segment_growth | model_export_verification | 2.3271 | ResearchGap: Valuation outside 0.5–2×, analyst review needed. The base FCFE value is $108.16 per share versus a $46.48 close (2.33×). The model uses a 21.2% EBIT margin and implies a 10.3% margin at the verified close with other assumptions fixed. |
| CMCSA | communications | FAIL | segment_growth | model_export_verification | 3.4903 | ResearchGap: Valuation outside 0.5–2×, analyst review needed. The base FCFE value is $86.91 per share versus a $24.90 close (3.49×). The model uses a 18.8% EBIT margin and implies a 5.4% margin at the verified close with other assumptions fixed. |
| FDX | transport | FAIL | Unverified | collection | — | Pro forma basis required: FEDEX CORP's 10-Q filed 2026-10-28 reports a spin-off. Discontinued operations held USD 6,895 million of assets at 2026-05-31 (7% of total assets) and were separated by 2026-09-30. The 10-K history and opening balance sheet include those operations, so a valuation needs pro forma continuing-operations history. |
| UPS | transport | PASS | segment_growth | verified_pair | 1.0086 |  |
| UNP | transport | FAIL | Unverified | collection | — | Union Pacific's filing reports a pending Norfolk Southern acquisition. A current model requires stand-alone and merger cases and the financing terms. |
| CSX | transport | PASS | segment_growth | verified_pair | 0.6160 |  |
| DAL | transport | PASS | segment_growth | verified_pair | 1.1535 |  |
| WMT | retail | FAIL | segment_growth | model_export_verification | 0.3623 | ResearchGap: Valuation outside 0.5–2×, analyst review needed. The base FCFE value is $46.36 per share versus a $127.95 close (0.36×). The model uses a 4.2% EBIT margin and implies a 10.8% margin at the verified close with other assumptions fixed. |
| COST | retail | FAIL | segment_growth | model_export_verification | 0.3997 | ResearchGap: Valuation outside 0.5–2×, analyst review needed. The base FCFE value is $440.51 per share versus a $1102.15 close (0.40×). The model uses a 3.8% EBIT margin and implies a 9.8% margin at the verified close with other assumptions fixed. |
| HD | retail | PASS | segment_growth | verified_pair | 0.7629 |  |
| TGT | retail | PASS | segment_growth | verified_pair | 1.0553 |  |
| AMZN | retail | FAIL | segment_growth | model_export_verification | 0.2534 | ResearchGap: Valuation outside 0.5–2×, analyst review needed. The base FCFE value is $62.53 per share versus a $246.82 close (0.25×). The model uses a 10.8% EBIT margin and implies a 36.3% margin at the verified close with other assumptions fixed. |

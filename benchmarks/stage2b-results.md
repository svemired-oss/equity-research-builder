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
| other | 3 |
| valuation_guard | 8 |

## Sector valuation review

Median base FCFE value at the cost of equity / close among verified pairs.
Failed runs are excluded. A pass already requires value / price between 0.5x and 2x.

| Sector | Verified values | Median value / price | Status |
| --- | ---: | ---: | --- |
| communications | 2 | 0.6664 | Review |
| consumer | 3 | 0.8635 | Within range |
| healthcare | 1 | 0.5671 | Review |
| industrials | 2 | 0.7815 | Within range |
| materials | 0 | — | No evidence |
| retail | 2 | 0.9248 | Within range |
| tech | 3 | 0.7333 | Within range |
| transport | 3 | 1.0087 | Within range |

## Stage 2 gate

- Verified pairs: 16/37 (gate: 15 or more) — met.
- Sectors with verified values inside 0.7-1.4: 5/7 — not met.
- Sectors without any verified value: materials.

## Valuation detail for verified pairs

| Ticker | FCFE value / price | FCFF / price | Cost of equity | Beta basis | Peer tier | Peers | Comps peers | Rating |
| --- | ---: | ---: | ---: | --- | --- | --- | ---: | --- |
| NVDA | 0.7333 | 0.7280 | 12.02% | regression | 3-digit SIC | AMD AVGO MU | 3 | Sell |
| ADBE | 1.1963 | 1.1748 | 9.42% | regression | 3-digit SIC | CRM INTU ADSK NOW WDAY | 5 | Buy |
| CRM | 0.6678 | 0.5595 | 9.96% | regression | 3-digit SIC | NOW WDAY ADBE | 3 | Sell |
| PG | 0.8635 | 0.8240 | 6.90% | regression | 3-digit SIC | CL | 1 | Hold |
| KO | 0.6488 | 0.5293 | 6.75% | regression | 3-digit SIC | PEP KDP MNST | 2 | Sell |
| PEP | 1.1364 | 1.1982 | 6.83% | regression | 3-digit SIC | KO KDP MNST | 2 | Buy |
| CAT | 0.5874 | 0.4986 | 10.09% | regression | 2-digit SIC fallback | DE CMI | 1 | Sell |
| LMT | 0.9755 | 0.9528 | 7.30% | regression | 2-digit SIC fallback | RTX GD BA TXT | 3 | Hold |
| JNJ | 0.5671 | 0.6822 | 6.72% | regression | 3-digit SIC | PFE MRK ABBV BMY LLY | 5 | Sell |
| META | 0.6113 | 0.5922 | 11.19% | regression | 3-digit SIC | GOOGL MSFT | 2 | Sell |
| NFLX | 0.7214 | 0.7099 | 9.08% | regression | none | none | 0 | Sell |
| UPS | 1.0087 | 1.0123 | 8.71% | regression | 3-digit SIC | ODFL JBHT KNX | 3 | Hold |
| CSX | 0.6160 | 0.5635 | 8.30% | regression | 3-digit SIC | UNP NSC | 2 | Sell |
| DAL | 1.1536 | 1.2375 | 9.66% | regression | 3-digit SIC | AAL UAL LUV | 3 | Buy |
| HD | 0.7941 | 0.7807 | 8.21% | regression | 3-digit SIC | LOW | 1 | Hold |
| TGT | 1.0554 | 1.0577 | 7.41% | regression | 3-digit SIC | DG DLTR COST | 3 | Hold |

## Per-ticker outcomes

| Ticker | Sector | Status | Driver | Stage | Value / price | Reason |
| --- | --- | --- | --- | --- | --- | --- |
| NVDA | tech | PASS | segment_growth | verified_pair | 0.7333 |  |
| MSFT | tech | FAIL | segment_growth | model_export_verification | 0.4558 | ResearchGap: Valuation outside 0.5–2×, analyst review needed. The base FCFE value is $220.83 per share versus a $484.52 close (0.46×). The model uses a 45.6% EBIT margin and implies a 93.0% margin at the verified close with other assumptions fixed. |
| ADBE | tech | PASS | segment_growth | verified_pair | 1.1963 |  |
| CRM | tech | PASS | segment_growth | verified_pair | 0.6678 |  |
| ORCL | tech | FAIL | Unverified | model_export_verification | — | ResearchGap: Completed common stock issuance needs verified net proceeds. |
| SBUX | consumer | FAIL | segment_growth | model_export_verification | 0.3403 | ResearchGap: Valuation outside 0.5–2×, analyst review needed. The base FCFE value is $35.10 per share versus a $103.13 close (0.34×). The model uses a 9.7% EBIT margin and implies a 30.0% margin at the verified close with other assumptions fixed. |
| MCD | consumer | FAIL | Unverified | collection | — | McDonald's interim balance sheet combines operating and finance leases. The latest separate finance lease liability is needed to finish the live valuation bridge. |
| PG | consumer | PASS | segment_growth | verified_pair | 0.8635 |  |
| KO | consumer | PASS | segment_growth | verified_pair | 0.6488 |  |
| PEP | consumer | PASS | segment_growth | verified_pair | 1.1364 |  |
| GD | industrials | FAIL | Unverified | collection | — | Conflicting facts were found in the filed balance-sheet bridge. |
| FAST | industrials | FAIL | segment_growth | model_export_verification | 0.3925 | ResearchGap: Valuation outside 0.5–2×, analyst review needed. The base FCFE value is $19.87 per share versus a $50.61 close (0.39×). The model uses a 20.2% EBIT margin and implies a 50.6% margin at the verified close with other assumptions fixed. |
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
| GOOGL | communications | FAIL | Unverified | collection | — | The research provider did not return a verified forecast selection. The read operation timed out |
| META | communications | PASS | segment_growth | verified_pair | 0.6113 |  |
| NFLX | communications | PASS | segment_growth | verified_pair | 0.7214 |  |
| VZ | communications | FAIL | segment_growth | model_export_verification | 2.3273 | ResearchGap: Valuation outside 0.5–2×, analyst review needed. The base FCFE value is $108.17 per share versus a $46.48 close (2.33×). The model uses a 21.2% EBIT margin and implies a 10.3% margin at the verified close with other assumptions fixed. |
| CMCSA | communications | FAIL | segment_growth | model_export_verification | 3.4905 | ResearchGap: Valuation outside 0.5–2×, analyst review needed. The base FCFE value is $86.91 per share versus a $24.90 close (3.49×). The model uses a 18.8% EBIT margin and implies a 5.4% margin at the verified close with other assumptions fixed. |
| FDX | transport | FAIL | Unverified | collection | — | Pro forma basis required: FEDEX CORP's 10-Q filed 2026-10-28 reports a spin-off. Discontinued operations held USD 6,895 million of assets at 2026-05-31 (7% of total assets) and were separated by 2026-09-30. The 10-K history and opening balance sheet include those operations, so a valuation needs pro forma continuing-operations history. |
| UPS | transport | PASS | segment_growth | verified_pair | 1.0087 |  |
| UNP | transport | FAIL | Unverified | collection | — | Union Pacific's filing reports a pending Norfolk Southern acquisition. A current model requires stand-alone and merger cases and the financing terms. |
| CSX | transport | PASS | segment_growth | verified_pair | 0.6160 |  |
| DAL | transport | PASS | segment_growth | verified_pair | 1.1536 |  |
| WMT | retail | FAIL | segment_growth | model_export_verification | 0.3623 | ResearchGap: Valuation outside 0.5–2×, analyst review needed. The base FCFE value is $46.36 per share versus a $127.95 close (0.36×). The model uses a 4.2% EBIT margin and implies a 10.8% margin at the verified close with other assumptions fixed. |
| COST | retail | FAIL | segment_growth | model_export_verification | 0.3987 | ResearchGap: Valuation outside 0.5–2×, analyst review needed. The base FCFE value is $439.48 per share versus a $1102.15 close (0.40×). The model uses a 3.8% EBIT margin and implies a 9.9% margin at the verified close with other assumptions fixed. |
| HD | retail | PASS | segment_growth | verified_pair | 0.7941 |  |
| TGT | retail | PASS | segment_growth | verified_pair | 1.0554 |  |
| AMZN | retail | FAIL | segment_growth | model_export_verification | 0.2535 | ResearchGap: Valuation outside 0.5–2×, analyst review needed. The base FCFE value is $62.57 per share versus a $246.82 close (0.25×). The model uses a 10.8% EBIT margin and implies a 36.3% margin at the verified close with other assumptions fixed. |

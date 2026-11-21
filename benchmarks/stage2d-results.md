# Generic operating model benchmark

Completed 37 tickers. Verified pairs 13/37 (35.1%).
Verified pairs using non-fallback drivers: 0/13 (0.0%).
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
| other | 1 |
| valuation_guard | 13 |

## Sector valuation review

Median base FCFE value at the cost of equity / close among verified pairs.
Failed runs are excluded. A pass already requires value / price between 0.5x and 2x.

| Sector | Verified values | Median value / price | Status |
| --- | ---: | ---: | --- |
| communications | 1 | 0.6288 | Reported, not gated (outside 0.7-1.4) |
| consumer | 3 | 0.8770 | Within range |
| healthcare | 1 | 0.8238 | Reported, not gated (inside 0.7-1.4) |
| industrials | 1 | 0.9998 | Reported, not gated (inside 0.7-1.4) |
| materials | 1 | 1.3794 | Reported, not gated (inside 0.7-1.4) |
| retail | 2 | 0.6455 | Review |
| tech | 2 | 1.0400 | Within range |
| transport | 2 | 0.8531 | Within range |

## Stage 2 gate

- Verified pairs: 13/37 (gate: 15 or more) — not met.
- Sectors with 2 or more verified values inside 0.7-1.4: 3/4 — not met.
- Single-value sectors (reported, not gated): communications, healthcare, industrials, materials.
- Sectors without any verified value: none.
- Ratings among verified pairs: Buy 4, Hold 4, Sell 5.

## Valuation detail for verified pairs

| Ticker | FCFE value / price | ROIC-terminal check / price | Final-year capex < D&A | FCFF / price | Cost of equity | Beta | Beta basis | Normalized margin | Reported margin | Debt / EBITDA | Peer tier | Peers | Comps peers | Rating |
| --- | ---: | ---: | --- | ---: | ---: | ---: | --- | ---: | ---: | ---: | --- | --- | ---: | --- |
| NVDA | 1.1388 | 1.1411 | No | 1.1682 | 19.20% | 2.20 | historical only (comparable beta unavailable: 1 peer betas, 2 required.) | 60.4% | 60.4% | 0.06 | 3-digit SIC | AMD AVGO | 2 | Buy |
| ADBE | 0.9412 | 0.9227 | No | 0.9182 | 12.81% | 1.21 | 50/50 historical and comparable | 34.3% | 34.3% | 0.65 | 3-digit SIC | CRM INTU NOW ADSK | 4 | Hold |
| PG | 0.8770 | 0.8462 | No | 0.8244 | 7.25% | 0.36 | historical only (comparable beta unavailable: 0 peer betas, 2 required.) | 24.2% | 22.7% | 1.49 | 3-digit SIC | CL | 1 | Hold |
| KO | 0.7086 | 0.6747 | No | 0.5628 | 6.74% | 0.28 | 50/50 historical and comparable | 24.8% | 24.7% | 3.07 | 3-digit SIC | PEP KDP MNST | 2 | Sell |
| PEP | 1.3634 | 1.2663 | No | 1.3320 | 6.98% | 0.32 | 50/50 historical and comparable | 14.4% | 13.1% | 3.29 | 3-digit SIC | KO KDP MNST | 2 | Buy |
| LMT | 0.9998 | 0.9519 | No | 0.9546 | 7.54% | 0.40 | 50/50 historical and comparable | 10.3% | 10.3% | 2.30 | 2-digit SIC fallback | RTX GD BA | 2 | Hold |
| JNJ | 0.8238 | 0.7890 | No | 0.9004 | 6.27% | 0.21 | 50/50 historical and comparable | 27.0% | 24.9% | 1.46 | 3-digit SIC | PFE MRK LLY ABBV BMY | 5 | Hold |
| APD | 1.3794 | 1.2451 | No | 0.3667 | 9.27% | 0.67 | historical only (comparable beta unavailable: 1 peer betas, 2 required.) | 24.5% | 0.2% | 11.51 | 2-digit SIC fallback | LIN DD | 2 | Buy |
| META | 0.6288 | 0.6201 | No | 0.6346 | 12.76% | 1.21 | 50/50 historical and comparable | 41.4% | 41.4% | 0.59 | 3-digit SIC | GOOGL MSFT | 2 | Sell |
| UPS | 0.5939 | 0.5659 | No | 0.5249 | 12.65% | 1.19 | 50/50 historical and comparable | 9.8% | 9.3% | 2.22 | 3-digit SIC | JBHT ODFL KNX | 3 | Sell |
| DAL | 1.1122 | 1.0290 | No | 1.0394 | 12.40% | 1.15 | 50/50 historical and comparable | 9.5% | 9.5% | 1.71 | 3-digit SIC | UAL AAL LUV | 3 | Buy |
| HD | 0.6127 | 0.5910 | No | 0.5183 | 10.25% | 0.82 | 50/50 historical and comparable | 13.5% | 13.5% | 2.29 | 2-digit SIC fallback | LOW FAST | 2 | Sell |
| TGT | 0.6784 | 0.6377 | No | 0.6198 | 9.90% | 0.77 | 50/50 historical and comparable | 5.2% | 5.2% | 1.99 | 3-digit SIC | COST DG DLTR FIVE | 4 | Sell |

## Per-ticker outcomes

| Ticker | Sector | Status | Driver | Stage | Value / price | Reason |
| --- | --- | --- | --- | --- | --- | --- |
| NVDA | tech | PASS | segment_growth | verified_pair | 1.1388 |  |
| MSFT | tech | FAIL | segment_growth | model_export_verification | 0.3797 | ResearchGap: Valuation outside 0.5–2×, analyst review needed. The base FCFE value is $183.97 per share versus a $484.52 close (0.38×). The model uses a 45.6% EBIT margin and implies a 100.2% margin at the verified close with other assumptions fixed. |
| ADBE | tech | PASS | segment_growth | verified_pair | 0.9412 |  |
| CRM | tech | FAIL | segment_growth | model_export_verification | 0.4403 | ResearchGap: Valuation outside 0.5–2×, analyst review needed. The base FCFE value is $79.53 per share versus a $180.62 close (0.44×). The model uses a 20.2% EBIT margin and implies a 39.9% margin at the verified close with other assumptions fixed. |
| ORCL | tech | FAIL | Unverified | model_export_verification | — | ResearchGap: Completed common stock issuance needs verified net proceeds. |
| SBUX | consumer | FAIL | segment_growth | model_export_verification | 0.3804 | ResearchGap: Valuation outside 0.5–2×, analyst review needed. The base FCFE value is $39.23 per share versus a $103.13 close (0.38×). The model uses a 11.6% EBIT margin and implies a 30.0% margin at the verified close with other assumptions fixed. |
| MCD | consumer | FAIL | Unverified | collection | — | McDonald's interim balance sheet combines operating and finance leases. The latest separate finance lease liability is needed to finish the live valuation bridge. |
| PG | consumer | PASS | segment_growth | verified_pair | 0.8770 |  |
| KO | consumer | PASS | segment_growth | verified_pair | 0.7086 |  |
| PEP | consumer | PASS | segment_growth | verified_pair | 1.3634 |  |
| GD | industrials | FAIL | Unverified | collection | — | Conflicting facts were found in the filed balance-sheet bridge. |
| FAST | industrials | FAIL | segment_growth | model_export_verification | 0.3096 | ResearchGap: Valuation outside 0.5–2×, analyst review needed. The base FCFE value is $15.67 per share versus a $50.61 close (0.31×). The model uses a 20.2% EBIT margin and implies a 56.7% margin at the verified close with other assumptions fixed. |
| CAT | industrials | FAIL | segment_growth | model_export_verification | 0.4525 | ResearchGap: Valuation outside 0.5–2×, analyst review needed. The base FCFE value is $230.96 per share versus a $510.45 close (0.45×). The model uses a 20.5% EBIT margin and implies a 45.2% margin at the verified close with other assumptions fixed. |
| HON | industrials | FAIL | Unverified | collection | — | Pro forma basis required: HONEYWELL INTERNATIONAL INC's 10-Q filed 2026-10-22 reports a spin-off. Discontinued operations held USD 19,844 million of assets at 2025-12-31 (27% of total assets) and were separated by 2026-09-30. The 10-K history and opening balance sheet include those operations, so a valuation needs pro forma continuing-operations history. |
| LMT | industrials | PASS | segment_growth | verified_pair | 0.9998 |  |
| JNJ | healthcare | PASS | segment_growth | verified_pair | 0.8238 |  |
| PFE | healthcare | FAIL | Unverified | model_export_verification | — | ResearchGap: Profitability or taxes require normalization outside the standard model. |
| NEM | materials | FAIL | Unverified | model_export_verification | — | ResearchGap: Required financial fact is missing: operating income. |
| FCX | materials | FAIL | Unverified | model_export_verification | — | ResearchGap: Required financial fact is missing: capex. |
| NUE | materials | FAIL | Unverified | model_export_verification | — | ResearchGap: Required financial fact is missing: operating income. |
| DOW | materials | FAIL | Unverified | model_export_verification | — | ResearchGap: Required financial fact is missing: operating income. |
| APD | materials | PASS | segment_growth | verified_pair | 1.3794 |  |
| GOOGL | communications | FAIL | segment_growth | model_export_verification | 0.3324 | ResearchGap: Valuation outside 0.5–2×, analyst review needed. The base FCFE value is $88.18 per share versus a $265.29 close (0.33×). The model uses a 32.0% EBIT margin and implies a 76.0% margin at the verified close with other assumptions fixed. |
| META | communications | PASS | segment_growth | verified_pair | 0.6288 |  |
| NFLX | communications | FAIL | segment_growth | model_export_verification | 0.4370 | ResearchGap: Valuation outside 0.5–2×, analyst review needed. The base FCFE value is $24.79 per share versus a $56.73 close (0.44×). The model uses a 26.7% EBIT margin and implies a 57.2% margin at the verified close with other assumptions fixed. |
| VZ | communications | FAIL | segment_growth | model_export_verification | 2.0492 | ResearchGap: Valuation outside 0.5–2×, analyst review needed. The base FCFE value is $95.25 per share versus a $46.48 close (2.05×). The model uses a 21.3% EBIT margin and implies a 11.8% margin at the verified close with other assumptions fixed. |
| CMCSA | communications | FAIL | segment_growth | model_export_verification | 3.2068 | ResearchGap: Valuation outside 0.5–2×, analyst review needed. The base FCFE value is $79.85 per share versus a $24.90 close (3.21×). The model uses a 18.8% EBIT margin and implies a 7.0% margin at the verified close with other assumptions fixed. |
| FDX | transport | FAIL | Unverified | collection | — | Pro forma basis required: FEDEX CORP's 10-Q filed 2026-10-28 reports a spin-off. Discontinued operations held USD 6,895 million of assets at 2026-05-31 (7% of total assets) and were separated by 2026-09-30. The 10-K history and opening balance sheet include those operations, so a valuation needs pro forma continuing-operations history. |
| UPS | transport | PASS | segment_growth | verified_pair | 0.5939 |  |
| UNP | transport | FAIL | Unverified | collection | — | Union Pacific's filing reports a pending Norfolk Southern acquisition. A current model requires stand-alone and merger cases and the financing terms. |
| CSX | transport | FAIL | segment_growth | model_export_verification | 0.4249 | ResearchGap: Valuation outside 0.5–2×, analyst review needed. The base FCFE value is $20.86 per share versus a $49.10 close (0.42×). The model uses a 36.7% EBIT margin and implies a 80.5% margin at the verified close with other assumptions fixed. |
| DAL | transport | PASS | segment_growth | verified_pair | 1.1122 |  |
| WMT | retail | FAIL | segment_growth | model_export_verification | 0.3560 | ResearchGap: Valuation outside 0.5–2×, analyst review needed. The base FCFE value is $45.55 per share versus a $127.95 close (0.36×). The model uses a 4.2% EBIT margin and implies a 10.3% margin at the verified close with other assumptions fixed. |
| COST | retail | FAIL | segment_growth | model_export_verification | 0.2529 | ResearchGap: Valuation outside 0.5–2×, analyst review needed. The base FCFE value is $278.72 per share versus a $1102.15 close (0.25×). The model uses a 3.8% EBIT margin and implies a 13.5% margin at the verified close with other assumptions fixed. |
| HD | retail | PASS | segment_growth | verified_pair | 0.6127 |  |
| TGT | retail | PASS | segment_growth | verified_pair | 0.6784 |  |
| AMZN | retail | FAIL | segment_growth | model_export_verification | 0.1286 | ResearchGap: Valuation outside 0.5–2×, analyst review needed. The base FCFE value is $31.74 per share versus a $246.82 close (0.13×). The model uses a 10.8% EBIT margin and implies a 45.4% margin at the verified close with other assumptions fixed. |

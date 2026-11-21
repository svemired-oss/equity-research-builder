# Generic operating model benchmark

Completed 37 tickers. Verified pairs 12/37 (32.4%).
Verified pairs using non-fallback drivers: 0/12 (0.0%).
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
| valuation_guard | 13 |

## Sector valuation review

Median base FCFE value at the cost of equity / close among verified pairs.
Failed runs are excluded. A pass already requires value / price between 0.5x and 2x.

| Sector | Verified values | Median value / price | Status |
| --- | ---: | ---: | --- |
| communications | 2 | 1.1857 | Within range |
| consumer | 3 | 0.7631 | Within range |
| healthcare | 1 | 0.5929 | Reported, not gated (outside 0.7-1.4) |
| industrials | 1 | 0.8681 | Reported, not gated (inside 0.7-1.4) |
| materials | 0 | — | No evidence |
| retail | 2 | 0.5836 | Review |
| tech | 1 | 0.8060 | Reported, not gated (inside 0.7-1.4) |
| transport | 2 | 0.7005 | Within range |

## Stage 2 gate

- Verified pairs: 12/37 (gate: 15 or more) — not met.
- Sectors with 2 or more verified values inside 0.7-1.4: 3/4 — not met.
- Single-value sectors (reported, not gated): healthcare, industrials, tech.
- Sectors without any verified value: materials.
- Ratings among verified pairs: Buy 1, Hold 4, Sell 7.

## Valuation detail for verified pairs

| Ticker | FCFE value / price | ROIC-terminal check / price | Final-year capex < D&A | FCFF / price | Cost of equity | Beta | Beta basis | Peer tier | Peers | Comps peers | Rating |
| --- | ---: | ---: | --- | ---: | ---: | ---: | --- | --- | --- | ---: | --- |
| ADBE | 0.8060 | 0.8159 | No | 0.7804 | 13.00% | 1.24 | 50/50 historical and comparable | 3-digit SIC | CRM ORCL INTU ADSK WDAY | 4 | Hold |
| PG | 0.7631 | 0.8018 | No | 0.7579 | 7.25% | 0.36 | historical only (comparable beta unavailable: 0 peer betas, 2 required.) | 3-digit SIC | CL | 1 | Sell |
| KO | 0.5998 | 0.6505 | No | 0.5307 | 6.74% | 0.28 | 50/50 historical and comparable | 3-digit SIC | PEP KDP MNST | 2 | Sell |
| PEP | 0.9869 | 1.0980 | No | 1.1472 | 6.98% | 0.32 | 50/50 historical and comparable | 3-digit SIC | KO KDP MNST | 2 | Hold |
| LMT | 0.8681 | 0.9319 | No | 0.9065 | 7.54% | 0.40 | 50/50 historical and comparable | 2-digit SIC fallback | RTX GD BA | 2 | Hold |
| JNJ | 0.5929 | 0.6314 | No | 0.7685 | 6.27% | 0.21 | 50/50 historical and comparable | 3-digit SIC | PFE MRK ABBV BMY LLY | 5 | Sell |
| META | 0.5042 | 0.5125 | No | 0.4873 | 12.82% | 1.21 | 50/50 historical and comparable | 3-digit SIC | GOOGL MSFT ORCL CRM | 3 | Sell |
| VZ | 1.8672 | 2.2620 | No | 3.0269 | 6.69% | 0.27 | historical only (comparable beta unavailable: 1 peer betas, 2 required.) | 3-digit SIC | T TMUS | 2 | Buy |
| UPS | 0.5987 | 0.6405 | No | 0.5480 | 12.65% | 1.19 | 50/50 historical and comparable | 3-digit SIC | JBHT ODFL KNX | 3 | Sell |
| DAL | 0.8023 | 0.8413 | No | 0.8638 | 12.40% | 1.15 | 50/50 historical and comparable | 3-digit SIC | UAL AAL LUV | 3 | Hold |
| HD | 0.5073 | 0.5410 | No | 0.4904 | 10.89% | 0.92 | historical only (comparable beta unavailable: 1 peer betas, 2 required.) | 3-digit SIC | LOW | 1 | Sell |
| TGT | 0.6599 | 0.7117 | No | 0.6450 | 9.90% | 0.77 | 50/50 historical and comparable | 3-digit SIC | COST DG DLTR | 3 | Sell |

## Per-ticker outcomes

| Ticker | Sector | Status | Driver | Stage | Value / price | Reason |
| --- | --- | --- | --- | --- | --- | --- |
| NVDA | tech | FAIL | segment_growth | model_export_verification | 0.4531 | ResearchGap: Valuation outside 0.5–2×, analyst review needed. The base FCFE value is $56.88 per share versus a $125.53 close (0.45×). The model uses a 60.4% EBIT margin and implies a 126.9% margin at the verified close with other assumptions fixed. |
| MSFT | tech | FAIL | segment_growth | model_export_verification | 0.3410 | ResearchGap: Valuation outside 0.5–2×, analyst review needed. The base FCFE value is $165.21 per share versus a $484.52 close (0.34×). The model uses a 45.6% EBIT margin and implies a 111.3% margin at the verified close with other assumptions fixed. |
| ADBE | tech | PASS | segment_growth | verified_pair | 0.8060 |  |
| CRM | tech | FAIL | segment_growth | model_export_verification | 0.4482 | ResearchGap: Valuation outside 0.5–2×, analyst review needed. The base FCFE value is $80.95 per share versus a $180.62 close (0.45×). The model uses a 19.0% EBIT margin and implies a 41.3% margin at the verified close with other assumptions fixed. |
| ORCL | tech | FAIL | Unverified | model_export_verification | — | ResearchGap: Completed common stock issuance needs verified net proceeds. |
| SBUX | consumer | FAIL | segment_growth | model_export_verification | 0.2829 | ResearchGap: Valuation outside 0.5–2×, analyst review needed. The base FCFE value is $29.18 per share versus a $103.13 close (0.28×). The model uses a 9.7% EBIT margin and implies a 31.2% margin at the verified close with other assumptions fixed. |
| MCD | consumer | FAIL | Unverified | collection | — | McDonald's interim balance sheet combines operating and finance leases. The latest separate finance lease liability is needed to finish the live valuation bridge. |
| PG | consumer | PASS | segment_growth | verified_pair | 0.7631 |  |
| KO | consumer | PASS | segment_growth | verified_pair | 0.5998 |  |
| PEP | consumer | PASS | segment_growth | verified_pair | 0.9869 |  |
| GD | industrials | FAIL | Unverified | collection | — | Conflicting facts were found in the filed balance-sheet bridge. |
| FAST | industrials | FAIL | segment_growth | model_export_verification | 0.3114 | ResearchGap: Valuation outside 0.5–2×, analyst review needed. The base FCFE value is $15.76 per share versus a $50.61 close (0.31×). The model uses a 20.2% EBIT margin and implies a 57.8% margin at the verified close with other assumptions fixed. |
| CAT | industrials | FAIL | segment_growth | model_export_verification | 0.3761 | ResearchGap: Valuation outside 0.5–2×, analyst review needed. The base FCFE value is $191.97 per share versus a $510.45 close (0.38×). The model uses a 19.3% EBIT margin and implies a 45.4% margin at the verified close with other assumptions fixed. |
| HON | industrials | FAIL | Unverified | collection | — | Pro forma basis required: HONEYWELL INTERNATIONAL INC's 10-Q filed 2026-10-22 reports a spin-off. Discontinued operations held USD 19,844 million of assets at 2025-12-31 (27% of total assets) and were separated by 2026-09-30. The 10-K history and opening balance sheet include those operations, so a valuation needs pro forma continuing-operations history. |
| LMT | industrials | PASS | segment_growth | verified_pair | 0.8681 |  |
| JNJ | healthcare | PASS | segment_growth | verified_pair | 0.5929 |  |
| PFE | healthcare | FAIL | Unverified | model_export_verification | — | ResearchGap: Profitability or taxes require normalization outside the standard model. |
| NEM | materials | FAIL | Unverified | model_export_verification | — | ResearchGap: Required financial fact is missing: operating income. |
| FCX | materials | FAIL | Unverified | model_export_verification | — | ResearchGap: Required financial fact is missing: capex. |
| NUE | materials | FAIL | Unverified | model_export_verification | — | ResearchGap: Required financial fact is missing: operating income. |
| DOW | materials | FAIL | Unverified | model_export_verification | — | ResearchGap: Required financial fact is missing: operating income. |
| APD | materials | FAIL | Unverified | model_export_verification | — | ResearchGap: Profitability or taxes require normalization outside the standard model. |
| GOOGL | communications | FAIL | segment_growth | model_export_verification | 0.3694 | ResearchGap: Valuation outside 0.5–2×, analyst review needed. The base FCFE value is $97.99 per share versus a $265.29 close (0.37×). The model uses a 32.0% EBIT margin and implies a 80.1% margin at the verified close with other assumptions fixed. |
| META | communications | PASS | segment_growth | verified_pair | 0.5042 |  |
| NFLX | communications | FAIL | segment_growth | model_export_verification | 0.3831 | ResearchGap: Valuation outside 0.5–2×, analyst review needed. The base FCFE value is $21.73 per share versus a $56.73 close (0.38×). The model uses a 26.7% EBIT margin and implies a 65.2% margin at the verified close with other assumptions fixed. |
| VZ | communications | PASS | segment_growth | verified_pair | 1.8672 |  |
| CMCSA | communications | FAIL | segment_growth | model_export_verification | 2.7178 | ResearchGap: Valuation outside 0.5–2×, analyst review needed. The base FCFE value is $67.67 per share versus a $24.90 close (2.72×). The model uses a 18.8% EBIT margin and implies a 9.6% margin at the verified close with other assumptions fixed. |
| FDX | transport | FAIL | Unverified | collection | — | Pro forma basis required: FEDEX CORP's 10-Q filed 2026-10-28 reports a spin-off. Discontinued operations held USD 6,895 million of assets at 2026-05-31 (7% of total assets) and were separated by 2026-09-30. The 10-K history and opening balance sheet include those operations, so a valuation needs pro forma continuing-operations history. |
| UPS | transport | PASS | segment_growth | verified_pair | 0.5987 |  |
| UNP | transport | FAIL | Unverified | collection | — | Union Pacific's filing reports a pending Norfolk Southern acquisition. A current model requires stand-alone and merger cases and the financing terms. |
| CSX | transport | FAIL | segment_growth | model_export_verification | 0.3493 | ResearchGap: Valuation outside 0.5–2×, analyst review needed. The base FCFE value is $17.15 per share versus a $49.10 close (0.35×). The model uses a 36.1% EBIT margin and implies a 84.3% margin at the verified close with other assumptions fixed. |
| DAL | transport | PASS | segment_growth | verified_pair | 0.8023 |  |
| WMT | retail | FAIL | segment_growth | model_export_verification | 0.2993 | ResearchGap: Valuation outside 0.5–2×, analyst review needed. The base FCFE value is $38.29 per share versus a $127.95 close (0.30×). The model uses a 4.2% EBIT margin and implies a 11.3% margin at the verified close with other assumptions fixed. |
| COST | retail | FAIL | segment_growth | model_export_verification | 0.2600 | ResearchGap: Valuation outside 0.5–2×, analyst review needed. The base FCFE value is $286.61 per share versus a $1102.15 close (0.26×). The model uses a 3.8% EBIT margin and implies a 14.2% margin at the verified close with other assumptions fixed. |
| HD | retail | PASS | segment_growth | verified_pair | 0.5073 |  |
| TGT | retail | PASS | segment_growth | verified_pair | 0.6599 |  |
| AMZN | retail | FAIL | segment_growth | model_export_verification | 0.1463 | ResearchGap: Valuation outside 0.5–2×, analyst review needed. The base FCFE value is $36.12 per share versus a $246.82 close (0.15×). The model uses a 10.8% EBIT margin and implies a 49.8% margin at the verified close with other assumptions fixed. |

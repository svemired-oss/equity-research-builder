# Generic operating model benchmark

Completed 37 tickers. Verified pairs 18/37 (48.6%).
Verified pairs using non-fallback drivers: 0/18 (0.0%).
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
| valuation_guard | 7 |

## Sector valuation review

Median base FCFE value at the cost of equity / close among verified pairs.
Failed runs are excluded. A pass already requires value / price between 0.5x and 2x.

| Sector | Verified values | Median value / price | Status |
| --- | ---: | ---: | --- |
| communications | 3 | 0.6760 | Review |
| consumer | 3 | 0.8635 | Within range |
| healthcare | 1 | 0.6026 | Reported, not gated (outside 0.7-1.4) |
| industrials | 2 | 0.8292 | Within range |
| materials | 0 | — | No evidence |
| retail | 2 | 0.9092 | Within range |
| tech | 4 | 0.6939 | Review |
| transport | 3 | 1.1027 | Within range |

## Stage 2 gate

- Verified pairs: 18/37 (gate: 15 or more) — met.
- Sectors with 2 or more verified values inside 0.7-1.4: 4/6 — not met.
- Single-value sectors (reported, not gated): healthcare.
- Sectors without any verified value: materials.
- Ratings among verified pairs: Buy 4, Hold 4, Sell 10.

## Valuation detail for verified pairs

| Ticker | FCFE value / price | At former 15% ROIC | Terminal ROIC | FCFF / price | Cost of equity | Beta basis | Peer tier | Peers | Comps peers | Rating |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- | --- | ---: | --- |
| NVDA | 0.8088 | 0.7333 | 40.0% | 0.8044 | 12.02% | regression | 3-digit SIC | AMD AVGO | 2 | Hold |
| MSFT | 0.5073 | 0.4558 | 36.6% | 0.5095 | 9.52% | regression | none | none | 0 | Sell |
| ADBE | 1.1963 | 1.1963 | 15.0% | 1.1748 | 9.42% | regression | 3-digit SIC | ADSK CRM INTU NOW ORCL | 4 | Buy |
| CRM | 0.5791 | 0.6678 | 8.0% | 0.4435 | 9.96% | regression | 3-digit SIC | ORCL NOW ADBE WDAY | 3 | Sell |
| PG | 0.8635 | 0.8635 | 15.0% | 0.8240 | 6.90% | regression | 2-digit SIC fallback | CL JNJ PFE MRK BMY | 5 | Hold |
| KO | 0.6537 | 0.6488 | 15.8% | 0.5347 | 6.75% | regression | 3-digit SIC | PEP MNST KDP | 2 | Sell |
| PEP | 1.1717 | 1.1364 | 18.2% | 1.2454 | 6.83% | regression | 3-digit SIC | KO KDP MNST | 2 | Buy |
| CAT | 0.5874 | 0.5874 | 15.0% | 0.4986 | 10.09% | regression | 2-digit SIC fallback | DE CMI | 1 | Sell |
| LMT | 1.0709 | 0.9755 | 31.8% | 1.0568 | 7.30% | regression | 2-digit SIC fallback | RKLB RTX GD BA | 3 | Hold |
| JNJ | 0.6026 | 0.5671 | 21.0% | 0.7206 | 6.72% | regression | 3-digit SIC | PFE MRK ABBV BMY AMGN | 5 | Sell |
| GOOGL | 0.5443 | 0.4937 | 37.7% | 0.5235 | 10.06% | regression | 3-digit SIC | MSFT META | 2 | Sell |
| META | 0.6760 | 0.6113 | 35.6% | 0.6613 | 11.19% | regression | 3-digit SIC | GOOGL MSFT | 2 | Sell |
| NFLX | 0.7602 | 0.7214 | 21.6% | 0.7506 | 9.08% | regression | none | none | 0 | Sell |
| UPS | 1.1027 | 1.0087 | 31.6% | 1.1312 | 8.71% | regression | 3-digit SIC | JBHT KNX ODFL | 3 | Buy |
| CSX | 0.6160 | 0.6160 | 15.0% | 0.5635 | 8.30% | regression | 3-digit SIC | UNP NSC | 2 | Sell |
| DAL | 1.1590 | 1.1536 | 15.4% | 1.2437 | 9.66% | regression | 3-digit SIC | AAL UAL LUV | 3 | Buy |
| HD | 0.7629 | 0.7629 | 15.0% | 0.7451 | 8.21% | regression | 3-digit SIC | LOW | 1 | Sell |
| TGT | 1.0554 | 1.0554 | 15.0% | 1.0577 | 7.41% | regression | 3-digit SIC | COST DG DLTR | 3 | Hold |

## Per-ticker outcomes

| Ticker | Sector | Status | Driver | Stage | Value / price | Reason |
| --- | --- | --- | --- | --- | --- | --- |
| NVDA | tech | PASS | segment_growth | verified_pair | 0.8088 |  |
| MSFT | tech | PASS | segment_growth | verified_pair | 0.5073 |  |
| ADBE | tech | PASS | segment_growth | verified_pair | 1.1963 |  |
| CRM | tech | PASS | segment_growth | verified_pair | 0.5791 |  |
| ORCL | tech | FAIL | Unverified | model_export_verification | — | ResearchGap: Completed common stock issuance needs verified net proceeds. |
| SBUX | consumer | FAIL | segment_growth | model_export_verification | 0.3403 | ResearchGap: Valuation outside 0.5–2×, analyst review needed. The base FCFE value is $35.10 per share versus a $103.13 close (0.34×). The model uses a 9.7% EBIT margin and implies a 30.0% margin at the verified close with other assumptions fixed. |
| MCD | consumer | FAIL | Unverified | collection | — | McDonald's interim balance sheet combines operating and finance leases. The latest separate finance lease liability is needed to finish the live valuation bridge. |
| PG | consumer | PASS | segment_growth | verified_pair | 0.8635 |  |
| KO | consumer | PASS | segment_growth | verified_pair | 0.6537 |  |
| PEP | consumer | PASS | segment_growth | verified_pair | 1.1717 |  |
| GD | industrials | FAIL | Unverified | collection | — | Conflicting facts were found in the filed balance-sheet bridge. |
| FAST | industrials | FAIL | segment_growth | model_export_verification | 0.4308 | ResearchGap: Valuation outside 0.5–2×, analyst review needed. The base FCFE value is $21.80 per share versus a $50.61 close (0.43×). The model uses a 20.2% EBIT margin and implies a 46.5% margin at the verified close with other assumptions fixed. |
| CAT | industrials | PASS | segment_growth | verified_pair | 0.5874 |  |
| HON | industrials | FAIL | Unverified | collection | — | Pro forma basis required: HONEYWELL INTERNATIONAL INC's 10-Q filed 2026-10-22 reports a spin-off. Discontinued operations held USD 19,844 million of assets at 2025-12-31 (27% of total assets) and were separated by 2026-09-30. The 10-K history and opening balance sheet include those operations, so a valuation needs pro forma continuing-operations history. |
| LMT | industrials | PASS | segment_growth | verified_pair | 1.0709 |  |
| JNJ | healthcare | PASS | segment_growth | verified_pair | 0.6026 |  |
| PFE | healthcare | FAIL | Unverified | model_export_verification | — | ResearchGap: Profitability or taxes require normalization outside the standard model. |
| NEM | materials | FAIL | Unverified | model_export_verification | — | ResearchGap: Required financial fact is missing: operating income. |
| FCX | materials | FAIL | Unverified | model_export_verification | — | ResearchGap: Required financial fact is missing: capex. |
| NUE | materials | FAIL | Unverified | model_export_verification | — | ResearchGap: Required financial fact is missing: operating income. |
| DOW | materials | FAIL | Unverified | model_export_verification | — | ResearchGap: Required financial fact is missing: operating income. |
| APD | materials | FAIL | Unverified | model_export_verification | — | ResearchGap: Profitability or taxes require normalization outside the standard model. |
| GOOGL | communications | PASS | segment_growth | verified_pair | 0.5443 |  |
| META | communications | PASS | segment_growth | verified_pair | 0.6760 |  |
| NFLX | communications | PASS | segment_growth | verified_pair | 0.7602 |  |
| VZ | communications | FAIL | segment_growth | model_export_verification | 2.0288 | ResearchGap: Valuation outside 0.5–2×, analyst review needed. The base FCFE value is $94.30 per share versus a $46.48 close (2.03×). The model uses a 21.2% EBIT margin and implies a 11.8% margin at the verified close with other assumptions fixed. |
| CMCSA | communications | FAIL | segment_growth | model_export_verification | 3.4905 | ResearchGap: Valuation outside 0.5–2×, analyst review needed. The base FCFE value is $86.91 per share versus a $24.90 close (3.49×). The model uses a 18.8% EBIT margin and implies a 5.4% margin at the verified close with other assumptions fixed. |
| FDX | transport | FAIL | Unverified | collection | — | Pro forma basis required: FEDEX CORP's 10-Q filed 2026-10-28 reports a spin-off. Discontinued operations held USD 6,895 million of assets at 2026-05-31 (7% of total assets) and were separated by 2026-09-30. The 10-K history and opening balance sheet include those operations, so a valuation needs pro forma continuing-operations history. |
| UPS | transport | PASS | segment_growth | verified_pair | 1.1027 |  |
| UNP | transport | FAIL | Unverified | collection | — | Union Pacific's filing reports a pending Norfolk Southern acquisition. A current model requires stand-alone and merger cases and the financing terms. |
| CSX | transport | PASS | segment_growth | verified_pair | 0.6160 |  |
| DAL | transport | PASS | segment_growth | verified_pair | 1.1590 |  |
| WMT | retail | FAIL | segment_growth | model_export_verification | 0.3669 | ResearchGap: Valuation outside 0.5–2×, analyst review needed. The base FCFE value is $46.95 per share versus a $127.95 close (0.37×). The model uses a 4.2% EBIT margin and implies a 10.7% margin at the verified close with other assumptions fixed. |
| COST | retail | FAIL | segment_growth | model_export_verification | 0.4375 | ResearchGap: Valuation outside 0.5–2×, analyst review needed. The base FCFE value is $482.19 per share versus a $1102.15 close (0.44×). The model uses a 3.8% EBIT margin and implies a 9.0% margin at the verified close with other assumptions fixed. |
| HD | retail | PASS | segment_growth | verified_pair | 0.7629 |  |
| TGT | retail | PASS | segment_growth | verified_pair | 1.0554 |  |
| AMZN | retail | FAIL | segment_growth | model_export_verification | 0.2607 | ResearchGap: Valuation outside 0.5–2×, analyst review needed. The base FCFE value is $64.36 per share versus a $246.82 close (0.26×). The model uses a 10.8% EBIT margin and implies a 35.5% margin at the verified close with other assumptions fixed. |

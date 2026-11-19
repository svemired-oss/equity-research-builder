# Generic operating model baseline

Completed 50 tickers. Verified pairs 7/50 (14.0%).
Missing or invalid caches 0. These count as failures in the full-set rate.
All 50 tickers have recorded collection attempts. The owner ran the collection and subsequent offline replay in the configured Codespace on November 19, 2026.

Provenance: this table was transcribed from the owner's supplied replay report. Seven statuses and all four-decimal ratios matched the initial run. XOM was recollected with the repaired harness (72603e4); the remaining 49 collection caches came from e26dec2. The raw caches and generated file pairs remain in that Codespace. The assistant verified the table's count and consistency and ran the separate offline unit and export tests locally.

ORCL's unexpected dimensions key error remains open. The initial XOM harness error was repaired and replaced with the actual missing-financials reason.
PASS requires the existing valuation checks, real formula workbook export, and paired PDF verification.
The rate measures mechanical completion. Economic assumptions and narrative still require analyst review.

| Ticker | Sector | Status | Stage | Value / price | Reason |
| --- | --- | --- | --- | --- | --- |
| NVDA | tech | PASS | verified_pair | 0.8045 |  |
| MSFT | tech | FAIL | model_export_verification | â€” | ResearchGap: Required financial fact is missing: finance lease current. |
| ADBE | tech | PASS | verified_pair | 1.0801 |  |
| CRM | tech | FAIL | collection | â€” | Filed product or segment revenue did not reconcile across two comparable years. |
| ORCL | tech | FAIL | collection | â€” | 'dimensions' |
| SBUX | consumer | FAIL | model_export_verification | â€” | ResearchGap: Required financial fact is missing: cash and equivalents. |
| MCD | consumer | FAIL | collection | â€” | McDonald's interim balance sheet combines operating and finance leases. The latest separate finance lease liability is needed to finish the live valuation bridge. |
| PG | consumer | FAIL | model_export_verification | â€” | ResearchGap: Required financial fact is missing: cash and equivalents. |
| KO | consumer | PASS | verified_pair | 0.2986 |  |
| PEP | consumer | FAIL | model_export_verification | â€” | ResearchGap: Minority or preferred interests need a reviewed valuation bridge. |
| GD | industrials | FAIL | collection | â€” | Conflicting facts were found in the filed balance-sheet bridge. |
| FAST | industrials | FAIL | model_export_verification | â€” | ResearchGap: The filing needs a verified short-term investment balance. |
| CAT | industrials | FAIL | model_export_verification | â€” | ResearchGap: The filing needs a verified short-term investment balance. |
| HON | industrials | FAIL | model_export_verification | â€” | ResearchGap: Minority or preferred interests need a reviewed valuation bridge. |
| LMT | industrials | PASS | verified_pair | 0.5890 |  |
| DHR | healthcare | FAIL | collection | â€” | The latest interim filing reports $9.88B in acquisition-related cash outlays. A comparable post-acquisition revenue and margin base and an acquisition financing bridge are required before issuing the valuation. |
| ABT | healthcare | FAIL | collection | â€” | The latest interim filing reports $20.08B in acquisition-related cash outlays. A comparable post-acquisition revenue and margin base and an acquisition financing bridge are required before issuing the valuation. |
| JNJ | healthcare | FAIL | model_export_verification | â€” | ResearchGap: Required financial fact is missing: current portion long term debt. |
| PFE | healthcare | FAIL | collection | â€” | Filed product or segment revenue did not reconcile across two comparable years. |
| MRK | healthcare | FAIL | collection | â€” | The latest interim filing reports $8.78B in acquisition-related cash outlays. A comparable post-acquisition revenue and margin base and an acquisition financing bridge are required before issuing the valuation. |
| XOM | energy | FAIL | collection | â€” | Valuation requires normalized annual financials from the issuer's reporting framework. |
| CVX | energy | FAIL | collection | â€” | Conflicting facts were found in the filed balance-sheet bridge. |
| COP | energy | FAIL | collection | â€” | Filed product or segment revenue did not reconcile across two comparable years. |
| EOG | energy | FAIL | collection | â€” | Filed product or segment revenue did not reconcile across two comparable years. |
| SLB | energy | FAIL | model_export_verification | â€” | ResearchGap: Required financial fact is missing: cash and equivalents. |
| NEM | materials | FAIL | model_export_verification | â€” | ResearchGap: Required financial fact is missing: finance lease current. |
| FCX | materials | FAIL | collection | â€” | Filed product or segment revenue did not reconcile across two comparable years. |
| NUE | materials | FAIL | model_export_verification | â€” | ResearchGap: Minority or preferred interests need a reviewed valuation bridge. |
| DOW | materials | FAIL | collection | â€” | Filed product or segment revenue did not reconcile across two comparable years. |
| APD | materials | FAIL | model_export_verification | â€” | ResearchGap: The filing needs a verified short-term investment balance. |
| NEE | utilities | FAIL | collection | â€” | Filed product or segment revenue did not reconcile across two comparable years. |
| DUK | utilities | FAIL | collection | â€” | Filed product or segment revenue did not reconcile across two comparable years. |
| SO | utilities | FAIL | collection | â€” | Filed product or segment revenue did not reconcile across two comparable years. |
| AEP | utilities | FAIL | model_export_verification | â€” | ResearchGap: The filed balance sheet did not reconcile. |
| EXC | utilities | FAIL | collection | â€” | Filed product or segment revenue did not reconcile across two comparable years. |
| GOOGL | communications | FAIL | collection | â€” | Filed product or segment revenue did not reconcile across two comparable years. |
| META | communications | FAIL | model_export_verification | â€” | ResearchGap: Required financial fact is missing: current portion long term debt. |
| NFLX | communications | FAIL | collection | â€” | Filed product or segment revenue did not reconcile across two comparable years. |
| VZ | communications | FAIL | collection | â€” | Filed product or segment revenue did not reconcile across two comparable years. |
| CMCSA | communications | FAIL | collection | â€” | Filed product or segment revenue did not reconcile across two comparable years. |
| FDX | transport | FAIL | collection | â€” | Filed product or segment revenue did not reconcile across two comparable years. |
| UPS | transport | FAIL | model_export_verification | â€” | ResearchGap: The filing needs a verified short-term investment balance. |
| UNP | transport | FAIL | collection | â€” | Union Pacific's filing reports a pending Norfolk Southern acquisition. A current model requires stand-alone and merger cases and the financing terms. |
| CSX | transport | FAIL | collection | â€” | Filed product or segment revenue did not reconcile across two comparable years. |
| DAL | transport | FAIL | collection | â€” | Filed product or segment revenue did not reconcile across two comparable years. |
| WMT | retail | PASS | verified_pair | 0.2269 |  |
| COST | retail | PASS | verified_pair | 0.2565 |  |
| HD | retail | PASS | verified_pair | 0.5458 |  |
| TGT | retail | FAIL | model_export_verification | â€” | ResearchGap: Required financial fact is missing: cash and equivalents. |
| AMZN | retail | FAIL | model_export_verification | â€” | ResearchGap: The standard DCF is outside 25% to 400% of the verified close. The base scenario gives $56.38 per share versus a $267.74 close. The model uses a 10.8% EBIT margin and implies a 36.2% margin at the verified close with other assumptions fixed. Review reinvestment, profit margins, and the equity bridge before issuing a valuation. |

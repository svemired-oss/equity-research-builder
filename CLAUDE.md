# Equity Research Builder

## Goal

Raise the verified-pair rate on the fixed 50-ticker U.S. operating benchmark (`benchmarks/tickers.csv`) from the Phase 8 baseline of **6/50 (12%)** to **above 60%**. Do this without relaxing any existing verification threshold. A verified pair is a recalculated formula workbook and a workbook-backed PDF that pass `check_pair`. The rate measures mechanical completion. Economic assumptions still need analyst review.

## Five-stage plan

This plan comes from the failure counts in `benchmarks/phase8-results.md`.

1. **Coverage fallbacks.** Revenue reconciliation (17 failures) and missing current-debt facts (ORCL, PEP, JNJ). Add evidence-backed fallbacks that preserve filing provenance and label every proxy.
2. **Market WACC evidence.** The missing BBB spread blocks 9 tickers (PG, CAT, SLB, NEM, APD, AEP, META, UPS, TGT). Add a dated fallback source for the debt-cost proxy.
3. **Valuation acceptance.** Peer valuation (MSFT, FAST, HON, WMT) and the DCF range guard (SBUX, AMZN). Fix the inputs. Do not widen the guard.
4. **Filing basis.** Acquisition pro forma revenue (DHR, ABT, MRK), the UNP merger, conflicting bridge facts (GD, CVX), the MCD finance lease, NUE operating income and XOM annual history.
5. **Release.** Run a fresh, full 50-ticker collection and replay that shows more than 60%. Update the README, docs and public benchmark from that run only.

## Rule: no stage is done until the benchmark re-runs on real data

A stage counts as done only after a fresh collection of the full 50-ticker cohort with real SEC and provider data, followed by the network-blocked replay:

```sh
/opt/venv/bin/python scripts/benchmark.py --collect --as-of <date> --cache private/<stage>-cache --output private/<stage>-collection.md
/opt/venv/bin/python scripts/benchmark.py --cache private/<stage>-cache --output benchmarks/<stage>-results.md
```

- Passing unit tests, synthetic fixtures or a subset run (`--only`) do not complete a stage. They are useful for development.
- Record the commit, cutoff date and pass count. Commit the results Markdown and JSON. Keep the caches in `private/`, which is not committed.
- Never fabricate or hand-edit benchmark numbers. If a run cannot happen (missing credentials, provider outage), mark the stage as unverified.
- Every new fallback must cite its filing source and appear as a labeled assumption in the workbook. No fallback may override a recorded fact conflict.

## Working notes

- Tests: `python -m pytest -q`. Network is blocked in tests. LibreOffice export tests skip when `soffice` is missing.
- Collection costs OpenAI and Alpaca API usage. Run one benchmark process at a time.

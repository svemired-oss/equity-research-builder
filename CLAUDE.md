# Equity Research Builder

`docs/PLAN.md` is the source of truth for scope, stages, gates and decisions. This file follows it. If they disagree, `docs/PLAN.md` wins, so update this file to match.

## Goal

Build formula workbooks and workbook-backed PDF reports for U.S. non-financial operating companies. Banks, REITs and foreign listings are out of scope. The product is done when all eight criteria in `docs/PLAN.md` pass on a fresh 50-ticker collection (`benchmarks/tickers.csv`) plus offline replay. Coverage is 25/50 verified pairs (50%), with 10+ real-driver passes. The baseline on 2026-11-19 was 6/50 (12%) with 0 real drivers.

A verified pair is a recalculated formula workbook and a workbook-backed PDF that pass `check_pair`.

## GD V4 standard

The model is measured against the owner's own build process:

1. Sources: 10-K, 10-Q and annual reports. Use investor relations files where XBRL lacks a driver table.
2. Line items as the company reports them: revenue model, income statement, balance sheet.
3. 5 years of history checked against the 10-Ks. The latest 10-Q is a display-only column.
4. Revenue model in the company's own segments or drivers, with a growth % row and a written reason under each line.
5. Operating costs in a separate cost table when disclosed. Otherwise project them in the income statement.
6. Balance sheet built from each line's history. Schedules for PP&E, long-term debt (history plus guidance), AR/AP/inventory days and equity (retained earnings, treasury, surplus, AOCI).
7. Cash flow from balance-sheet changes, with FCFE and closing cash. Closing cash ties back and the balance check is 0.
8. Cost of equity sheet: CAPM with S&P 500 and company data, plus historical, forward and comparable betas (each comparable's own D/E, tax rate and beta). No separate Comps sheets.
9. DCF: nominal FCFE at the cost of equity. Terminal growth is a labeled analyst assumption.
10. Scenarios: bull, base and bear from ±0.5% segment revenue growth or margin shifts.
11. Cover: price, value per share, 12-month target and scenarios, all as live formulas.

## Decisions (2026-11-20)

- FCFE at the cost of equity is the primary DCF. FCFF at WACC is a cross-check.
- Peer multiples are a football-field cross-check only. The target comes from the DCF.
- Code sets every number. The AI writes a one-line reason under each growth assumption, citing the filing or guidance passage.
- As-reported line items, cost tables and days-based working capital arrive in v2, not v1.

## Stages and gates

Stages run in the order listed. Stage 3 comes after the v1 release.

| Stage | Dates | Build | Gate |
| --- | --- | --- | --- |
| 1. Coverage fallbacks | Nov 20-24 | Total revenue as one labeled line when segments don't reconcile. Missing current-debt line = 0 when total debt is filed and the balance sheet ties. | 15+/50 pass |
| 2. Valuation sanity | Nov 25-Dec 1 | FCFE primary plus FCFF check. CAPM with historical, forward and comparable betas. Multiples as a cross-check from 3+ peers. Carry other and equity-method income. Growth-based bull/bear. Pass only if value/price is 0.5-2x. | Sector medians 0.7-1.4 |
| 4a. Report minimum | Dec 1-2 | Business and risk text from the 10-K for every pass | No empty business, industry or risk section |
| 5. Release v1 | Dec 2-4 | Merge to main, deploy Render, publish 10 passing pairs | Signed-out download and password run both work |
| 3. Real revenue drivers | Dec 7-23 | Stores (SBUX, TGT, MCD), volume × yield (FDX, UPS, DAL), units × price. AI-written cited reasons. | 10+ real-driver passes matching filings |
| 4b. Report depth | Dec 28-Jan 6 | Dated catalysts from 8-Ks and releases. Bull/bear tied to driver changes. Industry section. | No empty section in any passing report |
| v2 release | Jan 7-8 | As-reported line items. Rerun the 50 and refresh public pairs. | All 8 criteria pass |

## Rule: no stage is done until its gate passes on real data

Use one branch per stage. A stage is done only when all three hold:

- Its gate passes on a fresh collection of the full 50-ticker cohort with real SEC and provider data, followed by the network-blocked replay.
- The results are committed to `benchmarks/`.
- The independent audit agrees.

```sh
/opt/venv/bin/python scripts/benchmark.py --collect --as-of <date> --cache private/<stage>-cache --output private/<stage>-collection.md
/opt/venv/bin/python scripts/benchmark.py --cache private/<stage>-cache --output benchmarks/<stage>-results.md
```

- Passing unit tests, synthetic fixtures or a subset run (`--only`) do not complete a stage. They are useful for development.
- Record the commit, cutoff date and pass count. Commit the results Markdown and JSON. Keep the caches in `private/`, which is not committed.
- Never fabricate or hand-edit benchmark numbers. If a run cannot happen (missing credentials, provider outage), mark the stage as unverified.
- Never weaken a check to make a ticker pass. Label every proxy and analyst assumption, cite its filing source and show it in the workbook. No fallback may override a recorded fact conflict.
- If a gate fails twice, cut scope rather than slip the date. Drop utilities, oil and gas producers and acquisition-heavy pharma from the 50, and keep only working driver types.

## Working notes

- Tests: `python -m pytest -q`. Network is blocked in tests. LibreOffice export tests skip when `soffice` is missing.
- Collection costs OpenAI and Alpaca API usage. Run one benchmark process at a time.

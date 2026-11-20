# Phase 2 operating workbook

The general U.S. operating path uses the shared template. The existing Apple exporter remains callable, including through the benchmark's legacy flag. The public website and the dedicated company models have separate release paths.

The workbook has three to five fiscal years of historical statements and five forecast years. Forecast cash comes from the indirect cash-flow statement. The balance sheet itemizes normalized filing detail and retains explicit groupings where the filing feed does not provide a breakdown. Opening groupings use reported subtotals less the separately identified balances. Forecast groupings follow disclosed assumptions, such as scaling operating current balances with sales and holding nonoperating balances flat. No forecast line solves for the balance-sheet difference.

The template exposes net PP&E, combined D&A, days-based working capital, debt and interest, equity, dividends, repurchases, and book and unlevered tax calculations. The current operating forecast retains Phase 1's revenue drivers, WACC proxies, normalized reinvestment and P/E comps. Combined D&A is charged to net PP&E as a forecast proxy. Forecast margin sensitivity applies a constant EBIT margin and an additive annual sales-growth shock with fixed capex and working-capital ratios. The WACC and terminal-growth sensitivity holds forecast cash flows fixed. Each grid center must reproduce the DCF.

Blue inputs link to a Sources register. Black formulas drive the calculations. Checks shows the numerical differences, and the exporter checks the default workbook, Bear and Bull cases, and a temporary one-point margin edit. The original input is restored before delivery. The report checks its headline against the recalculated workbook and includes its SHA-256 fingerprint.

## Reproduce the benchmark

Run offline from this branch with the Phase 1 collection cache in the existing Codespace. Select four prior passes for workbook review, then replay the entire fixed universe when comparing coverage. This uses no provider calls.

```sh
CACHE=/workspaces/phase1-review/equity-research-builder-4598b5022e2e9d5d8dd0091fabdae002b6a6a784/private/benchmark-cache
/opt/venv/bin/python scripts/benchmark.py --cache "$CACHE" --only NVDA ADBE LMT HD --output private/phase2-four.md --artifacts private/phase2-review
/opt/venv/bin/python scripts/benchmark.py --cache "$CACHE" --output private/phase2-full.md
/opt/venv/bin/python scripts/benchmark.py --cache "$CACHE" --legacy-exporter --output private/phase2-legacy.md
```

Only verified file pairs are retained by the benchmark's artifact option. For workbook review, open the Excel file and inspect the three financial statements, the blue inputs and the Checks tab. In a copy, edit Assumptions B3 by one percentage point. The cover valuation and both sensitivity centers should move and all projected balance differences should remain zero.


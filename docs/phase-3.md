# Operating drivers

The collector stores candidate annual SEC table passages with their filing URL and period. The research response selects an enumerated business type and passage ID. The parser verifies the numeric cells in three fiscal-year columns against annual USD XBRL revenue. A disclosure for a business line requires a matching member and a separately modeled remainder. Unverified physical drivers use the existing segment method when its revenue basis reconciles. The PDF records the selected method and reason.

The workbook shows historical quantities, first-year driver guidance where explicitly labeled, and editable long-run growth assumptions. Industry rates are analyst proxies. Consolidated revenue guidance anchors the first forecast year when available. The benchmark reports the driver for each model reached and counts non-fallback drivers among verified pairs.

## Review findings

Starbucks FY2026 transferred 7,991 company-operated coffeehouses to its licensed business. The opening count was 21,514 and the closing count was 13,397. The year-end count cannot serve as the annual average for attributing company-operated sales. The current annual-table parser requires comparable average units, per-store sales, and comparable-sales growth. [FY2026 filing](https://www.sec.gov/Archives/edgar/data/829224/000082922426000162/sbux-20260927.htm)

FedEx reports volume per operating day, yields by service, and freight in pounds. For example, in the existing CY2025 Q1 recast, U.S. priority averaged 1.561 million packages per day at $26.43 over 63 operating days. Their product is $2,599.21 million against $2,600 million reported. The general annual-table adapter still needs a multi-period recast and a service rollup that handles rounding, freight units, and non-transport revenue. [FedEx recast](https://www.sec.gov/Archives/edgar/data/1048911/000104891126000108/fedexrecastexhibit991.htm)

## Benchmark

Use a separate cache directory for this phase. Earlier caches lack the new filing passages and research selection. In a configured Codespace on this branch, run the collection followed by the offline replay.

```sh
/opt/venv/bin/python scripts/benchmark.py --collect --as-of 2026-11-19 --cache private/phase3-cache --output private/phase3-collection.md
/opt/venv/bin/python scripts/benchmark.py --cache private/phase3-cache --artifacts private/phase3-review --output private/phase3-results.md
```

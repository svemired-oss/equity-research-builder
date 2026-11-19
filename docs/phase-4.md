# Phase 4 valuation

The shared U.S. operating pipeline collects split-adjusted daily stock bars and matches the last completed session in each calendar week to the S&P 500 price index. It requires 90 or more paired returns in the two-year window. Beta uses the regression slope adjusted two-thirds toward the observed beta and one-third toward 1. If the series is insufficient, it uses the median of at least two matching industry and business-driver peers. The WACC sheet shows the rate basis, dates, weights and sources.

[Alpaca historical prices](https://docs.alpaca.markets/us/reference/stockbars) · [S&P 500 at FRED](https://fred.stlouisfed.org/series/SP500) · [Damodaran monthly implied ERP](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/home.htm)

Interest expense divided by average filed debt supplies the debt-cost estimate. The fallback uses a dated BBB spread as a labeled rating-band proxy. The model uses carrying debt as a proxy for market debt where a traded debt value is unavailable. [FRED BBB spread](https://fred.stlouisfed.org/series/BAMLC0A4CBBB)

Eligible peers must share the verified driver and fall within a 0.25–4× market-cap band. For segment fallback, the peer must also use fallback and share the subject's three-digit SEC industry group. P/E requires positive earnings. P/E uses NTM USD GAAP diluted EPS when the subject and at least two eligible peers disclose it explicitly. Otherwise it uses comparable LTM EPS. The two enterprise-value multiples use LTM measures. Each method has at least two eligible observations and uses inclusive quartiles.

The exit valuation multiplies fifth-year EBITDA by the peer median and applies the same equity bridge as the Gordon DCF. Terminal values diverging more than 30% are marked for review. Reverse DCF solves for a constant revenue CAGR at the modeled margin and reports the existing implied margin at the base sales path. The two reverse solves hold other specified assumptions fixed.

The 12-month target blends the base DCF and peer valuation equally at the cutoff, rolls forward by the cost of equity, and subtracts the forecast dividend per share. The forecast dividend estimate weights the first two annual payouts by the remaining fraction of the current fiscal year. Rating uses total return and editable Buy/Hold/Sell thresholds. A greater than 40% gap between DCF and peer fair value, measured against peer fair value, triggers Review.

## Fixed benchmark

Run a fresh collection in the configured Codespace to obtain Phase 4 evidence. Earlier cache entries can still replay on their historical calculation basis. Sector medians use blended present fair value divided by the same-session close among verified Phase 4 pairs. A median below 0.7 or above 1.4 is marked Review. A sector without an eligible pair has no reported median.

```sh
/opt/venv/bin/python scripts/benchmark.py --collect --as-of 2026-11-19 --cache private/phase4-cache --output private/phase4-collection.md
/opt/venv/bin/python scripts/benchmark.py --cache private/phase4-cache --output private/phase4-results.md --artifacts private/phase4-review
```

# Plan to finish (agreed 2026-11-20)

Source of truth for scope, stages and gates. CLAUDE.md must follow this file.

## Definition of done (scoped product)
U.S. non-financial operating companies. Banks, REITs and foreign listings are out of scope.
v1 scope also excludes utilities, oil and gas producers and acquisition-heavy pharma (cut 2026-11-20 after the Stage 1 gate failed twice; see benchmarks/excluded-tickers.csv). The in-scope cohort is the 37 tickers in benchmarks/tickers.csv.
All eight criteria must pass on a fresh 37-ticker collection plus offline replay:

1. Coverage: 50% of the in-scope cohort produces a verified pair (19 of 37).
2. Real drivers: 10+ passes use stores, volume x yield, or units x price, each checked against its filing.
3. Statements: 5 years history + 5 forecast; every check is 0; year-1 net income within 25% of last actual unless the PDF explains why.
4. Valuation: sector median value / price between 0.7 and 1.4; comps use 3+ peers.
5. Report: thesis, business, industry, valuation, catalysts, bull/bear, risks filled for every pass; text matches the workbook.
6. Guided mode: 3 edited inputs regenerate both files on the hosted site.
7. Self-service: 10+ pairs download signed-out; password run works; daily API cap enforced.
8. Honest stops: every failed ticker shows a specific reason.

Baseline 2026-11-19: 6/50 (12%), 0 real drivers. Stage 1b (2026-11-19 cutoff): 10/50, 0 real drivers.

## GD V4 standard (the owner's own build process; the model is measured against it)
1. Sources: 10-K, 10-Q, annual reports (investor relations files where XBRL lacks a driver table).
2. Line items as the company reports them: revenue model, income statement, balance sheet.
3. 5 years of history, checked against the 10-Ks; latest 10-Q as a display-only column.
4. Revenue model in the company's own segments or drivers; growth % row under each line; a written reason per line.
5. Operating costs: separate cost table when disclosed, else projected in the income statement.
6. Balance sheet from each line's history; schedules for PP&E, long-term debt (history + guidance), AR/AP/inventory days, equity (retained earnings, treasury, surplus, AOCI).
7. Cash flow from balance-sheet changes; FCFE and closing cash; closing cash ties back; balance check.
8. Cost of equity sheet: CAPM with S&P 500 and company data; historical, forward and comparable betas (comparables' own D/E, tax rate, beta). No separate Comps sheets.
9. DCF: nominal FCFE at the cost of equity; terminal growth a labeled analyst assumption.
10. Scenarios: bull/base/bear by +/-0.5% segment revenue growth or margin shifts.
11. Cover: price, value per share, 12-month target, scenarios; all live formulas.

## Decisions (2026-11-20)
- FCFE at the cost of equity is the primary DCF; FCFF at WACC is a cross-check.
- Peer multiples are a football-field cross-check only; the target comes from the DCF.
- Code sets every number; the AI writes a one-line reason under each growth assumption, citing the filing or guidance passage.
- As-reported line items, cost tables and days-based working capital arrive in v2, not v1.

## Stages and gates
| Stage | Dates | Build | Gate |
| --- | --- | --- | --- |
| 1. Coverage fallbacks | Nov 20-24 | Total revenue as one labeled line when segments don't reconcile; missing current-debt line = 0 when total debt is filed and the balance sheet ties | 15+ pass on the 37-ticker cohort |
| 2. Valuation sanity | Nov 25-Dec 1 | FCFE primary + FCFF check; CAPM with historical/forward/comparable betas; multiples as cross-check from 3+ peers; carry other and equity-method income; growth-based bull/bear; pass only if value/price 0.5-2x | Sector medians 0.7-1.4 |
| 4a. Report minimum | Dec 1-2 | Business and risk text from the 10-K for every pass | No empty business, industry or risk section |
| 5. Release v1 | Dec 2-4 | Merge to main, deploy Render, publish 10 passing pairs | Signed-out download + password run both work |
| 3. Real revenue drivers | Dec 7-23 | Stores (SBUX, TGT, MCD), volume x yield (FDX, UPS, DAL), units x price; AI-written cited reasons | 10+ real-driver passes matching filings |
| 4b. Report depth | Dec 28-Jan 6 | Dated catalysts from 8-Ks and releases; bull/bear tied to driver changes; industry section | No empty section in any passing report |
| v2 release | Jan 7-8 | As-reported line items; rerun the 37; refresh public pairs | All 8 criteria pass |

## How we work
- One branch per stage. A stage is done only when its gate passes on a fresh full collection of the in-scope cohort plus offline replay, results are committed to benchmarks/, and the independent audit agrees.
- Never weaken a check to make a ticker pass. Label every proxy and analyst assumption.
- If a gate fails twice, cut scope rather than slip the date. The Stage 1 cut (2026-11-20) removed utilities, oil and gas producers and acquisition-heavy pharma: NEE, DUK, SO, AEP, EXC, XOM, CVX, COP, EOG, SLB, DHR, ABT, MRK. A further failure keeps only working driver types.

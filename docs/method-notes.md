# Valuation method notes (from reviewed Mayer Fund models)

Source: three peer-built, reviewed Mayer Fund models (a consumer staples company, a footwear and apparel
company, a hardware company), read on 2026-11-20. Only the method is recorded here. No figures, text or
company data from those models belong in code, tests or outputs. The GD V4 standard in docs/PLAN.md follows
the same method.

## The common method (all three models agree)

### Cost of equity
- CAPM: Rf + beta x ERP.
- Rf: 10-year U.S. Treasury yield on the valuation date.
- ERP: historical equity risk premium (Damodaran historical, 6.5% in all three), dated and cited.
  The implied S&P 500 ERP (forward P/E and consensus growth) is shown for reference only.
- Three betas, each turned into its own CAPM cost of equity, then a weighted average:
  1. Historical beta: 5-year monthly regression against the S&P 500.
  2. Comparable beta: each peer's beta unlevered with its own D/E and tax rate, mean of peers,
     relevered at the subject's D/E.
  3. Forward beta: implied from the subject's forward P/E and consensus EPS growth relative to the
     market's implied return. Needs consensus estimates.
- Weights: one third each (one model used 25% forward, 25% comparable, 50% historical).
- Peers live on the cost-of-equity sheet; there is no separate valuation-multiples output.

### DCF
- FCFE, not FCFF. FCFE = the year's change in cash from the three-statement model plus dividends and
  buybacks added back (cash available to equity after capex, working capital and net borrowing).
- Explicit forecast: 5 years (one model extended to 10 with fading growth).
- Terminal value: Gordon growth on the final explicit year's FCFE:
  TV = FCFE_final x (1 + g) / (Ke - g), with g a stated analyst assumption of 2.0-2.5%.
  There is no separate reinvestment or terminal-ROIC adjustment: reinvestment is already in the
  forecast cash flows.
- Partial first year: discounted by the fraction of the year remaining.
- Value per share = PV of FCFE + PV of TV, divided by current diluted shares.
- 12-month target: the same DCF re-dated one year forward, divided by the projected share count one
  year out.
- Share count: projected through a buyback and stock-based compensation schedule.
- Sensitivity: value per share across Ke +/- 0.5% steps and g +/- 0.25% steps.

### Revenue model
- Built in the company's own reporting segments, each with a growth row:
  - Consumer staples: segment revenue split into volume growth and price/product/geographic mix.
  - Footwear and apparel: brand x channel (wholesale vs direct-to-consumer), units x average selling
    price, and an international vs domestic split.
  - Hardware: segment, then sub-segment lines with year-over-year growth.
- Historical columns marked as actuals; assumption cells marked as inputs.

### Statements and schedules
- Income statement, balance sheet and cash flow derived from balance-sheet changes; closing cash ties.
- Schedules: buybacks, dividends per share, stock-based compensation, retained earnings, treasury stock,
  PP&E (capex as % of revenue, depreciation), intangibles amortization, operating leases.

## Rules for the automated model
1. Terminal value: Gordon growth on final-year FCFE, g = 2.5% default, labeled. Keep the ROIC-based
   terminal value only as a displayed check. Flag (do not change) a final year where capex is below D&A.
2. Cost of equity: CAPM with the historical ERP (dated, cited); the implied ERP shown for reference.
   Historical beta = 5-year monthly regression; comparable beta = relevered peer mean. Forward beta needs
   consensus estimates we do not have: label it "unavailable" and weight historical and comparable 50/50.
3. Share count: project buybacks at the trailing 3-year average buyback spend over the share price;
   12-month target uses the projected count one year out.
4. Sensitivity table: Ke +/- 0.5% and g +/- 0.25%.
5. Normalized margin (Stage 2d): remove disclosed unusual items from each historical year's operating income before
   taking the 3-year median operating margin: impairments, restructuring, litigation settlements, "other operating
   charges", changes in the fair value of contingent consideration, and asset-sale gains or losses. Use the largest of
   (a) a company-specific income-statement line labeled "Other operating charges" whose 10-K note itemizes
   impairments, remeasurements or restructuring (read from the filing's inline XBRL; KO 2024: $4,163 million),
   (b) the combined restructuring and impairment provision, or (c) restructuring + impairments + litigation +
   contingent consideration remeasurement, so an item inside an aggregate is not counted twice. The generic us-gaap
   OtherCostAndExpenseOperating tag holds recurring other operating expense for most filers (META, UPS, CSX and DAL
   in the 2026-11-21 run), so it is not treated as unusual on its own. Asset-sale gains are assumed to sit within operating income. Show the reported and normalized margins
   side by side (Assumptions rows 50-59).
6. Growth path: year 1 from guidance where filed (otherwise each line's recent growth faded halfway toward terminal
   growth); years 2-5 fade linearly from the 3-year consolidated revenue CAGR in year 2 to 4% nominal in year 5,
   applied to every revenue line; terminal growth stays 2.5%. Operating-driver paths keep their driver forecast.
7. Non-operating income: interest income, equity-method income and other nonoperating income are each carried forward
   at their own 3-year filed median and shown on the forecast income statement. Because that income is in FCFE, value
   per share = PV of FCFE + PV of terminal value, less noncontrolling interests and preferred equity; cash, short-term
   investments and equity-method investments are not added again. The FCFF cross-check keeps its enterprise-value
   bridge with cash.
8. Net borrowing: closing funded debt each forecast year = the last actual year's debt / EBITDA (reported operating
   income + D&A) times forecast EBITDA; interest accrues on opening debt and the change in debt is net borrowing in
   FCFE. Reverse DCF, implied margin and the margin x growth grid hold debt flows at the base path.

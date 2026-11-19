# AI Equity Research Builder

[Public recruiter portfolio](https://svemired-oss.github.io/equity-research-builder/) · [Private generator source, setup and demo guide](https://github.com/svemired-oss/equity-research-builder/tree/codespaces-demo#readme)

For the hosted demonstration, sign in to your private Codespace, enter **XTKS:7974**, select **Generate research**, then download the Excel financial model and PDF. The deployment branch documents private access, supported coverage, optional provider secrets and the $0/month infrastructure target within GitHub's free allowance. [Model coverage backlog](https://github.com/svemired-oss/equity-research-builder/blob/codespaces-demo/docs/model-backlog.md)

## Dated portfolio on this branch
A Python application that turns dated financial evidence into company-specific forecasts, valuations, editable Excel models, and cited research reports. The browser demo displays the valuation and scenario comparison for each covered company.

| Company | Operating forecast | Valuation |
| --- | --- | --- |
| General Dynamics (GD) | Gulfstream deliveries, segment margins, and backlog | Free cash flow to equity and trading comparables |
| FedEx (FDX) | Shipment volume, yield, and network operating costs | Continuing-operations DCF and trading comparables |
| Newmont (NEM) | Mine output, payable metal sales, prices, and reserve depletion | Finite mine-life NAV and peer P/E |

## Run the models

Use Python 3.10 or later. The dated inputs and their source links are in `inputs/`.

```bash
python -m pip install -r requirements.txt
python -m equity_research research-gd --as-of 2026-11-09
python -m equity_research value-fdx --as-of 2026-11-12
python -m equity_research value-nem --as-of 2026-11-12
```

### Guided example

Save the following as `gold-scenario.json` to use a $4,450 per ounce gold benchmark in Newmont’s 2027 forecast:

```json
{
  "overrides": [
    {
      "target": "fy_2027.gold.benchmark_usd",
      "value": 4450,
      "reason": "Test a higher 2027 gold price."
    }
  ]
}
```

```bash
python -m equity_research value-nem --as-of 2026-11-12 --guided gold-scenario.json
```

## How it works

1. Load dated company filings, earnings disclosures, market observations, and analyst assumptions. Each model validates its evidence cutoff and checks the source references.
2. Project revenue and linked financial statements using the operating drivers for that company. Keep scenario inputs separate from reported history.
3. Discount projected cash flows, test sensitivity to key assumptions, and compare implied values with public peers.
4. Export editable Excel formulas and a cited PDF. Verify the saved calculations against Python and tie the PDF to its exact workbook using a SHA-256 fingerprint.

The repository contains the modeling engine, dated evidence, and the website source. To serve these dated files locally, supply the six verified snapshot files under `outputs/`, then run `python -m equity_research serve-research`. FedEx and Newmont guided exports require Node.js and `@oai/artifact-tool`. The GD workbook export requires the original layout template. The source checkout supports the three dated valuations without those assets.

This portfolio branch covers three dated snapshots. Browser-guided runs use those snapshots as their starting point. The private live generator is maintained on the linked deployment branch.

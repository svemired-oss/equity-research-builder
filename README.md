# Equity Research Builder

Phase 0 adds an offline financial regression suite and a fixed 50-ticker benchmark on `phase-0-baseline`. See [test and benchmark instructions](docs/phase-0.md). The checked-in initial results identify missing caches. A credentialed collection run is required to establish financial coverage.

Run a private ticker-to-download demo in GitHub Codespaces. Hosted acceptance on November 19, 2026 produced verified Excel and PDF downloads for Nintendo (`XTKS:7974`) and NVIDIA (`NVDA`). The public recruiter portfolio displays dated General Dynamics, FedEx and Newmont valuations. [Public portfolio](https://svemired-oss.github.io/equity-research-builder/)

## Give a two-minute demonstration

1. Sign in to GitHub as the Codespace owner. Open the existing Codespace and select **Restart codespace** if it is stopped. Under **Ports**, open the browser link for private port **8053**. Bookmark that link for subsequent visits. [Your Codespaces](https://github.com/codespaces)
2. Enter **XTKS:7974**, keep **Automated** selected and choose **Generate research**. When the download links appear, select **Excel financial model** and **Equity research report**. Open both. Compare the value per share on the workbook’s **Cover** with the first line of the report.
3. To show Guided mode, select **Edit assumptions**. Change the post-guidance operating margin to `0.20`, enter a reason and choose **Regenerate research**. Download the new pair. The PDF records the change and the fingerprint of the workbook it accompanies.

## First-time Codespaces setup

1. Use a personal GitHub account with Codespaces enabled. In billing, keep paid Codespaces overage blocked for a zero-infrastructure-spend demo.
2. Create a **Codespaces** secret named `SEC_USER_AGENT` containing a monitored contact email. A complete project name and email also works. The server adds its project name to an email-only setting. Grant the secret access to this repository. Nintendo uses public sources and requires no paid API. [Manage Codespaces secrets](https://github.com/settings/codespaces)
3. Open the **codespaces-demo** branch. Select **Code → Codespaces → Create codespace** on this branch, using a two-core machine. The included dev container installs Python, the pinned packages and LibreOffice Calc. The post-start command launches the server on loopback. [Deployment branch](https://github.com/svemired-oss/equity-research-builder/tree/codespaces-demo)
4. In **Ports**, confirm **8053** is **Private** and open it in the browser. GitHub supplies HTTPS and handles owner authentication. Resume the Codespace before a presentation. A recruiter can use the public portfolio independently or watch the owner’s live demo. [GitHub port access](https://docs.github.com/en/codespaces/developing-in-a-codespace/forwarding-ports-in-your-codespace)
5. Optional U.S. research uses three additional repository-scoped Codespaces secrets: `OPENAI_API_KEY`, `APCA_API_KEY_ID` and `APCA_API_SECRET_KEY`. Stop and resume after adding or changing secrets. The website then offers generation where the engine supports it. Provider access, filing quality and model coverage determine the outcome. Keep keys in the host secret manager.

For native Linux installation and command-line verification, use the [portable setup](https://github.com/svemired-oss/equity-research-builder/blob/codespaces-demo/docs/portable-builder.md).

## What to expect

| Input | Demonstrated behavior |
| --- | --- |
| Nintendo, `XTKS:7974` | Live editable yen DCF, sensitivity table and a cited one-page PDF. Guided changes the post-guidance margin. The result is a balance-date scenario marked **Review**. |
| NVIDIA, `NVDA`, with U.S. providers enabled | Automated generation produced 13 workbook sheets, 280 formulas and a four-page PDF. The November 19 test matched at $121.44 per share and the PDF fingerprint matched the downloaded workbook. The five forecast balance checks tied. Live inputs and AI text can change on subsequent runs. |
| GD, FAST, SBUX and DHR with U.S. providers enabled | Each reached the financial engine. Each stopped on a specific filing or forecast gap. See the issuer-level backlog. |
| General U.S. research without provider credentials | SEC discovery and annual filing history. A filing check produces no downloads. |
| GD, FDX and NEM on GitHub Pages | Dated portfolio illustrations with scenario comparisons. Public downloads are disabled. |

The live-only demo runs from the source checkout without the original GD template, private reference files or pre-generated downloads. The desk-model fidelity and coverage work is tracked in the [model backlog](https://github.com/svemired-oss/equity-research-builder/blob/codespaces-demo/docs/model-backlog.md).

## Recovery and operating costs

- Refresh the same tab to return to its latest job. **Check this run again** reconnects after a dropped response. Completed files are retained for 24 hours while the Codespace exists. The site shows their expiry. A restart interrupts unfinished jobs. Start a fresh Automated run to enable Guided after a restart.
- One worker processes up to four running or queued submissions. The whole demo shares eight new runs per rolling hour and 20 per rolling 24 hours. Filing checks and Guided submissions count. Duplicate in-flight submissions share a job. Each file pair is limited to 16 MiB.
- Expected infrastructure bill is **$0/month** within the personal GitHub Free allowance of 120 core-hours and 15 GB-month. For example, a two-core Codespace running 10 hours uses 20 core-hours. Storage includes the custom image and persists while stopped. Paid overage, if enabled, is $0.18 per two-core running hour and $0.07 per GB-month beyond the allowance. With overage blocked, exhaustion can interrupt the demo. [GitHub pricing](https://docs.github.com/en/billing/concepts/product-billing/github-codespaces)
- Nintendo has no OpenAI or Alpaca usage. Optional U.S. generation can incur separate provider charges. A ChatGPT subscription does not fund API calls. Stop the Codespace after a demo to conserve compute.

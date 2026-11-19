# GitHub-only portfolio demo

Use the existing GitHub Pages site for the public portfolio. Run the live builder on demand in a personal GitHub Codespace when presenting. The forwarded port defaults to private and requires the creator's GitHub login. A recruiter can view Pages independently or watch a live generation during a screen share.

1. Put the reviewed portable sources, Dockerfile, browser assets and the included `.devcontainer` directory on the GitHub branch you will run. Keep the live demo branch separate from `gh-pages`.
2. Under personal GitHub settings, add repository-scoped **Codespaces** secrets. Set `SEC_USER_AGENT` to the project name and monitored contact. For general US company research also set `OPENAI_API_KEY`, `APCA_API_KEY_ID`, and `APCA_API_SECRET_KEY`. Nintendo (`XTKS:7974`) uses public data and needs only the contact.
3. Use personal billing, check remaining included storage and compute, and keep metered overage disabled. GitHub Free personal includes 120 core-hours and 15 GB-month of storage, shared with your other codespaces. Two cores consume two core-hours per running hour. With no valid payment method GitHub blocks further use when the allowance is exhausted. If a payment method is present, verify a zero spending budget with usage stopped at the limit before launch.
4. On the prepared branch select **Code → Codespaces** and create a two-core codespace. The build installs Python and LibreOffice. The startup command launches the generator. In **Ports**, keep port 8053 **Private**, then open its forwarded URL.
5. Enter a supported ticker and generate research. Refreshing the same browser tab resumes its latest run. If the connection drops, use **Check this run again** to recover the same submission. Verified downloads are available for 24 hours from completion while the Codespace exists, including after a stop and restart. The website displays the expiration time. Deleting the Codespace removes its archived downloads.

The private builder runs one worker with up to four active or queued jobs. It accepts eight new jobs per rolling hour and 20 per rolling 24 hours across the demo. Repeated requests for the same job do not use additional slots. A restart preserves completed files and marks unfinished jobs as interrupted. To use Guided mode after a restart, start a new Automated run first. Download pairs are capped at 16 MiB.

Stopping the Codespace releases active compute. A stopped Codespace still consumes storage.

API charges follow the API provider's billing separately. The GitHub Pages site continues to show the dated examples while the codespace sleeps or is deleted.

[GitHub Codespaces billing](https://docs.github.com/en/billing/concepts/product-billing/github-codespaces) · [Private ports](https://docs.github.com/en/codespaces/reference/security-in-github-codespaces) · [Codespaces secrets](https://docs.github.com/en/codespaces/managing-your-codespaces/managing-your-account-specific-secrets-for-github-codespaces)

# GitHub-only portfolio demo

Use the existing GitHub Pages site for the public portfolio. Run the live builder on demand in a personal GitHub Codespace when presenting. The forwarded port defaults to private and requires the creator's GitHub login. A recruiter can view Pages independently or watch a live generation during a screen share.

1. Put the reviewed portable sources, Dockerfile, browser assets and the included `.devcontainer` directory on the GitHub branch you will run. Keep the live demo branch separate from `gh-pages`.
2. Under personal GitHub settings, add repository-scoped **Codespaces** secrets. Set `SEC_USER_AGENT` to the project name and monitored contact. For general US company research also set `OPENAI_API_KEY`, `APCA_API_KEY_ID`, and `APCA_API_SECRET_KEY`. Nintendo (`XTKS:7974`) uses public data and needs only the contact.
3. Use personal billing, check remaining included storage and compute, and keep metered overage disabled. GitHub Free personal includes 120 core-hours and 15 GB-month of storage, shared with your other codespaces. Two cores consume two core-hours per running hour. With no valid payment method GitHub blocks further use when the allowance is exhausted. If a payment method is present, verify a zero spending budget with usage stopped at the limit before launch.
4. On the prepared branch select **Code → Codespaces** and create a two-core codespace. The build installs Python and LibreOffice. The startup command launches the generator. In **Ports**, keep port 8053 **Private**, then open its forwarded URL.
5. Enter a supported ticker, generate research, and save both downloads before stopping the codespace. Stopping it releases the active compute and clears the in-memory runs. A stopped codespace still consumes storage. Delete it when finished if you do not need to retain it.

API charges follow the API provider's billing separately. The GitHub Pages site continues to show the dated examples while the codespace sleeps or is deleted.

[GitHub Codespaces billing](https://docs.github.com/en/billing/concepts/product-billing/github-codespaces) · [Private ports](https://docs.github.com/en/codespaces/reference/security-in-github-codespaces) · [Codespaces secrets](https://docs.github.com/en/codespaces/managing-your-codespaces/managing-your-account-specific-secrets-for-github-codespaces)

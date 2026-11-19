# Phase 7 release

## Public Pages

The Pages build can publish the independently verified, dated GD, FDX and NEM snapshots. The older GD copy passes the existing metadata sanitization and fingerprint rebind. The staging command also accepts ten additional cached, automated file pairs with at least two driver types. Each pair must have a passing fixed-universe result, both recorded fingerprints, a verified workbook-report match and a clean public-file inspection. Use a dedicated directory containing only the nominated benchmark pairs.

The current release has three dated pairs. The public benchmark panel states the Phase 1 result supplied after the credentialed Codespace run and links to the complete report. There are no additional eligible pairs in the local release inputs.

```sh
python scripts/package_pages.py --root /path/to/private-project --destination private/pages-stage --allow-incomplete
```

For the full release, omit `--allow-incomplete` and pass `--benchmark`, `--phase`, `--source` and `--artifacts` for the matching fresh run. Store benchmark work outside the public Pages branch. Only stage the generated assets, catalog and verified pairs.

## Private live service

Import `render.yaml` into Render from `phase-7-release`. The single Starter service is $7 per month and the 1 GB persistent disk is $0.25 per month. The estimated infrastructure total is **$7.25 per month** before any usage overages and provider charges. The paid disk is needed to preserve the spend ledger and cached downloads across restarts. The 512 MB Starter plan still needs a hosted memory and generation smoke test. [Render pricing](https://render.com/pricing)

Set the recruiter password, SEC contact, OpenAI project API key and Alpaca keys as Render secrets. The service listens on the Render port, receives managed HTTPS and uses browser HTTP Basic authentication with username `recruiter`. Configure a unique password of at least 16 characters and rotate it when access should end. The public health route exposes only readiness.

One worker and the existing shared queue and rate limits control concurrency. Successful Automated reruns for the same request reuse that UTC day's cached pair. Downloads are still retained for 24 hours. Guided editing needs the in-memory evidence from an Automated run. Previously cached pairs can be downloaded after restarts even when their Guided evidence is gone.

The configured **$0.50 daily OpenAI cap** tracks worst-case reservations before each provider request, including retries and report generation. It uses the pinned GPT-5 mini snapshot's uncached full-context input ceiling and each call's bounded output. Reservations are retained even after an interrupted request and reset for new requests at midnight UTC. The cap applies to this server's OpenAI calls and does not cover other apps using the same key, Alpaca fees or hosting charges. The host rejects startup if the persistent disk or the configured cap is missing. [OpenAI model and pricing](https://developers.openai.com/api/docs/models/gpt-5-mini)

Hosted acceptance requires a signed-out Pages download, a credentialed browser request for a real supported ticker, and confirmation that an exhausted cap blocks paid work while retaining downloads. The automated tests exercise route protection, persistent cache and spend rejection with simulated provider calls.


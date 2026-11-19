# Install the private live builder

The portable configuration uses Python and LibreOffice Calc. Use a writable checkout on Ubuntu 26.04.

```sh
sudo apt-get update
sudo apt-get install --no-install-recommends ca-certificates libreoffice-calc python3-venv
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
export SEC_USER_AGENT='Equity Research Builder your-contact@example.com'
.venv/bin/python -m equity_research serve-research --live-only --port 8053
```

Replace the example contact with a real monitored address. On the machine running the server, open `http://127.0.0.1:8053/` and enter `XTKS:7974`. This Nintendo path uses public filings, exchange pricing and a Japanese government yield. The reusable U.S. operating-company path also requires `OPENAI_API_KEY`, `APCA_API_KEY_ID` and `APCA_API_SECRET_KEY`. Supply them through your environment or hosting secret manager. `RESEARCH_MODEL` optionally selects the OpenAI API model. Provider availability and quotas affect live runs.

From a second terminal, verify a generation and assumption change:

```sh
.venv/bin/python -m equity_research.acceptance --foreign --guided --output private/portable-acceptance XTKS:7974
```

The test checks workbook formulas and saved values, the valuation and sensitivity tie, and the workbook fingerprint in the PDF.

## Container recipe

The Dockerfile installs the same dependencies and runs as an unprivileged account. The build context allows application Python sources, three browser assets, and the requirements file.

```sh
docker build -t equity-research-builder .
docker run --rm --network host --env SEC_USER_AGENT equity-research-builder
```

These commands are for a Linux Docker host. Pass each credential by name with an additional `--env` option for the credentialed path. The server listens on loopback and enforces localhost requests. A standard Docker port mapping alone cannot reach it.

## Deployment boundaries

- Live export uses openpyxl and LibreOffice in place of the bundled Node spreadsheet package. It uses the same workbook plan, intended cell values, scenario checks and assumption probe. Styling can differ between renderers.
- Live-only startup works without saved example downloads, dated input files, or the original GD V4 workbook. The separate dated portfolio and GD template workflow retain those dependencies. Keep source models, generated files, credentials, and evidence caches private.
- The application needs a writable `private/web-runs` directory for temporary rendering. Completed downloads and active jobs are held in memory and expire on restart.
- The clean install test uses native Ubuntu. The Docker recipe still needs an image build and smoke test on a Docker host.

A hosted private release still needs an HTTPS access layer on the same host that proxies to loopback, runtime secrets for credentialed research, and hosted acceptance tests. The public portfolio has a separate deployment.

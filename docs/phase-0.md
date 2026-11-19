# Phase 0 baseline and test instructions

Use the `phase-0-baseline` branch based on `codespaces-demo`.

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

The tests use synthetic JSON data in `tests/fixtures`. The values in `expected.json` were captured once from the pre-migration engine. They characterize its current behavior. For example, the FCFF fixture has discounted enterprise value 1,446.2809917355369, an equity adjustment of -30 and 10 million shares, producing 141.6280991735537 per share. Parser fixtures are synthetic Nintendo-shaped text and a generated PDF. They establish parsing behavior without redistributing issuer documents. The network-blocking pytest plugin rejects socket calls.

The five web endpoint tests use a synthetic catalog, fixed download bytes and test defaults. Their hashing, input validation, request isolation and access checks still run. Workbook authoring is mocked at that HTTP boundary. Numerical and parser tests run separately.

## Benchmark

The fixed research cohort has 50 symbols, five in each of the ten requested sectors. These are research groupings and can differ from formal industry classifications. All tickers use the general U.S. operating path, including symbols with dedicated legacy models.

In the configured Codespace, with its existing four provider settings and LibreOffice available, run the initial collection and verification:

```sh
/opt/venv/bin/python scripts/benchmark.py --collect --as-of 2026-11-19
```

The collector uses the existing web pipeline to retrieve facts, select reconciled revenue groups, verify prices and peers, then saves its inputs or collection failure under `private/benchmark-cache`. API charges apply. Existing cache entries are reused, including recorded failures. For a new dated baseline, choose a new cache directory with `--cache private/benchmark-2026-11-20`.

To repeat the baseline with network access blocked, run:

```sh
/opt/venv/bin/python scripts/benchmark.py --cache private/benchmark-cache
```

The runner writes `benchmarks/results.md` and `benchmarks/results.json` after every ticker. Each result includes the stage, failure reason, elapsed time, input-cache hash, and value/price ratio when valuation succeeds. A pass requires actual workbook and report export plus the existing independent acceptance checks, including positive verified peer valuation. Source and model checks retain their existing thresholds. Saved inputs retain their original cutoff during replay.

The private cache contains public research evidence and provider decisions. Review it before redistribution. Generated workbooks and PDFs are temporary and remain in private working storage. The benchmark bypasses the website queue and its per-hour cap, so run one benchmark process at a time on a separate test checkout.

Missing caches count as failures in the full-set rate and are shown separately. A run with missing caches is an infrastructure report. A full model baseline requires a recorded collection attempt for all 50 companies. Test fixture numbers are never used as company benchmark evidence.

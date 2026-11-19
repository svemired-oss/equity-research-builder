# Phase 0 baseline and test instructions

Use the `phase-0-baseline` branch based on `codespaces-demo`.

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

The tests use synthetic JSON data in `tests/fixtures`. The values in `expected.json` were captured once from the pre-migration engine. They characterize its current behavior. For example, the FCFF fixture has discounted enterprise value 1,446.2809917355369, an equity adjustment of -30 and 10 million shares, producing 141.6280991735537 per share. Parser fixtures are synthetic Nintendo-shaped text and a generated PDF. They establish parsing behavior without redistributing issuer documents. The network-blocking pytest plugin rejects socket calls.

The five web endpoint tests use a synthetic catalog, fixed download bytes and test defaults. Their hashing, input validation, request isolation and access checks still run. Workbook authoring is mocked at that HTTP boundary. Numerical and parser tests run separately.

The Linux integration test exports a real synthetic workbook through LibreOffice and checks its PDF using the existing acceptance reader. It passed locally with all 30 tests, including the later collector-state and retry regressions. Hosts without LibreOffice skip that integration test. CI installs LibreOffice so the test runs there.

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

## Initial hosted result

The owner supplied the full output of collector `e26dec2` on November 19, 2026. It recorded all 50 attempts with no missing caches and seven verified pairs (14%). The successful symbols were NVDA, ADBE, KO, LMT, WMT, COST and HD. The committed report preserves the supplied four-decimal ratios and failure text. The raw caches and files remain in the owner's Codespace.

The initial XOM result revealed a benchmark bug. A screening-only response called the service's normal completion handler, but the harness had bypassed service initialization. The repaired collector uses an initialized service and reports that response's actual evidence gap. Its offline regression exercises this exact branch. ORCL's unexpected `dimensions` key error remains a collector problem for a later phase.

After updating `scripts/benchmark.py` in the collection checkout, run:

```sh
/opt/venv/bin/python scripts/benchmark.py --collect --refresh XOM --as-of 2026-11-19
/opt/venv/bin/python scripts/benchmark.py
```

The first command archives the old XOM cache, recollects only XOM and replays every other cached input. The second command blocks network access and repeats all 50 cached outcomes. The owner completed both with runner `72603e4` and supplied the final replay report. XOM now stops on missing normalized annual financials. The seven passes and 14% rate were unchanged. The committed baseline identifies these results as owner-supplied. The existing checks and financial calculations retain their current behavior.

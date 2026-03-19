# seed-ensembler

Coverage-aware seed filtering for multi-CRS ensemble fuzzing.

Ported from [Team-Atlanta/seed_ensembler](https://github.com/Team-Atlanta/seed_ensembler) with all competition-specific dependencies removed (Kafka, VAPI, libatlantis, protobuf, watchdog).  What's left is the libfuzzer merge/parse core — no external deps beyond the standard library.

See [#103](https://github.com/sslab-gatech/oss-crs/issues/103) for the full proposal.

## Why this exists

The exchange sidecar copies every seed between CRSs, deduplicating only by filename.  Most of those seeds add zero new coverage and just waste fuzzer time.  The original seed_ensembler solved this during the AIxCC competition by running `libfuzzer -merge=1` on each batch, keeping only coverage-adding seeds and flagging crashes separately.

This package is that same logic, minus everything that tied it to the competition infra.

## How it works

```
                     ┌─────────────────────┐
  new seeds ────────>│   LibfuzzerPool      │
  (batch dirs)       │                      │
                     │  Worker 0 ──┐        │
                     │  Worker 1 ──┤        │
                     │  Worker N ──┘        │
                     │       │              │
                     │  libfuzzer -merge=1  │
                     │  (Docker container)  │
                     └───┬──────────┬───────┘
                         │          │
              new_seeds_queue    crash_queue
              (coverage gain)   (crashes/timeouts)
```

Each worker process:

1. Takes a batch of seeds + a harness from the input queue
2. Runs `libfuzzer -merge=1` inside a Docker container with the existing coverage corpus and the new seeds
3. Seeds that added coverage get moved to the shared corpus dir and reported on `new_seeds_queue`
4. Crash-triggering seeds get mapped back to their input files (SHA-1 matching with fallbacks) and reported on `crash_queue`
5. Timeout seeds go to a slow-seed queue for re-testing with a longer timeout

The crash attribution logic (`map_failures_to_inputs` in pool.py) is the tricky part — libfuzzer's stderr doesn't always tell you which input caused which crash, especially across multi-attempt merges.  The alignment algorithm in `libfuzzer_result.py` handles this by matching "Test unit written to" and "SUMMARY:" lines using merge-outer attempt checkpoints.

## Package layout

```
seed_ensembler/
├── config.py              Configuration dataclass
├── constants.py           DEFAULT_SCORABLE_TIMEOUT_DURATION (65s)
├── harness.py             Harness metadata (name, binary path, timeout config)
├── libfuzzer_result.py    Parse libfuzzer stderr — crash detection, sanitizer
│                          identification, checkpoint alignment algorithm
├── libfuzzer_handler.py   Docker-based libfuzzer execution, mount mapping,
│                          command construction
├── pool.py                Multiprocessing worker pool, batch processing,
│                          seed-to-crash attribution, slow seed re-testing
├── util.py                .options file parsing, rsync copy, log compression
└── test_data/             Real libfuzzer merge output (nginx harness)
    ├── libfuzzer_output_01.txt   15 exits
    ├── libfuzzer_output_02.txt   9 AddressSanitizer crashes
    ├── libfuzzer_output_03.txt   1 timeout
    └── libfuzzer_output_04.txt   1 heap-buffer-overflow
```

## Running tests

```bash
cd oss-crs-infra/seed-ensembler
uv venv && uv pip install -e . && uv pip install pytest pytest-cov
uv run pytest tests/ -v
```

69 tests, all unit-level (no Docker needed).

## Supported languages

| Language | Harness format | Runner image | Status |
|----------|---------------|--------------|--------|
| C/C++ | ELF binary | `base-runner` | Supported |
| Java (Jazzer) | Shell script wrapping `jazzer_driver` | `base-runner` (includes JVM) | Supported |

**Java/Jazzer works out of the box** because `jazzer_driver` is a libfuzzer wrapper.  The shell script harnesses pass `$@` through to `jazzer_driver`, so `-merge=1` and all other libfuzzer flags are forwarded transparently.  Stderr output uses the same `MERGE-OUTER`/`SUMMARY` format.  The runner image (`gcr.io/oss-fuzz-base/base-runner`) already includes a JVM runtime.

Verified against `afc-apache-commons-compress` (CompressTarFuzzer harness) with `crs-jazzer` build output.

## TODO

Roughly follows the phased approach in #103.

Per-harness seed routing is already handled by the orchestrator (`workdir.py`).  Each harness gets its own compose stack, so `EXCHANGE_DIR` and `SUBMIT_DIR` are scoped per-harness at mount time.  No changes needed to libCRS or the exchange sidecar for this.

**Sidecar integration**

- [ ] `__main__.py` entry point (polling loop, similar to exchange sidecar)
- [ ] Dockerfile using the oss-crs owned builder image (not an arbitrary CRS's BUILD_OUT_DIR)
- [ ] Mount BUILD_OUT_DIR into the sidecar so it can access harness binaries
- [ ] Wire crash seeds through `libCRS submit pov`
- [ ] Make filtering configurable (able to turn it off)
- [ ] Compose template integration

**Other**

- [ ] Direct execution mode (skip Docker when the sidecar image already has the runtime)
- [ ] Metrics / logging integration for the future WebUI dashboard

## E2e testing plan

Unit tests cover the parsing and path logic, but we can't know this actually works until we run it against a real harness with real seeds.  Rough plan:

1. Pick a small C target from oss-fuzz (something that builds fast and crashes easily).  Build it once, stash the `$OUT/` dir as a test fixture.
2. Write a test that feeds a handful of seeds (some good, some junk, one crash-triggering) through `LibfuzzerPool` with a real Docker container.  Assert: coverage-adding seeds end up in the corpus, crash seeds land in the crash queue, junk gets dropped.
3. Once the sidecar entry point exists, run it inside a minimal compose stack (one CRS, one harness) and verify seeds flow through `SUBMIT_DIR` -> ensembler -> `EXCHANGE_DIR` correctly.
4. For Java/Jazzer: same E2E approach with a JVM target using `base-runner`.

Tests in steps 2-4 need Docker, so they should be marked `@pytest.mark.skipif(not docker_available())` like the existing integration tests in `oss_crs/tests/integration/`.

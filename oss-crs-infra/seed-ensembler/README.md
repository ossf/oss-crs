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

## TODO

Roughly follows the phased approach in #103.

**Phase 2 — per-harness seed routing**

- [ ] Update exchange data model so seeds are organized per-harness, not flat
- [ ] Update libCRS `submit`/`fetch` APIs to match

**Phase 3 — sidecar integration**

- [ ] `__main__.py` entry point (polling loop, similar to exchange sidecar)
- [ ] Dockerfile using the oss-crs owned builder image (not an arbitrary CRS's BUILD_OUT_DIR)
- [ ] Mount BUILD_OUT_DIR into the sidecar so it can access harness binaries
- [ ] Wire crash seeds through `libCRS submit pov`
- [ ] Make filtering configurable (able to turn it off)
- [ ] Compose template integration

**Other**

- [ ] Java/Jazzer support — Jazzer uses libfuzzer under the hood so the stderr parsing mostly works, but the execution environment needs JVM + classpath handling
- [ ] Direct execution mode (skip Docker when the sidecar image already has the runtime)
- [ ] Metrics / logging integration for the future WebUI dashboard

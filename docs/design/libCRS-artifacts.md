# libCRS Artifact Exchange & Directory Sync

## Overview

Inter-CRS collaboration in OSS-CRS is built around standardized artifact sharing. CRS containers submit findings, fuzzing seeds, candidate patches, and bug reports through `libCRS`. These artifacts are deduplicated, validated, and shared with sibling CRSs via the shared exchange sidecar.

---

## 1. Supported Artifact Types

All submission and fetching operations use standardized artifact types:

| Artifact Type | Subdirectory | Description |
|---|---|---|
| `pov` | `povs/` | Proof-of-vulnerability inputs that trigger crashes or violations |
| `seed` | `seeds/` | Corpus test cases to increase fuzzing code coverage |
| `bug-candidate` | `bug-candidates/` | Potential bug reports and SARIF 2.1.0 findings for verification |
| `report` | `reports/` | Detailed CRS-native verification, root-cause, or analysis reports |
| `patch` | `patches/` | Unified diff patches intended to fix discovered vulnerabilities |
| `diff` | `diffs/` | Reference diffs used for delta-mode analysis |

---

## 2. Directory Architecture & Data Flow

```text
   CRS Container                                          Exchange Sidecar
 ┌──────────────────────┐                               ┌──────────────────┐
 │ Local Directory      │                               │                  │
 │ (e.g. /work/crashes) │                               │                  │
 └──────────┬───────────┘                               │                  │
            │ File added                                │                  │
            ▼                                           │                  │
   SubmitHelper (watchdog)                              │                  │
   - Computes MD5 hash                                  │                  │
   - Batches files                                      │                  │
            │                                           │                  │
            ▼                                           │                  │
   SUBMIT_DIR/povs/<hash> ─────────────────────────────▶│  EXCHANGE_DIR/   │
   (per-CRS write mount)                                │  (Shared Global) │
                                                        └─────────┬────────┘
                                                                  │
                                                        Distributes to all CRSs
                                                                  │
                                                                  ▼
   FETCH_DIR/povs/<hash> ◀────────────────────────────────────────┘
   (per-CRS read-only mount)
            │
            ▼
   FetchHelper (poller)
   - Discovers new hashes
   - Copies to local /shared-povs
```

---

## 3. One-Shot Submission & Fetching

### Submitting Artifacts
```bash
# Submit an individual artifact
$ libCRS submit pov /path/to/crash.bin
$ libCRS submit seed /path/to/interesting_input.raw
$ libCRS submit bug-candidate /path/to/finding.sarif
$ libCRS submit patch /path/to/fix.patch
$ libCRS submit report /path/to/analysis.md
```

### Fetching Artifacts
```bash
# Fetch new artifacts of a given type from FETCH_DIR into a local directory
$ libCRS fetch pov /shared-povs
$ libCRS fetch seed /shared-seeds
$ libCRS fetch patch /shared-patches
```

---

## 4. Background Directory Registration

For automated, continuous synchronization without modifying your agent's core loop, `libCRS` provides background filesystem watchers:

### `register-submit-dir`
Monitors a local directory using filesystem events (`watchdog`). When new files are written, `libCRS` hashes them, batches them, and copies them to `SUBMIT_DIR`:
```bash
$ libCRS register-submit-dir pov /work/fuzzer/crashes
$ libCRS register-submit-dir seed /work/fuzzer/corpus
$ libCRS register-submit-dir patch /work/agent/generated_patches
```

### `register-fetch-dir`
Spawns a daemon polling `FETCH_DIR` every 5 seconds. New artifacts shared by other CRSs are automatically copied into the specified directory:
```bash
$ libCRS register-fetch-dir pov /work/incoming_povs
$ libCRS register-fetch-dir seed /work/incoming_seeds
```

### `register-shared-dir`
Shares arbitrary scratch files directly between containers belonging to the same CRS:
```bash
$ libCRS register-shared-dir /local/cache /shared-fs/cache
```

### `register-log-dir`
Flushes CRS agent logs to a host-persisted log directory:
```bash
$ libCRS register-log-dir /var/log/my-crs
```

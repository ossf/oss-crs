# libCRS — CRS Communication Library

## Overview

libCRS is a Python CLI library installed in every CRS container. It provides a uniform interface for CRS containers to interact with the OSS-CRS infrastructure — submitting artifacts (seeds, PoVs, bug candidates), sharing files between containers within a CRS, managing build outputs, and resolving service endpoints.

By abstracting these operations behind a single CLI, libCRS allows CRS developers to write infrastructure-agnostic code: the same `libCRS` commands work regardless of whether the CRS runs locally via Docker Compose or (in the future) on a cloud deployment.

## Installation

libCRS is installed inside CRS container images during the Docker build phase. The provided `install.sh` script handles the full setup:

```bash
# Inside a Dockerfile
COPY libCRS /opt/libCRS
RUN /opt/libCRS/install.sh
```

This installs:
- [uv](https://github.com/astral-sh/uv) (Python package manager)
- `rsync` (used for file operations)
- The `libCRS` CLI tool, available at `/usr/local/bin/libCRS`

### Dependencies

- Python >= 3.10
- `watchdog >= 6.0.0` (filesystem event monitoring for `register-submit-dir`)
- `requests >= 2.28.0` (HTTP client for builder sidecar communication)
- `rsync` (installed automatically by `install.sh`)

## Environment Variables

libCRS relies on several environment variables injected by CRS Compose at container startup:

| Variable | Description |
|---|---|
| `OSS_CRS_RUN_ENV_TYPE` | Execution environment type (`local`) |
| `OSS_CRS_NAME` | Name of the CRS (used for network domain resolution and metadata) |
| `OSS_CRS_BUILD_OUT_DIR` | Shared filesystem path for build outputs |
| `OSS_CRS_SUBMIT_DIR` | Shared filesystem path for submitted artifacts (seeds, PoVs, etc.) |
| `OSS_CRS_SHARED_DIR` | Shared filesystem path for inter-container file sharing within a CRS |
| `OSS_CRS_LOG_DIR` | Writable filesystem path for persisting CRS agent/internal logs to the host |
| `OSS_CRS_FETCH_DIR` | Read-only filesystem path for fetching inter-CRS data and bootup data (set on run containers, and on build-target builder containers when directed inputs are provided) |
| `OSS_CRS_REBUILD_OUT_DIR` | Shared filesystem path for rebuild artifacts (written by builder sidecar, read by CRS modules) |
| `BUILDER_MODULE` | Builder sidecar service name for DNS resolution (framework-injected, defaults to `builder-sidecar`) |
| `OSS_CRS_FUZZ_PROJ` | Read-only mount containing the fuzz project directory (set on all CRS containers) |
| `OSS_CRS_TARGET_SOURCE` | Read-only mount containing the target source directory (set on all CRS containers) |

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                      CRS Container                       │
│                                                          │
│   CRS Code  ──▶  libCRS CLI  ──▶  libCRS Library        │
│                                        │                 │
│                                        ▼                 │
│                                  ┌──────────┐            │
│                                  │ CRSUtils │            │
│                                  └────┬─────┘            │
│                                       │                  │
└───────────────────────────────────────┼──────────────────┘
                                        │
               ┌────────────┬───────────┼──────────┬──────────┐
               ▼            ▼           ▼          ▼          ▼
          ┌─────────┐ ┌──────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐
          │  Build  │ │ Submit / │ │ Shared  │ │  Log    │ │ Network │
          │ Output  │ │  Fetch   │ │   FS    │ │   Dir   │ │ (DNS)   │
          └─────────┘ └──────────┘ └─────────┘ └─────────┘ └─────────┘
```

libCRS uses the **strategy pattern** via the abstract `CRSUtils` base class. Currently, `LocalCRSUtils` implements all operations for local Docker Compose deployments. An `AzureCRSUtils` implementation is planned to support Azure-based deployments (e.g., using Azure Blob Storage for shared filesystems and Azure Container Instances for CRS execution). New deployment backends can be added by implementing the `CRSUtils` interface without changing the CLI or any CRS code.

### Data Exchange Flow

CRS containers never communicate directly. All inter-CRS data flows through a two-tier filesystem managed by the exchange sidecar:

```
  CRS-A Container                                              CRS-B Container
 ┌──────────────────┐                                        ┌──────────────────┐
 │                  │                                        │                  │
 │  SubmitHelper    │                                        │  FetchHelper     │
 │  (watchdog +     │                                        │  (poll every 5s) │
 │   MD5 dedup +    │                                        │                  │
 │   batch flush)   │                                        │  InfraClient     │
 │       │          │                                        │    .fetch_new()  │
 │       ▼          │                                        │       ▲          │
 │  SUBMIT_DIR/     │       Exchange Sidecar                 │  FETCH_DIR/      │
 │  ├─ povs/        │      (oss-crs-infra)                   │  ├─ povs/        │
 │  ├─ seeds/       │    ┌──────────────────┐                │  ├─ seeds/       │
 │  ├─ patches/     │───▶│  EXCHANGE_DIR/   │───▶            │  ├─ patches/     │
 │  └─ bug-         │    │  (shared global) │                │  └─ bug-         │
 │     candidates/  │    └──────────────────┘                │     candidates/  │
 │                  │                                        │                  │
 │  (per-CRS,       │                                        │  (per-CRS,       │
 │   write-only)    │                                        │   read-only)     │
 └──────────────────┘                                        └──────────────────┘
```

- **SUBMIT_DIR** — Per-CRS write area. `SubmitHelper` watches a local directory for new files, deduplicates by MD5 hash, batches them (100 files or 10 seconds), and copies to `SUBMIT_DIR/<type>/` with hash-based filenames.
- **EXCHANGE_DIR** — Global shared area managed by the exchange sidecar. The sidecar copies files from each CRS's SUBMIT_DIR into EXCHANGE_DIR and distributes them to other CRSs' FETCH_DIRs.
- **FETCH_DIR** — Per-CRS read-only area. `FetchHelper` polls every 5 seconds via `InfraClient`, deduplicates by filename (hash-based names from submit provide natural content dedup), and copies new files to the local directory.

### Data Types

All submission and fetching operations work with one of the following data types:

| Type | Description |
|---|---|
| `pov` | Proof-of-vulnerability inputs that trigger bugs |
| `seed` | Fuzzing seed inputs |
| `bug-candidate` | Potential bug reports for verification |
| `patch` | Patches to fix discovered bugs |
| `diff` | Reference diffs for delta-mode analysis |

## CLI Reference

### Build Output Commands

#### `submit-build-output` ✅

Submit a build artifact from the container to the shared build output filesystem.

```bash
$ libCRS submit-build-output <src_path> <dst_path>
```

| Argument | Description |
|---|---|
| `src_path` | Source file/directory path inside the container |
| `dst_path` | Destination path on the build output filesystem |

**Example** — Submit a compiled binary after target build:
```bash
$ libCRS submit-build-output /out/fuzzer /fuzzer
```

#### `download-build-output` ✅

Download a build artifact from the shared build output filesystem into the container.

```bash
$ libCRS download-build-output <src_path> <dst_path>
```

| Argument | Description |
|---|---|
| `src_path` | Source path on the build output filesystem |
| `dst_path` | Destination path inside the container |

**Example** — Retrieve a compiled binary during the run phase:
```bash
$ libCRS download-build-output /fuzzer /opt/fuzzer
```

#### `download-source` ✅

Download source tree into the container from mount paths.

```bash
$ libCRS download-source <fuzz-proj|target-source> <dst_path>
```

| Argument | Description |
|---|---|
| `fuzz-proj` | Copy from `/OSS_CRS_FUZZ_PROJ` (fuzz project mount) |
| `target-source` | Copy from `/OSS_CRS_TARGET_SOURCE` (target source mount) |
| `dst_path` | Destination path inside the container |

**Example** — Copy fuzz project files for analysis:
```bash
$ libCRS download-source fuzz-proj /work/project
```

**Example** — Copy target source tree for patch generation:
```bash
$ libCRS download-source target-source /work/src
```

#### `skip-build-output` ✅

Mark a build output path as intentionally skipped (creates a `.skip` sentinel file).

```bash
$ libCRS skip-build-output <dst_path>
```

| Argument | Description |
|---|---|
| `dst_path` | Path on the build output filesystem to skip |

**Example** — Skip an optional build output:
```bash
$ libCRS skip-build-output /optional-sanitizer-build
```

---

### Directory Registration Commands

These commands set up **automatic background syncing** between the container and the shared infrastructure. Registration commands fork a daemon process that watches the directory using filesystem events.

#### `register-submit-dir` ✅

Register a local directory for automatic submission to oss-crs-infra. A background daemon watches for new files and submits them in batches.

```bash
$ libCRS register-submit-dir [--log <log_path>] <type> <path>
```

| Argument | Description |
|---|---|
| `type` | Data type: `pov`, `seed`, `bug-candidate`, or `patch` |
| `path` | Local directory to watch |
| `--log` | *(Optional)* Log file path for the daemon |

**How it works:**
1. A daemon process is forked into the background.
2. The daemon uses `watchdog` to monitor the directory for new data files (dotfiles are ignored).
3. New files are deduplicated by MD5 hash and queued for submission.
4. Queued files are flushed in batches (every 10 seconds or when 100 files accumulate).
5. Flushed files are copied to `SUBMIT_DIR/<type_dir>/` (host-visible, per-CRS). The exchange sidecar handles copying to EXCHANGE_DIR.

**Example:**
```bash
$ libCRS register-submit-dir seed /output/seeds
$ libCRS register-submit-dir --log /var/log/pov-submit.log pov /output/povs
```

#### `register-shared-dir` ✅

Create a symlink from a local path to a shared filesystem path, enabling file sharing between containers within the same CRS.

```bash
$ libCRS register-shared-dir <local_path> <shared_fs_path>
```

| Argument | Description |
|---|---|
| `local_path` | Local directory path inside the container (must not already exist) |
| `shared_fs_path` | Path on the shared filesystem visible to all containers in the CRS |

**How it works:**
1. Creates the shared directory on the shared filesystem if it doesn't exist.
2. Creates a symlink from `local_path` → `$OSS_CRS_SHARED_DIR/<shared_fs_path>`.
3. Any container in the CRS that registers the same `shared_fs_path` will see the same files.

**Example** — Share a corpus between a fuzzer and an analyzer container:
```bash
# In the fuzzer container:
$ libCRS register-shared-dir /shared-corpus corpus

# In the analyzer container:
$ libCRS register-shared-dir /shared-corpus corpus
```

#### `register-log-dir` ✅

Create a symlink from a local path to a subdirectory under `LOG_DIR`, so that any files written to the local path are persisted on the host and available via `oss-crs artifacts`.

```bash
$ libCRS register-log-dir <local_path>
```

| Argument | Description |
|---|---|
| `local_path` | Local directory path inside the container (must not already exist) |

**How it works:**
1. Creates a subdirectory named after `local_path`'s basename under `$OSS_CRS_LOG_DIR`.
2. Creates a symlink from `local_path` → `$OSS_CRS_LOG_DIR/<basename>`.
3. Any files written to `local_path` are persisted on the host and visible via `oss-crs artifacts`.

**Example** — Persist agent logs from a patcher module:
```bash
# In the patcher container:
$ libCRS register-log-dir /var/log/agent
# Now writing to /var/log/agent/trace.log persists to the host
```

#### `register-fetch-dir` ✅

Register a local directory for automatic fetching of shared data from other CRSs. A background daemon polls the fetch directory periodically for new files and copies them to the registered path.

```bash
$ libCRS register-fetch-dir [--log <log_path>] <type> <path>
```

| Argument | Description |
|---|---|
| `type` | Data type: `pov`, `seed`, `bug-candidate`, `patch`, or `diff` |
| `path` | Local directory path to receive fetched data |
| `--log` | *(Optional)* Log file path for the daemon |

**How it works:**
1. A daemon process is forked into the background.
2. The daemon performs an initial sync: copies existing files from `FETCH_DIR/<type_dir>/` (bootup data + inter-CRS data) into the local path.
3. The daemon periodically polls `FETCH_DIR/<type_dir>/` for new files via `InfraClient.fetch_new()`.
4. Files are deduplicated by name (hash-based names from `submit` provide natural content dedup).

**Example:**
```bash
$ libCRS register-fetch-dir pov /shared-povs
$ libCRS register-fetch-dir --log /var/log/fetch-seeds.log seed /shared-seeds
```

---

### Manual Data Operations

#### `submit` ✅

Manually submit a single file to oss-crs-infra.

```bash
$ libCRS submit <type> <file_path>
```

| Argument | Description |
|---|---|
| `type` | Data type: `pov`, `seed`, `bug-candidate`, or `patch` |
| `file_path` | Path to the file to submit |

**Example:**
```bash
$ libCRS submit pov /tmp/crash-input
$ libCRS submit seed /tmp/interesting-input
$ libCRS submit bug-candidate /tmp/bug-report
```

#### `fetch` ✅

Fetch shared data from other CRSs (and bootup data) to a local directory. Returns a list of newly downloaded file names (one per line). Files already present in the destination are skipped.

```bash
$ libCRS fetch <type> <dst_dir_path>
```

| Argument | Description |
|---|---|
| `type` | Data type: `pov`, `seed`, `bug-candidate`, `patch`, or `diff` |
| `dst_dir_path` | Local directory to download files into |

**How it works:**
1. Scans `FETCH_DIR/<type_dir>/` for all available data (bootup data + inter-CRS data).
2. Copies only files not already present in the destination directory.
3. Returns the list of newly copied file names.

**Example:**
```bash
$ libCRS fetch seed /tmp/shared-seeds
$ libCRS fetch pov /tmp/shared-povs
$ libCRS fetch diff /tmp/ref-diffs
```

---

### Network Commands

#### `get-service-domain` ✅

Resolve the Docker network domain name for a service within the CRS. Returns the domain string and verifies it via DNS resolution.

```bash
$ libCRS get-service-domain <service_name>
```

| Argument | Description |
|---|---|
| `service_name` | Name of the service (as defined in `crs.yaml` modules) |

The returned domain follows the pattern `<service_name>.<crs_name>`.

**Example:**
```bash
$ libCRS get-service-domain my-analyzer
# Output: my-analyzer.my-crs
```

---

### Builder Sidecar Commands

These commands communicate with the framework-injected builder and runner sidecars to apply patches, rebuild, run PoVs, and run tests. The sidecars are always available during the run phase — CRS developers do not need to declare them in `crs.yaml`.

The `BUILDER_MODULE` environment variable (framework-injected as `builder-sidecar`) is used for DNS resolution. The `--builder` flag is optional and only needed to override the default.

#### `apply-patch-build` ✅

Apply a unified diff patch and rebuild in an ephemeral container. Sends the patch to the builder sidecar's `/build` endpoint, polls until completion, and writes results to the response directory.

```bash
$ libCRS apply-patch-build <patch_path> <response_dir> [--builder-name <name>]
```

| Argument | Description |
|---|---|
| `patch_path` | Path to the unified diff file |
| `response_dir` | Directory to receive build results |
| `--builder-name` | Builder config name (e.g., `default-build`). Auto-detected if omitted. |
| `--rebuild-id` | Rebuild ID (auto-increments if omitted) |

The command exits with the build's exit code (0 = success, non-zero = failure). The response directory contains:
- `retcode` — Exit code (0 = success)
- `rebuild_id` — The rebuild identifier for use with `run-pov`
- `stdout.log` / `stderr.log` — Build output

**Example:**
```bash
$ libCRS apply-patch-build /tmp/fix.diff /tmp/build-result
$ cat /tmp/build-result/rebuild_id
2
```

#### `run-pov` ✅

Run a PoV (proof-of-vulnerability) binary against a specific rebuild's output. Sends the PoV to the runner sidecar's `/run-pov` endpoint.

```bash
$ libCRS run-pov <pov_path> <response_dir> --harness <name> --rebuild-id <id>
```

| Argument | Description |
|---|---|
| `pov_path` | Path to the PoV binary file |
| `response_dir` | Directory to receive PoV results |
| `--harness` | **(Required)** Harness binary name in `/out/` |
| `--rebuild-id` | **(Required)** Rebuild ID from a prior `apply-patch-build` call |

The response directory contains:
- `retcode` — Exit code (0 = no crash / fix works, non-zero = still crashes)
- `stdout.log` / `stderr.log` — PoV execution output

**Example:**
```bash
$ libCRS run-pov /tmp/crash-input /tmp/pov-result \
    --harness fuzz_target --rebuild-id 2
```

#### `apply-patch-test` ✅

Apply a unified diff patch and run the project's bundled `test.sh` in an ephemeral container. Sends the patch to the builder sidecar's `/test` endpoint.

```bash
$ libCRS apply-patch-test <patch_path> <response_dir>
```

| Argument | Description |
|---|---|
| `patch_path` | Path to the unified diff file |
| `response_dir` | Directory to receive test results |

The response directory contains:
- `retcode` — Exit code (0 = tests pass, non-zero = failure)
- `stdout.log` / `stderr.log` — Test output

**Example:**
```bash
$ libCRS apply-patch-test /tmp/fix.diff /tmp/test-result
```

`apply-patch-test` contract notes:
- `test.sh` is resolved by the builder sidecar (checked at `/src/run_tests.sh`, `/src/test.sh`, `$OSS_CRS_PROJ_PATH/test.sh`).
- If no test script is found, the sidecar returns a skipped-success result (`retcode=0`) by contract.

## Typical Usage in a CRS

### During Target Build Phase

```bash
#!/bin/bash
# build.sh — executed during oss-crs build-target

# Compile the target with custom instrumentation
cd /src && make CC=afl-clang-fast

# Submit the compiled binary
libCRS submit-build-output /src/target /target

# If an optional build is not needed, skip it
libCRS skip-build-output /optional-target
```

### During Run Phase

```bash
#!/bin/bash
# run.sh — executed during oss-crs run

# Retrieve build outputs
libCRS download-build-output /target /opt/target

# Set up shared directories for inter-container communication
libCRS register-shared-dir /shared-corpus corpus

# Register directories for automatic submission to infra
libCRS register-submit-dir seed /output/seeds &
libCRS register-submit-dir pov /output/povs &
libCRS register-submit-dir bug-candidate /output/bugs &

# Resolve service endpoints
ANALYZER_HOST=$(libCRS get-service-domain analyzer)

# Start the fuzzer
/opt/fuzzer --target /opt/target --output /output --seeds /shared-corpus
```

### During Run Phase (Builder Sidecar / Patcher)

```bash
#!/bin/bash
# run-patcher.sh — executed in a patcher module
# Builder/runner sidecars are injected by the framework (BUILDER_MODULE env var is set automatically)

# Generate a patch (your CRS logic)
generate_patch > /tmp/patch.diff

# Apply the patch and rebuild in an ephemeral container
libCRS apply-patch-build /tmp/patch.diff /tmp/build-result
REBUILD_ID=$(cat /tmp/build-result/rebuild_id)

# Run PoV against the patched build
libCRS run-pov /tmp/crash-input /tmp/pov-result \
  --harness fuzz_target --rebuild-id "$REBUILD_ID"

# Run the project's test suite
libCRS apply-patch-test /tmp/patch.diff /tmp/test-result

# If all pass, submit the patch
libCRS submit patch /tmp/patch.diff
```

## Implementation Status

| Feature | Status | Notes |
|---|---|---|
| `submit-build-output` | ✅ Implemented | Uses `rsync` for file copying |
| `download-build-output` | ✅ Implemented | Uses `rsync` for file copying |
| `download-source` | ✅ Implemented | Copies from `/OSS_CRS_FUZZ_PROJ` or `/OSS_CRS_TARGET_SOURCE` mounts |
| `skip-build-output` | ✅ Implemented | Creates `.skip` sentinel file |
| `register-submit-dir` | ✅ Implemented | Daemon with `watchdog` + batch submission |
| `register-shared-dir` | ✅ Implemented | Symlink-based sharing |
| `register-log-dir` | ✅ Implemented | Symlink-based log persistence to host |
| `submit` | ✅ Implemented | Single-file submission |
| `get-service-domain` | ✅ Implemented | DNS-verified domain resolution |
| `register-fetch-dir` | ✅ Implemented | Daemon with periodic polling of FETCH_DIR via InfraClient |
| `apply-patch-build` | ✅ Implemented | Ephemeral rebuild via builder sidecar |
| `run-pov` | ✅ Implemented | PoV reproduction via runner sidecar |
| `apply-patch-test` | ✅ Implemented | Patch + test.sh via builder sidecar |
| `fetch` | ✅ Implemented | One-shot fetch from FETCH_DIR via InfraClient |
| `apply-patch-build` | ✅ Implemented | Sends patch to builder sidecar `/build` endpoint |
| `run-pov` | ✅ Implemented | Sends PoV to builder sidecar `/run-pov` endpoint |
| `AzureCRSUtils` | 📝 Planned | Azure deployment backend for `CRSUtils` |
| InfraClient integration | ✅ Implemented | Exchange sidecar copies from SUBMIT_DIR to EXCHANGE_DIR; InfraClient fetches from FETCH_DIR |

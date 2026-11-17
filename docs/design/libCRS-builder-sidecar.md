# libCRS Builder Sidecar Interface

## Overview

When a CRS generates a candidate bug fix or wants to test a Proof-of-Vulnerability (POV) input, it needs to compile the patched code and execute it against target binaries. Because target compilation tools (compilers, build scripts, system headers) are heavy and complex, OSS-CRS separates this work into a dedicated **Builder Sidecar**.

The Builder Sidecar executes alongside the CRS module and exposes HTTP REST endpoints that `libCRS` calls to perform builds and tests in clean, ephemeral environments.

---

## 1. Builder Sidecar Architecture

```
┌─────────────────────────────────┐           ┌─────────────────────────────────┐
│          CRS Container          │           │         Builder Sidecar         │
│                                 │   HTTP    │                                 │
│  $ libCRS apply-patch-build ... ├──────────▶│  POST /apply-patch-build        │
│                                 │           │   ├── Applies patch to source   │
│                                 │           │   ├── Compiles new binaries     │
│                                 │           │   └── Returns build status/logs │
│                                 │◀──────────┤                                 │
│                                 │           │                                 │
│  $ libCRS run-pov ...           ├──────────▶│  POST /run-pov                  │
│                                 │           │   ├── Replays input on target   │
│                                 │           │   └── Returns crash/hang output │
│                                 │◀──────────┤                                 │
└─────────────────────────────────┘           └─────────────────────────────────┘
```

The service name of the sidecar is resolved automatically via container DNS (`http://${BUILDER_MODULE}:8000`).

---

## 2. CLI Commands

### 1. `apply-patch-build`
Applies a unified git diff patch to the target source tree and rebuilds target binaries.

```bash
$ libCRS apply-patch-build <patch_file> <response_dir>
```

- **Arguments**:
  - `patch_file`: Path to the `.patch` or `.diff` file inside the CRS container.
  - `response_dir`: Directory where build artifacts, logs, and `response.json` will be written.

- **Output (`response.json`)**:
  ```json
  {
    "status": "success",
    "rebuild_id": "rb-98a4e3",
    "build_log": "/rebuilds/rb-98a4e3/build.log",
    "binaries": ["xml_fuzzer", "html_fuzzer"]
  }
  ```

---

### 2. `run-pov`
Executes a Proof-of-Vulnerability test case against a specific fuzz harness binary to verify whether it triggers a crash or sanitizer violation.

```bash
$ libCRS run-pov <pov_path> <response_dir> --harness <harness_name> [--rebuild-id <id>]
```

- **Arguments**:
  - `pov_path`: Path to the PoV input file.
  - `response_dir`: Directory where test outputs and exit status will be written.
  - `--harness`: Name of the target harness binary (e.g., `xml`).
  - `--rebuild-id`: (Optional) Rebuild ID from a previous `apply-patch-build` to test the patched binary.

- **Output (`response.json`)**:
  ```json
  {
    "crashed": true,
    "returncode": -11,
    "sanitizer": "AddressSanitizer: heap-buffer-overflow",
    "crash_log": "/rebuilds/rb-98a4e3/crash.log"
  }
  ```

---

### 3. `apply-patch-test`
Combines patch application and test suite execution to verify that a bug fix does not break existing unit tests.

```bash
$ libCRS apply-patch-test <patch_file> <response_dir>
```

---

## 3. Snapshot Acceleration (`--incremental-build`)

In standard mode, the builder sidecar recompiles the target from clean source trees. When `--incremental-build` is enabled:
- The sidecar launches from pre-baked Docker snapshot images created during `build-target`.
- Only modified files are recompiled by `make` / `ninja`, reducing rebuild latency from minutes to seconds.

# CRS Development Guide

This guide walks you through building a Cyber Reasoning System (CRS) that integrates with the OSS-CRS framework. By the end, your CRS will be able to:

- Target any project in [OSS-Fuzz](https://github.com/google/oss-fuzz) format
- Run in any supported environment (local, Azure) without modification
- Be composable with other CRSs in ensemble campaigns

> **Before you start:** Check the [CRS Registry](registry.md) to see if an existing CRS already fits your needs. You can use a registered CRS directly with `oss-crs` — no development required. If you want to extend or customize an existing CRS, forking a registered one is often faster than starting from scratch. Registered CRS repositories also serve as practical references for how to structure your own CRS — studying their `oss-crs/crs.yaml`, Dockerfiles, and build scripts is a great way to learn the patterns.

## Prerequisites

| Requirement | Version |
|---|---|
| Python | >= 3.10 |
| Docker | latest |
| Git | latest |
| [uv](https://github.com/astral-sh/uv) | latest |

Familiarity with Docker, Docker Compose, and the OSS-Fuzz project format is recommended.

---

## Overview

A CRS is a self-contained, containerized bug-finding or bug-fixing system. OSS-CRS manages the entire lifecycle of a CRS through three phases:

```
  prepare  ──▶  build-target  ──▶  run
```

| Phase | What happens | What you provide |
|---|---|---|
| **Prepare** | Pull your CRS repository and build Docker images | HCL file for `docker buildx bake` |
| **Build Target** | Compile the target project with your CRS's instrumentation | Builder Dockerfile(s) + expected outputs |
| **Run** | Launch your CRS containers against the built target | Module Dockerfile(s) + entry scripts |

Your CRS communicates with the infrastructure through **libCRS**, a CLI library pre-installed in every container.

---

## Repository Structure

Your CRS repository must contain an `oss-crs/` directory at the root with a `crs.yaml` configuration file:

```
my-crs/
├── oss-crs/
│   ├── crs.yaml                    # CRS configuration (required)
│   ├── build.hcl                   # Docker buildx bake file
│   ├── asan-builder.Dockerfile     # Target build Dockerfile(s)
│   └── docker-compose/
│       ├── fuzzer.Dockerfile       # Run-phase module Dockerfile(s)
│       └── analyzer.Dockerfile
├── src/                            # Your CRS source code
│   └── ...
└── ...
```

---

## Step 1: Write `crs.yaml`

The `crs.yaml` file is the central configuration for your CRS. It tells OSS-CRS how to prepare, build, and run your system.

```yaml
name: my-crs
type:
  - bug-finding           # bug-finding, bug-fixing, or both
version: "1.0.0"
docker_registry: "ghcr.io/my-org/my-crs"

prepare_phase:
  hcl: oss-crs/build.hcl

target_build_phase:
  - name: asan-build
    dockerfile: oss-crs/asan-builder.Dockerfile
    outputs:
      - asan/build              # directory containing compiled binaries
      - asan/src                # directory containing source snapshot
    additional_env:           # optional extra env vars for the build
      BUILD_TYPE: asan
  - name: coverage-build
    dockerfile: oss-crs/asan-builder.Dockerfile
    additional_env:
      BUILD_TYPE: coverage
    outputs:
      - coverage/build

crs_run_phase:
  fuzzer:
    dockerfile: oss-crs/docker-compose/fuzzer.Dockerfile
    additional_env:
      FUZZER_THREADS: "4"
  analyzer:
    dockerfile: oss-crs/docker-compose/analyzer.Dockerfile

supported_target:
  mode:
    - full
  language:
    - c
    - c++
  sanitizer:
    - address
  architecture:
    - x86_64

# Only needed if your CRS uses LLMs.
# This is a minimum dependency list (validation baseline), not an allowlist.
required_llms:
  - gpt-4.1
  - claude-sonnet-4-20250514

# Only needed if your CRS requires specific directed inputs to function.
# OSS-CRS will fail fast before spawning containers if these are missing.
required_inputs:
  - diff
  - bug-candidate
```

### Configuration Fields

| Field | Description |
|---|---|
| `name` | Unique name for your CRS |
| `type` | Set of CRS types: `bug-finding`, `bug-fixing` |
| `version` | Version string (used as a Docker image tag) |
| `docker_registry` | Docker registry URL for your CRS images |
| `prepare_phase.hcl` | Path to the HCL file for `docker buildx bake` |
| `target_build_phase` | List of build steps, each with a Dockerfile and expected outputs |
| `crs_run_phase` | Dictionary of named modules (containers) that run at runtime |
| `supported_target` | Languages, sanitizers, architectures, and modes your CRS supports |
| `required_llms` | *(Optional)* Minimum required LLM model names your CRS needs (validation baseline) |
| `required_inputs` | *(Optional)* Input channels the CRS requires (`diff`, `pov`, `seed`, `bug-candidate`). Validated before run. |

For the complete schema reference, see [config/crs.md](config/crs.md).

---

## Step 2: Write the Prepare Phase (HCL)

The prepare phase uses [Docker Buildx Bake](https://docs.docker.com/build/bake/) to build your CRS images. Create an HCL file that defines your build targets:

```hcl
// oss-crs/build.hcl

group "default" {
  targets = ["my-crs-base"]
}

target "my-crs-base" {
  dockerfile = "Dockerfile"
  context    = "."
  tags       = ["my-crs-base:latest"]
}
```

This runs during `oss-crs prepare` and builds all images your CRS needs.

---

## Step 3: Write Target Build Dockerfiles

During the build-target phase, OSS-CRS compiles the target project using your Dockerfile. Your builder Dockerfile receives:

- **`target_base_image`** — The base image for the OSS-Fuzz target (with source code and build environment)
- **`crs_version`** — Your CRS version string
- **`libcrs`** — An additional build context containing the libCRS library

### Example Builder Dockerfile

```dockerfile
# oss-crs/asan-builder.Dockerfile

ARG target_base_image
FROM ${target_base_image}

# Install libCRS
COPY --from=libcrs . /opt/libCRS
RUN /opt/libCRS/install.sh

# Install the compile script
COPY bin/compile_target /usr/local/bin/compile_target

# Actual compilation runs at container startup (not during image build),
# because the target source code is injected at runtime.
CMD ["compile_target"]
```

### Example Build Script

The `CMD` in the Dockerfile invokes a build script that compiles the target and submits outputs:

```bash
#!/bin/bash
# bin/compile_target
set -e

if [ "$BUILD_TYPE" = "asan" ]; then
    # Compile with AddressSanitizer instrumentation
    compile

    # Submit build output directories
    # These must match the `outputs` list in crs.yaml
    libCRS submit-build-output $OUT asan/build
    libCRS submit-build-output $SRC asan/src

elif [ "$BUILD_TYPE" = "coverage" ]; then
    SANITIZER=coverage compile
    libCRS submit-build-output $OUT coverage/build
fi
```

> **Note:** If a build step doesn't apply for certain targets (e.g., coverage builds for JVM projects), use `libCRS skip-build-output` to explicitly skip the output:
> ```bash
> libCRS skip-build-output coverage/build
> ```

### Key Points

- Each build step in `target_build_phase` runs as a separate Docker container.
- Build outputs are typically **directories**, not tarballs — `libCRS submit-build-output` handles both files and directories via `rsync`.
- Use `libCRS submit-build-output <src> <dst>` to make build artifacts available to the run phase.
- Use `libCRS skip-build-output <dst>` to mark optional outputs as intentionally skipped.
- The `outputs` list in `crs.yaml` declares what paths the build step is expected to produce. Every output must either be submitted or explicitly skipped.
- For directed fuzzers, `oss-crs build-target --diff <file>` plus `--bug-candidate <file>` or `--bug-candidate-dir <dir>` stages inputs into build-phase `OSS_CRS_FETCH_DIR`; consume them with `libCRS fetch diff <dir>` / `libCRS fetch bug-candidate <dir>`.

---

## Step 4: Write Run-Phase Module Dockerfiles

Each module in `crs_run_phase` becomes a container at runtime. Modules share a private Docker network (for intra-CRS communication) and have access to shared infrastructure.

### Example Run-Phase Dockerfile

```dockerfile
# oss-crs/docker-compose/fuzzer.Dockerfile

ARG target_base_image
FROM ${target_base_image}

# Install libCRS
COPY --from=libcrs . /opt/libCRS
RUN /opt/libCRS/install.sh

# Copy your CRS code
COPY src/ /opt/my-crs/

# Entry script
COPY oss-crs/docker-compose/run-fuzzer.sh /opt/run-fuzzer.sh
RUN chmod +x /opt/run-fuzzer.sh

CMD ["/opt/run-fuzzer.sh"]
```

### Example Entry Script

```bash
#!/bin/bash
# oss-crs/docker-compose/run-fuzzer.sh

# 1. Download build output directories from the target build phase
libCRS download-build-output asan/build /opt/target/build
libCRS download-build-output asan/src /opt/target/src

# 2. Set up shared directories for inter-container communication
libCRS register-shared-dir /shared-corpus corpus

# 3. Register directories for automatic artifact submission
#    (These run as background daemons)
libCRS register-submit-dir seed /output/seeds &
libCRS register-submit-dir pov /output/povs &
libCRS register-submit-dir bug-candidate /output/bugs &

# 3b. Persist agent logs to the host (visible via oss-crs artifacts)
libCRS register-log-dir /var/log/agent

# 4. Resolve other module endpoints (if needed)
ANALYZER_HOST=$(libCRS get-service-domain analyzer)

# 5. Run your fuzzer
/opt/target/fuzzer \
  --target /opt/target/binary \
  --seeds /shared-corpus \
  --output /output
```

---

## Step 5: Using libCRS

libCRS is automatically installed in every CRS container. It provides the interface between your CRS code and the OSS-CRS infrastructure.

### Environment Variables Available at Runtime

Your containers receive these environment variables automatically:

| Variable | Description | Example |
|---|---|---|
| `OSS_CRS_RUN_ENV_TYPE` | Execution environment | `local` |
| `OSS_CRS_NAME` | CRS name (from `crs-compose.yaml`) | `my-crs` |
| `OSS_CRS_SERVICE_NAME` | Full service name | `my-crs_fuzzer` |
| `OSS_CRS_TARGET` | Target project name | `libxml2` |
| `OSS_CRS_TARGET_HARNESS` | Target harness binary name | `xml` |
| `OSS_CRS_CPUSET` | Allocated CPU cores | `4-7` |
| `OSS_CRS_MEMORY_LIMIT` | Memory limit | `16G` |
| `OSS_CRS_BUILD_OUT_DIR` | Build output directory (read-only at run time) | `/OSS_CRS_BUILD_OUT_DIR` |
| `OSS_CRS_SUBMIT_DIR` | Submission directory | `/OSS_CRS_SUBMIT_DIR` |
| `OSS_CRS_SHARED_DIR` | Shared directory (between containers in this CRS) | `/OSS_CRS_SHARED_DIR` |
| `OSS_CRS_LOG_DIR` | Log directory for persisting CRS agent/internal logs to the host | `/OSS_CRS_LOG_DIR` |
| `OSS_CRS_FETCH_DIR` | Inter-CRS data exchange + bootup data (read-only, shared across all CRSs) | `/OSS_CRS_FETCH_DIR` |
| `FUZZING_ENGINE` | OSS-Fuzz fuzzing engine | `libfuzzer` |
| `SANITIZER` | OSS-Fuzz sanitizer | `address` |
| `ARCHITECTURE` | Target architecture | `x86_64` |
| `FUZZING_LANGUAGE` | Target language | `c` |
| `OSS_CRS_REBUILD_OUT_DIR` | Shared filesystem path for rebuild artifacts (builder sidecar output) | `/OSS_CRS_REBUILD_OUT_DIR` |
| `BUILDER_MODULE` | Builder sidecar service name for DNS (framework-injected) | `builder-sidecar` |
| `OSS_CRS_FUZZ_PROJ` | Fuzz project mount path (read-only) | `/OSS_CRS_FUZZ_PROJ` |
| `OSS_CRS_TARGET_SOURCE` | Target source mount path (read-only) | `/OSS_CRS_TARGET_SOURCE` |

Notes:
- `additional_env` keys are validated with pattern `[A-Za-z_][A-Za-z0-9_]*`.
- `OSS_CRS_*` keys are reserved. If provided by users, `oss-crs` emits warnings (`ENV001`/`ENV002`).
- Framework-owned `OSS_CRS_*` keys override user values for that phase; unknown reserved keys are warned and may pass through.

### LLM Environment Variables (if configured)

| Variable | Description |
|---|---|
| `OSS_CRS_LLM_API_URL` | LiteLLM proxy endpoint (e.g., `http://litellm.oss-crs:4000`) |
| `OSS_CRS_LLM_API_KEY_FILE` | Path to file containing per-CRS API key for LLM access |

Use these with any OpenAI-compatible client library. All requests are proxied through LiteLLM, which enforces your budget.

### libCRS Command Quick Reference

```bash
# Build outputs
libCRS submit-build-output <src_path> <dst_path>
libCRS download-build-output <src_path> <dst_path>
libCRS download-source <type> <dst_path>         # type: fuzz-proj, target-source
libCRS skip-build-output <dst_path>

# Automatic directory submission (runs as daemon)
libCRS register-submit-dir <type> <path>      # type: pov, seed, bug-candidate, patch
libCRS register-shared-dir <local_path> <shared_path>
libCRS register-log-dir <local_path>             # persist logs to host

# Fetch directory registration (daemon poller for FETCH_DIR)
libCRS register-fetch-dir <type> <path>       # type: pov, seed, bug-candidate, patch, diff

# One-shot fetch (copies from FETCH_DIR)
libCRS fetch <type> <path>                    # type: pov, seed, bug-candidate, patch, diff

# Manual submission
libCRS submit <type> <file_path>

# Network
libCRS get-service-domain <service_name>

# Builder sidecar (framework-injected, BUILDER_MODULE set automatically)
libCRS apply-patch-build <patch_path> <response_dir>
libCRS run-pov <pov_path> <response_dir> --harness <name> --rebuild-id <id>
libCRS apply-patch-test <patch_path> <response_dir>
```

For the complete libCRS reference, see [design/libCRS.md](design/libCRS.md).

---

## Step 6: Test Locally

### Create a Compose File

Create a `crs-compose.yaml` to run your CRS locally:

```yaml
run_env: local
docker_registry: ghcr.io/my-org

oss_crs_infra:
  cpuset: "0-1"
  memory: "4G"

my-crs:
  source:
    local_path: /path/to/my-crs    # Use local path during development
  cpuset: "2-7"
  memory: "16G"
  # llm_budget: 100               # Uncomment if using LLMs

# Uncomment if using LLMs
# llm_config:
#   litellm:
#     mode: internal
#     internal:
#       config_path: /path/to/litellm-config.yaml
#   litellm_config: /path/to/litellm-config.yaml  # Backward-compatible legacy key; will be deprecated after gen-compose is fully implemented.
```

### Run the Three Phases

```bash
# 1. Prepare — build CRS Docker images
uv run oss-crs prepare \
  --compose-file ./my-crs-compose.yaml

# 2. Build target — compile the target with your instrumentation
uv run oss-crs build-target \
  --compose-file ./my-crs-compose.yaml \
  --fuzz-proj-path ~/oss-fuzz/projects/libxml2

# Optional: directed build inputs for target build phase
uv run oss-crs build-target \
  --compose-file ./my-crs-compose.yaml \
  --fuzz-proj-path ~/oss-fuzz/projects/libxml2 \
  --diff ./ref.diff \
  --bug-candidate-dir ./bug-candidates

# 3. Run — launch the CRS
uv run oss-crs run \
  --compose-file ./my-crs-compose.yaml \
  --fuzz-proj-path ~/oss-fuzz/projects/libxml2 \
  --target-harness xml \
  --timeout 3600

# 4. Query artifacts — find PoVs and seeds
uv run oss-crs artifacts \
  --compose-file ./my-crs-compose.yaml \
  --fuzz-proj-path ~/oss-fuzz/projects/libxml2 \
  --target-harness xml

# Optional: pass POVs, diffs, or seed files to CRS containers
uv run oss-crs run \
  --compose-file ./my-crs-compose.yaml \
  --fuzz-proj-path ~/oss-fuzz/projects/libxml2 \
  --target-harness xml \
  --pov-dir ./povs \
  --diff ./ref.diff \
  --seed-dir ./seeds
```

### Debugging Tips

- **Check container logs:** `docker compose logs <service_name>`
- **Shell into a container:** `docker compose exec <service_name> bash`
- **Inspect build outputs:** Check the build output directory created during `build-target`
- **Use `local_path`:** During development, use `local_path` in `crs-compose.yaml` to avoid pushing to git for every change

---

## Step 7: Publish Your CRS

Once your CRS is working, push it to a Git repository and switch from `local_path` to `url` + `ref`:

```yaml
my-crs:
  source:
    url: https://github.com/my-org/my-crs.git
    ref: v1.0.0
  cpuset: "2-7"
  memory: "16G"
```

### Register in the CRS Registry

To make your CRS discoverable, add an entry to the [CRS Registry](registry.md) by creating `registry/<crs-name>.yaml`. Once registered, users can use your CRS without specifying `source` in their compose file:

```yaml
name: my-crs
type:
  - bug-finding
source:
  url: https://github.com/my-org/my-crs.git
  ref: main
```

---

## Multi-Module CRS Architecture

A CRS can contain multiple modules (containers) that work together. Modules share a private Docker network and can communicate via DNS.

```
┌────────────────────────────────────────────────┐
│              my-crs (Isolated)                 │
│                                                │
│  ┌──────────────┐    ┌─────────────────┐       │
│  │   fuzzer     │◄──►│    analyzer     │       │
│  │  (module)    │    │    (module)     │       │
│  └──────────────┘    └─────────────────┘       │
│         │                    │                 │
│         ▼                    ▼                 │
│     /shared-corpus  (via register-shared-dir)  │
│                                                │
│  DNS: fuzzer.my-crs    analyzer.my-crs         │
└────────────────────────────────────────────────┘
```

### Inter-Module Communication

| Method | Use case | How |
|---|---|---|
| **Shared filesystem** | Passing corpus, seeds, or results between modules | `libCRS register-shared-dir /local corpus` |
| **DNS** | HTTP APIs or custom protocols between modules | `libCRS get-service-domain analyzer` → `analyzer.my-crs` |

### Example: Two-Module CRS

```yaml
# crs.yaml
crs_run_phase:
  fuzzer:
    dockerfile: oss-crs/docker-compose/fuzzer.Dockerfile
  analyzer:
    dockerfile: oss-crs/docker-compose/analyzer.Dockerfile
    additional_env:
      ANALYSIS_MODE: "deep"
```

In the fuzzer container:
```bash
libCRS register-shared-dir /shared-corpus corpus
# Fuzzer writes seeds to /shared-corpus
```

In the analyzer container:
```bash
libCRS register-shared-dir /shared-corpus corpus
# Analyzer reads seeds from /shared-corpus
FUZZER_ADDR=$(libCRS get-service-domain fuzzer)
# Can connect to fuzzer's API at $FUZZER_ADDR
```

---

## Builder Sidecar

The builder sidecar lets your CRS apply patches and rebuild the target **without recompiling from scratch**. The framework automatically injects a builder sidecar and runner sidecar alongside your CRS containers during the run phase — no configuration needed in `crs.yaml`.

### When to Use

Use the builder sidecar when your CRS needs to:
- Generate and test patches (bug-fixing CRSs)
- Rapidly rebuild after small source changes
- Run PoVs or tests against patched builds

### How It Works

```
┌───────────────────────────────────────────────────────────────┐
│                    Docker Compose (run phase)                  │
│                                                               │
│  ┌──────────────┐    ┌──────────────────┐                    │
│  │   patcher    │───▶│ builder-sidecar  │ (framework-injected)│
│  │  (your CRS)  │    │ ephemeral builds │                    │
│  │              │    └──────────────────┘                    │
│  │              │    ┌──────────────────┐                    │
│  │              │───▶│ runner-sidecar   │ (framework-injected)│
│  └──────────────┘    │ PoV reproduction │                    │
│                      └──────────────────┘                    │
│                                                               │
│  Patcher uses libCRS commands:                               │
│    apply-patch-build  →  rebuild with patch, get rebuild_id  │
│    run-pov            →  run PoV against a patched build     │
│    apply-patch-test   →  apply patch and run test.sh         │
└───────────────────────────────────────────────────────────────┘
```

1. During `build-target`, the framework builds your CRS's builder image and preserves it.
2. During `run`, the framework injects `oss-crs-builder-sidecar` and `oss-crs-runner-sidecar` containers. The builder sidecar launches ephemeral containers from the preserved builder image to apply patches and rebuild.
3. Your patcher module sends patches via `libCRS apply-patch-build`, which calls the builder's `/build` endpoint.
4. The builder applies the patch in a fresh container, recompiles, and returns the result.

### CRS Configuration

No special `crs.yaml` entries are needed for the sidecar. Just declare your builder and patcher as usual:

```yaml
target_build_phase:
  - name: default-build
    dockerfile: oss-crs/builder.Dockerfile
    outputs:
      - build
      - src

crs_run_phase:
  patcher:
    dockerfile: oss-crs/patcher.Dockerfile
```

The framework automatically:
- Injects `BUILDER_MODULE=builder-sidecar` into all CRS run-phase containers
- Creates DNS aliases `builder-sidecar.<crs-name>` and `runner-sidecar.<crs-name>`
- Mounts the Docker socket and rebuild output volumes

### Compose File Setup

No special compose file configuration is needed. Just reference your CRS as usual:

```yaml
run_env: local
docker_registry: ghcr.io/my-org

oss_crs_infra:
  cpuset: "0-1"
  memory: "4G"

my-patcher-crs:
  cpuset: "2-7"
  memory: "16G"
  source:
    local_path: /path/to/my-crs
```

Framework-generated helper sidecars reserve the `oss-crs-` service-name prefix.
If you add new OSS-CRS-managed helper containers, keep that prefix so teardown
classification treats their expected shutdown exits as non-fatal.

### Using Builder Commands in Your Patcher

```bash
#!/bin/bash
# run-patcher.sh — entry script for a patcher module
# BUILDER_MODULE is set automatically by the framework

# 1. Generate a patch (your CRS logic)
generate_patch > /tmp/patch.diff

# 2. Apply the patch and rebuild in an ephemeral container
libCRS apply-patch-build /tmp/patch.diff /tmp/build-result
# Exit code 0 = build succeeded, non-zero = build failed
# /tmp/build-result/ contains rebuild_id, retcode, stdout.log, stderr.log

REBUILD_ID=$(cat /tmp/build-result/rebuild_id)

# 3. Run PoV against the patched build
libCRS run-pov /tmp/crash-input /tmp/pov-result \
  --harness fuzz_target --rebuild-id "$REBUILD_ID"

# 4. Run the project's test suite
libCRS apply-patch-test /tmp/patch.diff /tmp/test-result
```

`apply-patch-test` contract notes:
- The builder sidecar resolves the test script (checks `/src/run_tests.sh`, `/src/test.sh`, `$OSS_CRS_PROJ_PATH/test.sh`).
- If no test script is found, the sidecar returns skipped-success (`retcode=0`) by contract.

### Incremental Build Snapshots (`--incremental-build`)

For faster rebuilds across multiple runs, operators can pass `--incremental-build` to both `build-target` and `run`:

```bash
# Create snapshots during build
oss-crs build-target --incremental-build --compose-file ... --build-id my-build

# Use snapshots during run (ephemeral containers start from snapshot instead of base image)
oss-crs run --incremental-build --compose-file ... --build-id my-build --run-id my-run
```

Without `--incremental-build`, ephemeral containers start from the base builder image each time (clean builds). With it, they start from a Docker snapshot of the compiled target, making rebuilds significantly faster.

This is an operator concern — CRS developers don't need to do anything special.

---

## Using LLMs in Your CRS

If your CRS leverages LLMs (e.g., for harness generation, code analysis, or patch synthesis):

### 1. Declare Required Models

In `crs.yaml`:
```yaml
required_llms:
  - gpt-4.1
  - claude-sonnet-4-20250514
```

OSS-CRS validates that these models are available in the LiteLLM config before launching.

### 2. Use the LLM API

At runtime, use the provided environment variables with any OpenAI-compatible client:

```python
from openai import OpenAI
import os

with open(os.environ["OSS_CRS_LLM_API_KEY_FILE"]) as f:
    api_key = f.read().strip()

client = OpenAI(
    api_key=api_key,
    base_url=os.environ["OSS_CRS_LLM_API_URL"],
)

response = client.chat.completions.create(
    model="gpt-4.1",  # Use the model name from required_llms
    messages=[{"role": "user", "content": "Analyze this code for bugs..."}],
)
```

### 3. Budget Management

- LLM budgets are set per-CRS in `crs-compose.yaml` (in dollars)
- All requests are proxied through LiteLLM, which tracks usage and enforces limits
- When the budget is exhausted, LLM requests will be rejected

For LLM configuration details, see [config/llm.md](config/llm.md).

---

## Submitting Artifacts

Your CRS should submit findings through libCRS:

| Artifact | Description | How to submit |
|---|---|---|
| **Seeds** | Interesting fuzzing inputs | `libCRS register-submit-dir seed /output/seeds` or `libCRS submit seed <file>` |
| **PoVs** | Crash-triggering inputs | `libCRS register-submit-dir pov /output/povs` or `libCRS submit pov <file>` |
| **Bug Candidates** | Bug reports for verification | `libCRS register-submit-dir bug-candidate /output/bugs` or `libCRS submit bug-candidate <file>` |
| **Patches** | Fixes for discovered bugs | `libCRS register-submit-dir patch /output/patches` or `libCRS submit patch <file>` |

### Directory Registration vs. Manual Submission

- **`register-submit-dir`** — Best for high-volume output. Forks a daemon that watches the directory, deduplicates files by hash, and submits in batches. Use this for seeds and PoVs.
- **`submit`** — Best for one-off submissions. Submits a single file immediately.

---

## Fetching Data

CRS containers receive data through `FETCH_DIR`, a read-only volume mounted to run-phase CRS containers. During `build-target`, it is also mounted to builder containers when directed inputs are provided (`--diff`, `--bug-candidate`, `--bug-candidate-dir`).

Data arrives from two sources:

1. **Bootup data** — Files passed via CLI flags, pre-populated by the host before containers start.
run-phase: `oss-crs run --pov-dir/--diff/--seed-dir/--bug-candidate/--bug-candidate-dir`
build-phase: `oss-crs build-target --diff/--bug-candidate/--bug-candidate-dir`
2. **Inter-CRS data** — Files submitted by other CRSs at runtime via `register-submit-dir` or `submit`, delivered by the exchange sidecar which polls `SUBMIT_DIR` and copies artifacts into the shared exchange volume.

### Bootup Data (oss-crs run flags)

The operator passes data via `oss-crs run`:
- `--pov <file>` or `--pov-dir <dir>` — PoV files → `FETCH_DIR/povs/`
- `--diff <file>` — Reference diff → `FETCH_DIR/diffs/ref.diff`
- `--seed-dir <dir>` — Seed files → `FETCH_DIR/seeds/`
- `--bug-candidate <file>` — Bug-candidate report file → `FETCH_DIR/bug-candidates/`
- `--bug-candidate-dir <dir>` — Bug-candidate report directory → `FETCH_DIR/bug-candidates/`

TODO: Standardize the bug-candidate format across CRSs (for example, SARIF 2.1.0) and define validation policy.
Reference implementation: [`libCRS/libCRS/sarif.py`](../libCRS/libCRS/sarif.py).

### Using register-fetch-dir (Daemon Poller)

For continuous data fetching, use `register-fetch-dir`. It forks a daemon that performs an initial sync and then polls `FETCH_DIR` for new files:

```bash
# In your entry script:
libCRS register-fetch-dir pov /my-povs &
libCRS register-fetch-dir seed /my-seeds &
```

The daemon:
1. Copies existing files from `FETCH_DIR/<type_dir>/` into the local directory.
2. Polls `FETCH_DIR/<type_dir>/` periodically for new files and copies them as they arrive.

### Using fetch (One-Shot)

For a one-time data snapshot, use `fetch`:

```bash
# Get all currently available POVs (bootup + inter-CRS)
NEW_FILES=$(libCRS fetch pov /my-povs)
echo "New files: $NEW_FILES"

# Call again later to get only new files since last fetch
NEW_FILES=$(libCRS fetch pov /my-povs)
```

### Available Data Types

| CLI Flag | Data Type | FETCH_DIR Subdirectory |
|---|---|---|
| `--pov`, `--pov-dir` | `pov` | `FETCH_DIR/povs/` |
| `--diff` | `diff` | `FETCH_DIR/diffs/` |
| `--seed-dir` | `seed` | `FETCH_DIR/seeds/` |
| `--bug-candidate`, `--bug-candidate-dir` | `bug-candidate` | `FETCH_DIR/bug-candidates/` |

---

## Checklist

Before publishing your CRS, verify:

- [ ] `oss-crs/crs.yaml` is valid and complete
- [ ] `prepare_phase.hcl` builds all required images
- [ ] All `target_build_phase` outputs are submitted or skipped via libCRS
- [ ] Run-phase Dockerfiles install libCRS (`COPY --from=libcrs . /opt/libCRS && RUN /opt/libCRS/install.sh`)
- [ ] Containers download build outputs at startup via `libCRS download-build-output`
- [ ] Artifact directories are registered with `libCRS register-submit-dir`
- [ ] `supported_target` accurately reflects your CRS capabilities
- [ ] `required_llms` lists all models used (if any)
- [ ] `required_inputs` lists inputs the CRS depends on (if any)
- [ ] The CRS runs successfully against at least one OSS-Fuzz project

**Additional checks for bug-fixing CRSs (builder sidecar):**

- [ ] Patcher module uses `apply-patch-build`, `run-pov`, and/or `apply-patch-test` commands correctly
- [ ] No `BUILDER_MODULE` in `additional_env` (framework injects it automatically)

---

## Further Reading

- [CRS Configuration Reference](config/crs.md) — Full `crs.yaml` schema
- [CRS Compose Configuration](config/crs-compose.md) — `crs-compose.yaml` schema
- [Target Project Configuration](config/target-project.md) — OSS-Fuzz `project.yaml` format
- [LLM Configuration](config/llm.md) — LiteLLM model setup
- [libCRS Reference](design/libCRS.md) — Complete CLI documentation
- [Architecture Overview](design/architecture.md) — System design and component diagram

# OSS-CRS: Open Source Cyber Reasoning System Framework

**OSS-CRS** is a standard orchestration framework for building and running LLM-based autonomous bug-finding and bug-fixing systems (Cyber Reasoning Systems).

## Why OSS-CRS?

- **Standard CRS Interface** — OSS-CRS defines a unified interface for CRS development. Build your CRS once following the [development guide](docs/crs-development-guide.md), and run it across different environments (local, Azure, ...) **without any modification**.
- **Effortless Targeting** — Run any CRS against projects in [OSS-Fuzz](https://github.com/google/oss-fuzz) format. If your project is compatible with OSS-Fuzz, OSS-CRS can orchestrate CRSs against it out of the box.
- **Ensemble Multiple CRSs** — Compose and run multiple CRSs together in a single campaign to combine their strengths and maximize bug-finding and bug-fixing coverage.
- **Resource Control** — Manage CPU limits and LLM budgets per CRS to keep costs and resources in check.
- **Multi-Environment Support** — Run locally today; deploy to Azure (coming soon) with zero changes to your CRS.

## Quick Start

### Prerequisites

| Requirement | Version |
|---|---|
| Python | >= 3.10 |
| Docker | latest |
| Git | latest |
| [uv](https://github.com/astral-sh/uv) | latest |

### 1. Set Up Your Environment (Optional)

```bash
uv run oss-crs setup
```

Setup is optional and walks you through two configuration steps:
- **LLM proxy routing** — if you access LLM providers through a proxy (e.g. an external LiteLLM instance), setup will override the API key and base URL env vars across all example configs. Skip this if you use provider keys directly (e.g. `export ANTHROPIC_API_KEY=sk-...`).
- **Cgroup resource management** — enables fine-grained per-CRS CPU and memory isolation. This requires root privileges and modifies system cgroup settings, so review each step before applying.

### 2. Prepare a Target Project (OSS-Fuzz Format)

OSS-CRS works with any project that follows the [OSS-Fuzz](https://github.com/google/oss-fuzz) project structure. Clone the OSS-Fuzz repository to get started:

```bash
git clone --depth=1 --filter=blob:none --no-checkout https://github.com/google/oss-fuzz.git
cd oss-fuzz
git sparse-checkout init --cone
git sparse-checkout set projects
git checkout
cd ..
```

> **Tip:** You can also prepare your own target repository as long as it is compatible with the OSS-Fuzz project format.

### 3. Run a Simple Bug-Finding CRS

The example below uses **crs-libfuzzer**, a lightweight CRS that runs libFuzzer on the target.
See [`./example/crs-libfuzzer/compose.yaml`](example/crs-libfuzzer/compose.yaml) for the full configuration.

```bash
# Prepare the CRS (pull images, set up dependencies)
uv run oss-crs prepare \
  --compose-file ./example/crs-libfuzzer/compose.yaml

# Build the target project
uv run oss-crs build-target \
  --compose-file ./example/crs-libfuzzer/compose.yaml \
  --fuzz-proj-path ./oss-fuzz/projects/libxml2

# Run the CRS against a specific harness (e.g., "xml")
uv run oss-crs run \
  --compose-file ./example/crs-libfuzzer/compose.yaml \
  --fuzz-proj-path ./oss-fuzz/projects/libxml2 \
  --target-harness xml
```

For the same `crs-libfuzzer` example, `run_env: azure` now supports a limited
Azure Spot VM backend. The same backend also supports no-LLM, single-runtime
Atlantis runs such as `atlantis-multilang-given_fuzzer`.

- `prepare` and `build-target` still run locally.
- `run` uploads the built libFuzzer artifacts, provisions a Spot VM, runs the
  libFuzzer container remotely, checkpoints artifacts to Blob Storage, restores
  results locally, and tears the VM down.
- Stable run inputs (`BUILD_OUT`, fuzz project files, and target source) are
  cached in Blob Storage by build/target/harness metadata so repeated Azure runs
  do not re-upload the same large artifacts.
- The VM polls Azure Scheduled Events and immediately uploads a serialized
  checkpoint when it receives a disruptive event such as Spot `Preempt`.
- Checkpoints are built from an incrementally maintained rsync snapshot. They
  are intended for practical partial recovery after Spot eviction, not a
  transactionally consistent filesystem snapshot.
- `--resume-run-id <previous-run-id>` starts a new Azure run with seeds restored
  from the previous run's final archive or latest checkpoint. Compatible runs
  also hydrate cached `rebuild_out` intermediates when available.
- The Azure path supports single-container bug-finding runtimes with no LLM and
  the expected five-container `atlantis-multilang-wo-concolic` module set when
  it uses external LiteLLM. Internal LiteLLM sidecars, ensembles, and other
  general multi-container CRS runtimes still require `run_env: local`.

Set these environment variables before `uv run oss-crs run` with `run_env: azure`:

- `OSS_CRS_AZURE_RESOURCE_GROUP`
- `OSS_CRS_AZURE_LOCATION`
- `OSS_CRS_AZURE_STORAGE_ACCOUNT`
- `OSS_CRS_AZURE_STORAGE_CONTAINER`
- `OSS_CRS_AZURE_ACR_NAME`
- `OSS_CRS_AZURE_VM_ADMIN_USERNAME`
- `OSS_CRS_AZURE_SSH_PUBLIC_KEY_PATH`

Optional Azure runtime tuning:

- `OSS_CRS_AZURE_VM_SIZE`
- `OSS_CRS_AZURE_VM_SIZE_CANDIDATES`
- `OSS_CRS_AZURE_VM_ZONES`
- `OSS_CRS_AZURE_VM_OS_DISK_SIZE_GB` (default `256`)
- `OSS_CRS_AZURE_KEEP_FAILED_VM`
- `OSS_CRS_AZURE_SYNC_INTERVAL_SECONDS`
- `OSS_CRS_AZURE_DOCKER_COMPOSE_VERSION`
- `OSS_CRS_AZURE_SPOT_MAX_PRICE` (default `0.50`; set `-1` explicitly to cap at
  the on-demand price)
- `OSS_CRS_AZURE_ENABLE_SSH` (default `false`; run-command does not require SSH)
- `OSS_CRS_AZURE_INPUT_CACHE_ENABLED` (default `true`)
- `OSS_CRS_AZURE_REBUILD_CACHE_ENABLED` (default `true`)
- `OSS_CRS_AZURE_REQUIRE_PREBUILT_IMAGES` (default `false`; when `true`, fail
  instead of building a missing runtime image locally)

The transient `run-inputs/<run-id>/inputs.tgz` blob is deleted after its run.
Result, checkpoint, cache, and runner-image artifacts are never pruned
automatically.

If the controller is terminated during final cleanup, rerun the idempotent,
run-scoped cleanup explicitly:

```sh
uv run oss-crs azure-cleanup --run-id <run-id>
```

This deletes only the VM, disk, and network resources derived from that run ID;
it preserves all Blob Storage artifacts.

### 4. Run an LLM-Powered CRS

For a more advanced CRS that leverages LLMs, you can use **atlantis-multilang**. This CRS supports multiple languages and uses an LLM to generate and refine fuzz harnesses.
See [`./example/multilang/multilang-compose.yaml`](example/multilang/multilang-compose.yaml) for the full configuration.

> **Environment variables:** For LLM-backed runs, you can either `export` provider credentials in your shell or place them in a `.env` file in the directory where you run `oss-crs`. The CLI loads `.env` automatically via dotenv before parsing the compose file.

```bash
# Prepare the LLM-powered CRS, for example, multilang
uv run oss-crs prepare \
  --compose-file ./example/multilang/multilang-compose.yaml 

# Build the target
uv run oss-crs build-target \
  --compose-file ./example/multilang/multilang-compose.yaml \
  --fuzz-proj-path ./oss-fuzz/projects/libxml2 

# Run the CRS
# export OPENAI_API_KEY=<your-openai-key>
# export GEMINI_API_KEY=<your-gemini-key>
# export ANTHROPIC_API_KEY=<your-anthropic-key>
# Or put the same variables in .env and skip the export lines.
uv run oss-crs run \
  --compose-file ./example/multilang/multilang-compose.yaml \
  --fuzz-proj-path ./oss-fuzz/projects/libxml2 \
  --target-harness xml
```

> **Note:** LLM-powered CRSs require an LLM API key. Refer to [docs/config/llm.md](docs/config/llm.md) for configuration details.

### 5. Run Claude Code CRSs with OAuth

**crs-bug-finding-claude-code** (bug-finding) and **crs-claude-code** (bug-fixing/patching) can authenticate directly with Anthropic via Claude Code's OAuth flow — no LiteLLM proxy or API key required.

**Authenticate once on the host:**

```bash
claude setup-token
```

**Export the token** (or add to a `.env` file in your working directory):

```bash
export CLAUDE_CODE_OAUTH_TOKEN=<your-oauth-token>
# OR in .env:
# CLAUDE_CODE_OAUTH_TOKEN=<your-oauth-token>
```

**Run the bug-finding CRS:**

```bash
uv run oss-crs prepare \
  --compose-file ./example/crs-bug-finding-claude-code/compose.yaml

uv run oss-crs build-target \
  --compose-file ./example/crs-bug-finding-claude-code/compose.yaml \
  --fuzz-proj-path ./oss-fuzz/projects/libxml2

uv run oss-crs run \
  --compose-file ./example/crs-bug-finding-claude-code/compose.yaml \
  --fuzz-proj-path ./oss-fuzz/projects/libxml2 \
  --target-harness xml
```

**Run the bug-fixing (patching) CRS:**

```bash
uv run oss-crs prepare \
  --compose-file ./example/crs-claude-code/compose.yaml

uv run oss-crs build-target \
  --compose-file ./example/crs-claude-code/compose.yaml \
  --fuzz-proj-path ./oss-fuzz/projects/libxml2 \
  --incremental-build

uv run oss-crs run \
  --compose-file ./example/crs-claude-code/compose.yaml \
  --fuzz-proj-path ./oss-fuzz/projects/libxml2 \
  --target-harness xml \
  --incremental-build
```

> **Note:** The `CLAUDE_CODE_OAUTH_TOKEN` in the compose file uses `${CLAUDE_CODE_OAUTH_TOKEN}`, so the value is read from your shell environment or `.env` file at runtime.

## Build Your Own CRS

OSS-CRS is designed to make CRS development simple. Follow the [CRS Development Guide](docs/crs-development-guide.md) to package your bug-finding or bug-fixing tool as a CRS. Once integrated, your CRS will:

- Work with any OSS-Fuzz-compatible target
- Run in any supported environment (local, Azure, ...) without modification
- Be composable with other CRSs for ensemble campaigns

## Documentation

- [CRS Development Guide](docs/crs-development-guide.md): How to build or integrate your own CRS
- [Architecture](docs/design/architecture.md): System design and component overview
- [Target Project](docs/config/target-project.md): Target project setup and OSS-Fuzz compatibility
- [CRS Configuration](docs/config/crs.md): CRS config reference
- [CRS-Compose Configuration](docs/config/crs-compose.md): Compose file reference
- [LLM Configuration](docs/config/llm.md): LLM provider setup
- [Changelog](CHANGELOG.md): Breaking changes, deprecations, and migration notes
- [Plan](PLAN.md): Upcoming features and planned improvements

## Contributing

We welcome contributions! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

See [LICENSE](LICENSE) for details.

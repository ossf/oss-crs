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

### 1. Prepare a Target Project (OSS-Fuzz Format)

OSS-CRS works with any project that follows the [OSS-Fuzz](https://github.com/google/oss-fuzz) project structure. Clone the OSS-Fuzz repository to get started:

```bash
git clone git@github.com:google/oss-fuzz.git ~/oss-fuzz
```

> **Tip:** You can also prepare your own target repository as long as it is compatible with the OSS-Fuzz project format.

### 2. Run a Simple Bug-Finding CRS

The example below uses **crs-libfuzzer**, a lightweight CRS that runs libFuzzer on the target.
See [`./example/crs-libfuzzer/crs-libfuzzer-compose.yaml`](example/crs-libfuzzer/crs-libfuzzer-compose.yaml) for the full configuration.

```bash
# Prepare the CRS (pull images, set up dependencies)
uv run oss-crs prepare \
  --compose-file ./example/crs-libfuzzer/crs-libfuzzer-compose.yaml

# Build the target project
uv run oss-crs build-target \
  --compose-file ./example/crs-libfuzzer/crs-libfuzzer-compose.yaml \
  --fuzz-proj-path ~/oss-fuzz/projects/libxml2

# Run the CRS against a specific harness (e.g., "xml")
uv run oss-crs run \
  --compose-file ./example/crs-libfuzzer/crs-libfuzzer-compose.yaml \
  --fuzz-proj-path ~/oss-fuzz/projects/libxml2 \
  --target-harness xml
```

### 3. Run an LLM-Powered CRS

For a more advanced CRS that leverages LLMs, you can use **atlantis-multilang**. This CRS supports multiple languages and uses an LLM to generate and refine fuzz harnesses.
See [`./example/multilang/multilang-compose.yaml`](example/multilang/multilang-compose.yaml) for the full configuration.

```bash
# Prepare the LLM-powered CRS, for example, multilang
uv run oss-crs prepare \
  --compose-file ./example/multilang/multilang-compose.yaml 

# Build the target
uv run oss-crs build-target \
  --compose-file ./example/multilang/multilang-compose.yaml \
  --fuzz-proj-path ~/oss-fuzz/projects/libxml2 

# Run the CRS
export OPENAI_API_KEY=<OPENAI_API_KEY>
export GEMINI_API_KEY=<GEMINI_API_KEY>
export ANTHROPIC_API_KEY=<ANTHROPIC_API_KEY>
uv run oss-crs run \
  --compose-file ./example/multilang/multilang-compose.yaml \
  --fuzz-proj-path ~/oss-fuzz/projects/libxml2 \
  --target-harness xml
```

> **Note:** LLM-powered CRSs require an LLM API key. Refer to [docs/config/llm.md](docs/config/llm.md) for configuration details.

### 4. Use the Builder CRS for Incremental Builds

For CRSs that generate source patches (bug-fixing), the **Builder CRS** provides fast incremental builds. Instead of rebuilding the target from scratch for each patch, the builder creates a snapshot of the compiled project and applies patches on top of it.

First, fetch the required oss-fuzz scripts (one-time setup):

```bash
bash scripts/setup-third-party.sh
```

Then add the builder as a separate CRS entry in your compose file alongside your patcher CRS:

```yaml
oss-crs-builder:
  source:
    local_path: /path/to/oss-crs/builder
  cpuset: "2-3"
  memory: "8G"

my-patcher-crs:
  source:
    local_path: /path/to/my-patcher-crs
  cpuset: "4-7"
  memory: "16G"
```

The framework automatically creates a snapshot image, starts the builder service, and connects it with your patcher CRS. Any CRS that uses `libCRS.apply_patch_build()` will communicate with the builder via HTTP.

See [`builder/README.md`](builder/README.md) for full details on the builder's API and configuration.

### 5. Run an Ensemble of Multiple CRSs

Combine multiple CRSs in a single campaign to get the best of each approach. Simply define them in an ensemble compose file
 For example, [`./example/ensemble/ensemble-compose.yaml`](example/ensemble/ensemble-compose.yaml) launches both **crs-libfuzzer** and **multilang** simultaneously. Check the compose file for detailed configuration.

```bash
# Prepare 
uv run oss-crs prepare \
  --compose-file ./example/ensemble/ensemble-compose.yaml 

# Build the target
uv run oss-crs build-target \
  --compose-file ./example/ensemble/ensemble-compose.yaml  \
  --fuzz-proj-path ~/oss-fuzz/projects/libxml2 

# Run the CRS
export OPENAI_API_KEY=<OPENAI_API_KEY>
export GEMINI_API_KEY=<GEMINI_API_KEY>
export ANTHROPIC_API_KEY=<ANTHROPIC_API_KEY>
uv run oss-crs run \
  --compose-file ./example/ensemble/ensemble-compose.yaml  \
  --fuzz-proj-path ~/oss-fuzz/projects/libxml2 \
  --target-harness xml
```

Each CRS runs independently with its own resource allocation, and results are aggregated automatically.

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
- [Builder CRS](builder/README.md): Incremental build service for patch-testing CRSs
- [LLM Configuration](docs/config/llm.md): LLM provider setup
- [Changelog](CHANGELOG.md): Breaking changes, deprecations, and migration notes
- [Plan](PLAN.md): Upcoming features and planned improvements

## Contributing

We welcome contributions! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

See [LICENSE](LICENSE) for details.

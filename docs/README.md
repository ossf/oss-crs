# OSS-CRS Documentation

Welcome to the OSS-CRS documentation. This guide covers everything from getting started to building your own Cyber Reasoning System.

For a quick introduction and setup instructions, see the [project README](../README.md).

---

## Getting Started

| Topic | Description |
|---|---|
| [Quick Start](../README.md#quick-start) | Install prerequisites and run your first CRS in minutes |
| [Setup Command](setup.md) | Configure system for enhanced resource management (cgroup-parent support) |
| [CRS Development Guide](crs-development-guide.md) | Build or integrate your own CRS into the OSS-CRS framework |
| [CRS Registry](registry.md) | Browse available CRSs ready to use out of the box |

## Configuration Reference

| Config File | Description |
|---|---|
| [CRS Compose (`crs-compose.yaml`)](config/crs-compose.md) | Orchestration config — define CRS entries, resources, and ensemble campaigns |
| [CRS (`crs.yaml`)](config/crs.md) | Per-CRS config — prepare, build, and run phases for a single CRS |
| [Target Project (`project.yaml`)](config/target-project.md) | Target project setup — OSS-Fuzz format and `project.yaml` schema |
| [LLM (`litellm_config.yaml`)](config/llm.md) | LiteLLM config file format for internal mode (provider routing, API keys, custom endpoints) |

## Architecture & Design

| Document | Description |
|---|---|
| [Architecture Overview](design/architecture.md) | System design, component diagram, and lifecycle walkthrough |
| [Parallel Builds and Runs](design/parallel.md) | Build/run isolation with `--build-id` and `--run-id` |
| [libCRS](design/libCRS.md) | CRS communication library — submit/fetch seeds, PoVs, and patches |
| [LLM Providers](llm-providers.md) | LiteLLM proxy setup for local and remote models |

## Key Concepts

### CRS Lifecycle

Every CRS campaign follows three phases managed by `oss-crs`:

1. **Prepare** — Pull CRS source repositories and build Docker images (`oss-crs prepare`)
2. **Build Target** — Compile the target project and run each CRS's target build pipeline (`oss-crs build-target`). Pass `--incremental-build` to create Docker snapshots for faster rebuilds.
3. **Run** — Launch all CRSs and shared infrastructure via Docker Compose (`oss-crs run`). Pass `--incremental-build` to use snapshot images for ephemeral rebuild containers.

### CRS Isolation

Each CRS runs in its own containerized environment with strict resource boundaries:

- **CPU** — Pinned to specific cores via `cpuset`
- **Memory** — Hard memory cap via `mem_limit`
- **LLM Budget** — Per-CRS dollar-denominated limits enforced by LiteLLM
- **Network** — Private Docker network per CRS; shared network for infrastructure access

Run `oss-crs setup` to enable [cgroup-parent mode](setup.md) for flexible resource sharing within each CRS.

### Ensemble Campaigns

Multiple CRSs can be composed in a single `crs-compose.yaml` to run simultaneously. Each CRS operates independently with its own resource allocation, and results are aggregated automatically.

## Contributing

See [CONTRIBUTING.md](../CONTRIBUTING.md) for guidelines on contributing to OSS-CRS.

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
2. **Build Target** — Compile the target project and run each CRS's target build pipeline (`oss-crs build-target`). Pass `--incremental-build` to create Docker snapshots for faster rebuilds. Pass `--coverage` to additionally build a coverage-instrumented binary for the [Web Dashboard](#web-dashboard).
3. **Run** — Launch all CRSs and shared infrastructure via Docker Compose (`oss-crs run`). Pass `--incremental-build` to use snapshot images for ephemeral rebuild containers. Pass `--web-ui` to monitor the run live in the [Web Dashboard](#web-dashboard). Pass `--forward-artifacts <run-id>[,<run-id>...]` to seed a run with artifacts from previous runs.
4. **Clean** — Remove Docker images and workdir artifacts from previous phases (`oss-crs clean`). Target a specific phase with `clean prepare`, `clean build-target`, or `clean run`, or clean everything at once. Add `--artifacts` to also remove workdir build/run directories.

### CRS Isolation

Each CRS runs in its own containerized environment with strict resource boundaries:

- **CPU** — Pinned to specific cores via `cpuset`
- **Memory** — Hard memory cap via `mem_limit`
- **LLM Budget** — Per-CRS dollar-denominated limits enforced by LiteLLM
- **Network** — Private Docker network per CRS; shared network for infrastructure access

Run `oss-crs setup` to enable [cgroup-parent mode](setup.md) for flexible resource sharing within each CRS.

### Ensemble Campaigns

Multiple CRSs can be composed in a single `crs-compose.yaml` to run simultaneously. Each CRS operates independently with its own resource allocation, and results are aggregated automatically.

### Web Dashboard

An optional WebUI dashboard visualizes live run status — per-CRS and exchange artifact counts (POVs, seeds, bug-candidates, reports, patches, diffs), LLM cost, and a coverage panel.

- **Start the service** — `oss-crs web-ui start` (add `--port <N>` to override the default port `9090`). Use `oss-crs web-ui status` to check it and `oss-crs web-ui stop` to tear it down.
- **Monitor a run** — pass `--web-ui` to `oss-crs run`. This starts the service if it isn't already running and publishes final artifact and cost totals after teardown so the finished run reflects the true results.
- **Coverage panel** — build the target with `oss-crs build-target --coverage` (or rely on `--web-ui`, which builds it on demand) to populate the coverage panel. The coverage build is best-effort: targets that can't be instrumented simply omit the panel without affecting fuzzing or failing the build.

## Contributing

See [CONTRIBUTING.md](../CONTRIBUTING.md) for guidelines on contributing to OSS-CRS.

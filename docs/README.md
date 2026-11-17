# OSS-CRS Documentation Wiki

Welcome to the comprehensive documentation book for the **Open Source Cyber Reasoning System (OSS-CRS)** framework.

OSS-CRS is an open, modular orchestration framework for building, evaluating, and running autonomous LLM-powered bug-finding and bug-fixing systems (Cyber Reasoning Systems) against OSS-Fuzz targets.

---

## 📖 Book Chapters

| Chapter | Title | Focus |
|---|---|---|
| **[Chapter 1](#chapter-1-getting-started)** | **Getting Started** | Setup, prerequisites, quick-start guide, and the CRS registry |
| **[Chapter 2](#chapter-2-architecture--system-design)** | **Architecture & System Design** | Multi-tier orchestrator, isolation model, lifecycle, and parallel execution |
| **[Chapter 3](#chapter-3-configuration-reference)** | **Configuration Reference** | Complete schema guides for compose, CRS definitions, LLMs, and MCP tools |
| **[Chapter 4](#chapter-4-crs-development--libcrs)** | **CRS Development & libCRS** | Building custom CRSs, inter-container IPC, builder sidecars, and Python SDK |
| **[Chapter 5](#chapter-5-shared-infrastructure--services)** | **Shared Infrastructure & Services** | LiteLLM proxy, PostgreSQL, budget tracking, and the WebUI dashboard |
| **[Chapter 6](#chapter-6-operations-workflows--tooling)** | **Operations, Workflows & Tooling** | Incremental builds, artifact forwarding, snapshotting, and cleanup wizards |
| **[Chapter 7](#chapter-7-enterprise-integrations--cicd)** | **Enterprise Integrations & CI/CD** | GitLab CI pipelines, air-gapped export/import, and multi-host deployment |

---

## Chapter 1: Getting Started

Learn how to configure your host machine and orchestrate your first Cyber Reasoning System campaign in minutes.

- **[Quick Start Guide](../README.md#quick-start)**: Step-by-step tutorial running `crs-libfuzzer` and multi-language LLM agents on `libxml2`.
- **[System Setup & Cgroup Guide](setup.md)**: Configuring host cgroup v2 delegation and Docker cgroupfs drivers for kernel-enforced sandboxing via `oss-crs setup`.
- **[CRS Registry](registry.md)**: Catalog of pre-integrated, ready-to-use bug-finding and bug-fixing CRSs (libFuzzer, Atlantis Multilang, Claude Code).

---

## Chapter 2: Architecture & System Design

Deep-dive into the architectural principles and subsystems powering OSS-CRS.

- **[System Architecture Overview](design/architecture.md)**: High-level architectural diagram, orchestration layers, component hierarchy, and data flow.
- **[Isolation & Resource Sandboxing](design/isolation-model.md)**: Linux cgroup v2 hierarchy, CPU pinning (`cpuset`), hard memory ceilings (`memory.max`), swap suppression (`swap.max = 0`), and private Docker networks.
- **[Campaign Lifecycle Walkthrough](design/lifecycle.md)**: Deterministic execution phases: `prepare` ➔ `export`/`import` ➔ `build-target` ➔ `run` ➔ `clean`.
- **[Parallel Builds and Runs](design/parallel.md)**: Multi-tenancy, parallel build isolation via `--build-id`, runtime isolation via `--run-id`, sanitizer matrices, and workdir partitioning.
- **[Shared Infrastructure Architecture](design/infrastructure.md)**: Centralized support services (`oss-crs-infra`), dynamic secret injection, and inter-CRS exchange mechanisms.

---

## Chapter 3: Configuration Reference

Declarative YAML specifications that configure targets, orchestration policies, and agent behaviors.

- **[CRS Compose Specification (`crs-compose.yaml`)](config/crs-compose.md)**: Orchestration manifest defining CRS participants, resource limits, Docker registry, and global LLM policies.
- **[CRS Package Specification (`crs.yaml`)](config/crs.md)**: Individual CRS manifest declaring bake files, target build steps, run-phase modules, and required inputs.
- **[Target Project Specification (`project.yaml`)](config/target-project.md)**: OSS-Fuzz target configuration format, compilation flags, sanitizers, and fuzz harnesses.
- **[LLM Configuration (`litellm_config.yaml`)](config/llm.md)**: Provider routing, fallback models, custom base URLs, and API key environment variables.
- **[MCP Tool Servers Specification](config/mcp.md)**: Model Context Protocol (MCP) tool integration, transports, mounts, and tool exposure.

---

## Chapter 4: CRS Development & libCRS

A comprehensive developer guide for writing your own autonomous bug-finding and patching systems.

- **[CRS Development Guide](crs-development-guide.md)**: End-to-end handbook for building, packaging, and testing a custom CRS.
- **[libCRS Communication Library Overview](design/libCRS.md)**: Master reference for the containerized agent SDK and CLI tool.
- **[libCRS Artifact Exchange & Directory Sync](design/libCRS-artifacts.md)**: Real-time artifact exchange: submitting, fetching, and filesystem event watching for `pov`, `seed`, `bug-candidate`, `report`, and `patch`.
- **[libCRS Builder Sidecar Interface](design/libCRS-builder-sidecar.md)**: Patch application, ephemeral rebuild containers, PoV verification execution, and REST endpoints.
- **[libCRS Python SDK Reference](design/libCRS-sdk.md)**: Native Python bindings (`libcrs`), `LocalCRSUtils`, strategy pattern, and code examples.

---

## Chapter 5: Shared Infrastructure & Services

Centralized services that coordinate campaigns, manage external API budgets, and stream observability data.

- **[LLM Providers & Budget Proxy](llm-providers.md)**: LiteLLM proxy configuration, rate limiting resilience, and PostgreSQL state tracking.
- **[Spend & Token Tracking](design/infrastructure.md#spend-and-token-reporting)**: Real-time tracking of prompt tokens, completion tokens, and dollar expenditures.
- **[WebUI Dashboard](design/infrastructure.md#3-webui-dashboard--event-streaming)**: Real-time campaign visualization, live artifact streaming, and source-level Clang coverage viewer.

---

## Chapter 6: Operations, Workflows & Tooling

Operational procedures for running efficient, large-scale fuzzing and benchmarking campaigns.

- **Incremental Builds & Docker Snapshots**: Using `--incremental-build` during `build-target` and `run` to cache intermediate build trees and accelerate patch verification.
- **Cross-Run Artifact Forwarding**: Seeding new campaigns with findings from previous runs via `--forward-artifacts`.
- **Environment & Workspace Pruning**: Reclaiming disk space and Docker storage safely with the interactive `oss-crs clean` wizard.

---

## Chapter 7: Enterprise Integrations & CI/CD

Automating OSS-CRS in enterprise infrastructure and automated software security pipelines.

- **[GitLab CI/CD Integration](integrations/gitlab.md)**: Complete guide to embedding autonomous CRS fuzzing and patch verification into GitLab CI pipelines.
- **[Air-Gapped Operation](design/lifecycle.md#2-phase-2-export--import-air-gapped-transfers)**: Offline CRS deployment via `oss-crs export`, `oss-crs import`, and `oss-crs-deps` self-contained Nix closures.

---

## Contributing & Community

- **[Contributing Guidelines](../CONTRIBUTING.md)**: How to file issues, submit pull requests, and contribute new CRSs.
- **[Meeting Notes & Presentations](meeting/)**: Archive of community sync notes, slides, and architectural roadmaps.

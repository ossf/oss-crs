# RFC - Standard Bug-finding CRS Interfaces (OSS-CRS)

## Table of Contents

- [Overview and context](#overview-and-context)
- [Glossary and terms](#glossary-and-terms)
- [Motivations](#motivations)
  - [Post-competition standardisation](#post-competition-standardisation)
  - [OSS-Fuzz is Not ready for AI-Augmented Workflows](#oss-fuzz-is-not-ready-for-ai-augmented-workflows)
- [Goals and Non-Goals](#goals-and-non-goals)
- [Background](#background)
- [Standardization Proposals / CRS Integration Topology](#standardization-proposals--crs-integration-topology)
  - [Command Structure](#command-structure)
  - [Registering a New CRS through the Registry](#registering-a-new-crs-through-the-registry)
  - [CRS Configuration (crs.yaml)](#crs-configuration-crsyaml)
  - [CRS-Compose Configuration](#crs-compose-configuration)
  - [LLM Configuration](#llm-configuration)
- [CRS Integration Interface (libCRS)](#crs-integration-interface-libcrs)
  - [Output format standards](#output-format-standards)
  - [Diff Mode Support](#diff-mode-support)
  - [CRS Ensemble Support](#crs-ensemble-support)
- [Example Reference CRSs](#example-reference-crses)
- [Alternative Considerations](#alternative-considerations)
- [Risks / Open Questions](#risks--open-questions)
- [Rollout / Timeline](#rollout--timeline)
- [References](#references)

---

## Overview and context

This RFC proposes a standardized set of interfaces and specifications for Cyber Reasoning Systems (CRSs) following the DARPA AIxCC competition.
The aim is to unify bug finding and reporting workflows to make them portable across teams, scalable from laptops to clusters, and compatible with oss-fuzz.

This RFC proposes to standardise the following systems:

- Bug Finding Systems
- Tools for LLM-based systems

**The implementation of this proposal: OSS-CRS ([https://github.com/sslab-gatech/oss-crs/tree/main](https://github.com/sslab-gatech/oss-crs/tree/main)) is an independent standard CRS framework that can test and run against OSS-Fuzz projects.**

## Glossary and terms

- **CRS** — Cyber Reasoning System
- **AIxCC** — AI Cyber Challenge
- **PoV** — Proof of Vulnerability
- **libCRS** — Python library providing the CRS integration interface

## Motivations

### Post-competition standardisation

- During AIxCC, each team built its own CRS stack, leading to fragmented APIs and inconsistent workflows.
- Without standardization, it is difficult for the community to share benchmarks, reproduce results, or integrate with shared infrastructure like oss-fuzz.
- Post-AIxCC, we want to provide a common framework so that CRSs can interoperate, researchers can compare results, and the broader security ecosystem can build on AIxCC outcomes.

### OSS-Fuzz is Not ready for AI-Augmented Workflows

#### Providing Essential Functionalities for LLMs

- No AI Resource Management: The system does not provide mechanisms to track or enforce limits on LLM usage (e.g., token budgets, query rate caps).
- Missing Agent Tooling: There is no support for prompt lifecycle tracking, environment introspection, or coordination across multiple agents.

#### Highlighting the Impact of LLMs through Fine-Grained Bug-Finding Phase Control

In most existing OSS-Fuzz use cases such as FuzzBench, fuzzing starts from scratch and runs until coverage stops growing. It usually takes a long time before we can tell which fuzzer or setup performs better, often by comparing the total number of bugs found or the final coverage.

In this RFC, we want to make this process more flexible.
By adding fine-grained control of fuzzing phases, we can run AI agents at different stages such as the early exploration phase or the later saturation phase.
This helps us understand where and how much LLMs can contribute without waiting for the entire campaign to finish.

#### Pushing OSS-Fuzz Closer to developers via Diff Mode

New commits are often where bugs are introduced. In the past, OSS-Fuzz and its differential fuzzing setup mainly served researchers by selecting seeds that could reach modified code regions, which made it less practical for developers doing daily commits.

With the help of LLMs, this can change.
LLMs can reason about what inputs are likely to reach critical or newly modified code areas.
By integrating this capability, OSS-Fuzz can better support developers in testing their latest commits as part of regular CI workflows and pre-merge checks, making it more useful for everyday development.

#### Supporting the Demand of Diverse CRS Ecosystems

In OSS-Fuzz, the set of fuzzers is predefined, so developers can only choose from existing ones such as AFL, LibFuzzer, and Honggfuzz. This design works well for traditional fuzzing but becomes limiting in the new era of LLMs.

As AI-driven techniques evolve, there is growing demand for different types of Cyber Reasoning Systems (CRSs). Beyond classic fuzzers, we now need reasoning-based agents that can work with both harnessed and unharnessed targets to handle a wider range of use cases.

Supporting diverse CRSs allows OSS-Fuzz to move beyond a fixed set of tools and become a more open and adaptable platform for evaluating and integrating new AI-driven approaches.

## Goals and Non-Goals

### Goals

- Eliminate one-off, competition-specific APIs.
- Support environments ranging from a local laptop to remote server.
- Provide friendly environments for LLM agent systems.
- Provide resource-aware configurations (CPU, RAM, LLM budgets).
- Handling of LLM API costs and rate limits.
- Enable per-harness execution and ensembling.
- Support both oss-fuzz upstream integration and local runs.

### Non-Goals

- Define new vulnerability discovery algorithms.
- Replace oss-fuzz; instead, extend compatibility.

## Background

- **CRS (Cyber Reasoning System):** automation systems that find and patch vulnerabilities.
- **AIxCC experience:** each team had strong but siloed implementations. Standardization is the natural next step.
- **Motivating pain points:**
  - Lack of consistent interface for invoking bug finding vs patching.
  - No standardized format for benchmarks (POVs, metadata).
  - Inconsistent patch aggregation policies.

## Standardization Proposals / CRS Integration Topology

### Command Structure

```bash
uv run oss-crs prepare --crs=atlantis-java
uv run oss-crs build-target --crs=atlantis-java --target-proj-path=<path>
uv run oss-crs run --crs=atlantis-java --target-proj-path=<path> --target-harness=<harness>

# Running multiple CRSs
uv run oss-crs run --crs=atlantis-java,atlantis-multilang --target-proj-path=<path> --target-harness=<harness>
```

| Command | Purpose |
|---------|---------|
| `prepare` | Build the CRS Docker images and set up dependencies. Run once per CRS version. |
| `build-target` | Build and instrument the target project using each CRS's build phase. Produces sanitizer-enabled binaries, coverage builds, etc. |
| `run` | Execute the CRS(s) against a specific harness. Starts fuzzing, analysis, and POV collection. |

By default, the CLI uses interactive prompts to configure resource allocation (CPU cores, memory), LLM budget, and other runtime settings.

#### Custom Configuration

For scripted or reproducible runs, use `--compose-file` to specify all configuration via a YAML file instead of interactive prompts:

```bash
uv run oss-crs prepare --crs=atlantis-java --compose-file <compose.yaml>
uv run oss-crs build-target --crs=atlantis-java --compose-file <compose.yaml> --target-proj-path <path>
uv run oss-crs run --crs=atlantis-java --compose-file <compose.yaml> --target-proj-path <path> --target-harness <harness>
```

### Registering a New CRS through the Registry

The CRS registry has been simplified to a minimal structure within the oss-crs repository at `registry/`. The registry contains only basic metadata for each CRS, while the full CRS configuration lives in the CRS's own repository.

#### Registry Structure

```
registry/
├── atlantis-multilang-wo-concolic.yaml
└── crs-libfuzzer.yaml
```

#### pkg.yaml Format

Each CRS only needs its own YAML in the registry:

```yaml
type: bug-finding
source:
  url: git@github.com:Team-Atlanta/crs-libfuzzer.git
  ref: main
```

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | CRS identifier |
| `type` | Yes | `bug-finding` or `bug-fixing` |
| `source.url` | Yes* | Git repository URL |
| `source.ref` | Yes* | Git reference (branch, tag, or commit) |
| `source.local_path` | Yes* | Alternative: local filesystem path |

\* Either `url` + `ref` OR `local_path` must be provided.

The full CRS configuration is stored in the CRS repository itself at `oss-crs/crs.yaml`, enabling CRS authors to manage their configurations directly without requiring pull requests to the oss-crs project.

### CRS-Compose Configuration

Operators use a CRS-Compose file to orchestrate one or more CRSs with resource allocation.

#### Schema

```yaml
run_env: local
docker_registry: ghcr.io/team-atlanta/test

oss_crs_infra:
  cpuset: "0-3"
  memory: "16G"

crs-libfuzzer:
  source:
    url: git@github.com:Team-Atlanta/crs-libfuzzer.git
    ref: refined-oss-crs
  cpuset: "4-7"
  memory: "16G"

multilang:
  source:
    url: git@github.com:Team-Atlanta/atlantis-multilang-wo-concolic.git
    ref: refined-oss-crs
  cpuset: "8-11"
  memory: "16G"
  llm_budget: 100  # dollars

llm_config:
  litellm_config: ./example/multilang/litellm-config.yaml
```

#### Configuration Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `run_env` | enum | Yes | `local` or `azure` |
| `docker_registry` | string | Yes | Docker registry URL |
| `oss_crs_infra` | ResourceConfig | Yes | Infrastructure resource allocation |
| `llm_config.litellm_config` | string | No* | Path to LiteLLM configuration file |
| `<crs-name>` | CRSEntry | Yes | One or more CRS entries with source and resources |

\* Required if any CRS uses LLMs.

#### CRS Entry Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `source.url` | string | Yes* | Git repository URL |
| `source.ref` | string | Yes* | Git reference |
| `source.local_path` | string | Yes* | Alternative: local path |
| `cpuset` | string | Yes | CPU cores (e.g., `"4-7"`, `"0,2,4,6"`) |
| `memory` | string | Yes | Memory limit (e.g., `"16G"`) |
| `llm_budget` | integer | No | LLM budget in dollars |

\* Either `url` + `ref` OR `local_path` must be provided.

### LLM Configuration

LLM configuration uses the native [LiteLLM Proxy Configuration](https://docs.litellm.ai/docs/proxy/configs) format. The configuration file path is specified in the CRS-Compose file via `llm_config.litellm_config`.

#### Example LiteLLM Configuration

```yaml
model_list:
  # OpenAI
  - model_name: gpt-4o
    litellm_params:
      model: openai/gpt-4o
      api_key: os.environ/OPENAI_API_KEY

  # Anthropic
  - model_name: claude-sonnet-4-20250514
    litellm_params:
      model: anthropic/claude-sonnet-4-20250514
      api_key: os.environ/ANTHROPIC_API_KEY

  # Gemini
  - model_name: gemini-2.5-pro
    litellm_params:
      model: gemini/gemini-2.5-pro
      api_key: os.environ/GEMINI_API_KEY

  # Custom/Self-hosted (vLLM, Ollama, Azure)
  - model_name: my-local-llama
    litellm_params:
      model: openai/meta-llama/Llama-3.1-70B
      api_key: os.environ/VLLM_API_KEY
      api_base: "http://localhost:8000/v1"
```

#### LLM Availability Validation

CRSs declare their LLM requirements via `required_llms` in `oss-crs/crs.yaml`. At runtime, oss-crs validates that all required models are available in the LiteLLM configuration before launching the CRS.

### Diff Mode Support

Diff Mode lets the CRS focus on code changes between commits instead of fuzzing the whole project.
It's useful for validating patches, PRs, or a series of commits where you only want to explore the modified code paths.

#### How it works

You specify a **commit range**, **a single commit**, or a **diff file** as the base for comparison.
The system analyzes all changes from that base to HEAD and builds a focus map (changed files, functions, or lines).
**Fuzzing/Analyzing still runs on the current HEAD**, but inputs that reach or get close to these modified areas are prioritized.

```bash
# Compare HEAD to previous commit
uv run oss-crs run \
  --compose-file <compose.yaml> \
  --focus-base HEAD~1 \
  --target-proj-path <path> \
  --target-harness <harness>

# Compare HEAD to a specific base commit
uv run oss-crs run \
  --compose-file <compose.yaml> \
  --focus-base <commit-hash> \
  --target-proj-path <path> \
  --target-harness <harness>

# Compare HEAD to the main branch
uv run oss-crs run \
  --compose-file <compose.yaml> \
  --focus-base origin/main \
  --target-proj-path <path> \
  --target-harness <harness>

# Use a raw diff
uv run oss-crs run \
  --compose-file <co<path> \
  --focus-diff <path> \
  --target-proj-path <path> \
  --target-harness <harness>
```

### CRS Ensemble Support

**Motivation:** Combine pure agents with fuzzers for maximum coverage.

Multiple CRSs can run simultaneously against the same target, sharing data through libCRS:

- **Seed sharing:** CRSs register fetch directories to receive seeds from other CRSs
- **POV deduplication:** Infrastructure automatically deduplicates submitted POVs
- **Resource isolation:** Each CRS runs with its own cpuset and memory limits

<!-- NOTE: comment out until desired CLI interface is implemented -->
<!-- ### A Working Example -->

<!-- #### Simple CRS (crs-libfuzzer) -->

<!-- ```bash -->
<!-- # Prepare the CRS -->
<!-- uv run oss-crs prepare \ -->
<!--   --compose-file ./example/crs-libfuzzer/crs-libfuzzer-compose.yaml -->

<!-- # Build target -->
<!-- uv run oss-crs build-target \ -->
<!--   --compose-file ./example/crs-libfuzzer/crs-libfuzzer-compose.yaml \ -->
<!--   --target-proj-path ~/oss-fuzz/projects/libxml2 -->

<!-- # Run -->
<!-- uv run oss-crs run \ -->
<!--   --compose-file ./example/crs-libfuzzer/crs-libfuzzer-compose.yaml \ -->
<!--   --target-proj-path ~/oss-fuzz/projects/libxml2 \ -->
<!--   --target-harness xml -->
<!-- ``` -->

<!-- #### LLM-Powered CRS (multilang) -->

<!-- ```bash -->
<!-- # Set API keys -->
<!-- export OPENAI_API_KEY=<key> -->
<!-- export ANTHROPIC_API_KEY=<key> -->

<!-- # Prepare, build, and run -->
<!-- uv run oss-crs prepare \ -->
<!--   --compose-file ./example/multilang/multilang-compose.yaml -->

<!-- uv run oss-crs build-target \ -->
<!--   --compose-file ./example/multilang/multilang-compose.yaml \ -->
<!--   --target-proj-path ~/oss-fuzz/projects/libxml2 -->

<!-- uv run oss-crs run \ -->
<!--   --compose-file ./example/multilang/multilang-compose.yaml \ -->
<!--   --target-proj-path ~/oss-fuzz/projects/libxml2 \ -->
<!--   --target-harness xml -->
<!-- ``` -->

<!-- #### Ensemble (multiple CRSs) -->

<!-- ```bash -->
<!-- # Run both crs-libfuzzer and multilang together -->
<!-- uv run oss-crs prepare \ -->
<!--   --compose-file ./example/ensemble/ensemble-compose.yaml -->

<!-- uv run oss-crs build-target \ -->
<!--   --compose-file ./example/ensemble/ensemble-compose.yaml \ -->
<!--   --target-proj-path ~/oss-fuzz/projects/libxml2 -->

<!-- uv run oss-crs run \ -->
<!--   --compose-file ./example/ensemble/ensemble-compose.yaml \ -->
<!--   --target-proj-path ~/oss-fuzz/projects/libxml2 \ -->
<!--   --target-harness xml -->
<!-- ``` -->

## CRS Integration Interface

### CRS Configuration (crs.yaml)

Each CRS repository must include `oss-crs/crs.yaml` defining the CRS's build phases, run modules, and capabilities.

#### Configuration Schema

```yaml
# Example: atlantis-multilang (LLM-powered, multiple modules)
name: atlantis-multilang-wo-concolic
type:
  - bug-finding
version: 1.0.0
docker_registry: ghcr.io/team-atlanta/atlantis-multilang-wo-concolic

prepare_phase:
  hcl: oss-crs/docker-bake.hcl

target_build_phase:
  - name: uniafl-build
    dockerfile: oss-crs/dockerfiles/builder.Dockerfile
    additional_env:
      BUILD_TYPE: uniafl
    outputs:
      - uniafl/build
      - uniafl/src
  - name: coverage-build
    dockerfile: oss-crs/dockerfiles/builder.Dockerfile
    additional_env:
      BUILD_TYPE: coverage
    outputs:
      - coverage/build

crs_run_phase:
  multilang:
    dockerfile: oss-crs/dockerfiles/multilang.Dockerfile
    additional_env:
      CRS_INPUT_GENS: given_fuzzer,mlla,testlang_input_gen
  redis:
    dockerfile: oss-crs/dockerfiles/redis.Dockerfile
  joern:
    dockerfile: oss-crs/dockerfiles/joern.Dockerfile

supported_target:
  mode:
    - full
    - delta
  language:
    - c
  sanitizer:
    - address
  architecture:
    - x86_64

required_llms:
  - gpt-4o
  - claude-sonnet-4-20250514
  - gemini-2.5-pro
```

#### Root Configuration Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | Yes | CRS name |
| `type` | Set[CRSType] | Yes | `bug-finding` and/or `bug-fixing` |
| `version` | string | Yes | Version string |
| `docker_registry` | string | Yes | Docker registry URL for CRS images |
| `prepare_phase` | PreparePhase | Yes | Configuration for the prepare phase |
| `target_build_phase` | list[BuildConfig] | Yes | Build steps for the target |
| `crs_run_phase` | dict[str, Module] | Yes | Modules executed during CRS run |
| `supported_target` | SupportedTarget | Yes | Defines compatible targets |
| `required_llms` | list[string] | No | LLM model names required by this CRS |

#### Dockerfile Conventions

**Builder Dockerfile** (`target_build_phase`): Uses the target project image as base via build arg. This allows CRS-specific instrumentation on top of the OSS-Fuzz project build.

```dockerfile
ARG target_base_image
FROM $target_base_image

# Install libCRS for build output management
COPY --from=libcrs . /libCRS
RUN /libCRS/install.sh

COPY bin/compile_target /usr/local/bin/compile_target
CMD ["compile_target"]
```

**Runner Dockerfile** (`crs_run_phase`): Typically based on `gcr.io/oss-fuzz-base/base-runner` or a custom image. Includes libCRS for data submission and service discovery.

```dockerfile
FROM gcr.io/oss-fuzz-base/base-runner

# Install libCRS for POV/seed submission
COPY --from=libcrs . /libCRS
RUN /libCRS/install.sh

COPY ./run_fuzzer_wrapper.sh /usr/local/bin/run_fuzzer_wrapper.sh
ENTRYPOINT ["/usr/local/bin/run_fuzzer_wrapper.sh"]
```

The `libcrs` context is automatically provided by oss-crs during build.

### libCRS

CRSs interact with the oss-crs infrastructure through **libCRS**, a Python library that provides a standardized interface for data exchange, build output management, and service discovery.

#### Data Types

| Type | Description |
|------|-------------|
| `POV` | Proof of Vulnerability inputs |
| `SEED` | Fuzzing corpus seeds |
| `BUG_CANDIDATE` | Potential bug reports |
| `PATCH` | Bug fix patches |

#### Core API

```python
from libCRS import CRSUtils, DataType

crs = CRSUtils()

# Build Output Management
crs.download_build_output(src_path, dst_path)  # Download from infra
crs.submit_build_output(src_path, dst_path)    # Submit to infra
crs.skip_build_output(dst_path)                # Skip a build output

# Data Auto-Sync (register directories for automatic sync)
crs.register_submit_dir(DataType.POV, path)    # Auto-submit new files
crs.register_fetch_dir(DataType.SEED, path)    # Auto-fetch shared data
crs.register_shared_dir(local_path, shared_path)  # Share between containers

# Manual Data Operations
crs.submit(DataType.POV, file_path)            # Submit a single file
crs.fetch(DataType.SEED, directory)            # Fetch shared data

# Service Discovery
domain = crs.get_service_domain("litellm")     # Get service endpoint
```

#### Environment Variables

CRSs receive the following environment variables:

| Variable | Description |
|----------|-------------|
| `OSS_CRS_NAME` | Name of the current CRS |
| `OSS_CRS_SERVICE_NAME` | Full service name (e.g., `multilang_fuzzer`) |
| `OSS_CRS_RUN_ENV_TYPE` | Runtime environment (`local` or `azure`) |
| `OSS_CRS_CPUSET` | Allocated CPU cores (e.g., `"4-7"`) |
| `OSS_CRS_MEMORY_LIMIT` | Memory limit (e.g., `"16G"`) |
| `OSS_CRS_TARGET` | Target project name |
| `OSS_CRS_TARGET_HARNESS` | Target harness name |
| `OSS_CRS_BUILD_OUT_DIR` | Build output directory (read-only mount) |
| `OSS_CRS_SUBMIT_DIR` | Directory for submitting data |
| `OSS_CRS_FETCH_DIR` | Directory for fetching shared data (read-only) |
| `OSS_CRS_SHARED_DIR` | Shared data directory |
| `OSS_CRS_LLM_API_URL` | LiteLLM proxy URL (if LLM configured) |
| `OSS_CRS_LLM_API_KEY` | Per-CRS LiteLLM API key (if LLM configured) |

### Output format standards

For bug-finding CRSs, the output contains crashes, corpus, and crs-specific data:

```
/artifacts/                     # CRS output directory (container)
├── povs/                       # POVs discovered (required for bug finding CRS)
│   ├── pov_001                 # Binary blob (test input that triggers vulnerability)
│   ├── pov_002                 # Binary blob
│   └── pov_003                 # Binary blob
├── corpus/                     # Fuzzing corpus (optional)
│   ├── input-001               # Test input
│   ├── input-002
│   └── input-003
└── crs-data/                   # CRS-specific outputs (optional)
    ├── analysis-report.txt     # Any additional data CRS wants to record
    ├── intermediate-results.json
    └── debug-trace.log

/shared_seeds/                  # Shared seeds from other CRSs (via OSS_CRS_FETCH_DIR)
├── input-from-crs-a
└── input-from-crs-b
```

#### libCRS Usage for Data Submission and Fetching

```python
from libCRS import CRSUtils, DataType
from pathlib import Path

crs = CRSUtils()

# Register directories for automatic POV and seed submission
# Files placed in these directories are auto-submitted to oss-crs-infra
crs.register_submit_dir(DataType.POV, Path("/artifacts/povs"))
crs.register_submit_dir(DataType.SEED, Path("/artifacts/corpus"))

# Fetch seeds shared by other CRSs in the ensemble
shared_seeds = crs.fetch(DataType.SEED, Path("/shared_seeds"))
for seed in shared_seeds:
    print(f"Received seed from another CRS: {seed}")
```

## Example Reference CRSs

| CRS | Type | Description |
|-----|------|-------------|
| crs-libfuzzer | bug-finding | Baseline libFuzzer wrapper |
| atlantis-multilang | bug-finding | LLM-powered multi-language fuzzer |

## Risks / Open Questions

- Multi-language CRS support (C, Rust, Java).
- Distributed System support.
- Secure handling of LLM API keys.
- Resource isolation between CRS instances.

## References

- GitHub Issue #42 - CRS Configuration Format Refinement: [https://github.com/sslab-gatech/oss-crs/issues/42](https://github.com/sslab-gatech/oss-crs/issues/42)
- GitHub Issue #56 - LLM Configuration Enhancement: [https://github.com/sslab-gatech/oss-crs/issues/56](https://github.com/sslab-gatech/oss-crs/issues/56)
- LiteLLM Proxy Configuration: [https://docs.litellm.ai/docs/proxy/configs](https://docs.litellm.ai/docs/proxy/configs)

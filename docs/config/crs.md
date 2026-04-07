# CRS Configuration File Reference

This document describes the configuration file format for CRS (Cyber Reasoning System) used in the OSS-CRS project.
Configuration files are written in YAML format and must be placed `oss-crs/crs.yaml` in CRS repositories, not oss-crs repository.

## Table of Contents

- [Overview](#overview)
- [Root Configuration](#root-configuration)
- [Prepare Phase](#prepare-phase)
- [Target Build Phase](#target-build-phase)
- [CRS Run Phase](#crs-run-phase)
- [Supported Target](#supported-target)
- [Enumerations Reference](#enumerations-reference)

---

## Overview

A CRS configuration file defines how a Cyber Reasoning System is prepared, built, and run. The configuration is validated using Pydantic models and can be loaded from YAML files.

## Root Configuration

The root `CRSConfig` object contains the following fields:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | `string` | Yes | The name of the CRS |
| `type` | `Set[CRSType]` | Yes | The type(s) of CRS (see [CRSType](#crstype)) |
| `version` | `string` | Yes | Version string for the CRS (cannot be empty) |
| `docker_registry` | `string` | Yes | Docker registry URL for CRS images (cannot be empty) |
| `prepare_phase` | `PreparePhase` | Yes | Configuration for the prepare phase |
| `target_build_phase` | `TargetBuildPhase` | Yes | Configuration for the target build phase |
| `crs_run_phase` | `CRSRunPhase` | Yes | Configuration for the CRS run phase |
| `supported_target` | `SupportedTarget` | Yes | Defines what targets this CRS supports |
| `required_llms` | `list[string]` | No | Minimum required LLM model names (duplicates are automatically removed). This is for dependency validation, not a runtime allowlist. |
| `required_inputs` | `list[string]` | No | Input channels this CRS requires to function. Valid names: `diff`, `pov`, `seed`, `bug-candidate`. When declared, `oss-crs run` validates that the corresponding CLI flags were provided before spawning containers. |

### Example

```yaml
name: my-crs
type:
  - bug-finding
version: "1.0.0"
docker_registry: "ghcr.io/my-org/my-crs"
prepare_phase:
  hcl: oss-crs/build.hcl
target_build_phase:
  - name: build-step-1
    dockerfile: oss-crs/Build.Dockerfile
    outputs:
      - fuzzer
crs_run_phase:
  module-1:
    dockerfile: oss-crs/docker-compose/bug-finding.Dockerfile
    additional_env:
      CUSTOM_VAR: "value"
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
required_llms:
  - gpt-5
required_inputs:
  - diff
  - bug-candidate
```

---

## Prepare Phase

The `prepare_phase` configures the preparation step of the CRS.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `hcl` | `string` | Yes | Path to the HCL file (must have `.hcl` extension) |

### Example

```yaml
prepare_phase:
  hcl: oss-crs/build.hcl
```

---

## Target Build Phase

The `target_build_phase` defines one or more build steps for the target.

### Structure

The target build phase is a list of `BuildConfig` objects, each defining a named build step.

### BuildConfig

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `name` | `string` | Yes | - | The name of the build step |
| `dockerfile` | `string` | Yes | - | Path to the Dockerfile (must contain "Dockerfile" or end with `.Dockerfile`). Use an `oss-crs-infra:` prefix for framework-provided builds (e.g., `oss-crs-infra:default-builder`). |
| `outputs` | `list[string]` | No | `[]` | List of output paths to persist from the build. |
| `additional_env` | `dict[string, string]` | No | `{}` | Additional environment variables to pass during the build. Keys must match `[A-Za-z_][A-Za-z0-9_]*`. |

### Example

```yaml
target_build_phase:
  - name: asan
    dockerfile: oss-crs/asan-builder.Dockerfile
    additional_env:
      RUNNING_TIME_ENV: "value"
    outputs:
      - asan.tar.gz
  - name: cov-builder
    dockerfile: oss-crs/cov-builder.Dockerfile
    outputs:
      - cov-builder.tar.gz
```

### Directed Build Inputs

For directed fuzzing workflows, `oss-crs build-target` can stage input artifacts into build containers:

- `--diff <file>`: provide a reference diff
- `--bug-candidate <file>`: provide a single bug-candidate report
- `--bug-candidate-dir <dir>`: provide a directory of bug-candidate reports

When provided, these artifacts are mounted into `OSS_CRS_FETCH_DIR` during the build phase.
Builder scripts should consume them through libCRS fetch commands, for example:

```bash
libCRS fetch diff /work/diff
libCRS fetch bug-candidate /work/bug-candidates
```

### Input Semantics (Capability vs Requirement)

OSS-CRS distinguishes between:

- **Capability**: a CRS can consume a given input type through libCRS (for example `seed`, `pov`, `bug-candidate`, `diff`).
- **Requirement**: a specific CRS needs that input to function for a given workflow.

Framework-level integration expects CRS containers to support standard input channels where applicable, but input presence is still workflow-dependent.
For example, initial seeds are optional in general OSS-CRS execution, while a directed fuzzer may require a SARIF bug-candidate to start.
When an input is required by a CRS, validate and fail fast in CRS logic, and document the requirement in CRS-specific docs.

---

## CRS Run Phase

The `crs_run_phase` configures how the CRS is executed. It defines one or more modules, each with its own Dockerfile and environment variables.

### Structure

The CRS run phase is a dictionary where each key is a module name and each value is a `CRSRunPhaseModule` object.

### CRSRunPhaseModule

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `dockerfile` | `string` | Yes | - | Path to the Dockerfile (must contain "Dockerfile" or end with `.Dockerfile`). Can use `oss-crs-infra:` prefix for framework-provided services. |
| `additional_env` | `dict[string, string]` | No | `{}` | Additional environment variables to pass to the module. Keys must match `[A-Za-z_][A-Za-z0-9_]*`. |

### Example

```yaml
crs_run_phase:
  module-1:
    dockerfile: oss-crs/docker-compose/bug-finding.Dockerfile
    additional_env:
      RUNNING_TIME_ENV: "XXX"
  module-2:
    dockerfile: oss-crs/docker-compose/bug-finding.Dockerfile
    additional_env:
      RUNNING_TIME_ENV: "XXX2"
```

**Note:** Builder and runner sidecars are injected automatically by the framework during the run phase. CRS developers do not need to declare them in `crs_run_phase`. The `BUILDER_MODULE` environment variable is set automatically.

### `additional_env` Key Rules

- Key format is validated: `[A-Za-z_][A-Za-z0-9_]*`.
- Build-option keys such as `SANITIZER`, `FUZZING_ENGINE`, `ARCHITECTURE`, and
  `FUZZING_LANGUAGE` can be overridden through `additional_env`.
- `OSS_CRS_*` namespace is reserved for framework-managed values. If users set
  those keys in `additional_env`, `oss-crs` warns (`ENV001`/`ENV002`).
- For framework-owned `OSS_CRS_*` keys in a given phase, framework values take
  precedence. Unknown `OSS_CRS_*` keys are warned and may pass through.

---

## Supported Target

The `supported_target` section defines what types of targets the CRS can work with.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `mode` | `Set[TargetMode]` | Yes | Supported target modes (see [TargetMode](#targetmode)) |
| `language` | `Set[TargetLanguage]` | Yes | Supported programming languages (see [TargetLanguage](#targetlanguage)) |
| `sanitizer` | `Set[TargetSanitizer]` | Yes | Supported sanitizers (see [TargetSanitizer](#targetsanitizer)) |
| `architecture` | `Set[TargetArch]` | Yes | Supported CPU architectures (see [TargetArch](#targetarch)) |
| `fuzzing_engine` | `Set[FuzzingEngine]` | No | Supported fuzzing engines (see [FuzzingEngine](#fuzzingengine)). Defaults to all engines when omitted. |

### Example

```yaml
supported_target:
  mode:
    - full
    - delta
  language:
    - c
    - c++
    - rust
  sanitizer:
    - address
    - undefined
  architecture:
    - x86_64
  fuzzing_engine:
    - libfuzzer
    - afl
```

---

## Enumerations Reference

### CRSType

Defines the type of CRS:

| Value | Description |
|-------|-------------|
| `bug-finding` | CRS designed for finding bugs |
| `bug-fixing` | CRS designed for fixing bugs |
| `builder` | Builder-side CRS for incremental patch build/test workflows |

### TargetMode

Defines the operating mode for targets:

| Value | Description |
|-------|-------------|
| `full` | Full mode |
| `delta` | Delta mode |

### TargetLanguage

Supported programming languages (based on [OSS-Fuzz language support](https://google.github.io/oss-fuzz/getting-started/new-project-guide/#language)):

| Value | Description |
|-------|-------------|
| `c` | C language |
| `c++` | C++ language |
| `go` | Go language |
| `rust` | Rust language |
| `python` | Python language |
| `jvm` | JVM-based languages (Java, Kotlin, Scala, etc.) |
| `swift` | Swift language |
| `javascript` | JavaScript language |
| `lua` | Lua language |

### TargetSanitizer

Supported sanitizers (based on [OSS-Fuzz sanitizer support](https://google.github.io/oss-fuzz/getting-started/new-project-guide/#sanitizers)):

| Value | Description |
|-------|-------------|
| `address` | AddressSanitizer (ASAN) - detects memory errors |
| `memory` | MemorySanitizer (MSAN) - detects uninitialized memory reads |
| `undefined` | UndefinedBehaviorSanitizer (UBSAN) - detects undefined behavior |

### TargetArch

Supported CPU architectures (based on [OSS-Fuzz architecture support](https://google.github.io/oss-fuzz/getting-started/new-project-guide/#architectures)):

| Value | Description |
|-------|-------------|
| `x86_64` | 64-bit x86 architecture |
| `i386` | 32-bit x86 architecture |

### FuzzingEngine

Supported fuzzing engines (based on [OSS-Fuzz fuzzing engine support](https://google.github.io/oss-fuzz/getting-started/new-project-guide/#fuzzing_engines-optional)):

| Value | Description |
|-------|-------------|
| `libfuzzer` | LLVM libFuzzer |
| `afl` | American Fuzzy Lop |
| `honggfuzz` | Honggfuzz |
| `centipede` | Centipede |

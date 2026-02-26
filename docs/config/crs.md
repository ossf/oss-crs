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
| `outputs` | `list[string]` | No | `[]` | List of output paths. Can be empty for snapshot builds where no explicit outputs are needed. |
| `snapshot` | `bool` | No | `false` | When `true`, creates a snapshot Docker image during the build phase. The snapshot captures the fully compiled target and is used as the base image for incremental builds. |
| `additional_env` | `dict[string, string]` | No | `{}` | Additional environment variables to pass during the build |

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

### Example with Snapshot Builds

When using incremental builds, declare snapshot build steps that create reusable Docker images:

```yaml
target_build_phase:
  - name: asan-snapshot
    dockerfile: oss-crs-infra:default-builder
    snapshot: true
    additional_env:
      SANITIZER: address
  - name: coverage-snapshot
    dockerfile: oss-crs-infra:default-builder
    snapshot: true
    additional_env:
      SANITIZER: coverage
```

---

## CRS Run Phase

The `crs_run_phase` configures how the CRS is executed. It defines one or more modules, each with its own Dockerfile and environment variables.

### Structure

The CRS run phase is a dictionary where each key is a module name and each value is a `CRSRunPhaseModule` object.

### CRSRunPhaseModule

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `dockerfile` | `string` | Conditional | - | Path to the Dockerfile (must contain "Dockerfile" or end with `.Dockerfile`). Can use `oss-crs-infra:` prefix for framework-provided services. **Required** when `run_snapshot` is `false`; **optional** when `run_snapshot` is `true`. |
| `run_snapshot` | `bool` | No | `false` | When `true`, uses the snapshot image from the build phase as this module's base image. Enables the module to use builder sidecar commands (`apply-patch-build`, `run-pov`, `run-test`). |
| `additional_env` | `dict[string, string]` | No | `{}` | Additional environment variables to pass to the module |

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

### Example with Incremental Build Modules

Builder sidecar modules use `run_snapshot: true` to start from the snapshot image. The patcher module is a regular module that communicates with builder sidecars using the `--builder` flag on libCRS commands:

```yaml
crs_run_phase:
  patcher:
    dockerfile: oss-crs/docker-compose/patcher.Dockerfile
    additional_env:
      MAX_PATCHES: "100"
  builder-asan:
    dockerfile: oss-crs-infra:default-builder
    run_snapshot: true
  builder-coverage:
    dockerfile: oss-crs-infra:default-builder
    run_snapshot: true
```

---

## Supported Target

The `supported_target` section defines what types of targets the CRS can work with.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `mode` | `Set[TargetMode]` | Yes | Supported target modes (see [TargetMode](#targetmode)) |
| `language` | `Set[TargetLanguage]` | Yes | Supported programming languages (see [TargetLanguage](#targetlanguage)) |
| `sanitizer` | `Set[TargetSanitizer]` | Yes | Supported sanitizers (see [TargetSanitizer](#targetsanitizer)) |
| `architecture` | `Set[TargetArch]` | Yes | Supported CPU architectures (see [TargetArch](#targetarch)) |

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

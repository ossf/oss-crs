# crs-mantis (Auditing CRS)

An LLM-powered auditing CRS based on [Google Mantis](https://github.com/google/mantis) that analyzes target source code directly and submits SARIF 2.1.0 bug candidate reports via `libCRS`.

## Overview

Unlike fuzzer-based bug-finding CRSs that require compiled harnesses, `crs-mantis` is registered as an `auditing` CRS. It executes the core Mantis security skills against `OSS_CRS_TARGET_SOURCE`:

1. **Threat Modeling (`threat-model`)**: Analyzes codebase entrypoints, external interfaces, and security-sensitive boundaries.
2. **Research (`research`)**: Deep-dives into suspected vulnerability classes (buffer overflows, memory safety, injection, logic flaws).
3. **Review & Critic (`review`)**: Filters false positives with independent critic evaluations and verifies exploit feasibility.
4. **Report (`report`)**: Emits validated findings as structured SARIF 2.1.0 bug candidate records through `libCRS.submit_bug_candidate()`.

## Usage

### 1. Configure Environment

Ensure your chosen LLM provider API key is set:

```bash
export GEMINI_API_KEY="your-gemini-key"
# Or ANTHROPIC_API_KEY / OPENAI_API_KEY depending on model choice
```

### 2. Prepare the CRS

```bash
uv run oss-crs prepare --compose-file example/crs-mantis/compose.yaml
```

### 3. Run in Source-Only Auditing Mode

Because `crs-mantis` is of type `auditing`, it can run without `--target-harness` directly against the target source tree:

```bash
uv run oss-crs run \
  --compose-file example/crs-mantis/compose.yaml \
  --target-source /path/to/target/source
```

Alternatively, it can also run alongside other CRSs during standard benchmark campaigns.

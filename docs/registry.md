# CRS Registry

The **CRS Registry** is a curated catalog of certified open-source Cyber Reasoning Systems that are compatible with the OSS-CRS framework. Each registered CRS has been verified to follow the [CRS Development Guide](crs-development-guide.md) and can be used out of the box with `oss-crs`.

## Registry Structure

Each CRS in the registry is defined by a YAML file at `registry/<crs-name>.yaml`. The manifest describes the CRS metadata and where to fetch its source:

```yaml
name: <crs-name>
type:
  - bug-finding              # and/or bug-fixing
source:
  url: <git-repository-url>
  ref: <branch-or-tag>
```

| Field | Description |
|---|---|
| `name` | Unique identifier for the CRS |
| `type` | List of CRS capabilities — `bug-finding`, `bug-fixing`, or both |
| `source.url` | Git repository URL containing the CRS implementation |
| `source.ref` | Git branch or tag to use |

### Registry as Source of Truth

When a CRS entry in a compose file omits `source`, `oss-crs` automatically resolves it from the registry. This means registered CRSs can be used with minimal configuration:

```yaml
crs-libfuzzer:
  cpuset: "2-7"
  memory: "16G"
```

The `source` field in compose is only needed to **override** the registry (e.g., for local development with `local_path`).

## Available CRSs

### Currently Registered

| CRS | Type | Description |
|---|---|---|
| **crs-libfuzzer** | bug-finding | A lightweight CRS that runs libFuzzer on the target. Good starting point and baseline for evaluation. |
| **atlantis-multilang-wo-concolic** | bug-finding | A multi-language LLM-powered CRS that generates and refines fuzz harnesses without concolic execution. |
| **crs-claude-code** | bug-fixing | An LLM-powered CRS that uses Claude Code to generate patches for vulnerabilities. Language-agnostic via a builder sidecar. |
| **crs-codex** | bug-fixing | An LLM-powered CRS that uses Codex CLI to analyze crashes, edit code, and validate patches through a builder sidecar. |
| **crs-copilot-cli** | bug-fixing | An LLM-powered CRS that uses GitHub Copilot CLI to generate and validate patches through a builder sidecar. |
| **crs-gemini-cli** | bug-fixing | An LLM-powered CRS that uses Gemini CLI to generate and validate patches through a builder sidecar. |
| **crs-multi-retrieval** | bug-fixing | A two-step CRS that first analyzes crashes to gather code context, then iteratively generates and refines patches using multiple retrieval mechanisms. |
| **crs-prism** | bug-fixing | A cyclic CRS that rotates between analysis, patching, and evaluation agents to progressively converge on a validated fix. |
| **crs-vincent** | bug-fixing | A three-stage CRS that combines root-cause analysis, project-specific property analysis, and patch generation to target semantic correctness. |

### Planned

The following CRSs are planned for inclusion in the registry:

| CRS | Status |
|---|---|
| 42-patch-agent | Planned |
| atlantis-c-bullseye | Planned |
| atlantis-c-deepgen | Planned |
| atlantis-c-libafl | Planned |
| crs-claude-code | Registered |
| crs-codex | Registered |
| crs-copilot-cli | Registered |
| crs-gemini-cli | Registered |
| crs-multi-retrieval | Registered |
| crs-prism | Registered |
| crs-vincent | Registered |
| atlantis-java-atljazzer | Planned |
| atlantis-java-main | Planned |
| buttercup-patcher | Planned |
| swe-agent | Planned |

## Using a Registered CRS

To use a CRS from the registry, reference it in your [compose file](config/crs-compose.md) and run:

```bash
# Prepare the CRS
uv run oss-crs prepare --compose-file <compose-file>

# Build the target
uv run oss-crs build-target --compose-file <compose-file> --fuzz-proj-path <fuzz-proj-path>

# Run the CRS
uv run oss-crs run --compose-file <compose-file> --fuzz-proj-path <fuzz-proj-path> --target-harness <harness>
```

See the [Quick Start](../README.md#quick-start) for a complete walkthrough.

## Adding a CRS to the Registry

To register a new CRS:

1. Ensure your CRS follows the [CRS Development Guide](crs-development-guide.md).
2. Create `registry/<crs-name>.yaml` with the required fields.
3. Submit a pull request for review.

See [CONTRIBUTING.md](../CONTRIBUTING.md) for contribution guidelines.

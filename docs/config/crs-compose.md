# CRS Compose Configuration

This document describes the `crs-compose.yaml` configuration file format, used by the `oss-crs` CLI to define and orchestrate multiple CRS entries.

## Configuration File Format

The configuration file is written in YAML format and consists of the following sections:

1. `run_env` - The runtime environment
2. `docker_registry` - Docker registry URL
3. `oss_crs_infra` - Infrastructure resource configuration
4. `llm_config` - LLM configuration (optional; if omitted/null, OSS-CRS does not manage LiteLLM)
5. CRS entries - One or more named CRS configurations

## Schema Overview

```yaml
run_env: <local|azure>
docker_registry: <registry-url>
oss_crs_infra:
  cpuset: <cpu-set>
  memory: <memory-limit>
  llm_budget: <optional-integer>
llm_config:                    # optional
  litellm:
    mode: <internal|external>
    model_check: <true|false>  # optional, default: true
    internal:
      config_path: <path-to-litellm-config>   # optional; default config used if omitted
    external:
      url: <external-litellm-url>             # oneof(url, url_env)
      url_env: <host-env-var-name>
      key: <external-litellm-api-key>         # oneof(key, key_env)
      key_env: <host-env-var-name>
<crs-name>:
  cpuset: <cpu-set>
  memory: <memory-limit>
  llm_budget: <optional-integer>
  additional_env:              # optional extra env vars
    <KEY>: <value>
  source:                      # optional — resolved from registry if omitted
    url: <git-url>           # Either url+ref OR local_path required
    ref: <git-ref>
    local_path: <path>       # Cannot be combined with url/ref
```

## Configuration Fields

### `llm_config` (optional)

Controls LiteLLM integration mode.

- `null` / omitted:
  - No OSS-CRS-managed LiteLLM validation or sidecars.
- `litellm.mode=internal`:
  - Internal LiteLLM mode (OSS-CRS starts LiteLLM/postgres/key-gen services).
  - `internal.config_path` is optional; if omitted, OSS-CRS uses the default bundled LiteLLM config.
  - In this mode, OSS-CRS always generates per-CRS LiteLLM keys. `required_llms` only controls model-availability validation.
- `litellm.mode=external`:
  - External LiteLLM mode (OSS-CRS injects external URL/key into CRS containers, no internal LiteLLM sidecars).
  - Must set exactly one of `url` or `url_env`.
  - Must set exactly one of `key` or `key_env`.
- `model_check`:
  - When `true` (default), validates `/models` reachability and `required_llms` availability.
  - When `false`, skips `/models` model availability check.

**Examples:**

```yaml
# disabled
llm_config: null
```

```yaml
# internal (default config path)
llm_config:
  litellm:
    mode: internal
```

```yaml
# internal (explicit config path)
llm_config:
  litellm:
    mode: internal
    internal:
      config_path: /home/crs-compose/litellm-config.yaml
```

```yaml
# external (env-based endpoint/key)
llm_config:
  litellm:
    mode: external
    external:
      url_env: LITELLM_URL
      key_env: LITELLM_API_KEY
```

---

### `run_env` (required)

Specifies the runtime environment for the CRS Compose setup.

| Value   | Description                          |
|---------|--------------------------------------|
| `local` | Run CRS locally on your machine      |
| `azure` | Run CRS on Azure cloud infrastructure |

**Example:**
```yaml
run_env: local
```

---

### `docker_registry` (required)

Specifies the Docker registry URL to pull/push CRS images.

| Type   | Description                                      |
|--------|--------------------------------------------------|
| string | URL of the Docker registry (e.g., container registry endpoint) |

**Example:**
```yaml
docker_registry: ghcr.io/my-org
```

---

### `oss_crs_infra` (required)

Defines the resource configuration for the OSS-CRS infrastructure components.

| Field       | Type    | Required | Description                                      |
|-------------|---------|----------|--------------------------------------------------|
| `cpuset`    | string  | Yes      | CPU cores to allocate (see [CPU Set Format](#cpu-set-format)) |
| `memory`    | string  | Yes      | Memory limit (see [Memory Format](#memory-format)) |
| `llm_budget`| integer | No       | LLM API budget limit (must be > 0 if specified)  |

**Example:**
```yaml
oss_crs_infra:
  cpuset: "0-3"
  memory: "8G"
```

---

### CRS Entries

Each CRS entry is defined as a top-level key (other than `run_env` and `oss_crs_infra`). The key becomes the name of the CRS entry.

#### CRS Entry Fields

| Field       | Type    | Required | Description                                      |
|-------------|---------|----------|--------------------------------------------------|
| `cpuset`    | string  | Yes      | CPU cores to allocate (see [CPU Set Format](#cpu-set-format)) |
| `memory`    | string  | Yes      | Memory limit (see [Memory Format](#memory-format)) |
| `llm_budget`| integer | No       | LLM API budget limit (must be > 0 if specified)  |
| `additional_env` | dict[string, string] | No | Additional environment variables passed to all modules in this CRS entry |
| `source`    | object  | No       | Source configuration (see [Source Configuration](#source-configuration)). If omitted, resolved from the [CRS registry](../registry.md). |

#### Source Configuration

The `source` field specifies where to find the CRS configuration. If omitted entirely, the source is resolved from the [CRS registry](../registry.md) using the entry name (e.g., `crs-libfuzzer` looks up `registry/crs-libfuzzer.yaml`).

When provided, you must specify either `url` + `ref` OR `local_path`, but not both.

| Field        | Type   | Required | Description                                           |
|--------------|--------|----------|-------------------------------------------------------|
| `url`        | string | No*      | Git repository URL (HTTP URL format)                  |
| `ref`        | string | No*      | Git reference (branch, tag, or commit SHA). Required when `url` is provided |
| `local_path` | string | No*      | Local filesystem path to the CRS. Cannot be combined with `url` or `ref` |

\* Either `url` + `ref` OR `local_path` must be provided when `source` is specified.

**Example with registered CRS (no source needed):**
```yaml
crs-libfuzzer:
  cpuset: "2-7"
  memory: "16G"
```

**Example with Git URL:**
```yaml
my-crs:
  cpuset: "4-7"
  memory: "16G"
  llm_budget: 1000
  source:
    url: https://github.com/example/my-crs.git
    ref: main
    # This will load a CRS defined at @my-crs/oss-crs/crs.yaml
```

**Example with Local Path:**
```yaml
my-local-crs:
  cpuset: "0,2,4,6"
  memory: "8GB"
  source:
    local_path: /home/user/my-crs
    # This will load a CRS defined at /home/user/my-crs/oss-crs/crs.yaml
```

---

## Format Specifications

### CPU Set Format

The `cpuset` field accepts the following formats:

| Format          | Example       | Description                        |
|-----------------|---------------|------------------------------------|
| Range           | `"0-3"`       | CPUs 0, 1, 2, and 3                |
| List            | `"0,1,2,3"`   | CPUs 0, 1, 2, and 3                |
| Mixed           | `"0-3,5,7-9"` | CPUs 0-3, 5, and 7-9               |

### Memory Format

The `memory` field accepts memory values with the following units:

| Unit | Aliases | Example   |
|------|---------|-----------|
| Bytes | `B`    | `"1024B"` |
| Kilobytes | `K`, `KB` | `"1024K"`, `"1024KB"` |
| Megabytes | `M`, `MB` | `"1024M"`, `"1024MB"` |
| Gigabytes | `G`, `GB` | `"8G"`, `"8GB"` |
| Terabytes | `T`, `TB` | `"1T"`, `"1TB"` |

Decimal values are also supported (e.g., `"1.5G"`).

---

## Complete Example

```yaml
run_env: local
docker_registry: ghcr.io/my-org

oss_crs_infra:
  cpuset: "0-1"
  memory: "4G"

llm_config:
  litellm:
    mode: internal
    internal:
      config_path: /home/crs-compose/litellm-config.yaml

# Registered CRS — source resolved from registry/crs-libfuzzer.yaml
crs-libfuzzer:
  cpuset: "2-3"
  memory: "8G"

# CRS with explicit git source
atlantis-crs:
  cpuset: "4-5"
  memory: "16G"
  llm_budget: 5000
  source:
    url: https://github.com/example/crs.git
    ref: v1.2.0

# CRS with local path (for development)
local-path-crs:
  cpuset: "6-7"
  memory: "8GB"
  additional_env:
    DEBUG: "1"
  source:
    local_path: /home/user/my-crs
```

---

## Validation Rules

The configuration is validated using Pydantic with the following rules:

1. **Source Validation:**
   - If `source` is omitted, the CRS must exist in the registry (`registry/<crs-name>.yaml`)
   - When `source` is provided, either `url` or `local_path` must be specified (but not both)
   - When `url` is provided, `ref` is required
   - `local_path` cannot be combined with `url` or `ref`

2. **CPU Set Validation:**
   - Must match the pattern for valid CPU set specifications
   - Invalid examples: `"abc"`, `"0--3"`, `"-1"`

3. **Memory Validation:**
   - Must include a valid unit suffix (B, K, KB, M, MB, G, GB, T, TB)
   - Invalid examples: `"8"`, `"8 Gigabytes"`, `"8g b"`

4. **LLM Budget Validation:**
   - If specified, must be a positive integer (> 0)
   - If omitted, OSS-CRS forwards `max_budget: null` to LiteLLM during key generation (no explicit OSS-CRS budget cap).

5. **CRS Entry Name Validation:**
   - CRS entry names (keys) must be lowercase only
   - Invalid examples: `"My-CRS"`, `"ATLANTIS"`, `"myLocalCRS"`
   - Valid examples: `"my-crs"`, `"atlantis"`, `"my-local-crs"`

---

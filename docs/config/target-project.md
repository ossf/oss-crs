# Target Project Configuration

A target project defines the software a CRS will fuzz. It must follow the [OSS-Fuzz project format](https://google.github.io/oss-fuzz/getting-started/new-project-guide/).

## File Structure

```
<project-name>/
├── project.yaml    # Project metadata
├── Dockerfile      # Build environment
└── build.sh        # Build script
```

For `Dockerfile` and `build.sh` details, see the [OSS-Fuzz guide](https://google.github.io/oss-fuzz/getting-started/new-project-guide/).

## project.yaml

OSS-CRS parses `project.yaml` using the `TargetConfig` model:

| Field             | Required | Default                                          | Description                                         |
|-------------------|----------|--------------------------------------------------|-----------------------------------------------------|
| `language`        | Yes      | —                                                | `c`, `c++`, `go`, `rust`, `python`, `jvm`, `swift`, `javascript`, `lua` |
| `main_repo`       | Yes      | —                                                | Source repository URL                               |
| `sanitizers`      | No       | `["address", "undefined"]`                       | `address`, `memory`, `undefined`                    |
| `architectures`   | No       | `["x86_64"]`                                     | `x86_64`, `i386`                                    |
| `fuzzing_engines` | No       | `["libfuzzer", "afl", "honggfuzz", "centipede"]` | `libfuzzer`, `afl`, `honggfuzz`, `centipede`        |

Full spec: [OSS-Fuzz project.yaml reference](https://google.github.io/oss-fuzz/getting-started/new-project-guide/#projectyaml)

## Usage

```bash
# Build target
uv run oss-crs build-target \
    --compose-file ./crs-compose.yaml \
    --fuzz-proj-path /path/to/<project-name>

# Run CRS
uv run oss-crs run \
    --compose-file ./crs-compose.yaml \
    --fuzz-proj-path /path/to/<project-name> \
    --target-harness <harness_name>
```

| Argument              | Required | Description                                                                    |
|-----------------------|----------|--------------------------------------------------------------------------------|
| `--fuzz-proj-path` (`--target-path`, `--target-proj-path`, deprecated aliases) | Yes | Path to the OSS-Fuzz target project directory (`Dockerfile`, `project.yaml`, `build.sh`). |
| `--target-source-path` | No | Optional local source override path. If set, source is synchronized with `rsync -a --delete` into the effective Dockerfile `WORKDIR`. |
| `--target-harness`    | Yes (run)| Fuzz target harness binary name.                                               |

Existing [OSS-Fuzz projects](https://github.com/google/oss-fuzz/tree/master/projects) can be used directly as `--fuzz-proj-path` without modification.

### Source Path Semantics

- `OSS_CRS_PROJ_PATH` points to the copied target project directory.
- `OSS_CRS_REPO_PATH` points to the effective final Dockerfile `WORKDIR` inside
  the target image.
- `WORKDIR` resolution follows Dockerfile semantics, with fallback chain:
  final `WORKDIR` -> `$SRC` -> `/src` (when `SRC` is not provided).
- When `--target-source-path` is set, the override source is synchronized into
  `OSS_CRS_REPO_PATH` via `rsync -a --delete`.

### `--target-source-path` Sync Flow

`--target-source-path` is not bind-mounted directly to `OSS_CRS_REPO_PATH`.
Instead, during image build:

1. The host source path is passed as a Docker build context (`repo_path=...`).
2. It is copied into a temporary image path (`/OSS_CRS_REPO_OVERRIDE`).
3. `rsync -a --delete /OSS_CRS_REPO_OVERRIDE/ ./` runs from the effective
   `WORKDIR`.
4. `OSS_CRS_REPO_PATH` points to that effective `WORKDIR` path.

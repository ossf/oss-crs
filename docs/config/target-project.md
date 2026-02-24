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
    --target-path /path/to/<project-name>

# Run CRS
uv run oss-crs run \
    --compose-file ./crs-compose.yaml \
    --target-path /path/to/<project-name> \
    --target-harness <harness_name>
```

| Argument              | Required | Description                                                                    |
|-----------------------|----------|--------------------------------------------------------------------------------|
| `--target-path` (`--target-proj-path`) | Yes | Path to the OSS-Fuzz target project directory (`Dockerfile`, `project.yaml`, `build.sh`). |
| `--target-source-path` (`--target-repo-path`) | No | Optional local source override path. If set, source is overlaid to the effective Dockerfile `WORKDIR`. |
| `--target-harness`    | Yes (run)| Fuzz target harness binary name.                                               |

Existing [OSS-Fuzz projects](https://github.com/google/oss-fuzz/tree/master/projects) can be used directly as `--target-path` (or `--target-proj-path`) without modification.

### Source Path Semantics

- `OSS_CRS_PROJ_PATH` points to the copied target project directory.
- `OSS_CRS_REPO_PATH` points to the effective final Dockerfile `WORKDIR`.
- When `--target-source-path` is set, the override source is synchronized into
  `OSS_CRS_REPO_PATH`.

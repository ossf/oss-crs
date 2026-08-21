# Target Project Configuration

A target project defines the software a CRS will fuzz. It must follow the [OSS-Fuzz project format](https://google.github.io/oss-fuzz/getting-started/new-project-guide/).

## File Structure

```
<project-name>/
├── Dockerfile      # Build environment
├── build.sh        # Build script
└── project.yaml    # Optional OSS-Fuzz metadata
```

For `Dockerfile` and `build.sh` details, see the [OSS-Fuzz guide](https://google.github.io/oss-fuzz/getting-started/new-project-guide/).

## project.yaml (Optional)

`project.yaml` is optional. If present and parseable, `oss-crs` uses it as
fallback metadata for target defaults (`FUZZING_LANGUAGE`, `SANITIZER`,
`ARCHITECTURE`, `FUZZING_ENGINE`).

Effective precedence is:
1. `additional_env` overrides (CRS entry and module/build-step scopes)
2. `project.yaml` values (when available)
3. framework defaults (`address`, `libfuzzer`, `x86_64`, `c`)

Note:
- Effective build/run sanitizer selection (`{work_dir}/{sanitizer}/...` partition)
  uses `SANITIZER` from CRS-entry `additional_env` first, then `project.yaml`,
  then `address`.

Full spec (for OSS-Fuzz compatibility/reference): [OSS-Fuzz project.yaml reference](https://google.github.io/oss-fuzz/getting-started/new-project-guide/#projectyaml)

## Usage

### Harnessed Run

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

### Source-Only Run

```bash
uv run oss-crs run \
    --compose-file ./crs-compose.yaml \
    --target-source-path /path/to/source
```

Source-only runs omit `--fuzz-proj-path` entirely. The source path is
directly bind-mounted to `OSS_CRS_TARGET_SOURCE`. There is no build step,
no `OSS_CRS_FUZZ_PROJ` mount, and `SANITIZER`, `ARCHITECTURE`,
`FUZZING_LANGUAGE`, etc. not injected into source-only containers.

## Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `--fuzz-proj-path` (`--target-path`, `--target-proj-path`, deprecated aliases) | Yes for harnessed/harness-gen runs; omitted for source-only runs | Path to the OSS-Fuzz target project directory (`Dockerfile`, `build.sh`; `project.yaml` optional). |
| `--target-source-path` | Required for source-only runs; optional local source override otherwise | Path to the source tree. For source-only runs, this is directly bind-mounted. For harnessed runs, source is synchronized with `rsync -a --delete` into the effective Dockerfile `WORKDIR`. |
| `--target-harness` | Yes (run) | Fuzz target harness binary name. |

At least one of `--fuzz-proj-path` or `--target-source-path` is required.

Existing [OSS-Fuzz projects](https://github.com/google/oss-fuzz/tree/master/projects) can be used directly as `--fuzz-proj-path` without modification.

## Source Path Semantics

### Harnessed Runs with Source Override

`--target-source-path` is not bind-mounted directly to `OSS_CRS_REPO_PATH`.
Instead, during image build:

1. The host source path is passed as a Docker build context (`repo_path=...`).
2. It is copied into a temporary image path (`/OSS_CRS_REPO_OVERRIDE`).
3. `rsync -a --delete /OSS_CRS_REPO_OVERRIDE/ ./` runs from the effective
   `WORKDIR`.
4. `OSS_CRS_REPO_PATH` points to that effective `WORKDIR` path.

### Source-Only Runs

For source-only runs, `--target-source-path` is directly bind-mounted to
`OSS_CRS_TARGET_SOURCE`. There is no image build, no `OSS_CRS_FUZZ_PROJ`
mount, and environment variables like `SANITIZER`, `ARCHITECTURE`, `FUZZING_LANGUAGE`,
etc. are not injected.

### Common Semantics

- `OSS_CRS_REPO_PATH` points to the effective final Dockerfile `WORKDIR`
  inside the target image.
- `WORKDIR` resolution follows Dockerfile semantics, with fallback chain:
  final `WORKDIR` -> `$SRC` -> `/src` (when `SRC` is not provided).
- `libCRS download-source repo` prefers the live runtime source workspace
  rooted at `$SRC`/`/src`. When `OSS_CRS_REPO_PATH` is inside that workspace,
  the downloaded tree preserves the workspace layout rather than flattening a
  nested `WORKDIR`.

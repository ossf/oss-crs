# Parallel Builds and Runs

`oss-crs` supports running multiple builds and runs in parallel through build and run identifiers.

## Build ID

The `--build-id` flag isolates build artifacts, allowing parallel builds of different target versions or configurations.

**Default behavior:**
- `build-target`: Generates a new timestamp-based ID (e.g., `1739819274ab`), creating a fresh build each time
- `run`: Uses the latest existing build, or generates a new one if none exists

To reuse an existing build (avoid rebuilding), explicitly specify `--build-id`:

```bash
# Build fresh artifacts
uv run oss-crs build-target \
  --compose-file ./crs-compose.yaml \
  --fuzz-proj-path ~/oss-fuzz/projects/libxml2

# Pin to a specific build ID to reuse across invocations
uv run oss-crs build-target \
  --compose-file ./crs-compose.yaml \
  --fuzz-proj-path ~/oss-fuzz/projects/libxml2 \
  --build-id my-pinned-build
```

Build artifacts are stored at:
```
{work_dir}/{sanitizer}/builds/{build-id}/crs/{crs-name}/{target}/BUILD_OUT_DIR/
```

Multiple runs can share the same build by specifying the same `--build-id`.

## Target Build Options

`oss-crs` uses framework defaults for target build options:

- `SANITIZER=address`
- `FUZZING_ENGINE=libfuzzer`
- `ARCHITECTURE=x86_64`
- `FUZZING_LANGUAGE=c`

If `project.yaml` exists and is parseable, its values are used as fallback target defaults. Users can override through `additional_env` (CRS entry level and CRS module/build-step level), or by using the `--sanitizer` CLI flag on `build-target`, `run`, and `artifacts` commands. The CLI flag takes highest precedence when provided.

Effective precedence:
- CLI `--sanitizer` flag (highest priority when provided)
- compose CRS-entry `additional_env` (user)
- CRS module/build-step `additional_env` (`crs.yaml`)
- `project.yaml` fallback values
- framework defaults (`address`, `libfuzzer`, `x86_64`, `c`)

Notes:
- `additional_env` keys must follow env-var format: `[A-Za-z_][A-Za-z0-9_]*`.
- `OSS_CRS_*` keys are reserved for framework-owned values. If provided in
  `additional_env`, `oss-crs` warns (`ENV001`/`ENV002`).
- Framework-owned `OSS_CRS_*` keys in the active phase override user values.
  Unknown `OSS_CRS_*` keys are warned and may pass through.
- If multiple CRS entries specify conflicting `SANITIZER` values and no explicit
  sanitizer is provided, `build-target`/`run` fails fast with a conflict error.

Only `sanitizer` affects artifact directory partitioning.

```bash
uv run oss-crs build-target \
  --compose-file ./crs-compose.yaml \
  --fuzz-proj-path ~/oss-fuzz/projects/libxml2
```

All artifacts are stored under `{work_dir}/{sanitizer}/...`.

## Run ID

The `--run-id` flag isolates run artifacts (seeds, PoVs, shared state), allowing multiple experiments against the same build.

```bash
uv run oss-crs run \
  --compose-file ./crs-compose.yaml \
  --fuzz-proj-path ~/oss-fuzz/projects/libxml2 \
  --target-harness xml \
  --build-id my-pinned-build \
  --run-id experiment-1 \
  --timeout 3600
```

| Command     | Default                                                        |
|-------------|----------------------------------------------------------------|
| `run`       | Auto-generated timestamp + random bytes (e.g., `1739819274ab`) |
| `artifacts` | Interactive selection in reverse chronological order           |

Run artifacts are stored at:
```
{work_dir}/{sanitizer}/runs/{run-id}/BUILD_ID                              # records which build was used
{work_dir}/{sanitizer}/runs/{run-id}/crs/{crs-name}/{target}/SUBMIT_DIR/{harness}/povs/
{work_dir}/{sanitizer}/runs/{run-id}/crs/{crs-name}/{target}/SUBMIT_DIR/{harness}/seeds/
{work_dir}/{sanitizer}/runs/{run-id}/crs/{crs-name}/{target}/SUBMIT_DIR/{harness}/bug-candidates/
{work_dir}/{sanitizer}/runs/{run-id}/crs/{crs-name}/{target}/SUBMIT_DIR/{harness}/patches/
{work_dir}/{sanitizer}/runs/{run-id}/crs/{crs-name}/{target}/SHARED_DIR/{harness}/
{work_dir}/{sanitizer}/runs/{run-id}/EXCHANGE_DIR/{target}/{harness}/      # shared across all CRSs
{work_dir}/{sanitizer}/runs/{run-id}/logs/{target}/{harness}/              # compose/service logs
```

### Cleanup Semantics

- At run start, `oss-crs` cleans run-scoped directories for the selected
  `run-id` (including `EXCHANGE_DIR` and per-CRS `SHARED_DIR`) before services
  start.
- At run cleanup, `docker compose down` is always attempted, and project-scoped
  compose image cleanup is attempted best-effort (warnings are emitted on
  failures).

## Artifacts Command

The `artifacts` command uses `--fuzz-proj-path`, `--target-source-path`, `--target-harness`, `--run-id`, `--build-id`, and `--sanitizer` to compute artifact paths from the compose file.

```bash
oss-crs artifacts \
  --compose-file ./crs-compose.yaml \
  --fuzz-proj-path ~/oss-fuzz/projects/libxml2 \
  --target-harness xml \
  --run-id my-new-run
```

**Default behavior:**
- `--run-id`: Interactive selection if omitted
- `--build-id`: Reads from the run's `BUILD_ID` file, falls back to latest build by sanitizer (timestamp in ID if present, otherwise build directory mtime)
- `--sanitizer`: If omitted, resolves from compose/project defaults (`SANITIZER` in CRS-entry `additional_env` -> `project.yaml` -> `address`)

**Resolution behavior:**
- `--run-id` supports pre-run deterministic resolution (if the run does not exist yet, paths are still computed)
- `--build-id` is validated against existing builds when explicitly provided

### Interactive Run Selection

If `--run-id` is omitted, an interactive prompt lists available runs sorted by timestamp:

```
? Select run-id:
> 2025-02-17 16:01:57  (1739819274ab)
  2025-02-17 15:58:30  (test-explicit)
  2025-02-17 15:55:00  (experiment-1)
```

### Output Format

```json
{
  "build_id": "1739819200cd",
  "run_id": "1739819274ab",
  "sanitizer": "address",
  "exchange_dir": {
    "base": "/path/to/address/runs/1739819274ab/EXCHANGE_DIR/target/xml",
    "pov": "/path/to/address/runs/1739819274ab/EXCHANGE_DIR/target/xml/povs",
    "seed": "/path/to/address/runs/1739819274ab/EXCHANGE_DIR/target/xml/seeds",
    "bug_candidate": "/path/to/address/runs/1739819274ab/EXCHANGE_DIR/target/xml/bug-candidates",
    "patch": "/path/to/address/runs/1739819274ab/EXCHANGE_DIR/target/xml/patches",
    "diff": "/path/to/address/runs/1739819274ab/EXCHANGE_DIR/target/xml/diffs"
  },
  "run_logs": {
    "base": "/path/to/address/runs/1739819274ab/logs/target/xml",
    "compose_stdout_log": "/path/to/address/runs/1739819274ab/logs/target/xml/docker-compose.stdout.log",
    "compose_stderr_log": "/path/to/address/runs/1739819274ab/logs/target/xml/docker-compose.stderr.log",
    "service_logs": "/path/to/address/runs/1739819274ab/logs/target/xml/services"
  },
  "crs": {
    "crs-libfuzzer": {
      "build": "/path/to/address/builds/1739819200cd/crs/crs-libfuzzer/target/BUILD_OUT_DIR",
      "submit_dir": "/path/to/address/runs/1739819274ab/crs/crs-libfuzzer/target/SUBMIT_DIR/xml",
      "pov": "/path/to/address/runs/1739819274ab/crs/crs-libfuzzer/target/SUBMIT_DIR/xml/povs",
      "seed": "/path/to/address/runs/1739819274ab/crs/crs-libfuzzer/target/SUBMIT_DIR/xml/seeds",
      "bug_candidate": "/path/to/address/runs/1739819274ab/crs/crs-libfuzzer/target/SUBMIT_DIR/xml/bug-candidates",
      "patch": "/path/to/address/runs/1739819274ab/crs/crs-libfuzzer/target/SUBMIT_DIR/xml/patches",
      "fetch": "/path/to/address/runs/1739819274ab/EXCHANGE_DIR/target/xml",
      "shared": "/path/to/address/runs/1739819274ab/crs/crs-libfuzzer/target/SHARED_DIR/xml"
    }
  }
}
```

- All computed paths are returned regardless of whether they exist on disk
- `build_id` is `null` if no build is associated with the run
- If `--target-harness` is not provided, only `build` is returned

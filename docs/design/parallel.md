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
  --target-proj-path ~/oss-fuzz/projects/libxml2

# Pin to a specific build ID to reuse across invocations
uv run oss-crs build-target \
  --compose-file ./crs-compose.yaml \
  --target-proj-path ~/oss-fuzz/projects/libxml2 \
  --build-id my-pinned-build
```

Build artifacts are stored at:
```
{work_dir}/{sanitizer}/builds/{build-id}/crs/{crs-name}/{target}/BUILD_OUT_DIR/
```

Multiple runs can share the same build by specifying the same `--build-id`.

## Sanitizer

The `--sanitizer` flag (default: from target config, usually `address`) determines which sanitizer configuration to use. It affects the directory structure, isolating artifacts by sanitizer type.

```bash
uv run oss-crs build-target \
  --compose-file ./crs-compose.yaml \
  --target-proj-path ~/oss-fuzz/projects/libxml2 \
  --sanitizer undefined
```

All artifacts are stored under `{work_dir}/{sanitizer}/...`.

## Run ID

The `--run-id` flag isolates run artifacts (seeds, PoVs, shared state), allowing multiple experiments against the same build.

```bash
uv run oss-crs run \
  --compose-file ./crs-compose.yaml \
  --target-proj-path ~/oss-fuzz/projects/libxml2 \
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
{work_dir}/{sanitizer}/runs/{run-id}/crs/{crs-name}/{target}/SHARED_DIR/{harness}/
{work_dir}/{sanitizer}/runs/{run-id}/EXCHANGE_DIR/{target}/{harness}/      # shared across all CRSs
```

## Artifacts Command

Query artifact directories for a specific build and run.

```bash
uv run oss-crs artifacts \
  --compose-file ./crs-compose.yaml \
  --target-proj-path ~/oss-fuzz/projects/libxml2 \
  --target-harness xml \
  --run-id 1739819274ab
```

**Default behavior:**
- `--run-id`: Interactive selection if omitted
- `--build-id`: Reads from the run's `BUILD_ID` file, falls back to latest build
- `--sanitizer`: Defaults to `address`

### Interactive Run Selection

If `--run-id` is omitted, an interactive prompt lists available runs sorted by timestamp:

```
? Select run-id:
❯ 2025-02-17 16:01:57  (1739819274ab)
  2025-02-17 15:58:30  (test-explicit)
  2025-02-17 15:55:00  (experiment-1)
```

### Output Format

```json
{
  "build_id": "1739819200cd",
  "run_id": "1739819274ab",
  "crs": {
    "crs-libfuzzer": {
      "build": "/path/to/address/builds/1739819200cd/crs/crs-libfuzzer/target/BUILD_OUT_DIR",
      "pov": "/path/to/address/runs/1739819274ab/crs/crs-libfuzzer/target/SUBMIT_DIR/xml/povs",
      "seed": "/path/to/address/runs/1739819274ab/crs/crs-libfuzzer/target/SUBMIT_DIR/xml/seeds",
      "fetch": "/path/to/address/runs/1739819274ab/EXCHANGE_DIR/target/xml",
      "shared": "/path/to/address/runs/1739819274ab/crs/crs-libfuzzer/target/SHARED_DIR/xml"
    }
  }
}
```

Fields are omitted if the directory does not exist. If `--target-harness` is not provided, only `build` is returned.

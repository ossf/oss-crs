# Changelog

All notable changes to this project are documented in this file.
This format is based on [Common Changelog](https://common-changelog.org/) (a
stricter subset of Keep a Changelog).

## [Unreleased]

### Added
- `--incremental-build` flag for `oss-crs build-target` and `oss-crs run` — creates Docker snapshots of compiled builder images for faster rebuilds across runs
- Framework-injected builder and runner sidecars during run phase — CRS developers no longer declare them in `crs.yaml`
- `libCRS apply-patch-test` command — applies a patch and runs the project's `test.sh` in a fresh ephemeral container
- `--early-exit` flag to `oss-crs run` to stop on the first discovered artifact (POV or patch)
- GitHub Actions CI pipeline with lint (ruff check), format check (ruff format), type check (pyright), unit tests, and parallel C/Java smoke tests
- atlantis-java-main to registry/ and example/
- atlantis-c-deepgen to registry/ and example/
- roboduck to registry/ and example/
- fuzzing-brain to registry/ and example/ (bug-finding, C/C++, multi-provider LLM)
- buttercup-seed-gen to registry/ and example/

### Changed
- Builder sidecar redesigned: framework-injected ephemeral containers replace CRS-declared long-running builders. Rebuilds launch a fresh container per patch from the preserved builder image.
- `libCRS apply-patch-build`: `--builder` no longer required (framework injects `BUILDER_MODULE`), `--builder-name` auto-detected. Response fields renamed: `retcode`, `rebuild_id`, `stdout.log`/`stderr.log`.
- `libCRS run-pov`: `--build-id` renamed to `--rebuild-id`, `--builder` no longer required.
- `libCRS apply-patch-test` replaces `run-test`: takes a patch file, applies it, and runs `test.sh` in a fresh container.
- Clarified that target env `repo_path` is the effective in-container source
  path (Dockerfile final `WORKDIR`) used for `OSS_CRS_REPO_PATH`, not a host
  path override.
- When `--target-source-path` is provided, source override now uses
  `rsync -a --delete` into the effective `WORKDIR` (strict replacement of that
  tree).
- `OSS_CRS_REPO_PATH` resolution is documented as: final `WORKDIR` -> `$SRC` ->
  `/src` fallback chain.
- Target build-option resolution now uses precedence:
  CLI `--sanitizer` flag -> `additional_env` override (SANITIZER at CRS-entry scope) 
  -> `project.yaml` fallback (uses address if provided, else first) 
  -> framework defaults.
- `artifacts --sanitizer` is now optional; when omitted, sanitizer is resolved
  using the same contract (compose/project/default) used by build/run flows.

### Deprecated
- Deprecated CLI aliases:
  - `--target-path` in favor of `--fuzz-proj-path`
  - `--target-proj-path` in favor of `--fuzz-proj-path`
- Deprecated aliases now emit runtime warnings and are planned for removal in a
  future minor release.

### Removed
- `crs.yaml`: `snapshot` field from `target_build_phase`, `run_snapshot` field from `crs_run_phase` — snapshot behavior is now operator-controlled via `--incremental-build`
- `libCRS run-test` — replaced by `libCRS apply-patch-test`
- `OSS_CRS_SNAPSHOT_IMAGE` environment variable
- Removed legacy CLI alias `--target-repo-path`; use `--target-source-path`.

### Fixed
- The local run path now passes a `Path` compose-file object consistently into
  `docker_compose_up()`, so helper-sidecar teardown classification applies on
  the main local run path.
- `libCRS download-source repo` now prefers the live runtime source workspace
  (`$SRC` / `/src`) over build-output snapshots and preserves workspace layout
  consistently for nested `WORKDIR` targets.

### Security
- N/A

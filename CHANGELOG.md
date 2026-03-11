# Changelog

All notable changes to this project are documented in this file.
This format is based on [Common Changelog](https://common-changelog.org/) (a
stricter subset of Keep a Changelog).

## [Unreleased]

### Added
- atlantis-java-main to registry/ and example/
- atlantis-c-deepgen to registry/ and example/

### Changed
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

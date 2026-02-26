# Changelog

All notable changes to this project are documented in this file.
This format is based on [Common Changelog](https://common-changelog.org/) (a
stricter subset of Keep a Changelog).

## [Unreleased]

### Added
- N/A

### Changed
- Clarified that target env `repo_path` is the effective in-container source
  path (Dockerfile final `WORKDIR`) used for `OSS_CRS_REPO_PATH`, not a host
  path override.
- When `--target-source-path` is provided, source override now uses
  `rsync -a --delete` into the effective `WORKDIR` (strict replacement of that
  tree).
- `OSS_CRS_REPO_PATH` resolution is documented as: final `WORKDIR` -> `$SRC` ->
  `/src` fallback chain.

### Deprecated
- Deprecated CLI aliases:
  - `--target-path` in favor of `--fuzz-proj-path`
  - `--target-proj-path` in favor of `--fuzz-proj-path`
- Deprecated aliases now emit runtime warnings and are planned for removal in a
  future minor release.

### Removed
- Removed legacy CLI alias `--target-repo-path`; use `--target-source-path`.

### Fixed
- N/A

### Security
- N/A

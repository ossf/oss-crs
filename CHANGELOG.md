# Changelog

All notable changes to this project are documented in this file.
This format is based on [Common Changelog](https://common-changelog.org/) (a
stricter subset of Keep a Changelog).

## [Unreleased]

### Added
- `oss-crs archive` command — packages submitted artifacts (POVs, seeds, patches, bug-candidates) from a run into a `.tar.gz`. When a triage CRS is present, POVs are sourced from its submit dir instead of individual CRS submit dirs. Use `--all` to also include exchange dir, logs, and shared dirs. Supports `--run-id`, `--latest`, and `--sanitizer` for run selection.
- `--latest` flag for `oss-crs artifacts` and `oss-crs archive` — automatically selects the most recent run instead of prompting interactively.
- `oss-crs gen-compose --litellm-proxy KEY_ENV PROVIDERS [BASE_URL_ENV]` — override litellm config env vars to route selected providers through a proxy. Only rewrites entries that use known default provider keys; custom keys (e.g. `VLLM_KEY`) are never touched.
- `oss-crs setup` now includes an interactive LLM proxy configuration phase — asks which providers to route through a proxy, the key/base-URL env var names, and applies the override to all example litellm configs that use default provider keys.
- `LITELLM_PROVIDERS` constant in `llm.py` — canonical registry mapping provider names to model prefixes and default API key env vars (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `XAI_API_KEY`).
- `oss-crs clean` command — removes Docker images and workdir artifacts from previous prepare, build-target, and run phases. Supports phase-specific subcommands (`clean prepare`, `clean build-target`, `clean run`) or cleaning everything at once. Use `--artifacts` to also delete workdir directories, and `-y` to skip the confirmation prompt.
- Website under `site/`
- `bug-finding-triage` and `seed-filter` CRS types — post-processor CRS that read from the main exchange dir and write triaged/filtered results to a separate processed exchange dir, which non-processor CRS mount as `FETCH_DIR`
- `oss-crs-processed-exchange` sidecar — automatically injected when the compose includes a triage or seed-filter CRS; collects post-processor submit dirs into `PROCESSED_EXCHANGE_DIR`
- crs-atlantis-triage, crs-clusterfuzz-triage, and crs-roboduck-triage to registry/ and example/ (bug-finding-triage)
- crs-atlantis-ensemble to registry/ and example/ (seed-filter)
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
- 42-directed and 42-seedgen to registry/ and example/
- `libCRS download-source fuzz-proj <dest>`: copies clean fuzz project
- `libCRS download-source target-source <dest>`: copies clean target source
- Warn user when compose file resource configs exceed machine resources (#49)

### Changed
- Post-run results are now printed outside the Rich UI box so long artifact directory paths are never truncated by panel border wrapping. Directories are only shown when the artifact count is non-zero.
- `oss-crs artifacts` and `oss-crs archive` now ignore unrecognized CLI arguments, allowing run command args to be forwarded directly.
- Example litellm configs now use standard provider API keys (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`) by default instead of `EXTERNAL_LITELLM_API_KEY`/`EXTERNAL_LITELLM_API_BASE`. Use `oss-crs setup` or `gen-compose --litellm-proxy` to configure proxy routing.
- `oss-crs setup` is now a general setup command (LLM configuration + cgroup setup) instead of cgroup-only.
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
- **Breaking:** `libCRS download-source` API replaced — `target`/`repo`
  subcommands removed, use `fuzz-proj`/`target-source` instead. Python API
  `download_source()` now returns `None` instead of `Path`.

### Deprecated
- Deprecated CLI aliases:
  - `--target-path` in favor of `--fuzz-proj-path`
  - `--target-proj-path` in favor of `--fuzz-proj-path`
- Deprecated aliases now emit runtime warnings and are planned for removal in a
  future minor release.

### Removed
- `builder` CRS type — replaced by framework-injected builder sidecars
- `crs.yaml`: `snapshot` field from `target_build_phase`, `run_snapshot` field from `crs_run_phase` — snapshot behavior is now operator-controlled via `--incremental-build`
- `libCRS run-test` — replaced by `libCRS apply-patch-test`
- `OSS_CRS_SNAPSHOT_IMAGE` environment variable
- Removed legacy CLI alias `--target-repo-path`; use `--target-source-path`.
- Removed `libCRS download-source target` and `download-source repo` commands.
- Removed `SourceType.TARGET` and `SourceType.REPO` enum values from libCRS.
- Removed ~140 lines of fallback resolution logic from libCRS
  (`_resolve_repo_source_path`, `_normalize_repo_source_path`,
  `_translate_repo_hint_to_build_output`, `_resolve_downloaded_repo_path`,
  `_relative_repo_hint`).

### Fixed
- Builder and runner sidecar APIs now reject path-like CRS, harness, and
  rebuild identifiers before using them to resolve artifact paths.
- The local run path now passes a `Path` compose-file object consistently into
  `docker_compose_up()`, so helper-sidecar teardown classification applies on
  the main local run path.

### Security
- CRS entry names are now validated at config load time before being used in
  paths, Docker tags, service aliases, and artifact directories.

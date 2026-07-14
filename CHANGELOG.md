# Changelog

All notable changes to this project are documented in this file.
This format is based on [Common Changelog](https://common-changelog.org/) (a
stricter subset of Keep a Changelog).

## [Unreleased]

### Changed

- Run-phase modules now default to `target_dependent: true`, so their images are built once per target during `build-target`. Set `target_dependent: false` for modules that can be built once during `prepare`.
- `--offline` flag for all subcommands: disables git fetch
- `oss-crs build-target` now validates `--bug-candidate`/`--bug-candidate-dir` the same way `oss-crs run` does: a missing path, or a directory passed to `--bug-candidate` (or a file passed to `--bug-candidate-dir`), fails before any container starts instead of being silently ignored.
- `libCRS` `apply_patch_build`: builder-side errors returned before a `rebuild_id` is assigned are now written to `<response_dir>/stderr.log` instead of being dropped. The public signature (`apply_patch_build(patch_path, response_dir, ...)`) and the positional shell form (`apply-patch-build <patch> <response_dir>`) are unchanged; `apply_patch_test` and `run-pov` are likewise unchanged.
- `libCRS` bug-candidates are no longer submitted as raw files: `bug-candidate` has been removed from the `submit` and `register-submit-dir` type choices (`submit pov|seed|patch`, `register-submit-dir pov|seed|patch`). Use the new `libCRS bug-candidate` command group instead. Directed inputs (`--bug-candidate` / `--bug-candidate-dir`) still deliver raw SARIF via `FETCH_DIR/bug-candidates`; those pre-seeds are ingested into the shared DB on `bug-candidate sync`.

### Added
- Run-phase exchange artifact inputs and forwarding: generated `oss-crs run` flags for `--pov/--pov-dir`, `--seed-dir`, `--bug-candidate/--bug-candidate-dir`, `--report/--report-dir`, and `--patch/--patch-dir`; `libCRS fetch patch` support via `FETCH_DIR/patches`; and `--forward-artifacts [<run-id>[,<run-id>...]]` to forward prior run artifacts into the new run's `FETCH_DIR`. Forwarding searches sibling compose-hash workdirs, prompts when a run ID maps to multiple source target/harness/sanitizer combinations, prefers processed POVs/seeds when available, and records provenance in `FORWARDED_ARTIFACTS.json`. Passing bare `--forward-artifacts` in an interactive terminal prompts with prior artifact-producing runs for the same target project across any CRS compose hash. Omitting the flag does not forward artifacts. `--diff` remains the special delta-mode reference diff input.
- `OSS_CRS_BUG_CANDIDATE_DIR` — a global read-write volume (one top-level `BUG_CANDIDATE_DIR/`, not partitioned by run/sanitizer/target) mounted into every CRS module container of every run; holds one bug-candidate SQLite DB for the whole workdir plus its SARIF files. Being global, bug-candidates persist across runs; stale claim leases expire by TTL.
- `OSS_CRS_TARGET_KEY` — the target's key (docker image name with `:`→`_`), injected into run-phase CRS containers. libCRS scopes bug-candidate rows by `OSS_CRS_TARGET_KEY` + `OSS_CRS_TARGET_HARNESS`.
- `libCRS bug-candidate` command group — a queryable, coordination-capable interface for bug-candidates, backed by a **single global SQLite database** on `OSS_CRS_BUG_CANDIDATE_DIR` (SQLite's WAL locking handles parallel access; the DB is one portable file under `.oss-crs-workdir`). Each row carries `target_key` and `harness` columns; libCRS reads the CRS's `OSS_CRS_TARGET_KEY` / `OSS_CRS_TARGET_HARNESS` and automatically filters every query and stamps every insert, so a CRS only sees its own target+harness candidates while the file stays shared. State is immediately consistent and `claim` is a genuine atomic check-and-set. Candidate identity is the SARIF `correlationGuid` (minted when absent; per-instance `guid` kept as detection provenance) and location versions are SARIF `revisionId`s. Verbs (each with a Python counterpart on `CRSUtils`):
  - `bug-candidate add <sarif> [--status] [--harness] [--pov-ref]` → prints candidate id(s) (Python: `bug_candidate_add(sarif_path, *, agent=None, status="new", harness=None, pov_ref=None) -> list[str]`). Mints and embeds a `correlationGuid` when absent.
  - `bug-candidate add-location <id> --version <rev> --file <p> --start-line <n> [--end-line --start-col --end-col --function --repo --branch --uri-base-id]` (Python: `bug_candidate_add_location(candidate_id, version, file_path, start_line, ...)`) — tie a location to a program version so a bug that moves across versions stays one candidate.
  - `bug-candidate set-status <id> <status>` where status ∈ `new|exploring|confirmed|fixed|false_positive` (Python: `bug_candidate_set_status(candidate_id, status, *, agent=None)`).
  - `bug-candidate claim <id> [--owner --ttl]` → JSON, **exits 0 iff claimed** (Python: `bug_candidate_claim(candidate_id, owner=None, ttl_seconds=3600) -> bool`). Atomic across all CRSs sharing the DB; leases auto-expire. `bug-candidate release <id> [--owner]` (Python: `bug_candidate_release(candidate_id, owner=None)`).
  - `bug-candidate merge <id> --into <id2>` (Python: `bug_candidate_merge(candidate_id, into_candidate_id, *, agent=None)`) — explicit dedup: alias one candidate into another.
  - `bug-candidate note <id> <text>` (Python: `bug_candidate_note(candidate_id, text, *, agent=None) -> str`) — append commentary to the pub/sub stream.
  - `bug-candidate list [--status --claimed|--unclaimed --version]` / `bug-candidate get <id>` → JSON (Python: `bug_candidate_list(*, status=None, claimed=None, version=None) -> list[dict]`, `bug_candidate_get(candidate_id) -> dict | None`).
  - `bug-candidate sync [--daemon --poll-interval --log]` → ingests directed-input/bootup raw SARIF from `FETCH_DIR/bug-candidates` into the DB (Python: `bug_candidate_sync() -> int`; `--daemon` is a CLI-only poll loop).
  - `bug-candidate watch [--candidate --since --type --follow --poll-interval --timeout]` → streams update events from the shared `updates` table as JSON Lines for pub/sub (Python: `bug_candidate_watch(since_seq=0, *, candidate_id=None, types=None, follow=False, poll_interval=5.0, timeout=None) -> Iterator[dict]`).
  - `--agent` (all mutating verbs) records the actor on updates; defaults to `$OSS_CRS_AGENT_ID` or the CRS name.
- `oss-crs web-ui {start,stop,status}` command — manages a standalone WebUI dashboard for monitoring CRS run status (served on port 9090; `start` accepts `--port`).
- `--web-ui` flag for `oss-crs run` — launches the WebUI dashboard to monitor the run live and publishes authoritative final artifact and cost totals to it after teardown.
- `--coverage` flag for `oss-crs build-target` — builds an additional coverage-instrumented binary (best-effort; never fails the build) used by the WebUI coverage panel at run time.
- `libCRS build-project --response-dir <dir> [--fuzz-proj-dir <dir>] [--target-source-dir <dir>]` — rebuild the project image from a modified fuzz-proj and/or target-source **directory** (Python: `build_project(response_dir, fuzz_proj_dir=None, target_source_dir=None, builder=None, builder_name=None, rebuild_id=None)`). libCRS diffs each directory against its base mount (`/OSS_CRS_FUZZ_PROJ`, `/OSS_CRS_TARGET_SOURCE`) and applies the resulting patch via the builder sidecar's `/build` endpoint; at least one directory is required. Used by harness-gen CRSs to validate generated harnesses without hand-writing diffs.
- `libCRS submit-harness --fuzz-proj-dir <dir> [--target-source-dir <dir>] [--name <name>]` — submit a generated harness project as directories (Python: `submit_harness(fuzz_proj_dir, target_source_dir=None, name=None)`). Syncs them to `OSS_CRS_SUBMIT_DIR/harnesses/<name>/{fuzz-proj,target-source}/`. `target-source` is omitted when the harness needs no source-level changes.
- `base_runner_image` build arg provided to CRS run-phase module Dockerfiles: the OSS-Fuzz `base-runner` image whose OS matches the target's `base_os_version` from `project.yaml`
- `required_envs` in CRS configuration — declares environment variables a CRS needs and fails fast before `oss-crs run` when they are missing from the host environment and compose `additional_env`.
- `additional_env` preflight warnings for optional env placeholders that reference unset host environment variables.
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
- Runner/builder OS mismatch for targets pinned to a newer base image. Fixes glibc symbol mismatch errors
- Builder and runner sidecar APIs now reject path-like CRS, harness, and
  rebuild identifiers before using them to resolve artifact paths.
- The local run path now passes a `Path` compose-file object consistently into
  `docker_compose_up()`, so helper-sidecar teardown classification applies on
  the main local run path.

### Security
- CRS entry names are now validated at config load time before being used in
  paths, Docker tags, service aliases, and artifact directories.

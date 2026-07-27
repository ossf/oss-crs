---
marp: true
theme: default
paginate: true
html: true
---

# Meeting Notes

OpenSSF Cyber Reasoning Systems Special Interest Group

2026-07-27

---

## PR #319 — Offline dependencies via Nix

libCRS dependencies are now built ahead of time with a **Nix derivation** instead of fetched at run time.

- During **prepare**, libCRS is built as a Nix derivation and stored locally as the `oss-crs-deps` Docker image
- OSS-CRS only provides the image; CRS builder Dockerfiles opt in by copying the closure in themselves:
  ```dockerfile
  COPY --from=oss-crs-deps /nix/store /nix/store
  COPY --from=oss-crs-deps /usr/local/bin/libCRS /usr/local/bin/libCRS
  ```
- Only the libCRS **CLI tool** is supported this way (not the Python library, Issue #322)

---

## PR #312 — Offline run images

Added a `--offline` flag and moved all image builds out of the run phase.

- **`--offline`** (all subcommands): disables `git fetch`
- Image builds move earlier, keyed by whether they depend on the target: `target_dependent` flag (default true)

| Image kind | Built in phase | Examples |
|---|---|---|
| Target-**independent** | `prepare` | exchange, runner/builder sidecar, litellm, postgres, lifecycle |
| Target-**dependent** | `build-target` | `lsp` from atlantis-multilang-wo-concolic |

---

## Report artifact & cross-run forwarding

PR #323 (open)

- Support for `report` artifact, can either submit a directory or a single file.
  `libCRS` creates a tarball for submitted artifacts (note: may revise this)
- Cross-run forwarding: `oss-crs run --forward-artifacts <run-id>,…` seeds a new run from a prior run's povs / seeds / bug-candidates / reports / patches
- Downloading artifacts from the Web UI

---

## Personal Backlog

- Review and merge MIT LL PRs
- Bug-candidate support in OSS-CRS and crs-bug-finding-template (in testing)
- Documentation cleanup (#328)

---

## Q&A / Discussion

Refer to Cyber Reasoning Systems bi-weekly meeting notes.

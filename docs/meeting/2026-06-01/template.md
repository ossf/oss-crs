---
marp: true
theme: default
paginate: true
html: true
---

# Meeting Notes

OpenSSF Cyber Reasoning Systems Special Interest Group

---

## Agenda

1. Harness generation CRS support
2. Other OSS-CRS updates
3. OSTIF
4. MIT LL
5. Open Discussion

---

## Harness generation CRS support

Proposal to support **harness generation** CRSs that produce new fuzzing harnesses for an OSS-Fuzz target and create a project bundle for downstream bug-finding CRSs to consume.

- New libCRS APIs: `build_project` and `submit_harness`
- Artifacts are **directories**, not single files: dir for OSS-Fuzz project files, dir for repo source code
- Reference CRS: [`crs-harness-gen-claude-code`](https://github.com/Team-Atlanta/crs-harness-gen-claude-code)
- Implementation on branch [`feat/harness-gen`](https://github.com/ossf/oss-crs/tree/feat/harness-gen)
- Discussion: [ossf/oss-crs#243](https://github.com/ossf/oss-crs/issues/243)

---

## Harness generation — end-to-end flow

```bash
# 1. Generate harnesses with a harness-gen CRS
oss-crs prepare     --compose-file example/crs-harness-gen-claude-code/compose-oauth.yaml
oss-crs build-target --compose-file example/crs-harness-gen-claude-code/compose-oauth.yaml \
  --fuzz-proj-path ../oss-fuzz/projects/tmux
oss-crs run         --compose-file example/crs-harness-gen-claude-code/compose-oauth.yaml \
  --fuzz-proj-path ../oss-fuzz/projects/tmux

# 2. Locate the generated harness bundle
HARNESS_DIR=$(oss-crs artifacts --compose-file …/compose-oauth.yaml \
  --fuzz-proj-path … --latest | jq '.crs."crs-harness-gen-claude-code".harness')

# 3. Feed the bundle to a bug-finding CRS (libfuzzer here)
oss-crs run --compose-file example/crs-libfuzzer/compose.yaml \
  --fuzz-proj-path     $HARNESS_DIR/tmux/fuzz-proj \
  --target-source-path $HARNESS_DIR/tmux/target-source \
  --target-harness     utf8-fuzzer   # new harness created by the CRS
```

---

## Other OSS-CRS updates

- **`oss-crs archive`**: packages submitted artifacts (POVs, seeds, patches) from a run into a `.tar.gz`. 
  `--all` also includes exchange dir, logs, and shared dirs. ([#218](https://github.com/ossf/oss-crs/pull/218))
- **`--latest` flag** for `oss-crs artifacts` and `oss-crs archive`: selects the most recent run instead of prompting interactively ([#218](https://github.com/ossf/oss-crs/pull/218))
- Post-run results printed outside the Rich UI panel so long artifact paths are not truncated ([#218](https://github.com/ossf/oss-crs/pull/218))
- CRS name sanitization, alphanumeric plus dash and underscore ([#241](https://github.com/ossf/oss-crs/pull/241))

---

## New contributor

Welcome **[Brian Mendonca](https://github.com/bmendonca3)** (@bmendonca3) — first contribution to `oss-crs` with [#241](https://github.com/ossf/oss-crs/pull/241) (CRS entry name validation).

---

## OSTIF

---

## MIT LL

---

## Q&A / Discussion

Refer to Cyber Reasoning Systems bi-weekly meeting notes.

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

1. Website launch
2. CRSBench page
3. `oss-crs` UX improvements
4. Triage CRS registry updates
5. Claude Code CRSs with Subscription
6. Community Contributions

---

## Website launch

OSS-CRS now has a public site at **[oss-crs.openssf.org](https://oss-crs.openssf.org)**.

- Landing page, registry browser with CRS descriptions, and CRSBench overview

---

## CRSBench page

Dedicated page on the site covering the **CRSBench** evaluation framework at
**[oss-crs.openssf.org/crsbench](https://oss-crs.openssf.org/crsbench)**.

- Overview of bug-finding / bug-fixing benchmarks
- Benchmark statistics (challenges, languages, sanitizers)
- Quick-start instructions for running CRSBench against an OSS-CRS pipeline

---

## `oss-crs` UX improvements

Two rounds of CLI work landed in the last cycle.

- **`oss-crs clean`** — removes Docker images and workdir artifacts from prior `prepare`, `build-target`, and `run` phases. Phase-specific subcommands, `--artifacts` to wipe the workdir and artifacts, `-y` to skip prompt (non-interactive).
- **`oss-crs setup`** — now a general setup command (LLM + cgroup) with an interactive LLM proxy configuration phase to modify `example/*/litellm-config.yaml`
- Example litellm configs now default to standard provider keys (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`).

---

## Triage CRS registry updates

New components for **triage** and **seed-filter** CRS, example configuration on how to use them coming soon.

- `crs-atlantis-triage` — bug-finding-triage
- `crs-clusterfuzz-triage` — bug-finding-triage (Clusterfuzz crash dedup)
- `crs-roboduck-triage` — bug-finding-triage (agentic triage)
- `crs-atlantis-ensemble` — seed-filter

---

## Claude Code CRSs with Subscription

Run `crs-claude-code` (patching) and `crs-bug-finding-claude-code` using your own Claude subscription instead of an API key.

- Generate an OAuth token once with `claude setup-token`
- Export `CLAUDE_CODE_OAUTH_TOKEN` and run with the new `compose-oauth.yaml`
- Skips LiteLLM proxy setup — token is passed directly to the CRS container
- Model selection (Opus / Sonnet / Haiku) preset in the compose file
- Quickstart on the site has a side-by-side OAuth walkthrough

---

## Community Contributions

- Docker Compose secrets for LLM keys instead of env vars (@tusharshah21)

---

## Q&A / Discussion

Refer to Cyber Reasoning Systems bi-weekly meeting notes.

# OSS-CRS Plan

This document tracks the current state of OSS-CRS, upcoming features, and the long-term roadmap. It is intended to give contributors and users a clear view of what works today, what's coming next, and where help is needed.

---

## Current State (What's Working)

### CRS Compose (Orchestration Layer)

- [x] Three-phase lifecycle: `prepare` → `build-target` → `run`
- [x] `local` runtime environment via Docker Compose
- [x] `crs-compose.yaml` configuration with Pydantic-based validation
- [x] Per-CRS resource isolation (`cpuset`, `memory`, `llm_budget`)
- [x] CRS sourcing from **git URLs** (url + ref) and **local paths**
- [x] Docker Buildx Bake integration for the prepare phase
- [x] Multi-module CRS support (multiple containers per CRS with private networking)
- [x] Ensemble composition (run multiple CRSs in a single campaign)

### libCRS (CRS Communication Library)

- [x] `submit-build-output` — rsync-based build artifact submission
- [x] `download-build-output` — retrieve build artifacts during run phase
- [x] `skip-build-output` — mark optional outputs as intentionally skipped
- [x] `register-submit-dir` — background daemon with `watchdog` for batch submission (dedup by MD5)
- [x] `register-shared-dir` — symlink-based inter-container file sharing within a CRS
- [x] `submit` — manual single-file submission (`pov`, `seed`, `bug-candidate`)
- [x] `get-service-domain` — DNS-verified service endpoint resolution

### oss-crs-infra (Shared Infrastructure)

- [x] **LiteLLM Proxy** — unified LLM API endpoint, per-CRS API keys, budget enforcement, model routing
- [x] **PostgreSQL** — LiteLLM state and budget tracking
- [x] **Storage** — shared filesystem for seeds, PoVs, bug candidates, and patches
- [x] **litellm-key-gen** — service that registers per-CRS keys and budgets at launch

### Examples & Registry

- [x] `crs-libfuzzer` — lightweight libFuzzer-based CRS example
- [x] `atlantis-multilang-wo-concolic` — LLM-powered multi-language CRS
- [x] Ensemble composition example (both CRSs running simultaneously)
- [x] CRS registry format (`registry/<name>.yaml`) with auto-resolution from compose

---

## Near-Term (Next Up)

These items are the highest-priority incomplete features, critical for making ensemble campaigns and the CRS lifecycle fully functional.

### Cross-CRS Data Sharing

| Item | Status | Notes |
|---|---|---|
| `register-fetch-dir` | Registered in CLI, **not yet implemented** | Enable CRSs to receive shared seeds/PoVs from other CRSs |
| `fetch` (pov, seed, bug-candidate) | Registered in CLI, **not yet implemented** | Core primitive for ensemble value |

> **Why it matters:** Without `fetch`, ensembled CRSs cannot share seeds or PoVs at runtime, limiting the value of running multiple CRSs together.

### CRS / Target Compatibility

| Item | Status | Notes |
|---|---|---|
| `supported_target` validation | TODO in code | Properly check CRS-declared supported languages, sanitizers, and architectures against the actual target |
| Warn vs. error policy | TODO in code | Decide whether a compatibility mismatch should warn or abort |

### Validation Improvements

| Item | Status | Notes |
|---|---|---|
| Env var name/value validation in `crs.yaml` | TODO | Disallow pre-defined env vars from being overridden |
| Docker registry URL validation | TODO | More robust URL format checks |
| LLM model name validation | TODO | Validate model names against LiteLLM config before launch |
| Version string validation | TODO | Stricter parsing of CRS version identifiers |

---

## Mid-Term

Features that will significantly improve the platform's utility for real-world campaigns.

### Infrastructure Services

| Service | Status | Purpose |
|---|---|---|
| **Seed Deduplication** | 📝 Planned | Cross-CRS seed deduplication to reduce redundant fuzzing effort |
| **PoV Verification / Deduplication** | 📝 Planned | Verify crash inputs and deduplicate bugs found by multiple CRSs |
| **WebUI Dashboard** | 📝 Planned | Real-time monitoring: coverage metrics, bug candidates, PoV status, LLM usage |

### Bug-Fixing Pipeline

| Item | Status | Notes |
|---|---|---|
| `apply-patch-build` | Not implemented | Required for CRSs that generate and test patches for discovered bugs |
| Patch submission / review flow | Not designed | End-to-end workflow for bug-fixing CRS types |

### Target Build Improvements

| Item | Status | Notes |
|---|---|---|
| Build caching | TODO in code | Consider cache options for target builds to speed up repeated campaigns |
| Target build implementation cleanup | TODO in code | Marked as "implement this properly" in `target.py` |

---

## Long-Term

### Azure Deployment

| Item | Status | Notes |
|---|---|---|
| `azure` run environment | Declared in config enum, **not implemented** | Currently prints `TODO: Support run env` |
| `AzureCRSUtils` in libCRS | Planned | Azure Blob Storage + ACI backend for libCRS operations |
| Zero-modification portability | Design goal | CRSs should run on Azure without any changes |

### Standalone CRS CLI

| Item | Status | Notes |
|---|---|---|
| `crs` CLI | Stub only | Standalone CRS management outside of `oss-crs` (e.g., build, test, publish a single CRS) |

### InfraClient Integration

| Item | Status | Notes |
|---|---|---|
| `InfraClient` in libCRS | ✅ Implemented | Exchange sidecar handles SUBMIT_DIR → EXCHANGE_DIR; InfraClient fetches from FETCH_DIR (read-only) |

---

## Known Limitations

1. **No cross-CRS runtime data sharing** — `fetch` / `register-fetch-dir` are stubs, so ensembled CRSs cannot share seeds or PoVs during a campaign.
2. **No crash deduplication** — multiple CRSs in an ensemble may report the same bug independently.
3. **No campaign monitoring** — no WebUI or dashboard to observe progress in real-time.
4. **No patching pipeline** — bug-fixing CRS types cannot yet execute `apply-patch-build`.
5. **Single deployment target** — only `local` (Docker Compose) is functional; Azure is config-supported but not implemented.
6. **Standalone CRS CLI not functional** — individual CRS management (outside compose) is not yet available.

---

## How to Contribute

See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidelines. Areas where help is especially welcome:

- **Infrastructure services** — designing and implementing seed dedup, PoV dedup, and WebUI
- **Cross-CRS sharing** — implementing `fetch` / `register-fetch-dir` in libCRS
- **Azure backend** — `AzureCRSUtils` and Azure deployment support
- **New CRS integrations** — packaging existing bug-finding or bug-fixing tools as OSS-CRS-compatible CRSs
- **Validation & robustness** — tightening config validation and error handling across the codebase

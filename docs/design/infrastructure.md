# Shared Infrastructure Architecture (oss-crs-infra)

## Overview

`oss-crs-infra` is the collection of centralized, framework-managed support services shared across all active Cyber Reasoning Systems during a campaign. These services execute in dedicated containers on the `oss-crs-infra-net` network with isolated resource boundaries.

```
┌────────────────────────────────────────────────────────────────────────┐
│                             oss-crs-infra                              │
│                                                                        │
│   ┌────────────────────┐   ┌────────────────────┐   ┌───────────────┐  │
│   │   LiteLLM Proxy    │   │     PostgreSQL     │   │  Key-Gen      │  │
│   │   (Budget & API)   │◀─▶│   (State & Usage)  │◀──│  (Sidecar)    │  │
│   └────────────────────┘   └────────────────────┘   └───────────────┘  │
│             ▲                                               │          │
│             │ HTTP Requests (Bearer Key)                    │ Keys     │
│             ▼                                               ▼          │
│   ┌────────────────────┐                            ┌───────────────┐  │
│   │   Exchange Sidecar │                            │   WebUI       │  │
│   │   (IPC & Synced FS)│                            │   Dashboard   │  │
│   └────────────────────┘                            └───────────────┘  │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 1. LiteLLM Proxy & Budget Enforcement

OSS-CRS uses [LiteLLM](https://github.com/BerriAI/litellm) to abstract model providers (OpenAI, Anthropic, Google Gemini, xAI, custom endpoints) behind a single unified OpenAI-compatible endpoint.

### Architectural Features:
1. **Dynamic Per-CRS Key Generation**:
   - `litellm-key-gen` generates a cryptographically unique API key for each CRS at launch.
   - Keys are injected as Docker secrets mounted at `/run/secrets/litellm_api_key`.
2. **Dollar-Denominated Budget Caps**:
   - If `llm_budget: 25` is set in `crs-compose.yaml`, LiteLLM enforces a hard $25 budget ceiling for that CRS's API key.
   - When the budget is exhausted, further API calls fail fast, preventing unbounded cost overruns.
3. **Spend & Token Tracking**:
   - `litellm-key-gen` polls LiteLLM's `/key/info` and `/spend/logs` endpoints.
   - Outputs a persistent `<run_dir>/spend/litellm-spend-report.json` with exact counts of prompt tokens, completion tokens, and dollar spend per CRS.

---

## 2. Artifact Exchange Sidecar

CRSs never communicate directly over the network. Cross-CRS cooperation (such as sharing generated fuzz seeds, proof-of-vulnerability inputs, and candidate patches) is managed through the **Exchange Sidecar**:

- **Write Area (`SUBMIT_DIR`)**:
  - Each CRS has its own write-only submission directory.
  - `libCRS` deduplicates files by MD5 hash and copies them to `SUBMIT_DIR/<type>/`.
- **Global Exchange (`EXCHANGE_DIR`)**:
  - The exchange sidecar polls submissions across all CRSs.
  - Validates file formats and places unique artifacts into `EXCHANGE_DIR`.
- **Read Area (`FETCH_DIR`)**:
  - Incoming artifacts are made available in each CRS's read-only `FETCH_DIR/<type>/`.
  - `libCRS` can periodically fetch new artifacts via `libCRS fetch` or automatic daemon watching via `libCRS register-fetch-dir`.

---

## 3. WebUI Dashboard & Event Streaming

OSS-CRS includes an integrated WebUI service for monitoring campaigns in real time:

- **Service Management**:
  - Start daemon: `oss-crs web-ui start [--port 9090]`
  - Check status: `oss-crs web-ui status`
  - Stop service: `oss-crs web-ui stop`
- **Dashboard Metrics**:
  - Live artifact counters (Seeds, POVs, Bug Candidates, Reports, Patches).
  - LLM token usage and dollar spend broken down by CRS.
  - Source-level Clang code coverage visualization showing covered functions and lines.
  - Run logs tailing and error event feeds.

---

## 4. Container Image Versioning & Pinning

All infrastructure images are pinned by exact digest in `oss_crs/src/constants.py`:

| Service | Upstream Image | Version |
|---|---|---|
| LiteLLM Proxy | `ghcr.io/berriai/litellm-database` | 1.82.3 |
| State Database | `docker.io/library/postgres` | 16.13 |
| Alpine Utility | `docker.io/library/alpine` | latest |

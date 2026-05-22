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

1. CRSBench Paper
2. Triage Mode

---

## CRSBench Paper

Paper covering the CRSBench evaluation framework is **submitted to ACM CCS**.

- Benchmarks for evaluating CRS bug-finding and bug-fixing capabilities
- Fully compatible with CRS in OSS-CRS
- Resource management (LLM budget, compute constraints) for fair evaluation
- Benchmarks are mix of AIxCC and Team Atlanta written challenges

---

## Triage Mode

A new CRS mode has been added to OSS-CRS for **crash triage** and **seed filtering**.

- Consumes PoVs / seeds produced by bug-finding CRSs
- Deduplicates and clusters crashes
- Produces structured triage reports for downstream patching
- Cleaner handoff between bug-finding and bug-fixing pipelines

---

## Triage Mode: Seed / PoV Flow

<style scoped>img { max-height: 480px; display: block; margin: auto; }</style>

```mermaid
flowchart LR
    BF["Bug-Finding CRS"]:::tr
    EX["EXCHANGE_DIR<br/>(raw PoVs / seeds)"]
    TR["Triage CRS"]:::tr
    PEX["PROCESSED_EXCHANGE_DIR<br/>(triaged output)"]

    BF -->|"libCRS submit (bug-finding)"| EX
    EX -->|"libCRS fetch (triage)"| TR
    TR -->|"libCRS submit (triage)"| PEX
    PEX -->|"libCRS fetch (bug-finding)"| BF

    classDef tr fill:#ffd6a5,stroke:#d97706,color:#5b3a00
```

---

## Q&A / Discussion

Refer to Cyber Reasoning Systems bi-weekly meeting notes.

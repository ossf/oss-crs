# Lab 4: Build from the Bug-Finding CRS Template

**Goal:** Continue from running an existing agentic CRS to building and customizing a bug-finding CRS.

**Target:** `atlanta-libavc-full-01` from the tutorial benchmark archive.

This lab moves from observing an existing CRS to working from a template. The target is intentionally harder than the previous examples: it is difficult for an LLM agent to trigger by reasoning over source alone, and it generally requires substantial fuzzing compute to discover through coverage-guided fuzzing. That makes it a useful challenge for exploring hybrid bug-finding workflows where agent reasoning, target-specific guidance, and automated execution feedback all matter.

The bug-finding template is based on Claude Code. It uses `AGENTS.md` and skills-style prompting to steer the agent, and it includes boilerplate for common CRS tools so you can focus on improving the bug-finding workflow instead of wiring every helper from scratch.

The next tutorial lives in the `tutorial/` subdirectory of Team Atlanta's bug-finding CRS template:

- [Team-Atlanta/crs-bug-finding-template tutorial](https://github.com/Team-Atlanta/crs-bug-finding-template/tree/snapshot/tutorial)
- [Template tutorial README](https://github.com/Team-Atlanta/crs-bug-finding-template/blob/snapshot/tutorial/README.md)

This repository also includes local example compose files for the template under [`example/crs-bug-finding-template`](../../example/crs-bug-finding-template). Use those compose files when you want to run the template through this OSS-CRS checkout.

The upstream tutorial covers prompt engineering, instrumentation, static analysis, dynamic feedback, and multi-agent workflows for a custom bug-finding CRS.

# Videos & Presentations

Short narrated walkthroughs of setting up OSS-CRS and running a CRS end to end, plus slides from public talks.

The videos follow one workflow: set up OSS-CRS, run the LibFuzzer bug-finding CRS against a target, inspect the seeds and proofs of vulnerability (POVs) it produces, and then pass those POVs to a Claude Code bug-fixing CRS that writes a patch. Watch them in order, or jump to the step you need.

## Training videos

| # | Video | Length | What it covers |
|---|---|---|---|
| 1 | [Set up OSS-CRS](videos/01-setup.mp4) | 3:15 | `oss-crs setup`: routing LLM providers through a proxy (provider selection, API key and base URL env vars) and the cgroup v2 delegation step for CPU and memory isolation (Linux x86 only). |
| 2 | [Set variables for the LibFuzzer CRS](videos/02-set-variables.mp4) | 1:15 | Defining the compose file (`CRS`), the harness (`HARNESS`) and the target project (`TARGET`) for a bug-finding campaign. |
| 3 | [Prepare the LibFuzzer CRS](videos/03-prepare.mp4) | 0:41 | `oss-crs prepare`: building the CRS base image and pre-caching tools so later phases don't rebuild. |
| 4 | [Build a target for LibFuzzer](videos/04-build-target.mp4) | 1:42 | `oss-crs build-target`: compiling the target with sanitizers (ASan, UBSan, MSan) and coverage instrumentation, and linking it against the fuzzing harness. |
| 5 | [Run the LibFuzzer CRS](videos/05-run.mp4) | 2:03 | `oss-crs run`: initializing the CRS, validating the configuration, preparing the run environment and starting the fuzzer with its sidecars. |
| 6 | [LibFuzzer outputs: seeds and POVs](videos/06-outputs-seeds-povs.mp4) | 2:13 | What seeds and proofs of vulnerability are, where they are stored under `SUBMIT_DIR`, and how to view them in the web UI dashboard. |
| 7 | [Run a patching CRS](videos/07-run-patching-crs.mp4) | 4:44 | Passing LibFuzzer POVs to the Claude Code bug-fixing CRS with `--pov-dir`, using the LiteLLM proxy, and reviewing the generated patch. |

## Slides

- [OSS-CRS at OpenSSF Community Day 2026](slides/OSS-CRS%20OpenSSF%20Community%20Day%202026.pdf)
- [OSS-CRS at OpenAI, July 2026](slides/OSS-CRS%20OpenAI%20July%202026%20Public.pdf)

## Related documentation

- [Quick Start](https://github.com/ossf/oss-crs/blob/main/README.md#quick-start): the commands shown in these videos, step by step
- [Setup Command](setup.md): details of `oss-crs setup`
- [LLM Providers](llm-providers.md): LiteLLM proxy configuration
- [libCRS](design/libCRS.md): how CRSs submit and fetch seeds, POVs and patches

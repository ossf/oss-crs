# OSS-CRS Tutorial

Team Atlanta presented this OSS-CRS tutorial at SVCC 2026 in San Jose. The labs walk through running a fuzzer-based CRS, patching a discovered vulnerability, running an agentic bug-finding CRS, and then continuing into the bug-finding CRS template tutorial.

## Setup

**Goal:** Prepare the basic environment for the labs.

OSS-CRS is designed to run on Linux. If you are using Windows, we recommend WSL. On macOS, we were able to reproduce the tutorial locally, but we recommend enabling Docker Desktop -> Settings -> General -> "Use containerd for pulling and storing images".

For the official setup and preparation instructions, refer to the documentation in the OSS-CRS repository. We prepared a subset of CRSBench projects for the tutorial. Download it from [Google Drive](https://tinyurl.com/4nvnyxvu) and unzip it. You can unzip it anywhere, but the following commands assume that the benchmarks are inside the `oss-crs` directory.

```bash
tar -xvf benchmarks.tar.gz
```

The unzipped directory contains four entries: three tutorial benchmarks and one PoV that can be used later if Lab 1 does not generate a PoV for the later labs.

## Labs

1. [Run libFuzzer](lab-1-run-libfuzzer.md)
2. [Run a patching CRS](lab-2-run-patching-crs.md)
3. [Run a bug-finding agentic CRS](lab-3-run-bug-finding-agentic-crs.md)
4. [Build from the bug-finding CRS template](lab-4-bug-finding-template.md)

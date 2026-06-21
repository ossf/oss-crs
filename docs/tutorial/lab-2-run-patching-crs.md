# Lab 2: Run a Patching CRS

**Goal:** Run a patching CRS and confirm it fixes the vulnerability the fuzzer found.

**CRS-Claude-Code** is an LLM-based patching CRS built on the Claude Code agent. Given a PoV, it generates a patch, rebuilds the patched source through the builder and runner sidecars, and verifies that the bug no longer reproduces.

## Run CRS-Claude-Code

Reuse the PoV path you saved in [Lab 1](lab-1-run-libfuzzer.md).

```bash
CRS=./example/crs-claude-code/compose.yaml
TARGET=benchmarks/atlanta-mongoose-delta-01
POV=<path to the PoV found in Lab 1, make sure to give a specific PoV file inside the directory>
HARNESS=fuzz

uv run oss-crs prepare --compose-file "$CRS"
uv run oss-crs build-target --compose-file "$CRS" --fuzz-proj-path "$TARGET"
uv run oss-crs run --compose-file "$CRS" --fuzz-proj-path "$TARGET" \
  --target-harness "$HARNESS" --pov "$POV" --timeout 600
```

## Ground-Truth Patch

The fix makes the checksum cover only the bytes that were actually copied (`left`) instead of the raw `pay.len`:

```diff
  memcpy(icmp + 1, pkt->pay.buf, left);
- icmp->csum = ipcsum(icmp, sizeof(*icmp) + pkt->pay.len);
+ icmp->csum = ipcsum(icmp, sizeof(*icmp) + left);
```

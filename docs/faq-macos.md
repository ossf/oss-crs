# Running oss-crs on macOS (Apple Silicon) — Setup Guide

A step-by-step guide to get `oss-crs` running on an Apple Silicon Mac, resolving the
three root causes found during troubleshooting: the `oss-crs-deps` image error, the
`Killed` (exit 137) fuzzer crash, and the `cpuset` cgroup requirement that
`oss-crs setup` cannot configure on Mac.

This guide uses **Docker Desktop + Rosetta**, which is the environment the tool is
actually built and tested against.

---

## Background: the three root causes

1. **`oss-crs-deps: pull access denied` error.** The compose file uses
   `docker_registry: local`, meaning `oss-crs-deps` is meant to be **built locally**
   by the `prepare` step, then referenced as a local tag. The error appears when that
   local image isn't present in the daemon being built against — which happened
   repeatedly because `prepare` and `build-target` kept targeting different Docker
   daemons (due to Colima profile switching). Fix: run all phases against **one**
   daemon, and verify the image exists after `prepare`.

2. **`Killed` / exit 137 when the fuzzer starts.** The fuzzing images are amd64-only.
   On Apple Silicon they run under emulation. Colima's default QEMU emulation is
   unstable for AddressSanitizer/libFuzzer binaries (they die instantly on startup).
   Fix: use **Rosetta** emulation instead of QEMU.

3. **`Requested CPUs are not available` / cgroup errors.** The compose file pins
   containers to specific CPU cores (`cpuset`), which requires cgroup delegation that
   `oss-crs setup`'s Phase 2 **fails to configure on Mac** (it targets Docker
   Desktop's host file layout and errors with
   `tee: /etc/docker/daemon.json: No such file or directory`). Fix: **remove the
   `cpuset` lines** from the compose file.

---

# PART A — admin account: one-time setup

## A1. Clean up old Colima experiments (from the standard account's account, optional but recommended)

These caused most of the daemon confusion. Before the admin starts:

```
colima stop
colima stop --profile x86
colima delete x86
colima delete crs
```

Leave the original `default` profile for now; remove it later once Docker Desktop is
confirmed working.

## A2. Install Docker Desktop

- Download Docker Desktop for Mac (Apple Silicon) from
  https://www.docker.com/products/docker-desktop
- Open the `.dmg`, drag **Docker.app** into `/Applications`.
- Launch it from Finder. When prompted:
  - Accept the license agreement.
  - Follow the privileged-helper prompt (this is the
    only admin-required step — it installs `com.docker.vmnetd`).
  - Skip sign-in if offered (not required).
- Wait for the whale icon in the menu bar to stop animating.

**Done when:** the whale icon is steady and clicking it shows "Docker Desktop is running."

## A3. Verify the daemon works

From the admin account's session:

```
docker ps
```

Should return an empty table cleanly.

## A4. Enable Rosetta and set resources

In **Docker Desktop → Settings**:

- **General tab:** enable **"Use Rosetta for x86_64/amd64 emulation on Apple Silicon."**
  *(This is the single most important toggle — it fixes the `Killed`/exit-137 fuzzer crash.)*
- **Resources tab:** set **CPUs ≥ 8** and **Memory ≥ 16 GB**.
  *(This Mac has 15 cores / 48 GB, so this is comfortable. If you can spare it,
  setting Memory to 24 GB avoids having to shrink the memory limits in Part B4.)*
- Click **Apply & Restart**.

**Part A is done when:** Docker Desktop is running, Rosetta is enabled, resources are
set, and `docker ps` works from the admin account's session.

---

# PART B 

## B1. Switch back to your own account

Log out of the admin account's session and log into the standard account's normally. **Do not use `su`** — that
caused environment/path confusion previously.

## B2. Point your shell at Docker Desktop's socket (not Colima's)

Your `~/.zshrc` still points `DOCKER_HOST` at the old Colima socket. Remove it:

```
sed -i '' '/DOCKER_HOST/d' ~/.zshrc
```

Then open a fresh terminal, or run `unset DOCKER_HOST` in the current one.

Reset the Docker context that was changed during the Colima work:

```
docker context use desktop-linux
```

**Done when** this returns a clean empty table — no socket error, no
"DOCKER_HOST overrides context" warning:

```
docker ps
```

## B3. Confirm resources

```
docker info | grep -iE "cpus|architecture"
```

You want CPUs ≥ 8. (Rosetta doesn't appear here; it activates automatically per
container for amd64 images.)

## B4. Edit the compose file — remove the `cpuset` lines

**Back up the file first:**

```
cp ~/code/oss-crs/example/crs-libfuzzer/compose.yaml ~/code/oss-crs/example/crs-libfuzzer/compose.yaml.bak
```

Open it:

```
code ~/code/oss-crs/example/crs-libfuzzer/compose.yaml
```

### BEFORE

```yaml
run_env: local
docker_registry: local
# --- Infrastructure ---------------------------------------------------------
oss_crs_infra:
  cpuset: "0-3"
  memory: "16G"
# --- CRS (crs-libfuzzer) ---------------------------------------------------
crs-libfuzzer:
  cpuset: "4-7"
  memory: "16G"
```

### AFTER

```yaml
run_env: local
docker_registry: local
# --- Infrastructure ---------------------------------------------------------
oss_crs_infra:
  cpuset: "0-7"
  memory: "16G"
# --- CRS (crs-libfuzzer) ---------------------------------------------------
# Pure fuzzer — no LLM or builder sidecar needed.
crs-libfuzzer:
  cpuset: "0-7"
  memory: "16G"
```

**The change:** delete exactly two lines — `cpuset: "0-3"` and `cpuset: "4-7"`.
Leave `run_env`, `docker_registry: local`, and the `memory` lines unchanged.

### Memory note

With `cpuset` gone, the two services request 16G + 16G = **32G** of memory limits
combined.

- If **Docker Desktop is allocated 24 GB or more:** leave both at `"16G"`.
- If **Docker Desktop is allocated 16 GB:** lower both to `"8G"` so they fit:

```yaml
oss_crs_infra:
  memory: "16G"
crs-libfuzzer:
  memory: "16G"
```

**Verify the edit:**

```
grep -n "cpuset\|memory" ~/code/oss-crs/example/crs-libfuzzer/compose.yaml
```

You should see the two `memory:` lines and two lines for  `cpuset`**.

## B5. Do NOT run `oss-crs setup`'s cgroup step

Setup's Phase 2 (cgroup config) is the part that is broken on Mac. Your LLM
configuration from earlier persists, so you can **skip `setup` entirely** at this
point. If you ever do run it, only Phase 1 (LLM keys) is safe — never proceed through
Phase 2's Docker cgroup changes.

## B6. Run the three phases back-to-back on the same daemon

This is the critical discipline that was violated repeatedly before: **do not switch
Docker contexts, profiles, or `DOCKER_HOST` between these commands.**

From `~/code/oss-crs`:

```
uv run oss-crs prepare --compose-file ./example/crs-libfuzzer/compose.yaml
```

### CHECKPOINT — this must pass before continuing

```
docker images | grep oss-crs-deps
```

**You must see `oss-crs-deps` in the output.**

- If it **is** there → `prepare` built it into Docker Desktop's daemon. The
  "pull access denied" error is now permanently resolved. Continue below.
- If it is **NOT** there → **stop.** Do not run `build-target`. The problem is
  isolated to `prepare`, not the later phases. See "If the checkpoint fails" below.

Assuming the image is present, continue:

```
uv run oss-crs build-target \
  --compose-file ./example/crs-libfuzzer/compose.yaml \
  --fuzz-proj-path benchmarks/atlanta-mongoose-delta-01
```

Then:

```
uv run oss-crs run \
  --compose-file ./example/crs-libfuzzer/compose.yaml \
  --fuzz-proj-path benchmarks/atlanta-mongoose-delta-01 \
  --target-harness fuzz \
  --timeout 300
```

---

## How each step maps to the root causes

| Root cause | Fixed by |
|---|---|
| `oss-crs-deps: pull access denied` | Running `prepare` on the **same single daemon** as `build-target`/`run` (B6), with the checkpoint proving the image landed |
| `Killed` / exit 137 (ASAN under emulation) | Docker Desktop's **Rosetta** toggle (A4) |
| `cpuset` / cgroup requirement `setup` can't satisfy | **Adjusting the `cpuset` lines** from the compose file (B4) |

---

## If the checkpoint (B6) fails

If `docker images | grep oss-crs-deps` shows nothing after a successful-looking
`prepare`, this is the single precise issue to escalate. Do **not** keep running
`build-target`/`run` — they will only reproduce the same downstream error.

Two possibilities:

1. **A second `cpuset` injection point.** If the failing generated compose file (in
   `/tmp/...`) still shows a `cpuset` line even after the B4 edit, the value is being
   injected from a template default elsewhere, not just this compose file. That would
   need tracking down in the oss-crs templates.

2. **A genuine gap in how `prepare` builds/loads `oss-crs-deps` on Mac.** 

- `oss-crs setup` Phase 2 fails on macOS because it assumes Docker Desktop's host
  filesystem layout, erroring with
  `tee: /etc/docker/daemon.json: No such file or directory`. There is no documented
  Colima/Apple-Silicon path.
- On macOS with Docker Desktop, `oss-crs prepare` reports success but (in some
  environments) does not produce `oss-crs-deps:latest` locally, causing
  `build-target` to fail resolving it.


---

## Quick reference — the full happy path

```
# Install Docker Desktop, enable Rosetta, set CPUs>=8 / Mem>=16GB

# 
sed -i '' '/DOCKER_HOST/d' ~/.zshrc
unset DOCKER_HOST
docker context use desktop-linux
docker ps                       # must be clean

# Edit compose: remove both cpuset lines (see B4)
grep -n "cpuset\|memory" ~/code/oss-crs/example/crs-libfuzzer/compose.yaml

cd ~/code/oss-crs
uv run oss-crs prepare --compose-file ./example/crs-libfuzzer/compose.yaml
docker images | grep oss-crs-deps          # MUST show the image before continuing

uv run oss-crs build-target --compose-file ./example/crs-libfuzzer/compose.yaml \
  --fuzz-proj-path benchmarks/atlanta-mongoose-delta-01

uv run oss-crs run --compose-file ./example/crs-libfuzzer/compose.yaml \
  --fuzz-proj-path benchmarks/atlanta-mongoose-delta-01 \
  --target-harness fuzz --timeout 300
```

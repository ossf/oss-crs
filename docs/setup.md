# Setup Command

The `oss-crs setup` command configures your host system for **cgroup-parent mode**, which enables memory resource control shared across CRS containers.

## Requirements

- **Linux** with cgroup v2 (unified hierarchy)
- **Docker** installed and running
- **systemd** as the init system (for user service cgroups)
- **sudo** access (for initial configuration only)

## Quick Start

```bash
# Interactive setup (recommended for first-time)
oss-crs setup

# Non-interactive setup (for automation)
oss-crs setup --yes

# Check status only (no changes)
oss-crs setup --check
```

## Command Options

| Option | Description |
|--------|-------------|
| `--check` | Only check current status without making changes |
| `-y`, `--yes` | Automatically accept all prompts (non-interactive mode) |

## What Setup Does

The setup command performs the following configuration steps:

### 1. Docker Cgroup Driver

Configures Docker to use the `cgroupfs` driver instead of `systemd`. This is required for cgroup-parent to work correctly.

**Changes made:**
- Adds `{"exec-opts": ["native.cgroupdriver=cgroupfs"]}` to `/etc/docker/daemon.json`
- Restarts Docker daemon

**Note:** Running containers will be stopped when Docker restarts.

### 2. Cgroup v2 Delegation

Enables cgroup v2 controller delegation so non-root users can manage cgroups.

**Changes made:**
- Enables `cpuset` and `memory` controllers at your user service level
- Creates the `oss-crs` directory under your user's cgroup hierarchy
- Sets ownership so you can create sub-cgroups without sudo

### 3. Controller Enablement

Enables the required controllers (`cpuset`, `memory`) at the oss-crs cgroup level.

## Cgroup Hierarchy

After setup, the cgroup hierarchy looks like:

```
/sys/fs/cgroup/
└── user.slice/
    └── user-<uid>.slice/
        └── user@<uid>.service/
            └── oss-crs/                    # Created by setup
                └── <run-id>/               # Created per oss-crs run
                    ├── crs-1/              # cgroup_parent for CRS 1
                    └── crs-2/              # cgroup_parent for CRS 2
```

## Verifying Setup

Run `oss-crs setup --check` to verify your system is configured correctly:

```bash
$ oss-crs setup --check

Checking requirements...
  [OK] Docker cgroup driver - cgroupfs
  [OK] Cgroup v2 delegation - cpuset, memory enabled
  [OK] oss-crs cgroup directory - /sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service/oss-crs
  [OK] oss-crs controllers - cpuset, memory enabled

All checks passed! Your system is ready for cgroup-parent mode.
```

## Auto-Detection

Once setup is complete, `oss-crs build-target` and `oss-crs run` will automatically detect cgroup-parent availability:

- **If available:** Creates per-run cgroups and uses shared resource limits
- **If not available:** Falls back to per-container `mem_limit` and `cpuset` limits

No additional configuration is needed after running `oss-crs setup`.

## Rootless Docker & Podman

`prepare`, `build-target`, `run` and `list-harnesses` work under
[rootless Docker](https://docs.docker.com/engine/security/rootless/) or
[rootless Podman](https://github.com/containers/podman/blob/main/docs/tutorials/rootless_tutorial.md).
Follow the upstream guide for your runtime, including the subordinate ID range
(`/etc/subuid` / `/etc/subgid`) it requires; images with several users
(`_apt`, `postgres`, `nixbld*`) fail without one.

Compose and the builder and lifecycle sidecars need a Docker-compatible API
socket. With Podman, enable `podman.socket`. `oss-crs` uses `DOCKER_HOST` if
set, and otherwise `$XDG_RUNTIME_DIR/podman/podman.sock` when it exists.

Run `oss-crs setup --check` to see the detected runtime, whether a subordinate
ID range is configured and, on Podman, which Compose v2 is used.

`oss-crs setup` (cgroup-parent mode) needs sudo and rootful Docker, so you can
skip it for rootless runtimes; runs then use per-container limits (see
[Auto-Detection](#auto-detection)).

### Podman

`oss-crs` runs compose through `podman compose` with
[Docker Compose v2](https://docs.docker.com/compose/install/standalone/) as the
provider; `podman-compose` and Compose v1 are not supported. Install the
`docker-compose` v2 binary on `PATH` or as `~/.docker/cli-plugins/docker-compose`,
or set `PODMAN_COMPOSE_PROVIDER` to its path. On Podman, `oss-crs` builds compose
images with `podman build` itself, because Compose v2 needs BuildKit for
`additional_contexts`.

`depends_on: condition: service_healthy` relies on Podman's healthchecks, which
Podman runs from systemd user timers. If those timers cannot run Podman (for
example because its storage is configured only through environment variables
that systemd units do not see), healthchecks never pass and `oss-crs run`
waits forever. Put such settings in `~/.config/containers/storage.conf` instead.

### Privileged Containers

Build, run and `list-harnesses` containers are privileged by default. Pass
`--unprivileged` to `build-target`, `run` or `list-harnesses` to drop this,
e.g. when a rootless Docker/Podman setup rejects `--privileged`.

## Troubleshooting

### Docker not using cgroupfs after setup

If Docker is still using systemd driver after setup:

1. Check `/etc/docker/daemon.json` contains the cgroupfs configuration
2. Restart Docker: `sudo systemctl restart docker`
3. Verify: `docker info --format '{{.CgroupDriver}}'`

### Permission denied when creating cgroups

If you see permission errors during `oss-crs run`:

1. Verify the oss-crs directory exists and is owned by you:
   ```bash
   ls -la /sys/fs/cgroup/user.slice/user-$(id -u).slice/user@$(id -u).service/oss-crs
   ```

2. Re-run setup: `oss-crs setup`

### Controllers not enabled

If controllers are missing:

1. Check delegation at user service level:
   ```bash
   cat /sys/fs/cgroup/user.slice/user-$(id -u).slice/user@$(id -u).service/cgroup.subtree_control
   ```

2. Enable delegation with systemd (requires root):
   ```bash
   sudo mkdir -p /etc/systemd/system/user@.service.d
   sudo tee /etc/systemd/system/user@.service.d/delegate.conf <<EOF
   [Service]
   Delegate=cpuset memory
   EOF
   sudo systemctl daemon-reload
   ```

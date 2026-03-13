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

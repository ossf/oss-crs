"""Cgroup v2 management for resource control.

This module provides functions to create and manage cgroup v2 hierarchies
for Docker container resource management using cgroup-parent.
"""

import os
import re
import secrets
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .crs import CRS


CGROUP_FS_ROOT = Path("/sys/fs/cgroup")


def get_user_cgroup_base() -> Path:
    """Get the base cgroup path for the current user.

    Returns:
        Path to /sys/fs/cgroup/user.slice/user-<uid>.slice/user@<uid>.service/oss-crs
    """
    uid = os.getuid()
    return CGROUP_FS_ROOT / f"user.slice/user-{uid}.slice/user@{uid}.service/oss-crs"


def get_user_service_cgroup() -> Path:
    """Get the user service cgroup path.

    Returns:
        Path to /sys/fs/cgroup/user.slice/user-<uid>.slice/user@<uid>.service
    """
    uid = os.getuid()
    return CGROUP_FS_ROOT / f"user.slice/user-{uid}.slice/user@{uid}.service"


def check_docker_cgroup_driver() -> tuple[bool, str]:
    """Check if Docker is using cgroupfs driver.

    Returns:
        Tuple of (is_cgroupfs, driver_name)
        - is_cgroupfs: True if using cgroupfs driver
        - driver_name: The actual driver name (e.g., "cgroupfs", "systemd")
    """
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.CgroupDriver}}"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        driver = result.stdout.strip()
        return driver == "cgroupfs", driver
    except subprocess.CalledProcessError:
        return False, "unknown"
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except FileNotFoundError:
        return False, "docker_not_found"


def check_cgroup_delegation() -> tuple[bool, list[str]]:
    """Verify that required cgroup controllers are delegated to user.

    Returns:
        Tuple of (is_valid, missing_controllers)
        - is_valid: True if all required controllers are delegated
        - missing_controllers: List of missing controller names
    """
    required_controllers = {"cpuset", "memory"}
    user_service = get_user_service_cgroup()
    subtree_control_file = user_service / "cgroup.subtree_control"

    if not subtree_control_file.exists():
        return False, list(required_controllers)

    try:
        enabled_controllers = subtree_control_file.read_text().strip().split()
        enabled_set = set(enabled_controllers)
        missing = required_controllers - enabled_set
        return len(missing) == 0, sorted(missing)
    except (OSError, PermissionError):
        return False, list(required_controllers)


def check_oss_crs_directory() -> tuple[bool, str]:
    """Check if the oss-crs cgroup directory exists and is writable.

    Returns:
        Tuple of (exists_and_writable, status_message)
    """
    oss_crs_path = get_user_cgroup_base()

    if not oss_crs_path.exists():
        return False, "not_exists"

    # Check if we can write to it
    try:
        # Try to read subtree_control
        subtree_control = oss_crs_path / "cgroup.subtree_control"
        if subtree_control.exists():
            subtree_control.read_text()
        return True, "ok"
    except PermissionError:
        return False, "permission_denied"
    except OSError as e:
        return False, str(e)


def check_oss_crs_controllers() -> tuple[bool, list[str]]:
    """Check if oss-crs directory has required controllers enabled.

    Returns:
        Tuple of (controllers_enabled, missing_controllers)
    """
    required_controllers = {"cpuset", "memory"}
    oss_crs_path = get_user_cgroup_base()
    subtree_control_file = oss_crs_path / "cgroup.subtree_control"

    if not subtree_control_file.exists():
        return False, list(required_controllers)

    try:
        enabled = subtree_control_file.read_text().strip().split()
        enabled_set = set(enabled)
        missing = required_controllers - enabled_set
        return len(missing) == 0, sorted(missing)
    except (OSError, PermissionError):
        return False, list(required_controllers)


def get_docker_daemon_config_path() -> Path:
    """Get the path to Docker daemon configuration file."""
    return Path("/etc/docker/daemon.json")


def read_docker_daemon_config() -> dict | None:
    """Read Docker daemon configuration.

    Returns:
        Parsed JSON config or None if file doesn't exist or is invalid
    """
    import json
    config_path = get_docker_daemon_config_path()
    if not config_path.exists():
        return None
    try:
        return json.loads(config_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def generate_docker_config_commands() -> list[tuple[str, str]]:
    """Generate commands to configure Docker for cgroupfs driver.

    Returns:
        List of (description, command) tuples
    """
    import json
    config_path = get_docker_daemon_config_path()
    current_config = read_docker_daemon_config() or {}

    # Add cgroupfs driver option
    exec_opts = current_config.get("exec-opts", [])
    if "native.cgroupdriver=cgroupfs" not in exec_opts:
        exec_opts.append("native.cgroupdriver=cgroupfs")
    current_config["exec-opts"] = exec_opts

    new_config = json.dumps(current_config, indent=2)

    commands = []

    # Create backup if file exists
    if config_path.exists():
        commands.append((
            "Create backup of existing Docker config",
            f"sudo cp {config_path} {config_path}.bak"
        ))

    # Write new config
    commands.append((
        "Write Docker daemon configuration",
        f"echo '{new_config}' | sudo tee {config_path}"
    ))

    # Restart Docker
    commands.append((
        "Restart Docker daemon",
        "sudo systemctl restart docker"
    ))

    return commands


def generate_cgroup_setup_commands() -> list[tuple[str, str]]:
    """Generate commands to set up cgroup delegation.

    Returns:
        List of (description, command) tuples
    """
    uid = os.getuid()
    gid = os.getgid()
    user_service = get_user_service_cgroup()
    oss_crs_path = get_user_cgroup_base()

    commands = []

    # Enable controllers at user@<uid>.service level
    commands.append((
        "Enable cpuset and memory controllers at user service level",
        f'echo "+cpuset +memory" | sudo tee {user_service}/cgroup.subtree_control'
    ))

    # Create oss-crs directory
    commands.append((
        "Create oss-crs cgroup directory",
        f"sudo mkdir -p {oss_crs_path}"
    ))

    # Set ownership (recursive to include cgroup files)
    commands.append((
        f"Set ownership of oss-crs directory to {uid}:{gid}",
        f"sudo chown -R {uid}:{gid} {oss_crs_path}"
    ))

    # Enable controllers at oss-crs level (needs to be done after chown)
    commands.append((
        "Enable cpuset and memory controllers at oss-crs level",
        f'echo "+cpuset +memory" | tee {oss_crs_path}/cgroup.subtree_control'
    ))

    return commands


def enable_oss_crs_controllers() -> tuple[bool, str]:
    """Enable controllers at oss-crs level (no sudo required).

    Returns:
        Tuple of (success, message)
    """
    oss_crs_path = get_user_cgroup_base()
    subtree_control = oss_crs_path / "cgroup.subtree_control"

    try:
        subtree_control.write_text("+cpuset +memory")
        return True, "Controllers enabled successfully"
    except PermissionError:
        return False, "Permission denied writing to cgroup.subtree_control"
    except OSError as e:
        return False, str(e)


def cleanup_cgroup(cgroup_path: Path, recursive: bool = True) -> tuple[bool, str]:
    """Remove a cgroup directory after containers have exited.

    Args:
        cgroup_path: Path to the cgroup to remove
        recursive: If True, remove child cgroups first (depth-first)

    Returns:
        Tuple of (success, message)

    Note:
        This will fail if processes are still running in the cgroup.
        The caller should ensure all containers have stopped first.
        The kernel will eventually clean up empty cgroups automatically.
    """
    if not cgroup_path.exists():
        return True, "Cgroup does not exist"

    try:
        if recursive:
            # Remove child cgroups first (depth-first)
            for child in sorted(cgroup_path.iterdir(), reverse=True):
                if child.is_dir() and not child.name.startswith("cgroup."):
                    success, msg = cleanup_cgroup(child, recursive=True)
                    if not success:
                        return False, f"Failed to remove child {child.name}: {msg}"

        # Remove the cgroup directory
        cgroup_path.rmdir()
        return True, f"Removed {cgroup_path}"
    except OSError as e:
        # Common errors:
        # - EBUSY: processes still in cgroup
        # - ENOTEMPTY: child cgroups still exist
        return False, str(e)


def cleanup_worker_cgroups(max_age_seconds: int | None = None) -> list[tuple[Path, bool, str]]:
    """Clean up old worker cgroups under oss-crs directory.

    Args:
        max_age_seconds: If provided, only clean up cgroups older than this.
                        If None, attempt to clean up all worker cgroups.

    Returns:
        List of (cgroup_path, success, message) tuples
    """
    import time

    base_path = get_user_cgroup_base()
    if not base_path.exists():
        return []

    results = []
    now = time.time()

    for child in base_path.iterdir():
        if not child.is_dir():
            continue

        # Skip if checking age and cgroup is too new
        if max_age_seconds is not None:
            try:
                mtime = child.stat().st_mtime
                age = now - mtime
                if age < max_age_seconds:
                    continue
            except OSError:
                continue

        success, msg = cleanup_cgroup(child, recursive=True)
        results.append((child, success, msg))

    return results


def cgroup_path_for_docker(cgroup_path: Path) -> str:
    """Convert cgroup filesystem path to Docker cgroup_parent format.

    Strips /sys/fs/cgroup prefix for Docker's cgroupfs driver.

    Example:
        /sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service/oss-crs/worker
        -> /user.slice/user-1000.slice/user@1000.service/oss-crs/worker

    Args:
        cgroup_path: Full filesystem path to cgroup

    Returns:
        Path suitable for Docker's cgroup_parent option
    """
    path_str = str(cgroup_path)
    cgroup_root = str(CGROUP_FS_ROOT)
    if path_str.startswith(cgroup_root):
        return path_str[len(cgroup_root):]
    return path_str


# =============================================================================
# Runtime cgroup management (used during build/run)
# =============================================================================


def check_cgroup_parent_available() -> tuple[bool, str]:
    """Check if cgroup-parent mode is available.

    Performs all checks needed to use cgroup-parent:
    1. Docker uses cgroupfs driver
    2. Cgroup v2 delegation is enabled
    3. oss-crs directory exists and is writable
    4. Controllers are enabled at oss-crs level

    Returns:
        Tuple of (available, message)
        - available: True if all checks pass
        - message: Description of first failing check, or "ok" if all pass
    """
    # Check Docker driver
    is_cgroupfs, driver = check_docker_cgroup_driver()
    if not is_cgroupfs:
        return False, f"Docker using '{driver}' driver, needs 'cgroupfs'. Run: oss-crs setup"

    # Check delegation
    delegation_ok, missing = check_cgroup_delegation()
    if not delegation_ok:
        return False, f"Cgroup delegation missing: {', '.join(missing)}. Run: oss-crs setup"

    # Check oss-crs directory
    dir_ok, status = check_oss_crs_directory()
    if not dir_ok:
        return False, f"oss-crs cgroup directory: {status}. Run: oss-crs setup"

    # Check controllers
    controllers_ok, missing = check_oss_crs_controllers()
    if not controllers_ok:
        return False, f"oss-crs controllers missing: {', '.join(missing)}. Run: oss-crs setup"

    return True, "ok"


def parse_memory_to_bytes(memory_str: str) -> int:
    """Parse memory string to bytes.

    Args:
        memory_str: Memory string like "8G", "1024M", "2GB"

    Returns:
        Memory in bytes
    """
    pattern = r"^(\d+(?:\.\d+)?)\s*(B|K|KB|M|MB|G|GB|T|TB)$"
    match = re.match(pattern, memory_str.strip(), re.IGNORECASE)
    if not match:
        raise ValueError(f"Invalid memory format: '{memory_str}'")

    value = float(match.group(1))
    unit = match.group(2).upper()

    multipliers = {
        "B": 1,
        "K": 1024,
        "KB": 1024,
        "M": 1024 ** 2,
        "MB": 1024 ** 2,
        "G": 1024 ** 3,
        "GB": 1024 ** 3,
        "T": 1024 ** 4,
        "TB": 1024 ** 4,
    }

    return int(value * multipliers[unit])


def parse_cpuset(cpuset_str: str) -> set[int]:
    """Parse cpuset string to set of CPU IDs.

    Args:
        cpuset_str: Cpuset string like "0-3", "0,1,2,3", "0-3,5,7-9"

    Returns:
        Set of CPU IDs
    """
    cpus = set()
    for part in cpuset_str.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-", 1)
            cpus.update(range(int(start), int(end) + 1))
        else:
            cpus.add(int(part))
    return cpus


def format_cpuset(cpus: set[int]) -> str:
    """Format set of CPU IDs to cpuset string.

    Args:
        cpus: Set of CPU IDs

    Returns:
        Cpuset string like "0-3,5,7-9"
    """
    if not cpus:
        return ""

    sorted_cpus = sorted(cpus)
    ranges = []
    start = prev = sorted_cpus[0]

    for cpu in sorted_cpus[1:]:
        if cpu == prev + 1:
            prev = cpu
        else:
            if start == prev:
                ranges.append(str(start))
            else:
                ranges.append(f"{start}-{prev}")
            start = prev = cpu

    # Handle last range
    if start == prev:
        ranges.append(str(start))
    else:
        ranges.append(f"{start}-{prev}")

    return ",".join(ranges)


def generate_worker_cgroup_name(run_id: str, phase: str) -> str:
    """Generate a unique worker cgroup name.

    Args:
        run_id: Run identifier
        phase: "build" or "run"

    Returns:
        Worker cgroup name like "run_id-phase-timestamp-random"
    """
    timestamp = int(time.time())
    random_suffix = secrets.token_hex(4)
    return f"{run_id}-{phase}-{timestamp}-{random_suffix}"


def create_worker_cgroup(worker_name: str) -> Path:
    """Create a worker cgroup for organizing per-CRS cgroups.

    The worker cgroup does NOT set resource limits - it only enables
    controllers for child cgroups. Each CRS cgroup sets its own limits.

    Args:
        worker_name: Name for the worker cgroup

    Returns:
        Path to the created worker cgroup

    Raises:
        OSError: If cgroup creation fails
    """
    base_path = get_user_cgroup_base()
    worker_path = base_path / worker_name

    # Create worker directory
    worker_path.mkdir(parents=True, exist_ok=True)

    # Enable controllers for sub-cgroups (no resource limits at this level)
    (worker_path / "cgroup.subtree_control").write_text("+cpuset +memory")

    return worker_path


def create_crs_cgroup(
    worker_path: Path,
    crs_name: str,
    cpuset: str,
    memory_bytes: int,
) -> Path:
    """Create a per-CRS cgroup under a worker cgroup.

    Args:
        worker_path: Path to parent worker cgroup
        crs_name: Name of the CRS
        cpuset: CPU set for this CRS
        memory_bytes: Memory limit for this CRS

    Returns:
        Path to the created CRS cgroup

    Raises:
        OSError: If cgroup creation fails
    """
    crs_path = worker_path / crs_name

    # Create CRS directory
    crs_path.mkdir(parents=True, exist_ok=True)

    # Set cpuset
    (crs_path / "cpuset.cpus").write_text(cpuset)

    # Set memory limit
    (crs_path / "memory.max").write_text(str(memory_bytes))

    # Enable controllers for Docker container sub-cgroups
    (crs_path / "cgroup.subtree_control").write_text("+cpuset +memory")

    return crs_path


def create_run_cgroups(
    run_id: str,
    phase: str,
    crs_list: list["CRS"],
) -> tuple[Path, dict[str, str]]:
    """Create cgroups for a run with per-CRS sub-cgroups.

    Creates a worker cgroup with total resources (union of CPUs, sum of memory),
    then creates per-CRS sub-cgroups with individual limits.

    Args:
        run_id: Run identifier
        phase: "build" or "run"
        crs_list: List of CRS instances with resource configurations

    Returns:
        Tuple of (worker_cgroup_path, cgroup_parents)
        - worker_cgroup_path: Path to worker cgroup (for cleanup)
        - cgroup_parents: Dict mapping CRS name to Docker cgroup_parent string

    Raises:
        OSError: If cgroup creation fails
    """
    # Create worker cgroup (no resource limits, just enables controllers)
    worker_name = generate_worker_cgroup_name(run_id, phase)
    worker_path = create_worker_cgroup(worker_name)

    # Create per-CRS cgroups and collect docker cgroup_parent strings
    cgroup_parents: dict[str, str] = {}
    for crs in crs_list:
        memory_bytes = parse_memory_to_bytes(crs.resource.memory)
        crs_cgroup_path = create_crs_cgroup(
            worker_path,
            crs.name,
            crs.resource.cpuset,
            memory_bytes,
        )
        cgroup_parents[crs.name] = cgroup_path_for_docker(crs_cgroup_path)

    return worker_path, cgroup_parents

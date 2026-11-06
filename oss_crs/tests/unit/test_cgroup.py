# SPDX-License-Identifier: MIT
"""Unit tests for cgroup v2 resource management and sandboxing."""

import json
import os
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import oss_crs.src.cgroup as cgroup_mod
from oss_crs.src.cgroup import (
    CGROUP_FS_ROOT,
    cgroup_path_for_docker,
    check_cgroup_delegation,
    check_cgroup_parent_available,
    check_docker_cgroup_driver,
    check_oss_crs_controllers,
    check_oss_crs_directory,
    cleanup_cgroup,
    cleanup_worker_cgroups,
    create_crs_cgroup,
    create_run_cgroups,
    create_worker_cgroup,
    enable_oss_crs_controllers,
    generate_cgroup_setup_commands,
    generate_docker_config_commands,
    generate_worker_cgroup_name,
    get_docker_daemon_config_path,
    get_user_cgroup_base,
    get_user_service_cgroup,
    read_docker_daemon_config,
)


@pytest.fixture(autouse=True)
def mock_uid_gid(monkeypatch):
    """Ensure os.getuid and os.getgid are available and deterministic across platforms."""
    monkeypatch.setattr(os, "getuid", lambda: 1000, raising=False)
    monkeypatch.setattr(os, "getgid", lambda: 1000, raising=False)


# =====================================================================
# Base Path Utilities
# =====================================================================


def test_get_user_cgroup_base():
    base = get_user_cgroup_base()
    expected = CGROUP_FS_ROOT / "user.slice/user-1000.slice/user@1000.service/oss-crs"
    assert base == expected


def test_get_user_service_cgroup():
    service = get_user_service_cgroup()
    expected = CGROUP_FS_ROOT / "user.slice/user-1000.slice/user@1000.service"
    assert service == expected


# =====================================================================
# Docker Cgroup Driver Checks
# =====================================================================


def test_check_docker_cgroup_driver_cgroupfs():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="cgroupfs\n")
        is_cgroupfs, driver = check_docker_cgroup_driver()
        assert is_cgroupfs is True
        assert driver == "cgroupfs"
        mock_run.assert_called_once_with(
            ["docker", "info", "--format", "{{.CgroupDriver}}"],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )


def test_check_docker_cgroup_driver_systemd():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="systemd\n")
        is_cgroupfs, driver = check_docker_cgroup_driver()
        assert is_cgroupfs is False
        assert driver == "systemd"


def test_check_docker_cgroup_driver_called_process_error():
    with patch(
        "subprocess.run",
        side_effect=subprocess.CalledProcessError(1, ["docker"]),
    ):
        is_cgroupfs, driver = check_docker_cgroup_driver()
        assert is_cgroupfs is False
        assert driver == "unknown"


def test_check_docker_cgroup_driver_timeout():
    with patch(
        "subprocess.run",
        side_effect=subprocess.TimeoutExpired(["docker"], 30),
    ):
        is_cgroupfs, driver = check_docker_cgroup_driver()
        assert is_cgroupfs is False
        assert driver == "timeout"


def test_check_docker_cgroup_driver_not_found():
    with patch("subprocess.run", side_effect=FileNotFoundError()):
        is_cgroupfs, driver = check_docker_cgroup_driver()
        assert is_cgroupfs is False
        assert driver == "docker_not_found"


# =====================================================================
# Cgroup Delegation Checks
# =====================================================================


def test_check_cgroup_delegation_missing_subtree_file(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        cgroup_mod, "get_user_service_cgroup", lambda: tmp_path / "user_svc"
    )
    is_valid, missing = check_cgroup_delegation()
    assert is_valid is False
    assert sorted(missing) == ["cpuset", "memory"]


def test_check_cgroup_delegation_valid(tmp_path: Path, monkeypatch):
    user_svc = tmp_path / "user_svc"
    user_svc.mkdir(parents=True)
    (user_svc / "cgroup.subtree_control").write_text("cpuset memory io\n")
    monkeypatch.setattr(cgroup_mod, "get_user_service_cgroup", lambda: user_svc)

    is_valid, missing = check_cgroup_delegation()
    assert is_valid is True
    assert missing == []


def test_check_cgroup_delegation_partially_missing(tmp_path: Path, monkeypatch):
    user_svc = tmp_path / "user_svc"
    user_svc.mkdir(parents=True)
    (user_svc / "cgroup.subtree_control").write_text("cpuset io\n")
    monkeypatch.setattr(cgroup_mod, "get_user_service_cgroup", lambda: user_svc)

    is_valid, missing = check_cgroup_delegation()
    assert is_valid is False
    assert missing == ["memory"]


def test_check_cgroup_delegation_read_oserror(tmp_path: Path, monkeypatch):
    user_svc = tmp_path / "user_svc"
    user_svc.mkdir(parents=True)
    subtree_file = user_svc / "cgroup.subtree_control"
    subtree_file.write_text("cpuset memory\n")
    monkeypatch.setattr(cgroup_mod, "get_user_service_cgroup", lambda: user_svc)

    with patch.object(Path, "read_text", side_effect=PermissionError("denied")):
        is_valid, missing = check_cgroup_delegation()
        assert is_valid is False
        assert sorted(missing) == ["cpuset", "memory"]


# =====================================================================
# oss-crs Directory Checks
# =====================================================================


def test_check_oss_crs_directory_not_exists(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        cgroup_mod, "get_user_cgroup_base", lambda: tmp_path / "nonexistent"
    )
    exists, status = check_oss_crs_directory()
    assert exists is False
    assert status == "not_exists"


def test_check_oss_crs_directory_ok_without_subtree_file(tmp_path: Path, monkeypatch):
    base = tmp_path / "oss-crs"
    base.mkdir(parents=True)
    monkeypatch.setattr(cgroup_mod, "get_user_cgroup_base", lambda: base)

    exists, status = check_oss_crs_directory()
    assert exists is True
    assert status == "ok"


def test_check_oss_crs_directory_ok_with_subtree_file(tmp_path: Path, monkeypatch):
    base = tmp_path / "oss-crs"
    base.mkdir(parents=True)
    (base / "cgroup.subtree_control").write_text("+cpuset +memory")
    monkeypatch.setattr(cgroup_mod, "get_user_cgroup_base", lambda: base)

    exists, status = check_oss_crs_directory()
    assert exists is True
    assert status == "ok"


def test_check_oss_crs_directory_permission_denied(tmp_path: Path, monkeypatch):
    base = tmp_path / "oss-crs"
    base.mkdir(parents=True)
    (base / "cgroup.subtree_control").write_text("+cpuset +memory")
    monkeypatch.setattr(cgroup_mod, "get_user_cgroup_base", lambda: base)

    with patch.object(Path, "read_text", side_effect=PermissionError("denied")):
        exists, status = check_oss_crs_directory()
        assert exists is False
        assert status == "permission_denied"


def test_check_oss_crs_directory_os_error(tmp_path: Path, monkeypatch):
    base = tmp_path / "oss-crs"
    base.mkdir(parents=True)
    (base / "cgroup.subtree_control").write_text("+cpuset +memory")
    monkeypatch.setattr(cgroup_mod, "get_user_cgroup_base", lambda: base)

    with patch.object(Path, "read_text", side_effect=OSError("I/O failure")):
        exists, status = check_oss_crs_directory()
        assert exists is False
        assert status == "I/O failure"


# =====================================================================
# oss-crs Controllers Checks
# =====================================================================


def test_check_oss_crs_controllers_missing_file(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        cgroup_mod, "get_user_cgroup_base", lambda: tmp_path / "nonexistent"
    )
    ok, missing = check_oss_crs_controllers()
    assert ok is False
    assert sorted(missing) == ["cpuset", "memory"]


def test_check_oss_crs_controllers_valid(tmp_path: Path, monkeypatch):
    base = tmp_path / "oss-crs"
    base.mkdir(parents=True)
    (base / "cgroup.subtree_control").write_text("cpuset memory\n")
    monkeypatch.setattr(cgroup_mod, "get_user_cgroup_base", lambda: base)

    ok, missing = check_oss_crs_controllers()
    assert ok is True
    assert missing == []


def test_check_oss_crs_controllers_missing_some(tmp_path: Path, monkeypatch):
    base = tmp_path / "oss-crs"
    base.mkdir(parents=True)
    (base / "cgroup.subtree_control").write_text("memory\n")
    monkeypatch.setattr(cgroup_mod, "get_user_cgroup_base", lambda: base)

    ok, missing = check_oss_crs_controllers()
    assert ok is False
    assert missing == ["cpuset"]


def test_check_oss_crs_controllers_os_error(tmp_path: Path, monkeypatch):
    base = tmp_path / "oss-crs"
    base.mkdir(parents=True)
    (base / "cgroup.subtree_control").write_text("cpuset memory\n")
    monkeypatch.setattr(cgroup_mod, "get_user_cgroup_base", lambda: base)

    with patch.object(Path, "read_text", side_effect=OSError("Read error")):
        ok, missing = check_oss_crs_controllers()
        assert ok is False
        assert sorted(missing) == ["cpuset", "memory"]


# =====================================================================
# Docker Daemon Config & Commands
# =====================================================================


def test_get_docker_daemon_config_path():
    assert get_docker_daemon_config_path() == Path("/etc/docker/daemon.json")


def test_read_docker_daemon_config_nonexistent(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        cgroup_mod,
        "get_docker_daemon_config_path",
        lambda: tmp_path / "daemon.json",
    )
    assert read_docker_daemon_config() is None


def test_read_docker_daemon_config_valid(tmp_path: Path, monkeypatch):
    cfg_file = tmp_path / "daemon.json"
    cfg_file.write_text('{"log-driver": "json-file"}')
    monkeypatch.setattr(cgroup_mod, "get_docker_daemon_config_path", lambda: cfg_file)
    assert read_docker_daemon_config() == {"log-driver": "json-file"}


def test_read_docker_daemon_config_invalid_json(tmp_path: Path, monkeypatch):
    cfg_file = tmp_path / "daemon.json"
    cfg_file.write_text("not json")
    monkeypatch.setattr(cgroup_mod, "get_docker_daemon_config_path", lambda: cfg_file)
    assert read_docker_daemon_config() is None


def test_read_docker_daemon_config_os_error(tmp_path: Path, monkeypatch):
    cfg_file = tmp_path / "daemon.json"
    cfg_file.write_text("{}")
    monkeypatch.setattr(cgroup_mod, "get_docker_daemon_config_path", lambda: cfg_file)
    with patch.object(Path, "read_text", side_effect=OSError("read err")):
        assert read_docker_daemon_config() is None


def test_generate_docker_config_commands_new_file(tmp_path: Path, monkeypatch):
    cfg_file = tmp_path / "daemon.json"
    monkeypatch.setattr(cgroup_mod, "get_docker_daemon_config_path", lambda: cfg_file)

    cmds = generate_docker_config_commands()
    assert len(cmds) == 2
    desc1, cmd1 = cmds[0]
    assert desc1 == "Write Docker daemon configuration"
    assert "native.cgroupdriver=cgroupfs" in cmd1
    assert str(cfg_file) in cmd1

    desc2, cmd2 = cmds[1]
    assert desc2 == "Restart Docker daemon"
    assert cmd2 == "sudo systemctl restart docker"


def test_generate_docker_config_commands_existing_file(tmp_path: Path, monkeypatch):
    cfg_file = tmp_path / "daemon.json"
    cfg_file.write_text(json.dumps({"exec-opts": ["native.cgroupdriver=cgroupfs"]}))
    monkeypatch.setattr(cgroup_mod, "get_docker_daemon_config_path", lambda: cfg_file)

    cmds = generate_docker_config_commands()
    assert len(cmds) == 3
    # Check backup command is first
    assert cmds[0][0] == "Create backup of existing Docker config"
    assert f"sudo cp {cfg_file} {cfg_file}.bak" == cmds[0][1]


# =====================================================================
# Cgroup Setup & Controller Activation Commands
# =====================================================================


def test_generate_cgroup_setup_commands():
    cmds = generate_cgroup_setup_commands()
    assert len(cmds) == 4
    assert "Enable cpuset and memory controllers at user service level" in cmds[0][0]
    assert "Create oss-crs cgroup directory" in cmds[1][0]
    assert "Set ownership of oss-crs directory to 1000:1000" in cmds[2][0]
    assert "Enable cpuset and memory controllers at oss-crs level" in cmds[3][0]


def test_enable_oss_crs_controllers_success(tmp_path: Path, monkeypatch):
    base = tmp_path / "oss-crs"
    base.mkdir(parents=True)
    monkeypatch.setattr(cgroup_mod, "get_user_cgroup_base", lambda: base)

    success, msg = enable_oss_crs_controllers()
    assert success is True
    assert msg == "Controllers enabled successfully"
    assert (base / "cgroup.subtree_control").read_text() == "+cpuset +memory"


def test_enable_oss_crs_controllers_permission_error(tmp_path: Path, monkeypatch):
    base = tmp_path / "oss-crs"
    base.mkdir(parents=True)
    monkeypatch.setattr(cgroup_mod, "get_user_cgroup_base", lambda: base)

    with patch.object(Path, "write_text", side_effect=PermissionError("Denied")):
        success, msg = enable_oss_crs_controllers()
        assert success is False
        assert msg == "Permission denied writing to cgroup.subtree_control"


def test_enable_oss_crs_controllers_os_error(tmp_path: Path, monkeypatch):
    base = tmp_path / "oss-crs"
    base.mkdir(parents=True)
    monkeypatch.setattr(cgroup_mod, "get_user_cgroup_base", lambda: base)

    with patch.object(Path, "write_text", side_effect=OSError("Disk failure")):
        success, msg = enable_oss_crs_controllers()
        assert success is False
        assert msg == "Disk failure"


# =====================================================================
# Cgroup Cleanup Utilities
# =====================================================================


def test_cleanup_cgroup_nonexistent(tmp_path: Path):
    nonexistent = tmp_path / "does_not_exist"
    success, msg = cleanup_cgroup(nonexistent)
    assert success is True
    assert msg == "Cgroup does not exist"


def test_cleanup_cgroup_single_directory(tmp_path: Path):
    cg = tmp_path / "sample_cg"
    cg.mkdir()
    success, msg = cleanup_cgroup(cg)
    assert success is True
    assert f"Removed {cg}" in msg
    assert not cg.exists()


def test_cleanup_cgroup_recursive(tmp_path: Path):
    parent = tmp_path / "parent_cg"
    child1 = parent / "child_1"
    child2 = parent / "child_2"
    grandchild = child1 / "grandchild"

    grandchild.mkdir(parents=True)
    child2.mkdir(parents=True)

    success, _ = cleanup_cgroup(parent, recursive=True)
    assert success is True
    assert not parent.exists()


def test_cleanup_cgroup_skips_cgroup_prefix_dirs(tmp_path: Path):
    parent = tmp_path / "parent_cg"
    parent.mkdir()
    cgroup_dir = parent / "cgroup.special"

    with patch.object(
        cgroup_mod, "cleanup_cgroup", wraps=cgroup_mod.cleanup_cgroup
    ) as mock_cleanup:
        # Create cgroup.special and normal child
        normal_child = parent / "normal_child"
        normal_child.mkdir()
        cgroup_dir.mkdir()

        # When removing parent with rmdir patched
        with patch.object(Path, "rmdir"):
            success, _ = cleanup_cgroup(parent, recursive=True)
            assert success is True
            # normal_child was cleaned up, cgroup.special was skipped by loop
            mock_cleanup.assert_any_call(normal_child, recursive=True)
            assert not any(
                call.args[0] == cgroup_dir
                for call in mock_cleanup.call_args_list
                if call.args
            )


def test_cleanup_cgroup_child_failure(tmp_path: Path):
    parent = tmp_path / "parent_cg"
    child = parent / "child"
    child.mkdir(parents=True)

    with patch.object(Path, "rmdir", side_effect=OSError("Busy directory")):
        success, msg = cleanup_cgroup(parent, recursive=True)
        assert success is False
        assert "Failed to remove child child: Busy directory" in msg


def test_cleanup_cgroup_parent_rmdir_failure(tmp_path: Path):
    cg = tmp_path / "sample_cg"
    cg.mkdir()

    with patch.object(Path, "rmdir", side_effect=OSError("Device or resource busy")):
        success, msg = cleanup_cgroup(cg, recursive=False)
        assert success is False
        assert "Device or resource busy" in msg


def test_cleanup_worker_cgroups_nonexistent_base(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(cgroup_mod, "get_user_cgroup_base", lambda: tmp_path / "none")
    results = cleanup_worker_cgroups()
    assert results == []


def test_cleanup_worker_cgroups_with_and_without_age(tmp_path: Path, monkeypatch):
    base = tmp_path / "oss-crs"
    base.mkdir(parents=True)
    monkeypatch.setattr(cgroup_mod, "get_user_cgroup_base", lambda: base)

    # File that is not a directory should be skipped
    (base / "ignored.txt").write_text("hello")

    old_worker = base / "old_worker"
    old_worker.mkdir()
    new_worker = base / "new_worker"
    new_worker.mkdir()

    # Set mock mtimes: old_worker 1000s ago, new_worker 10s ago
    now = time.time()
    os.utime(old_worker, (now - 1000, now - 1000))
    os.utime(new_worker, (now - 10, now - 10))

    # Test age threshold: max_age_seconds = 100
    # only old_worker should be cleaned up
    results = cleanup_worker_cgroups(max_age_seconds=100)
    assert len(results) == 1
    assert results[0][0] == old_worker
    assert results[0][1] is True
    assert not old_worker.exists()
    assert new_worker.exists()

    # Test cleaning without max_age_seconds: cleans up remaining new_worker
    results2 = cleanup_worker_cgroups()
    assert len(results2) == 1
    assert results2[0][0] == new_worker
    assert results2[0][1] is True
    assert not new_worker.exists()


def test_cleanup_worker_cgroups_stat_os_error(tmp_path: Path, monkeypatch):
    base = tmp_path / "oss-crs"
    base.mkdir(parents=True)
    worker = base / "err_worker"
    worker.mkdir()
    monkeypatch.setattr(cgroup_mod, "get_user_cgroup_base", lambda: base)

    real_stat = Path.stat
    stat_call_count = 0

    def mock_stat(self, *args, **kwargs):
        nonlocal stat_call_count
        if self.name == "err_worker":
            stat_call_count += 1
            # First stat call is is_dir(); second stat call is checking mtime
            if stat_call_count > 1:
                raise OSError("stat failed")
        return real_stat(self, *args, **kwargs)

    with patch.object(Path, "stat", mock_stat):
        results = cleanup_worker_cgroups(max_age_seconds=60)
        # err_worker was skipped due to stat OSError inside try/except
        assert results == []


# =====================================================================
# Docker Cgroup Parent Format
# =====================================================================


def test_cgroup_path_for_docker():
    # If path starts with CGROUP_FS_ROOT (/sys/fs/cgroup)
    path_with_root = (
        CGROUP_FS_ROOT / "user.slice/user-1000.slice/user@1000.service/oss-crs/worker-1"
    )
    docker_cgroup = cgroup_path_for_docker(path_with_root)
    assert not docker_cgroup.startswith(str(CGROUP_FS_ROOT))
    assert docker_cgroup.endswith("worker-1")

    # If path does not start with CGROUP_FS_ROOT
    custom_path = Path("/custom/cgroup/path")
    assert cgroup_path_for_docker(custom_path) == str(custom_path)


# =====================================================================
# check_cgroup_parent_available Check Suite
# =====================================================================


def test_check_cgroup_parent_available_all_pass():
    with (
        patch(
            "oss_crs.src.cgroup.check_docker_cgroup_driver",
            return_value=(True, "cgroupfs"),
        ),
        patch(
            "oss_crs.src.cgroup.check_cgroup_delegation",
            return_value=(True, []),
        ),
        patch(
            "oss_crs.src.cgroup.check_oss_crs_directory",
            return_value=(True, "ok"),
        ),
        patch(
            "oss_crs.src.cgroup.check_oss_crs_controllers",
            return_value=(True, []),
        ),
    ):
        available, msg = check_cgroup_parent_available()
        assert available is True
        assert msg == "ok"


def test_check_cgroup_parent_available_driver_fail():
    with patch(
        "oss_crs.src.cgroup.check_docker_cgroup_driver",
        return_value=(False, "systemd"),
    ):
        available, msg = check_cgroup_parent_available()
        assert available is False
        assert "Docker using 'systemd' driver, needs 'cgroupfs'" in msg


def test_check_cgroup_parent_available_delegation_fail():
    with (
        patch(
            "oss_crs.src.cgroup.check_docker_cgroup_driver",
            return_value=(True, "cgroupfs"),
        ),
        patch(
            "oss_crs.src.cgroup.check_cgroup_delegation",
            return_value=(False, ["memory"]),
        ),
    ):
        available, msg = check_cgroup_parent_available()
        assert available is False
        assert "Cgroup delegation missing: memory" in msg


def test_check_cgroup_parent_available_dir_fail():
    with (
        patch(
            "oss_crs.src.cgroup.check_docker_cgroup_driver",
            return_value=(True, "cgroupfs"),
        ),
        patch(
            "oss_crs.src.cgroup.check_cgroup_delegation",
            return_value=(True, []),
        ),
        patch(
            "oss_crs.src.cgroup.check_oss_crs_directory",
            return_value=(False, "not_exists"),
        ),
    ):
        available, msg = check_cgroup_parent_available()
        assert available is False
        assert "oss-crs cgroup directory: not_exists" in msg


def test_check_cgroup_parent_available_controllers_fail():
    with (
        patch(
            "oss_crs.src.cgroup.check_docker_cgroup_driver",
            return_value=(True, "cgroupfs"),
        ),
        patch(
            "oss_crs.src.cgroup.check_cgroup_delegation",
            return_value=(True, []),
        ),
        patch(
            "oss_crs.src.cgroup.check_oss_crs_directory",
            return_value=(True, "ok"),
        ),
        patch(
            "oss_crs.src.cgroup.check_oss_crs_controllers",
            return_value=(False, ["cpuset"]),
        ),
    ):
        available, msg = check_cgroup_parent_available()
        assert available is False
        assert "oss-crs controllers missing: cpuset" in msg


# =====================================================================
# Worker & CRS Cgroup Creation
# =====================================================================


def test_generate_worker_cgroup_name():
    name1 = generate_worker_cgroup_name("run123", "build")
    name2 = generate_worker_cgroup_name("run123", "build")
    assert name1.startswith("run123-build-")
    assert name2.startswith("run123-build-")
    assert name1 != name2


def test_create_worker_cgroup(tmp_path: Path, monkeypatch):
    base = tmp_path / "oss-crs"
    base.mkdir(parents=True)
    monkeypatch.setattr(cgroup_mod, "get_user_cgroup_base", lambda: base)

    worker_path = create_worker_cgroup("worker-test-1")
    assert worker_path == base / "worker-test-1"
    assert worker_path.is_dir()
    subtree_file = worker_path / "cgroup.subtree_control"
    assert subtree_file.read_text() == "+cpuset +memory"


def test_create_crs_cgroup(tmp_path: Path):
    worker_path = tmp_path / "worker"
    worker_path.mkdir(parents=True)

    crs_path = create_crs_cgroup(
        worker_path=worker_path,
        crs_name="crs-alpha",
        cpuset="0-3",
        memory_bytes=4294967296,
    )
    assert crs_path == worker_path / "crs-alpha"
    assert crs_path.is_dir()

    # Resource Sandboxing & Limits Assertions
    assert (crs_path / "cpuset.cpus").read_text() == "0-3"
    assert (crs_path / "memory.max").read_text() == "4294967296"
    assert (crs_path / "memory.swap.max").read_text() == "0"
    assert (crs_path / "cgroup.subtree_control").read_text() == "+cpuset +memory"


def test_create_run_cgroups_success(tmp_path: Path, monkeypatch):
    base = tmp_path / "oss-crs"
    base.mkdir(parents=True)
    monkeypatch.setattr(cgroup_mod, "get_user_cgroup_base", lambda: base)

    crs_a = SimpleNamespace(
        name="crs-a",
        resource=SimpleNamespace(cpuset="0-1", memory="2g"),
    )
    crs_b = SimpleNamespace(
        name="crs-b",
        resource=SimpleNamespace(cpuset="2-3", memory="1024m"),
    )

    worker_path, cgroup_parents = create_run_cgroups(
        run_id="run-42",
        phase="run",
        crs_list=[crs_a, crs_b],
    )

    assert worker_path.parent == base
    assert "crs-a" in cgroup_parents
    assert "crs-b" in cgroup_parents

    # Verify per-CRS cgroups created and configured
    crs_a_path = worker_path / "crs-a"
    assert (crs_a_path / "cpuset.cpus").read_text() == "0-1"
    assert (crs_a_path / "memory.max").read_text() == str(2 * 1024 * 1024 * 1024)
    assert (crs_a_path / "memory.swap.max").read_text() == "0"

    crs_b_path = worker_path / "crs-b"
    assert (crs_b_path / "cpuset.cpus").read_text() == "2-3"
    assert (crs_b_path / "memory.max").read_text() == str(1024 * 1024 * 1024)
    assert (crs_b_path / "memory.swap.max").read_text() == "0"


def test_create_run_cgroups_missing_resource_raises(tmp_path: Path, monkeypatch):
    base = tmp_path / "oss-crs"
    base.mkdir(parents=True)
    monkeypatch.setattr(cgroup_mod, "get_user_cgroup_base", lambda: base)

    crs_no_res = SimpleNamespace(name="crs-no-res", resource=None)

    with pytest.raises(
        ValueError, match="CRS 'crs-no-res' is missing resource configuration"
    ):
        create_run_cgroups(
            run_id="run-42",
            phase="build",
            crs_list=[crs_no_res],
        )

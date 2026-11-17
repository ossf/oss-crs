# SPDX-License-Identifier: MIT
"""Resilience tests for abrupt Docker daemon disconnects, hangs, and socket failures."""

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import docker.errors
import pytest

# Ensure fcntl is mocked on Windows platforms for target/workdir imports
if "fcntl" not in sys.modules:
    try:
        import fcntl  # noqa: F401
    except ImportError:
        sys.modules["fcntl"] = MagicMock()

from oss_crs.src.cli.clean import CleanPlan, _dir_size, execute_clean_plan
from oss_crs.src.ui import MultiTaskProgress, TaskResult


@pytest.fixture
def progress() -> MultiTaskProgress:
    return MultiTaskProgress(tasks=[("test_task", lambda p: TaskResult(success=True))])


# =====================================================================
# Docker Daemon Disconnect during docker_compose_down
# =====================================================================


def test_compose_down_daemon_hang_timeout(progress: MultiTaskProgress, tmp_path: Path):
    """When docker daemon hangs during compose down, TimeoutExpired is trapped and reported."""
    compose_path = tmp_path / "docker-compose.yaml"
    compose_path.write_text("services: {}\n")

    with patch(
        "subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd=["docker", "compose"], timeout=120),
    ):
        result = progress.docker_compose_down("test-proj", compose_path)
        assert result.success is False
        assert "docker compose down timed out after 120 seconds" in (result.error or "")


def test_compose_down_daemon_socket_disconnect(
    progress: MultiTaskProgress, tmp_path: Path
):
    """When docker daemon socket is closed/unreachable, non-zero exit code and error are trapped."""
    compose_path = tmp_path / "docker-compose.yaml"
    compose_path.write_text("services: {}\n")

    error_msg = "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?"
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=1,
            stdout="",
            stderr=error_msg,
        )
        result = progress.docker_compose_down("test-proj", compose_path)
        assert result.success is False
        assert "docker compose down failed with exit code 1" in (result.error or "")
        assert "Cannot connect to the Docker daemon" in (result.error or "")


def test_compose_down_unexpected_daemon_exception(
    progress: MultiTaskProgress, tmp_path: Path
):
    """When an unexpected exception occurs communicating with docker, it does not crash teardown."""
    compose_path = tmp_path / "docker-compose.yaml"
    compose_path.write_text("services: {}\n")

    with patch(
        "subprocess.run",
        side_effect=ConnectionResetError("Connection forcibly closed by remote host"),
    ):
        result = progress.docker_compose_down("test-proj", compose_path)
        assert result.success is False
        assert "Connection forcibly closed by remote host" in (result.error or "")


def test_compose_down_best_effort_image_cleanup_resilience(
    progress: MultiTaskProgress, tmp_path: Path
):
    """Failure during post-teardown image inspection/removal does not overwrite compose down result."""
    compose_path = tmp_path / "docker-compose.yaml"
    compose_path.write_text("services: {}\n")

    call_count = 0

    def mock_subprocess_run(cmd, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        # First call is `docker compose down` which succeeds
        if call_count == 1:
            return MagicMock(returncode=0, stdout="", stderr="")
        # Subsequent image pruning / inspection call fails due to daemon disconnect
        raise OSError("Docker daemon pipe closed")

    with patch("subprocess.run", side_effect=mock_subprocess_run):
        result = progress.docker_compose_down("test-proj", compose_path)
        # Teardown should still be considered successful because best-effort image cleanup failure is ignored
        assert result.success is True


# =====================================================================
# Docker Daemon Disconnect during docker_compose_up
# =====================================================================


def test_compose_up_abrupt_disconnect(progress: MultiTaskProgress, tmp_path: Path):
    """When docker daemon disconnects mid-run during compose up, error is reported without unhandled crash."""
    compose_path = tmp_path / "docker-compose.yaml"
    compose_path.write_text("services: {}\n")

    with (
        patch.object(
            progress,
            "_get_teardown_helper_services",
            return_value=set(),
        ),
        patch.object(
            progress,
            "_start_compose_event_collection",
            return_value=([], MagicMock(), False),
        ),
        patch.object(
            progress,
            "_get_running_helper_services",
            return_value=set(),
        ),
        patch.object(
            progress,
            "run_command_with_streaming_output",
            return_value=TaskResult(
                success=False,
                error="Error: connection to docker daemon lost during stream",
            ),
        ),
        patch("subprocess.run") as mock_run,
    ):
        # Even if the emergency stop times out
        mock_run.side_effect = subprocess.TimeoutExpired(
            cmd=["docker", "compose", "stop"], timeout=60
        )

        result = progress.docker_compose_up("test-proj", compose_path)
        assert result.success is False
        assert "connection to docker daemon lost" in (result.error or "")


def test_check_failed_containers_detects_crashed_container(
    progress: MultiTaskProgress, tmp_path: Path
):
    """When a container crashes with non-zero exit code, _check_failed_containers reports it."""
    compose_path = tmp_path / "docker-compose.yaml"
    compose_path.write_text("services: {}\n")

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="fuzzer:137:crs-fuzzer_1\n",
            stderr="",
        )
        check_res = progress._check_failed_containers("test-proj", compose_path)
        assert check_res.success is False
        assert "crs-fuzzer_1 (exit 137)" in (check_res.error or "")


def test_check_failed_containers_daemon_disconnect_does_not_crash(
    progress: MultiTaskProgress, tmp_path: Path
):
    """When docker daemon disconnects during ps check, exception is caught and does not crash."""
    compose_path = tmp_path / "docker-compose.yaml"
    compose_path.write_text("services: {}\n")

    with patch("subprocess.run", side_effect=OSError("Docker daemon pipe closed")):
        check_res = progress._check_failed_containers("test-proj", compose_path)
        # Resilient design: passes through gracefully rather than terminating teardown
        assert check_res.success is True


# =====================================================================
# Docker Daemon Failure during Clean & Prune Operations
# =====================================================================


def test_clean_plan_docker_daemon_disconnect():
    """execute_clean_plan handles docker APIError / disconnect across multiple images without aborting early."""
    plan = CleanPlan(
        prepare_images=["prep-img-1", "prep-img-2"],
        run_images=["run-img-1"],
    )

    with patch("oss_crs.src.cli.clean.docker.from_env") as mock_docker:
        client = mock_docker.return_value
        # First image succeeds, second image encounters daemon APIError (e.g. 500 daemon crash), third succeeds
        client.images.remove.side_effect = [
            None,
            docker.errors.APIError("500 Server Error: daemon disconnected"),
            None,
        ]

        success = execute_clean_plan(plan)
        # Should record the failure but continue to prune remaining images
        assert success is False
        assert client.images.remove.call_count == 3


def test_dir_size_docker_daemon_crash(tmp_path: Path):
    """_dir_size returns '?' when docker daemon fails or returns error during du execution."""
    d = tmp_path / "test_dir"
    d.mkdir()

    with (
        patch.object(Path, "rglob", side_effect=PermissionError("root owned")),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(
            returncode=1, stderr="Cannot connect to docker daemon"
        )
        assert _dir_size(d) == "?"

"""Shared fixtures for integration tests."""

import subprocess
import sys
import shutil
import pytest
import yaml
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent.parent
FIXTURES_DIR = Path(__file__).parent / "fixtures"


def docker_available():
    """Check if Docker is available."""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def init_git_repo(dst):
    """Initialize a directory as a git repo with initial commit."""
    subprocess.run(["git", "init"], cwd=dst, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=dst,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"], cwd=dst, check=True, capture_output=True
    )
    subprocess.run(["git", "add", "."], cwd=dst, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"], cwd=dst, check=True, capture_output=True
    )


@pytest.fixture
def cli_runner(request):
    """Return a function to run the oss-crs CLI.

    Logs are saved to oss_crs/tests/integration/.logs/<test_name>/<call>_<command>/
    """
    test_name = request.node.name
    log_dir = Path(__file__).parent / ".logs" / test_name
    if log_dir.exists():
        shutil.rmtree(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    call_count = [0]  # mutable counter

    def run(*args, check=False, timeout=120):
        cmd = [sys.executable, "-m", "oss_crs.src.cli.crs_compose"] + list(args)
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            timeout=timeout,
        )

        # Save logs to separate files
        call_count[0] += 1
        command = args[0] if args else "unknown"
        call_dir = log_dir / f"{call_count[0]:02d}_{command}"
        call_dir.mkdir(exist_ok=True)

        (call_dir / "command.log").write_text(" ".join(cmd))
        (call_dir / "returncode.log").write_text(str(result.returncode))
        (call_dir / "stdout.log").write_text(result.stdout)
        (call_dir / "stderr.log").write_text(result.stderr)

        if check and result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode, cmd, result.stdout, result.stderr
            )
        return result

    return run


@pytest.fixture
def compose_file(tmp_dir):
    """Create a minimal compose file for testing."""
    content = {
        "run_env": "local",
        "docker_registry": "local",
        "oss_crs_infra": {"cpuset": "0-3", "memory": "8G"},
        "crs-libfuzzer": {"cpuset": "4-7", "memory": "8G"},
    }
    path = tmp_dir / "compose.yaml"
    path.write_text(yaml.dump(content))
    return path


@pytest.fixture
def work_dir(tmp_dir):
    """Create a work directory for CRS operations."""
    d = tmp_dir / "work"
    d.mkdir()
    return d

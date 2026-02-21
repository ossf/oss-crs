"""Integration tests for mock-java target."""

import json
import shutil
import pytest
from pathlib import Path

from .conftest import FIXTURES_DIR, docker_available, init_git_repo

pytestmark = [pytest.mark.integration, pytest.mark.docker]


@pytest.fixture
def mock_java_project_path():
    """Return path to the embedded mock-java OSS-Fuzz project."""
    return FIXTURES_DIR / "mock-java-project"


@pytest.fixture
def mock_java_repo_path(tmp_dir):
    """Copy embedded mock-java repo to tmp_dir and init as git repo."""
    src = FIXTURES_DIR / "mock-java-repo"
    dst = tmp_dir / "mock-java"
    shutil.copytree(src, dst)
    init_git_repo(dst)
    return dst


@pytest.mark.skipif(not docker_available(), reason="Docker not available")
def test_run_mock_java(cli_runner, mock_java_project_path, mock_java_repo_path, compose_file, work_dir):
    """Build and run mock-java target, verify seeds are produced."""
    # Build
    build_result = cli_runner(
        "build-target",
        "--compose-file", str(compose_file),
        "--work-dir", str(work_dir),
        "--target-proj-path", str(mock_java_project_path),
        "--target-repo-path", str(mock_java_repo_path),
        "--build-id", "java-run-test",
        timeout=600,
    )
    assert build_result.returncode == 0, f"build-target failed: {build_result.stderr}"

    # Run with timeout
    run_result = cli_runner(
        "run",
        "--compose-file", str(compose_file),
        "--work-dir", str(work_dir),
        "--target-proj-path", str(mock_java_project_path),
        "--target-repo-path", str(mock_java_repo_path),
        "--target-harness", "OssFuzz1",
        "--timeout", "10",
        "--build-id", "java-run-test",
        "--run-id", "java-run",
        timeout=60,
    )
    assert run_result.returncode in (0, 1), f"run --timeout did not exit cleanly: {run_result.stderr}"

    # Verify artifacts
    artifacts_result = cli_runner(
        "artifacts",
        "--compose-file", str(compose_file),
        "--work-dir", str(work_dir),
        "--target-proj-path", str(mock_java_project_path),
        "--target-repo-path", str(mock_java_repo_path),
        "--target-harness", "OssFuzz1",
        "--build-id", "java-run-test",
        "--run-id", "java-run",
    )
    assert artifacts_result.returncode == 0, f"artifacts failed: {artifacts_result.stderr}"
    artifacts = json.loads(artifacts_result.stdout)
    assert "build_id" in artifacts
    assert "run_id" in artifacts

    # Verify seeds exist for at least one CRS
    for crs_artifacts in artifacts.get("crs", {}).values():
        seed_dir = Path(crs_artifacts.get("seed", ""))
        if seed_dir.exists() and list(seed_dir.iterdir()):
            break
    else:
        pytest.fail("Expected seeds in at least one CRS artifact directory")

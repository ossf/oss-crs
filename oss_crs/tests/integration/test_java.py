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


@pytest.mark.smoke
@pytest.mark.skipif(not docker_available(), reason="Docker not available")
def test_run_mock_java(
    cli_runner, mock_java_project_path, mock_java_repo_path, compose_file, work_dir
):
    """Build and run mock-java target, verify POVs are produced."""
    # Build
    build_result = cli_runner(
        "build-target",
        "--compose-file",
        str(compose_file),
        "--work-dir",
        str(work_dir),
        "--fuzz-proj-path",
        str(mock_java_project_path),
        "--target-source-path",
        str(mock_java_repo_path),
        "--build-id",
        "java-run-test",
        timeout=600,
    )
    assert build_result.returncode == 0, f"build-target failed: {build_result.stderr}"

    # Run with early-exit (stops on first artifact)
    run_result = cli_runner(
        "run",
        "--compose-file",
        str(compose_file),
        "--work-dir",
        str(work_dir),
        "--fuzz-proj-path",
        str(mock_java_project_path),
        "--target-source-path",
        str(mock_java_repo_path),
        "--target-harness",
        "OssFuzz1",
        "--timeout",
        "600",
        "--build-id",
        "java-run-test",
        "--run-id",
        "java-run",
        "--early-exit",
        timeout=900,
    )
    # early-exit returns 0 when artifact found, non-zero on timeout
    assert run_result.returncode == 0, f"run failed: {run_result.stderr}"

    # Verify artifacts
    artifacts_result = cli_runner(
        "artifacts",
        "--compose-file",
        str(compose_file),
        "--work-dir",
        str(work_dir),
        "--fuzz-proj-path",
        str(mock_java_project_path),
        "--target-source-path",
        str(mock_java_repo_path),
        "--target-harness",
        "OssFuzz1",
        "--build-id",
        "java-run-test",
        "--run-id",
        "java-run",
    )
    assert artifacts_result.returncode == 0, (
        f"artifacts failed: {artifacts_result.stderr}"
    )
    artifacts = json.loads(artifacts_result.stdout)
    assert "build_id" in artifacts
    assert "run_id" in artifacts

    # Verify POVs exist for at least one CRS
    for crs_artifacts in artifacts.get("crs", {}).values():
        pov_dir = Path(crs_artifacts.get("pov", ""))
        if pov_dir.exists() and list(pov_dir.iterdir()):
            break
    else:
        pytest.fail("Expected POVs in at least one CRS artifact directory")

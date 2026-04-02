"""Integration tests for mock-c target."""

import json
import shutil
import pytest
from pathlib import Path

from .conftest import FIXTURES_DIR, docker_available, init_git_repo

pytestmark = [pytest.mark.integration, pytest.mark.docker]


@pytest.fixture
def mock_project_path():
    """Return path to the embedded mock-c OSS-Fuzz project."""
    return FIXTURES_DIR / "mock-c-project"


@pytest.fixture
def mock_repo_path(tmp_dir):
    """Copy embedded mock-c repo to tmp_dir and init as git repo."""
    src = FIXTURES_DIR / "mock-c-repo"
    dst = tmp_dir / "mock-c"
    shutil.copytree(src, dst)
    init_git_repo(dst)
    return dst


@pytest.mark.smoke
@pytest.mark.skipif(not docker_available(), reason="Docker not available")
def test_run_mock_c(
    cli_runner, mock_project_path, mock_repo_path, compose_file, work_dir
):
    """Build and run mock-c target, verify POVs are produced."""
    # Build
    build_result = cli_runner(
        "build-target",
        "--compose-file",
        str(compose_file),
        "--work-dir",
        str(work_dir),
        "--fuzz-proj-path",
        str(mock_project_path),
        "--target-source-path",
        str(mock_repo_path),
        "--build-id",
        "mock-c-test",
        timeout=300,
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
        str(mock_project_path),
        "--target-source-path",
        str(mock_repo_path),
        "--target-harness",
        "fuzz_parse_buffer",
        "--timeout",
        "600",
        "--build-id",
        "mock-c-test",
        "--run-id",
        "mock-c-run",
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
        str(mock_project_path),
        "--target-source-path",
        str(mock_repo_path),
        "--target-harness",
        "fuzz_parse_buffer",
        "--build-id",
        "mock-c-test",
        "--run-id",
        "mock-c-run",
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


class TestBuildTargetErrors:
    """Error handling tests for build-target (no Docker needed)."""

    def test_missing_compose_file(self, cli_runner, mock_project_path):
        """Error when compose file doesn't exist."""
        result = cli_runner(
            "build-target",
            "--compose-file",
            "/nonexistent/compose.yaml",
            "--fuzz-proj-path",
            str(mock_project_path),
        )
        assert result.returncode != 0

    def test_missing_fuzz_proj_path(self, cli_runner, compose_file, work_dir):
        """Error when target project path doesn't exist."""
        result = cli_runner(
            "build-target",
            "--compose-file",
            str(compose_file),
            "--work-dir",
            str(work_dir),
            "--fuzz-proj-path",
            "/nonexistent/project",
        )
        assert result.returncode != 0

    def test_deprecated_target_path_warns(self, cli_runner, mock_project_path):
        """Compatibility alias prints deprecation warning."""
        result = cli_runner(
            "build-target",
            "--compose-file",
            "/nonexistent/compose.yaml",
            "--target-path",
            str(mock_project_path),
        )
        assert result.returncode != 0
        assert "--target-path is deprecated" in result.stderr

    def test_deprecated_target_proj_path_warns(self, cli_runner, mock_project_path):
        """Compatibility alias prints deprecation warning."""
        result = cli_runner(
            "build-target",
            "--compose-file",
            "/nonexistent/compose.yaml",
            "--target-proj-path",
            str(mock_project_path),
        )
        assert result.returncode != 0
        assert "--target-proj-path is deprecated" in result.stderr

    def test_removed_target_repo_path_alias_fails(self, cli_runner, mock_project_path):
        """Removed alias is rejected by argparse."""
        result = cli_runner(
            "build-target",
            "--compose-file",
            "/nonexistent/compose.yaml",
            "--fuzz-proj-path",
            str(mock_project_path),
            "--target-repo-path",
            "/tmp/repo",
        )
        assert result.returncode != 0
        assert "unrecognized arguments: --target-repo-path" in result.stderr

"""Integration tests for fuzzer sidecar pattern."""

import json
import shutil
import pytest
import yaml
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


@pytest.fixture
def fuzzer_sidecar_crs_path():
    """Return path to the fuzzer-sidecar-crs fixture."""
    return FIXTURES_DIR / "fuzzer-sidecar-crs"


@pytest.fixture
def fuzzer_compose_file(tmp_dir, fuzzer_sidecar_crs_path):
    """Create compose file using fuzzer-sidecar-crs with local path."""
    content = {
        "run_env": "local",
        "docker_registry": "local",
        "oss_crs_infra": {"cpuset": "0-3", "memory": "8G"},
        "fuzzer-sidecar-crs": {
            "cpuset": "4-7",
            "memory": "8G",
            "source": {"local_path": str(fuzzer_sidecar_crs_path)},
        },
    }
    path = tmp_dir / "compose.yaml"
    path.write_text(yaml.dump(content))
    return path


@pytest.mark.skipif(not docker_available(), reason="Docker not available")
def test_fuzzer_sidecar_finds_crash(
    cli_runner, mock_project_path, mock_repo_path, fuzzer_compose_file, work_dir
):
    """Test that fuzzer sidecar CRS can find crashes in mock-c target."""
    # Build target
    build_result = cli_runner(
        "build-target",
        "--compose-file", str(fuzzer_compose_file),
        "--work-dir", str(work_dir),
        "--target-proj-path", str(mock_project_path),
        "--target-repo-path", str(mock_repo_path),
        "--build-id", "fuzzer-sidecar-test",
        timeout=300,
    )
    assert build_result.returncode == 0, f"build-target failed: {build_result.stderr}"

    # Run with timeout - fuzzer should find crash quickly
    run_result = cli_runner(
        "run",
        "--compose-file", str(fuzzer_compose_file),
        "--work-dir", str(work_dir),
        "--target-proj-path", str(mock_project_path),
        "--target-repo-path", str(mock_repo_path),
        "--target-harness", "fuzz_parse_buffer",
        "--timeout", "60",
        "--build-id", "fuzzer-sidecar-test",
        "--run-id", "fuzzer-sidecar-run",
        timeout=120,
    )
    # Allow return codes 0 or 1 (timeout is acceptable)
    assert run_result.returncode in (0, 1), f"run failed: {run_result.stderr}"

    # Verify artifacts
    artifacts_result = cli_runner(
        "artifacts",
        "--compose-file", str(fuzzer_compose_file),
        "--work-dir", str(work_dir),
        "--target-proj-path", str(mock_project_path),
        "--target-repo-path", str(mock_repo_path),
        "--target-harness", "fuzz_parse_buffer",
        "--build-id", "fuzzer-sidecar-test",
        "--run-id", "fuzzer-sidecar-run",
    )
    assert artifacts_result.returncode == 0, f"artifacts failed: {artifacts_result.stderr}"
    artifacts = json.loads(artifacts_result.stdout)
    assert "build_id" in artifacts
    assert "run_id" in artifacts

    # Verify POVs exist for the fuzzer-sidecar-crs
    for crs_name, crs_artifacts in artifacts.get("crs", {}).items():
        pov_dir = Path(crs_artifacts.get("pov", ""))
        if pov_dir.exists() and list(pov_dir.iterdir()):
            print(f"Found POVs in {crs_name}: {list(pov_dir.iterdir())}")
            break
    else:
        pytest.fail("Expected POVs in fuzzer-sidecar-crs artifact directory")

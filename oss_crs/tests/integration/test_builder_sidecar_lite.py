"""Integration test: builder-sidecar-lite (patch verify: build + rebuild + POV + test).

Exercises the patch verification pipeline with mock-c:
  prepare → build-target --incremental-build → run (with --diff and --pov-dir)

The CRS patcher runs verify_patch inside its container, which validates:
  1. POV crashes on original (unpatched) build
  2. Patch applies and rebuild succeeds
  3. POV no longer crashes after patch
  4. Functionality tests pass
"""

import shutil
import yaml
import pytest

from .conftest import FIXTURES_DIR, docker_available, init_git_repo

pytestmark = [pytest.mark.integration, pytest.mark.docker]

CRS_FIXTURE = FIXTURES_DIR / "builder-sidecar-lite"


@pytest.fixture
def mock_repo(tmp_dir):
    """Copy mock-c repo to a temp dir and init as git repo."""
    dst = tmp_dir / "mock-c"
    shutil.copytree(FIXTURES_DIR / "mock-c-repo", dst)
    init_git_repo(dst)
    return dst


@pytest.fixture
def sidecar_lite_compose(tmp_dir):
    """Generate a compose.yaml for builder-sidecar-lite."""
    content = {
        "run_env": "local",
        "docker_registry": "local",
        "oss_crs_infra": {"cpuset": "0-3", "memory": "8G"},
        "builder-sidecar-lite": {
            "source": {"local_path": str(CRS_FIXTURE)},
            "cpuset": "0-15",
            "memory": "32G",
        },
    }
    path = tmp_dir / "compose.yaml"
    path.write_text(yaml.dump(content))
    return path


@pytest.mark.skipif(not docker_available(), reason="Docker not available")
def test_builder_sidecar_lite(cli_runner, sidecar_lite_compose, mock_repo):
    """Lite sidecar E2E: patch verify — build, rebuild, POV, test."""
    proj = str(FIXTURES_DIR / "mock-c-project")
    repo = str(mock_repo)
    patch = str(FIXTURES_DIR / "mock-c-patches" / "patch_0.diff")
    pov_dir = str(FIXTURES_DIR / "mock-c-povs")
    compose = str(sidecar_lite_compose)

    result = cli_runner(
        "prepare",
        "--compose-file", compose,
        timeout=120,
    )
    assert result.returncode == 0, f"prepare failed:\n{result.stderr[-2000:]}"

    result = cli_runner(
        "build-target",
        "--compose-file", compose,
        "--fuzz-proj-path", proj,
        "--target-source-path", repo,
        "--incremental-build",
        "--build-id", "sidecar-lite-e2e",
        timeout=600,
    )
    assert result.returncode == 0, f"build-target failed:\n{result.stderr[-2000:]}"

    result = cli_runner(
        "run",
        "--compose-file", compose,
        "--fuzz-proj-path", proj,
        "--target-source-path", repo,
        "--target-harness", "fuzz_parse_buffer",
        "--diff", patch,
        "--incremental-build",
        "--build-id", "sidecar-lite-e2e",
        "--run-id", "sidecar-lite-e2e",
        "--pov-dir", pov_dir,
        timeout=600,
    )
    assert result.returncode == 0, (
        f"run failed (verify_patch did not pass):\n{result.stderr[-3000:]}"
    )

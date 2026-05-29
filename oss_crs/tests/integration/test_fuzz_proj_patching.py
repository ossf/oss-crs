# SPDX-License-Identifier: MIT
"""Integration test: fuzz-proj patching (build-project --fuzz-proj).

Exercises the fuzz-proj patch pipeline with mock-c:
  prepare → build-target → run (CRS applies fuzz-proj diff, verifies sentinel in rebuild output)

The CRS applies a bundled fuzz-proj diff via build_project(..., fuzz_proj_patch_path=...),
which triggers a full image rebuild from the patched OSS-Fuzz project directory.
The patched build.sh echoes a sentinel string to stdout; the CRS verifies it in stdout.log.
"""

import shutil

import pytest
import yaml

from .conftest import FIXTURES_DIR, docker_available, init_git_repo

pytestmark = [pytest.mark.integration, pytest.mark.docker]

CRS_FIXTURE = FIXTURES_DIR / "builder-sidecar-fuzz-proj"


@pytest.fixture
def mock_repo(tmp_dir):
    """Copy mock-c repo to a temp dir and init as git repo."""
    dst = tmp_dir / "mock-c"
    shutil.copytree(FIXTURES_DIR / "mock-c-repo", dst)
    init_git_repo(dst)
    return dst


@pytest.fixture
def fuzz_proj_compose(tmp_dir):
    """Generate a compose.yaml for builder-sidecar-fuzz-proj."""
    content = {
        "run_env": "local",
        "docker_registry": "local",
        "oss_crs_infra": {"cpuset": "0-3", "memory": "8G"},
        "builder-sidecar-fuzz-proj": {
            "source": {"local_path": str(CRS_FIXTURE)},
            "cpuset": "0-3",
            "memory": "8G",
        },
    }
    path = tmp_dir / "compose.yaml"
    path.write_text(yaml.dump(content))
    return path


@pytest.mark.skipif(not docker_available(), reason="Docker not available")
def test_fuzz_proj_patching(cli_runner, fuzz_proj_compose, mock_repo):
    """Fuzz-proj patching E2E: image rebuilt from patched fuzz proj, sentinel verified.

    The CRS (apply_fuzz_proj_patch) applies a bundled diff against mock-c-project/build.sh
    that adds `echo "fuzz_proj_patched_sentinel"`. After the sidecar rebuilds the image
    and runs an ephemeral container, the CRS checks response_dir/stdout.log for the sentinel
    string, proving the patched image was actually used.
    """
    proj = str(FIXTURES_DIR / "mock-c-project")
    repo = str(mock_repo)
    compose = str(fuzz_proj_compose)

    result = cli_runner(
        "prepare",
        "--compose-file",
        compose,
        timeout=120,
    )
    assert result.returncode == 0, f"prepare failed:\n{result.stdout[-2000:]}"

    result = cli_runner(
        "build-target",
        "--compose-file",
        compose,
        "--fuzz-proj-path",
        proj,
        "--target-source-path",
        repo,
        "--build-id",
        "fuzz-proj-e2e",
        timeout=600,
    )
    assert result.returncode == 0, (
        f"build-target failed:\n{result.stdout[-3000:]}"
    )

    result = cli_runner(
        "run",
        "--compose-file",
        compose,
        "--fuzz-proj-path",
        proj,
        "--target-source-path",
        repo,
        "--build-id",
        "fuzz-proj-e2e",
        "--run-id",
        "fuzz-proj-e2e",
        timeout=300,
    )
    assert result.returncode == 0, (
        f"run failed (apply_fuzz_proj_patch did not pass):\n{result.stdout[-3000:]}"
    )

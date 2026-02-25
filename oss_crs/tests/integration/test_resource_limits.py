"""Integration tests for resource limit enforcement."""

import re
import shutil
from pathlib import Path

import pytest
import yaml

from .conftest import FIXTURES_DIR, docker_available, init_git_repo

pytestmark = [pytest.mark.integration, pytest.mark.docker]


def _memory_to_bytes(memory: str) -> int:
    match = re.fullmatch(r"(\d+)([KMG])", memory.strip().upper())
    if not match:
        raise ValueError(f"Unsupported test memory format: {memory}")
    value = int(match.group(1))
    unit = match.group(2)
    if unit == "K":
        return value * 1024
    if unit == "M":
        return value * 1024 * 1024
    return value * 1024 * 1024 * 1024


def _extract_field(text: str, key: str) -> str:
    for line in text.splitlines():
        marker = f"{key}:"
        if marker in line:
            return line.split(marker, 1)[1].strip()
    raise AssertionError(f"Missing field '{key}' in text:\n{text}")


@pytest.fixture
def mock_project_path():
    return FIXTURES_DIR / "mock-c-project"


@pytest.fixture
def mock_repo_path(tmp_dir):
    src = FIXTURES_DIR / "mock-c-repo"
    dst = tmp_dir / "mock-c"
    shutil.copytree(src, dst)
    init_git_repo(dst)
    return dst


@pytest.fixture
def dummy_compose_file(tmp_dir):
    content = {
        "run_env": "local",
        "docker_registry": "local",
        "oss_crs_infra": {"cpuset": "0", "memory": "256M"},
        "dummy-crs": {
            "source": {"local_path": str(FIXTURES_DIR / "dummy-crs")},
            "cpuset": "0-1",
            "memory": "192M",
        },
    }
    path = tmp_dir / "compose.yaml"
    path.write_text(yaml.dump(content))
    return path


@pytest.mark.skipif(not docker_available(), reason="Docker not available")
def test_build_target_applies_configured_limits(
    cli_runner,
    mock_project_path,
    mock_repo_path,
    dummy_compose_file,
    work_dir,
):
    build_id = "resource-build"
    expected_memory = _memory_to_bytes("192M")

    result = cli_runner(
        "build-target",
        "--compose-file",
        str(dummy_compose_file),
        "--work-dir",
        str(work_dir),
        "--target-proj-path",
        str(mock_project_path),
        "--target-repo-path",
        str(mock_repo_path),
        "--build-id",
        build_id,
        timeout=300,
    )
    assert result.returncode == 0, f"build-target failed: {result.stderr}"

    build_info_files = list(work_dir.rglob("build_info.txt"))
    assert build_info_files, "Expected dummy builder output build_info.txt"

    build_info = build_info_files[0].read_text()
    memory_max = int(_extract_field(build_info, "memory_max"))
    cpuset = _extract_field(build_info, "cpuset")

    assert memory_max == expected_memory
    assert cpuset == "0-1"


@pytest.mark.skipif(not docker_available(), reason="Docker not available")
def test_run_enforces_memory_limits_in_active_mode(
    cli_runner,
    mock_project_path,
    mock_repo_path,
    dummy_compose_file,
    work_dir,
):
    build_id = "resource-build"
    run_id = "resource-run"
    expected_memory = _memory_to_bytes("192M")

    build_result = cli_runner(
        "build-target",
        "--compose-file",
        str(dummy_compose_file),
        "--work-dir",
        str(work_dir),
        "--target-proj-path",
        str(mock_project_path),
        "--target-repo-path",
        str(mock_repo_path),
        "--build-id",
        build_id,
        timeout=300,
    )
    assert build_result.returncode == 0, f"build-target failed: {build_result.stderr}"

    run_result = cli_runner(
        "run",
        "--compose-file",
        str(dummy_compose_file),
        "--work-dir",
        str(work_dir),
        "--target-proj-path",
        str(mock_project_path),
        "--target-repo-path",
        str(mock_repo_path),
        "--target-harness",
        "fuzz_parse_buffer",
        "--build-id",
        build_id,
        "--run-id",
        run_id,
        "--timeout",
        "25",
        timeout=90,
    )
    assert run_result.returncode in (0, 1), f"run failed unexpectedly: {run_result.stderr}"

    memory_logs = sorted(work_dir.rglob("*_memory_test.txt"))
    assert memory_logs, "Expected memory logs from dummy CRS modules"

    uses_cgroup_parent = "Created cgroups at:" in run_result.stdout
    observed_max_values: list[str] = []

    for log_path in memory_logs:
        text = log_path.read_text()
        assert "env_memory_limit: \"192M\"" in text
        assert "ALLOCATED chunk" in text
        observed_max_values.append(_extract_field(text, "memory.max"))

    if uses_cgroup_parent:
        assert any(value == "max" for value in observed_max_values)
    else:
        numeric = [int(value) for value in observed_max_values if value.isdigit()]
        assert numeric, f"Expected numeric memory.max values, got: {observed_max_values}"
        assert all(value == expected_memory for value in numeric)


@pytest.mark.skipif(not docker_available(), reason="Docker not available")
def test_containers_within_crs_share_memory_limit(
    cli_runner,
    mock_project_path,
    mock_repo_path,
    dummy_compose_file,
    work_dir,
):
    """Multiple containers within a CRS should share the memory limit.

    The dummy-crs has 3 containers (fuzzer, analyzer, monitor) each trying
    to allocate 40MB chunks. With a 192M CRS limit and cgroup-parent mode:
    - If limits were per-container: each could allocate ~4 chunks (160MB) = 480MB total
    - If limits are shared: total across all containers should be ~192MB

    This test verifies the shared limit by checking that:
    1. At least one container hit OOM (couldn't allocate all 30 chunks)
    2. The combined allocation is bounded by the CRS limit
    """
    build_id = "shared-mem-build"
    run_id = "shared-mem-run"
    crs_memory_limit_mb = 192

    build_result = cli_runner(
        "build-target",
        "--compose-file",
        str(dummy_compose_file),
        "--work-dir",
        str(work_dir),
        "--target-proj-path",
        str(mock_project_path),
        "--target-repo-path",
        str(mock_repo_path),
        "--build-id",
        build_id,
        timeout=300,
    )
    assert build_result.returncode == 0, f"build-target failed: {build_result.stderr}"

    # Longer timeout to let containers compete for memory
    run_result = cli_runner(
        "run",
        "--compose-file",
        str(dummy_compose_file),
        "--work-dir",
        str(work_dir),
        "--target-proj-path",
        str(mock_project_path),
        "--target-repo-path",
        str(mock_repo_path),
        "--target-harness",
        "fuzz_parse_buffer",
        "--build-id",
        build_id,
        "--run-id",
        run_id,
        "--timeout",
        "60",
        timeout=180,
    )
    assert run_result.returncode in (0, 1), f"run failed unexpectedly: {run_result.stderr}"

    uses_cgroup_parent = "Created cgroups at:" in run_result.stdout

    # Parse all memory logs from containers in this CRS
    memory_logs = sorted(work_dir.rglob("*_memory_test.txt"))
    assert len(memory_logs) >= 2, f"Expected logs from multiple containers, got {len(memory_logs)}"

    container_results = []
    for log_path in memory_logs:
        parsed = _parse_memory_log(log_path)
        parsed["container"] = log_path.stem.replace("_memory_test", "")
        container_results.append(parsed)

    # Calculate totals
    total_allocated_mb = sum(r["final_allocated_mb"] or 0 for r in container_results)
    containers_with_oom = [r for r in container_results if r["hit_oom"]]
    containers_without_oom = [r for r in container_results if not r["hit_oom"]]

    if uses_cgroup_parent:
        # With cgroup-parent, containers share the CRS memory limit
        # At least one container should have hit OOM since 3 containers
        # each trying to allocate up to 1200MB (30 * 40MB) exceeds 192MB
        assert containers_with_oom, (
            f"With shared 192M limit, at least one container should OOM. "
            f"Results: {[(r['container'], r['final_allocated_mb'], r['hit_oom']) for r in container_results]}"
        )

        # Total allocation should be roughly bounded by CRS limit
        # Allow some overhead for container runtime (~50MB buffer)
        max_expected_total = crs_memory_limit_mb + 50
        assert total_allocated_mb <= max_expected_total, (
            f"Total allocation ({total_allocated_mb}MB) exceeds CRS limit ({crs_memory_limit_mb}MB) + buffer. "
            f"This suggests containers are NOT sharing the limit. "
            f"Results: {[(r['container'], r['final_allocated_mb']) for r in container_results]}"
        )
    else:
        # Without cgroup-parent, each container has its own limit
        # Containers might still OOM based on per-container limits
        # Just verify we got logs and some allocation happened
        assert total_allocated_mb > 0, "Expected some memory allocation"


@pytest.fixture
def no_resource_compose_file(tmp_dir):
    """Create a compose file WITHOUT resource limits specified."""
    content = {
        "run_env": "local",
        "docker_registry": "local",
        "oss_crs_infra": {"cpuset": "0", "memory": "256M"},
        "dummy-crs": {
            "source": {"local_path": str(FIXTURES_DIR / "dummy-crs")},
            # No cpuset or memory specified
        },
    }
    path = tmp_dir / "compose.yaml"
    path.write_text(yaml.dump(content))
    return path


@pytest.mark.skipif(not docker_available(), reason="Docker not available")
def test_build_target_no_limits_when_unspecified(
    cli_runner,
    mock_project_path,
    mock_repo_path,
    no_resource_compose_file,
    work_dir,
):
    """When resource limits are not specified, Docker should not have cpuset/mem_limit."""
    build_id = "no-limits-build"

    result = cli_runner(
        "build-target",
        "--compose-file",
        str(no_resource_compose_file),
        "--work-dir",
        str(work_dir),
        "--target-proj-path",
        str(mock_project_path),
        "--target-repo-path",
        str(mock_repo_path),
        "--build-id",
        build_id,
        timeout=300,
    )
    assert result.returncode == 0, f"build-target failed: {result.stderr}"

    # Check that the build_info.txt shows no explicit limits
    build_info_files = list(work_dir.rglob("build_info.txt"))
    assert build_info_files, "Expected dummy builder output build_info.txt"

    build_info = build_info_files[0].read_text()
    # memory.max should be "max" (unlimited) when no limit is set
    memory_max = _extract_field(build_info, "memory_max")
    assert memory_max == "max", f"Expected 'max' for unlimited memory, got: {memory_max}"


def _parse_memory_log(log_path) -> dict:
    """Parse a dummy-crs memory test log file.

    Returns dict with:
        - cgroup_path: str
        - memory_max: int (bytes) or "max" for unlimited
        - env_memory_limit: str (e.g., "128M")
        - final_allocated_mb: int (MB allocated before OOM or completion)
        - hit_oom: bool
    """
    text = log_path.read_text()
    result = {
        "cgroup_path": None,
        "memory_max": None,
        "env_memory_limit": None,
        "final_allocated_mb": None,
        "hit_oom": False,
    }

    for line in text.splitlines():
        if "cgroup_path:" in line:
            result["cgroup_path"] = line.split("cgroup_path:", 1)[1].strip()
        elif "memory.max:" in line:
            val = line.split("memory.max:", 1)[1].strip()
            if val == "max":
                result["memory_max"] = "max"
            elif val.isdigit():
                result["memory_max"] = int(val)
        elif "env_memory_limit:" in line:
            result["env_memory_limit"] = line.split("env_memory_limit:", 1)[1].strip().strip('"')
        elif "FINAL: allocated" in line:
            # Parse "FINAL: allocated X chunks = YMB before OOM" or "... (no OOM)"
            result["hit_oom"] = "before OOM" in line
            match = re.search(r"= (\d+)MB", line)
            if match:
                result["final_allocated_mb"] = int(match.group(1))

    return result


def _get_crs_name_from_path(log_path) -> str | None:
    """Extract CRS name from log file path.

    Log paths look like: .../crs-alpha/.../fuzzer_memory_test.txt
    """
    parts = log_path.parts
    for part in parts:
        if part.startswith("crs-"):
            return part
    return None


@pytest.fixture
def multi_crs_compose_file(tmp_dir):
    """Create a compose file with multiple CRSs having different resource limits."""
    content = {
        "run_env": "local",
        "docker_registry": "local",
        "oss_crs_infra": {"cpuset": "0", "memory": "256M"},
        "crs-alpha": {
            "source": {"local_path": str(FIXTURES_DIR / "dummy-crs")},
            "cpuset": "0-1",
            "memory": "128M",
        },
        "crs-beta": {
            "source": {"local_path": str(FIXTURES_DIR / "dummy-crs")},
            "cpuset": "2-3",
            "memory": "256M",
        },
    }
    path = tmp_dir / "compose.yaml"
    path.write_text(yaml.dump(content))
    return path


@pytest.mark.skipif(not docker_available(), reason="Docker not available")
def test_multi_crs_different_resource_limits(
    cli_runner,
    mock_project_path,
    mock_repo_path,
    multi_crs_compose_file,
    work_dir,
):
    """Multiple CRSs should each have their own resource limits.

    crs-alpha: 128M limit -> should OOM around 80-120MB allocated (40MB chunks)
    crs-beta:  256M limit -> should allocate more than crs-alpha before OOM
    """
    build_id = "multi-crs-build"
    run_id = "multi-crs-run"

    build_result = cli_runner(
        "build-target",
        "--compose-file",
        str(multi_crs_compose_file),
        "--work-dir",
        str(work_dir),
        "--target-proj-path",
        str(mock_project_path),
        "--target-repo-path",
        str(mock_repo_path),
        "--build-id",
        build_id,
        timeout=300,
    )
    assert build_result.returncode == 0, f"build-target failed: {build_result.stderr}"

    run_result = cli_runner(
        "run",
        "--compose-file",
        str(multi_crs_compose_file),
        "--work-dir",
        str(work_dir),
        "--target-proj-path",
        str(mock_project_path),
        "--target-repo-path",
        str(mock_repo_path),
        "--target-harness",
        "fuzz_parse_buffer",
        "--build-id",
        build_id,
        "--run-id",
        run_id,
        "--timeout",
        "60",
        timeout=180,
    )
    assert run_result.returncode in (0, 1), f"run failed unexpectedly: {run_result.stderr}"

    # Parse all memory test logs
    memory_logs = sorted(work_dir.rglob("*_memory_test.txt"))
    assert memory_logs, "Expected memory test logs from dummy-crs containers"

    # Group logs by CRS
    crs_results: dict[str, list[dict]] = {}
    for log_path in memory_logs:
        crs_name = _get_crs_name_from_path(log_path)
        if crs_name:
            parsed = _parse_memory_log(log_path)
            parsed["log_path"] = str(log_path)
            crs_results.setdefault(crs_name, []).append(parsed)

    # We should have logs from both CRSs
    assert "crs-alpha" in crs_results, f"Missing logs from crs-alpha. Found: {list(crs_results.keys())}"
    assert "crs-beta" in crs_results, f"Missing logs from crs-beta. Found: {list(crs_results.keys())}"

    # Get max allocation for each CRS (across all containers)
    alpha_max_alloc = max(
        (r["final_allocated_mb"] or 0 for r in crs_results["crs-alpha"]),
        default=0
    )
    beta_max_alloc = max(
        (r["final_allocated_mb"] or 0 for r in crs_results["crs-beta"]),
        default=0
    )

    # Check memory.max values seen by containers
    alpha_memory_max = [r["memory_max"] for r in crs_results["crs-alpha"] if r["memory_max"]]
    beta_memory_max = [r["memory_max"] for r in crs_results["crs-beta"] if r["memory_max"]]

    # Verify limits are different and correct
    # crs-alpha should see ~128MB limit (134217728 bytes)
    # crs-beta should see ~256MB limit (268435456 bytes)
    uses_cgroup_parent = "Created cgroups at:" in run_result.stdout

    if uses_cgroup_parent:
        # With cgroup-parent, containers see memory.max from their cgroup
        # alpha: 128M = 134217728 bytes, beta: 256M = 268435456 bytes
        for mem_max in alpha_memory_max:
            if mem_max != "max":
                assert mem_max <= 150 * 1024 * 1024, f"crs-alpha memory.max too high: {mem_max}"

        for mem_max in beta_memory_max:
            if mem_max != "max":
                assert mem_max <= 300 * 1024 * 1024, f"crs-beta memory.max too high: {mem_max}"
                assert mem_max > 150 * 1024 * 1024, f"crs-beta memory.max too low: {mem_max}"
    else:
        # Without cgroup-parent, check env vars
        alpha_env = [r["env_memory_limit"] for r in crs_results["crs-alpha"]]
        beta_env = [r["env_memory_limit"] for r in crs_results["crs-beta"]]
        assert any("128" in (e or "") for e in alpha_env), f"crs-alpha should have 128M limit: {alpha_env}"
        assert any("256" in (e or "") for e in beta_env), f"crs-beta should have 256M limit: {beta_env}"

    # The key assertion: crs-beta (256M) should be able to allocate more than crs-alpha (128M)
    # With 40MB chunks: alpha ~2-3 chunks (80-120MB), beta ~4-6 chunks (160-240MB)
    if alpha_max_alloc > 0 and beta_max_alloc > 0:
        assert beta_max_alloc >= alpha_max_alloc, (
            f"crs-beta (256M limit) should allocate >= crs-alpha (128M limit). "
            f"Got alpha={alpha_max_alloc}MB, beta={beta_max_alloc}MB"
        )

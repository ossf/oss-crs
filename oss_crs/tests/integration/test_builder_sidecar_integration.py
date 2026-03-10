"""Integration test for host-side builder sidecar harness.

Default behavior runs only the embedded mock C/Java OSS-Fuzz projects.

To test arbitrary OSS-Fuzz-compatible projects, provide either:
- `OSS_CRS_BUILDER_CASES_YAML=/path/to/cases.yaml`
- or the single-case env trio:
  - `OSS_CRS_BUILDER_FUZZ_PROJ_PATH`
  - `OSS_CRS_BUILDER_TARGET_SOURCE_PATH`
  - `OSS_CRS_BUILDER_TARGET_HARNESS`
  - optional: `OSS_CRS_BUILDER_CASE_NAME`
"""

import csv
import fcntl
import hashlib
import os
import socket
import subprocess
import tarfile
import time
from pathlib import Path

import pytest
import yaml

from oss_crs.src.target import Target
from oss_crs.tests.integration.conftest import FIXTURES_DIR, docker_available, init_git_repo
from oss_crs.tests.integration.support.builder_client import BuilderClient

pytestmark = [pytest.mark.integration, pytest.mark.docker]

REPO_ROOT = Path(__file__).parent.parent.parent.parent
BUILDER_HARNESS_CRS = FIXTURES_DIR / "builder-harness-crs"
DEFAULT_SUMMARY_CSV = Path(__file__).parent / ".logs" / "builder_sidecar_summary.csv"
CASES_YAML_ENV = "OSS_CRS_BUILDER_CASES_YAML"
FUZZ_PROJ_ENV = "OSS_CRS_BUILDER_FUZZ_PROJ_PATH"
TARGET_SOURCE_ENV = "OSS_CRS_BUILDER_TARGET_SOURCE_PATH"
TARGET_HARNESS_ENV = "OSS_CRS_BUILDER_TARGET_HARNESS"
CASE_NAME_ENV = "OSS_CRS_BUILDER_CASE_NAME"
SUMMARY_FIELDS = [
    "benchmark",
    "language",
    "harness",
    "source_tarball",
    "build_target_rc",
    "snapshot_tag",
    "snapshot_out_exists",
    "builder_image_built",
    "builder_healthy",
    "base_test_exit_code",
    "patch_build_exit_code",
    "patch_build_id",
    "patched_test_exit_code",
    "overall_success",
    "duration_sec",
    "error",
]


def _summary_csv_path() -> Path:
    raw = os.environ.get("OSS_CRS_BUILDER_SUMMARY_CSV")
    if raw:
        return Path(raw).expanduser().resolve()
    return DEFAULT_SUMMARY_CSV.resolve()


def _load_language(project_yaml_path: Path) -> str:
    data = yaml.safe_load(project_yaml_path.read_text()) or {}
    return str(data.get("language", "unknown"))


def _normalize_case(raw_case: dict[str, object], index: int) -> dict[str, str]:
    case_name = str(raw_case.get("benchmark") or raw_case.get("name") or f"case-{index}")
    project_path = Path(str(raw_case["project"])).expanduser().resolve()
    repo_path = Path(str(raw_case["repo_path"])).expanduser().resolve()
    harness = str(raw_case["harness"])
    if not project_path.exists():
        raise RuntimeError(f"Case '{case_name}' has invalid project path: {project_path}")
    if not repo_path.exists():
        raise RuntimeError(f"Case '{case_name}' has invalid repo path: {repo_path}")
    project_yaml = project_path / "project.yaml"
    if not project_yaml.exists():
        raise RuntimeError(f"Case '{case_name}' is missing project.yaml: {project_yaml}")

    source_tarball = str(raw_case.get("source_tarball") or repo_path.name)
    language = str(raw_case.get("language") or _load_language(project_yaml))
    return {
        "benchmark": case_name,
        "project": str(project_path),
        "harness": harness,
        "language": language,
        "source_tarball": source_tarball,
        "repo_path": str(repo_path),
    }


def _default_mock_cases() -> list[dict[str, str]]:
    return [
        {
            "benchmark": "builder_mock_c_case",
            "project": str((FIXTURES_DIR / "mock-c-project").resolve()),
            "repo_path": str((FIXTURES_DIR / "mock-c-repo").resolve()),
            "harness": "fuzz_parse_buffer",
            "language": "c",
            "source_tarball": "embedded-mock-c-repo",
        },
        {
            "benchmark": "builder_mock_java_case",
            "project": str((FIXTURES_DIR / "mock-java-project").resolve()),
            "repo_path": str((FIXTURES_DIR / "mock-java-repo").resolve()),
            "harness": "OssFuzz1",
            "language": "jvm",
            "source_tarball": "embedded-mock-java-repo",
        },
    ]


def _load_cases_from_yaml(path: Path) -> list[dict[str, str]]:
    data = yaml.safe_load(path.read_text()) or {}
    raw_cases = data.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise RuntimeError(f"{path} must define a non-empty 'cases' list")
    return [_normalize_case(raw_case, index) for index, raw_case in enumerate(raw_cases, start=1)]


def _load_single_case_from_env() -> list[dict[str, str]]:
    fuzz_proj = os.environ.get(FUZZ_PROJ_ENV, "").strip()
    target_source = os.environ.get(TARGET_SOURCE_ENV, "").strip()
    target_harness = os.environ.get(TARGET_HARNESS_ENV, "").strip()
    provided = [bool(fuzz_proj), bool(target_source), bool(target_harness)]
    if not any(provided):
        return []
    if not all(provided):
        raise RuntimeError(
            f"{FUZZ_PROJ_ENV}, {TARGET_SOURCE_ENV}, and {TARGET_HARNESS_ENV} must all be set together."
        )
    return [
        _normalize_case(
            {
                "benchmark": os.environ.get(CASE_NAME_ENV, "").strip() or Path(fuzz_proj).name,
                "project": fuzz_proj,
                "repo_path": target_source,
                "harness": target_harness,
            },
            1,
        )
    ]


def _discover_cases() -> list[dict[str, str]]:
    cases_yaml = os.environ.get(CASES_YAML_ENV, "").strip()
    if cases_yaml:
        return _load_cases_from_yaml(Path(cases_yaml).expanduser().resolve())

    single_case = _load_single_case_from_env()
    if single_case:
        return single_case

    return [_normalize_case(case, index) for index, case in enumerate(_default_mock_cases(), start=1)]


TEST_CASES = _discover_cases()


def _append_summary_row(summary_path: Path, row: dict[str, str]) -> None:
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with summary_path.open("a+", newline="", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        f.seek(0, os.SEEK_END)
        writer = csv.DictWriter(f, fieldnames=SUMMARY_FIELDS)
        if f.tell() == 0:
            writer.writeheader()
        writer.writerow(row)
        f.flush()
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def _prepare_repo_path(repo_path: Path, dest_dir: Path) -> Path:
    if repo_path.is_file() and repo_path.name.endswith(".tar.gz"):
        dest_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(repo_path, "r:gz") as tar:
            tar.extractall(dest_dir, filter="data")

        source_name = repo_path.name[:-7]
        extracted_repo_path = dest_dir / source_name
        if not extracted_repo_path.exists():
            raise FileNotFoundError(f"Extracted source directory not found: {extracted_repo_path}")
        repo_path = extracted_repo_path

    if not repo_path.is_dir():
        raise FileNotFoundError(f"Repo path is neither a directory nor a tarball: {repo_path}")

    prepared_repo_path = dest_dir / repo_path.name
    prepared_repo_path.parent.mkdir(parents=True, exist_ok=True)
    if prepared_repo_path.exists():
        subprocess.run(["rm", "-rf", str(prepared_repo_path)], check=True)
    subprocess.run(["cp", "-a", str(repo_path), str(prepared_repo_path)], check=True)
    if not (prepared_repo_path / ".git").exists():
        init_git_repo(prepared_repo_path)
    return prepared_repo_path


def _reserve_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _write_builder_harness_compose(path: Path) -> None:
    content = {
        "run_env": "local",
        "docker_registry": "local",
        "oss_crs_infra": {"cpuset": "0-1", "memory": "4G"},
        "builder-harness": {
            "cpuset": "2-3",
            "memory": "8G",
            "source": {"local_path": str(BUILDER_HARNESS_CRS)},
        },
    }
    path.write_text(yaml.dump(content))


def _snapshot_out_dir(target: Target, sanitizer: str) -> Path:
    snapshot_tag = target.get_snapshot_image_name(sanitizer)
    snapshot_key = hashlib.sha256(snapshot_tag.encode()).hexdigest()[:12]
    return target.work_dir / f"snapshot-out-{sanitizer}-{snapshot_key}"


def _build_builder_image(snapshot_tag: str, image_tag: str) -> None:
    cmd = [
        "docker",
        "build",
        "-f",
        str(REPO_ROOT / "oss-crs-infra" / "default-builder" / "Dockerfile"),
        "--build-arg",
        f"snapshot_image={snapshot_tag}",
        "-t",
        image_tag,
        str(REPO_ROOT),
    ]
    subprocess.run(cmd, cwd=REPO_ROOT, check=True, capture_output=True, text=True)


def _run_builder_container(
    image_tag: str,
    port: int,
    snapshot_out: Path,
    proj_path: Path,
    shared_dir: Path,
    target: Target,
    sanitizer: str,
) -> str:
    env = target.get_target_env()
    container_name = f"builder-harness-{port}"
    cmd = [
        "docker",
        "run",
        "-d",
        "--rm",
        "--name",
        container_name,
        "--privileged",
        "--shm-size=2g",
        "-p",
        f"127.0.0.1:{port}:8080",
        "-e",
        f"SANITIZER={sanitizer}",
        "-e",
        f"FUZZING_ENGINE={env.get('engine', 'libfuzzer')}",
        "-e",
        f"ARCHITECTURE={env.get('architecture', 'x86_64')}",
        "-e",
        f"FUZZING_LANGUAGE={env.get('language', 'c')}",
        "-e",
        f"PROJECT_NAME={target.name}",
        "-e",
        f"OSS_CRS_PROJ_PATH=/OSS_CRS_PROJ_PATH",
        "-e",
        "OSS_CRS_RUN_ENV_TYPE=local",
        "-e",
        f"OSS_CRS_SHARED_DIR=/OSS_CRS_SHARED_DIR",
        "-v",
        f"{snapshot_out}:/OSS_CRS_BUILD_OUT_DIR/build:ro",
        "-v",
        f"{proj_path}:/OSS_CRS_PROJ_PATH:ro",
        "-v",
        f"{shared_dir}:/OSS_CRS_SHARED_DIR:rw",
        image_tag,
    ]
    subprocess.run(cmd, cwd=REPO_ROOT, check=True, capture_output=True, text=True)
    return container_name


@pytest.fixture(scope="session", autouse=True)
def builder_summary_csv() -> Path:
    summary_path = _summary_csv_path()
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    if summary_path.exists():
        summary_path.unlink()
    return summary_path


@pytest.mark.skipif(not docker_available(), reason="Docker not available")
@pytest.mark.parametrize("case", TEST_CASES, ids=lambda case: case["benchmark"])
def test_builder_sidecar_host_harness(cli_runner, tmp_dir, work_dir, case, builder_summary_csv):
    benchmark_name = case["benchmark"]
    project_path = Path(case["project"])
    repo_input_path = Path(case["repo_path"])
    repo_path = _prepare_repo_path(repo_input_path, tmp_dir / f"{benchmark_name}-repo")

    compose_file = tmp_dir / f"{benchmark_name}-compose.yaml"
    build_id = f"{benchmark_name}-build"
    sanitizer = "address"
    _write_builder_harness_compose(compose_file)
    started = time.time()
    summary: dict[str, str] = {
        "benchmark": benchmark_name,
        "language": case["language"],
        "harness": case["harness"],
        "source_tarball": case["source_tarball"],
        "build_target_rc": "",
        "snapshot_tag": "",
        "snapshot_out_exists": "0",
        "builder_image_built": "0",
        "builder_healthy": "0",
        "base_test_exit_code": "",
        "patch_build_exit_code": "",
        "patch_build_id": "",
        "patched_test_exit_code": "",
        "overall_success": "0",
        "duration_sec": "",
        "error": "",
    }

    build_result = cli_runner(
        "build-target",
        "--compose-file", str(compose_file),
        "--work-dir", str(work_dir),
        "--fuzz-proj-path", str(project_path),
        "--target-source-path", str(repo_path),
        "--build-id", build_id,
        timeout=1800,
    )
    summary["build_target_rc"] = str(build_result.returncode)

    target = Target(work_dir, project_path, repo_path, case["harness"])
    snapshot_tag = target.get_snapshot_image_name(sanitizer)
    summary["snapshot_tag"] = snapshot_tag
    snapshot_out = _snapshot_out_dir(target, sanitizer)
    summary["snapshot_out_exists"] = "1" if snapshot_out.exists() else "0"

    builder_image = f"builder-harness:{benchmark_name}"
    port = _reserve_local_port()
    shared_dir = tmp_dir / f"{benchmark_name}-shared"
    shared_dir.mkdir()
    container_name = ""
    try:
        assert build_result.returncode == 0, f"build-target failed: {build_result.stderr}"
        assert snapshot_out.exists(), f"snapshot output missing: {snapshot_out}"

        _build_builder_image(snapshot_tag, builder_image)
        summary["builder_image_built"] = "1"
        container_name = _run_builder_container(
            builder_image,
            port,
            snapshot_out,
            project_path,
            shared_dir,
            target,
            sanitizer,
        )

        client = BuilderClient(f"http://127.0.0.1:{port}")
        healthy = client.wait_for_health(max_wait=60)
        summary["builder_healthy"] = "1" if healthy else "0"
        assert healthy, "builder sidecar never became healthy"

        base_test_dir = tmp_dir / f"{benchmark_name}-base-test"
        base_test_rc = client.run_test("base", base_test_dir)
        summary["base_test_exit_code"] = str(base_test_rc)
        assert base_test_rc == 0
        assert (base_test_dir / "test_exit_code").read_text() == "0"

        probe_name = f"oss_crs_builder_probe_{benchmark_name.replace('-', '_')}.txt"
        patch_path = tmp_dir / f"{benchmark_name}-probe.diff"
        patch_path.write_text(
            f"diff --git a/{probe_name} b/{probe_name}\n"
            "new file mode 100644\n"
            "index 0000000..8c22b44\n"
            "--- /dev/null\n"
            f"+++ b/{probe_name}\n"
            "@@ -0,0 +1 @@\n"
            "+builder probe\n"
        )
        build_response_dir = tmp_dir / f"{benchmark_name}-patch-build"
        patch_build_rc = client.apply_patch_build(patch_path, build_response_dir)
        summary["patch_build_exit_code"] = str(patch_build_rc)
        assert patch_build_rc == 0
        build_id_value = (build_response_dir / "build_id").read_text().strip()
        summary["patch_build_id"] = build_id_value
        assert build_id_value

        patched_test_dir = tmp_dir / f"{benchmark_name}-patched-test"
        patched_test_rc = client.run_test(build_id_value, patched_test_dir)
        summary["patched_test_exit_code"] = str(patched_test_rc)
        assert patched_test_rc == 0
        assert (patched_test_dir / "test_exit_code").read_text() == "0"
        summary["overall_success"] = "1"
    except Exception as exc:
        summary["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        summary["duration_sec"] = f"{time.time() - started:.2f}"
        _append_summary_row(builder_summary_csv, summary)
        if container_name:
            subprocess.run(
                ["docker", "rm", "-f", container_name],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
            )
        subprocess.run(
            ["docker", "image", "rm", "-f", builder_image],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )

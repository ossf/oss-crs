"""Tests for artifacts command pre-run path resolution behavior."""

from types import SimpleNamespace

from oss_crs.src.cli.artifacts import handle_artifacts
from oss_crs.src.utils import normalize_run_id


class _FakeTarget:
    def __init__(self, harness: str = "fuzz_target"):
        self.target_harness = harness

    def get_docker_image_name(self) -> str:
        return "mock-proj:deadbeef"


class _FakeWorkDir:
    def __init__(self, tmp_path, resolved_run_id: str | None):
        self._tmp = tmp_path
        self._resolved_run_id = resolved_run_id

    def resolve_run_id(self, _raw: str, _sanitizer: str) -> str | None:
        return self._resolved_run_id

    def resolve_build_id(self, _raw: str, _sanitizer: str) -> str | None:
        return None

    def read_build_id_for_run(self, _run_id: str, _sanitizer: str) -> str | None:
        return None

    def get_exchange_dir(
        self, target, run_id: str, sanitizer: str, *, create: bool = False
    ):
        _ = create
        return (
            self._tmp
            / sanitizer
            / "runs"
            / run_id
            / "EXCHANGE_DIR"
            / target.get_docker_image_name().replace(":", "_")
            / target.target_harness
        )

    def get_run_logs_dir(
        self, target, run_id: str, sanitizer: str, *, create: bool = False
    ):
        _ = create
        return (
            self._tmp
            / sanitizer
            / "runs"
            / run_id
            / "logs"
            / target.get_docker_image_name().replace(":", "_")
            / target.target_harness
        )

    def get_build_output_dir(
        self,
        crs_name: str,
        target,
        build_id: str,
        sanitizer: str,
        *,
        create: bool = False,
    ):
        _ = create
        return (
            self._tmp
            / sanitizer
            / "builds"
            / build_id
            / "crs"
            / crs_name
            / target.get_docker_image_name().replace(":", "_")
            / "BUILD_OUT_DIR"
        )

    def get_submit_dir(
        self,
        crs_name: str,
        target,
        run_id: str,
        sanitizer: str,
        *,
        create: bool = False,
    ):
        _ = create
        return (
            self._tmp
            / sanitizer
            / "runs"
            / run_id
            / "crs"
            / crs_name
            / target.get_docker_image_name().replace(":", "_")
            / "SUBMIT_DIR"
            / target.target_harness
        )

    def get_shared_dir(
        self,
        crs_name: str,
        target,
        run_id: str,
        sanitizer: str,
        *,
        create: bool = False,
    ):
        _ = create
        return (
            self._tmp
            / sanitizer
            / "runs"
            / run_id
            / "crs"
            / crs_name
            / target.get_docker_image_name().replace(":", "_")
            / "SHARED_DIR"
            / target.target_harness
        )


def _make_compose(
    tmp_path, resolved_run_id: str | None, resolved_sanitizer: str = "address"
):
    return SimpleNamespace(
        work_dir=_FakeWorkDir(tmp_path, resolved_run_id),
        crs_list=[SimpleNamespace(name="crs-a")],
        get_latest_build_id=lambda _target, _sanitizer: None,
        resolve_effective_sanitizer=lambda _target: resolved_sanitizer,
    )


def _make_args(run_id: str, sanitizer: str | None = "address") -> SimpleNamespace:
    return SimpleNamespace(run_id=run_id, build_id=None, sanitizer=sanitizer)


def test_artifacts_accepts_prerun_run_id_and_normalizes(tmp_path, capsys) -> None:
    compose = _make_compose(tmp_path, resolved_run_id=None)
    args = _make_args("My Pre Run")
    target = _FakeTarget("fuzz_target")

    ok = handle_artifacts(args, compose, target)
    assert ok is True

    out = capsys.readouterr().out
    assert normalize_run_id("My Pre Run") in out


def test_artifacts_prefers_existing_resolved_run_id(tmp_path, capsys) -> None:
    compose = _make_compose(tmp_path, resolved_run_id="existing-run-id")
    args = _make_args("My Pre Run")
    target = _FakeTarget("fuzz_target")

    ok = handle_artifacts(args, compose, target)
    assert ok is True

    out = capsys.readouterr().out
    assert '"run_id": "existing-run-id"' in out


def test_artifacts_rejects_invalid_prerun_run_id(tmp_path, capsys) -> None:
    compose = _make_compose(tmp_path, resolved_run_id=None)
    args = _make_args("@#$%^&*()")
    target = _FakeTarget("fuzz_target")

    ok = handle_artifacts(args, compose, target)
    assert ok is False

    err = capsys.readouterr().err
    assert "Invalid run id" in err


def test_artifacts_resolves_default_sanitizer_from_compose(tmp_path, capsys) -> None:
    compose = _make_compose(tmp_path, resolved_run_id=None, resolved_sanitizer="memory")
    args = _make_args("My Pre Run", sanitizer=None)
    target = _FakeTarget("fuzz_target")

    ok = handle_artifacts(args, compose, target)
    assert ok is True

    out = capsys.readouterr().out
    assert '"sanitizer": "memory"' in out

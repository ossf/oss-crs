# SPDX-License-Identifier: MIT
"""Tests for artifacts command pre-run path resolution behavior."""

import argparse
import json
from pathlib import Path
from types import SimpleNamespace

from oss_crs.src.constants import UNHARNESSED
from oss_crs.src.cli.artifacts import (
    collect_run_ids_for_target,
    handle_artifacts,
    resolve_run_context,
)
from oss_crs.src.cli.crs_compose import add_artifacts_command, add_archive_command
from oss_crs.src.utils import normalize_run_id


class _FakeTarget:
    def __init__(self, harness: str = "fuzz_target"):
        self.target_harness = harness

    def get_docker_image_name(self) -> str:
        return "mock-proj:deadbeef"


class _FakeWorkDir:
    NO_HARNESS_SCOPE = UNHARNESSED

    def __init__(self, tmp_path, resolved_run_id: str | None):
        self._tmp = tmp_path
        self._resolved_run_id = resolved_run_id

    def _harness_scope(self, target) -> str:
        return target.target_harness or self.NO_HARNESS_SCOPE

    def resolve_run_id(self, _raw: str, _sanitizer: str) -> str | None:
        return self._resolved_run_id

    def iter_runs(self, sanitizer: str):
        runs_dir = self._tmp / sanitizer / "runs"
        if not runs_dir.exists():
            return []
        return [
            SimpleNamespace(run_id=path.name)
            for path in runs_dir.iterdir()
            if path.is_dir()
        ]

    def resolve_build_id(self, _raw: str, _sanitizer: str) -> str | None:
        return None

    def read_build_id_for_run(self, _run_id: str, _sanitizer: str) -> str | None:
        return None

    def get_run_meta_file(self, run_id: str, sanitizer: str):
        return self._tmp / sanitizer / "runs" / run_id / "meta.json"

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
            / self._harness_scope(target)
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
            / self._harness_scope(target)
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
            / self._harness_scope(target)
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
            / self._harness_scope(target)
        )

    def get_log_dir(
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
            / "LOG_DIR"
            / self._harness_scope(target)
        )

    def get_sidecar_metrics_file(
        self,
        crs_name: str,
        target,
        run_id: str,
        sanitizer: str,
    ):
        return (
            self.get_log_dir(crs_name, target, run_id, sanitizer)
            / "libcrs-sidecar-metrics.jsonl"
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


def test_artifacts_reports_no_harness_scope(tmp_path, capsys) -> None:
    compose = _make_compose(tmp_path, resolved_run_id=None)
    args = _make_args("No Harness Run")
    target = _FakeTarget(None)

    ok = handle_artifacts(args, compose, target, source_only=True, unharnessed=True)
    assert ok is True

    out = capsys.readouterr().out
    assert UNHARNESSED in out
    assert '"pov":' in out
    assert '"exchange_dir":' in out


def test_source_only_artifacts_ignore_recorded_build_id(tmp_path, capsys) -> None:
    compose = _make_compose(tmp_path, resolved_run_id="run-1")
    compose.work_dir.read_build_id_for_run = lambda *_args: "source-only-run-1"
    args = _make_args("run-1")
    target = _FakeTarget(None)

    assert handle_artifacts(args, compose, target, source_only=True, unharnessed=True)
    output = json.loads(capsys.readouterr().out)
    assert "build_id" not in output
    assert "build" not in output["crs"]["crs-a"]


def test_no_harness_discovery_matches_arbitrary_harness_scope(tmp_path) -> None:
    compose = _make_compose(tmp_path, resolved_run_id=None)
    target = _FakeTarget(None)
    run_id = "run-1700000000"
    submit_parent = compose.work_dir.get_submit_dir(
        "crs-a", target, run_id, "address", create=False
    ).parent
    (submit_parent / "generated-harness" / "povs").mkdir(parents=True)

    assert collect_run_ids_for_target(compose, target, None, "address") == [run_id]


def test_no_harness_discovery_matches_unharnessed_harness_generator(tmp_path) -> None:
    compose = _make_compose(tmp_path, resolved_run_id=None)
    target = _FakeTarget(None)
    run_id = "run-1700000001"
    harnesses_dir = (
        compose.work_dir.get_submit_dir(
            "crs-a", target, run_id, "address", create=False
        )
        / "harnesses"
        / "generated-harness"
    )
    harnesses_dir.mkdir(parents=True)

    assert collect_run_ids_for_target(compose, target, None, "address") == [run_id]


def test_explicit_harness_discovery_only_matches_requested_scope(tmp_path) -> None:
    compose = _make_compose(tmp_path, resolved_run_id=None)
    target = _FakeTarget(None)
    matching_run = "run-1700000002"
    other_run = "run-1700000003"

    target.target_harness = "fuzz-a"
    compose.work_dir.get_submit_dir(
        "crs-a", target, matching_run, "address", create=False
    ).mkdir(parents=True)
    target.target_harness = "fuzz-b"
    compose.work_dir.get_submit_dir(
        "crs-a", target, other_run, "address", create=False
    ).mkdir(parents=True)
    target.target_harness = None

    assert collect_run_ids_for_target(compose, target, "fuzz-a", "address") == [
        matching_run
    ]
    assert target.target_harness is None


def test_existing_no_harness_query_applies_sole_scope(tmp_path) -> None:
    run_id = "run-1700000004"
    compose = _make_compose(tmp_path, resolved_run_id=run_id)
    target = _FakeTarget(None)
    submit_parent = compose.work_dir.get_submit_dir(
        "crs-a", target, run_id, "address", create=False
    ).parent
    (submit_parent / "fuzz-a").mkdir(parents=True)
    args = _make_args(run_id)

    assert resolve_run_context(args, compose, target) == ("address", run_id, True)
    assert target.target_harness == "fuzz-a"


def test_existing_no_harness_query_rejects_multiple_scopes(tmp_path, capsys) -> None:
    run_id = "run-1700000005"
    compose = _make_compose(tmp_path, resolved_run_id=run_id)
    target = _FakeTarget(None)
    submit_parent = compose.work_dir.get_submit_dir(
        "crs-a", target, run_id, "address", create=False
    ).parent
    (submit_parent / "fuzz-a").mkdir(parents=True)
    (submit_parent / "fuzz-b").mkdir(parents=True)

    assert resolve_run_context(_make_args(run_id), compose, target) is None
    assert "multiple harness scopes" in capsys.readouterr().err


def test_artifacts_includes_meta_stats_when_meta_json_exists(tmp_path, capsys) -> None:
    run_id = "existing-run-id"
    sanitizer = "address"
    meta_path = tmp_path / sanitizer / "runs" / run_id / "meta.json"
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(
        json.dumps(
            {
                "totals": {
                    "artifacts": {
                        "povs": 2,
                        "seeds": 4,
                        "patches": 1,
                        "bug_candidates": 2,
                        "reports": 3,
                    },
                    "llm": {"credits_used": 1.65},
                    "sidecar": {
                        "patch_builds": 4,
                        "patch_tests": 2,
                        "pov_runs": 8,
                    },
                },
                "crs": {
                    "crs-a": {
                        "artifacts": {
                            "povs": 2,
                            "seeds": 3,
                            "patches": 1,
                            "bug_candidates": 0,
                            "reports": 1,
                        },
                        "llm": {"credits_used": 1.25},
                        "sidecar": {
                            "patch_builds": 4,
                            "patch_tests": 2,
                            "pov_runs": 7,
                        },
                    }
                },
            }
        )
    )

    compose = _make_compose(tmp_path, resolved_run_id=run_id)
    args = _make_args(run_id, sanitizer=sanitizer)
    target = _FakeTarget("fuzz_target")

    ok = handle_artifacts(args, compose, target)
    assert ok is True

    out = capsys.readouterr().out
    assert '"meta"' in out
    assert '"totals"' in out
    assert '"credits_used": 1.65' in out
    assert '"patch_builds": 4' in out
    assert '"patch_tests": 2' in out
    assert '"pov_runs": 8' in out
    assert '"bug_candidates": 2' in out
    assert '"reports": 3' in out


def test_artifacts_advertises_per_crs_sidecar_metrics_path(tmp_path, capsys) -> None:
    """The per-CRS artifacts block points at the sidecar API-call JSONL."""
    compose = _make_compose(tmp_path, resolved_run_id="existing-run-id")
    args = _make_args("existing-run-id")
    target = _FakeTarget("fuzz_target")

    # The path is only advertised once the sidecars have produced the file.
    metrics_file = compose.work_dir.get_sidecar_metrics_file(
        "crs-a", target, "existing-run-id", "address"
    )
    metrics_file.parent.mkdir(parents=True, exist_ok=True)
    metrics_file.write_text('{"event":"run-pov"}\n')

    ok = handle_artifacts(args, compose, target)
    assert ok is True

    out = json.loads(capsys.readouterr().out)
    sidecar_path = out["crs"]["crs-a"]["sidecar_metrics"]
    assert sidecar_path.endswith(
        "crs/crs-a/mock-proj_deadbeef/LOG_DIR/fuzz_target/libcrs-sidecar-metrics.jsonl"
    )


def test_artifacts_omits_sidecar_metrics_when_file_absent(tmp_path, capsys) -> None:
    """No sidecar_metrics key is emitted when the JSONL was never written."""
    compose = _make_compose(tmp_path, resolved_run_id="existing-run-id")
    args = _make_args("existing-run-id")
    target = _FakeTarget("fuzz_target")

    ok = handle_artifacts(args, compose, target)
    assert ok is True

    out = json.loads(capsys.readouterr().out)
    assert "sidecar_metrics" not in out["crs"]["crs-a"]


# ---------------------------------------------------------------------------
# Argparse-level tests for source-only (no --fuzz-proj-path) support
# ---------------------------------------------------------------------------


def _make_artifacts_parser():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    add_artifacts_command(sub)
    return parser


def _make_archive_parser():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    add_archive_command(sub)
    return parser


def test_artifacts_accepts_target_source_path_without_fuzz_proj_path():
    """artifacts --target-source-path should work without --fuzz-proj-path."""
    parser = _make_artifacts_parser()
    args = parser.parse_args(
        [
            "artifacts",
            "--compose-file",
            "c.yaml",
            "--target-source-path",
            "/tmp/src",
        ]
    )
    assert args.target_repo_path == Path("/tmp/src")
    assert args.target_proj_path is None


def test_archive_accepts_target_source_path_without_fuzz_proj_path():
    """archive --target-source-path should work without --fuzz-proj-path."""
    parser = _make_archive_parser()
    args = parser.parse_args(
        [
            "archive",
            "--compose-file",
            "c.yaml",
            "--target-source-path",
            "/tmp/src",
            "--out",
            "results.tar.gz",
        ]
    )
    assert args.target_repo_path == Path("/tmp/src")
    assert args.target_proj_path is None
    assert args.out == "results.tar.gz"


def test_archive_target_harness_is_optional():
    """archive --target-harness should be optional (source-only support)."""
    parser = _make_archive_parser()
    args = parser.parse_args(
        [
            "archive",
            "--compose-file",
            "c.yaml",
            "--target-source-path",
            "/tmp/src",
            "--out",
            "results.tar.gz",
        ]
    )
    assert args.target_harness is None

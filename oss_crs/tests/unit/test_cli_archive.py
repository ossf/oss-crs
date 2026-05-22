# SPDX-License-Identifier: MIT
"""Unit tests for the archive command and --latest flag."""

import tarfile
from pathlib import Path
from types import SimpleNamespace

from oss_crs.src.cli.archive import handle_archive
from oss_crs.src.cli.artifacts import resolve_run_context


# ---------------------------------------------------------------------------
# Shared test infrastructure
# ---------------------------------------------------------------------------


class _FakeWorkDir:
    def __init__(self, tmp_path: Path):
        self._tmp = tmp_path

    def resolve_run_id(self, raw: str, _sanitizer: str) -> str:
        # Always treat the run as already existing (simulates on-disk run).
        return raw

    def get_submit_dir(
        self,
        crs_name: str,
        _target,
        run_id: str,
        sanitizer: str,
        *,
        create: bool = False,
    ) -> Path:
        p = self._tmp / sanitizer / "runs" / run_id / "crs" / crs_name / "SUBMIT_DIR"
        if create:
            p.mkdir(parents=True, exist_ok=True)
        return p

    def get_exchange_dir(
        self, _target, run_id: str, sanitizer: str, *, create: bool = False
    ) -> Path:
        p = self._tmp / sanitizer / "runs" / run_id / "EXCHANGE_DIR"
        if create:
            p.mkdir(parents=True, exist_ok=True)
        return p

    def get_run_logs_dir(
        self, _target, run_id: str, sanitizer: str, *, create: bool = False
    ) -> Path:
        p = self._tmp / sanitizer / "runs" / run_id / "logs"
        if create:
            p.mkdir(parents=True, exist_ok=True)
        return p

    def get_shared_dir(
        self,
        crs_name: str,
        _target,
        run_id: str,
        sanitizer: str,
        *,
        create: bool = False,
    ) -> Path:
        p = self._tmp / sanitizer / "runs" / run_id / "crs" / crs_name / "SHARED_DIR"
        if create:
            p.mkdir(parents=True, exist_ok=True)
        return p

    def get_log_dir(
        self,
        crs_name: str,
        _target,
        run_id: str,
        sanitizer: str,
        *,
        create: bool = False,
    ) -> Path:
        p = self._tmp / sanitizer / "runs" / run_id / "crs" / crs_name / "LOG_DIR"
        if create:
            p.mkdir(parents=True, exist_ok=True)
        return p


def _make_crs(name: str, *, is_triage: bool = False) -> SimpleNamespace:
    return SimpleNamespace(name=name, config=SimpleNamespace(is_triage=is_triage))


def _make_compose(tmp_path: Path, crs_list: list) -> SimpleNamespace:
    return SimpleNamespace(
        work_dir=_FakeWorkDir(tmp_path),
        crs_list=crs_list,
        resolve_effective_sanitizer=lambda _target: "address",
    )


def _make_target(harness: str = "fuzz_target") -> SimpleNamespace:
    return SimpleNamespace(target_harness=harness)


def _make_args(
    *,
    run_id: str | None = None,
    latest: bool = False,
    sanitizer: str = "address",
    out: str = "out.tar.gz",
    include_all: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        run_id=run_id,
        latest=latest,
        sanitizer=sanitizer,
        out=out,
        include_all=include_all,
    )


def _write_file(path: Path, content: str = "data") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def _tar_members(tar_path: Path) -> set[str]:
    with tarfile.open(tar_path, "r:gz") as tar:
        return {m.name for m in tar.getmembers()}


# ---------------------------------------------------------------------------
# --latest tests (via resolve_run_context)
# ---------------------------------------------------------------------------


def test_latest_picks_most_recent_run(tmp_path: Path, monkeypatch) -> None:
    run_ids = ["1700000002ab", "1700000001xy"]
    monkeypatch.setattr(
        "oss_crs.src.cli.artifacts.collect_run_ids_for_target",
        lambda *_a, **_kw: run_ids,
    )
    compose = _make_compose(tmp_path, [_make_crs("crs-a")])
    args = SimpleNamespace(run_id=None, latest=True, sanitizer="address")
    target = _make_target()

    ctx = resolve_run_context(args, compose, target)
    assert ctx is not None
    _, run_id = ctx
    assert run_id == "1700000002ab"


def test_latest_returns_none_when_no_runs(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "oss_crs.src.cli.artifacts.collect_run_ids_for_target",
        lambda *_a, **_kw: [],
    )
    compose = _make_compose(tmp_path, [_make_crs("crs-a")])
    args = SimpleNamespace(run_id=None, latest=True, sanitizer="address")
    target = _make_target()

    ctx = resolve_run_context(args, compose, target)
    assert ctx is None
    assert "No runs found" in capsys.readouterr().err


def test_run_id_takes_precedence_over_latest(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(
        "oss_crs.src.cli.artifacts.collect_run_ids_for_target",
        lambda *_a, **_kw: ["1700000002ab"],
    )
    compose = _make_compose(tmp_path, [_make_crs("crs-a")])
    # Use a timestamped id so resolve_run_id returns it directly (no normalization)
    explicit_id = "1700000099zz"
    args = SimpleNamespace(run_id=explicit_id, latest=True, sanitizer="address")
    target = _make_target()

    ctx = resolve_run_context(args, compose, target)
    assert ctx is not None
    _, run_id = ctx
    # --latest should be ignored; the explicit run_id wins
    assert run_id == explicit_id
    assert run_id != "1700000002ab"


# ---------------------------------------------------------------------------
# archive: basic collection (no triage)
# ---------------------------------------------------------------------------


def test_archive_collects_all_subdirs_from_all_crs(tmp_path: Path) -> None:
    run_id = "1700000001ab"
    crs_a = _make_crs("crs-a")
    compose = _make_compose(tmp_path, [crs_a])
    work_dir = compose.work_dir

    submit = work_dir.get_submit_dir("crs-a", None, run_id, "address")
    _write_file(submit / "povs" / "crash-001")
    _write_file(submit / "seeds" / "seed-001")
    _write_file(submit / "patches" / "patch-001.diff")
    _write_file(submit / "bug-candidates" / "bug-001")

    out = tmp_path / "results.tar.gz"
    args = _make_args(run_id=run_id, out=str(out))
    ok = handle_archive(args, compose, _make_target())

    assert ok is True
    members = _tar_members(out)
    assert "povs/crash-001" in members
    assert "seeds/seed-001" in members
    assert "patches/patch-001.diff" in members
    assert "bug-candidates/bug-001" in members


def test_archive_returns_false_when_no_artifacts(tmp_path: Path, capsys) -> None:
    run_id = "1700000001ab"
    compose = _make_compose(tmp_path, [_make_crs("crs-a")])
    out = tmp_path / "results.tar.gz"
    args = _make_args(run_id=run_id, out=str(out))

    ok = handle_archive(args, compose, _make_target())

    assert ok is False
    assert "No artifacts found" in capsys.readouterr().err


def test_archive_multiple_crs_no_triage(tmp_path: Path) -> None:
    run_id = "1700000001ab"
    crs_a, crs_b = _make_crs("crs-a"), _make_crs("crs-b")
    compose = _make_compose(tmp_path, [crs_a, crs_b])
    work_dir = compose.work_dir

    _write_file(
        work_dir.get_submit_dir("crs-a", None, run_id, "address") / "povs" / "crash-a"
    )
    _write_file(
        work_dir.get_submit_dir("crs-b", None, run_id, "address") / "povs" / "crash-b"
    )

    out = tmp_path / "results.tar.gz"
    args = _make_args(run_id=run_id, out=str(out))
    ok = handle_archive(args, compose, _make_target())

    assert ok is True
    members = _tar_members(out)
    assert "povs/crash-a" in members
    # crash-b collides on arcname and gets a suffix
    assert any(m.startswith("povs/crash-b") for m in members)


# ---------------------------------------------------------------------------
# archive: triage CRS routing
# ---------------------------------------------------------------------------


def test_archive_triage_povs_from_triage_not_finder(tmp_path: Path) -> None:
    run_id = "1700000001ab"
    finder = _make_crs("crs-finder", is_triage=False)
    triage = _make_crs("crs-triage", is_triage=True)
    compose = _make_compose(tmp_path, [finder, triage])
    work_dir = compose.work_dir

    # Both produce POVs — only triage's should end up in the archive
    _write_file(
        work_dir.get_submit_dir("crs-finder", None, run_id, "address")
        / "povs"
        / "raw-pov"
    )
    _write_file(
        work_dir.get_submit_dir("crs-triage", None, run_id, "address")
        / "povs"
        / "triaged-pov"
    )
    _write_file(
        work_dir.get_submit_dir("crs-finder", None, run_id, "address")
        / "seeds"
        / "seed-001"
    )

    out = tmp_path / "results.tar.gz"
    args = _make_args(run_id=run_id, out=str(out))
    ok = handle_archive(args, compose, _make_target())

    assert ok is True
    members = _tar_members(out)
    assert "povs/triaged-pov" in members
    assert "povs/raw-pov" not in members
    assert "seeds/seed-001" in members


def test_archive_triage_non_pov_artifacts_from_non_triage(tmp_path: Path) -> None:
    run_id = "1700000001ab"
    finder = _make_crs("crs-finder", is_triage=False)
    triage = _make_crs("crs-triage", is_triage=True)
    compose = _make_compose(tmp_path, [finder, triage])
    work_dir = compose.work_dir

    _write_file(
        work_dir.get_submit_dir("crs-triage", None, run_id, "address")
        / "povs"
        / "triaged-pov"
    )
    _write_file(
        work_dir.get_submit_dir("crs-finder", None, run_id, "address")
        / "patches"
        / "patch-001.diff"
    )
    _write_file(
        work_dir.get_submit_dir("crs-finder", None, run_id, "address")
        / "bug-candidates"
        / "bug-001"
    )

    out = tmp_path / "results.tar.gz"
    args = _make_args(run_id=run_id, out=str(out))
    ok = handle_archive(args, compose, _make_target())

    assert ok is True
    members = _tar_members(out)
    assert "patches/patch-001.diff" in members
    assert "bug-candidates/bug-001" in members


# ---------------------------------------------------------------------------
# archive: --all flag
# ---------------------------------------------------------------------------


def test_archive_all_includes_exchange_and_logs(tmp_path: Path) -> None:
    run_id = "1700000001ab"
    compose = _make_compose(tmp_path, [_make_crs("crs-a")])
    work_dir = compose.work_dir

    _write_file(
        work_dir.get_submit_dir("crs-a", None, run_id, "address") / "povs" / "crash-001"
    )
    _write_file(work_dir.get_exchange_dir(None, run_id, "address") / "extra.bin")
    _write_file(work_dir.get_run_logs_dir(None, run_id, "address") / "compose.log")

    out = tmp_path / "results.tar.gz"
    args = _make_args(run_id=run_id, out=str(out), include_all=True)
    ok = handle_archive(args, compose, _make_target())

    assert ok is True
    members = _tar_members(out)
    assert "povs/crash-001" in members
    assert "exchange/extra.bin" in members
    assert "logs/compose.log" in members


def test_archive_default_excludes_exchange_and_logs(tmp_path: Path) -> None:
    run_id = "1700000001ab"
    compose = _make_compose(tmp_path, [_make_crs("crs-a")])
    work_dir = compose.work_dir

    _write_file(
        work_dir.get_submit_dir("crs-a", None, run_id, "address") / "povs" / "crash-001"
    )
    _write_file(work_dir.get_exchange_dir(None, run_id, "address") / "extra.bin")

    out = tmp_path / "results.tar.gz"
    args = _make_args(run_id=run_id, out=str(out), include_all=False)
    ok = handle_archive(args, compose, _make_target())

    assert ok is True
    members = _tar_members(out)
    assert "exchange/extra.bin" not in members


# ---------------------------------------------------------------------------
# archive: arcname deduplication
# ---------------------------------------------------------------------------


def test_archive_deduplicates_colliding_arcnames(tmp_path: Path) -> None:
    run_id = "1700000001ab"
    crs_a, crs_b = _make_crs("crs-a"), _make_crs("crs-b")
    compose = _make_compose(tmp_path, [crs_a, crs_b])
    work_dir = compose.work_dir

    # Both CRSs produce a file with the same name
    _write_file(
        work_dir.get_submit_dir("crs-a", None, run_id, "address") / "povs" / "crash",
        "a",
    )
    _write_file(
        work_dir.get_submit_dir("crs-b", None, run_id, "address") / "povs" / "crash",
        "b",
    )

    out = tmp_path / "results.tar.gz"
    args = _make_args(run_id=run_id, out=str(out))
    ok = handle_archive(args, compose, _make_target())

    assert ok is True
    members = _tar_members(out)
    # Both files present, one with a suffix
    assert "povs/crash" in members
    assert "povs/crash.1" in members

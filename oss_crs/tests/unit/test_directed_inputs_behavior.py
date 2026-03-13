from pathlib import Path

from oss_crs.src.crs_compose import CRSCompose
from oss_crs.src.target import Target
from oss_crs.src.workdir import WorkDir


def _make_run_compose(tmp_path: Path) -> CRSCompose:
    compose = CRSCompose.__new__(CRSCompose)
    compose.crs_list = []
    compose.work_dir = WorkDir(tmp_path / "work")
    return compose


def _stub_common(compose: CRSCompose, target: Target, monkeypatch) -> None:
    """Stub common dependencies for run() tests."""
    monkeypatch.setattr(
        compose,
        "_resolve_target_build_options",
        lambda target, sanitizer=None: ("address", None, None),
    )
    monkeypatch.setattr(
        "oss_crs.src.crs_compose.check_cgroup_parent_available",
        lambda: (False, None),
    )
    monkeypatch.setattr(
        compose, "_CRSCompose__validate_before_run", lambda *args, **kwargs: True
    )
    monkeypatch.setattr(target, "init_repo", lambda: True)


def _stub_run_for_existing_build(
    compose: CRSCompose, target: Target, monkeypatch, captured: dict
) -> None:
    """Stub for tests where an existing build should be reused (no rebuild)."""
    _stub_common(compose, target, monkeypatch)
    monkeypatch.setattr(
        compose, "_CRSCompose__check_target_built", lambda *args, **kwargs: True
    )

    def _capture_run(*args, **kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(compose, "_CRSCompose__run", _capture_run)

    def _unexpected_build_target(*args, **kwargs):
        raise AssertionError("run() should reuse the existing build instead of rebuilding")

    monkeypatch.setattr(compose, "build_target", _unexpected_build_target)


def _stub_run_for_new_build(
    compose: CRSCompose,
    target: Target,
    monkeypatch,
    build_captured: dict,
    run_captured: dict,
) -> None:
    """Stub for tests where no builds exist and a new build is triggered."""
    _stub_common(compose, target, monkeypatch)
    monkeypatch.setattr(compose, "get_latest_build_id", lambda t, s: None)

    def _capture_build_target(target, build_id, sanitizer, **kwargs):
        build_captured["build_id"] = build_id
        build_captured["diff"] = kwargs.get("diff")
        build_captured["bug_candidate"] = kwargs.get("bug_candidate")
        build_captured["bug_candidate_dir"] = kwargs.get("bug_candidate_dir")
        return True

    monkeypatch.setattr(compose, "build_target", _capture_build_target)

    def _capture_run(*args, **kwargs):
        run_captured.update(kwargs)
        return True

    monkeypatch.setattr(compose, "_CRSCompose__run", _capture_run)


def test_existing_build_allows_run_phase_diff_without_build_metadata(
    tmp_path: Path, monkeypatch
) -> None:
    compose = _make_run_compose(tmp_path)
    target = Target(tmp_path / "work", tmp_path / "proj", None, "fuzz_target")
    diff = tmp_path / "ref.diff"
    diff.write_text("patch")
    captured: dict = {}

    _stub_run_for_existing_build(compose, target, monkeypatch, captured)

    assert (
        compose.run(
            target,
            run_id="run-1",
            build_id="build-1",
            sanitizer="address",
            diff=diff,
        )
        is True
    )
    assert captured["build_id"].startswith("build-1")
    assert captured["diff_path"] == diff


def test_existing_build_allows_run_phase_bug_candidate_file(tmp_path: Path, monkeypatch) -> None:
    compose = _make_run_compose(tmp_path)
    target = Target(tmp_path / "work", tmp_path / "proj", None, "fuzz_target")
    bug_candidate = tmp_path / "report.sarif"
    bug_candidate.write_text("{}")
    captured: dict = {}

    _stub_run_for_existing_build(compose, target, monkeypatch, captured)

    assert (
        compose.run(
            target,
            run_id="run-1",
            build_id="build-1",
            sanitizer="address",
            bug_candidate=bug_candidate,
        )
        is True
    )
    assert captured["bug_candidate"] == bug_candidate


def test_existing_build_allows_run_phase_bug_candidate_dir(tmp_path: Path, monkeypatch) -> None:
    compose = _make_run_compose(tmp_path)
    target = Target(tmp_path / "work", tmp_path / "proj", None, "fuzz_target")
    bug_candidate_dir = tmp_path / "bug-candidates"
    bug_candidate_dir.mkdir()
    (bug_candidate_dir / "report.sarif").write_text("{}")
    captured: dict = {}

    _stub_run_for_existing_build(compose, target, monkeypatch, captured)

    assert (
        compose.run(
            target,
            run_id="run-1",
            build_id="build-1",
            sanitizer="address",
            bug_candidate_dir=bug_candidate_dir,
        )
        is True
    )
    assert captured["bug_candidate"] == bug_candidate_dir


def test_build_fetch_dir_is_target_scoped_without_harness(tmp_path: Path) -> None:
    work_dir = WorkDir(tmp_path / "work")
    target = Target(tmp_path / "work", tmp_path / "proj", None, None)

    fetch_dir = work_dir.get_build_fetch_dir(target, "build-1", "address", create=False)

    assert fetch_dir == (
        tmp_path / "work" / "address" / "builds" / "build-1" / "FETCH_DIR" / target.get_docker_image_name().replace(":", "_")
    )


def test_prepare_build_fetch_dir_accepts_directed_inputs_without_harness(
    tmp_path: Path,
) -> None:
    compose = _make_run_compose(tmp_path)
    target = Target(tmp_path / "work", tmp_path / "proj", None, None)
    diff = tmp_path / "ref.diff"
    diff.write_text("patch")
    bug_candidate = tmp_path / "report.sarif"
    bug_candidate.write_text("{}")

    fetch_dir = compose._prepare_build_fetch_dir(
        target=target,
        build_id="build-1",
        sanitizer="address",
        diff=diff,
        bug_candidate=bug_candidate,
        bug_candidate_dir=None,
    )

    assert fetch_dir is not None
    assert (fetch_dir / "diffs" / "ref.diff").read_text() == "patch"
    assert (fetch_dir / "bug-candidates" / "report.sarif").read_text() == "{}"


def test_parallel_runs_with_same_build_id_use_distinct_exchange_dirs(tmp_path: Path) -> None:
    work_dir = WorkDir(tmp_path / "work")
    target = Target(tmp_path / "work", tmp_path / "proj", None, "fuzz_target")

    run_a_exchange = work_dir.get_exchange_dir(
        target, run_id="run-a", sanitizer="address", create=False
    )
    run_b_exchange = work_dir.get_exchange_dir(
        target, run_id="run-b", sanitizer="address", create=False
    )

    assert run_a_exchange != run_b_exchange
    assert "run-a" in str(run_a_exchange)
    assert "run-b" in str(run_b_exchange)


def test_parallel_runs_with_same_build_id_and_different_harnesses_are_isolated(
    tmp_path: Path,
) -> None:
    work_dir = WorkDir(tmp_path / "work")
    target_a = Target(tmp_path / "work", tmp_path / "proj", None, "harness-a")
    target_b = Target(tmp_path / "work", tmp_path / "proj", None, "harness-b")

    exchange_a = work_dir.get_exchange_dir(
        target_a, run_id="run-1", sanitizer="address", create=False
    )
    exchange_b = work_dir.get_exchange_dir(
        target_b, run_id="run-2", sanitizer="address", create=False
    )

    assert exchange_a != exchange_b
    assert str(exchange_a).endswith("/harness-a")
    assert str(exchange_b).endswith("/harness-b")


def test_build_fetch_dir_is_shared_for_same_build_id_regardless_of_harness(
    tmp_path: Path,
) -> None:
    work_dir = WorkDir(tmp_path / "work")
    target_a = Target(tmp_path / "work", tmp_path / "proj", None, "harness-a")
    target_b = Target(tmp_path / "work", tmp_path / "proj", None, "harness-b")

    fetch_a = work_dir.get_build_fetch_dir(
        target_a, build_id="build-1", sanitizer="address", create=False
    )
    fetch_b = work_dir.get_build_fetch_dir(
        target_b, build_id="build-1", sanitizer="address", create=False
    )

    assert fetch_a == fetch_b


def test_run_without_build_id_reuses_latest_build_with_directed_inputs(
    tmp_path: Path, monkeypatch
) -> None:
    """When build_id is not provided, run() resolves to the latest build and passes directed inputs."""
    compose = _make_run_compose(tmp_path)
    target = Target(tmp_path / "work", tmp_path / "proj", None, "fuzz_target")
    diff = tmp_path / "ref.diff"
    diff.write_text("patch")
    bug_candidate = tmp_path / "report.sarif"
    bug_candidate.write_text("{}")
    captured: dict = {}

    # Stub to return a latest build
    monkeypatch.setattr(compose, "get_latest_build_id", lambda t, s: "latest-build-abc")
    _stub_run_for_existing_build(compose, target, monkeypatch, captured)

    assert (
        compose.run(
            target,
            run_id="run-1",
            build_id=None,  # Not provided - should auto-resolve
            sanitizer="address",
            diff=diff,
            bug_candidate=bug_candidate,
        )
        is True
    )
    # Should have resolved to the latest build
    assert captured["build_id"] == "latest-build-abc"
    # Both directed inputs should be passed through
    assert captured["diff_path"] == diff
    assert captured["bug_candidate"] == bug_candidate


def test_run_without_build_id_triggers_build_and_uses_same_id_for_both_phases(
    tmp_path: Path, monkeypatch
) -> None:
    """When build_id is not provided and no builds exist, build and run receive the same generated build_id."""
    compose = _make_run_compose(tmp_path)
    target = Target(tmp_path / "work", tmp_path / "proj", None, "fuzz_target")
    diff = tmp_path / "ref.diff"
    diff.write_text("patch")
    bug_candidate = tmp_path / "report.sarif"
    bug_candidate.write_text("{}")

    build_captured: dict = {}
    run_captured: dict = {}
    _stub_run_for_new_build(compose, target, monkeypatch, build_captured, run_captured)

    assert (
        compose.run(
            target,
            run_id="run-1",
            build_id=None,
            sanitizer="address",
            diff=diff,
            bug_candidate=bug_candidate,
        )
        is True
    )

    # Both build and run should receive the same generated build_id
    assert build_captured["build_id"] is not None
    assert run_captured["build_id"] is not None
    assert build_captured["build_id"] == run_captured["build_id"]
    # Build receives diff and bug_candidate
    assert build_captured["diff"] == diff
    assert build_captured["bug_candidate"] == bug_candidate
    # Run receives diff_path and bug_candidate
    assert run_captured["diff_path"] == diff
    assert run_captured["bug_candidate"] == bug_candidate


def test_run_without_build_id_passes_bug_candidate_dir_to_both_phases(
    tmp_path: Path, monkeypatch
) -> None:
    """When build_id is not provided, bug_candidate_dir is passed correctly to build and run."""
    compose = _make_run_compose(tmp_path)
    target = Target(tmp_path / "work", tmp_path / "proj", None, "fuzz_target")
    bug_candidate_dir = tmp_path / "bug-candidates"
    bug_candidate_dir.mkdir()
    (bug_candidate_dir / "report.sarif").write_text("{}")

    build_captured: dict = {}
    run_captured: dict = {}
    _stub_run_for_new_build(compose, target, monkeypatch, build_captured, run_captured)

    assert (
        compose.run(
            target,
            run_id="run-1",
            build_id=None,
            sanitizer="address",
            bug_candidate_dir=bug_candidate_dir,
        )
        is True
    )

    assert build_captured["build_id"] == run_captured["build_id"]
    # build_target receives bug_candidate_dir as a keyword arg
    assert build_captured["bug_candidate_dir"] == bug_candidate_dir
    # __run receives bug_candidate (which is set to bug_candidate_dir when bug_candidate is None)
    assert run_captured["bug_candidate"] == bug_candidate_dir

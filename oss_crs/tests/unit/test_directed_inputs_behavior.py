from pathlib import Path

from oss_crs.src.crs_compose import CRSCompose
from oss_crs.src.target import Target
from oss_crs.src.workdir import WorkDir


def _make_run_compose(tmp_path: Path) -> CRSCompose:
    compose = CRSCompose.__new__(CRSCompose)
    compose.crs_list = []
    compose.work_dir = WorkDir(tmp_path / "work")
    return compose


def _stub_run_for_existing_build(
    compose: CRSCompose, target: Target, monkeypatch, captured: dict
) -> None:
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

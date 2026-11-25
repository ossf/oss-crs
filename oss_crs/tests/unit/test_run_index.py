# SPDX-License-Identifier: MIT
"""Unit tests for the run provenance payload and the <work_dir>/index symlink tree."""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from oss_crs.src.run_index import (
    BY_CRS,
    BY_TARGET,
    build_run_info,
    find_links_to,
    find_prunable_links,
    iter_index_links,
    link_run,
    make_link_name,
    unlink_if_dangling,
)


class _Target:
    def __init__(self, tmp_path: Path, *, harness: str | None = "fuzz_target"):
        self.name = "proj"
        self.proj_path = tmp_path / "proj"
        self.repo_path = tmp_path / "repo"
        self.target_harness = harness
        self.source_only = harness is None

    def get_workdir_target_key(self) -> str:
        return "proj_abc123def456"


STARTED = datetime(2026, 10, 16, 14, 3, 7, tzinfo=timezone(timedelta(hours=-4)))


def _run_dir(tmp_path: Path, run_id: str = "exp-1-9b1c2f", hash_: str = "h1") -> Path:
    run_dir = tmp_path / "work" / "crs_compose" / hash_ / "address" / "runs" / run_id
    run_dir.mkdir(parents=True)
    return run_dir


def _index(tmp_path: Path) -> Path:
    return tmp_path / "work" / "index"


# ---------------------------------------------------------------------------
# run-info.json payload
# ---------------------------------------------------------------------------


def test_build_run_info_schema(tmp_path: Path) -> None:
    info = build_run_info(
        target=_Target(tmp_path),
        run_id="exp-1-9b1c2f",
        sanitizer="address",
        build_id="b-1",
        compose_hash="h1",
        compose_file=tmp_path / "compose.yaml",
        crs_names=["crs-a", "crs-b"],
        started=STARTED,
    )

    assert set(info) == {
        "build_id",
        "compose_file",
        "compose_hash",
        "crs_names",
        "harness",
        "run_id",
        "sanitizer",
        "started_at",
        "started_at_epoch",
        "target",
    }
    assert set(info["target"]) == {
        "key",
        "name",
        "proj_path",
        "repo_path",
        "source_only",
    }
    assert info["build_id"] == "b-1"
    assert info["harness"] == "fuzz_target"
    assert info["crs_names"] == ["crs-a", "crs-b"]
    assert info["started_at"] == "2026-10-16T14:03:07-04:00"
    assert info["started_at_epoch"] == int(STARTED.timestamp())
    assert os.path.isabs(info["compose_file"])
    assert os.path.isabs(info["target"]["proj_path"])
    assert os.path.isabs(info["target"]["repo_path"])
    assert info["target"]["name"] == "proj"
    assert info["target"]["key"] == "proj_abc123def456"
    assert info["target"]["source_only"] is False


def test_build_run_info_source_only_nulls(tmp_path: Path) -> None:
    info = build_run_info(
        target=_Target(tmp_path, harness=None),
        run_id="r",
        sanitizer="address",
        build_id=None,
        compose_hash="h1",
        compose_file=None,
        crs_names=[],
        started=STARTED,
    )

    assert info["build_id"] is None
    assert info["harness"] is None
    assert info["compose_file"] is None
    assert info["target"]["source_only"] is True


def test_make_link_name_format() -> None:
    assert make_link_name(STARTED, "exp-1-9b1c2f") == "20261016-140307_exp-1-9b1c2f"


# ---------------------------------------------------------------------------
# link_run
# ---------------------------------------------------------------------------


def test_link_run_creates_relative_links_in_by_target_and_by_crs(
    tmp_path: Path,
) -> None:
    run_dir = _run_dir(tmp_path)
    index = _index(tmp_path)

    links = link_run(
        index,
        run_dir,
        target_name="proj",
        crs_names=["crs-a", "crs-b"],
        link_name="20261016-140307_exp-1-9b1c2f",
        sanitizer="address",
        compose_hash="h1",
    )

    assert sorted(links) == sorted(
        [
            index / BY_TARGET / "proj" / "20261016-140307_exp-1-9b1c2f",
            index / BY_CRS / "crs-a" / "20261016-140307_exp-1-9b1c2f",
            index / BY_CRS / "crs-b" / "20261016-140307_exp-1-9b1c2f",
        ]
    )
    for link in links:
        assert link.is_symlink()
        assert not os.path.isabs(os.readlink(link))
        assert link.resolve() == run_dir.resolve()


def test_link_run_same_name_same_target_is_noop(tmp_path: Path) -> None:
    run_dir = _run_dir(tmp_path)
    kwargs = dict(
        target_name="proj",
        crs_names=["crs-a"],
        link_name="20261016-140307_exp-1-9b1c2f",
        sanitizer="address",
        compose_hash="h1",
    )

    first = link_run(_index(tmp_path), run_dir, **kwargs)
    second = link_run(_index(tmp_path), run_dir, **kwargs)

    assert first == second
    assert sorted(
        p.name for p in (_index(tmp_path) / BY_TARGET / "proj").iterdir()
    ) == ["20261016-140307_exp-1-9b1c2f"]


def test_link_run_replaces_stale_link_to_same_run_dir(tmp_path: Path) -> None:
    run_dir = _run_dir(tmp_path)
    index = _index(tmp_path)
    group = index / BY_TARGET / "proj"
    group.mkdir(parents=True)
    stale = group / "20260101-000000_exp-1-9b1c2f"
    stale.symlink_to(os.path.relpath(run_dir, start=group))

    link_run(
        index,
        run_dir,
        target_name="proj",
        crs_names=[],
        link_name="20261016-140307_exp-1-9b1c2f",
        sanitizer="address",
        compose_hash="h1",
    )

    assert not stale.is_symlink()
    assert [p.name for p in group.iterdir()] == ["20261016-140307_exp-1-9b1c2f"]


def test_link_run_collision_with_other_run_dir_appends_sanitizer(
    tmp_path: Path,
) -> None:
    other_run = _run_dir(tmp_path, hash_="h0")
    run_dir = _run_dir(tmp_path, hash_="h1")
    index = _index(tmp_path)
    name = "20261016-140307_exp-1-9b1c2f"
    link_run(
        index,
        other_run,
        target_name="proj",
        crs_names=[],
        link_name=name,
        sanitizer="address",
        compose_hash="h0",
    )

    links = link_run(
        index,
        run_dir,
        target_name="proj",
        crs_names=[],
        link_name=name,
        sanitizer="address",
        compose_hash="h1",
    )

    group = index / BY_TARGET / "proj"
    assert links == [group / f"{name}_address"]
    assert (group / name).resolve() == other_run.resolve()
    assert (group / f"{name}_address").resolve() == run_dir.resolve()


def test_link_run_never_replaces_real_directory(tmp_path: Path) -> None:
    run_dir = _run_dir(tmp_path)
    index = _index(tmp_path)
    name = "20261016-140307_exp-1-9b1c2f"
    group = index / BY_TARGET / "proj"
    for taken in (name, f"{name}_address", f"{name}_address_h1"):
        (group / taken).mkdir(parents=True)
        (group / taken / "keep").write_text("x")

    links = link_run(
        index,
        run_dir,
        target_name="proj",
        crs_names=["crs-a"],
        link_name=name,
        sanitizer="address",
        compose_hash="h1",
    )

    assert links == [index / BY_CRS / "crs-a" / name]
    for taken in (name, f"{name}_address", f"{name}_address_h1"):
        assert (group / taken / "keep").read_text() == "x"


# ---------------------------------------------------------------------------
# discovery / pruning
# ---------------------------------------------------------------------------


def test_find_links_to_returns_only_matching_group_links(tmp_path: Path) -> None:
    run_a = _run_dir(tmp_path, run_id="a")
    run_b = _run_dir(tmp_path, run_id="b")
    index = _index(tmp_path)
    for run_dir, name in ((run_a, "20260101-000000_a"), (run_b, "20260102-000000_b")):
        link_run(
            index,
            run_dir,
            target_name="proj",
            crs_names=["crs-a"],
            link_name=name,
            sanitizer="address",
            compose_hash="h1",
        )

    assert find_links_to(index, run_a) == [
        index / BY_TARGET / "proj" / "20260101-000000_a"
    ]
    assert find_links_to(index, run_a, group=BY_CRS) == [
        index / BY_CRS / "crs-a" / "20260101-000000_a"
    ]
    assert find_links_to(tmp_path / "missing", run_a) == []


def test_iter_index_links_lists_every_symlink(tmp_path: Path) -> None:
    run_dir = _run_dir(tmp_path)
    index = _index(tmp_path)
    link_run(
        index,
        run_dir,
        target_name="proj",
        crs_names=["crs-a"],
        link_name="20260101-000000_r",
        sanitizer="address",
        compose_hash="h1",
    )
    (index / BY_TARGET / "proj" / "not-a-link").write_text("x")

    assert iter_index_links(index) == [
        index / BY_TARGET / "proj" / "20260101-000000_r",
        index / BY_CRS / "crs-a" / "20260101-000000_r",
    ]
    assert iter_index_links(tmp_path / "missing") == []


def test_find_prunable_links_dangling_and_doomed(tmp_path: Path) -> None:
    doomed = _run_dir(tmp_path, run_id="doomed")
    live = _run_dir(tmp_path, run_id="live")
    gone = _run_dir(tmp_path, run_id="gone")
    index = _index(tmp_path)
    for run_dir, name in (
        (doomed, "20260101-000000_doomed"),
        (live, "20260102-000000_live"),
        (gone, "20260103-000000_gone"),
    ):
        link_run(
            index,
            run_dir,
            target_name="proj",
            crs_names=[],
            link_name=name,
            sanitizer="address",
            compose_hash="h1",
        )
    gone.rmdir()  # manual rm -> dangling link

    prunable = find_prunable_links(index, [doomed])

    group = index / BY_TARGET / "proj"
    assert prunable == [
        group / "20260101-000000_doomed",
        group / "20260103-000000_gone",
    ]


def test_unlink_if_dangling_only_removes_dangling_and_empty_group(
    tmp_path: Path,
) -> None:
    live = _run_dir(tmp_path, run_id="live")
    gone = _run_dir(tmp_path, run_id="gone")
    index = _index(tmp_path)
    link_run(
        index,
        live,
        target_name="proj",
        crs_names=[],
        link_name="20260102-000000_live",
        sanitizer="address",
        compose_hash="h1",
    )
    link_run(
        index,
        gone,
        target_name="other",
        crs_names=[],
        link_name="20260103-000000_gone",
        sanitizer="address",
        compose_hash="h1",
    )
    gone.rmdir()
    live_link = index / BY_TARGET / "proj" / "20260102-000000_live"
    gone_link = index / BY_TARGET / "other" / "20260103-000000_gone"

    assert unlink_if_dangling(live_link) is False
    assert unlink_if_dangling(gone_link) is True
    assert unlink_if_dangling(gone_link) is False  # already gone

    assert live_link.is_symlink()
    assert not gone_link.parent.exists()  # emptied group dir removed
    assert live_link.parent.exists()

# SPDX-License-Identifier: MIT
"""Unit tests for WorkDir.get_bug_candidate_dir() — the global shared DB dir."""

from oss_crs.src.workdir import WorkDir


def test_bug_candidate_dir_is_global(tmp_path):
    """The dir is a single top-level BUG_CANDIDATE_DIR — not partitioned by
    sanitizer/target/harness/run — so one DB serves the whole workdir."""
    workdir = WorkDir(tmp_path)

    path = workdir.get_bug_candidate_dir()

    assert path == workdir.root / "BUG_CANDIDATE_DIR"
    assert path.exists()
    # Not under any per-run / per-sanitizer partition.
    assert "runs" not in path.parts
    assert path.parent == workdir.root


def test_bug_candidate_dir_hangs_off_root_not_compose_hash(tmp_path):
    """Two different compose hashes must resolve to the SAME bug-candidate DB.

    The DB is partitioned internally by target_key+harness columns, so forking it
    per compose hash would mean two CRSs (or one CRS whose compose changed at all
    outside md5_hash()'s exclusion list) silently never see each other's
    candidates — defeating the point of a shared store.
    """
    root = tmp_path / "oss-crs-workdir"
    a = WorkDir(root / "crs_compose" / "aaaaaaaaaaaa", root=root)
    b = WorkDir(root / "crs_compose" / "bbbbbbbbbbbb", root=root)

    assert a.get_bug_candidate_dir() == b.get_bug_candidate_dir()
    assert a.get_bug_candidate_dir() == root / "BUG_CANDIDATE_DIR"
    # ...and specifically NOT under the compose-hash partition.
    assert "crs_compose" not in a.get_bug_candidate_dir().parts
    # Per-compose state still IS partitioned.
    assert a.path != b.path


def test_root_defaults_to_base_path(tmp_path):
    """Callers that aren't partitioning get the old behavior."""
    workdir = WorkDir(tmp_path / "wd")
    assert workdir.root == workdir.path
    assert workdir.get_bug_candidate_dir() == workdir.path / "BUG_CANDIDATE_DIR"


def test_bug_candidate_dir_create_flag(tmp_path):
    workdir = WorkDir(tmp_path / "wd")
    p = workdir.get_bug_candidate_dir(create=False)
    assert not p.exists()
    p2 = workdir.get_bug_candidate_dir()
    assert p2.exists() and p2 == p

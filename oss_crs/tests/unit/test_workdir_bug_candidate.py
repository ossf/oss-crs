# SPDX-License-Identifier: MIT
"""Unit tests for WorkDir.get_bug_candidate_dir() — the global shared DB dir."""

from oss_crs.src.workdir import WorkDir


def test_bug_candidate_dir_is_global(tmp_path):
    """The dir is a single top-level BUG_CANDIDATE_DIR — not partitioned by
    sanitizer/target/harness/run — so one DB serves the whole workdir."""
    workdir = WorkDir(tmp_path)

    path = workdir.get_bug_candidate_dir()

    assert path == workdir.path / "BUG_CANDIDATE_DIR"
    assert path.exists()
    # Not under any per-run / per-sanitizer partition.
    assert "runs" not in path.parts
    assert path.parent == workdir.path


def test_bug_candidate_dir_create_flag(tmp_path):
    workdir = WorkDir(tmp_path / "wd")
    p = workdir.get_bug_candidate_dir(create=False)
    assert not p.exists()
    p2 = workdir.get_bug_candidate_dir()
    assert p2.exists() and p2 == p

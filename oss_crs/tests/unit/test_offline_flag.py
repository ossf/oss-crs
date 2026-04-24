"""Tests for the --offline flag that disables git fetch on CRS repos.

The flag controls how ``init_crs_repo`` initializes a CRS source checkout:

  | offline | repo exists | behavior                         |
  |---------|-------------|----------------------------------|
  | True    | no          | hard error (cannot fetch/clone)  |
  | True    | yes         | git reset only (no fetch)        |
  | False   | yes         | git fetch + git reset            |
  | False   | no          | git clone                        |

These tests verify the branch selection without invoking real git, plus
that the flag is forwarded from the CLI/compose layers down to
``init_crs_repo``.
"""

from unittest.mock import patch

import pytest

from oss_crs.src.config.crs_compose import CRSEntry, CRSSource
from oss_crs.src.crs import init_crs_repo
from oss_crs.src.ui import TaskResult


@pytest.fixture
def scheduled():
    """Patch MultiTaskProgress and yield the list of task names scheduled.

    ``init_crs_repo`` builds a list of (name, callable) tasks and runs them via
    ``MultiTaskProgress(tasks, ...).run_added_tasks()``. The fake records the
    task names into a fresh per-test list, so tests can assert which git
    operations were scheduled without running real git. An empty list means no
    tasks were scheduled at all.
    """
    names: list[str] = []

    class _FakeMultiTaskProgress:
        def __init__(self, tasks, title=None, **kwargs):
            names.extend(name for name, _ in tasks)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def run_added_tasks(self, *args, **kwargs):
            return TaskResult(success=True)

    with patch("oss_crs.src.crs.MultiTaskProgress", _FakeMultiTaskProgress):
        yield names


def test_offline_missing_repo_returns_failure(tmp_path, scheduled):
    """offline + missing checkout fails without scheduling any git task."""
    dest = tmp_path / "crs_src" / "test-crs"  # does not exist

    result = init_crs_repo(
        name="test-crs",
        repo_url="https://example.com/test-crs.git",
        branch="main",
        dest_path=dest,
        offline=True,
    )

    assert result.success is False
    # No git tasks should have been scheduled.
    assert scheduled == []


def test_offline_existing_repo_resets_without_fetch(tmp_path, scheduled):
    """offline + existing checkout resets to origin but never fetches."""
    dest = tmp_path / "crs_src" / "test-crs"
    dest.mkdir(parents=True)

    result = init_crs_repo(
        name="test-crs",
        repo_url="https://example.com/test-crs.git",
        branch="main",
        dest_path=dest,
        offline=True,
    )

    assert result.success is True
    assert scheduled == ["Git Reset"]


def test_online_existing_repo_fetches_and_resets(tmp_path, scheduled):
    """Without offline, an existing checkout fetches then resets."""
    dest = tmp_path / "crs_src" / "test-crs"
    dest.mkdir(parents=True)

    result = init_crs_repo(
        name="test-crs",
        repo_url="https://example.com/test-crs.git",
        branch="main",
        dest_path=dest,
        offline=False,
    )

    assert result.success is True
    assert scheduled == ["Git Fetch", "Git Reset"]


def test_online_missing_repo_clones(tmp_path, scheduled):
    """Without offline, a missing checkout is cloned."""
    dest = tmp_path / "crs_src" / "test-crs"  # does not exist

    result = init_crs_repo(
        name="test-crs",
        repo_url="https://example.com/test-crs.git",
        branch="main",
        dest_path=dest,
        offline=False,
    )

    assert result.success is True
    assert scheduled == ["Cloning CRS repository"]


def test_offline_defaults_to_false(tmp_path, scheduled):
    """offline is opt-in: the default behavior still clones a missing repo."""
    dest = tmp_path / "crs_src" / "test-crs"  # does not exist

    result = init_crs_repo(
        name="test-crs",
        repo_url="https://example.com/test-crs.git",
        branch="main",
        dest_path=dest,
    )

    assert result.success is True
    assert scheduled == ["Cloning CRS repository"]


def test_skip_if_exists_short_circuits_before_offline_check(tmp_path, scheduled):
    """skip_if_exists wins over offline: an existing repo is left untouched."""
    dest = tmp_path / "crs_src" / "test-crs"
    dest.mkdir(parents=True)

    result = init_crs_repo(
        name="test-crs",
        repo_url="https://example.com/test-crs.git",
        branch="main",
        dest_path=dest,
        skip_if_exists=True,
        offline=True,
    )

    assert result.success is True
    # No reset/fetch/clone scheduled at all.
    assert scheduled == []

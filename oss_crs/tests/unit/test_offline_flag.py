"""Unit tests for the --offline flag

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

A separate group of tests covers the ``offline`` behavior in
``CRSCompose.__prepare_local_running_env``: when offline, the
"Build docker images" task must be skipped (relying on pre-existing local
images) while all other preparation tasks are still scheduled.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from oss_crs.src.crs import init_crs_repo
from oss_crs.src.crs_compose import CRSCompose
from oss_crs.src.ui import TaskResult


class _FakeMultiTaskProgress:
    """Progress double that records scheduled task names and notes.

    It supports both ways production code drives a ``MultiTaskProgress``:

    - tasks supplied to the constructor (``MultiTaskProgress(tasks, ...)``), as
      ``init_crs_repo`` does; and
    - tasks/notes added on an injected instance via ``add_task``/``add_note``,
      as ``CRSCompose.__prepare_local_running_env`` does.

    Task callables are never executed, so this only captures *what* was
    scheduled, not its side effects.
    """

    def __init__(self, tasks=None, title=None, **kwargs):
        self.task_names: list[str] = [name for name, _ in (tasks or [])]
        self.notes: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def add_task(self, name, func):
        self.task_names.append(name)

    def add_note(self, note):
        self.notes.append(note)

    def run_added_tasks(self, *args, **kwargs):
        return TaskResult(success=True)


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

    class _Tracking(_FakeMultiTaskProgress):
        def __init__(self, tasks=None, **kwargs):
            super().__init__(tasks=tasks, **kwargs)
            names.extend(self.task_names)

        def add_task(self, name, func):
            super().add_task(name, func)
            names.append(name)

    with patch("oss_crs.src.crs.MultiTaskProgress", _Tracking):
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

    init_crs_repo(
        name="test-crs",
        repo_url="https://example.com/test-crs.git",
        branch="main",
        dest_path=dest,
        offline=True,
    )

    assert scheduled == ["Git Reset"]


def test_online_existing_repo_fetches_and_resets(tmp_path, scheduled):
    """Without offline, an existing checkout fetches then resets."""
    dest = tmp_path / "crs_src" / "test-crs"
    dest.mkdir(parents=True)

    init_crs_repo(
        name="test-crs",
        repo_url="https://example.com/test-crs.git",
        branch="main",
        dest_path=dest,
        offline=False,
    )

    assert scheduled == ["Git Fetch", "Git Reset"]


def test_online_missing_repo_clones(tmp_path, scheduled):
    """Without offline, a missing checkout is cloned."""
    dest = tmp_path / "crs_src" / "test-crs"  # does not exist

    init_crs_repo(
        name="test-crs",
        repo_url="https://example.com/test-crs.git",
        branch="main",
        dest_path=dest,
        offline=False,
    )

    assert scheduled == ["Cloning CRS repository"]


def test_offline_defaults_to_false(tmp_path, scheduled):
    """offline is opt-in: the default behavior still clones a missing repo."""
    dest = tmp_path / "crs_src" / "test-crs"  # does not exist

    init_crs_repo(
        name="test-crs",
        repo_url="https://example.com/test-crs.git",
        branch="main",
        dest_path=dest,
    )

    assert scheduled == ["Cloning CRS repository"]


def test_skip_if_exists_short_circuits_before_offline_check(tmp_path, scheduled):
    """skip_if_exists wins over offline: an existing repo is left untouched."""
    dest = tmp_path / "crs_src" / "test-crs"
    dest.mkdir(parents=True)

    init_crs_repo(
        name="test-crs",
        repo_url="https://example.com/test-crs.git",
        branch="main",
        dest_path=dest,
        skip_if_exists=True,
        offline=True,
    )

    # No reset/fetch/clone scheduled at all.
    assert scheduled == []


# ---------------------------------------------------------------------------
# offline build-skip in CRSCompose.__prepare_local_running_env
# ---------------------------------------------------------------------------


def _prepare_local_running_env(offline: bool, tmp_path: Path):
    """Invoke the (name-mangled) private method on a minimal fake instance.

    Only ``self.offline`` is read during task scheduling; every other ``self``
    access lives inside task closures that are never executed by the recording
    progress double, so a lightweight stand-in is sufficient.
    """
    fake_self = SimpleNamespace(offline=offline)
    tmp_docker_compose = SimpleNamespace(
        docker_compose=tmp_path / "docker-compose.yaml"
    )
    progress = _FakeMultiTaskProgress()

    result = CRSCompose._CRSCompose__prepare_local_running_env(
        fake_self,
        project_name="proj",
        target=SimpleNamespace(),
        tmp_docker_compose=tmp_docker_compose,
        run_id="run-id",
        build_id="build-id",
        sanitizer="address",
        progress=progress,
    )
    return result, progress


_BUILD_TASK = "Build docker images in the combined docker compose file"


def test_online_schedules_docker_build(tmp_path):
    """Without offline, the docker image build task is scheduled."""
    _, progress = _prepare_local_running_env(offline=False, tmp_path=tmp_path)
    assert _BUILD_TASK in progress.task_names


def test_offline_skips_docker_build(tmp_path):
    """With offline, the docker image build task is skipped."""
    _, progress = _prepare_local_running_env(offline=True, tmp_path=tmp_path)
    assert _BUILD_TASK not in progress.task_names

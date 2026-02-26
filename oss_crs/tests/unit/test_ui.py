"""Unit tests for MultiTaskProgress cleanup semantics."""

from oss_crs.src.ui import MultiTaskProgress, TaskResult


def test_run_added_tasks_fails_on_cleanup_failure_by_default() -> None:
    progress = MultiTaskProgress(tasks=[], title="test")

    progress.add_task("main", lambda p: TaskResult(success=True))
    progress.add_cleanup_task("cleanup", lambda p: TaskResult(success=False, error="x"))

    result = progress.run_added_tasks()

    assert result.success is False


def test_run_added_tasks_can_ignore_cleanup_failure() -> None:
    progress = MultiTaskProgress(tasks=[], title="test")

    progress.add_task("main", lambda p: TaskResult(success=True))
    progress.add_cleanup_task("cleanup", lambda p: TaskResult(success=False, error="x"))

    result = progress.run_added_tasks(cleanup_failure_is_error=False)

    assert result.success is True

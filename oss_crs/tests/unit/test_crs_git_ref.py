# SPDX-License-Identifier: MIT
"""Unit tests for CRS git repository initialization and tag/branch ref handling."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure fcntl is stubbed on platforms where it is not available (e.g. Windows)
if "fcntl" not in sys.modules:
    try:
        import fcntl  # noqa: F401
    except ImportError:
        sys.modules["fcntl"] = MagicMock()

from oss_crs.src.crs import init_crs_repo
from oss_crs.src.ui import TaskResult


def test_init_crs_repo_clone_invokes_git_clone_with_ref(tmp_path: Path):
    dest_path = tmp_path / "crs_repo"
    recorded_commands = []

    def fake_streaming_output(cmd, cwd=None, **kwargs):
        recorded_commands.append(cmd)
        return TaskResult(success=True)

    with (
        patch("oss_crs.src.crs.file_lock"),
        patch("oss_crs.src.crs.MultiTaskProgress") as mock_progress_cls,
    ):
        mock_progress = MagicMock()
        mock_progress.run_command_with_streaming_output.side_effect = (
            fake_streaming_output
        )
        mock_progress.run_added_tasks.side_effect = lambda: (
            [fn(mock_progress) for _, fn in mock_progress_cls.call_args[0][0]],
            TaskResult(success=True),
        )[1]
        mock_progress.__enter__.return_value = mock_progress
        mock_progress_cls.return_value = mock_progress

        result = init_crs_repo(
            name="test-crs",
            repo_url="https://github.com/example/crs.git",
            branch="v1.0.0",
            dest_path=dest_path,
        )

        assert result.success is True
        assert len(recorded_commands) == 1
        assert recorded_commands[0] == [
            "git",
            "clone",
            "--recurse-submodules",
            "--branch",
            "v1.0.0",
            "https://github.com/example/crs.git",
            str(dest_path),
        ]


def test_init_crs_repo_online_resets_to_fetch_head_for_tags_and_branches(
    tmp_path: Path,
):
    """Verifies that online updates reset to FETCH_HEAD, supporting tags (#147)."""
    dest_path = tmp_path / "crs_repo"
    dest_path.mkdir()
    recorded_commands = []

    def fake_streaming_output(cmd, cwd=None, **kwargs):
        recorded_commands.append((cmd, cwd))
        return TaskResult(success=True)

    with (
        patch("oss_crs.src.crs.file_lock"),
        patch("oss_crs.src.crs.MultiTaskProgress") as mock_progress_cls,
    ):
        mock_progress = MagicMock()
        mock_progress.run_command_with_streaming_output.side_effect = (
            fake_streaming_output
        )
        mock_progress.run_added_tasks.side_effect = lambda: (
            [fn(mock_progress) for _, fn in mock_progress_cls.call_args[0][0]],
            TaskResult(success=True),
        )[1]
        mock_progress.__enter__.return_value = mock_progress
        mock_progress_cls.return_value = mock_progress

        result = init_crs_repo(
            name="test-crs",
            repo_url="https://github.com/example/crs.git",
            branch="v2.3.4",
            dest_path=dest_path,
            offline=False,
        )

        assert result.success is True
        assert len(recorded_commands) == 2
        # Task 1: Fetch from origin with ref
        assert recorded_commands[0] == (
            ["git", "fetch", "--recurse-submodules", "origin", "v2.3.4"],
            dest_path,
        )
        # Task 2: Reset to FETCH_HEAD (not origin/{branch})
        assert recorded_commands[1] == (
            ["git", "reset", "--hard", "FETCH_HEAD"],
            dest_path,
        )


def test_init_crs_repo_offline_existing_resets_to_ref(tmp_path: Path):
    dest_path = tmp_path / "crs_repo"
    dest_path.mkdir()
    recorded_commands = []

    def fake_streaming_output(cmd, cwd=None, **kwargs):
        recorded_commands.append((cmd, cwd))
        return TaskResult(success=True)

    with (
        patch("oss_crs.src.crs.file_lock"),
        patch("oss_crs.src.crs.MultiTaskProgress") as mock_progress_cls,
    ):
        mock_progress = MagicMock()
        mock_progress.run_command_with_streaming_output.side_effect = (
            fake_streaming_output
        )
        mock_progress.run_added_tasks.side_effect = lambda: (
            [fn(mock_progress) for _, fn in mock_progress_cls.call_args[0][0]],
            TaskResult(success=True),
        )[1]
        mock_progress.__enter__.return_value = mock_progress
        mock_progress_cls.return_value = mock_progress

        result = init_crs_repo(
            name="test-crs",
            repo_url="https://github.com/example/crs.git",
            branch="v2.3.4",
            dest_path=dest_path,
            offline=True,
        )

        assert result.success is True
        assert len(recorded_commands) == 1
        # Offline does not fetch; resets directly to branch/tag ref
        assert recorded_commands[0] == (
            ["git", "reset", "--hard", "v2.3.4"],
            dest_path,
        )


def test_init_crs_repo_offline_missing_fails(tmp_path: Path):
    dest_path = tmp_path / "nonexistent"

    with patch("oss_crs.src.crs.file_lock"):
        result = init_crs_repo(
            name="test-crs",
            repo_url="https://github.com/example/crs.git",
            branch="v1.0.0",
            dest_path=dest_path,
            offline=True,
        )

        assert result.success is False
        assert "does not exist and --offline is enabled" in result.error

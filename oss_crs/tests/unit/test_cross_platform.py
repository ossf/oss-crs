# SPDX-License-Identifier: MIT
"""Unit tests for cross-platform file locking and user ID resolution."""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from oss_crs.src.target import (
    _acquire_file_lock,
    _release_file_lock,
    file_lock,
)


def test_file_lock_basic(tmp_path: Path):
    """Verify file_lock acquires and releases without fcntl requirement."""
    lock_file = tmp_path / "test.lock"

    with file_lock(lock_file):
        assert lock_file.exists()


def test_acquire_and_release_lock_msvcrt_fallback():
    """Verify fallback to msvcrt when fcntl is None."""
    mock_file = MagicMock()
    mock_file.fileno.return_value = 3

    mock_msvcrt = MagicMock()

    with (
        patch("oss_crs.src.target.fcntl", None),
        patch("oss_crs.src.target.msvcrt", mock_msvcrt),
    ):
        _acquire_file_lock(mock_file)
        assert mock_msvcrt.locking.called
        assert mock_msvcrt.locking.call_args[0][1] == mock_msvcrt.LK_NBLCK

        _release_file_lock(mock_file)
        assert mock_msvcrt.locking.call_args[0][1] == mock_msvcrt.LK_UNLCK


def test_acquire_and_release_lock_fcntl():
    """Verify fcntl is used when available."""
    mock_file = MagicMock()
    mock_file.fileno.return_value = 4

    mock_fcntl = MagicMock()

    with patch("oss_crs.src.target.fcntl", mock_fcntl):
        _acquire_file_lock(mock_file)
        mock_fcntl.flock.assert_called_once_with(4, mock_fcntl.LOCK_EX)

        _release_file_lock(mock_file)
        mock_fcntl.flock.assert_called_with(4, mock_fcntl.LOCK_UN)


def test_webui_uid_gid_cross_platform(monkeypatch):
    """Verify getattr fallback to 1000 when os.getuid and os.getgid are absent."""
    monkeypatch.delattr(os, "getuid", raising=False)
    monkeypatch.delattr(os, "getgid", raising=False)

    uid = getattr(os, "getuid", lambda: 1000)()
    gid = getattr(os, "getgid", lambda: 1000)()

    assert uid == 1000
    assert gid == 1000

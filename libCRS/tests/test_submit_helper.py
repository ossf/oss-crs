# SPDX-License-Identifier: MIT
"""Unit tests for libCRS SubmitHelper and file-system watching event handlers."""

from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest
from libCRS.submit import NewFileHandler, SubmitData, SubmitHelper
from watchdog.events import (
    DirCreatedEvent,
    DirModifiedEvent,
    DirMovedEvent,
    FileCreatedEvent,
    FileModifiedEvent,
    FileMovedEvent,
)


# ===========================================================================
# 1. NewFileHandler Event Handling Tests
# ===========================================================================
def test_new_file_handler_created_file_invokes_callback(tmp_path: Path):
    cb = MagicMock()
    handler = NewFileHandler(cb)
    test_file = tmp_path / "test.patch"

    event = FileCreatedEvent(str(test_file))
    handler.on_created(event)

    cb.assert_called_once_with(test_file)


def test_new_file_handler_created_directory_ignored(tmp_path: Path):
    cb = MagicMock()
    handler = NewFileHandler(cb)
    test_dir = tmp_path / "sub_dir"

    # Directory creation events should not invoke callback
    event = DirCreatedEvent(str(test_dir))
    handler.on_created(event)

    cb.assert_not_called()


def test_new_file_handler_modified_file_invokes_callback(tmp_path: Path):
    cb = MagicMock()
    handler = NewFileHandler(cb)
    test_file = tmp_path / "seed.bin"

    event = FileModifiedEvent(str(test_file))
    handler.on_modified(event)

    cb.assert_called_once_with(test_file)


def test_new_file_handler_modified_directory_ignored(tmp_path: Path):
    cb = MagicMock()
    handler = NewFileHandler(cb)
    test_dir = tmp_path / "sub_dir"

    event = DirModifiedEvent(str(test_dir))
    handler.on_modified(event)

    cb.assert_not_called()


def test_new_file_handler_moved_file_uses_dest_path(tmp_path: Path):
    cb = MagicMock()
    handler = NewFileHandler(cb)
    src_file = tmp_path / "temp.tmp"
    dst_file = tmp_path / "final.patch"

    event = FileMovedEvent(str(src_file), str(dst_file))
    handler.on_moved(event)

    cb.assert_called_once_with(dst_file)


def test_new_file_handler_moved_directory_ignored(tmp_path: Path):
    cb = MagicMock()
    handler = NewFileHandler(cb)
    src_dir = tmp_path / "src_dir"
    dst_dir = tmp_path / "dst_dir"

    event = DirMovedEvent(str(src_dir), str(dst_dir))
    handler.on_moved(event)

    cb.assert_not_called()


def test_new_file_handler_moved_missing_dest_path(tmp_path: Path):
    cb = MagicMock()
    handler = NewFileHandler(cb)
    src_file = tmp_path / "temp.tmp"

    event = FileMovedEvent(str(src_file), "")
    event.dest_path = None
    handler.on_moved(event)

    cb.assert_not_called()


# ===========================================================================
# 2. SubmitHelper submit_file Tests
# ===========================================================================
def test_submit_file_success(tmp_path: Path):
    shared_fs_dir = tmp_path / "shared"
    shared_fs_dir.mkdir()
    src_file = tmp_path / "exploit.pov"
    src_file.write_bytes(b"crash-data-12345")

    helper = SubmitHelper(shared_fs_dir)

    with (
        patch("libCRS.submit.file_hash", return_value="deadbeefcafe"),
        patch("libCRS.submit.rsync_copy") as mock_rsync,
    ):
        helper.submit_file(src_file)

        expected_dst = shared_fs_dir / "deadbeefcafe.pov"
        mock_rsync.assert_called_once_with(src_file, expected_dst)


def test_submit_file_shared_dir_none(tmp_path: Path):
    src_file = tmp_path / "file.txt"
    src_file.write_text("hello")

    helper = SubmitHelper(shared_fs_dir=None)

    with (
        patch("libCRS.submit.file_hash", return_value="hash123"),
        patch("libCRS.submit.rsync_copy") as mock_rsync,
    ):
        helper.submit_file(src_file)
        mock_rsync.assert_not_called()


def test_submit_file_already_exists_skipped(tmp_path: Path):
    shared_fs_dir = tmp_path / "shared"
    shared_fs_dir.mkdir()
    existing_dest = shared_fs_dir / "hash123.bin"
    existing_dest.write_text("already here")

    src_file = tmp_path / "new.bin"
    src_file.write_bytes(b"data")

    helper = SubmitHelper(shared_fs_dir)

    with (
        patch("libCRS.submit.file_hash", return_value="hash123"),
        patch("libCRS.submit.rsync_copy") as mock_rsync,
    ):
        helper.submit_file(src_file)
        mock_rsync.assert_not_called()


# ===========================================================================
# 3. SubmitHelper Batch Enqueue & Flush Logic
# ===========================================================================
def test_enqueue_empty_file_ignored(tmp_path: Path):
    shared_fs_dir = tmp_path / "shared"
    shared_fs_dir.mkdir()
    empty_file = tmp_path / "empty.txt"
    empty_file.write_bytes(b"")

    helper = SubmitHelper(shared_fs_dir)
    helper._SubmitHelper__enqueue_file(empty_file)

    assert len(helper.queue) == 0


def test_enqueue_oserror_handled(tmp_path: Path):
    shared_fs_dir = tmp_path / "shared"
    shared_fs_dir.mkdir()
    nonexistent = tmp_path / "ghost.txt"

    helper = SubmitHelper(shared_fs_dir)
    helper._SubmitHelper__enqueue_file(nonexistent)

    assert len(helper.queue) == 0


def test_enqueue_already_existing_dst_skipped(tmp_path: Path):
    shared_fs_dir = tmp_path / "shared"
    shared_fs_dir.mkdir()
    (shared_fs_dir / "abc.dat").write_text("already present")

    src_file = tmp_path / "input.dat"
    src_file.write_text("fresh content")

    helper = SubmitHelper(shared_fs_dir)
    with patch("libCRS.submit.file_hash", return_value="abc"):
        helper._SubmitHelper__enqueue_file(src_file)

    assert len(helper.queue) == 0


def test_flush_batch_size_threshold(tmp_path: Path):
    shared_fs_dir = tmp_path / "shared"
    shared_fs_dir.mkdir()

    f1 = tmp_path / "f1.bin"
    f2 = tmp_path / "f2.bin"
    f1.write_bytes(b"1")
    f2.write_bytes(b"2")

    helper = SubmitHelper(shared_fs_dir)
    helper.queue.append(SubmitData(file_path=f1, content_hash="hash1"))
    helper.queue.append(SubmitData(file_path=f2, content_hash="hash2"))

    with patch("libCRS.submit.rsync_copy") as mock_rsync:
        flushed = helper._SubmitHelper__flush(batch_time=60.0, batch_size=2)
        assert flushed is True
        assert len(helper.queue) == 0
        assert mock_rsync.call_count == 2
        mock_rsync.assert_has_calls(
            [
                call(f1, shared_fs_dir / "hash1.bin"),
                call(f2, shared_fs_dir / "hash2.bin"),
            ],
            any_order=True,
        )


def test_flush_below_threshold_returns_false(tmp_path: Path):
    shared_fs_dir = tmp_path / "shared"
    shared_fs_dir.mkdir()
    f1 = tmp_path / "f1.bin"
    f1.write_bytes(b"1")

    helper = SubmitHelper(shared_fs_dir)
    helper.queue.append(SubmitData(file_path=f1, content_hash="hash1"))
    # Not enough time has passed and batch_size not reached
    flushed = helper._SubmitHelper__flush(batch_time=300.0, batch_size=5)
    assert flushed is False
    assert len(helper.queue) == 1


def test_flush_batch_time_exceeded(tmp_path: Path):
    shared_fs_dir = tmp_path / "shared"
    shared_fs_dir.mkdir()
    f1 = tmp_path / "f1.bin"
    f1.write_bytes(b"1")

    helper = SubmitHelper(shared_fs_dir)
    helper.queue.append(SubmitData(file_path=f1, content_hash="hash1"))
    helper._last_flush_time = 0.0  # Simulate long elapsed time

    with patch("libCRS.submit.rsync_copy") as mock_rsync:
        flushed = helper._SubmitHelper__flush(batch_time=10.0, batch_size=10)
        assert flushed is True
        assert len(helper.queue) == 0
        mock_rsync.assert_called_once_with(f1, shared_fs_dir / "hash1.bin")


def test_flush_handles_rsync_exception(tmp_path: Path):
    shared_fs_dir = tmp_path / "shared"
    shared_fs_dir.mkdir()
    f1 = tmp_path / "f1.bin"
    f1.write_bytes(b"1")

    helper = SubmitHelper(shared_fs_dir)
    helper.queue.append(SubmitData(file_path=f1, content_hash="hash1"))

    with (
        patch("libCRS.submit.rsync_copy", side_effect=RuntimeError("rsync error")),
        patch("libCRS.submit.logger.exception") as mock_log,
    ):
        flushed = helper._SubmitHelper__flush(batch_time=0.0, batch_size=1)
        assert flushed is True
        mock_log.assert_called_once()


# ===========================================================================
# 4. SubmitHelper Directory Registration & Lifecycle
# ===========================================================================
def test_register_dir_already_registered_raises(tmp_path: Path):
    watch_dir = tmp_path / "watch"
    watch_dir.mkdir()

    helper = SubmitHelper(shared_fs_dir=tmp_path / "shared")
    helper.registered_dir = watch_dir

    with pytest.raises(RuntimeError, match="Directory already registered"):
        helper.register_dir(watch_dir, batch_time=5, batch_size=10)


def test_register_dir_processes_existing_files_and_cleans_up(tmp_path: Path):
    watch_dir = tmp_path / "watch"
    watch_dir.mkdir()
    shared_dir = tmp_path / "shared"
    shared_dir.mkdir()

    # Pre-populate directory with data file and non-data file
    valid_file = watch_dir / "candidate.patch"
    valid_file.write_text("diff --git a b")
    ignored_file = watch_dir / ".hidden_ignore"
    ignored_file.write_text("ignore me")

    helper = SubmitHelper(shared_dir)

    mock_observer = MagicMock()
    with (
        patch("libCRS.submit.Observer", return_value=mock_observer),
        patch("time.sleep", side_effect=KeyboardInterrupt),
        patch("libCRS.submit.rsync_copy") as mock_rsync,
    ):
        with pytest.raises(KeyboardInterrupt):
            helper.register_dir(watch_dir, batch_time=1, batch_size=1)

        assert helper.registered_dir == watch_dir
        mock_observer.schedule.assert_called_once()
        mock_observer.start.assert_called_once()
        mock_observer.stop.assert_called_once()
        mock_observer.join.assert_called_once()
        # Verify valid_file was flushed
        assert mock_rsync.call_count >= 1

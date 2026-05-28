# SPDX-License-Identifier: MIT
import stat

from oss_crs.src.target import (
    SHARED_LOCK_DIR_MODE,
    SHARED_LOCK_FILE_MODE,
    file_lock,
)


def test_shared_file_lock_sets_shared_modes(tmp_path):
    lock_path = tmp_path / "snapshot-locks" / "snapshot-abc.lock"

    with file_lock(lock_path, shared_permissions=True):
        dir_mode = lock_path.parent.stat().st_mode
        file_mode = lock_path.stat().st_mode

        assert dir_mode & 0o777 == SHARED_LOCK_DIR_MODE & 0o777
        assert dir_mode & stat.S_ISVTX
        assert file_mode & 0o777 == SHARED_LOCK_FILE_MODE

    assert lock_path.exists()


def test_shared_file_lock_reuses_read_only_existing_lock_file(tmp_path):
    lock_dir = tmp_path / "snapshot-locks"
    lock_dir.mkdir()
    lock_path = lock_dir / "snapshot-abc.lock"
    lock_path.touch()
    lock_path.chmod(0o444)

    with file_lock(lock_path, shared_permissions=True):
        assert lock_path.exists()

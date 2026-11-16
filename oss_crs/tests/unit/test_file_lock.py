# SPDX-License-Identifier: MIT
import stat
import subprocess
import sys

from oss_crs.src.target import (
    SHARED_LOCK_DIR_MODE,
    SHARED_LOCK_FILE_MODE,
    file_lock,
)


def test_shared_file_lock_sets_shared_modes(tmp_path):
    lock_path = tmp_path / "snapshot-locks" / "snapshot-abc.lock"

    with file_lock(lock_path, shared_permissions=True):
        assert lock_path.exists()
        if sys.platform != "win32":
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


def test_file_lock_basic(tmp_path):
    lock_path = tmp_path / "regular.lock"
    with file_lock(lock_path):
        assert lock_path.exists()
    assert lock_path.exists()


def test_file_lock_mutual_exclusion(tmp_path):
    lock_path = tmp_path / "mutex.lock"
    with file_lock(lock_path):
        worker_code = (
            "import sys\n"
            "try:\n"
            "    import fcntl\n"
            "except ImportError:\n"
            "    fcntl = None\n"
            "try:\n"
            "    import msvcrt\n"
            "except ImportError:\n"
            "    msvcrt = None\n"
            f"with open(r'{lock_path}', 'r+') as f:\n"
            "    if fcntl is not None:\n"
            "        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
            "    elif msvcrt is not None:\n"
            "        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)\n"
            "sys.exit(0)\n"
        )
        proc = subprocess.run(
            [sys.executable, "-c", worker_code],
            capture_output=True,
        )
        # Should fail with non-zero exit code because lock is already held
        assert proc.returncode != 0

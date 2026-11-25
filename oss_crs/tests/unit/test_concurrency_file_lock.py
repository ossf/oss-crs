# SPDX-License-Identifier: MIT
"""Concurrency and race condition tests for CRS file-lock (oss_crs/src/target.py)."""

import sys
import threading
import time
from pathlib import Path
from types import ModuleType

import pytest

# Cross-platform fcntl support: on Linux uses native fcntl; on Windows emulates flock mutual exclusion.
if "fcntl" not in sys.modules:
    try:
        import fcntl  # noqa: F401
    except ImportError:
        _fcntl_mock = ModuleType("fcntl")
        _fcntl_mock.LOCK_EX = 2
        _fcntl_mock.LOCK_NB = 4
        _fcntl_mock.LOCK_UN = 8

        _global_locks: dict[int, threading.Lock] = {}
        _lock_registry_guard = threading.Lock()

        def _get_or_create_mutex(fd: int) -> threading.Lock:
            with _lock_registry_guard:
                if fd not in _global_locks:
                    _global_locks[fd] = threading.Lock()
                return _global_locks[fd]

        # Emulate lock on the target file path
        _path_locks: dict[str, threading.Lock] = {}

        def _mock_flock(fd: int, operation: int):
            with _lock_registry_guard:
                if "global" not in _path_locks:
                    _path_locks["global"] = threading.Lock()
                target_lock = _path_locks["global"]

            if operation & _fcntl_mock.LOCK_EX:
                if operation & _fcntl_mock.LOCK_NB:
                    acquired = target_lock.acquire(blocking=False)
                    if not acquired:
                        raise BlockingIOError("Resource temporarily unavailable")
                else:
                    target_lock.acquire(blocking=True)
            elif operation & _fcntl_mock.LOCK_UN:
                try:
                    target_lock.release()
                except RuntimeError:
                    pass

        _fcntl_mock.flock = _mock_flock
        sys.modules["fcntl"] = _fcntl_mock

from oss_crs.src.target import file_lock


def test_file_lock_mutual_exclusion(tmp_path: Path):
    """Verify that multiple concurrent threads serialize execution and never overlap in critical section."""
    lock_path = tmp_path / "race_test.lock"
    shared_counter = 0
    active_threads = 0
    max_concurrent_threads = 0
    lock_state = threading.Lock()
    num_threads = 10
    iterations_per_thread = 5

    def worker():
        nonlocal shared_counter, active_threads, max_concurrent_threads
        for _ in range(iterations_per_thread):
            with file_lock(lock_path):
                with lock_state:
                    active_threads += 1
                    max_concurrent_threads = max(max_concurrent_threads, active_threads)
                # Critical section: simulate work
                time.sleep(0.005)
                current = shared_counter
                time.sleep(0.005)
                shared_counter = current + 1

                with lock_state:
                    active_threads -= 1

    threads = [threading.Thread(target=worker) for _ in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Mutual exclusion guarantee: at no point should >1 thread have been inside the lock
    assert max_concurrent_threads == 1
    assert shared_counter == num_threads * iterations_per_thread


def test_file_lock_releases_on_exception(tmp_path: Path):
    """Verify that if an exception is raised inside the critical section, the lock is released."""
    lock_path = tmp_path / "exception_test.lock"

    # Holder 1 raises an error inside lock
    with pytest.raises(RuntimeError, match="Intentional failure"), file_lock(lock_path):
        raise RuntimeError("Intentional failure")

    # Holder 2 should immediately be able to acquire the lock without deadlocking
    acquired = False
    with file_lock(lock_path):
        acquired = True

    assert acquired is True


def test_file_lock_contention_blocks_until_released(tmp_path: Path):
    """Verify that thread 2 is blocked until thread 1 exits the critical section."""
    lock_path = tmp_path / "block_test.lock"
    order = []
    ready_evt = threading.Event()

    def holder_one():
        with file_lock(lock_path):
            order.append("holder_1_acquired")
            ready_evt.set()
            time.sleep(0.05)
            order.append("holder_1_released")

    def holder_two():
        ready_evt.wait()  # Ensure holder 1 holds lock
        with file_lock(lock_path):
            order.append("holder_2_acquired")

    t1 = threading.Thread(target=holder_one)
    t2 = threading.Thread(target=holder_two)

    t1.start()
    t2.start()

    t1.join()
    t2.join()

    assert order == [
        "holder_1_acquired",
        "holder_1_released",
        "holder_2_acquired",
    ]


def test_shared_permissions_concurrent_creation(tmp_path: Path):
    """Verify parallel creation with shared_permissions=True does not cause race conditions."""
    lock_path = tmp_path / "shared" / "shared_concurrent.lock"
    errors = []

    def worker():
        try:
            with file_lock(lock_path, shared_permissions=True):
                time.sleep(0.005)
        except (OSError, RuntimeError) as e:
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert lock_path.exists()

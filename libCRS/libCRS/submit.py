# SPDX-License-Identifier: MIT
import os
import logging
import tarfile
import tempfile
import time
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent

from .common import file_hash, is_data_file, rsync_copy

logger = logging.getLogger(__name__)


def _unique_tarball_path(reports_dir: Path, base: str) -> Path:
    """Return `<reports_dir>/<base>.tar.gz`, appending `_N` on collision.

    So a second submission of the same name yields `<base>_1.tar.gz`, a third
    `<base>_2.tar.gz`, and so on.
    """
    candidate = reports_dir / f"{base}.tar.gz"
    counter = 1
    while candidate.exists():
        candidate = reports_dir / f"{base}_{counter}.tar.gz"
        counter += 1
    return candidate


def submit_report(src: Path, reports_dir: Path) -> Path:
    """Bundle a report file or directory into a gzip tarball under reports_dir.

    - Directory: archived *without* its top-level directory component, so its
      contents sit at the tarball root; the tarball is named after the
      directory.
    - File: archived on its own; the tarball is named after the file's stem
      (the name minus its final suffix).

    Name collisions are resolved with a `_N` counter suffix, so submitting
    `foo.md` twice yields `foo.tar.gz` then `foo_1.tar.gz`. Returns the path of
    the created tarball.
    """
    src = Path(src)
    if not src.exists():
        raise FileNotFoundError(f"submit report: path does not exist: {src}")

    if src.is_dir():
        base = src.name
    elif src.is_file():
        base = src.stem or src.name
    else:
        raise ValueError(f"submit report: not a regular file or directory: {src}")

    if not base or base in (".", ".."):
        raise ValueError(f"submit report: cannot derive a tarball name from {src}")

    reports_dir.mkdir(parents=True, exist_ok=True)
    dst = _unique_tarball_path(reports_dir, base)

    # Build into a temp file in the same dir, then atomically rename, so a
    # partial archive is never observed under the final name.
    fd, tmp_name = tempfile.mkstemp(dir=reports_dir, prefix=f".{base}.", suffix=".tmp")
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        with tarfile.open(tmp_path, "w:gz") as tar:
            if src.is_dir():
                for item in sorted(src.iterdir()):
                    tar.add(item, arcname=item.name)
            else:
                tar.add(src, arcname=src.name)
        tmp_path.replace(dst)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    return dst


@dataclass
class SubmitData:
    file_path: Path
    content_hash: str


class NewFileHandler(FileSystemEventHandler):
    def __init__(self, callback: Callable[[Path], None]):
        super().__init__()
        self.callback = callback

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self.callback(Path(os.fsdecode(event.src_path)))

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self.callback(Path(os.fsdecode(event.src_path)))

    def on_moved(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        dest_path = getattr(event, "dest_path", None)
        if dest_path is not None:
            self.callback(Path(os.fsdecode(dest_path)))


class SubmitHelper:
    def __init__(self, shared_fs_dir: Path | None):
        self.shared_fs_dir = shared_fs_dir
        self.queue: list[SubmitData] = []
        self.queue_lock = threading.Lock()
        self.flush_lock = threading.Lock()

        self.registered_dir: Path | None = None
        self._last_flush_time = time.time()

    def __dst_path(self, content_hash: str, suffix: str) -> Path | None:
        if self.shared_fs_dir is None:
            return None
        return self.shared_fs_dir / f"{content_hash}{suffix}"

    def __enqueue_file(self, file_path: Path) -> None:
        try:
            if file_path.stat().st_size == 0:
                return
            content_hash = file_hash(file_path)
        except OSError:
            return
        suffix = file_path.suffix or ""
        dst = self.__dst_path(content_hash, suffix)
        if dst is not None and dst.exists():
            return
        with self.queue_lock:
            self.queue.append(
                SubmitData(file_path=file_path, content_hash=content_hash)
            )

    def __flush(self, batch_time: float, batch_size: int) -> bool:
        cur_queue: list[SubmitData] = []
        with self.queue_lock:
            should_flush = len(self.queue) >= batch_size or (
                len(self.queue) > 0
                and (time.time() - self._last_flush_time) >= batch_time
            )
            if not should_flush:
                return False
            cur_queue, self.queue = self.queue, cur_queue

        with self.flush_lock:
            for item in cur_queue:
                suffix = item.file_path.suffix or ""
                dst = self.__dst_path(item.content_hash, suffix)
                if dst is None or dst.exists():
                    continue
                try:
                    rsync_copy(item.file_path, dst)
                except Exception:
                    logger.exception("flush failed for %s", item.file_path)
        self._last_flush_time = time.time()
        return True

    def submit_file(self, file_path: Path) -> None:
        """Submit a single file to SUBMIT_DIR."""
        content_hash = file_hash(file_path)
        suffix = file_path.suffix or ""
        dst = self.__dst_path(content_hash, suffix)
        if dst is not None and not dst.exists():
            rsync_copy(file_path, dst)

    def register_dir(self, dir_path: Path, batch_time: int, batch_size: int) -> None:
        """Watch a directory for new files and submit them in batches.

        Args:
            dir_path: Directory to watch for new files
            batch_time: Maximum time in seconds to wait before flushing the batch
            batch_size: Maximum number of files to accumulate before flushing
        """
        if self.registered_dir is not None:
            raise RuntimeError("Directory already registered")
        self.registered_dir = dir_path

        def handle_new_file(file_path: Path) -> None:
            if is_data_file(file_path):
                self.__enqueue_file(file_path)
                self.__flush(batch_time, batch_size)

        # Process existing files in the directory
        for file_path in dir_path.iterdir():
            handle_new_file(file_path)

        # Set up the observer
        observer = Observer()
        observer.schedule(
            NewFileHandler(handle_new_file), str(dir_path), recursive=False
        )
        observer.start()

        try:
            while True:
                time.sleep(batch_time)
                self.__flush(batch_time, batch_size)
        finally:
            observer.stop()
            observer.join()
            self.__flush(batch_time, batch_size)

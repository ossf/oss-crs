from pathlib import Path
from typing import Optional

import time
import threading
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileCreatedEvent, FileMovedEvent

from .infra_client import InfraClient, SubmitData
from .common import file_hash, is_data_file, rsync_copy


class NewFileHandler(FileSystemEventHandler):
    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    def on_created(self, event: FileCreatedEvent) -> None:
        if not event.is_directory:
            self.callback(Path(event.src_path))

    def on_moved(self, event: FileMovedEvent) -> None:
        if not event.is_directory:
            self.callback(Path(event.dest_path))


class SubmitHelper:
    def __init__(self, data_type: str, shared_fs_dir: Optional[Path], infra_client: InfraClient):
        self.data_type = data_type
        self.shared_fs_dir = shared_fs_dir
        self.infra_client = infra_client
        self.submitted = set()
        self.queue = []
        self.queue_lock = threading.Lock()
        self.flush_lock = threading.Lock()

        self.registered_dir: Optional[Path] = None
        self._last_flush_time = time.time()

    def __enqueue_file(self, file_path: Path) -> None:
        hash = file_hash(file_path)
        with self.queue_lock:
            if hash in self.submitted:
                return
            self.queue.append(SubmitData(file_path=file_path, hash=hash))
            self.submitted.add(hash)

    def __flush(self, batch_time: float, batch_size: int) -> bool:
        cur_queue = []
        with self.queue_lock:
            should_flush = len(self.queue) >= batch_size or (
                len(self.queue) > 0
                and (time.time() - self._last_flush_time) >= batch_time
            )
            if not should_flush:
                return False
            cur_queue, self.queue = self.queue, cur_queue

        with self.flush_lock:
            if self.shared_fs_dir is not None:
                for item in cur_queue:
                    suffix = item.file_path.suffix or ""
                    rsync_copy(item.file_path, self.shared_fs_dir / f"{item.hash}{suffix}")
            self.infra_client.submit_batch(self.data_type, cur_queue, False)
        self._last_flush_time = time.time()
        return True

    def submit_file(self, file_path: Path) -> None:
        """Submit a single file to both SUBMIT_DIR and EXCHANGE_DIR."""
        hash = file_hash(file_path)
        item = SubmitData(file_path=file_path, hash=hash)

        if self.shared_fs_dir is not None:
            suffix = file_path.suffix or ""
            rsync_copy(file_path, self.shared_fs_dir / f"{hash}{suffix}")
        self.infra_client.submit_batch(self.data_type, [item], False)

    def register_dir(self, dir_path: Path, batch_time: int, batch_size: int) -> None:
        """
        Watch a directory for new files and submit them in batches.

        Args:
            dir_path: Directory to watch for new files
            batch_time: Maximum time in seconds to wait before flushing the batch
            batch_size: Maximum number of files to accumulate before flushing
        """
        assert self.registered_dir is None, "Directory already registered"
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

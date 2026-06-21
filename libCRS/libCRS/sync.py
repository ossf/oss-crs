# SPDX-License-Identifier: MIT
import logging
import time
from pathlib import Path

from .common import rsync_copy
from .fetch import POLL_INTERVAL

logger = logging.getLogger(__name__)


class DirSyncHelper:
    def __init__(self, src: Path, dst: Path):
        self.src = Path(src)
        self.dst = Path(dst)

    def sync_once(self) -> None:
        self.dst.mkdir(parents=True, exist_ok=True)
        rsync_copy(self.src, self.dst)

    def register_dir(self) -> None:
        self.sync_once()
        try:
            while True:
                time.sleep(POLL_INTERVAL)
                try:
                    self.sync_once()
                except Exception:
                    logger.exception("sync_once failed, will retry")
        finally:
            self.sync_once()

import logging
import os
from pathlib import Path

from .common import is_data_file, rsync_copy_files

logger = logging.getLogger(__name__)


class InfraClient:
    def __init__(self):
        self._fetch_dir = None
        self._fetch_dir_loaded = False

    def _get_fetch_dir(self) -> Path | None:
        if not self._fetch_dir_loaded:
            self._fetch_dir_loaded = True
            raw = os.environ.get("OSS_CRS_FETCH_DIR")
            if raw:
                self._fetch_dir = Path(raw)
        return self._fetch_dir

    def fetch_new(self, data_type: str, dst: Path) -> list[str]:
        """Fetch new data files from FETCH_DIR to dst. Returns new filenames."""
        fetch_dir = self._get_fetch_dir()
        if fetch_dir is None:
            return []

        type_dir = fetch_dir / str(data_type)
        if not type_dir.is_dir():
            return []

        new_files = []
        for f in type_dir.iterdir():
            dst_file = dst / f.name
            if is_data_file(f) and not dst_file.exists():
                new_files.append(f.name)
        rsync_copy_files(type_dir, new_files, dst)
        return new_files

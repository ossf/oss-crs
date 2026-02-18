import logging
import os
from pathlib import Path
from dataclasses import dataclass

from .common import is_data_file, rsync_copy

logger = logging.getLogger(__name__)


@dataclass
class SubmitData:
    file_path: Path
    hash: str


class InfraClient:
    def __init__(self):
        self._exchange_dir = None
        self._exchange_dir_loaded = False

    def _get_exchange_dir(self) -> Path | None:
        if not self._exchange_dir_loaded:
            self._exchange_dir_loaded = True
            raw = os.environ.get("OSS_CRS_EXCHANGE_DIR")
            if raw:
                self._exchange_dir = Path(raw)
        return self._exchange_dir

    def submit_batch(
        self, data_type: str, data_list: list[SubmitData], ship_file_data: bool
    ) -> None:
        exchange_dir = self._get_exchange_dir()
        if exchange_dir is None:
            return

        type_dir = exchange_dir / str(data_type)
        type_dir.mkdir(parents=True, exist_ok=True)

        for item in data_list:
            suffix = item.file_path.suffix or ""
            rsync_copy(item.file_path, type_dir / f"{item.hash}{suffix}")

    def fetch_new(
        self, data_type: str, dst: Path, known: set[str]
    ) -> list[str]:
        """Fetch new data files from EXCHANGE_DIR to dst. Returns new filenames."""
        exchange_dir = self._get_exchange_dir()
        if exchange_dir is None:
            return []

        type_dir = exchange_dir / str(data_type)
        if not type_dir.is_dir():
            return []

        new_files = []
        for f in type_dir.iterdir():
            if is_data_file(f) and f.name not in known:
                rsync_copy(f, dst / f.name)
                new_files.append(f.name)
        return new_files

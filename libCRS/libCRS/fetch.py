import threading
import time
from pathlib import Path

from .infra_client import InfraClient

POLL_INTERVAL = 5  # seconds


class FetchHelper:
    """Inbound counterpart of SubmitHelper — fetches files via InfraClient."""

    def __init__(self, data_type: str, infra_client: InfraClient):
        self.data_type = data_type
        self.infra_client = infra_client
        self.fetched: set[str] = set()
        self.lock = threading.Lock()

    def fetch_once(self, dst: Path) -> list[str]:
        """One-shot fetch: get new files from infra, copy to dst."""
        dst.mkdir(parents=True, exist_ok=True)
        with self.lock:
            new_files = self.infra_client.fetch_new(self.data_type, dst, self.fetched)
            self.fetched.update(new_files)
        return new_files

    def register_dir(self, dst: Path) -> None:
        """Periodic polling of EXCHANGE_DIR for new files.

        This blocks forever (intended to run inside a DaemonContext).
        """
        dst.mkdir(parents=True, exist_ok=True)

        # Initial sync
        self.fetch_once(dst)

        try:
            while True:
                time.sleep(POLL_INTERVAL)
                self.fetch_once(dst)
        finally:
            self.fetch_once(dst)

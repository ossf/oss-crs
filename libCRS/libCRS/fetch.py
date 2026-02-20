import logging
import time
from pathlib import Path

from .base import DataType
from .infra_client import InfraClient

logger = logging.getLogger(__name__)

POLL_INTERVAL = 5  # seconds


class FetchHelper:
    """Inbound counterpart of SubmitHelper — fetches files via InfraClient."""

    def __init__(self, data_type: DataType, infra_client: InfraClient):
        self.data_type = data_type
        self.infra_client = infra_client

    def fetch_once(self, dst: Path) -> list[str]:
        """One-shot fetch: get new files from infra, copy to dst."""
        dst.mkdir(parents=True, exist_ok=True)
        return self.infra_client.fetch_new(self.data_type.dir_name, dst)

    def register_dir(self, dst: Path) -> None:
        """Periodic polling of FETCH_DIR for new files.

        This blocks forever (intended to run inside a DaemonContext).
        """
        dst.mkdir(parents=True, exist_ok=True)

        # Initial sync
        self.fetch_once(dst)

        try:
            while True:
                time.sleep(POLL_INTERVAL)
                try:
                    self.fetch_once(dst)
                except Exception:
                    logger.exception("fetch_once failed, will retry")
        finally:
            self.fetch_once(dst)

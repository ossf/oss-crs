from enum import Enum
from abc import ABC, abstractmethod
from pathlib import Path

from .infra_client import InfraClient


class DataType(str, Enum):
    POV = "pov"
    SEED = "seed"
    BUG_CANDIDATE = "bug-candidate"
    PATCH = "patch"
    DIFF = "diff"

    def __str__(self) -> str:
        return self.value


class CRSUtils(ABC):
    def __init__(self):
        # TODO: InfraClient currently only copies files to EXCHANGE_DIR.
        # Future: add dedup, rate limiting, or API calls to a real infra service.
        self.infra_client = InfraClient()

    @abstractmethod
    def download_build_output(self, src_path: str, dst_path: Path) -> None:
        """Download build output from src_path (in infra) to dst_path (in local)."""
        pass

    @abstractmethod
    def submit_build_output(self, src_path: str, dst_path: Path) -> None:
        """Submit build output from src_path (in local) to dst_path (in infra)."""
        pass

    @abstractmethod
    def skip_build_output(self, dst_path: str) -> None:
        """Skip build output for dst_path (in infra)."""
        pass

    @abstractmethod
    def register_submit_dir(self, type: DataType, path: Path) -> None:
        """Register a directory for automatic submission to oss-crs-infra."""
        pass

    @abstractmethod
    def register_shared_dir(self, local_path: Path, shared_path: str) -> None:
        """Register a directory for sharing data between containers in a CRS."""
        pass

    @abstractmethod
    def register_fetch_dir(self, type: DataType, path: Path) -> None:
        """Register a directory for automatic fetching of shared data from oss-crs-infra."""
        pass

    @abstractmethod
    def submit(self, type: DataType, src: Path) -> None:
        """Submit a local file to oss-crs-infra."""
        pass

    @abstractmethod
    def fetch(self, type: DataType, dst: Path) -> list[str]:
        """Download shared data from oss-crs-infra to a local directory.

        Returns:
            List of downloaded file names
        """
        pass

    @abstractmethod
    def get_service_domain(self, service_name: str) -> str:
        """Get the service domain for accessing CRS services."""
        pass

    @abstractmethod
    def apply_patch_build(
        self,
        patch_path: Path,
        response_dir: Path,
        builder: str,
    ) -> int:
        """Apply a patch to the snapshot image and rebuild.

        Args:
            patch_path: Path to a unified diff file.
            response_dir: Directory to receive results:
                - build_exit_code, build.log, build_id
            builder: Builder sidecar module name (resolved to URL internally).

        Returns:
            Build exit code (0 = success).
        """
        pass

    @abstractmethod
    def run_pov(
        self,
        pov_path: Path,
        harness_name: str,
        build_id: str,
        response_dir: Path,
        builder: str,
    ) -> int:
        """Run a POV binary against a specific build's output.

        Args:
            pov_path: Path to the POV binary file.
            harness_name: Harness binary name in /out/.
            build_id: Build ID from a prior apply_patch_build call.
            response_dir: Directory to receive results:
                - pov_exit_code, pov_stderr.log
            builder: Builder sidecar module name (resolved to URL internally).

        Returns:
            POV exit code (0 = no crash = patch fixed the bug).
        """
        pass

    @abstractmethod
    def run_test(
        self,
        build_id: str,
        response_dir: Path,
        builder: str,
    ) -> int:
        """Run the project's bundled test.sh against a specific build's output.

        Args:
            build_id: Build ID from a prior apply_patch_build call.
            response_dir: Directory to receive results:
                - test_exit_code, test_stderr.log
            builder: Builder sidecar module name (resolved to URL internally).

        Returns:
            Test exit code (0 = tests pass, 0 with skipped=true if no test.sh).
        """
        pass

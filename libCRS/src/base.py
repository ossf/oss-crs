from enum import Enum
from abc import ABC, abstractmethod
from pathlib import Path

from .infra_client import InfraClient


class DataType(str, Enum):
    POV = "pov"
    SEED = "seed"
    BUG_CANDIDATE = "bug-candidate"
    PATCH = "patch"

    def __str__(self) -> str:
        return self.value


class CRSUtils(ABC):
    def __init__(self):
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
    ) -> int:
        """Apply a patch to the snapshot image and rebuild.

        Args:
            patch_path: Path to a unified diff file.
            response_dir: Directory to receive results:
                - build_exit_code, build.log, build_id

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
    ) -> int:
        """Run a POV binary against a specific build's output.

        Args:
            pov_path: Path to the POV binary file.
            harness_name: Harness binary name in /out/.
            build_id: Build ID from a prior apply_patch_build call.
            response_dir: Directory to receive results:
                - pov_exit_code, pov_stderr.log

        Returns:
            POV exit code (0 = no crash = patch fixed the bug).
        """
        pass

    @abstractmethod
    def run_test(
        self,
        test_script: Path,
        build_id: str,
        response_dir: Path,
    ) -> int:
        """Run a test script against a specific build's output.

        Args:
            test_script: Path to the test script.
            build_id: Build ID from a prior apply_patch_build call.
            response_dir: Directory to receive results:
                - test_exit_code, test_stderr.log

        Returns:
            Test exit code (0 = tests pass).
        """
        pass

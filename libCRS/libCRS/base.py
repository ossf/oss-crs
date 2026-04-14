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

    @property
    def dir_name(self) -> str:
        _DIR_NAMES = {
            "pov": "povs",
            "seed": "seeds",
            "bug-candidate": "bug-candidates",
            "patch": "patches",
            "diff": "diffs",
        }
        return _DIR_NAMES[self.value]


class SourceType(str, Enum):
    FUZZ_PROJ = "fuzz-proj"
    TARGET_SOURCE = "target-source"

    def __str__(self) -> str:
        return self.value


class CRSUtils(ABC):
    def __init__(self):
        # InfraClient handles fetching from FETCH_DIR (read-only).
        # Writes go through SUBMIT_DIR → exchange sidecar → EXCHANGE_DIR.
        self.infra_client = InfraClient()

    @abstractmethod
    def download_build_output(
        self, src_path: str, dst_path: Path, rebuild_id: "int | None" = None
    ) -> None:
        """Download build output from src_path (in infra) to dst_path (in local)."""
        pass

    @abstractmethod
    def download_source(self, source_type: SourceType, dst_path: Path) -> None:
        """Download source tree to local destination path.

        source_type:
            - fuzz-proj: /OSS_CRS_FUZZ_PROJ (fuzz project mount)
            - target-source: /OSS_CRS_TARGET_SOURCE (target source mount)
        """
        pass

    @abstractmethod
    def submit_build_output(
        self, src_path: str, dst_path: Path, rebuild_id: "int | None" = None
    ) -> None:
        """Submit build output.

        Args:
            src_path: Local source path.
            dst_path: Destination path within build output directory.
            rebuild_id: Required during run phase, forbidden during build-target phase.
                        Detected via OSS_CRS_CURRENT_PHASE env var.
        """
        pass

    @abstractmethod
    def skip_build_output(self, dst_path: str) -> None:
        """Skip build output for dst_path (in infra)."""
        pass

    @abstractmethod
    def register_submit_dir(self, data_type: DataType, path: Path) -> None:
        """Register a directory for automatic submission to oss-crs-infra."""
        pass

    @abstractmethod
    def register_shared_dir(self, local_path: Path, shared_path: str) -> None:
        """Register a directory for sharing data between containers in a CRS."""
        pass

    @abstractmethod
    def register_log_dir(self, local_path: Path) -> None:
        """Register a local directory as a log directory.

        Creates a symlink from local_path to a subdirectory under LOG_DIR,
        so that any files written to local_path are persisted on the host
        and available via ``oss-crs artifacts``.
        """
        pass

    @abstractmethod
    def register_fetch_dir(self, data_type: DataType, path: Path) -> None:
        """Register a directory for automatic fetching of shared data from oss-crs-infra."""
        pass

    @abstractmethod
    def submit(self, data_type: DataType, src: Path) -> None:
        """Submit a local file to oss-crs-infra."""
        pass

    @abstractmethod
    def fetch(self, data_type: DataType, dst: Path) -> list[str]:
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
        builder: "str | None" = None,
        builder_name: "str | None" = None,
        rebuild_id: "int | None" = None,
    ) -> int:
        """Apply a patch and rebuild via the builder sidecar.

        Args:
            patch_path: Path to a unified diff file.
            response_dir: Directory to receive results:
                - retcode, stdout.log, stderr.log, rebuild_id
            builder: Builder sidecar service name for DNS. Defaults to BUILDER_MODULE env var.
            builder_name: Builder config name for image resolution (e.g. "coverage-build").
                          Defaults to builder if not specified.
            rebuild_id: Optional rebuild ID. Auto-increments if None, overwrites if provided.

        Returns:
            Build exit code (0 = success).
        """
        pass

    @abstractmethod
    def run_pov(
        self,
        pov_path: Path,
        harness_name: str,
        response_dir: Path,
        rebuild_id: "str | None" = None,
        builder: "str | None" = None,
    ) -> int:
        """Run a POV binary against a specific rebuild's output.

        Args:
            pov_path: Path to the POV binary file.
            harness_name: Harness binary name in /out/.
            rebuild_id: Rebuild ID. None or "base" runs against the original unpatched build.
            response_dir: Directory to receive results:
                - retcode, stdout.log, stderr.log
            builder: Runner sidecar module name. Defaults to RUNNER_MODULE env var or "runner-sidecar".

        Returns:
            POV exit code (0 = no crash = patch fixed the bug).
        """
        pass

    @abstractmethod
    def apply_patch_test(
        self,
        patch_path: Path,
        response_dir: Path,
        builder: "str | None" = None,
    ) -> int:
        """Apply a patch and run the project's bundled test.sh via the builder sidecar.

        Args:
            patch_path: Path to the unified diff file to apply before testing.
            response_dir: Directory to receive results:
                - retcode, stdout.log, stderr.log
            builder: Builder sidecar module name. Defaults to BUILDER_MODULE env var.

        Returns:
            Test exit code (0 = tests pass).
        """
        pass

"""WorkDir: Centralized path management for CRS Compose work directories.

This module provides a WorkDir class that encapsulates all path construction
logic for the CRS Compose work directory structure:

    <work_dir>/
    └── <sanitizer>/
        ├── builds/
        │   └── <build_id>/
        │       ├── crs/<crs_name>/<target_key>/BUILD_OUT_DIR/
        │       └── targets/<target_key>/snapshot-out-<sanitizer>/
        └── runs/
            └── <run_id>/
                ├── BUILD_ID
                ├── EXCHANGE_DIR/<target_key>/<harness>/
                └── crs/<crs_name>/<target_key>/
                    ├── SUBMIT_DIR/<harness>/
                    └── SHARED_DIR/<harness>/
"""

from pathlib import Path

from .target import Target
from .utils import normalize_run_id


class WorkDir:
    """Centralized path management for CRS Compose work directories."""

    def __init__(self, base_path: Path):
        """Initialize WorkDir with a base path.

        Args:
            base_path: The root work directory path.
        """
        self.path = base_path
        self.path.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _get_target_key(target: Target) -> str:
        """Compute target_key from a Target."""
        return target.get_docker_image_name().replace(":", "_")

    # -------------------------------------------------------------------------
    # Base directory helpers
    # -------------------------------------------------------------------------

    def get_builds_dir(self, sanitizer: str) -> Path:
        """Get the builds directory for a sanitizer."""
        return self.path / sanitizer / "builds"

    def get_build_dir(self, build_id: str, sanitizer: str) -> Path:
        """Get directory for a specific build."""
        return self.get_builds_dir(sanitizer) / build_id

    def get_runs_dir(self, sanitizer: str) -> Path:
        """Get the runs directory for a sanitizer."""
        return self.path / sanitizer / "runs"

    def get_run_dir(self, run_id: str, sanitizer: str) -> Path:
        """Get directory for a specific run."""
        return self.get_runs_dir(sanitizer) / run_id

    @staticmethod
    def _resolve_existing_id(raw_id: str, ids_dir: Path) -> str | None:
        """Resolve a user-provided id to an existing directory name.

        Prefer exact match first, then try normalized form for backward
        compatibility.
        """
        if not raw_id:
            return None
        if (ids_dir / raw_id).is_dir():
            return raw_id
        try:
            normalized = normalize_run_id(raw_id)
        except ValueError:
            return None
        if (ids_dir / normalized).is_dir():
            return normalized
        return None

    def resolve_run_id(self, raw_id: str, sanitizer: str) -> str | None:
        """Resolve a user-provided run-id to an existing directory name."""
        return self._resolve_existing_id(raw_id, self.get_runs_dir(sanitizer))

    def resolve_build_id(self, raw_id: str, sanitizer: str) -> str | None:
        """Resolve a user-provided build-id to an existing directory name."""
        return self._resolve_existing_id(raw_id, self.get_builds_dir(sanitizer))

    def get_run_logs_dir(
        self,
        target: Target,
        run_id: str,
        sanitizer: str,
        create: bool = True,
    ) -> Path:
        """Get run-scoped logs directory (outside EXCHANGE_DIR/SUBMIT_DIR mounts).

        Structure: <sanitizer>/runs/<run_id>/logs/<target_key>/<harness>/
        """
        assert target.target_harness, "target_harness must be set for run logs dir"
        target_key = self._get_target_key(target)
        path = (
            self.get_run_dir(run_id, sanitizer)
            / "logs"
            / target_key
            / target.target_harness
        )
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    # -------------------------------------------------------------------------
    # CRS-specific directory helpers
    # -------------------------------------------------------------------------

    def get_crs_build_dir(
        self, crs_name: str, target: Target, build_id: str, sanitizer: str
    ) -> Path:
        """Get the CRS build work directory.

        Structure: <sanitizer>/builds/<build_id>/crs/<crs_name>/<target_key>/
        """
        target_key = self._get_target_key(target)
        return self.get_build_dir(build_id, sanitizer) / "crs" / crs_name / target_key

    def get_build_output_dir(
        self,
        crs_name: str,
        target: Target,
        build_id: str,
        sanitizer: str,
        create: bool = True,
    ) -> Path:
        """Get the BUILD_OUT_DIR for a CRS build.

        Structure: <sanitizer>/builds/<build_id>/crs/<crs_name>/<target_key>/BUILD_OUT_DIR/
        """
        path = (
            self.get_crs_build_dir(crs_name, target, build_id, sanitizer)
            / "BUILD_OUT_DIR"
        )
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    def get_crs_run_dir(
        self, crs_name: str, target: Target, run_id: str, sanitizer: str
    ) -> Path:
        """Get the CRS run work directory.

        Structure: <sanitizer>/runs/<run_id>/crs/<crs_name>/<target_key>/
        """
        target_key = self._get_target_key(target)
        return self.get_run_dir(run_id, sanitizer) / "crs" / crs_name / target_key

    def get_submit_dir(
        self,
        crs_name: str,
        target: Target,
        run_id: str,
        sanitizer: str,
        create: bool = True,
    ) -> Path:
        """Get the SUBMIT_DIR for a CRS run.

        Structure: <sanitizer>/runs/<run_id>/crs/<crs_name>/<target_key>/SUBMIT_DIR/<harness>/
        """
        assert target.target_harness, "target_harness must be set for submit dir"
        path = (
            self.get_crs_run_dir(crs_name, target, run_id, sanitizer)
            / "SUBMIT_DIR"
            / target.target_harness
        )
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    def get_shared_dir(
        self,
        crs_name: str,
        target: Target,
        run_id: str,
        sanitizer: str,
        create: bool = True,
    ) -> Path:
        """Get the SHARED_DIR for a CRS run.

        Structure: <sanitizer>/runs/<run_id>/crs/<crs_name>/<target_key>/SHARED_DIR/<harness>/
        """
        assert target.target_harness, "target_harness must be set for shared dir"
        path = (
            self.get_crs_run_dir(crs_name, target, run_id, sanitizer)
            / "SHARED_DIR"
            / target.target_harness
        )
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    # -------------------------------------------------------------------------
    # Shared/target directory helpers (not CRS-specific)
    # -------------------------------------------------------------------------

    def get_exchange_dir(
        self,
        target: Target,
        run_id: str,
        sanitizer: str,
        create: bool = True,
    ) -> Path:
        """Get the shared EXCHANGE_DIR (shared across all CRSs).

        Structure: <sanitizer>/runs/<run_id>/EXCHANGE_DIR/<target_key>/<harness>/
        """
        assert target.target_harness, "target_harness must be set for exchange dir"
        target_key = self._get_target_key(target)
        path = (
            self.get_run_dir(run_id, sanitizer)
            / "EXCHANGE_DIR"
            / target_key
            / target.target_harness
        )
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    def get_snapshot_dir(
        self,
        target: Target,
        build_id: str,
        sanitizer: str,
        create: bool = True,
    ) -> Path:
        """Get the snapshot output directory for a target.

        Structure: <sanitizer>/builds/<build_id>/targets/<target_key>/snapshot-out-<sanitizer>/
        """
        target_key = self._get_target_key(target)
        path = (
            self.get_build_dir(build_id, sanitizer)
            / "targets"
            / target_key
            / f"snapshot-out-{sanitizer}"
        )
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    def get_build_fetch_dir(
        self,
        target: Target,
        build_id: str,
        sanitizer: str,
        create: bool = True,
    ) -> Path:
        """Get build-phase FETCH_DIR for target-scoped directed inputs.

        Structure: <sanitizer>/builds/<build_id>/FETCH_DIR/<target_key>/
        """
        target_key = self._get_target_key(target)
        path = self.get_build_dir(build_id, sanitizer) / "FETCH_DIR" / target_key
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    def get_build_metadata_file(
        self,
        target: Target,
        build_id: str,
        sanitizer: str,
        create_parent: bool = True,
    ) -> Path:
        """Get per-target build metadata file path.

        Structure: <sanitizer>/builds/<build_id>/targets/<target_key>/build-metadata.json
        """
        target_key = self._get_target_key(target)
        parent = self.get_build_dir(build_id, sanitizer) / "targets" / target_key
        if create_parent:
            parent.mkdir(parents=True, exist_ok=True)
        return parent / "build-metadata.json"

    # -------------------------------------------------------------------------
    # Metadata helpers
    # -------------------------------------------------------------------------

    def get_build_id_file(self, run_id: str, sanitizer: str) -> Path:
        """Get the BUILD_ID file path for a run."""
        return self.get_run_dir(run_id, sanitizer) / "BUILD_ID"

    def read_build_id_for_run(self, run_id: str, sanitizer: str) -> str | None:
        """Read the build-id that was used for a specific run."""
        build_id_file = self.get_build_id_file(run_id, sanitizer)
        if build_id_file.exists():
            return build_id_file.read_text().strip()
        return None

    def write_build_id_for_run(
        self, run_id: str, sanitizer: str, build_id: str
    ) -> None:
        """Write the build-id for a run."""
        run_dir = self.get_run_dir(run_id, sanitizer)
        run_dir.mkdir(parents=True, exist_ok=True)
        self.get_build_id_file(run_id, sanitizer).write_text(build_id)

# SPDX-License-Identifier: MIT
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
                    ├── SHARED_DIR/<harness>/
                    └── LOG_DIR/<harness>/
"""

import json
from dataclasses import dataclass
from pathlib import Path

from .target import Target
from .utils import normalize_run_id
from .constants import SUBMITTED_ARTIFACT_DIR_NAMES, UNHARNESSED


@dataclass
class BuildEntry:
    """A discovered build within the workdir."""

    build_id: str
    sanitizer: str
    path: Path


@dataclass
class RunEntry:
    """A discovered run within the workdir."""

    run_id: str
    sanitizer: str
    path: Path
    build_id: str | None = None


class WorkDir:
    """Centralized path management for CRS Compose work directories."""

    NO_HARNESS_SCOPE = UNHARNESSED

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
        return target.get_workdir_target_key()

    @staticmethod
    def count_data_files(dir_path: Path, recursive: bool = False) -> int:
        """Count non-hidden files in a directory.

        Hidden files are skipped everywhere: they are sidecar bookkeeping
        (``.<name>.skip`` markers, partial-write temp files), never artifacts.

        Exchange directories are flat for hash-named submissions, but
        host-provided inputs (``--bug-candidate-dir``, ``--report-dir``,
        ``--patch-dir``) preserve their nesting, so callers that must see those
        pass ``recursive=True``.
        """
        if not dir_path.exists() or not dir_path.is_dir():
            return 0
        entries = dir_path.rglob("*") if recursive else dir_path.iterdir()
        return sum(1 for f in entries if f.is_file() and not f.name.startswith("."))

    @classmethod
    def _get_harness_scope(cls, target: Target) -> str:
        """Return the run artifact scope for harnessed or source-level runs."""
        return target.target_harness or cls.NO_HARNESS_SCOPE

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

    # -------------------------------------------------------------------------
    # Enumeration helpers
    # -------------------------------------------------------------------------

    def iter_sanitizers(self) -> list[str]:
        """List sanitizer directories present in the workdir."""
        if not self.path.exists():
            return []
        return sorted(d.name for d in self.path.iterdir() if d.is_dir())

    def iter_sibling_compose_workdirs(self) -> list[tuple[str, "WorkDir"]]:
        """Enumerate every compose-hash workdir under the shared root.

        A workdir is rooted at ``<work_dir>/crs_compose/<compose_hash>``, so
        sibling directories hold the runs of other CRS ensembles against the
        same ``--work-dir``. Returns ``(compose_hash, WorkDir)`` pairs sorted by
        hash, including this workdir itself.
        """
        root = self.path.parent
        if not root.is_dir():
            return []
        return [(d.name, WorkDir(d)) for d in sorted(root.iterdir()) if d.is_dir()]

    def iter_builds(self, sanitizer: str | None = None) -> list[BuildEntry]:
        """Enumerate all builds, optionally filtered by sanitizer."""
        sanitizers = [sanitizer] if sanitizer else self.iter_sanitizers()
        entries: list[BuildEntry] = []
        for san in sanitizers:
            builds_dir = self.get_builds_dir(san)
            if not builds_dir.exists():
                continue
            for d in builds_dir.iterdir():
                if d.is_dir():
                    entries.append(
                        BuildEntry(
                            build_id=d.name,
                            sanitizer=san,
                            path=d,
                        )
                    )
        return entries

    def iter_runs(self, sanitizer: str | None = None) -> list[RunEntry]:
        """Enumerate all runs, optionally filtered by sanitizer."""
        sanitizers = [sanitizer] if sanitizer else self.iter_sanitizers()
        entries: list[RunEntry] = []
        for san in sanitizers:
            runs_dir = self.get_runs_dir(san)
            if not runs_dir.exists():
                continue
            for d in runs_dir.iterdir():
                if d.is_dir():
                    build_id_file = d / "BUILD_ID"
                    build_id = (
                        build_id_file.read_text().strip()
                        if build_id_file.exists()
                        else None
                    )
                    entries.append(
                        RunEntry(
                            run_id=d.name,
                            sanitizer=san,
                            path=d,
                            build_id=build_id,
                        )
                    )
        return entries

    # -------------------------------------------------------------------------
    # ID resolution helpers
    # -------------------------------------------------------------------------

    @staticmethod
    def candidate_ids(raw_id: str) -> list[str]:
        """Directory names a user-provided run/build id may refer to.

        Exact match first, then the normalized form for backward compatibility.
        """
        if not raw_id:
            return []
        candidates = [raw_id]
        try:
            normalized = normalize_run_id(raw_id)
        except ValueError:
            return candidates
        if normalized != raw_id:
            candidates.append(normalized)
        return candidates

    @classmethod
    def _resolve_existing_id(cls, raw_id: str, ids_dir: Path) -> str | None:
        """Resolve a user-provided id to an existing directory name."""
        for candidate in cls.candidate_ids(raw_id):
            if (ids_dir / candidate).is_dir():
                return candidate
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

        Structure: <sanitizer>/runs/<run_id>/logs/<target_key>/<harness|_no_harness>/
        """
        target_key = self._get_target_key(target)
        path = (
            self.get_run_dir(run_id, sanitizer)
            / "logs"
            / target_key
            / self._get_harness_scope(target)
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

    def get_rebuild_out_dir(
        self,
        crs_name: str,
        target: Target,
        run_id: str,
        sanitizer: str,
        create: bool = True,
    ) -> Path:
        """Get the REBUILD_OUT_DIR for versioned build artifacts from a CRS run.

        Structure: <sanitizer>/runs/<run_id>/crs/<crs_name>/<target_key>/REBUILD_OUT_DIR/
        """
        path = (
            self.get_crs_run_dir(crs_name, target, run_id, sanitizer)
            / "REBUILD_OUT_DIR"
        )
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    def get_submit_dir(
        self,
        crs_name: str,
        target: Target,
        run_id: str,
        sanitizer: str,
        create: bool = True,
    ) -> Path:
        """Get the SUBMIT_DIR for a CRS run.

        Structure: <sanitizer>/runs/<run_id>/crs/<crs_name>/<target_key>/SUBMIT_DIR/<harness|_no_harness>/
        """
        path = (
            self.get_crs_run_dir(crs_name, target, run_id, sanitizer)
            / "SUBMIT_DIR"
            / self._get_harness_scope(target)
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

        Structure: <sanitizer>/runs/<run_id>/crs/<crs_name>/<target_key>/SHARED_DIR/<harness|_no_harness>/
        """
        path = (
            self.get_crs_run_dir(crs_name, target, run_id, sanitizer)
            / "SHARED_DIR"
            / self._get_harness_scope(target)
        )
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    def get_log_dir(
        self,
        crs_name: str,
        target: Target,
        run_id: str,
        sanitizer: str,
        create: bool = True,
    ) -> Path:
        """Get the LOG_DIR for a CRS run (agent/internal logs).

        Structure: <sanitizer>/runs/<run_id>/crs/<crs_name>/<target_key>/LOG_DIR/<harness|_no_harness>/
        """
        path = (
            self.get_crs_run_dir(crs_name, target, run_id, sanitizer)
            / "LOG_DIR"
            / self._get_harness_scope(target)
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

        Structure: <sanitizer>/runs/<run_id>/EXCHANGE_DIR/<target_key>/<harness|_no_harness>/
        """
        target_key = self._get_target_key(target)
        path = (
            self.get_run_dir(run_id, sanitizer)
            / "EXCHANGE_DIR"
            / target_key
            / self._get_harness_scope(target)
        )
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    def get_processed_exchange_dir(
        self,
        target: Target,
        run_id: str,
        sanitizer: str,
        create: bool = True,
    ) -> Path:
        """Get the PROCESSED_EXCHANGE_DIR (post-processor outputs for non-processor CRSs).

        Collects outputs from triage and seed-filter CRSs into a single dir
        that non-processor CRSs mount as their FETCH_DIR.

        Structure: <sanitizer>/runs/<run_id>/PROCESSED_EXCHANGE_DIR/<target_key>/<harness>/
        """
        target_key = self._get_target_key(target)
        path = (
            self.get_run_dir(run_id, sanitizer)
            / "PROCESSED_EXCHANGE_DIR"
            / target_key
            / self._get_harness_scope(target)
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

    def get_target_source_dir(
        self,
        target: "Target",
        build_id: str,
        sanitizer: str,
        create: bool = True,
    ) -> Path:
        """Get the extracted target source directory for a build.

        Structure: <sanitizer>/builds/<build_id>/targets/<target_key>/target-source/
        """
        target_key = self._get_target_key(target)
        path = (
            self.get_build_dir(build_id, sanitizer)
            / "targets"
            / target_key
            / "target-source"
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

    def get_run_meta_file(self, run_id: str, sanitizer: str) -> Path:
        """Get the run metadata file path for a run."""
        return self.get_run_dir(run_id, sanitizer) / "meta.json"

    def get_litellm_spend_report_file(
        self, run_id: str, sanitizer: str, create_parent: bool = True
    ) -> Path:
        """Get run-level LiteLLM spend report file path."""
        run_dir = self.get_run_dir(run_id, sanitizer)
        if create_parent:
            run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / "litellm-spend-report.json"
        if create_parent:
            path.touch(exist_ok=True)
        return path

    def get_sidecar_metrics_file(
        self,
        crs_name: str,
        target: Target,
        run_id: str,
        sanitizer: str,
    ) -> Path:
        """Get per-CRS sidecar metrics file path under LOG_DIR."""
        return (
            self.get_log_dir(crs_name, target, run_id, sanitizer, create=False)
            / "libcrs-sidecar-metrics.jsonl"
        )

    def get_submit_artifact_counts(
        self,
        crs_name: str,
        target: Target,
        run_id: str,
        sanitizer: str,
    ) -> dict[str, int]:
        """Count run artifacts for one CRS from SUBMIT_DIR."""
        submit_dir = self.get_submit_dir(
            crs_name, target, run_id, sanitizer, create=False
        )
        return {
            name.replace("-", "_"): self.count_data_files(submit_dir / name)
            for name in SUBMITTED_ARTIFACT_DIR_NAMES
        }

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

    def write_run_meta_for_run(
        self, run_id: str, sanitizer: str, metadata: dict
    ) -> None:
        """Write run metadata to meta.json for a specific run."""
        run_dir = self.get_run_dir(run_id, sanitizer)
        run_dir.mkdir(parents=True, exist_ok=True)
        self.get_run_meta_file(run_id, sanitizer).write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n"
        )

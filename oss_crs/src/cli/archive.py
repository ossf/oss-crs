# SPDX-License-Identifier: MIT
"""Archive command: package submitted artifacts from a run into a tarball."""

import sys
import tarfile
from pathlib import Path

from .artifacts import resolve_run_context
from ..target import Target


def handle_archive(args, crs_compose, target: Target) -> bool:
    """Handle the archive command."""
    ctx = resolve_run_context(args, crs_compose, target)
    if ctx is None:
        return False
    sanitizer, run_id = ctx
    harness = target.target_harness
    work_dir = crs_compose.work_dir

    out_path = Path(args.out)

    # Identify triage CRS(s)
    triage_crs = [crs for crs in crs_compose.crs_list if crs.config.is_triage]
    non_triage_crs = [crs for crs in crs_compose.crs_list if not crs.config.is_triage]

    # Collect (arcname, src_path) pairs for each artifact subdir
    artifact_subdirs = ["povs", "seeds", "patches", "bug-candidates"]

    def _add_dir(collected: list, src_dir: Path, arcname_prefix: str) -> None:
        """Recursively add files from src_dir under arcname_prefix."""
        if not src_dir.exists():
            return

        resolved_src_dir = src_dir.resolve()
        for f in src_dir.rglob("*"):
            if f.is_symlink() or not f.is_file():
                continue
            try:
                f.resolve().relative_to(resolved_src_dir)
            except ValueError:
                continue
            rel = f.relative_to(src_dir)
            collected.append((f, f"{arcname_prefix}/{rel}"))

    collected: list[tuple[Path, str]] = []

    if triage_crs:
        # POVs come from the triage CRS submit dir (verified/dedup'd)
        for crs in triage_crs:
            submit_dir = work_dir.get_submit_dir(
                crs.name, target, run_id, sanitizer, create=False
            )
            _add_dir(collected, submit_dir / "povs", "povs")

        # Seeds, patches, bug-candidates from non-triage CRSs
        for crs in non_triage_crs:
            submit_dir = work_dir.get_submit_dir(
                crs.name, target, run_id, sanitizer, create=False
            )
            for subdir in ["seeds", "patches", "bug-candidates"]:
                _add_dir(collected, submit_dir / subdir, subdir)
    else:
        # No triage: collect all submitted artifact types from every CRS
        for crs in crs_compose.crs_list:
            submit_dir = work_dir.get_submit_dir(
                crs.name, target, run_id, sanitizer, create=False
            )
            for subdir in artifact_subdirs:
                _add_dir(collected, submit_dir / subdir, subdir)

    if args.include_all:
        # Also include exchange dir, logs, and shared dirs
        if harness:
            exchange_dir = work_dir.get_exchange_dir(
                target, run_id, sanitizer, create=False
            )
            _add_dir(collected, exchange_dir, "exchange")

            run_logs_dir = work_dir.get_run_logs_dir(
                target, run_id, sanitizer, create=False
            )
            _add_dir(collected, run_logs_dir, "logs")

            for crs in crs_compose.crs_list:
                shared_dir = work_dir.get_shared_dir(
                    crs.name, target, run_id, sanitizer, create=False
                )
                _add_dir(collected, shared_dir, f"shared/{crs.name}")

                log_dir = work_dir.get_log_dir(
                    crs.name, target, run_id, sanitizer, create=False
                )
                _add_dir(collected, log_dir, f"logs/crs/{crs.name}")

    if not collected:
        print("No artifacts found for the selected run.", file=sys.stderr)
        return False

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(out_path, "w:gz") as tar:
        seen_arcnames: set[str] = set()
        for src, arcname in collected:
            # Deduplicate: if multiple CRSs produced the same filename, suffix with index
            base_arcname = arcname
            idx = 1
            while arcname in seen_arcnames:
                stem = Path(base_arcname)
                arcname = str(stem.parent / f"{stem.stem}.{idx}{stem.suffix}")
                idx += 1
            seen_arcnames.add(arcname)
            tar.add(src, arcname=arcname)

    total = len(seen_arcnames)
    print(f"Archived {total} file(s) to {out_path}")
    return True

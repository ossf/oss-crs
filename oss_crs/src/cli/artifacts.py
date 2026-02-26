"""Artifacts command handling for oss-crs CLI."""

import re
import sys
import datetime

from ..config.artifacts import ArtifactsOutput, CRSArtifacts, ExchangeDir, RunLogs
from ..target import Target
from ..utils import normalize_run_id, select


def collect_run_ids_for_target(
    crs_compose, target: Target, harness: str | None, sanitizer: str
) -> list[str]:
    """Collect all run-ids for a target from SUBMIT_DIR (run artifacts)."""
    seen = set()

    runs_dir = crs_compose.work_dir.get_runs_dir(sanitizer)
    if not runs_dir.exists():
        return []

    # Temporarily set harness for path resolution
    original_harness = target.target_harness
    if harness:
        target.target_harness = harness

    for run_id_dir in runs_dir.iterdir():
        if not run_id_dir.is_dir():
            continue
        run_id = run_id_dir.name
        for crs in crs_compose.crs_list:
            submit_path = crs_compose.work_dir.get_submit_dir(
                crs.name, target, run_id, sanitizer, create=False
            )
            # For harness=None, check parent dir (without harness component)
            if not harness:
                submit_path = submit_path.parent
            if submit_path.exists():
                seen.add(run_id)
                break

    target.target_harness = original_harness

    # Sort by timestamp (extract 10-digit sequences), newest first
    def extract_ts(run_id: str) -> int:
        match = re.search(r"\d{10}", run_id)
        return int(match.group()) if match else 0

    return sorted(seen, key=extract_ts, reverse=True)


def format_run_id(run_id: str) -> str:
    """Format a run-id for display. Extract unix timestamp and show human-readable time."""
    match = re.search(r"\d{10}", run_id)
    if match:
        ts = int(match.group())
        dt = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        return f"{dt}  ({run_id})"
    return run_id


def select_run_id_interactively(
    crs_compose, target: Target, harness: str | None, sanitizer: str
) -> str | None:
    """Prompt user to select a run-id from available runs."""
    all_run_ids = collect_run_ids_for_target(crs_compose, target, harness, sanitizer)
    if not all_run_ids:
        print("No runs found for this target.", file=sys.stderr)
        return None

    choices = [(format_run_id(r), r) for r in all_run_ids]
    return select("Select run-id:", choices)


def handle_artifacts(args, crs_compose, target: Target) -> bool:
    """Handle the artifacts command."""
    harness = target.target_harness
    sanitizer = args.sanitizer
    work_dir = crs_compose.work_dir

    # run_id for SUBMIT/FETCH/SHARED dirs.
    # If --run-id is provided and not found on disk yet, still accept it and
    # normalize to deterministic run-scoped paths (pre-run resolution).
    if args.run_id:
        resolved_run_id = work_dir.resolve_run_id(args.run_id, sanitizer)
        if resolved_run_id is not None:
            run_id = resolved_run_id
        else:
            try:
                run_id = normalize_run_id(args.run_id)
            except ValueError:
                print(f"Invalid run id '{args.run_id}'.", file=sys.stderr)
                return False
    else:
        run_id = select_run_id_interactively(crs_compose, target, harness, sanitizer)
        if run_id is None:
            return False

    # build_id for BUILD_OUT_DIR - use provided, read from run, or find latest
    if args.build_id:
        resolved_build_id = work_dir.resolve_build_id(args.build_id, sanitizer)
        if resolved_build_id is None:
            print(f"Build '{args.build_id}' not found.", file=sys.stderr)
            return False
        build_id = resolved_build_id
    else:
        # Try to get build_id from the run directory first
        build_id = work_dir.read_build_id_for_run(run_id, sanitizer)
        if build_id is None:
            # Fall back to latest build
            build_id = crs_compose.get_latest_build_id(target, sanitizer)

    # Build structured result
    output = ArtifactsOutput(build_id=build_id, run_id=run_id, sanitizer=sanitizer)

    if harness:
        output.exchange_dir = ExchangeDir.from_work_dir(
            work_dir, target, run_id, sanitizer
        )
        output.run_logs = RunLogs.from_work_dir(work_dir, target, run_id, sanitizer)

    exchange_base = output.exchange_dir.base if output.exchange_dir else None
    for crs in crs_compose.crs_list:
        output.crs[crs.name] = CRSArtifacts.from_work_dir(
            work_dir, crs.name, target, build_id, run_id, sanitizer, exchange_base
        )

    print(output.to_json())
    return True

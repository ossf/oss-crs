import sys
import time
import signal
import argparse
import datetime
from pathlib import Path
from dotenv import load_dotenv
import questionary
from ..crs_compose import CRSCompose
from ..target import Target
from ..utils import normalize_run_id
from ..config.artifacts import ArtifactsOutput, CRSArtifacts, ExchangeDir


DEFAULT_WORK_DIR = (Path(__file__) / "../../../../.oss-crs-workdir").resolve()


def add_common_arguments(parser):
    parser.add_argument(
        "--compose-file",
        type=Path,
        required=True,
        help="Path to the CRS Compose file",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=DEFAULT_WORK_DIR,
        help="Working directory for CRS Compose operations",
    )


def add_target_arguments(parser):
    parser.add_argument(
        "--target-proj-path",
        type=Path,
        required=True,
        help="""
        Target Project Path where includes oss-fuzz compatible files (e.g., Dockerfile, project.yaml, ...)
        # TODO: this accepts only local paths for now. But, we will support remote paths later.
        """,
    )
    parser.add_argument(
        "--target-repo-path",
        type=Path,
        required=False,
        help="Local path to the target repository to build with the target project configuration.",
    )


def add_prepare_command(subparsers):
    prepare = subparsers.add_parser(
        "prepare", help="Prepare CRSs defined in CRS Compose file"
    )
    add_common_arguments(prepare)
    prepare.add_argument(
        "--publish",
        type=bool,
        default=False,
        help="Publish prepared CRS docker images to the specified docker resgistry",
    )


def add_build_target_command(subparsers):
    build_target = subparsers.add_parser(
        "build-target", help="Build target repository defined in CRS Compose file"
    )
    add_common_arguments(build_target)
    add_target_arguments(build_target)
    build_target.add_argument(
        "--build-id",
        type=str,
        default=None,
        help="Build identifier used to isolate parallel builds (default: generates timestamp-based ID).",
    )
    build_target.add_argument(
        "--sanitizer",
        type=str,
        default=None,
        help="Sanitizer to use for build (default: from target config, usually 'address').",
    )


def add_run_command(subparsers):
    run = subparsers.add_parser(
        "run", help="Run CRSs against a target using CRS Compose file"
    )
    add_common_arguments(run)
    add_target_arguments(run)
    run.add_argument(
        "--target-harness",
        type=str,
        required=True,
        help="Specify the target harness to use for the run",
    )
    run.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="Maximum run duration in seconds. Gracefully stops all containers when exceeded.",
    )
    run.add_argument(
        "--build-id",
        type=str,
        default=None,
        help="Build identifier to use (default: uses latest build, or generates new if none exists).",
    )
    run.add_argument(
        "--sanitizer",
        type=str,
        default=None,
        help="Sanitizer to use (default: from target config, usually 'address').",
    )
    run.add_argument(
        "--run-id",
        type=str,
        default=None,
        help="Run identifier for this run's artifacts. If not provided, generates timestamp-based id.",
    )
    run.add_argument(
        "--pov",
        type=Path,
        default=None,
        help="Single POV file to pre-populate into FETCH_DIR before containers start",
    )
    run.add_argument(
        "--pov-dir",
        type=Path,
        default=None,
        help="Directory containing POV files to pre-populate into FETCH_DIR before containers start",
    )
    run.add_argument(
        "--diff",
        type=Path,
        default=None,
        help="Diff file for delta-mode analysis, pre-populated into FETCH_DIR before containers start. Accessible via: libCRS fetch diff <local_path>",
    )
    run.add_argument(
        "--seed-dir",
        type=Path,
        default=None,
        help="Directory of initial seed files, pre-populated into FETCH_DIR before containers start. Accessible via: libCRS fetch seed <local_path>",
    )

def add_artifacts_command(subparsers):
    artifacts = subparsers.add_parser(
        "artifacts", help="Show directories for run artifacts (JSON output)"
    )
    add_common_arguments(artifacts)
    add_target_arguments(artifacts)
    artifacts.add_argument(
        "--target-harness",
        type=str,
        required=False,
        default=None,
        help="Specify the target harness (required for submit/fetch/shared dirs)",
    )
    artifacts.add_argument(
        "--build-id",
        type=str,
        default=None,
        help="Build identifier (default: uses latest build).",
    )
    artifacts.add_argument(
        "--sanitizer",
        type=str,
        default="address",
        help="Sanitizer used for artifact paths (default: 'address').",
    )
    artifacts.add_argument(
        "--run-id",
        type=str,
        required=False,
        default=None,
        help="Run identifier to look up artifacts for (interactive selection if omitted)",
    )


def add_check_command(subparsers):
    pass


def init_target_from_args(args) -> Target:
    target_harness = args.target_harness if hasattr(args, "target_harness") else None
    return Target(
        args.work_dir,
        args.target_proj_path,
        args.target_repo_path,
        target_harness,
    )


def _collect_run_ids_for_target(crs_compose, target, harness: str | None, sanitizer: str) -> list[str]:
    """Collect all run-ids for a target from SUBMIT_DIR (run artifacts).

    Structure: <sanitizer>/runs/<run-id>/crs/<crs-name>/<target>/SUBMIT_DIR/<harness>/
    """
    import re
    target_base_image = target.get_docker_image_name()
    target_key = target_base_image.replace(":", "_")
    seen = set()

    runs_dir = crs_compose.work_dir / sanitizer / "runs"
    if not runs_dir.exists():
        return []

    for run_id_dir in runs_dir.iterdir():
        if not run_id_dir.is_dir():
            continue
        run_id = run_id_dir.name
        # Check if any CRS has submit output for this target
        for crs in crs_compose.crs_list:
            if harness:
                submit_path = run_id_dir / "crs" / crs.name / target_key / "SUBMIT_DIR" / harness
            else:
                submit_path = run_id_dir / "crs" / crs.name / target_key / "SUBMIT_DIR"
            if submit_path.exists():
                seen.add(run_id)
                break  # Found at least one CRS with this run, no need to check others

    # Sort by timestamp (extract 10-digit sequences), newest first
    def extract_ts(run_id: str) -> int:
        match = re.search(r'\d{10}', run_id)
        return int(match.group()) if match else 0

    return sorted(seen, key=extract_ts, reverse=True)


def _format_run_id(run_id: str) -> str:
    """Format a run-id for display. Extract unix timestamp and show human-readable time."""
    import re
    match = re.search(r'\d{10}', run_id)
    if match:
        ts = int(match.group())
        dt = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
        return f"{dt}  ({run_id})"
    return run_id


def _select_run_id_interactively(crs_compose, target, harness: str | None, sanitizer: str) -> str | None:
    """Prompt user to select a run-id from available runs."""
    all_run_ids = _collect_run_ids_for_target(crs_compose, target, harness, sanitizer)
    if not all_run_ids:
        print("No runs found for this target.", file=sys.stderr)
        return None

    choices = [questionary.Choice(title=_format_run_id(r), value=r) for r in all_run_ids]
    selected = questionary.select("Select run-id:", choices=choices).ask()
    return selected


def _handle_artifacts(args, crs_compose) -> bool:
    target = init_target_from_args(args)
    harness = target.target_harness
    sanitizer = args.sanitizer

    # run_id for SUBMIT/FETCH/SHARED dirs - interactive selection if not provided
    if args.run_id:
        run_id = normalize_run_id(args.run_id)
    else:
        run_id = _select_run_id_interactively(crs_compose, target, harness, sanitizer)
        if run_id is None:
            return False

    # build_id for BUILD_OUT_DIR - use provided, read from run, or find latest
    if args.build_id:
        build_id = normalize_run_id(args.build_id)
        # Explicit --build-id must exist on disk
        builds_dir = crs_compose.work_dir / sanitizer / "builds" / build_id
        if not builds_dir.exists():
            print(f"Build '{args.build_id}' not found.", file=sys.stderr)
            return False
    else:
        # Try to get build_id from the run directory first
        build_id = crs_compose.get_build_id_for_run(run_id, sanitizer)
        if build_id is None:
            # Fall back to latest build
            build_id = crs_compose.get_latest_build_id(target, sanitizer)

    # Build structured result — always return computed paths
    output = ArtifactsOutput(build_id=build_id, run_id=run_id, sanitizer=sanitizer)

    # exchange_dir is shared across all CRSs — compute once from the first CRS
    if harness and crs_compose.crs_list:
        exchange_path = crs_compose.crs_list[0].get_exchange_dir(
            target, run_id=run_id, sanitizer=sanitizer, create=False
        )
        output.exchange_dir = ExchangeDir(
            base=str(exchange_path),
            pov=str(exchange_path / "povs"),
            seed=str(exchange_path / "seeds"),
            bug_candidate=str(exchange_path / "bug-candidates"),
            patch=str(exchange_path / "patches"),
            diff=str(exchange_path / "diffs"),
        )

    for crs in crs_compose.crs_list:
        crs_artifacts = CRSArtifacts()

        # Build output uses build_id
        if build_id:
            build_path = crs.get_build_output_dir(target, build_id=build_id, sanitizer=sanitizer, create=False)
            crs_artifacts.build = str(build_path)

        # Per-harness artifacts use run_id
        if harness:
            submit_path = crs.get_submit_dir(target, run_id=run_id, sanitizer=sanitizer, create=False)
            crs_artifacts.submit_dir = str(submit_path)
            crs_artifacts.pov = str(submit_path / "povs")
            crs_artifacts.seed = str(submit_path / "seeds")
            crs_artifacts.bug_candidate = str(submit_path / "bug-candidates")
            crs_artifacts.patch = str(submit_path / "patches")

            crs_artifacts.fetch = output.exchange_dir.base if output.exchange_dir else None

            shared_path = crs.get_shared_dir(target, run_id=run_id, sanitizer=sanitizer, create=False)
            crs_artifacts.shared = str(shared_path)

        output.crs[crs.name] = crs_artifacts

    print(output.to_json())
    return True


def _sigterm_handler(signum, frame):
    """Convert SIGTERM into KeyboardInterrupt so cleanup tasks can run."""
    raise KeyboardInterrupt("SIGTERM received")


def cli() -> bool:
    signal.signal(signal.SIGTERM, _sigterm_handler)
    load_dotenv()
    parser = argparse.ArgumentParser(
        prog="oss-crs",
        description="OSS-CRS: Cyber Reasoning System orchestration CLI"
    )
    subparsers = parser.add_subparsers(
        dest="command", required=True, help="Command to run"
    )
    add_prepare_command(subparsers)
    add_build_target_command(subparsers)
    add_run_command(subparsers)
    add_artifacts_command(subparsers)
    add_check_command(subparsers)

    args = parser.parse_args()

    # Resolve all Path arguments to absolute paths so that relative paths
    # (e.g., --target-proj-path ../ghostscript) work regardless of cwd.
    for key, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, key, value.expanduser().resolve())

    # Skip CRS repo init for commands that don't need it
    skip_crs_init = args.command == "artifacts"
    crs_compose = CRSCompose.from_yaml_file(args.compose_file, args.work_dir, skip_crs_init=skip_crs_init)

    if args.command == "prepare":
        if not crs_compose.prepare(publish=args.publish):
            return False
    elif args.command == "build-target":
        target = init_target_from_args(args)
        # Library normalizes build_id internally
        if not crs_compose.build_target(target, build_id=args.build_id, sanitizer=args.sanitizer):
            return False
    elif args.command == "run":
        target = init_target_from_args(args)
        if args.timeout is not None:
            crs_compose.set_deadline(time.monotonic() + args.timeout)
        if not crs_compose.run(target, run_id=args.run_id, build_id=args.build_id, sanitizer=args.sanitizer, pov=args.pov, pov_dir=args.pov_dir, diff=args.diff, seed_dir=args.seed_dir):
            return False
    elif args.command == "artifacts":
        return _handle_artifacts(args, crs_compose)
    elif args.command == "check":
        pass
    return True


def main() -> int:
    return 0 if cli() else 1


if __name__ == "__main__":
    sys.exit(main())

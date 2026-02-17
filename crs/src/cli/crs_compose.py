import sys
import time
import signal
import argparse
from pathlib import Path
from dotenv import load_dotenv
from ..crs_compose import CRSCompose
from ..target import Target
from ..utils import generate_run_id, normalize_run_id


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
    parser.add_argument(
        "--no-checkout",
        action="store_true",
        default=False,
        help="Whether to checkout the target repository before building.",
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
        "--run-id",
        type=str,
        default=None,
        help="Run identifier used to isolate parallel builds (default: random).",
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
        "--run-id",
        type=str,
        default=None,
        help="Run identifier used to isolate parallel runs (default: random).",
    )


def add_check_command(subparsers):
    pass


def init_target_from_args(args) -> Target:
    target_harness = args.target_harness if hasattr(args, "target_harness") else None
    return Target(
        args.work_dir,
        args.target_proj_path,
        args.target_repo_path,
        args.no_checkout,
        target_harness,
    )


def _sigterm_handler(signum, frame):
    """Convert SIGTERM into KeyboardInterrupt so cleanup tasks can run."""
    raise KeyboardInterrupt("SIGTERM received")


def cli() -> bool:
    signal.signal(signal.SIGTERM, _sigterm_handler)
    load_dotenv()
    parser = argparse.ArgumentParser(
        description="CRS (Cyber Reasoning System) Compose CLI"
    )
    subparsers = parser.add_subparsers(
        dest="command", required=True, help="Command to run"
    )
    add_prepare_command(subparsers)
    add_build_target_command(subparsers)
    add_run_command(subparsers)
    add_check_command(subparsers)

    args = parser.parse_args()

    # Resolve all Path arguments to absolute paths so that relative paths
    # (e.g., --target-proj-path ../ghostscript) work regardless of cwd.
    for key, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, key, value.expanduser().resolve())

    resolved_run_id = None
    if hasattr(args, "run_id"):
        resolved_run_id = (
            normalize_run_id(args.run_id) if args.run_id else generate_run_id()
        )

    crs_compose = CRSCompose.from_yaml_file(args.compose_file, args.work_dir)

    if args.command == "prepare":
        if not crs_compose.prepare(publish=args.publish):
            return False
    elif args.command == "build-target":
        assert resolved_run_id is not None
        target = init_target_from_args(args)
        if not crs_compose.build_target(target, run_id=resolved_run_id):
            return False
    elif args.command == "run":
        assert resolved_run_id is not None
        target = init_target_from_args(args)
        if args.timeout is not None:
            crs_compose.set_deadline(time.monotonic() + args.timeout)
        if not crs_compose.run(target, run_id=resolved_run_id):
            return False
    elif args.command == "check":
        pass
    return True


def main() -> int:
    return 0 if cli() else 1


if __name__ == "__main__":
    sys.exit(main())

import sys
import time
import signal
import argparse
from pathlib import Path
from dotenv import load_dotenv
from ..crs_compose import CRSCompose
from ..config.crs_compose import CRSComposeConfig
from ..target import Target
from .artifacts import handle_artifacts
from ..utils import normalize_run_id
from ..config.artifacts import ArtifactsOutput, CRSArtifacts, ExchangeDir
from .setup import add_setup_command, handle_setup


DEFAULT_WORK_DIR = (Path(__file__) / "../../../../.oss-crs-workdir").resolve()
DEPRECATED_FLAGS = {
    "--target-proj-path": "--fuzz-proj-path",
    "--target-path": "--fuzz-proj-path",
}


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
        "--fuzz-proj-path",
        "--target-path",
        "--target-proj-path",
        dest="target_proj_path",
        type=Path,
        required=True,
        help=(
            "Path to target project directory "
            "(contains Dockerfile/build.sh; project.yaml optional). "
            "--target-path and --target-proj-path are kept as compatibility aliases."
        ),
    )
    parser.add_argument(
        "--target-source-path",
        dest="target_repo_path",
        type=Path,
        required=False,
        help=(
            "Optional local source override path. "
            "When set, oss-crs overlays this source into the effective target "
            "source path resolved from Dockerfile WORKDIR."
        ),
    )
    parser.add_argument(
        "--bug-candidate",
        type=Path,
        required=False,
        default=None,
        help="Path to a bug-candidate report file.",
    )
    parser.add_argument(
        "--bug-candidate-dir",
        type=Path,
        required=False,
        default=None,
        help="Path to a directory containing bug-candidate report files.",
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
        "--diff",
        type=Path,
        default=None,
        help="Diff file for directed build analysis, mounted into build-target containers.",
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
        default=None,
        help="Sanitizer used for artifact paths (default: resolved from compose/project.yaml, else 'address').",
    )
    artifacts.add_argument(
        "--run-id",
        type=str,
        required=False,
        default=None,
        help=(
            "Run identifier to resolve artifacts for. If omitted, interactive "
            "selection is used. If provided but not found yet, paths are still "
            "computed deterministically for pre-run resolution."
        ),
    )


def add_check_command(subparsers):
    pass


def add_gen_compose_command(subparsers):
    gen_compose = subparsers.add_parser(
        "gen-compose", help="Generate a compose file with mapped CPU sets"
    )
    gen_compose.add_argument(
        "--compose-template",
        type=Path,
        required=True,
        help="Path to the template CRS Compose file",
    )
    gen_compose.add_argument(
        "--cpus",
        type=str,
        required=True,
        help="Real CPU pool to map virtual cpusets to (e.g., '20-31' or '1-3,5,8-11')",
    )
    gen_compose.add_argument(
        "--compose-output",
        type=Path,
        required=True,
        help="Path to write the generated compose file",
    )


def init_target_from_args(args) -> Target:
    target_harness = args.target_harness if hasattr(args, "target_harness") else None
    return Target(
        args.work_dir,
        args.target_proj_path,
        args.target_repo_path,
        target_harness,
    )


def _warn_deprecated_cli_aliases(argv: list[str]) -> None:
    for legacy, preferred in DEPRECATED_FLAGS.items():
        if legacy in argv:
            print(
                (
                    f"Warning: {legacy} is deprecated and will be removed in a "
                    f"future minor release. Use {preferred} instead."
                ),
                file=sys.stderr,
            )


def _sigterm_handler(signum, frame):
    """Convert SIGTERM into KeyboardInterrupt so cleanup tasks can run."""
    raise KeyboardInterrupt("SIGTERM received")


def cli() -> bool:
    signal.signal(signal.SIGTERM, _sigterm_handler)
    load_dotenv()
    parser = argparse.ArgumentParser(
        prog="oss-crs", description="OSS-CRS: Cyber Reasoning System orchestration CLI"
    )
    subparsers = parser.add_subparsers(
        dest="command", required=True, help="Command to run"
    )
    add_prepare_command(subparsers)
    add_build_target_command(subparsers)
    add_run_command(subparsers)
    add_artifacts_command(subparsers)
    add_check_command(subparsers)
    add_gen_compose_command(subparsers)
    add_setup_command(subparsers)

    argv = sys.argv[1:]
    _warn_deprecated_cli_aliases(argv)
    args = parser.parse_args(argv)

    # Handle setup command early - it doesn't need compose file
    if args.command == "setup":
        return handle_setup(args)

    # Resolve all Path arguments to absolute paths so that relative paths
    # (e.g., --fuzz-proj-path ../ghostscript) work regardless of cwd.
    for key, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, key, value.expanduser().resolve())

    # Handle gen-compose early - it doesn't need CRSCompose initialization
    if args.command == "gen-compose":
        template_path = args.compose_template
        if not template_path.exists():
            print(f"Error: Template file not found: {template_path}", file=sys.stderr)
            return False
        try:
            config = CRSComposeConfig.from_yaml_file(template_path)
            config.map_cpus(args.cpus)
            config.to_yaml_file(args.compose_output)
            print(f"Generated compose file: {args.compose_output}")
            return True
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            return False
        except Exception as e:
            print(f"Error: Failed to process template: {e}", file=sys.stderr)
            return False

    # Skip CRS repo init for commands that don't need it
    skip_crs_init = args.command == "artifacts"
    crs_compose = CRSCompose.from_yaml_file(
        args.compose_file, args.work_dir, skip_crs_init=skip_crs_init
    )

    if args.command == "prepare":
        if not crs_compose.prepare(publish=args.publish):
            return False
    elif args.command == "build-target":
        target = init_target_from_args(args)
        if args.bug_candidate and args.bug_candidate_dir:
            print(
                "Error: --bug-candidate and --bug-candidate-dir are mutually exclusive."
            )
            return False
        bug_candidate = args.bug_candidate if hasattr(args, "bug_candidate") else None
        bug_candidate_dir = (
            args.bug_candidate_dir if hasattr(args, "bug_candidate_dir") else None
        )
        if not crs_compose.build_target(
            target,
            build_id=args.build_id,
            bug_candidate=bug_candidate,
            bug_candidate_dir=bug_candidate_dir,
            diff=args.diff,
        ):
            return False
    elif args.command == "run":
        target = init_target_from_args(args)
        if args.timeout is not None:
            crs_compose.set_deadline(time.monotonic() + args.timeout)
        if args.bug_candidate and args.bug_candidate_dir:
            print(
                "Error: --bug-candidate and --bug-candidate-dir are mutually exclusive."
            )
            return False
        bug_candidate = args.bug_candidate if hasattr(args, "bug_candidate") else None
        bug_candidate_dir = (
            args.bug_candidate_dir if hasattr(args, "bug_candidate_dir") else None
        )
        if not crs_compose.run(
            target,
            run_id=args.run_id,
            build_id=args.build_id,
            pov=args.pov,
            pov_dir=args.pov_dir,
            diff=args.diff,
            seed_dir=args.seed_dir,
            bug_candidate=bug_candidate,
            bug_candidate_dir=bug_candidate_dir,
        ):
            return False
    elif args.command == "artifacts":
        target = init_target_from_args(args)
        return handle_artifacts(args, crs_compose, target)
    elif args.command == "check":
        pass
    return True


def main() -> int:
    return 0 if cli() else 1


if __name__ == "__main__":
    sys.exit(main())

import json
import os
import sys
import argparse
from pathlib import Path
from ..base import DataType, CRSUtils
from ..local import LocalCRSUtils
from ..common import get_run_env_type, EnvType
from ..fuzzer import FuzzerHandle, FuzzerStatus, FuzzerResult


def init_crs_utils() -> CRSUtils:
    env_type = get_run_env_type()
    if env_type == EnvType.LOCAL:
        return LocalCRSUtils()
    else:
        raise NotImplementedError(
            f"CRSUtils not implemented for run environment: {env_type}"
        )


class DaemonContext:
    def __init__(self, log_path: str = None):
        self.log_path = log_path
        self.log_file = None

    def __enter__(self):
        pid = os.fork()
        if pid > 0:
            # Parent exits immediately
            print(f"Started daemon with PID: {pid}")
            os._exit(0)  # Use os._exit to avoid cleanup in parent

        # Child continues as daemon
        os.setsid()

        # Redirect stdout/stderr to log file or /dev/null
        if self.log_path:
            self.log_file = open(self.log_path, "a", buffering=1)
        else:
            self.log_file = open(os.devnull, "w")

        sys.stdout = self.log_file
        sys.stderr = self.log_file
        sys.stdin = open(os.devnull, "r")

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.log_file:
            self.log_file.close()
        return False


def register_submit_dir(crs_utils, args):
    with DaemonContext(log_path=args.log):
        crs_utils.register_submit_dir(args.type, args.path)


def register_fetch_dir(crs_utils, args):
    with DaemonContext(log_path=args.log):
        crs_utils.register_fetch_dir(args.type, args.path)


def get_service_domain(crs_utils, args):
    domain = crs_utils.get_service_domain(args.service_name)
    print(domain)


def main():
    crs_utils = init_crs_utils()
    parser = argparse.ArgumentParser(
        prog="libCRS", description="libCRS - CRS utilities"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # =========================================================================
    # Build output commands
    # =========================================================================

    # submit-build-output command
    submit_build_parser = subparsers.add_parser(
        "submit-build-output", help="Submit build output from src_path to dst_path"
    )
    submit_build_parser.add_argument("src_path", help="Source path in docker container")
    submit_build_parser.add_argument(
        "dst_path", help="Destination path on build output file system"
    )
    submit_build_parser.set_defaults(
        func=lambda args: crs_utils.submit_build_output(args.src_path, args.dst_path)
    )

    # skip-build-output command
    skip_parser = subparsers.add_parser(
        "skip-build-output",
        help="Skip build output for dst_path on build output file system",
    )
    skip_parser.add_argument(
        "dst_path", help="Destination path on build output file system"
    )
    skip_parser.set_defaults(
        func=lambda args: crs_utils.skip_build_output(args.dst_path)
    )

    # download-build-output command
    download_build_parser = subparsers.add_parser(
        "download-build-output",
        help="Download build output from src_path (on build output filesystem) to dst_path (in docker container)",
    )
    download_build_parser.add_argument(
        "src_path", help="Source path on build output file system"
    )
    download_build_parser.add_argument(
        "dst_path", help="Destination path in docker container"
    )
    download_build_parser.set_defaults(
        func=lambda args: crs_utils.download_build_output(args.src_path, args.dst_path)
    )

    # =========================================================================
    # Data registration commands (auto-sync directories)
    # =========================================================================

    # Valid types for submit vs fetch commands
    submit_types = [DataType.POV, DataType.SEED, DataType.BUG_CANDIDATE, DataType.PATCH]
    fetch_types = list(DataType)

    # register-submit-dir command (auto-submit data to oss-crs-infra)
    register_submit_dir_parser = subparsers.add_parser(
        "register-submit-dir",
        help="Register a directory for automatic submission to oss-crs-infra",
    )
    register_submit_dir_parser.add_argument(
        "type",
        type=DataType,
        choices=submit_types,
        metavar="TYPE",
        help="Type of data: pov, seed, bug-candidate, patch",
    )
    register_submit_dir_parser.add_argument(
        "path", type=Path, help="Directory path to register"
    )
    register_submit_dir_parser.add_argument(
        "--log",
        type=Path,
        default=None,
        help="Log file path for the registered directory",
    )
    register_submit_dir_parser.set_defaults(
        func=lambda args: register_submit_dir(crs_utils, args)
    )

    # register-shared-dir command (share a directory between containers in a CRS)
    register_shared_dir_parser = subparsers.add_parser(
        "register-shared-dir",
        help="Register a shared directory for sharing data between containers in a CRS",
    )
    register_shared_dir_parser.add_argument(
        "local_path", type=Path, help="Local directory path inside the container"
    )
    register_shared_dir_parser.add_argument(
        "shared_path",
        type=str,
        help="Path on the shared filesystem accessible by all containers in the CRS",
    )
    register_shared_dir_parser.set_defaults(
        func=lambda args: crs_utils.register_shared_dir(
            args.local_path, args.shared_path
        )
    )

    # register-fetch-dir command (auto-fetch shared data from other CRS)
    register_fetch_dir_parser = subparsers.add_parser(
        "register-fetch-dir",
        help="Register a directory to automatically fetch shared data from other CRS",
    )
    register_fetch_dir_parser.add_argument(
        "type",
        type=DataType,
        choices=fetch_types,
        metavar="TYPE",
        help="Type of data: pov, seed, bug-candidate, patch, diff",
    )
    register_fetch_dir_parser.add_argument(
        "path", type=Path, help="Directory path to receive shared data"
    )
    register_fetch_dir_parser.add_argument(
        "--log",
        type=Path,
        default=None,
        help="Log file path for the registered directory",
    )
    register_fetch_dir_parser.set_defaults(
        func=lambda args: register_fetch_dir(crs_utils, args)
    )

    # =========================================================================
    # Manual data operations
    # =========================================================================

    # submit command (manually submit a single file)
    submit_parser = subparsers.add_parser(
        "submit",
        help="Submit a single file to oss-crs-infra",
    )
    submit_parser.add_argument(
        "type",
        type=DataType,
        choices=submit_types,
        metavar="TYPE",
        help="Type of data: pov, seed, bug-candidate, patch",
    )
    submit_parser.add_argument("path", type=Path, help="File path to submit")
    submit_parser.set_defaults(func=lambda args: crs_utils.submit(args.type, args.path))

    # fetch command (manually fetch shared data)
    fetch_parser = subparsers.add_parser(
        "fetch",
        help="Fetch shared data from other CRS to a directory",
    )
    fetch_parser.add_argument(
        "type",
        type=DataType,
        choices=fetch_types,
        metavar="TYPE",
        help="Type of data: pov, seed, bug-candidate, patch, diff",
    )
    fetch_parser.add_argument("path", type=Path, help="Output directory path")
    fetch_parser.set_defaults(
        func=lambda args: print("\n".join(crs_utils.fetch(args.type, args.path)))
    )

    # =========================================================================
    # Patch build commands
    # =========================================================================

    # apply-patch-build command
    apply_patch_build_parser = subparsers.add_parser(
        "apply-patch-build",
        help="Apply a patch to the snapshot image and rebuild",
    )
    apply_patch_build_parser.add_argument(
        "patch_path", type=Path, help="Path to the unified diff file"
    )
    apply_patch_build_parser.add_argument(
        "response_dir", type=Path, help="Directory to receive build results"
    )
    apply_patch_build_parser.add_argument(
        "--builder", type=str, required=True,
        help="Builder sidecar module name (resolved to URL via get-service-domain)",
    )

    def _apply_patch_build(args):
        exit_code = crs_utils.apply_patch_build(
            args.patch_path, args.response_dir, args.builder,
        )
        sys.exit(exit_code)

    apply_patch_build_parser.set_defaults(func=_apply_patch_build)

    # run-pov command
    run_pov_parser = subparsers.add_parser(
        "run-pov",
        help="Run a POV binary against a specific build's output",
    )
    run_pov_parser.add_argument(
        "pov_path", type=Path, help="Path to the POV binary file"
    )
    run_pov_parser.add_argument(
        "response_dir", type=Path, help="Directory to receive POV results"
    )
    run_pov_parser.add_argument(
        "--harness", type=str, required=True,
        help="Harness binary name in /out/",
    )
    run_pov_parser.add_argument(
        "--build-id", type=str, required=True,
        help="Build ID from a prior apply-patch-build call",
    )
    run_pov_parser.add_argument(
        "--builder", type=str, required=True,
        help="Builder sidecar module name (resolved to URL via get-service-domain)",
    )

    def _run_pov(args):
        exit_code = crs_utils.run_pov(
            args.pov_path, args.harness, args.build_id, args.response_dir,
            args.builder,
        )
        sys.exit(exit_code)

    run_pov_parser.set_defaults(func=_run_pov)

    # run-test command
    run_test_parser = subparsers.add_parser(
        "run-test",
        help="Run the project's bundled test.sh against a specific build's output",
    )
    run_test_parser.add_argument(
        "response_dir", type=Path, help="Directory to receive test results"
    )
    run_test_parser.add_argument(
        "--build-id", type=str, required=True,
        help="Build ID from a prior apply-patch-build call",
    )
    run_test_parser.add_argument(
        "--builder", type=str, required=True,
        help="Builder sidecar module name (resolved to URL via get-service-domain)",
    )

    def _run_test(args):
        exit_code = crs_utils.run_test(
            args.build_id, args.response_dir, args.builder,
        )
        sys.exit(exit_code)

    run_test_parser.set_defaults(func=_run_test)

    # =========================================================================
    # Service discovery
    # =========================================================================

    get_service_domain_parser = subparsers.add_parser(
        "get-service-domain",
        help="Get the service domain for accessing CRS services",
    )
    get_service_domain_parser.add_argument(
        "service_name", type=str, help="Service name to get the domain for"
    )
    get_service_domain_parser.set_defaults(
        func=lambda args: get_service_domain(crs_utils, args)
    )

    # =========================================================================
    # Fuzzer commands
    # =========================================================================

    # start-fuzzer command
    start_fuzzer_parser = subparsers.add_parser(
        "start-fuzzer",
        help="Start a fuzzer in the fuzzer sidecar (non-blocking, prints fuzzer_id)",
    )
    start_fuzzer_parser.add_argument(
        "harness", type=str, help="Harness binary name in /out/"
    )
    start_fuzzer_parser.add_argument(
        "corpus_dir", type=Path, help="Directory for corpus files"
    )
    start_fuzzer_parser.add_argument(
        "crashes_dir", type=Path, help="Directory for crash files"
    )
    start_fuzzer_parser.add_argument(
        "--fuzzer", type=str, required=True,
        help="Fuzzer sidecar module name",
    )
    start_fuzzer_parser.add_argument(
        "--engine", type=str, default="libfuzzer",
        help="Fuzzing engine (default: libfuzzer)",
    )
    start_fuzzer_parser.add_argument(
        "--timeout", type=int, default=0,
        help="Maximum fuzzing time in seconds (0 = unlimited)",
    )
    start_fuzzer_parser.add_argument(
        "--extra-args", type=str, default=None,
        help="Additional engine-specific arguments (space-separated)",
    )

    def _start_fuzzer(args):
        extra_args = args.extra_args.split() if args.extra_args else None
        try:
            handle = crs_utils.start_fuzzer(
                harness_name=args.harness,
                corpus_dir=args.corpus_dir,
                crashes_dir=args.crashes_dir,
                fuzzer=args.fuzzer,
                engine=args.engine,
                timeout=args.timeout,
                extra_args=extra_args,
            )
            print(json.dumps({"fuzzer_id": handle.fuzzer_id, "pid": handle.pid}))
        except RuntimeError as e:
            print(json.dumps({"error": str(e)}), file=sys.stderr)
            sys.exit(1)

    start_fuzzer_parser.set_defaults(func=_start_fuzzer)

    # fuzzer-status command
    fuzzer_status_parser = subparsers.add_parser(
        "fuzzer-status",
        help="Get status of a running fuzzer (JSON output)",
    )
    fuzzer_status_parser.add_argument(
        "fuzzer_id", type=str, help="Fuzzer ID from start-fuzzer"
    )
    fuzzer_status_parser.add_argument(
        "--fuzzer", type=str, required=True,
        help="Fuzzer sidecar module name",
    )

    def _fuzzer_status(args):
        try:
            status = crs_utils.fuzzer_status(args.fuzzer_id, args.fuzzer)
            print(json.dumps({
                "state": status.state,
                "runtime_seconds": status.runtime_seconds,
                "execs": status.execs,
                "corpus_size": status.corpus_size,
                "crashes_found": status.crashes_found,
                "pid": status.pid,
            }))
        except RuntimeError as e:
            print(json.dumps({"error": str(e)}), file=sys.stderr)
            sys.exit(1)

    fuzzer_status_parser.set_defaults(func=_fuzzer_status)

    # stop-fuzzer command
    stop_fuzzer_parser = subparsers.add_parser(
        "stop-fuzzer",
        help="Stop a running fuzzer (blocks until terminated, prints result JSON)",
    )
    stop_fuzzer_parser.add_argument(
        "fuzzer_id", type=str, help="Fuzzer ID from start-fuzzer"
    )
    stop_fuzzer_parser.add_argument(
        "--fuzzer", type=str, required=True,
        help="Fuzzer sidecar module name",
    )

    def _stop_fuzzer(args):
        try:
            result = crs_utils.stop_fuzzer(args.fuzzer_id, args.fuzzer)
            print(json.dumps({
                "exit_code": result.exit_code,
                "runtime_seconds": result.runtime_seconds,
                "corpus_size": result.corpus_size,
                "crashes_found": result.crashes_found,
            }))
            sys.exit(result.exit_code)
        except RuntimeError as e:
            print(json.dumps({"error": str(e)}), file=sys.stderr)
            sys.exit(1)

    stop_fuzzer_parser.set_defaults(func=_stop_fuzzer)

    # list-fuzzers command
    list_fuzzers_parser = subparsers.add_parser(
        "list-fuzzers",
        help="List all fuzzer instances in the sidecar (JSON array)",
    )
    list_fuzzers_parser.add_argument(
        "--fuzzer", type=str, required=True,
        help="Fuzzer sidecar module name",
    )

    def _list_fuzzers(args):
        try:
            handles = crs_utils.list_fuzzers(args.fuzzer)
            print(json.dumps([
                {"fuzzer_id": h.fuzzer_id, "pid": h.pid}
                for h in handles
            ]))
        except RuntimeError as e:
            print(json.dumps({"error": str(e)}), file=sys.stderr)
            sys.exit(1)

    list_fuzzers_parser.set_defaults(func=_list_fuzzers)

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return

    else:
        args.func(args)


if __name__ == "__main__":
    main()

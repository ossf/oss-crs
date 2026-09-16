# SPDX-License-Identifier: MIT
import json
import os
import sys
import argparse
from pathlib import Path
from ..base import DataType, SourceType, CRSUtils
from ..local import LocalCRSUtils
from ..common import get_run_env_type, EnvType
from ..mcp import MCPClient, MCPGatewayError


def init_crs_utils() -> CRSUtils:
    env_type = get_run_env_type()
    if env_type == EnvType.LOCAL:
        return LocalCRSUtils()
    else:
        raise NotImplementedError(
            f"CRSUtils not implemented for run environment: {env_type}"
        )


class DaemonContext:
    def __init__(self, log_path: str | None = None):
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


# =========================================================================
# MCP gateway command handlers
# =========================================================================

_MCP_TRUNCATION_NOTE = (
    "\n[libCRS mcp: output truncated at {limit} characters; "
    "rerun with --max-output-chars 0 for the full output]"
)


def _mcp_client_from_env():
    client = MCPClient.from_env()
    if not client.is_enabled():
        raise MCPGatewayError(
            "MCP gateway is not configured in this environment "
            "(OSS_CRS_LLM_API_URL and OSS_CRS_LLM_API_KEY_FILE / "
            "OSS_CRS_LLM_API_KEY are missing)"
        )
    return client


def _run_mcp(func, args):
    try:
        func(args)
    except MCPGatewayError as e:
        print(f"libCRS mcp: error: {e}", file=sys.stderr)
        sys.exit(2)


def _print_truncated(text: str, max_chars: int) -> None:
    if max_chars and max_chars > 0 and len(text) > max_chars:
        print(text[:max_chars] + _MCP_TRUNCATION_NOTE.format(limit=max_chars))
    else:
        print(text)


def _mcp_list(args) -> None:
    """`libCRS mcp list`: print available tool names (or full JSON)."""
    client = _mcp_client_from_env()
    tools = client.list_tools(getattr(args, "server", None))
    if getattr(args, "json", False):
        _print_truncated(
            json.dumps(tools, indent=2), getattr(args, "max_output_chars", 20000) or 0
        )
        return
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        name = tool.get("name", "?")
        desc = (tool.get("description") or "").splitlines()
        print(f"{name} - {desc[0] if desc else ''}".rstrip(" -"))


def _mcp_describe(args) -> None:
    """`libCRS mcp describe`: print one tool's description + input schema."""
    client = _mcp_client_from_env()
    tool = client.describe(args.name, getattr(args, "server", None))
    if getattr(args, "json", False):
        print(json.dumps(tool, indent=2))
        return
    print(f"Tool: {tool.get('name', args.name)}")
    info = tool.get("mcp_info") or {}
    server = info.get("alias") or info.get("server_id") or "?"
    print(f"Server: {server}")
    if tool.get("description"):
        print(f"Description: {tool['description']}")
    print("Input schema:")
    print(json.dumps(tool.get("inputSchema", {}), indent=2))


def _mcp_call(args) -> None:
    """`libCRS mcp call`: invoke a tool and print its result as text."""
    if getattr(args, "args_file", None):
        try:
            arguments = json.loads(Path(args.args_file).read_text())
        except (OSError, ValueError) as e:
            print(f"libCRS mcp: error: cannot read --args-file: {e}", file=sys.stderr)
            sys.exit(2)
    else:
        try:
            arguments = json.loads(getattr(args, "args", "{}"))
        except ValueError as e:
            print(f"libCRS mcp: error: --args is not valid JSON: {e}", file=sys.stderr)
            sys.exit(2)
    if not isinstance(arguments, dict):
        print(
            "libCRS mcp: error: tool arguments must be a JSON object",
            file=sys.stderr,
        )
        sys.exit(2)

    client = _mcp_client_from_env()
    result = client.call_tool(
        args.name,
        arguments,
        getattr(args, "server", None),
        timeout=getattr(args, "timeout", None),
    )
    if getattr(args, "json", False):
        _print_truncated(
            json.dumps(result, indent=2), getattr(args, "max_output_chars", 0) or 0
        )
        return
    _print_truncated(
        _extract_result_text(result), getattr(args, "max_output_chars", 20000) or 0
    )


def _extract_result_text(result) -> str:
    """Best-effort text rendering of an MCP CallToolResult for agents."""
    if isinstance(result, str):
        return result
    if not isinstance(result, dict):
        return json.dumps(result, indent=2)
    parts = []
    for block in result.get("content", []) or []:
        if not isinstance(block, dict):
            parts.append(json.dumps(block))
        elif block.get("type") == "text":
            parts.append(str(block.get("text", "")))
        else:
            parts.append(json.dumps(block))
    if parts:
        return "\n".join(parts)
    structured = result.get("structuredContent")
    if structured is not None:
        return json.dumps(structured, indent=2)
    return json.dumps(result, indent=2)


def main():
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
    submit_build_parser.add_argument(
        "--rebuild-id",
        type=int,
        default=None,
        help="Rebuild ID (required during run phase, forbidden during build-target phase)",
    )
    submit_build_parser.set_defaults(
        func=lambda args: crs_utils.submit_build_output(
            args.src_path, Path(args.dst_path), rebuild_id=args.rebuild_id
        )
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
        help="Download build output to dst_path. Uses rebuild artifacts if --rebuild-id provided, otherwise build-target artifacts.",
    )
    download_build_parser.add_argument(
        "src_path", help="Source path within build output directory"
    )
    download_build_parser.add_argument(
        "dst_path", help="Destination path in docker container"
    )
    download_build_parser.add_argument(
        "--rebuild-id",
        type=int,
        default=None,
        help="Rebuild ID to fetch sidecar artifacts (omit for build-target artifacts)",
    )
    download_build_parser.set_defaults(
        func=lambda args: crs_utils.download_build_output(
            args.src_path, Path(args.dst_path), rebuild_id=args.rebuild_id
        )
    )

    # download-source command
    download_source_parser = subparsers.add_parser(
        "download-source",
        help="Download source tree from mount path to destination",
    )
    download_source_parser.add_argument(
        "type",
        type=SourceType,
        choices=list(SourceType),
        metavar="TYPE",
        help="Source type: fuzz-proj, target-source",
    )
    download_source_parser.add_argument(
        "dst_path",
        type=Path,
        help="Destination path in docker container",
    )
    download_source_parser.set_defaults(
        func=lambda args: crs_utils.download_source(args.type, args.dst_path)
    )

    # =========================================================================
    # Data registration commands (auto-sync directories)
    # =========================================================================

    # Valid types for submit vs fetch commands. `report` is submit-only via the
    # one-shot `submit` command (it is bundled into a tarball); it is not a
    # valid target for `register-submit-dir`.
    submit_types = [
        DataType.POV,
        DataType.SEED,
        DataType.BUG_CANDIDATE,
        DataType.REPORT,
        DataType.PATCH,
    ]
    register_submit_types = [t for t in submit_types if t != DataType.REPORT]
    fetch_types = list(DataType)

    # register-submit-dir command (auto-submit data to oss-crs-infra)
    register_submit_dir_parser = subparsers.add_parser(
        "register-submit-dir",
        help="Register a directory for automatic submission to oss-crs-infra",
    )
    register_submit_dir_parser.add_argument(
        "type",
        type=DataType,
        choices=register_submit_types,
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

    # register-log-dir command (symlink a local directory into LOG_DIR)
    register_log_dir_parser = subparsers.add_parser(
        "register-log-dir",
        help="Register a local directory for persisting CRS agent/internal logs",
    )
    register_log_dir_parser.add_argument(
        "local_path",
        type=Path,
        help="Local directory path inside the container to symlink into LOG_DIR",
    )
    register_log_dir_parser.set_defaults(
        func=lambda args: crs_utils.register_log_dir(args.local_path)
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
        help="Type of data: pov, seed, bug-candidate, report, patch, diff",
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
        help="Type of data: pov, seed, bug-candidate, report, patch",
    )
    submit_parser.add_argument(
        "path",
        type=Path,
        help="File to submit (for `report`, a file or directory bundled into a tarball)",
    )
    submit_parser.set_defaults(func=lambda args: crs_utils.submit(args.type, args.path))

    # submit-harness command (harness-gen output: fuzz-proj dir + optional target source dir)
    submit_harness_parser = subparsers.add_parser(
        "submit-harness",
        help="Submit a generated harness project (fuzz-proj dir + optional target source dir)",
    )
    submit_harness_parser.add_argument(
        "--fuzz-proj-dir",
        type=Path,
        required=True,
        dest="fuzz_proj_dir",
        metavar="DIR",
        help="OSS-Fuzz project directory (Dockerfile, build.sh, harness source)",
    )
    submit_harness_parser.add_argument(
        "--target-source-dir",
        type=Path,
        default=None,
        dest="target_source_dir",
        metavar="DIR",
        help="Modified upstream target source tree (optional)",
    )
    submit_harness_parser.add_argument(
        "--name",
        type=str,
        default=None,
        help="Subdir name under harnesses (defaults to fuzz-proj dir basename)",
    )
    submit_harness_parser.set_defaults(
        func=lambda args: crs_utils.submit_harness(
            args.fuzz_proj_dir, args.target_source_dir, args.name
        )
    )

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
        help="Type of data: pov, seed, bug-candidate, report, patch, diff",
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
        help="Apply a target-source patch to the snapshot image and rebuild",
    )
    apply_patch_build_parser.add_argument(
        "patch_path", type=Path, help="Path to the unified diff file"
    )
    apply_patch_build_parser.add_argument(
        "response_dir", type=Path, help="Directory to receive build results"
    )
    apply_patch_build_parser.add_argument(
        "--builder",
        type=str,
        default=None,
        help="Builder sidecar module name (defaults to BUILDER_MODULE env var)",
    )
    apply_patch_build_parser.add_argument(
        "--builder-name",
        type=str,
        default=None,
        help="Builder config name for image resolution (e.g. 'coverage-build'). Defaults to --builder value.",
    )
    apply_patch_build_parser.add_argument(
        "--rebuild-id",
        type=int,
        default=None,
        help="Rebuild ID (auto-increments if omitted, overwrites artifacts if provided)",
    )

    def _apply_patch_build(args):
        exit_code = crs_utils.apply_patch_build(
            args.patch_path,
            args.response_dir,
            builder=args.builder,
            builder_name=args.builder_name,
            rebuild_id=args.rebuild_id,
        )
        sys.exit(exit_code)

    apply_patch_build_parser.set_defaults(func=_apply_patch_build)

    # build-project command (harness-gen: rebuild from a modified fuzz-proj and/or target source)
    build_project_parser = subparsers.add_parser(
        "build-project",
        help="Rebuild the project image from a modified fuzz-proj and/or target source dir",
    )
    build_project_parser.add_argument(
        "--response-dir",
        type=Path,
        required=True,
        dest="response_dir",
        metavar="DIR",
        help="Directory to receive build results",
    )
    build_project_parser.add_argument(
        "--fuzz-proj-dir",
        type=Path,
        default=None,
        dest="fuzz_proj_dir",
        metavar="DIR",
        help="Modified OSS-Fuzz project directory (diffed against the fuzz-proj base)",
    )
    build_project_parser.add_argument(
        "--target-source-dir",
        type=Path,
        default=None,
        dest="target_source_dir",
        metavar="DIR",
        help="Modified target source directory (diffed against the target-source base)",
    )
    build_project_parser.add_argument(
        "--builder",
        type=str,
        default=None,
        help="Builder sidecar module name (defaults to BUILDER_MODULE env var)",
    )
    build_project_parser.add_argument(
        "--builder-name",
        type=str,
        default=None,
        help="Builder config name for image resolution (e.g. 'coverage-build'). Defaults to --builder value.",
    )
    build_project_parser.add_argument(
        "--rebuild-id",
        type=int,
        default=None,
        help="Rebuild ID (auto-increments if omitted, overwrites artifacts if provided)",
    )

    def _build_project(args):
        if not args.fuzz_proj_dir and not args.target_source_dir:
            build_project_parser.error(
                "At least one of --fuzz-proj-dir or --target-source-dir must be provided"
            )
        exit_code = crs_utils.build_project(
            args.response_dir,
            fuzz_proj_dir=args.fuzz_proj_dir,
            target_source_dir=args.target_source_dir,
            builder=args.builder,
            builder_name=args.builder_name,
            rebuild_id=args.rebuild_id,
        )
        sys.exit(exit_code)

    build_project_parser.set_defaults(func=_build_project)

    # run-pov command
    run_pov_parser = subparsers.add_parser(
        "run-pov",
        help="Run a POV binary against a specific rebuild's output",
    )
    run_pov_parser.add_argument(
        "pov_path", type=Path, help="Path to the POV binary file"
    )
    run_pov_parser.add_argument(
        "response_dir", type=Path, help="Directory to receive POV results"
    )
    run_pov_parser.add_argument(
        "--harness",
        type=str,
        required=True,
        help="Harness binary name in /out/",
    )
    run_pov_parser.add_argument(
        "--rebuild-id",
        type=str,
        default=None,
        help="Rebuild ID from a prior apply-patch-build call. Omit to run against the base build.",
    )
    run_pov_parser.add_argument(
        "--builder",
        type=str,
        default=None,
        help="Runner sidecar module name (defaults to BUILDER_MODULE env var)",
    )

    def _run_pov(args):
        exit_code = crs_utils.run_pov(
            args.pov_path,
            args.harness,
            args.response_dir,
            args.rebuild_id,
            args.builder,
        )
        sys.exit(exit_code)

    run_pov_parser.set_defaults(func=_run_pov)

    # apply-patch-test command
    apply_patch_test_parser = subparsers.add_parser(
        "apply-patch-test",
        help="Apply a target-source patch and run the project's bundled test.sh",
    )
    apply_patch_test_parser.add_argument(
        "patch_path", type=Path, help="Path to the unified diff file"
    )
    apply_patch_test_parser.add_argument(
        "response_dir", type=Path, help="Directory to receive test results"
    )
    apply_patch_test_parser.add_argument(
        "--builder",
        type=str,
        default=None,
        help="Builder sidecar module name (defaults to BUILDER_MODULE env var)",
    )

    def _apply_patch_test(args):
        exit_code = crs_utils.apply_patch_test(
            args.patch_path,
            args.response_dir,
            args.builder,
        )
        sys.exit(exit_code)

    apply_patch_test_parser.set_defaults(func=_apply_patch_test)

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
    # MCP gateway commands (no CRS utils needed: the gateway is reached with
    # the framework-injected LLM endpoint + per-CRS key)
    # =========================================================================

    mcp_parser = subparsers.add_parser(
        "mcp",
        help="Discover and call MCP tools via the OSS-CRS MCP gateway",
    )
    mcp_subparsers = mcp_parser.add_subparsers(
        dest="mcp_command", help="MCP gateway operations"
    )

    mcp_list_parser = mcp_subparsers.add_parser(
        "list", help="List MCP tools available to this CRS"
    )
    mcp_list_parser.add_argument(
        "--server",
        type=str,
        default=None,
        help="Only list tools from this MCP server (name, alias or id)",
    )
    mcp_list_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full tool list as JSON",
    )
    mcp_list_parser.add_argument(
        "--max-output-chars",
        type=int,
        default=20000,
        help="Truncate printed output beyond this many characters "
        "(0 disables truncation; default: 20000)",
    )
    mcp_list_parser.set_defaults(func=_mcp_list)

    mcp_describe_parser = mcp_subparsers.add_parser(
        "describe", help="Show a tool's description and input schema"
    )
    mcp_describe_parser.add_argument("name", help="Tool name")
    mcp_describe_parser.add_argument(
        "--server",
        type=str,
        default=None,
        help="MCP server hint (name, alias or id)",
    )
    mcp_describe_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full tool entry as JSON",
    )
    mcp_describe_parser.set_defaults(func=_mcp_describe)

    mcp_call_parser = mcp_subparsers.add_parser(
        "call", help="Call an MCP tool and print its result"
    )
    mcp_call_parser.add_argument("name", help="Tool name")
    mcp_call_parser.add_argument(
        "--server",
        type=str,
        default=None,
        help="MCP server hint (name, alias or id)",
    )
    mcp_call_parser.add_argument(
        "--args",
        type=str,
        default="{}",
        help="Tool arguments as a JSON object string (default: {})",
    )
    mcp_call_parser.add_argument(
        "--args-file",
        type=Path,
        default=None,
        help="Read tool arguments from a JSON file (overrides --args)",
    )
    mcp_call_parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="Request timeout in seconds",
    )
    mcp_call_parser.add_argument(
        "--max-output-chars",
        type=int,
        default=20000,
        help="Truncate printed output beyond this many characters "
        "(0 disables truncation; default: 20000)",
    )
    mcp_call_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the raw JSON result instead of extracted text",
    )
    mcp_call_parser.set_defaults(func=_mcp_call)

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return

    if args.command == "mcp":
        if getattr(args, "mcp_command", None) is None:
            mcp_parser.print_help()
            return
        _run_mcp(args.func, args)
        return

    # All other commands need CRS utils (requires OSS_CRS_RUN_ENV_TYPE).
    crs_utils = init_crs_utils()
    args.func(args)


if __name__ == "__main__":
    main()

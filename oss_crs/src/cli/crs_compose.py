# SPDX-License-Identifier: MIT
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
from .clean import add_clean_command, handle_clean
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


def add_target_resolution_arguments(parser):
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


def add_prepare_command(subparsers):
    prepare = subparsers.add_parser(
        "prepare", help="Prepare CRSs defined in CRS Compose file"
    )
    add_common_arguments(prepare)
    prepare.add_argument(
        "--publish",
        action="store_true",
        default=False,
        help="Publish prepared CRS docker images to the specified docker registry",
    )
    prepare.add_argument(
        "--no-pull",
        action="store_true",
        default=False,
        help="Skip pulling prebuilt images and always build locally",
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
        help="Sanitizer to use for the build (overrides compose/project.yaml; default: resolved from additional_env or 'address').",
    )
    build_target.add_argument(
        "--diff",
        type=Path,
        default=None,
        help="Diff file for directed build analysis, mounted into build-target containers.",
    )
    build_target.add_argument(
        "--incremental-build",
        action="store_true",
        default=False,
        help="Snapshot all builder images and the project image after build for fast incremental runs.",
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
        help="Sanitizer to use for the run (overrides compose/project.yaml; default: resolved from additional_env or 'address').",
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
    run.add_argument(
        "--early-exit",
        action="store_true",
        default=False,
        help="Stop run when first artifact is discovered (POV for bug-finding, patch for bug-fixing CRSs)",
    )
    run.add_argument(
        "--incremental-build",
        action="store_true",
        default=False,
        help="Snapshot all builder images and the project image after build for fast incremental runs.",
    )


def add_artifacts_command(subparsers):
    artifacts = subparsers.add_parser(
        "artifacts", help="Show directories for run artifacts (JSON output)"
    )
    add_common_arguments(artifacts)
    add_target_resolution_arguments(artifacts)
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
        "gen-compose",
        help="Generate a compose file from an example with optional resource overrides",
    )
    gen_compose.add_argument(
        "--example",
        type=str,
        required=True,
        help="Example name (resolves to example/<name>/compose.yaml)",
    )
    gen_compose.add_argument(
        "--cpus",
        type=str,
        default=None,
        help="CPU pool to allocate (e.g., '0-15' or '1-4,10-13'). "
        "Scales existing template allocations proportionally.",
    )
    gen_compose.add_argument(
        "--memory",
        type=str,
        default=None,
        help="Total memory to distribute (e.g., '64G'). "
        "Scales existing template allocations proportionally.",
    )
    gen_compose.add_argument(
        "--litellm-external",
        nargs=2,
        metavar=("URL_ENV", "KEY_ENV"),
        default=None,
        help="Set litellm to external mode with env var names for URL and API key "
        "(e.g., --litellm-external AIXCC_LITELLM_HOSTNAME LITELLM_KEY)",
    )
    gen_compose.add_argument(
        "--litellm-proxy",
        nargs="+",
        metavar="ARG",
        default=None,
        help="Override litellm config env vars to route through a proxy. "
        "Format: KEY_ENV PROVIDERS [BASE_URL_ENV]. "
        "PROVIDERS is a comma-separated list (e.g., openai,anthropic,gemini). "
        "Example: --litellm-proxy MY_KEY openai,anthropic MY_BASE",
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


def _handle_gen_compose(args) -> bool:
    """Handle the gen-compose command."""
    import yaml
    from ..cpuset import parse_cpuset, scale_cpusets, default_cpu_allocation
    from ..memory import parse_memory, scale_memory, default_memory_allocation

    # 1. Resolve template from example name
    example_dir = Path(__file__).resolve().parents[3] / "example" / args.example
    template_path = example_dir / "compose.yaml"
    if not template_path.exists():
        available = sorted(
            d.name
            for d in (Path(__file__).resolve().parents[3] / "example").iterdir()
            if d.is_dir() and (d / "compose.yaml").exists()
        )
        raise ValueError(
            f"Example '{args.example}' not found at {template_path}\n"
            f"Available examples: {', '.join(available)}"
        )

    # 2. Load as raw dict
    with open(template_path) as f:
        data = yaml.safe_load(f)

    reserved_keys = {"run_env", "docker_registry", "oss_crs_infra", "llm_config"}
    crs_names = [k for k in data if k not in reserved_keys]
    infra = data.get("oss_crs_infra", {})

    # 3. CPU handling
    has_cpusets = "cpuset" in infra or any(
        "cpuset" in data.get(n, {}) for n in crs_names
    )

    if args.cpus:
        parse_cpuset(args.cpus)  # validate format
        if has_cpusets:
            # Scale existing allocations proportionally
            allocations = {}
            allocations["oss_crs_infra"] = len(parse_cpuset(infra.get("cpuset", "0")))
            for name in crs_names:
                entry = data.get(name, {})
                allocations[name] = len(parse_cpuset(entry.get("cpuset", "0")))
            scaled = scale_cpusets(allocations, args.cpus)
        else:
            # No cpusets in template — use default allocation
            scaled = default_cpu_allocation(crs_names, args.cpus)

        # Apply scaled cpusets to data
        infra["cpuset"] = scaled["oss_crs_infra"]
        data["oss_crs_infra"] = infra
        for name in crs_names:
            if name in scaled:
                if not isinstance(data.get(name), dict):
                    data[name] = {}
                data[name]["cpuset"] = scaled[name]
    elif not has_cpusets:
        raise ValueError(
            "Template has no cpuset allocations and --cpus was not provided. "
            "Use --cpus to specify a CPU pool."
        )

    # 4. Memory handling
    has_memory = "memory" in infra or any(
        "memory" in data.get(n, {}) for n in crs_names
    )

    if args.memory:
        parse_memory(args.memory)  # validate format
        if has_memory:
            mem_allocations = {}
            mem_allocations["oss_crs_infra"] = infra.get("memory", "1G")
            for name in crs_names:
                entry = data.get(name, {})
                mem_allocations[name] = entry.get("memory", "1G")
            scaled_mem = scale_memory(mem_allocations, args.memory)
        else:
            scaled_mem = default_memory_allocation(crs_names, args.memory)

        infra["memory"] = scaled_mem["oss_crs_infra"]
        data["oss_crs_infra"] = infra
        for name in crs_names:
            if name in scaled_mem:
                if not isinstance(data.get(name), dict):
                    data[name] = {}
                data[name]["memory"] = scaled_mem[name]

    # 5. LiteLLM external override
    if args.litellm_external:
        url_env, key_env = args.litellm_external
        data["llm_config"] = {
            "litellm": {
                "mode": "external",
                "model_check": False,
                "external": {
                    "url_env": url_env,
                    "key_env": key_env,
                },
            }
        }

    # 5b. LiteLLM proxy override (rewrites env vars in litellm config)
    if args.litellm_proxy:
        from ..llm import apply_litellm_proxy_to_file, validate_providers

        proxy_args = args.litellm_proxy
        if len(proxy_args) < 2 or len(proxy_args) > 3:
            raise ValueError(
                "--litellm-proxy requires 2 or 3 arguments: KEY_ENV PROVIDERS [BASE_URL_ENV]"
            )
        proxy_key_env = proxy_args[0]
        providers_str = proxy_args[1]
        proxy_base_url_env = proxy_args[2] if len(proxy_args) == 3 else None

        providers = [p.strip() for p in providers_str.split(",")]
        validate_providers(providers)

        # Resolve the litellm config path from the compose data
        litellm_config_path = _resolve_litellm_config_path(data, example_dir)
        if litellm_config_path is None:
            raise ValueError(
                "--litellm-proxy requires a litellm config. "
                "The example has no llm_config with internal mode config_path."
            )

        if apply_litellm_proxy_to_file(
            litellm_config_path, proxy_key_env, proxy_base_url_env, providers
        ):
            print(f"Updated litellm config: {litellm_config_path}")
        else:
            print(f"No changes needed: {litellm_config_path}")

    # 6. Validate through CRSComposeConfig and write output
    config = CRSComposeConfig.from_dict(data)
    config.to_yaml_file(args.compose_output)
    print(f"Generated compose file: {args.compose_output}")
    return True


def _resolve_litellm_config_path(data: dict, example_dir: Path) -> "Path | None":
    """Resolve the litellm config file path from compose data.

    Looks at llm_config.litellm.internal.config_path. If it's a relative path,
    resolves it relative to the repo root (parent of example_dir's parent).
    Falls back to the default bundled config if no config_path is specified.
    """
    from ..llm import DEFAULT_LITELLM_CONFIG_PATH

    llm_config = data.get("llm_config")
    if llm_config is None:
        return None

    litellm = llm_config.get("litellm", {})
    if litellm.get("mode") != "internal":
        return None

    internal = litellm.get("internal", {})
    config_path = internal.get("config_path") if internal else None

    if config_path is None:
        return DEFAULT_LITELLM_CONFIG_PATH

    path = Path(config_path)
    if not path.is_absolute():
        # config_path in examples is relative to repo root (e.g. ./example/foo/litellm-config.yaml)
        repo_root = (
            example_dir.parents[0].parent
            if "example" in example_dir.parts
            else example_dir
        )
        # Walk up from example_dir to find repo root (directory containing "example/")
        repo_root = Path(__file__).resolve().parents[3]
        path = (repo_root / config_path).resolve()

    return path


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


def cli() -> bool | int:
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
    add_clean_command(subparsers, add_common_arguments, add_target_arguments)
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
        try:
            return _handle_gen_compose(args)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            return False
        except Exception as e:
            print(f"Error: Failed to generate compose: {e}", file=sys.stderr)
            return False

    # Handle clean early - it manages its own CRSCompose initialization
    if args.command == "clean":
        return handle_clean(args)

    # Skip CRS repo init for commands that don't need it
    skip_crs_init = args.command == "artifacts"
    crs_compose = CRSCompose.from_yaml_file(
        args.compose_file, args.work_dir, skip_crs_init=skip_crs_init
    )

    if args.command == "prepare":
        if not crs_compose.prepare(publish=args.publish, no_pull=args.no_pull):
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
            sanitizer=args.sanitizer,
            bug_candidate=bug_candidate,
            bug_candidate_dir=bug_candidate_dir,
            diff=args.diff,
            incremental_build=args.incremental_build,
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
        run_rc = crs_compose.run(
            target,
            run_id=args.run_id,
            build_id=args.build_id,
            sanitizer=args.sanitizer,
            pov=args.pov,
            pov_dir=args.pov_dir,
            diff=args.diff,
            seed_dir=args.seed_dir,
            bug_candidate=bug_candidate,
            bug_candidate_dir=bug_candidate_dir,
            early_exit=args.early_exit,
            incremental_build=args.incremental_build,
        )
        if run_rc != 0:
            return run_rc
    elif args.command == "artifacts":
        target = init_target_from_args(args)
        return handle_artifacts(args, crs_compose, target)
    elif args.command == "check":
        pass
    return True


def main() -> int:
    rc = cli()
    if isinstance(rc, bool):
        return 0 if rc else 1
    return rc


if __name__ == "__main__":
    sys.exit(main())

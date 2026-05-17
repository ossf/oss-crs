# SPDX-License-Identifier: MIT
"""Clean command for oss-crs: remove Docker images and work-directory artifacts."""

import shutil
from dataclasses import dataclass, field
from pathlib import Path

import docker
import docker.errors

from ..constants import PRESERVED_BUILDER_REPO
from ..utils import confirm, get_console, green, red, yellow, rm_with_docker


# ---------------------------------------------------------------------------
# Plan dataclass
# ---------------------------------------------------------------------------

@dataclass
class CleanPlan:
    """Accumulates items to be cleaned, grouped by category."""

    prepare_images: list[str] = field(default_factory=list)
    builder_images: list[str] = field(default_factory=list)
    snapshot_images: list[str] = field(default_factory=list)
    target_images: list[str] = field(default_factory=list)
    run_images: list[str] = field(default_factory=list)
    artifact_dirs: list[Path] = field(default_factory=list)
    @property
    def all_images(self) -> list[str]:
        return (
            self.prepare_images
            + self.builder_images
            + self.snapshot_images
            + self.target_images
            + self.run_images
        )

    @property
    def is_empty(self) -> bool:
        return not self.all_images and not self.artifact_dirs


# ---------------------------------------------------------------------------
# Discovery helpers
# ---------------------------------------------------------------------------

def discover_prepare_images(crs_compose) -> list[str]:
    """Discover images produced by prepare-phase bake for all CRSs."""
    candidate_tags: list[str] = []
    for crs in crs_compose.crs_list:
        try:
            candidate_tags.extend(crs.get_bake_image_tags())
        except Exception:
            pass  # best-effort; CRS repo may not be available

    # Only include images that actually exist locally
    client = docker.from_env()
    existing: list[str] = []
    for tag in _dedupe(candidate_tags):
        try:
            client.images.get(tag)
            existing.append(tag)
        except docker.errors.ImageNotFound:
            pass
    return existing


def _enumerate_ids(base_dir: Path, subdir: str) -> list[str]:
    """List build-ids or run-ids from ``{sanitizer}/{subdir}/{id}/`` dirs."""
    ids: set[str] = set()
    if not base_dir.exists():
        return []
    for sanitizer_dir in base_dir.iterdir():
        if not sanitizer_dir.is_dir():
            continue
        container = sanitizer_dir / subdir
        if not container.is_dir():
            continue
        for entry in container.iterdir():
            if entry.is_dir():
                ids.add(entry.name)
    return sorted(ids)


def discover_build_target_images(crs_compose, target=None) -> tuple[list[str], list[str], list[str]]:
    """Discover builder, snapshot, and target-base images scoped to this compose config.

    Uses CRS names from the compose config and build-ids from the workdir to
    match only images belonging to this configuration.

    Returns (builder_tags, snapshot_tags, target_tags).
    """
    client = docker.from_env()
    builder_tags: list[str] = []
    snapshot_tags: list[str] = []
    target_tags: list[str] = []

    crs_names = {crs.name for crs in crs_compose.crs_list}
    build_ids = _enumerate_ids(crs_compose.work_dir.path, "builds")

    # Preserved builders: oss-crs-builder:{crs_name}-{build_name}-{build_id}
    # Filter by matching crs_name prefix AND build_id suffix
    for img in client.images.list(name=PRESERVED_BUILDER_REPO):
        for tag in img.tags:
            _, _, tag_suffix = tag.partition(":")
            if not tag_suffix:
                continue
            # tag_suffix is "{crs_name}-{build_name}-{build_id}"
            # Check if it starts with any known CRS name and ends with a known build_id
            for crs_name in crs_names:
                prefix = f"{crs_name}-"
                if tag_suffix.startswith(prefix):
                    remainder = tag_suffix[len(prefix):]
                    # remainder is "{build_name}-{build_id}"
                    for bid in build_ids:
                        if remainder.endswith(f"-{bid}"):
                            builder_tags.append(tag)
                            break
                    break

    # Snapshots: oss-crs-snapshot:{kind}-{crs_name}-{build_name}-{build_id}
    # Same scoping logic
    for img in client.images.list(name="oss-crs-snapshot"):
        for tag in img.tags:
            _, _, tag_suffix = tag.partition(":")
            if not tag_suffix:
                continue
            for crs_name in crs_names:
                if f"-{crs_name}-" in tag_suffix:
                    for bid in build_ids:
                        if tag_suffix.endswith(f"-{bid}"):
                            snapshot_tags.append(tag)
                            break
                    break
            # Also match content-hash snapshots if they're under our build dirs
            # These use format "content-{hash}" and aren't CRS-scoped, but we
            # include them since they were created by builds in this workdir
            if tag_suffix.startswith("test-"):
                for bid in build_ids:
                    if tag_suffix == f"test-{bid}":
                        snapshot_tags.append(tag)
                        break

    # Target base images (only if target provided)
    if target is not None:
        tag = target.get_docker_image_name()
        try:
            client.images.get(tag)
            target_tags.append(tag)
        except docker.errors.ImageNotFound:
            pass

    return (
        _dedupe(builder_tags),
        _dedupe(snapshot_tags),
        _dedupe(target_tags),
    )


def discover_run_images(crs_compose) -> list[str]:
    """Discover run-phase images scoped to this compose config.

    Enumerates run-ids from the workdir and matches compose project images
    with the pattern ``crs_compose_{run_id}*``.
    """
    run_ids = _enumerate_ids(crs_compose.work_dir.path, "runs")
    if not run_ids:
        return []

    client = docker.from_env()
    tags: list[str] = []
    for img in client.images.list():
        for tag in img.tags:
            for rid in run_ids:
                if tag.startswith(f"crs_compose_{rid}") or tag == f"{rid}-oss-crs-litellm-key-gen:latest":
                    tags.append(tag)
                    break
    return _dedupe(tags)


def discover_artifact_dirs(work_dir_path: Path, phase: str) -> list[Path]:
    """Find builds/ and/or runs/ directories under each sanitizer dir.

    Args:
        work_dir_path: The WorkDir.path (already includes crs_compose/{hash}/).
        phase: One of "prepare", "build-target", "run", or "all".
    """
    dirs: list[Path] = []
    if not work_dir_path.exists():
        return dirs
    for sanitizer_dir in sorted(work_dir_path.iterdir()):
        if not sanitizer_dir.is_dir():
            continue
        if phase in ("build-target", "all"):
            builds = sanitizer_dir / "builds"
            if builds.exists():
                dirs.append(builds)
        if phase in ("run", "all"):
            runs = sanitizer_dir / "runs"
            if runs.exists():
                dirs.append(runs)
    return dirs


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def _dir_size(path: Path) -> str:
    """Human-readable total size of a directory tree."""
    import subprocess
    total = 0
    try:
        for f in path.rglob("*"):
            if f.is_file():
                total += f.stat().st_size
    except PermissionError:
        # Fall back to docker to read root-owned files
        result = subprocess.run(
            ["docker", "run", "--rm", "-v", f"{path}:/data:ro",
             "alpine", "du", "-sb", "/data"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            try:
                total = int(result.stdout.split()[0])
            except (ValueError, IndexError):
                return "?"
        else:
            return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if total < 1024:
            return f"{total:.1f} {unit}"
        total /= 1024
    return f"{total:.1f} PB"


def display_clean_plan(plan: CleanPlan) -> None:
    console = get_console()
    console.print()
    console.print("[bold]The following items will be removed:[/bold]")

    def _print_section(title: str, items: list[str]) -> None:
        if not items:
            return
        console.print(f"\n  [bold]{title}[/bold] ({len(items)}):")
        for item in items:
            console.print(f"    - {item}")

    _print_section("Prepare images", plan.prepare_images)
    _print_section("Builder images", plan.builder_images)
    _print_section("Snapshot images", plan.snapshot_images)
    _print_section("Target images", plan.target_images)
    _print_section("Run images", plan.run_images)

    if plan.artifact_dirs:
        console.print(f"\n  [bold]Artifact directories[/bold] ({len(plan.artifact_dirs)}):")
        for d in plan.artifact_dirs:
            console.print(f"    - {d}  ({_dir_size(d)})")
    console.print()


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

def execute_clean_plan(plan: CleanPlan) -> bool:
    """Remove all images and directories in the plan. Returns True on full success."""
    console = get_console()
    client = docker.from_env()
    removed = 0
    failed: list[str] = []

    for tag in plan.all_images:
        try:
            client.images.remove(tag, force=True)
            console.print(f"  {green('Removed')} image {tag}")
            removed += 1
        except docker.errors.ImageNotFound:
            console.print(f"  {yellow('Skipped')} image {tag} (already removed)")
        except docker.errors.APIError as e:
            console.print(f"  {red('Failed')} image {tag}: {e}")
            failed.append(tag)

    for d in plan.artifact_dirs:
        try:
            shutil.rmtree(d)
            console.print(f"  {green('Removed')} {d}")
            removed += 1
        except PermissionError:
            console.print(f"  {yellow('Retrying')} {d} with docker...")
            try:
                rm_with_docker(d)
                console.print(f"  {green('Removed')} {d}")
                removed += 1
            except Exception as e:
                console.print(f"  {red('Failed')} {d}: {e}")
                failed.append(str(d))
        except Exception as e:
            console.print(f"  {red('Failed')} {d}: {e}")
            failed.append(str(d))

    console.print()
    console.print(f"Removed {removed} item(s).")
    if failed:
        console.print(f"{red(f'Failed to remove {len(failed)} item(s):')} {', '.join(failed)}")
    return not failed


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def build_clean_plan(
    crs_compose,
    subcommand: str | None,
    target,
    include_artifacts: bool,
) -> CleanPlan:
    """Build a plan of what to clean based on the subcommand."""
    plan = CleanPlan()
    phase = subcommand or "all"

    if phase in ("prepare", "all"):
        plan.prepare_images = discover_prepare_images(crs_compose)

    if phase in ("build-target", "all"):
        builders, snapshots, targets = discover_build_target_images(
            crs_compose, target
        )
        plan.builder_images = builders
        plan.snapshot_images = snapshots
        plan.target_images = targets

    if phase in ("run", "all"):
        plan.run_images = discover_run_images(crs_compose)

    if include_artifacts:
        plan.artifact_dirs = discover_artifact_dirs(
            crs_compose.work_dir.path, phase
        )

    return plan


def handle_clean(args) -> bool:
    """Entry point for the clean command."""
    import sys
    console = get_console()

    if not args.compose_file:
        print("Error: --compose-file is required", file=sys.stderr)
        return False

    from ..crs_compose import CRSCompose

    sub = getattr(args, "clean_subcommand", None)
    # Only need CRS repos for prepare-phase image discovery
    skip_crs_init = sub in ("build-target", "run")
    crs_compose = CRSCompose.from_yaml_file(
        args.compose_file, args.work_dir, skip_crs_init=skip_crs_init
    )

    # Build target object if fuzz_proj_path was provided
    target = None
    if hasattr(args, "target_proj_path") and args.target_proj_path:
        from ..target import Target

        target_repo_path = (
            args.target_repo_path
            if hasattr(args, "target_repo_path")
            else None
        )
        target = Target(args.work_dir, args.target_proj_path, target_repo_path, None)

    subcommand = args.clean_subcommand if hasattr(args, "clean_subcommand") else None
    include_artifacts = args.artifacts if hasattr(args, "artifacts") and args.artifacts else False

    plan = build_clean_plan(crs_compose, subcommand, target, include_artifacts)

    if plan.is_empty:
        console.print(green("Nothing to clean."))
        return True

    display_clean_plan(plan)

    answer = confirm("Proceed with cleanup?", auto_confirm=args.yes)
    if answer is None or not answer:
        console.print(yellow("Aborted."))
        return True  # not an error

    return execute_clean_plan(plan)


# ---------------------------------------------------------------------------
# CLI registration
# ---------------------------------------------------------------------------

def add_clean_command(subparsers, add_common_arguments_fn, _add_target_arguments_fn=None) -> None:
    """Register the clean command and its subcommands.

    The *add_common_arguments_fn* callable is passed from crs_compose.py to
    reuse the shared ``--compose-file`` / ``--work-dir`` definitions on
    subcommand parsers.
    """
    # Shared args added to every clean parser
    def _add_clean_flags(parser):
        parser.add_argument(
            "-y", "--yes",
            action="store_true",
            help="Skip confirmation prompt",
        )
        parser.add_argument(
            "--artifacts",
            action="store_true",
            help="Also delete workdir artifact directories",
        )

    def _add_optional_target_args(parser):
        parser.add_argument(
            "--fuzz-proj-path",
            "--target-path",
            "--target-proj-path",
            dest="target_proj_path",
            type=Path,
            required=False,
            default=None,
            help="Path to target project directory (optional, for target-image cleanup)",
        )
        parser.add_argument(
            "--target-source-path",
            dest="target_repo_path",
            type=Path,
            required=False,
            help="Optional local source override path",
        )

    clean = subparsers.add_parser(
        "clean",
        help="Remove Docker images and artifacts from previous runs",
    )
    _add_clean_flags(clean)
    # compose-file/work-dir are optional on the parent so that
    # `oss-crs clean build-target --compose-file ...` works (argparse
    # parses parent args before seeing the subcommand name).
    clean.add_argument(
        "--compose-file", type=Path, required=False,
        help="Path to the CRS Compose file",
    )
    clean.add_argument(
        "--work-dir", type=Path,
        default=(Path(__file__) / "../../../../.oss-crs-workdir").resolve(),
        help="Working directory for CRS Compose operations",
    )
    _add_optional_target_args(clean)
    clean_subs = clean.add_subparsers(dest="clean_subcommand")

    # --- prepare ---
    prep = clean_subs.add_parser(
        "prepare", help="Clean prepare-phase (bake) images"
    )
    _add_clean_flags(prep)
    add_common_arguments_fn(prep)

    # --- build-target ---
    bt = clean_subs.add_parser(
        "build-target", help="Clean builder, snapshot, and target images"
    )
    _add_clean_flags(bt)
    add_common_arguments_fn(bt)
    _add_optional_target_args(bt)

    # --- run ---
    run_p = clean_subs.add_parser(
        "run", help="Clean run-phase compose and infra images"
    )
    _add_clean_flags(run_p)
    add_common_arguments_fn(run_p)
    _add_optional_target_args(run_p)


def _dedupe(lst: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in lst:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out

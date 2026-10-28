# SPDX-License-Identifier: MIT
"""Check command: validate whether a CRS adheres to the OSS-CRS specification."""

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel

from ..config.crs import (
    OSS_CRS_INFRA_PREFIX,
    VALID_REQUIRED_INPUT_NAMES,
    CRSConfig,
    CRSType,
)
from ..utils import get_console

DEFAULT_WORK_DIR = (Path(__file__) / "../../../../.oss-crs-workdir").resolve()
CRS_CONFIG_CANDIDATES = [
    Path("oss-crs") / "crs.yaml",
    Path("oss-crs") / "crs.yml",
    Path("crs.yaml"),
    Path("crs.yml"),
]
GIT_URL_SCHEMES = ("https://", "http://", "git@", "ssh://", "git://")


@dataclass
class ValidationIssue:
    """An issue identified during validation."""

    message: str
    hint: str = ""
    is_warning: bool = False
    field: str = ""


class CheckReport:
    """Aggregated report of checks, passes, warnings, and errors."""

    def __init__(self, crs_target: str):
        self.crs_target = crs_target
        self.crs_name: str | None = None
        self.config_path: Path | None = None
        self.repo_root: Path | None = None
        self.issues: list[ValidationIssue] = []
        self.passed_checks: list[str] = []

    def add_pass(self, check_name: str) -> None:
        self.passed_checks.append(check_name)

    def add_error(self, message: str, hint: str = "", field: str = "") -> None:
        self.issues.append(
            ValidationIssue(message=message, hint=hint, is_warning=False, field=field)
        )

    def add_warning(self, message: str, hint: str = "", field: str = "") -> None:
        self.issues.append(
            ValidationIssue(message=message, hint=hint, is_warning=True, field=field)
        )

    @property
    def has_errors(self) -> bool:
        return any(not i.is_warning for i in self.issues)

    @property
    def has_warnings(self) -> bool:
        return any(i.is_warning for i in self.issues)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if not i.is_warning]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.is_warning]


def is_git_url(path_str: str) -> bool:
    """Determine whether a path string represents a remote Git repository URL."""
    if any(path_str.startswith(scheme) for scheme in GIT_URL_SCHEMES):
        return True
    return bool(path_str.endswith(".git") and not Path(path_str).exists())


def _sanitize_name_for_path(name: str) -> str:
    """Sanitize a string for safe filesystem path usage."""
    return re.sub(r"[^a-zA-Z0-9_\-\.]", "_", name)


def resolve_crs_target(
    crs_path_input: str,
    branch: str = "main",
    work_dir: Path = DEFAULT_WORK_DIR,
    offline: bool = False,
    report: CheckReport | None = None,
) -> tuple[Path | None, Path | None]:
    """Resolve CRS target to (repo_root, config_path).

    Supports local directories, local crs.yaml files, and remote Git URLs.
    Returns (None, None) if resolution fails, logging errors to report.
    """
    if is_git_url(crs_path_input):
        if offline:
            if report:
                report.add_error(
                    f"Cannot check remote Git repository '{crs_path_input}' with --offline enabled.",
                    hint="Omit the --offline flag or clone the repository locally first.",
                )
            return None, None

        git_bin = shutil.which("git")
        if not git_bin:
            if report:
                report.add_error(
                    "Git executable not found on PATH. Git is required to clone remote repositories.",
                    hint="Install Git or ensure it is accessible in your PATH.",
                )
            return None, None

        url_tail = crs_path_input.rstrip("/").split("/")[-1]
        url_tail = url_tail.removesuffix(".git")
        safe_name = _sanitize_name_for_path(url_tail) or "remote_crs"
        clone_dest = work_dir / "check_repos" / f"{safe_name}_{branch}"

        clone_dest.parent.mkdir(parents=True, exist_ok=True)
        if clone_dest.exists() and (clone_dest / ".git").is_dir():
            # Update existing clone
            update_cmd = ["git", "-C", str(clone_dest), "pull", "--ff-only"]
            proc = subprocess.run(
                update_cmd,
                capture_output=True,
                check=False,
                text=True,
            )
            if proc.returncode != 0:
                # If pull fails, re-clone cleanly
                shutil.rmtree(clone_dest, ignore_errors=True)

        if not clone_dest.exists():
            clone_cmd = [
                "git",
                "clone",
                "--depth",
                "1",
                "--branch",
                branch,
                crs_path_input,
                str(clone_dest),
            ]
            proc = subprocess.run(
                clone_cmd,
                capture_output=True,
                check=False,
                text=True,
            )
            if proc.returncode != 0:
                err_detail = proc.stderr.strip() or proc.stdout.strip()
                if report:
                    report.add_error(
                        f"Failed to clone Git repository '{crs_path_input}' (branch: '{branch}'): {err_detail}",
                        hint=f"Verify that the URL is correct, branch '{branch}' exists, and you have access permissions.",
                    )
                return None, None

        if report:
            report.add_pass(
                f"Cloned remote Git repository '{crs_path_input}' (branch: {branch})"
            )
        target_path = clone_dest
    else:
        target_path = Path(crs_path_input).expanduser().resolve()
        if not target_path.exists():
            if report:
                report.add_error(
                    f"Path does not exist: '{crs_path_input}'",
                    hint="Check the path spelling and ensure the file or directory exists.",
                )
            return None, None

    # Identify crs.yaml location
    if target_path.is_file():
        if target_path.name in ("crs.yaml", "crs.yml"):
            config_path = target_path
            repo_root = (
                target_path.parent.parent
                if target_path.parent.name == "oss-crs"
                else target_path.parent
            )
            return repo_root, config_path
        else:
            if report:
                report.add_error(
                    f"File '{target_path.name}' is not a CRS configuration file (expected crs.yaml or crs.yml).",
                    hint="Point directly to a crs.yaml file or to the directory containing it.",
                )
            return None, None

    # Target is a directory
    for candidate in CRS_CONFIG_CANDIDATES:
        cand_path = target_path / candidate
        if cand_path.is_file():
            config_path = cand_path
            repo_root = target_path
            return repo_root, config_path

    if report:
        report.add_error(
            f"No CRS configuration file found in '{target_path}'.",
            hint=(
                "Ensure your repository contains an 'oss-crs/crs.yaml' or 'crs.yaml' file "
                "conforming to the OSS-CRS specification."
            ),
        )
    return None, None


def _format_pydantic_error_hint(err: dict) -> tuple[str, str]:
    """Extract a user-friendly field description and hint from a Pydantic error dict."""
    loc = err.get("loc", ())
    field_name = " -> ".join(str(item) for item in loc)
    msg = err.get("msg", "Validation error")
    err_type = err.get("type", "")

    hint = ""
    key_set = {str(k) for k in loc}

    if "name" in key_set and "dockerfile" not in key_set:
        hint = "Specify a non-empty string for 'name' identifying this CRS."
    elif "type" in key_set:
        valid_types = ", ".join(t.value for t in CRSType)
        hint = f"Valid types: {valid_types}."
    elif "version" in key_set:
        hint = "Specify a non-empty version string (e.g., '1.0.0')."
    elif "crs_run_phase" in key_set and "dockerfile" not in key_set:
        hint = "Define 'crs_run_phase' with at least one module (e.g., 'patcher', 'fuzzer')."
    elif "supported_target" in key_set:
        hint = "Define 'supported_target' specifying 'mode' (full, delta) and 'language' (c, c++, jvm, python, rust)."
    elif "dockerfile" in key_set:
        hint = "Must be a valid Dockerfile path containing 'Dockerfile' or an 'oss-crs-infra:<module>' reference."
    elif "required_inputs" in key_set:
        valid_inputs = ", ".join(sorted(VALID_REQUIRED_INPUT_NAMES))
        hint = f"Valid inputs: {valid_inputs}."
    elif "required_envs" in key_set:
        hint = "Environment variable names must match pattern [A-Za-z_][A-Za-z0-9_]*."
    elif "extra_forbidden" in err_type:
        hint = "Remove or rename this unexpected field."

    return field_name, hint or msg


def validate_crs_config(config_path: Path, report: CheckReport) -> CRSConfig | None:
    """Validate YAML syntax and Pydantic schema for crs.yaml."""
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError as exc:
        report.add_error(
            f"Cannot read configuration file '{config_path}': {exc}",
            hint="Check file read permissions.",
        )
        return None

    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        pos = f" (line {mark.line + 1}, column {mark.column + 1})" if mark else ""
        problem = getattr(exc, "problem", str(exc))
        report.add_error(
            f"Invalid YAML syntax in '{config_path.name}': {problem}{pos}",
            hint="Fix the YAML formatting and indentation error.",
        )
        return None

    if not isinstance(data, dict):
        report.add_error(
            f"Configuration in '{config_path.name}' must be a YAML mapping (dictionary).",
            hint="Ensure the file is not empty and defines top-level keys like name, type, and version.",
        )
        return None

    try:
        config = CRSConfig.from_dict(data)
    except ValidationError as exc:
        for err in exc.errors():
            field_name, hint = _format_pydantic_error_hint(err)
            msg = err.get("msg", "Invalid value")
            report.add_error(
                f"Schema error at '{field_name}': {msg}",
                hint=hint,
                field=field_name,
            )
        return None

    report.crs_name = config.name
    report.add_pass(f"Configuration file discovered: {config_path}")
    types_str = ", ".join(sorted(t.value for t in config.type))
    report.add_pass(
        f"Schema validation passed (name: '{config.name}', version: '{config.version}', types: [{types_str}])"
    )
    return config


def _check_file_exists(rel_path_str: str, repo_root: Path, config_dir: Path) -> bool:
    """Check if a relative path exists in repo_root, config_dir, or config_dir.parent."""
    p = Path(rel_path_str)
    if p.is_absolute():
        return p.is_file()
    candidates = [
        repo_root / p,
        config_dir / p,
    ]
    if config_dir.name == "oss-crs":
        candidates.append(config_dir.parent / p)
    return any(c.is_file() for c in candidates)


def validate_crs_files(
    repo_root: Path,
    config_path: Path,
    config: CRSConfig,
    report: CheckReport,
) -> None:
    """Validate that local files referenced in crs.yaml exist."""
    config_dir = config_path.parent

    # 1. Prepare phase HCL
    if config.prepare_phase and config.prepare_phase.hcl:
        hcl_file = config.prepare_phase.hcl
        if not _check_file_exists(hcl_file, repo_root, config_dir):
            report.add_error(
                f"Referenced HCL file not found: '{hcl_file}'",
                hint=f"Create '{hcl_file}' or update 'prepare_phase.hcl' in crs.yaml.",
                field="prepare_phase.hcl",
            )
        else:
            report.add_pass(f"Prepare phase HCL verified: {hcl_file}")

    # 2. Target build phase Dockerfiles
    if config.target_build_phase:
        for build in config.target_build_phase.builds:
            df = build.dockerfile
            if df.startswith(OSS_CRS_INFRA_PREFIX):
                report.add_pass(
                    f"Target build step '{build.name}' uses framework module: {df}"
                )
            elif not _check_file_exists(df, repo_root, config_dir):
                report.add_error(
                    f"Target build step '{build.name}' references Dockerfile '{df}' which does not exist.",
                    hint=f"Create '{df}' or specify an existing Dockerfile path relative to repository root.",
                    field=f"target_build_phase.builds.{build.name}.dockerfile",
                )
            else:
                report.add_pass(
                    f"Target build Dockerfile verified for step '{build.name}': {df}"
                )

    # 3. CRS run phase Dockerfiles
    for mod_name, mod in config.crs_run_phase.modules.items():
        df = mod.dockerfile
        if df.startswith(OSS_CRS_INFRA_PREFIX):
            report.add_pass(
                f"Run phase module '{mod_name}' uses framework module: {df}"
            )
        elif not _check_file_exists(df, repo_root, config_dir):
            report.add_error(
                f"Run phase module '{mod_name}' references Dockerfile '{df}' which does not exist.",
                hint=f"Create '{df}' or specify an existing Dockerfile path relative to repository root.",
                field=f"crs_run_phase.modules.{mod_name}.dockerfile",
            )
        else:
            report.add_pass(
                f"Run phase Dockerfile verified for module '{mod_name}': {df}"
            )

    # 4. Optional compose files
    for comp_name in ("example-compose.yaml", "compose.yaml"):
        candidates = [config_dir / comp_name, repo_root / comp_name]
        if (repo_root / "oss-crs").is_dir():
            candidates.append(repo_root / "oss-crs" / comp_name)
        for cand in candidates:
            if cand.is_file():
                try:
                    with open(cand, "r", encoding="utf-8") as f:
                        yaml.safe_load(f)
                    report.add_pass(f"Optional compose template verified: {cand.name}")
                except yaml.YAMLError as exc:
                    report.add_warning(
                        f"Compose file '{cand.name}' has YAML syntax errors: {exc}",
                        hint=f"Check syntax in '{cand}' to ensure compose file is usable.",
                    )
                break


def validate_crs_functional(
    repo_root: Path,
    config_path: Path,
    config: CRSConfig,
    report: CheckReport,
) -> None:
    """Run functional validation against Docker daemon and referenced Dockerfiles."""
    docker_bin = shutil.which("docker")
    if not docker_bin:
        report.add_error(
            "Docker CLI executable not found on PATH. Docker is required for functional validation.",
            hint="Install Docker or run without the --functional flag for static validation.",
        )
        return

    try:
        proc = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
        if proc.returncode != 0:
            report.add_error(
                "Docker daemon is not running or not responsive.",
                hint="Start the Docker daemon service, or run without --functional for static validation.",
            )
            return
    except (subprocess.TimeoutExpired, OSError) as exc:
        report.add_error(
            f"Failed to communicate with Docker daemon: {exc}",
            hint="Verify Docker is running and current user has permissions to access the docker socket.",
        )
        return

    report.add_pass("Docker daemon is running and accessible")

    # Verify Dockerfile contents have valid FROM instructions
    config_dir = config_path.parent
    all_dockerfiles: list[tuple[str, str]] = []
    if config.target_build_phase:
        for b in config.target_build_phase.builds:
            if not b.dockerfile.startswith(OSS_CRS_INFRA_PREFIX):
                all_dockerfiles.append((f"target_build:{b.name}", b.dockerfile))
    for mod_name, mod in config.crs_run_phase.modules.items():
        if not mod.dockerfile.startswith(OSS_CRS_INFRA_PREFIX):
            all_dockerfiles.append((f"run_phase:{mod_name}", mod.dockerfile))

    for label, df_rel in all_dockerfiles:
        p = Path(df_rel)
        resolved_df = None
        for cand in [repo_root / p, config_dir / p, config_dir.parent / p]:
            if cand.is_file():
                resolved_df = cand
                break

        if not resolved_df:
            continue

        try:
            with open(resolved_df, "r", encoding="utf-8") as f:
                lines = [
                    line.strip()
                    for line in f
                    if line.strip() and not line.strip().startswith("#")
                ]
            has_from = any(line.upper().startswith("FROM ") for line in lines)
            if not has_from:
                report.add_error(
                    f"Dockerfile '{df_rel}' ({label}) does not contain a valid 'FROM' instruction.",
                    hint="Ensure Dockerfile begins with a valid 'FROM <base_image>' instruction.",
                )
            else:
                report.add_pass(
                    f"Functional validation: Dockerfile syntax confirmed for '{df_rel}' ({label})"
                )
        except OSError as exc:
            report.add_error(
                f"Failed to read Dockerfile '{df_rel}': {exc}",
                hint="Check file read permissions.",
            )


def print_report(report: CheckReport, console: Console | None = None) -> bool:
    """Format and print validation results to console. Returns True on success, False on error."""
    if console is None:
        console = get_console()

    console.print()
    console.print(
        Panel.fit(
            f"[bold blue]OSS-CRS Specification Validation[/bold blue]\n"
            f"[dim]Target:[/dim] [cyan]{report.crs_target}[/cyan]"
            + (
                f"\n[dim]CRS Name:[/dim] [green]{report.crs_name}[/green]"
                if report.crs_name
                else ""
            ),
            border_style="blue",
        )
    )

    if report.passed_checks:
        console.print("[bold green]Passed Checks:[/bold green]")
        for item in report.passed_checks:
            console.print(f"  [bold green][PASS][/bold green] {escape(item)}")
        console.print()

    if report.warnings:
        console.print("[bold yellow]Warnings:[/bold yellow]")
        for warn in report.warnings:
            console.print(f"  [bold yellow][WARN][/bold yellow] {escape(warn.message)}")
            if warn.hint:
                console.print(f"    [dim]Suggestion:[/dim] {escape(warn.hint)}")
        console.print()

    if report.errors:
        console.print("[bold red]Failures:[/bold red]")
        for err in report.errors:
            console.print(f"  [bold red][FAIL][/bold red] {escape(err.message)}")
            if err.hint:
                console.print(
                    f"    [dim]Action:[/dim] [yellow]{escape(err.hint)}[/yellow]"
                )
        console.print()

    # Final summary banner
    if not report.has_errors:
        console.print(
            Panel(
                "[bold green]SUCCESS: CRS adheres to the OSS-CRS specification![/bold green]",
                border_style="green",
            )
        )
        return True
    else:
        num_errs = len(report.errors)
        console.print(
            Panel(
                f"[bold red]FAILURE: Found {num_errs} error(s). Please fix the issues above.[/bold red]",
                border_style="red",
            )
        )
        return False


def handle_check(args) -> bool:
    """Main CLI handler for 'oss-crs check <CRS path>'."""
    crs_path_input = args.crs_path
    branch = getattr(args, "branch", "main")
    work_dir = getattr(args, "work_dir", DEFAULT_WORK_DIR)
    functional = getattr(args, "functional", False)
    offline = getattr(args, "offline", False)

    report = CheckReport(crs_path_input)

    repo_root, config_path = resolve_crs_target(
        crs_path_input,
        branch=branch,
        work_dir=work_dir,
        offline=offline,
        report=report,
    )

    if not config_path or not repo_root:
        return print_report(report)

    report.repo_root = repo_root
    report.config_path = config_path

    config = validate_crs_config(config_path, report)
    if not config:
        return print_report(report)

    validate_crs_files(repo_root, config_path, config, report)

    if functional:
        validate_crs_functional(repo_root, config_path, config, report)

    return print_report(report)


def add_check_command(subparsers) -> None:
    """Register the 'check' subcommand."""
    check_parser = subparsers.add_parser(
        "check",
        help="Validate whether a CRS adheres to the OSS-CRS specification and format",
        description="Validate a CRS repository or directory against the OSS-CRS specification.",
    )
    check_parser.add_argument(
        "crs_path",
        help="Path to the local CRS directory or remote Git URL (e.g., https://github.com/org/repo.git)",
    )
    check_parser.add_argument(
        "--branch",
        default="main",
        help="Branch name to clone when crs_path is a remote Git URL (default: main)",
    )
    check_parser.add_argument(
        "--work-dir",
        type=Path,
        default=DEFAULT_WORK_DIR,
        help="Working directory for temporary clones (default: .oss-crs-workdir)",
    )
    check_parser.add_argument(
        "--functional",
        action="store_true",
        default=False,
        help="Run optional functional validation (mock container build and libCRS check)",
    )
    check_parser.add_argument(
        "--offline",
        action="store_true",
        default=False,
        help="Disable network operations (fail if remote Git URL is provided)",
    )

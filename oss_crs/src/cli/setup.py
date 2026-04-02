"""Setup command for oss-crs cgroup-parent mode.

This module implements the `oss-crs setup` command which configures
the host system for cgroup-parent based resource management.
"""

import subprocess
from dataclasses import dataclass
from typing import Callable, Optional
from rich.panel import Panel

from ..utils import get_console, configure_logging, confirm, green, red, yellow
from ..cgroup import (
    check_docker_cgroup_driver,
    check_cgroup_delegation,
    check_oss_crs_directory,
    check_oss_crs_controllers,
    enable_oss_crs_controllers,
    generate_docker_config_commands,
    generate_cgroup_setup_commands,
    get_user_cgroup_base,
    get_user_service_cgroup,
)


# =============================================================================
# Data Classes
# =============================================================================


@dataclass
class CheckResult:
    """Result of a requirement check."""

    ok: bool
    detail: str = ""
    needs_fix: bool = False


@dataclass
class SetupStep:
    """A setup step that can be executed to fix a requirement."""

    title: str
    description: str
    commands: Callable[
        [], list[tuple[str, str]]
    ]  # Returns [(description, command), ...]
    verify: Optional[Callable[[], bool]] = None
    skip_message: str = ""


# =============================================================================
# Requirement Checks
# =============================================================================


def check_docker_driver() -> CheckResult:
    """Check if Docker is using cgroupfs driver."""
    is_cgroupfs, driver = check_docker_cgroup_driver()

    if driver == "docker_not_found":
        return CheckResult(ok=False, detail="Docker not found in PATH")
    elif driver == "timeout":
        return CheckResult(ok=False, detail="Docker daemon not responding")
    elif is_cgroupfs:
        return CheckResult(ok=True, detail="cgroupfs")
    else:
        return CheckResult(
            ok=False, detail=f"using '{driver}', needs 'cgroupfs'", needs_fix=True
        )


def check_delegation() -> CheckResult:
    """Check if cgroup v2 delegation is configured."""
    delegation_ok, missing = check_cgroup_delegation()

    if delegation_ok:
        return CheckResult(ok=True, detail="cpuset, memory enabled")
    else:
        detail = f"missing: {', '.join(missing)}" if missing else "not configured"
        return CheckResult(ok=False, detail=detail, needs_fix=True)


def check_directory() -> CheckResult:
    """Check if oss-crs cgroup directory exists and is writable."""
    dir_ok, status = check_oss_crs_directory()

    if dir_ok:
        return CheckResult(ok=True, detail=str(get_user_cgroup_base()))
    else:
        detail = {
            "not_exists": "directory does not exist",
            "permission_denied": "permission denied",
        }.get(status, status)
        return CheckResult(ok=False, detail=detail, needs_fix=True)


def check_controllers() -> CheckResult:
    """Check if controllers are enabled at oss-crs level."""
    controllers_ok, missing = check_oss_crs_controllers()

    if controllers_ok:
        return CheckResult(ok=True, detail="cpuset, memory enabled")
    else:
        detail = f"missing: {', '.join(missing)}" if missing else "not enabled"
        return CheckResult(ok=False, detail=detail, needs_fix=True)


# =============================================================================
# Setup Steps
# =============================================================================


def docker_setup_step() -> SetupStep:
    """Create the Docker configuration setup step."""
    return SetupStep(
        title="Configure Docker to use cgroupfs driver",
        description="""
Docker must use the [bold]cgroupfs[/bold] cgroup driver instead of [bold]systemd[/bold].
This change requires modifying /etc/docker/daemon.json and restarting Docker.

[yellow]Warning:[/yellow] This will restart Docker daemon. Running containers will be stopped.
""",
        commands=generate_docker_config_commands,
        verify=lambda: check_docker_cgroup_driver()[0],
        skip_message="""You can configure Docker manually by adding to /etc/docker/daemon.json:
[dim]{"exec-opts": ["native.cgroupdriver=cgroupfs"]}[/dim]""",
    )


def cgroup_setup_step() -> SetupStep:
    """Create the cgroup delegation setup step."""
    user_service = get_user_service_cgroup()
    oss_crs_path = get_user_cgroup_base()

    return SetupStep(
        title="Set up cgroup v2 delegation",
        description=f"""
Cgroup v2 requires proper delegation to allow non-root users to manage resources.
This will:
1. Enable cpuset and memory controllers at [dim]{user_service}[/dim]
2. Create the oss-crs directory at [dim]{oss_crs_path}[/dim]
3. Set ownership so you can manage cgroups without sudo
""",
        commands=generate_cgroup_setup_commands,
    )


def controller_setup_step() -> SetupStep:
    """Create the controller enable setup step."""
    oss_crs_path = get_user_cgroup_base()

    return SetupStep(
        title="Enable controllers in oss-crs cgroup",
        description="Enabling cpuset and memory controllers at oss-crs level...",
        commands=lambda: [
            (
                "Enable controllers at oss-crs level",
                f'echo "+cpuset +memory" | sudo tee {oss_crs_path}/cgroup.subtree_control',
            )
        ],
    )


# =============================================================================
# Setup Runner
# =============================================================================

# Define all requirements in order
REQUIREMENTS: list[tuple[str, Callable[[], CheckResult], str]] = [
    ("Docker cgroup driver", check_docker_driver, "docker"),
    ("Cgroup v2 delegation", check_delegation, "delegation"),
    ("oss-crs cgroup directory", check_directory, "directory"),
    ("oss-crs controllers", check_controllers, "controllers"),
]


class SetupRunner:
    """Runs the interactive setup process for cgroup-parent mode."""

    def __init__(self, yes: bool = False):
        configure_logging(quiet=yes)
        self.console = get_console()
        self.yes = yes
        self.step_num = 0
        self.results: dict[str, CheckResult] = {}

    def print_status(self, label: str, ok: bool, detail: str = "") -> None:
        """Print a status line with checkmark or cross."""
        icon = green("OK") if ok else red("X")
        detail_text = f" - {detail}" if detail else ""
        self.console.print(f"  [{icon}] {label}{detail_text}")

    def run_command(self, description: str, command: str) -> bool:
        """Run a command with user confirmation."""
        self.console.print(f"\n{yellow('Action:')} {description}")
        self.console.print(f"[dim]Command:[/dim] {command}")

        answer = confirm("Run this command?", auto_confirm=self.yes)
        if answer is None:
            self.console.print(yellow("Aborted by user"))
            return False
        if not answer:
            self.console.print(yellow("Skipped"))
            return False

        try:
            result = subprocess.run(
                command, shell=True, capture_output=True, text=True, timeout=120
            )
            if result.returncode == 0:
                self.console.print(green("Success"))
                return True
            else:
                self.console.print(f"{red('Failed:')} {result.stderr.strip()}")
                return False
        except subprocess.TimeoutExpired:
            self.console.print(red("Command timed out"))
            return False
        except Exception as e:
            self.console.print(f"{red('Error:')} {e}")
            return False

    def execute_step(self, step: SetupStep) -> bool:
        """Execute a setup step with user interaction."""
        self.step_num += 1
        self.console.print(
            f"\n[bold cyan]Step {self.step_num}:[/bold cyan] {step.title}"
        )
        self.console.print(step.description)

        proceed = confirm(f"Proceed with {step.title.lower()}?", auto_confirm=self.yes)
        if proceed is None:
            self.console.print(yellow("Aborted by user"))
            return False
        if not proceed:
            self.console.print(yellow("Skipped"))
            if step.skip_message:
                self.console.print(step.skip_message)
            return False

        # Run all commands for this step
        for desc, cmd in step.commands():
            if not self.run_command(desc, cmd):
                self.console.print(
                    yellow(f"{step.title} incomplete. Please complete manually.")
                )
                return False

        # Verify if verification function provided
        if step.verify and not step.verify():
            self.console.print(red(f"Verification failed for {step.title}"))
            return False

        return True

    def run_checks(self) -> dict[str, CheckResult]:
        """Run all requirement checks."""
        self.console.print("\n[bold]Checking requirements...[/bold]")
        self.results = {}

        for name, check_fn, key in REQUIREMENTS:
            # Skip controller check if directory doesn't exist
            if (
                key == "controllers"
                and not self.results.get("directory", CheckResult(ok=False)).ok
            ):
                continue

            result = check_fn()
            self.results[key] = result
            self.print_status(name, result.ok, result.detail)

        return self.results

    def all_ok(self) -> bool:
        """Check if all requirements are satisfied."""
        return all(r.ok for r in self.results.values())

    def needs_fix(self, key: str) -> bool:
        """Check if a specific requirement needs fixing."""
        return self.results.get(key, CheckResult(ok=True)).needs_fix

    def run(self, check_only: bool = False) -> bool:
        """Run the setup process."""
        self.console.print(
            Panel(
                "[bold]oss-crs setup[/bold]\n\n"
                "This command configures your system for cgroup-parent mode,\n"
                "which enables fine-grained CPU and memory resource control\n"
                "for CRS containers without Docker-in-Docker.",
                title="Cgroup-Parent Setup",
                border_style="blue",
            )
        )

        # Run initial checks
        self.run_checks()

        # Check for fatal errors (Docker not found/not running)
        docker_result = self.results.get("docker", CheckResult(ok=False))
        if not docker_result.ok and not docker_result.needs_fix:
            self.console.print(f"\n{red(f'Cannot continue: {docker_result.detail}')}")
            return False

        if self.all_ok():
            self.console.print(
                f"\n{green('All checks passed!', bold=True)} Your system is ready for cgroup-parent mode."
            )
            return True

        if check_only:
            self.console.print(
                f"\n{yellow('Some checks failed.')} Run [bold]oss-crs setup[/bold] to fix issues."
            )
            return False

        # Interactive setup
        self.console.print("\n[bold]Setup required[/bold]")
        self.console.print(
            "Some configuration is needed. The following changes require [bold]sudo[/bold] privileges."
        )
        self.console.print(
            "You will be asked to confirm each command before it runs.\n"
        )

        # Docker configuration
        if self.needs_fix("docker"):
            if not self.execute_step(docker_setup_step()):
                return False

        # Cgroup delegation and directory setup
        if self.needs_fix("delegation") or self.needs_fix("directory"):
            if not self.execute_step(cgroup_setup_step()):
                return False

        # Controller setup (only if directory exists but controllers not enabled)
        elif self.needs_fix("controllers"):
            # Try direct write first
            success, message = enable_oss_crs_controllers()
            if success:
                self.console.print(green(message))
            else:
                self.console.print(yellow(f"Direct write failed: {message}"))
                if not self.execute_step(controller_setup_step()):
                    return False

        # Final verification
        self.console.print("\n[bold]Verifying setup...[/bold]")
        self.run_checks()

        if self.all_ok():
            self.console.print(
                f"\n{green('Setup complete!', bold=True)} Your system is ready for cgroup-parent mode."
            )
            self.console.print(
                "\nCgroup-parent mode will be used automatically when available."
            )
        else:
            self.console.print(
                f"\n{yellow('Setup incomplete.')} Some checks are still failing."
            )
            self.console.print(
                "Please address the issues above and run [bold]oss-crs setup[/bold] again."
            )

        return self.all_ok()


# =============================================================================
# CLI Entry Points
# =============================================================================


def add_setup_command(subparsers) -> None:
    """Add the setup command to the CLI parser."""
    setup = subparsers.add_parser(
        "setup", help="Configure system for cgroup-parent resource management"
    )
    setup.add_argument(
        "--check",
        action="store_true",
        help="Only check status without making changes",
    )
    setup.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Automatically accept all prompts (non-interactive mode)",
    )


def handle_setup(args) -> bool:
    """Handle the setup command."""
    return SetupRunner(yes=args.yes).run(check_only=args.check)

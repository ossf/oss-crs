# SPDX-License-Identifier: MIT
"""Setup command for oss-crs.

This module implements the `oss-crs setup` command which configures
the host system for cgroup-parent based resource management and
optionally configures LLM proxy routing for a compose file.
"""

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import questionary
import yaml
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
from ..llm import LITELLM_PROVIDERS, apply_litellm_proxy_to_file, override_litellm_proxy


def _litellm_config_would_change(
    path: Path, key_env: str, base_url_env: str | None, providers: list[str]
) -> bool:
    """Return True if applying the proxy override would modify *path*."""
    with open(path) as f:
        original = yaml.safe_load(f) or {}
    return (
        override_litellm_proxy(original, key_env, base_url_env, providers) != original
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
    """Runs the interactive setup process."""

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
                "Configure your system for OSS-CRS:\n"
                "  1. LLM provider proxy routing (optional)\n"
                "  2. Cgroup resource management (CPU/memory isolation)",
                title="OSS-CRS Setup",
                border_style="blue",
            )
        )

        # --- Phase 1: LLM proxy configuration ---
        if not check_only:
            self._run_llm_proxy_setup()
            self.console.print("")

        # --- Phase 2: Cgroup setup ---
        return self._run_cgroup_setup(check_only)

    def _run_cgroup_setup(self, check_only: bool = False) -> bool:
        """Run cgroup checks and interactive fixes."""
        self.console.print("[bold]Phase 2: Cgroup resource management[/bold]")

        # Run initial checks
        self.run_checks()

        # Check for fatal errors (Docker not found/not running)
        docker_result = self.results.get("docker", CheckResult(ok=False))
        if not docker_result.ok and not docker_result.needs_fix:
            self.console.print(f"\n{red(f'Cannot continue: {docker_result.detail}')}")
            return False

        if self.all_ok():
            self.console.print(f"\n{green('Cgroup setup OK!', bold=True)}")
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
        self.console.print("\n[bold]Verifying cgroup setup...[/bold]")
        self.run_checks()

        if self.all_ok():
            self.console.print(f"\n{green('Cgroup setup complete!', bold=True)}")
        else:
            self.console.print(
                f"\n{yellow('Cgroup setup incomplete.')} Some checks are still failing."
            )
            self.console.print(
                "Please address the issues above and run [bold]oss-crs setup[/bold] again."
            )

        return self.all_ok()

    # -----------------------------------------------------------------
    # LLM proxy configuration
    # -----------------------------------------------------------------

    def _find_example_litellm_configs(self) -> list[Path]:
        """Find all example litellm config files."""
        example_dir = Path(__file__).resolve().parents[3] / "example"
        return sorted(example_dir.glob("*/litellm-config.yaml"))

    def _run_llm_proxy_setup(self) -> None:
        """Interactively configure LLM proxy routing for all example configs."""
        self.console.print("[bold]Phase 1: LLM proxy configuration[/bold]")
        self.console.print(
            "\nBy default, examples use standard provider API keys "
            "([bold]OPENAI_API_KEY[/bold], [bold]ANTHROPIC_API_KEY[/bold], "
            "[bold]GEMINI_API_KEY[/bold]).\n"
            "If you access LLM providers through a proxy (e.g. an external "
            "LiteLLM instance), you can override the env vars here."
        )

        want_proxy = confirm(
            "Do you want to configure a proxy for LLM providers?",
            default=False,
            auto_confirm=False,
        )
        if not want_proxy:
            self.console.print(green("Skipped") + " — using default provider API keys.")
            return

        configs = self._find_example_litellm_configs()
        if not configs:
            self.console.print(yellow("No example litellm configs found. Skipping."))
            return

        # Ask which providers to route through the proxy
        provider_choices = [
            questionary.Choice(
                title=f"{name} (default key: {info['default_key_env']})",
                value=name,
                checked=True,
            )
            for name, info in sorted(LITELLM_PROVIDERS.items())
        ]
        selected = questionary.checkbox(
            "Which providers should go through the proxy?",
            choices=provider_choices,
        ).ask()

        if not selected:
            self.console.print(yellow("No providers selected. Skipping."))
            return

        # Ask for proxy key env var
        key_env = questionary.text(
            "Proxy API key env var name:",
            default="EXTERNAL_LITELLM_API_KEY",
        ).ask()
        if not key_env:
            self.console.print(yellow("Aborted."))
            return

        # Ask for proxy base URL env var
        want_base = confirm(
            "Does your proxy require a custom base URL?",
            default=True,
            auto_confirm=False,
        )
        base_url_env = None
        if want_base:
            base_url_env = questionary.text(
                "Proxy base URL env var name:",
                default="EXTERNAL_LITELLM_API_BASE",
            ).ask()
            if not base_url_env:
                self.console.print(yellow("Aborted."))
                return

        # Pre-filter to configs that would actually change
        affected = [
            c
            for c in configs
            if _litellm_config_would_change(c, key_env, base_url_env, selected)
        ]

        if not affected:
            self.console.print(
                yellow(
                    "No example configs use the selected provider keys. Nothing to do."
                )
            )
            return

        # Show summary and confirm
        self.console.print("\n[bold]Proxy configuration summary:[/bold]")
        self.console.print(f"  Providers: {', '.join(selected)}")
        self.console.print(f"  API key env: [bold]{key_env}[/bold]")
        if base_url_env:
            self.console.print(f"  Base URL env: [bold]{base_url_env}[/bold]")
        self.console.print(
            f"  Config files: [bold]{len(affected)}[/bold] example litellm configs"
        )
        for c in affected:
            self.console.print(f"    [dim]{c.relative_to(c.parents[2])}[/dim]")

        proceed = confirm("\nApply this configuration?", auto_confirm=self.yes)
        if not proceed:
            self.console.print(yellow("Skipped."))
            return

        # Apply the override to affected configs
        for config_path in affected:
            apply_litellm_proxy_to_file(config_path, key_env, base_url_env, selected)

        self.console.print(
            green(f"Updated {len(affected)} litellm configs!", bold=True)
        )
        self.console.print(
            f"\nMake sure these env vars are set before running:\n"
            f"  export {key_env}=<your-proxy-key>"
        )
        if base_url_env:
            self.console.print(f"  export {base_url_env}=<your-proxy-url>")


# =============================================================================
# CLI Entry Points
# =============================================================================


def add_setup_command(subparsers) -> None:
    """Add the setup command to the CLI parser."""
    setup = subparsers.add_parser(
        "setup", help="Configure system and LLM provider settings"
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

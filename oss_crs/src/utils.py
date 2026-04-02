import hashlib
import shutil
import subprocess
import random
import string
import time
import re
from pathlib import Path
from typing import Optional

import questionary
from rich.console import Console


RAND_CHARS = string.ascii_lowercase + string.digits

# Global console instance for unified logging
_console: Console | None = None
_quiet: bool = False


def configure_logging(quiet: bool = False) -> None:
    """Configure global logging settings.

    Args:
        quiet: If True, suppress non-essential output.
    """
    global _quiet, _console
    _quiet = quiet
    # Reset console so it picks up new quiet setting
    _console = None


def get_console() -> Console:
    """Get the shared Console instance for unified logging.

    Returns:
        The global Console instance, configured with current quiet setting.
    """
    global _console
    if _console is None:
        _console = Console(quiet=_quiet)
    return _console


def log_info(message: str) -> None:
    """Log an informational message."""
    if not _quiet:
        get_console().print(message)


def log_success(message: str) -> None:
    """Log a success message in green."""
    if not _quiet:
        get_console().print(f"[green]{message}[/green]")


def log_warning(message: str) -> None:
    """Log a warning message in yellow."""
    get_console().print(f"[yellow]Warning:[/yellow] {message}")


def log_error(message: str) -> None:
    """Log an error message in red."""
    get_console().print(f"[bold red]Error:[/bold red] {message}")


def log_dim(message: str) -> None:
    """Log a dimmed/subtle message."""
    if not _quiet:
        get_console().print(f"[dim]{message}[/dim]")


def generate_random_name(length: int = 10) -> str:
    """Generate a random alphanumeric string."""
    return "".join(random.choice(RAND_CHARS) for _ in range(length))


def generate_run_id() -> str:
    """Generate a run ID from unix timestamp + 2 chars of randomness."""
    ts = int(time.time())
    suffix = "".join(random.choice(RAND_CHARS) for _ in range(2))
    return f"{ts}{suffix}"


def normalize_run_id(run_id: str) -> str:
    """Normalize run_id to filesystem-safe string, appending hash to avoid collisions."""
    normalized = run_id.strip().lower()
    normalized = re.sub(r"[^a-z0-9_-]+", "-", normalized)
    normalized = re.sub(r"-{2,}", "-", normalized).strip("-_")
    if not normalized:
        raise ValueError("run_id must contain at least one alphanumeric character")
    # Append short hash of original string to avoid collisions
    original_hash = hashlib.sha256(run_id.encode()).hexdigest()[:6]
    return f"{normalized}-{original_hash}"


class TmpDockerCompose:
    def __init__(
        self,
        progress,
        project_name_prefix: str = "proj",
        run_id: str | None = None,
        auto_cleanup: bool = True,
    ):
        self.progress = progress
        self._project_name_prefix = project_name_prefix
        self._requested_run_id = run_id
        self._auto_cleanup = auto_cleanup
        self.dir: Optional[Path] = None
        self.docker_compose: Optional[Path] = None
        self.project_name: Optional[str] = None
        self.run_id: Optional[str] = None

    def __enter__(self) -> "TmpDockerCompose":
        # Create a temporary docker-compose YAML file
        # Note: run_id should already be normalized by the caller (CLI)
        run_id = (
            self._requested_run_id
            if self._requested_run_id
            else generate_random_name(10)
        )
        tmp_name = generate_random_name(10)
        self.dir = Path(f"/tmp/{tmp_name}")
        self.dir.mkdir(parents=True, exist_ok=True)
        self.docker_compose = self.dir / "docker-compose.yaml"
        self.project_name = f"{self._project_name_prefix}_{run_id}"
        self.run_id = run_id
        self.docker_compose.touch()
        if self._auto_cleanup:
            self.progress.add_cleanup_task(
                "Cleanup Docker Compose",
                lambda progress: progress.docker_compose_down(
                    self.project_name, self.docker_compose
                ),
            )
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        if self.docker_compose is None or self.project_name is None:
            return
        # Clean up the temporary dir
        if self.dir is not None and self.dir.exists():
            shutil.rmtree(self.dir, ignore_errors=True)


PRESERVED_BUILDER_REPO = "oss-crs-builder"


def preserved_builder_image_name(crs_name: str, build_name: str, build_id: str) -> str:
    """Deterministic image name for a preserved builder image.

    Used by __build_target_one to save the builder image before compose cleanup,
    by _create_incremental_snapshots for snapshot creation, and by the sidecar
    compose template for BASE_IMAGE_* env vars.
    """
    return f"{PRESERVED_BUILDER_REPO}:{crs_name}-{build_name}-{build_id}"


def build_snapshot_tag(crs_name: str, build_name: str, build_id: str) -> str:
    """Full image tag for a builder snapshot.

    Format: oss-crs-snapshot:build-{crs_name}-{build_name}-{build_id}
    Used by _create_incremental_snapshots, _check_snapshots_exist, and the sidecar.
    """
    return f"oss-crs-snapshot:build-{crs_name}-{build_name}-{build_id}"


def rm_with_docker(path: Path) -> None:
    try:
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "-v",
                f"{path.parent}:/data",
                "alpine",
                "rm",
                "-rf",
                f"/data/{path.name}",
            ],
            stderr=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Error removing {path} with Docker: {e}")


# =============================================================================
# Text Styling Helpers
# =============================================================================


def bold(text: str) -> str:
    """Return bold markup string."""
    return f"[bold]{text}[/bold]"


def yellow(text: str, bold: bool = False) -> str:
    """Return yellow markup string, optionally bold."""
    style = "bold yellow" if bold else "yellow"
    return f"[{style}]{text}[/{style}]"


def green(text: str, bold: bool = False) -> str:
    """Return green markup string, optionally bold."""
    style = "bold green" if bold else "green"
    return f"[{style}]{text}[/{style}]"


def red(text: str, bold: bool = False) -> str:
    """Return red markup string, optionally bold."""
    style = "bold red" if bold else "red"
    return f"[{style}]{text}[/{style}]"


def confirm(
    message: str, default: bool = True, auto_confirm: bool = False
) -> bool | None:
    """Prompt user for yes/no confirmation.

    Args:
        message: The question to ask.
        default: Default value if user just presses enter.
        auto_confirm: If True, skip prompt and return True immediately.

    Returns:
        True if confirmed, False if declined, None if aborted (Ctrl+C).
    """
    if auto_confirm:
        return True
    return questionary.confirm(message, default=default).ask()


def select(message: str, choices: list[tuple[str, str]]) -> str | None:
    """Prompt user to select from a list of choices.

    Args:
        message: The question to ask.
        choices: List of (display_title, value) tuples.

    Returns:
        The selected value, or None if aborted (Ctrl+C).
    """
    q_choices = [
        questionary.Choice(title=title, value=value) for title, value in choices
    ]
    return questionary.select(message, choices=q_choices).ask()

# SPDX-License-Identifier: MIT
import hashlib
import shutil
import subprocess
import random
import secrets
import string
import time
import re
from pathlib import Path
from collections.abc import Sequence
from typing import Any, Callable, Optional, TypeVar, cast

import questionary
from prompt_toolkit.application import Application
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.styles import Style
from questionary.constants import (
    DEFAULT_QUESTION_PREFIX,
    DEFAULT_SELECTED_POINTER,
    INVALID_INPUT,
)
from questionary.prompts import common as questionary_common
from questionary.prompts.common import InquirerControl, Separator
from questionary.question import Question
from questionary.styles import merge_styles_default
from rich.console import Console

from .constants import PRESERVED_BUILDER_REPO


RAND_CHARS = string.ascii_lowercase + string.digits
T = TypeVar("T")

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


def generate_random_key(length: int = 10) -> str:
    """Generate a cryptographically secure random alphanumeric string."""
    return "".join(secrets.choice(RAND_CHARS) for _ in range(length))


def generate_run_id() -> str:
    """Generate a run ID from unix timestamp + 2 chars of randomness."""
    ts = int(time.time())
    suffix = "".join(random.choice(RAND_CHARS) for _ in range(2))
    return f"{ts}{suffix}"


def normalize_run_id(run_id: str) -> str:
    """Normalize run_id to filesystem-safe string, appending hash to avoid collisions.

    Idempotent: calling normalize_run_id on an already-normalized ID returns the
    same value. This is achieved by hashing only the *base* portion (before the
    last ``-<6hex>`` suffix) when the suffix already matches.
    """
    normalized = run_id.strip().lower()
    normalized = re.sub(r"[^a-z0-9_-]+", "-", normalized)
    normalized = re.sub(r"-{2,}", "-", normalized).strip("-_")
    if not normalized:
        raise ValueError("run_id must contain at least one alphanumeric character")

    # Check if already normalized: strip trailing -<6hex>, hash the base, compare
    match = re.match(r"^(.+)-([0-9a-f]{6})$", normalized)
    if match:
        base, suffix = match.group(1), match.group(2)
        expected_hash = hashlib.sha256(base.encode()).hexdigest()[:6]
        if suffix == expected_hash:
            return normalized  # already normalized

    # Append short hash of normalized string to avoid collisions
    original_hash = hashlib.sha256(normalized.encode()).hexdigest()[:6]
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


def multi_select(
    message: str,
    choices: Sequence[tuple[str, T] | tuple[str, T, str]],
    instruction: str | None = None,
    validate: Callable[[list[T]], bool | str] | None = None,
) -> list[T] | None:
    """Prompt user to select zero or more choices.

    Args:
        message: The question to ask.
        choices: List of (display_title, value) tuples, optionally with a
            description as the third element.
        instruction: Optional prompt instruction text.
        validate: Optional selected-value validator.

    Returns:
        The selected values, the highlighted value if Enter was pressed with
        no explicit selections, or None if aborted (Ctrl+C).
    """
    q_choices = []
    for choice in choices:
        title, value = choice[:2]
        description = choice[2] if len(choice) == 3 else None
        q_choices.append(
            questionary.Choice(
                title=title,
                value=value,
                description=description,
            )
        )
    return _checkbox_enter_selects_current(
        message,
        choices=q_choices,
        instruction=instruction,
        validate=validate or (lambda _selected: True),
    ).ask()


def _checkbox_enter_selects_current(
    message: str,
    choices: list[questionary.Choice],
    instruction: str | None,
    validate: Callable[[list[Any]], bool | str],
) -> Question:
    """Questionary checkbox variant where Enter selects the highlighted row."""
    merged_style = merge_styles_default(
        [
            Style([("bottom-toolbar", "noreverse")]),
            None,
        ]
    )
    ic = InquirerControl(
        choices,
        pointer=DEFAULT_SELECTED_POINTER,
        show_description=True,
    )

    def get_prompt_tokens() -> list[tuple[str, str]]:
        tokens = [
            ("class:qmark", DEFAULT_QUESTION_PREFIX),
            ("class:question", f" {message} "),
        ]

        if ic.is_answered:
            selected_count = len(ic.selected_options)
            if selected_count == 0:
                tokens.append(("class:answer", "done"))
            elif selected_count == 1:
                selected_title = ic.get_selected_values()[0].title
                if isinstance(selected_title, list):
                    tokens.append(
                        (
                            "class:answer",
                            "".join(token[1] for token in selected_title),
                        )
                    )
                else:
                    tokens.append(("class:answer", f"[{selected_title}]"))
            else:
                tokens.append(("class:answer", f"done ({selected_count} selections)"))
        else:
            tokens.append(
                (
                    "class:instruction",
                    instruction
                    or "(Use arrow keys to move, <space> to select, <a> to toggle, <i> to invert)",
                )
            )
        return tokens

    def get_selected_values() -> list[Any]:
        return [choice.value for choice in ic.get_selected_values()]

    def perform_validation(selected_values: list[Any]) -> bool:
        verdict = validate(selected_values)
        valid = verdict is True
        if not valid:
            error_text = INVALID_INPUT if verdict is False else str(verdict)
            error_message = FormattedText([("class:validation-toolbar", error_text)])
        else:
            error_message = None
        # questionary leaves error_message unannotated, so it infers as None.
        cast(Any, ic).error_message = (
            error_message if not valid and ic.submission_attempted else None
        )
        return valid

    layout = questionary_common.create_inquirer_layout(ic, get_prompt_tokens)
    bindings = KeyBindings()

    @bindings.add(Keys.ControlQ, eager=True)
    @bindings.add(Keys.ControlC, eager=True)
    def _(event):
        event.app.exit(exception=KeyboardInterrupt, style="class:aborting")

    @bindings.add(" ", eager=True)
    def toggle(_event):
        pointed_choice = ic.get_pointed_at().value
        if pointed_choice in ic.selected_options:
            ic.selected_options.remove(pointed_choice)
        else:
            ic.selected_options.append(pointed_choice)
        perform_validation(get_selected_values())

    @bindings.add("i", eager=True)
    def invert(_event):
        ic.selected_options = [
            c.value
            for c in ic.choices
            if not isinstance(c, Separator)
            and c.value not in ic.selected_options
            and not c.disabled
        ]
        perform_validation(get_selected_values())

    @bindings.add("a", eager=True)
    def all(_event):
        all_selected = True
        for choice in ic.choices:
            if (
                not isinstance(choice, Separator)
                and choice.value not in ic.selected_options
                and not choice.disabled
            ):
                ic.selected_options.append(choice.value)
                all_selected = False
        if all_selected:
            ic.selected_options = []
        perform_validation(get_selected_values())

    def move_cursor_down(_event):
        ic.select_next()
        while not ic.is_selection_valid():
            ic.select_next()

    def move_cursor_up(_event):
        ic.select_previous()
        while not ic.is_selection_valid():
            ic.select_previous()

    bindings.add(Keys.Down, eager=True)(move_cursor_down)
    bindings.add(Keys.Up, eager=True)(move_cursor_up)
    bindings.add("j", eager=True)(move_cursor_down)
    bindings.add("k", eager=True)(move_cursor_up)
    bindings.add(Keys.ControlN, eager=True)(move_cursor_down)
    bindings.add(Keys.ControlP, eager=True)(move_cursor_up)

    @bindings.add(Keys.ControlM, eager=True)
    def set_answer(event):
        selected_values = get_selected_values()
        if not selected_values:
            pointed_choice = ic.get_pointed_at()
            if (
                not isinstance(pointed_choice, Separator)
                and not pointed_choice.disabled
            ):
                ic.selected_options = [pointed_choice.value]
                selected_values = [pointed_choice.value]

        ic.submission_attempted = True
        if perform_validation(selected_values):
            ic.is_answered = True
            event.app.exit(result=selected_values)

    @bindings.add(Keys.Any)
    def other(_event):
        """Disallow inserting other text."""

    return Question(
        Application(
            layout=layout,
            key_bindings=bindings,
            style=merged_style,
        )
    )

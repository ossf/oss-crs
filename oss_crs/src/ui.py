import json
import subprocess
import time
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

import yaml
from rich.console import Console, Group

from .utils import bold, get_console, green, log_error, yellow  # noqa: F401 (re-exported)
from rich.live import Live
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

FRAMEWORK_HELPER_SERVICE_PREFIX = "oss-crs-"


@dataclass
class EarlyExitConfig:
    """Configuration for early exit artifact monitoring."""

    watch_dirs: list[Path]  # SUBMIT_DIR paths to monitor
    artifact_subdir: str  # "povs" or "patches"
    poll_interval: float = 2.0  # seconds between checks


class TaskStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    SUCCESS = "success"
    FAILED = "failed"
    STOPPED = "stopped"


class TaskResult:
    def __init__(
        self,
        success: bool,
        output: Optional[str] = None,
        error: Optional[str] = None,
        interrupted: bool = False,
    ):
        self.success = success
        self.output = output
        self.error = error
        self.interrupted = interrupted


class MultiTaskProgress:
    """
    A progress tracker for multiple tasks with live updates.
    Shows a table of all tasks with their current status.

    Args:
        tasks: A list of (task_name, task_function) tuples.
               Each function takes (progress: MultiTaskProgress) and returns bool.
        title: Title for the progress panel.
        head: Optional header for the progress panel.
        console: Optional Rich console instance.
    """

    def __init__(
        self,
        tasks: list[tuple[str, Callable[["MultiTaskProgress"], TaskResult]]],
        title: str = "Task Progress",
        console: Optional[Console] = None,
        deadline: Optional[float] = None,
        early_exit_config: Optional[EarlyExitConfig] = None,
    ):
        self.tasks = tasks
        self.task_names = [name for name, _ in tasks]
        self.title = title
        self.console = console or get_console()
        self.deadline: Optional[float] = deadline
        self.early_exit_config = early_exit_config
        self._early_exit_triggered = threading.Event()
        self.statuses: dict[str, TaskStatus] = {
            name: TaskStatus.PENDING for name in self.task_names
        }
        self.task_info: dict[str, str] = {}  # Info text for each task
        self.cmd_info: dict[str, tuple[str, str]] = {}  # (cmd, cwd) for each task
        self.error_info: dict[str, str] = {}  # Error message for failed tasks
        self.task_notes: dict[str, list[str]] = {}  # Notes for each task
        self.current_output_lines: list[str] = []
        self.max_output_lines = 10
        self._live: Optional[Live] = None
        self._current_task: Optional[str] = None  # Current task ID (full path)
        self._head = []
        self._tail = []
        # Subtask hierarchy: parent_id -> list of child task IDs
        self._subtasks: dict[Optional[str], list[str]] = {None: list(self.task_names)}
        self._task_funcs: dict[str, Callable[["MultiTaskProgress"], TaskResult]] = {
            name: func for name, func in tasks
        }
        # Mapping from task ID to display name
        self._display_names: dict[str, str] = {name: name for name in self.task_names}
        # Cleanup tasks: parent_id -> list of cleanup task IDs (run at end of subtasks)
        self._cleanup_tasks: dict[Optional[str], list[str]] = {}
        # Set of parent task IDs whose cleanup is complete (errors should be shown)
        self._cleanup_complete: set[Optional[str]] = set()
        self._headless = not self.console.is_interactive
        self._entered = False

    def _print_headless(self, message) -> None:
        """Emit plain progress logs when Live rendering is disabled."""
        if self._headless:
            self.console.print(message)

    def _task_label(self, task_name: str) -> str:
        """Return the display label for a task."""
        return escape(self._display_names.get(task_name, task_name))

    def _get_task_parent(self, task_id: str) -> Optional[str]:
        """Find the parent of a given task ID."""
        for parent, children in self._subtasks.items():
            if task_id in children:
                return parent
        for parent, children in self._cleanup_tasks.items():
            if task_id in children:
                return parent
        return None

    def _check_early_exit(self) -> bool:
        """Check if any artifact files exist in watched directories."""
        if not self.early_exit_config:
            return False
        for watch_dir in self.early_exit_config.watch_dirs:
            artifact_dir = watch_dir / self.early_exit_config.artifact_subdir
            if artifact_dir.exists():
                files = [
                    f
                    for f in artifact_dir.iterdir()
                    if f.is_file() and not f.name.startswith(".")
                ]
                if files:
                    return True
        return False

    def _start_early_exit_monitor(self) -> threading.Thread:
        """Start background thread that monitors for early exit condition."""

        def monitor():
            while not self._early_exit_triggered.is_set():
                if self._check_early_exit():
                    self._early_exit_triggered.set()
                    return
                time.sleep(self.early_exit_config.poll_interval)

        thread = threading.Thread(target=monitor, daemon=True)
        thread.start()
        return thread

    def _get_status_icon(self, status: TaskStatus) -> str:
        icons = {
            TaskStatus.PENDING: "[dim]○[/dim]",
            TaskStatus.IN_PROGRESS: "[yellow]●[/yellow]",
            TaskStatus.SUCCESS: "[green]✓[/green]",
            TaskStatus.FAILED: "[red]✗[/red]",
            TaskStatus.STOPPED: "[yellow]■[/yellow]",
        }
        return icons[status]

    def _get_status_text(self, status: TaskStatus) -> str:
        texts = {
            TaskStatus.PENDING: "[dim]Pending[/dim]",
            TaskStatus.IN_PROGRESS: "[yellow]In Progress[/yellow]",
            TaskStatus.SUCCESS: "[green]Success[/green]",
            TaskStatus.FAILED: "[red]Failed[/red]",
            TaskStatus.STOPPED: "[yellow]Stopped[/yellow]",
        }
        return texts[status]

    def _build_display(self) -> Panel:
        """Build the display panel with task table and current output."""
        content_parts = []

        # Add header if present
        for h in self._head:
            content_parts.append(h)

        table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
        table.add_column("", width=3)
        table.add_column("Task", style="bold")
        table.add_column("Status")

        current_task = None

        def add_tasks_to_table(parent: Optional[str], depth: int = 0) -> None:
            nonlocal current_task
            if parent not in self._subtasks:
                return
            for task_id in self._subtasks[parent]:
                status = self.statuses.get(task_id, TaskStatus.PENDING)
                if status == TaskStatus.IN_PROGRESS:
                    current_task = task_id
                indent = "  " * depth
                prefix = "└─ " if depth > 0 else ""
                display_name = self._display_names.get(task_id, task_id)
                table.add_row(
                    self._get_status_icon(status) if depth == 0 else "",
                    f"{indent}{prefix}{display_name}",
                    self._get_status_text(status),
                )
                add_tasks_to_table(task_id, depth + 1)
                # Add notes for this task below its subtasks
                if task_id in self.task_notes:
                    note_indent = "  " * (depth + 1)
                    for note in self.task_notes[task_id]:
                        table.add_row(
                            "",
                            f"{note_indent}[dim cyan]📝 {escape(note)}[/dim cyan]",
                            "",
                        )
            # Add cleanup tasks for this parent
            if parent in self._cleanup_tasks:
                for task_id in self._cleanup_tasks[parent]:
                    status = self.statuses.get(task_id, TaskStatus.PENDING)
                    if status == TaskStatus.IN_PROGRESS:
                        current_task = task_id
                    indent = "  " * depth
                    prefix = "└─ " if depth > 0 else ""
                    display_name = self._display_names.get(task_id, task_id)
                    table.add_row(
                        self._get_status_icon(status) if depth == 0 else "",
                        f"{indent}{prefix}{display_name}",
                        self._get_status_text(status),
                    )

        add_tasks_to_table(None)
        content_parts.append(table)

        # Add task info if there's an in-progress task with info
        if current_task and current_task in self.task_info:
            info_panel = Panel(
                f"[dim]{escape(self.task_info[current_task])}[/dim]",
                title=f"[bold]{escape(current_task)}[/bold]",
                border_style="yellow",
            )
            content_parts.append(info_panel)

        # Add command progress panel if there's an in-progress task
        if current_task and current_task in self.cmd_info:
            cmd, cwd = self.cmd_info[current_task]
            cmd_header = Text()
            cmd_header.append("Command: ", style="bold")
            cmd_header.append(f"{cmd}\n", style="dim")  # Text.append is safe
            cmd_header.append("CWD: ", style="bold")
            cmd_header.append(f"{cwd}", style="dim")  # Text.append is safe

            if self.current_output_lines:
                output_text = Text()
                output_text.append("\n\n")
                for line in self.current_output_lines[-self.max_output_lines :]:
                    output_text.append(f"{line}\n", style="dim")
                cmd_content = Group(cmd_header, output_text)
            else:
                cmd_content = cmd_header

            cmd_panel = Panel(
                cmd_content,
                title="[bold yellow]Command Progress[/bold yellow]",
                border_style="yellow",
            )
            content_parts.append(cmd_panel)

        # Add error panels for all failed tasks (only after their parent's cleanup is complete)
        for task_id, status in self.statuses.items():
            if (
                status in (TaskStatus.FAILED, TaskStatus.STOPPED)
                and task_id in self.error_info
            ):
                # Find the parent of this task and check if cleanup is complete
                parent = self._get_task_parent(task_id)
                if parent in self._cleanup_complete:
                    display_name = self._display_names.get(task_id, task_id)
                    if status == TaskStatus.STOPPED:
                        panel = Panel(
                            f"[yellow]{escape(self.error_info[task_id])}[/yellow]",
                            title=f"[bold yellow]Stopped: {escape(display_name)}[/bold yellow]",
                            border_style="yellow",
                        )
                    else:
                        panel = Panel(
                            f"[red]{escape(self.error_info[task_id])}[/red]",
                            title=f"[bold red]Error: {escape(display_name)}[/bold red]",
                            border_style="red",
                        )
                    content_parts.append(panel)

        # Add tail panels at the end
        for t in self._tail:
            content_parts.append(t)

        return Panel(
            Group(*content_parts),
            title=f"[bold]{self.title}[/bold]",
            border_style="blue",
        )

    def set_status(self, task_name: str, status: TaskStatus) -> None:
        """Update the status of a task."""
        self.statuses[task_name] = status
        if self._headless and self._entered:
            status_prefix = {
                TaskStatus.PENDING: "[dim]PENDING[/dim]",
                TaskStatus.IN_PROGRESS: "[yellow]START[/yellow]",
                TaskStatus.SUCCESS: "[green]OK[/green]",
                TaskStatus.FAILED: "[red]FAIL[/red]",
                TaskStatus.STOPPED: "[yellow]STOP[/yellow]",
            }[status]
            self._print_headless(f"{status_prefix} {self._task_label(task_name)}")
        if status != TaskStatus.IN_PROGRESS:
            # Clear output and cmd_info when task completes
            self.current_output_lines = []
            if task_name in self.cmd_info:
                del self.cmd_info[task_name]
        if self._live:
            self._live.update(self._build_display())

    def __set_task_info(self, task_name: str, info: str) -> None:
        """Set info text for a task (shown when task is in progress)."""
        self.task_info[task_name] = info
        if self._headless:
            self._print_headless(f"[dim]Info:[/dim] {escape(info)}")
        if self._live:
            self._live.update(self._build_display())

    def __set_cmd_info(self, task_name: str, cmd: str, cwd: str) -> None:
        """Set command info for a task (shown in command progress panel)."""
        self.cmd_info[task_name] = (cmd, cwd)
        if self._headless:
            self._print_headless(
                f"[bold]Command:[/bold] {escape(cmd)}\n[bold]CWD:[/bold] {escape(cwd)}"
            )
        if self._live:
            self._live.update(self._build_display())

    def set_error_info(self, task_name: str, error: str) -> None:
        """Set error message for a failed task."""
        self.error_info[task_name] = error
        if self._headless:
            self._print_headless(
                f"[bold red]Error ({self._task_label(task_name)}):[/bold red] {escape(error)}"
            )
        if self._live:
            self._live.update(self._build_display())

    def add_note(self, note: str) -> None:
        """Add a note to the current task (shown below subtasks in the table)."""
        if self._current_task:
            if self._current_task not in self.task_notes:
                self.task_notes[self._current_task] = []
            self.task_notes[self._current_task].append(note)
            if self._headless:
                self._print_headless(f"[dim cyan]Note:[/dim cyan] {escape(note)}")
            if self._live:
                self._live.update(self._build_display())

    def clear_notes(self) -> None:
        """Clear all notes for the current task."""
        if self._current_task and self._current_task in self.task_notes:
            del self.task_notes[self._current_task]
            if self._live:
                self._live.update(self._build_display())

    def add_task(
        self,
        task_name: str,
        task_func: Callable[["MultiTaskProgress"], "TaskResult"],
    ) -> None:
        """
        Add a task as a subtask of the current running task.
        If no task is running, it becomes a top-level task.

        Args:
            task_name: Name of the task to add.
            task_func: Function that takes progress and returns bool.
        """
        parent = self._current_task
        # Create unique task ID by combining parent path with task name
        if parent:
            task_id = f"{parent}/{task_name}"
        else:
            task_id = task_name

        if parent not in self._subtasks:
            self._subtasks[parent] = []
        self._subtasks[parent].append(task_id)
        self._task_funcs[task_id] = task_func
        self._display_names[task_id] = task_name
        self.statuses[task_id] = TaskStatus.PENDING
        if self._live:
            self._live.update(self._build_display())

    def add_tasks(
        self, tasks: list[tuple[str, Callable[["MultiTaskProgress"], "TaskResult"]]]
    ) -> None:
        for task_name, task_func in tasks:
            self.add_task(task_name, task_func)

    def add_cleanup_task(
        self,
        task_name: str,
        task_func: Callable[["MultiTaskProgress"], "TaskResult"],
    ) -> None:
        """
        Add a cleanup task that runs at the end of subtasks, regardless of success/failure.
        Cleanup tasks run in the order they were added, after all regular subtasks.

        Args:
            task_name: Name of the cleanup task to add.
            task_func: Function that takes progress and returns TaskResult.
        """
        parent = self._current_task
        # Create unique task ID by combining parent path with task name
        if parent:
            task_id = f"{parent}/{task_name}"
        else:
            task_id = task_name

        if parent not in self._cleanup_tasks:
            self._cleanup_tasks[parent] = []
        self._cleanup_tasks[parent].append(task_id)
        self._task_funcs[task_id] = task_func
        self._display_names[task_id] = f"🧹 {task_name}"
        self.statuses[task_id] = TaskStatus.PENDING
        if self._live:
            self._live.update(self._build_display())

    def add_cleanup_tasks(
        self, tasks: list[tuple[str, Callable[["MultiTaskProgress"], "TaskResult"]]]
    ) -> None:
        """Add multiple cleanup tasks."""
        for task_name, task_func in tasks:
            self.add_cleanup_task(task_name, task_func)

    def run_added_tasks(self, cleanup_failure_is_error: bool = True) -> TaskResult:
        """
        Run all subtasks added under the current task, then run cleanup tasks.
        Cleanup tasks always run, even if regular subtasks fail.

        Args:
            cleanup_failure_is_error: If False, cleanup task failures are recorded
                in the UI but do not fail the returned TaskResult.

        Returns:
            TaskResult for the run.
        """
        parent = self._current_task
        main_result = TaskResult(success=True)

        # Run regular subtasks
        if parent in self._subtasks:
            for task_id in self._subtasks[parent]:
                if self.statuses[task_id] != TaskStatus.PENDING:
                    continue  # Skip already run tasks
                prev_task = self._current_task
                self._current_task = task_id
                self.set_status(task_id, TaskStatus.IN_PROGRESS)
                try:
                    result = self._task_funcs[task_id](self)
                    if result.success:
                        self.set_status(task_id, TaskStatus.SUCCESS)
                        if result.interrupted:
                            # Early exit: success + interrupted, just log it
                            self._current_task = prev_task
                            main_result = TaskResult(
                                success=True,
                                output=result.output,
                                interrupted=True,
                            )
                            break  # Stop on early exit, continue to cleanup
                    elif result.interrupted:
                        self.set_status(task_id, TaskStatus.STOPPED)
                    else:
                        self.set_status(task_id, TaskStatus.FAILED)
                    if not result.success:
                        if result.error:
                            self.set_error_info(task_id, result.error)
                        self._current_task = prev_task
                        main_result = TaskResult(
                            success=False,
                            error=result.error,
                            interrupted=result.interrupted,
                        )
                        break  # Stop regular tasks on failure, but continue to cleanup
                except Exception as e:
                    self.set_error_info(task_id, str(e))
                    self.set_status(task_id, TaskStatus.FAILED)
                    self._current_task = prev_task
                    main_result = TaskResult(success=False, error=str(e))
                    break  # Stop regular tasks on failure, but continue to cleanup
                self._current_task = prev_task

        # Always run cleanup tasks, even if regular subtasks failed
        cleanup_result = self._run_cleanup_tasks(parent)

        # Mark this parent's cleanup as complete so its errors can be displayed
        self._cleanup_complete.add(parent)
        if self._live:
            self._live.update(self._build_display())

        # Return the first failure (main task failure takes precedence)
        if not main_result.success:
            return main_result
        if not cleanup_failure_is_error and not cleanup_result.success:
            return TaskResult(success=True)
        return cleanup_result

    def _run_cleanup_tasks(self, parent: Optional[str]) -> TaskResult:
        """
        Run cleanup tasks for a given parent task.
        All cleanup tasks are attempted even if some fail.
        Cleanup tasks always ignore the deadline so they can tear down resources.

        Returns:
            TaskResult indicating overall cleanup success.
        """
        if parent not in self._cleanup_tasks:
            return TaskResult(success=True)

        # Suspend deadline during cleanup so docker compose down can finish
        saved_deadline = self.deadline
        self.deadline = None

        first_error = None
        all_success = True

        for task_id in self._cleanup_tasks[parent]:
            if self.statuses[task_id] != TaskStatus.PENDING:
                continue  # Skip already run tasks
            prev_task = self._current_task
            self._current_task = task_id
            self.set_status(task_id, TaskStatus.IN_PROGRESS)
            try:
                result = self._task_funcs[task_id](self)
                if result.success:
                    self.set_status(task_id, TaskStatus.SUCCESS)
                elif result.interrupted:
                    self.set_status(task_id, TaskStatus.STOPPED)
                else:
                    self.set_status(task_id, TaskStatus.FAILED)
                if not result.success:
                    if result.error:
                        self.set_error_info(task_id, result.error)
                    all_success = False
                    if first_error is None:
                        first_error = result.error
                    # Continue running other cleanup tasks even on failure
            except Exception as e:
                self.set_error_info(task_id, str(e))
                self.set_status(task_id, TaskStatus.FAILED)
                all_success = False
                if first_error is None:
                    first_error = str(e)
                # Continue running other cleanup tasks even on failure
            self._current_task = prev_task

        self.deadline = saved_deadline
        return TaskResult(success=all_success, error=first_error)

    def add_output_line(self, line: str) -> None:
        """Add an output line for the current in-progress task."""
        self.current_output_lines.append(line)
        if self._headless:
            self._print_headless(escape(line))
        if self._live:
            self._live.update(self._build_display())

    def __enter__(self) -> "MultiTaskProgress":
        self._entered = True
        if self._headless:
            self._print_headless(f"[bold]{escape(self.title)}[/bold]")
            for item in self._head:
                self._print_headless(item)
            return self
        self._live = Live(
            self._build_display(),
            console=self.console,
            refresh_per_second=10,
        )
        self._live.__enter__()
        return self

    def __exit__(self, *args) -> None:
        if self._live:
            self._live.__exit__(*args)
            self._live = None
        self._entered = False

    def run_command_with_streaming_output(
        self,
        cmd: list[str],
        cwd: Optional[Path] = None,
        env: Optional[dict] = None,
        info_text: Optional[str] = None,
    ) -> TaskResult:
        """
        Run a subprocess command with streaming output displayed via MultiTaskProgress.

        Args:
            cmd: Command and arguments to execute.
            cwd: Working directory for the command.
            env: Environment variables for the command.

        Returns:
            True if command succeeded, False otherwise.
        """
        task_name = self._current_task
        if info_text:
            self.__set_task_info(task_name, info_text)
        self.__set_cmd_info(self._current_task, " ".join(cmd), str(cwd) if cwd else ".")
        output_lines = []

        def process_output(line: str) -> None:
            """Process a line of output."""
            output_lines.append(line)
            self.add_output_line(line)

        def _graceful_terminate(proc: subprocess.Popen, timeout: int = 20) -> None:
            """Gracefully terminate a process: SIGTERM, wait, then SIGKILL if needed."""
            proc.terminate()
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=cwd,
                env=env,
                bufsize=1,
            )

            timed_out = threading.Event()
            early_exited = threading.Event()
            timer = None
            early_exit_thread = None

            if self.deadline is not None:
                remaining = self.deadline - time.monotonic()
                if remaining <= 0:
                    _graceful_terminate(process)
                    error_msg = (
                        f"Deadline already exceeded before running:\n{' '.join(cmd)}"
                    )
                    if task_name:
                        self.set_error_info(task_name, error_msg)
                    return TaskResult(success=False, error=error_msg)

                def _on_timeout():
                    timed_out.set()
                    # Stop the early exit monitor thread
                    self._early_exit_triggered.set()
                    _graceful_terminate(process)

                timer = threading.Timer(remaining, _on_timeout)
                timer.daemon = True
                timer.start()

            # Early exit uses a thread that waits for the trigger event
            if self.early_exit_config is not None:

                def _on_early_exit():
                    self._early_exit_triggered.wait()  # Block until artifact found
                    if timed_out.is_set():
                        return  # Timeout already handled it
                    # Confirm it's a real artifact (not just signaled by timeout)
                    if self._check_early_exit():
                        early_exited.set()
                        _graceful_terminate(process, timeout=30)

                early_exit_thread = threading.Thread(target=_on_early_exit, daemon=True)
                early_exit_thread.start()

            try:
                for line in iter(process.stdout.readline, ""):
                    line = line.rstrip()
                    if line:
                        process_output(line)
                process.wait()
            except KeyboardInterrupt:
                _graceful_terminate(process)
                error = "\n".join(output_lines)
                error += "\n\n📝 Process interrupted by user"
                return TaskResult(success=False, error=error, interrupted=True)
            finally:
                if timer is not None:
                    timer.cancel()

            if timed_out.is_set():
                error_msg = f"■ Timed out.\nContainers were gracefully stopped.\n{' '.join(cmd)}\n\n"
                error_msg += "\n".join(output_lines)
                if task_name:
                    self.set_error_info(task_name, error_msg)
                return TaskResult(success=False, error=error_msg, interrupted=True)

            if early_exited.is_set():
                output = "\n".join(output_lines)
                output += "\n\n■ Early exit: artifact discovered. Containers gracefully stopped."
                return TaskResult(success=True, output=output, interrupted=True)

            if process.returncode == 0:
                return TaskResult(success=True, output="\n".join(output_lines))
            else:
                # Set error info with last output lines
                error_msg = f"Command failed with exit code {process.returncode}:\n{' '.join(cmd)}\n\n"
                error_msg += "\n".join(output_lines)
                if task_name:
                    self.set_error_info(task_name, error_msg)
                return TaskResult(success=False, error=error_msg)

        except FileNotFoundError:
            cmd_name = cmd[0] if cmd else "command"
            error_msg = (
                f"{cmd_name} not found. Please ensure it is installed and in PATH."
            )
            if task_name:
                self.set_error_info(task_name, error_msg)
            else:
                log_error(error_msg)
            return TaskResult(success=False, error=error_msg)
        except Exception as e:
            error_msg = str(e)
            if task_name:
                self.set_error_info(task_name, error_msg)
            else:
                log_error(error_msg)
            return TaskResult(success=False, error=error_msg)

    def add_items_to_head(self, items) -> None:
        self._head += items

    def show_run_result(self, crs_results: list[dict]) -> None:
        """
        Display a summary of CRS run results in a dedicated panel.

        Args:
            crs_results: List of dicts with keys:
                - name: CRS name
                - submit_dir: Path to the CRS submit directory
        """
        lines = Text()
        for i, entry in enumerate(crs_results):
            submit_dir = entry["submit_dir"]
            pov_dir = submit_dir / "povs"
            seed_dir = submit_dir / "seeds"
            patch_dir = submit_dir / "patches"

            pov_count = _count_files(pov_dir)
            seed_count = _count_files(seed_dir)
            patch_count = _count_files(patch_dir)

            lines.append(f"{entry['name']}:\n", style="bold cyan")
            lines.append("  - Patches: ", style="bold")
            lines.append(f"{patch_count}\n", style="yellow")
            lines.append("       - Directory: ", style="dim")
            lines.append(f"{patch_dir}\n" if patch_dir.exists() else "-\n", style="dim")
            lines.append("  - POVs: ", style="bold")
            lines.append(f"{pov_count}\n", style="red")
            lines.append("       - Directory: ", style="dim")
            lines.append(f"{pov_dir}\n" if pov_dir.exists() else "-\n", style="dim")
            lines.append("  - Seeds: ", style="bold")
            lines.append(f"{seed_count}\n", style="green")
            lines.append("       - Directory: ", style="dim")
            lines.append(f"{seed_dir}\n" if seed_dir.exists() else "-\n", style="dim")

            if i < len(crs_results) - 1:
                lines.append("\n")

        result_panel = Panel(
            lines,
            title="[bold]CRS Run Results[/bold]",
            border_style="green",
        )
        self._tail.append(result_panel)
        if self._headless:
            self._print_headless(result_panel)
        if self._live:
            self._live.update(self._build_display())

    def docker_compose_build(
        self,
        project_name: str,
        docker_compose_path: Path,
    ) -> TaskResult:
        cmd = [
            "docker",
            "compose",
            "-p",
            project_name,
            "-f",
            str(docker_compose_path),
            "build",
        ]
        ret = self.run_command_with_streaming_output(
            cmd=cmd,
            info_text="Building Docker images with docker-compose",
        )
        if ret.success:
            return ret
        docker_compose_contents = docker_compose_path.read_text()
        ret.error += (
            f"\n📝 Docker compose file contents:\n---\n{docker_compose_contents}\n---"
        )
        return ret

    def docker_compose_run(
        self, project_name: str, docker_compose_path: Path, service_name: str
    ) -> TaskResult:
        cmd = [
            "docker",
            "compose",
            "-p",
            project_name,
            "-f",
            str(docker_compose_path),
            "run",
            "--rm",
            service_name,
        ]
        return self.run_command_with_streaming_output(
            cmd=cmd,
            info_text=f"Running service {service_name} with docker-compose",
        )

    def docker_compose_up(
        self,
        project_name: str,
        docker_compose_path: Path,
    ) -> TaskResult:
        helper_services = self._get_teardown_helper_services(docker_compose_path)
        (
            event_lines,
            stop_event_collection,
            event_collection_started,
        ) = self._start_compose_event_collection(project_name, docker_compose_path)
        running_helpers_before_stop = self._get_running_helper_services(
            project_name, docker_compose_path, helper_services
        )
        cmd = [
            "docker",
            "compose",
            "-p",
            project_name,
            "-f",
            str(docker_compose_path),
            "up",
            "--abort-on-container-exit",
        ]
        result = self.run_command_with_streaming_output(
            cmd=cmd,
            info_text="Bringing up services with docker-compose",
        )

        # Explicitly stop all containers after compose up exits.
        # --abort-on-container-exit may not stop containers with restart policies
        # or services with attach: false.
        try:
            subprocess.run(
                [
                    "docker",
                    "compose",
                    "-p",
                    project_name,
                    "-f",
                    str(docker_compose_path),
                    "stop",
                ],
                capture_output=True,
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            pass
        stop_event_collection()
        if event_collection_started and event_lines:
            ignored_helper_exit_services = self._get_ignored_helper_exit_services(
                event_lines, helper_services
            )
        else:
            ignored_helper_exit_services = running_helpers_before_stop

        # If compose returned success, double-check no containers failed
        # Skip if interrupted (early exit) since we intentionally killed the containers
        if result.success and not result.interrupted:
            check_result = self._check_failed_containers(
                project_name, docker_compose_path, ignored_helper_exit_services
            )
            if not check_result.success:
                check_result.error = result.output + "\n\n" + check_result.error
                return check_result

        return result

    def _check_failed_containers(
        self,
        project_name: str,
        docker_compose_path: Path,
        ignored_helper_exit_services: Optional[set[str]] = None,
    ) -> TaskResult:
        """Check if any containers exited with non-zero exit code."""
        cmd = [
            "docker",
            "compose",
            "-p",
            project_name,
            "-f",
            str(docker_compose_path),
            "ps",
            "-a",
            "--format",
            "{{.Service}}:{{.ExitCode}}:{{.Name}}",
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            failed_containers = []
            for line in result.stdout.strip().split("\n"):
                if not line.strip():
                    continue
                parts = line.split(":", 2)
                if len(parts) == 3:
                    service_name, exit_code, name = parts
                elif len(parts) == 2:
                    service_name = ""
                    name, exit_code = parts
                else:
                    continue
                exit_code = exit_code.strip()
                if exit_code in ("", "0"):
                    continue
                if service_name in (
                    ignored_helper_exit_services or set()
                ) and exit_code in {"137", "143"}:
                    continue
                failed_containers.append(f"{name} (exit {exit_code})")
            if failed_containers:
                error_msg = "Containers exited with errors:\n" + "\n".join(
                    failed_containers
                )
                return TaskResult(success=False, error=error_msg)
        except Exception:
            pass  # If check fails, assume success
        return TaskResult(success=True)

    def _get_teardown_helper_services(self, docker_compose_path: Path) -> set[str]:
        """Return helper services whose teardown exits should not fail the run."""
        try:
            compose_data = yaml.safe_load(docker_compose_path.read_text()) or {}
        except Exception:
            return set()

        services = compose_data.get("services", {})
        helper_services = set()
        for service_name, service_config in services.items():
            if not isinstance(service_config, dict):
                continue
            # Framework-managed helper sidecars must keep the reserved
            # "oss-crs-" prefix so teardown classification can exempt their
            # expected non-zero exits during compose shutdown.
            if service_name.startswith(FRAMEWORK_HELPER_SERVICE_PREFIX):
                helper_services.add(service_name)
                continue
            if service_config.get("attach") is False and (
                "restart" in service_config or "healthcheck" in service_config
            ):
                helper_services.add(service_name)
        return helper_services

    def _start_compose_event_collection(
        self, project_name: str, docker_compose_path: Path
    ) -> tuple[list[str], Callable[[], None], bool]:
        event_lines: list[str] = []
        event_process = None
        event_thread = None
        started = False

        try:
            event_process = subprocess.Popen(
                [
                    "docker",
                    "compose",
                    "-p",
                    project_name,
                    "-f",
                    str(docker_compose_path),
                    "events",
                    "--json",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )

            def _collect_events() -> None:
                if event_process is None or event_process.stdout is None:
                    return
                for line in iter(event_process.stdout.readline, ""):
                    line = line.strip()
                    if line:
                        event_lines.append(line)

            event_thread = threading.Thread(target=_collect_events, daemon=True)
            event_thread.start()
            started = True
        except Exception:
            event_process = None
            event_thread = None

        def _stop_collection() -> None:
            if event_process is None:
                return
            try:
                event_process.terminate()
                event_process.wait(timeout=5)
            except Exception:
                try:
                    event_process.kill()
                    event_process.wait(timeout=5)
                except Exception:
                    pass
            if event_thread is not None:
                event_thread.join(timeout=5)

        return event_lines, _stop_collection, started

    def _get_running_helper_services(
        self,
        project_name: str,
        docker_compose_path: Path,
        helper_services: set[str],
    ) -> set[str]:
        if not helper_services:
            return set()

        cmd = [
            "docker",
            "compose",
            "-p",
            project_name,
            "-f",
            str(docker_compose_path),
            "ps",
            "--format",
            "{{.Service}}",
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
        except Exception:
            return set()

        running = {
            line.strip()
            for line in result.stdout.splitlines()
            if line.strip() in helper_services
        }
        return running

    def _get_ignored_helper_exit_services(
        self, event_lines: list[str], helper_services: set[str]
    ) -> set[str]:
        if not event_lines or not helper_services:
            return set()

        die_events: list[tuple[int, str, str, bool]] = []
        for line in event_lines:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            if event.get("type") != "container" or event.get("action") != "die":
                continue

            attrs = event.get("attributes", {})
            service_name = (
                event.get("service")
                or attrs.get("service")
                or attrs.get("com.docker.compose.service")
            )
            if not service_name:
                continue

            exit_code = str(attrs.get("exitCode", event.get("exitCode", ""))).strip()
            event_time = self._parse_compose_event_time(event)
            if event_time == 0:
                continue
            die_events.append(
                (event_time, service_name, exit_code, service_name in helper_services)
            )

        primary_success_time = min(
            (
                event_time
                for event_time, _service_name, exit_code, is_helper in die_events
                if not is_helper and exit_code == "0"
            ),
            default=None,
        )

        if primary_success_time is None:
            return set()

        helper_failures_before_primary: set[str] = set()
        helper_signal_exits_after_primary: set[str] = set()
        for event_time, service_name, exit_code, is_helper in sorted(die_events):
            if not is_helper:
                continue
            if event_time < primary_success_time:
                if exit_code not in ("", "0"):
                    helper_failures_before_primary.add(service_name)
                continue
            if exit_code in {"137", "143"}:
                helper_signal_exits_after_primary.add(service_name)

        return helper_signal_exits_after_primary - helper_failures_before_primary

    def _parse_compose_event_time(self, event: dict) -> int:
        time_nano = event.get("timeNano")
        if isinstance(time_nano, int):
            return time_nano
        if isinstance(time_nano, str) and time_nano.isdigit():
            return int(time_nano)

        raw_time = event.get("time")
        if isinstance(raw_time, int):
            return raw_time * 1_000_000_000
        if isinstance(raw_time, float):
            return int(raw_time * 1_000_000_000)
        if isinstance(raw_time, str):
            stripped = raw_time.strip()
            if stripped.isdigit():
                return int(stripped) * 1_000_000_000
            try:
                normalized = stripped
                if normalized.endswith("Z"):
                    normalized = normalized[:-1] + "+00:00"
                if "." in normalized:
                    main, suffix = normalized.split(".", 1)
                    frac = suffix
                    tz = ""
                    for marker in ("+", "-"):
                        idx = frac.find(marker)
                        if idx != -1:
                            tz = frac[idx:]
                            frac = frac[:idx]
                            break
                    frac = (frac[:6]).ljust(6, "0")
                    normalized = f"{main}.{frac}{tz}"
                dt = datetime.fromisoformat(normalized)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return int(dt.timestamp() * 1_000_000_000)
            except ValueError:
                return 0
        return 0

    def docker_compose_down(
        self, project_name: str, docker_compose_path: Path
    ) -> TaskResult:
        task_name = self._current_task
        down_error: str | None = None
        if task_name:
            self.__set_task_info(
                task_name, "Stopping and removing containers with docker-compose down"
            )
            self.__set_cmd_info(
                task_name,
                "docker compose down -v --rmi local --remove-orphans",
                str(docker_compose_path.parent),
            )
        try:
            result = subprocess.run(
                [
                    "docker",
                    "compose",
                    "-p",
                    project_name,
                    "-f",
                    str(docker_compose_path),
                    "down",
                    "-v",
                    "--rmi",
                    "local",
                    "--remove-orphans",
                ],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode != 0:
                down_error = (
                    f"docker compose down failed with exit code {result.returncode}\n"
                    f"{result.stdout or ''}\n{result.stderr or ''}"
                ).strip()
        except subprocess.TimeoutExpired:
            down_error = "docker compose down timed out after 120 seconds"
        except Exception as exc:
            down_error = f"docker compose down failed: {exc}"

        # Best effort: remove project-scoped compose-built images that can linger.
        # We intentionally do not fail teardown if image cleanup fails.
        try:
            image_refs: set[str] = set()

            # Prefer compose project labels when available.
            labeled_cmd = [
                "docker",
                "image",
                "ls",
                "--filter",
                f"label=com.docker.compose.project={project_name}",
                "--format",
                "{{.Repository}}:{{.Tag}}",
            ]
            labeled_result = subprocess.run(
                labeled_cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if labeled_result.returncode == 0:
                image_refs.update(
                    line.strip()
                    for line in labeled_result.stdout.splitlines()
                    if line.strip() and not line.strip().endswith(":<none>")
                )
            else:
                self.add_note(
                    "Warning: failed to list compose-labeled images for cleanup."
                )

            # Fallback discovery: prefix-based repository matching, then verify
            # compose ownership by image label before deleting.
            ls_cmd = ["docker", "image", "ls", "--format", "{{.Repository}}:{{.Tag}}"]
            ls_result = subprocess.run(
                ls_cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if ls_result.returncode == 0:
                prefix = f"{project_name}-"
                for line in ls_result.stdout.splitlines():
                    ref = line.strip()
                    if ref and ref.startswith(prefix) and not ref.endswith(":<none>"):
                        inspect_result = subprocess.run(
                            [
                                "docker",
                                "image",
                                "inspect",
                                "--format",
                                '{{.Id}} {{index .Config.Labels "com.docker.compose.project"}}',
                                ref,
                            ],
                            capture_output=True,
                            text=True,
                            timeout=30,
                        )
                        if inspect_result.returncode != 0:
                            continue
                        out = inspect_result.stdout.strip().split(maxsplit=1)
                        if len(out) != 2:
                            continue
                        _image_id, project_label = out
                        if project_label == project_name:
                            image_refs.add(ref)
            else:
                self.add_note(
                    "Warning: failed to list docker images for cleanup fallback."
                )

            if image_refs:
                rm_result = subprocess.run(
                    ["docker", "image", "rm", "-f", *sorted(image_refs)],
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if rm_result.returncode != 0:
                    self.add_note("Warning: some compose images could not be removed.")
        except Exception:
            self.add_note("Warning: compose image cleanup encountered an error.")

        if down_error is not None:
            if task_name:
                self.set_error_info(task_name, down_error)
            return TaskResult(success=False, error=down_error)
        return TaskResult(success=True)


def _count_files(dir_path: Path) -> int:
    """Count non-hidden files in a directory."""
    if not dir_path.exists():
        return 0
    return len(
        [f for f in dir_path.iterdir() if f.is_file() and not f.name.startswith(".")]
    )

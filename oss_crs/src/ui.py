import subprocess
import time
import threading
from enum import Enum
from pathlib import Path
from typing import Callable, Optional

from rich.console import Console, Group

from .utils import log_error
from rich.live import Live
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


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
    ):
        self.tasks = tasks
        self.task_names = [name for name, _ in tasks]
        self.title = title
        self.console = console or Console()
        self.deadline: Optional[float] = deadline
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

    def _get_task_parent(self, task_id: str) -> Optional[str]:
        """Find the parent of a given task ID."""
        for parent, children in self._subtasks.items():
            if task_id in children:
                return parent
        for parent, children in self._cleanup_tasks.items():
            if task_id in children:
                return parent
        return None

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
        if self._live:
            self._live.update(self._build_display())

    def __set_cmd_info(self, task_name: str, cmd: str, cwd: str) -> None:
        """Set command info for a task (shown in command progress panel)."""
        self.cmd_info[task_name] = (cmd, cwd)
        if self._live:
            self._live.update(self._build_display())

    def set_error_info(self, task_name: str, error: str) -> None:
        """Set error message for a failed task."""
        self.error_info[task_name] = error
        if self._live:
            self._live.update(self._build_display())

    def add_note(self, note: str) -> None:
        """Add a note to the current task (shown below subtasks in the table)."""
        if self._current_task:
            if self._current_task not in self.task_notes:
                self.task_notes[self._current_task] = []
            self.task_notes[self._current_task].append(note)
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

    def run_added_tasks(self) -> TaskResult:
        """
        Run all subtasks added under the current task, then run cleanup tasks.
        Cleanup tasks always run, even if regular subtasks fail.

        Returns:
            True if all subtasks succeeded, False if any failed.
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
        if self._live:
            self._live.update(self._build_display())

    def __enter__(self) -> "MultiTaskProgress":
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
            timer = None

            if self.deadline is not None:
                remaining = self.deadline - time.monotonic()
                if remaining <= 0:
                    process.terminate()
                    process.wait()
                    error_msg = (
                        f"Deadline already exceeded before running:\n{' '.join(cmd)}"
                    )
                    if task_name:
                        self.set_error_info(task_name, error_msg)
                    return TaskResult(success=False, error=error_msg)

                def _on_timeout():
                    timed_out.set()
                    process.terminate()
                    try:
                        process.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        process.kill()

                timer = threading.Timer(remaining, _on_timeout)
                timer.daemon = True
                timer.start()

            try:
                for line in iter(process.stdout.readline, ""):
                    line = line.rstrip()
                    if line:
                        process_output(line)
                process.wait()
            except KeyboardInterrupt:
                process.terminate()
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
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
                ["docker", "compose", "-p", project_name, "-f",
                 str(docker_compose_path), "stop"],
                capture_output=True, timeout=60,
            )
        except subprocess.TimeoutExpired:
            pass

        # If compose returned success, double-check no containers failed
        if result.success:
            check_result = self._check_failed_containers(
                project_name, docker_compose_path
            )
            if not check_result.success:
                check_result.error = result.output + "\n\n" + check_result.error
                return check_result

        return result

    def _check_failed_containers(
        self, project_name: str, docker_compose_path: Path
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
            "{{.Name}}:{{.ExitCode}}",
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            failed_containers = []
            for line in result.stdout.strip().split("\n"):
                if ":" in line:
                    name, exit_code = line.rsplit(":", 1)
                    if exit_code.strip() not in ("", "0"):
                        failed_containers.append(f"{name} (exit {exit_code})")
            if failed_containers:
                error_msg = "Containers exited with errors:\n" + "\n".join(
                    failed_containers
                )
                return TaskResult(success=False, error=error_msg)
        except Exception:
            pass  # If check fails, assume success
        return TaskResult(success=True)

    def docker_compose_down(
        self, project_name: str, docker_compose_path: Path
    ) -> TaskResult:
        cmd = [
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
        ]
        return self.run_command_with_streaming_output(
            cmd=cmd,
            info_text="Stopping and removing containers with docker-compose down",
        )


def bold(text: str) -> Text:
    return Text(text, style="bold")


def yellow(text: str, bold=False) -> Text:
    return Text(text, style="bold yellow" if bold else "yellow")


def green(text: str, bold=False) -> Text:
    return Text(text, style="bold green" if bold else "green")


def _count_files(dir_path: Path) -> int:
    """Count non-hidden files in a directory."""
    if not dir_path.exists():
        return 0
    return len(
        [f for f in dir_path.iterdir() if f.is_file() and not f.name.startswith(".")]
    )

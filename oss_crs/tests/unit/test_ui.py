"""Unit tests for MultiTaskProgress cleanup semantics."""

import io
import threading
from pathlib import Path
from types import SimpleNamespace

from oss_crs.src.ui import MultiTaskProgress, TaskResult


def test_run_added_tasks_fails_on_cleanup_failure_by_default() -> None:
    progress = MultiTaskProgress(tasks=[], title="test")

    progress.add_task("main", lambda p: TaskResult(success=True))
    progress.add_cleanup_task("cleanup", lambda p: TaskResult(success=False, error="x"))

    result = progress.run_added_tasks()

    assert result.success is False


def test_run_added_tasks_can_ignore_cleanup_failure() -> None:
    progress = MultiTaskProgress(tasks=[], title="test")

    progress.add_task("main", lambda p: TaskResult(success=True))
    progress.add_cleanup_task("cleanup", lambda p: TaskResult(success=False, error="x"))

    result = progress.run_added_tasks(cleanup_failure_is_error=False)

    assert result.success is True


def test_docker_compose_down_prunes_project_scoped_images(monkeypatch) -> None:
    progress = MultiTaskProgress(tasks=[], title="test")
    docker_cmds: list[list[str]] = []

    def fake_subprocess_run(cmd, **_kwargs):
        docker_cmds.append(cmd)
        if cmd[:6] == [
            "docker",
            "compose",
            "-p",
            "proj",
            "-f",
            "/tmp/docker-compose.yml",
        ]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[:5] == [
            "docker",
            "image",
            "ls",
            "--filter",
            "label=com.docker.compose.project=proj",
        ]:
            return SimpleNamespace(
                returncode=0, stdout="proj-svc-label:latest\n", stderr=""
            )
        if cmd[:3] == ["docker", "image", "ls"]:
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    "proj-svc1:latest\n"
                    "other:latest\n"
                    "proj-svc2:latest\n"
                    "proj-oss-crs-litellm-key-gen:latest\n"
                    "oss-crs-litellm-key-gen:latest\n"
                ),
                stderr="",
            )
        if cmd[:4] == ["docker", "image", "inspect", "--format"]:
            ref = cmd[5]
            if ref == "proj-svc1:latest":
                return SimpleNamespace(
                    returncode=0, stdout="sha256:svc1 proj\n", stderr=""
                )
            if ref == "proj-svc2:latest":
                return SimpleNamespace(
                    returncode=0, stdout="sha256:svc2 proj\n", stderr=""
                )
            if ref == "proj-oss-crs-litellm-key-gen:latest":
                return SimpleNamespace(
                    returncode=0, stdout="sha256:keygen proj\n", stderr=""
                )
            return SimpleNamespace(
                returncode=0, stdout="sha256:other other\n", stderr=""
            )
        if cmd[:4] == ["docker", "image", "rm", "-f"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[:4] == ["docker", "image", "prune", "-f"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("oss_crs.src.ui.subprocess.run", fake_subprocess_run)
    result = progress.docker_compose_down("proj", Path("/tmp/docker-compose.yml"))

    assert result.success is True
    assert [
        "docker",
        "compose",
        "-p",
        "proj",
        "-f",
        "/tmp/docker-compose.yml",
        "down",
        "-v",
        "--rmi",
        "local",
        "--remove-orphans",
    ] in docker_cmds
    assert [
        "docker",
        "image",
        "ls",
        "--filter",
        "label=com.docker.compose.project=proj",
        "--format",
        "{{.Repository}}:{{.Tag}}",
    ] in docker_cmds
    assert [
        "docker",
        "image",
        "ls",
        "--format",
        "{{.Repository}}:{{.Tag}}",
    ] in docker_cmds
    assert [
        "docker",
        "image",
        "rm",
        "-f",
        "proj-oss-crs-litellm-key-gen:latest",
        "proj-svc-label:latest",
        "proj-svc1:latest",
        "proj-svc2:latest",
    ] in docker_cmds


def test_docker_compose_down_attempts_image_cleanup_even_on_down_failure(
    monkeypatch,
) -> None:
    progress = MultiTaskProgress(tasks=[], title="test")
    docker_cmds: list[list[str]] = []

    def fake_subprocess_run(cmd, **_kwargs):
        docker_cmds.append(cmd)
        if cmd[:6] == [
            "docker",
            "compose",
            "-p",
            "proj",
            "-f",
            "/tmp/docker-compose.yml",
        ]:
            return SimpleNamespace(returncode=1, stdout="x", stderr="y")
        if cmd[:5] == [
            "docker",
            "image",
            "ls",
            "--filter",
            "label=com.docker.compose.project=proj",
        ]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[:3] == ["docker", "image", "ls"]:
            return SimpleNamespace(returncode=0, stdout="proj-svc1:latest\n", stderr="")
        if cmd[:4] == ["docker", "image", "inspect", "--format"]:
            return SimpleNamespace(returncode=0, stdout="sha256:svc1 proj\n", stderr="")
        if cmd[:4] == ["docker", "image", "rm", "-f"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[:4] == ["docker", "image", "prune", "-f"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("oss_crs.src.ui.subprocess.run", fake_subprocess_run)
    result = progress.docker_compose_down("proj", Path("/tmp/docker-compose.yml"))

    assert result.success is False
    assert [
        "docker",
        "image",
        "ls",
        "--format",
        "{{.Repository}}:{{.Tag}}",
    ] in docker_cmds


def test_docker_compose_down_adds_warning_on_image_rm_failure(monkeypatch) -> None:
    progress = MultiTaskProgress(tasks=[], title="test")
    progress._current_task = "cleanup"
    progress.task_notes["cleanup"] = []

    def fake_subprocess_run(cmd, **_kwargs):
        if cmd[:6] == [
            "docker",
            "compose",
            "-p",
            "proj",
            "-f",
            "/tmp/docker-compose.yml",
        ]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[:5] == [
            "docker",
            "image",
            "ls",
            "--filter",
            "label=com.docker.compose.project=proj",
        ]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[:3] == ["docker", "image", "ls"]:
            return SimpleNamespace(returncode=0, stdout="proj-svc1:latest\n", stderr="")
        if cmd[:4] == ["docker", "image", "inspect", "--format"]:
            return SimpleNamespace(returncode=0, stdout="sha256:svc1 proj\n", stderr="")
        if cmd[:4] == ["docker", "image", "rm", "-f"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="rm failed")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("oss_crs.src.ui.subprocess.run", fake_subprocess_run)
    result = progress.docker_compose_down("proj", Path("/tmp/docker-compose.yml"))

    assert result.success is True
    assert any("could not be removed" in n for n in progress.task_notes["cleanup"])


def test_docker_compose_down_does_not_remove_prefix_collision_images(
    monkeypatch,
) -> None:
    progress = MultiTaskProgress(tasks=[], title="test")
    docker_cmds: list[list[str]] = []

    def fake_subprocess_run(cmd, **_kwargs):
        docker_cmds.append(cmd)
        if cmd[:6] == [
            "docker",
            "compose",
            "-p",
            "proj",
            "-f",
            "/tmp/docker-compose.yml",
        ]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[:5] == [
            "docker",
            "image",
            "ls",
            "--filter",
            "label=com.docker.compose.project=proj",
        ]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[:3] == ["docker", "image", "ls"]:
            return SimpleNamespace(
                returncode=0,
                stdout="proj-owned:latest\nproj-collision:latest\n",
                stderr="",
            )
        if cmd[:4] == ["docker", "image", "inspect", "--format"]:
            ref = cmd[5]
            if ref == "proj-owned:latest":
                return SimpleNamespace(
                    returncode=0, stdout="sha256:owned proj\n", stderr=""
                )
            return SimpleNamespace(
                returncode=0, stdout="sha256:collision someone-else\n", stderr=""
            )
        if cmd[:4] == ["docker", "image", "rm", "-f"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("oss_crs.src.ui.subprocess.run", fake_subprocess_run)
    result = progress.docker_compose_down("proj", Path("/tmp/docker-compose.yml"))

    assert result.success is True
    assert ["docker", "image", "rm", "-f", "proj-owned:latest"] in docker_cmds


def test_docker_compose_down_warns_when_image_list_fails(monkeypatch) -> None:
    progress = MultiTaskProgress(tasks=[], title="test")
    progress._current_task = "cleanup"
    progress.task_notes["cleanup"] = []

    def fake_subprocess_run(cmd, **_kwargs):
        if cmd[:6] == [
            "docker",
            "compose",
            "-p",
            "proj",
            "-f",
            "/tmp/docker-compose.yml",
        ]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[:5] == [
            "docker",
            "image",
            "ls",
            "--filter",
            "label=com.docker.compose.project=proj",
        ]:
            return SimpleNamespace(returncode=1, stdout="", stderr="boom")
        if cmd[:3] == ["docker", "image", "ls"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="boom")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("oss_crs.src.ui.subprocess.run", fake_subprocess_run)
    result = progress.docker_compose_down("proj", Path("/tmp/docker-compose.yml"))

    assert result.success is True
    assert any("compose-labeled images" in n for n in progress.task_notes["cleanup"])
    assert any("cleanup fallback" in n for n in progress.task_notes["cleanup"])


def test_check_failed_containers_ignores_helper_teardown_exit(
    tmp_path: Path, monkeypatch
) -> None:
    progress = MultiTaskProgress(tasks=[], title="test")
    compose_path = tmp_path / "docker-compose.yml"
    compose_path.write_text(
        """
services:
  crs-codex_patcher:
    image: test
  crs-codex_inc-builder-asan:
    image: test
    attach: false
    restart: on-failure:3
    healthcheck:
      test: ["CMD", "true"]
  oss-crs-exchange:
    image: test
    attach: false
""".strip()
    )

    def fake_subprocess_run(cmd, **_kwargs):
        assert cmd[-2:] == ["--format", "{{.Service}}:{{.ExitCode}}:{{.Name}}"]
        return SimpleNamespace(
            returncode=0,
            stdout=(
                "crs-codex_patcher:0:proj-crs-codex_patcher-1\n"
                "crs-codex_inc-builder-asan:137:proj-crs-codex_inc-builder-asan-1\n"
                "oss-crs-exchange:143:proj-oss-crs-exchange-1\n"
            ),
            stderr="",
        )

    monkeypatch.setattr("oss_crs.src.ui.subprocess.run", fake_subprocess_run)

    result = progress._check_failed_containers(
        "proj", compose_path, {"crs-codex_inc-builder-asan", "oss-crs-exchange"}
    )

    assert result.success is True


def test_check_failed_containers_reports_unsuppressed_helper_failures(
    tmp_path: Path, monkeypatch
) -> None:
    progress = MultiTaskProgress(tasks=[], title="test")
    compose_path = tmp_path / "docker-compose.yml"
    compose_path.write_text(
        """
services:
  crs-codex_patcher:
    image: test
  crs-codex_inc-builder-asan:
    image: test
    attach: false
    restart: on-failure:3
""".strip()
    )

    def fake_subprocess_run(cmd, **_kwargs):
        assert cmd[-2:] == ["--format", "{{.Service}}:{{.ExitCode}}:{{.Name}}"]
        return SimpleNamespace(
            returncode=0,
            stdout=(
                "crs-codex_patcher:0:proj-crs-codex_patcher-1\n"
                "crs-codex_inc-builder-asan:1:proj-crs-codex_inc-builder-asan-1\n"
            ),
            stderr="",
        )

    monkeypatch.setattr("oss_crs.src.ui.subprocess.run", fake_subprocess_run)

    result = progress._check_failed_containers("proj", compose_path)

    assert result.success is False
    assert "proj-crs-codex_inc-builder-asan-1 (exit 1)" in result.error


def test_check_failed_containers_still_reports_non_helper_failures(
    tmp_path: Path, monkeypatch
) -> None:
    progress = MultiTaskProgress(tasks=[], title="test")
    compose_path = tmp_path / "docker-compose.yml"
    compose_path.write_text(
        """
services:
  crs-codex_patcher:
    image: test
  oss-crs-exchange:
    image: test
    attach: false
    restart: on-failure:3
""".strip()
    )

    def fake_subprocess_run(cmd, **_kwargs):
        if cmd[-2:] == ["--format", "{{.Service}}:{{.ExitCode}}:{{.Name}}"]:
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    "crs-codex_patcher:1:proj-crs-codex_patcher-1\n"
                    "oss-crs-exchange:137:proj-oss-crs-exchange-1\n"
                ),
                stderr="",
            )
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr("oss_crs.src.ui.subprocess.run", fake_subprocess_run)

    result = progress._check_failed_containers("proj", compose_path)

    assert result.success is False
    assert "proj-crs-codex_patcher-1 (exit 1)" in result.error


def test_docker_compose_up_ignores_helper_teardown_after_primary_exit(
    tmp_path: Path, monkeypatch
) -> None:
    progress = MultiTaskProgress(tasks=[], title="test")
    compose_path = tmp_path / "docker-compose.yml"
    compose_path.write_text(
        """
services:
  crs-codex_patcher:
    image: test
  oss-crs-exchange:
    image: test
    attach: false
    restart: on-failure:3
""".strip()
    )

    monkeypatch.setattr(
        progress,
        "run_command_with_streaming_output",
        lambda **_kwargs: TaskResult(success=True, output="compose up ok"),
    )

    class StreamingStdout:
        def __init__(self, initial_lines: list[str]):
            self._lines = initial_lines[:]
            self._closed = False
            self._condition = threading.Condition()

        def push(self, line: str) -> None:
            with self._condition:
                self._lines.append(line)
                self._condition.notify_all()

        def close(self) -> None:
            with self._condition:
                self._closed = True
                self._condition.notify_all()

        def readline(self) -> str:
            with self._condition:
                while not self._lines and not self._closed:
                    self._condition.wait(timeout=1)
                if self._lines:
                    return self._lines.pop(0)
                return ""

    class FakeProcess:
        def __init__(self, initial_lines: list[str]):
            self.stdout = StreamingStdout(initial_lines)

        def terminate(self):
            self.stdout.close()

        def wait(self, timeout=None):
            return 0

        def kill(self):
            self.stdout.close()

    process = FakeProcess(
        [
            '{"type":"container","action":"die","service":"crs-codex_patcher","timeNano":200,'
            '"attributes":{"exitCode":"0"}}\n'
        ]
    )

    monkeypatch.setattr(
        "oss_crs.src.ui.subprocess.Popen",
        lambda *args, **kwargs: process,
    )

    def fake_subprocess_run(cmd, **_kwargs):
        if cmd[-1] == "stop":
            process.stdout.push(
                '{"type":"container","action":"die","service":"oss-crs-exchange","timeNano":300,'
                '"attributes":{"exitCode":"137"}}\n'
            )
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[-2:] == ["--format", "{{.Service}}:{{.ExitCode}}:{{.Name}}"]:
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    "crs-codex_patcher:0:proj-crs-codex_patcher-1\n"
                    "oss-crs-exchange:137:proj-oss-crs-exchange-1\n"
                ),
                stderr="",
            )
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr("oss_crs.src.ui.subprocess.run", fake_subprocess_run)

    result = progress.docker_compose_up("proj", compose_path)

    assert result.success is True


def test_get_ignored_helper_exit_services_sorts_out_of_order_events() -> None:
    progress = MultiTaskProgress(tasks=[], title="test")
    event_lines = [
        '{"type":"container","action":"die","service":"oss-crs-exchange","timeNano":300,'
        '"attributes":{"exitCode":"137"}}',
        '{"type":"container","action":"die","service":"crs-codex_patcher","timeNano":200,'
        '"attributes":{"exitCode":"0"}}',
        '{"type":"container","action":"die","service":"oss-crs-exchange","timeNano":100,'
        '"attributes":{"exitCode":"1"}}',
    ]

    ignored = progress._get_ignored_helper_exit_services(
        event_lines, {"oss-crs-exchange"}
    )

    assert ignored == set()


def test_docker_compose_up_reports_helper_failure_before_primary_exit(
    tmp_path: Path, monkeypatch
) -> None:
    progress = MultiTaskProgress(tasks=[], title="test")
    compose_path = tmp_path / "docker-compose.yml"
    compose_path.write_text(
        """
services:
  crs-codex_patcher:
    image: test
  oss-crs-exchange:
    image: test
    attach: false
    restart: on-failure:3
""".strip()
    )

    monkeypatch.setattr(
        progress,
        "run_command_with_streaming_output",
        lambda **_kwargs: TaskResult(success=True, output="compose up ok"),
    )

    class FakeProcess:
        def __init__(self, lines: str):
            self.stdout = io.StringIO(lines)

        def terminate(self):
            return None

        def wait(self, timeout=None):
            return 0

        def kill(self):
            return None

    event_lines = (
        '{"type":"container","action":"die","service":"oss-crs-exchange","timeNano":100,'
        '"attributes":{"exitCode":"1"}}\n'
        '{"type":"container","action":"die","service":"crs-codex_patcher","timeNano":200,'
        '"attributes":{"exitCode":"0"}}\n'
    )

    monkeypatch.setattr(
        "oss_crs.src.ui.subprocess.Popen",
        lambda *args, **kwargs: FakeProcess(event_lines),
    )

    def fake_subprocess_run(cmd, **_kwargs):
        if cmd[-1] == "stop":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[-2:] == ["--format", "{{.Service}}:{{.ExitCode}}:{{.Name}}"]:
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    "crs-codex_patcher:0:proj-crs-codex_patcher-1\n"
                    "oss-crs-exchange:1:proj-oss-crs-exchange-1\n"
                ),
                stderr="",
            )
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr("oss_crs.src.ui.subprocess.run", fake_subprocess_run)

    result = progress.docker_compose_up("proj", compose_path)

    assert result.success is False
    assert "proj-oss-crs-exchange-1 (exit 1)" in result.error


def test_parse_compose_event_time_accepts_rfc3339_timestamp() -> None:
    progress = MultiTaskProgress(tasks=[], title="test")

    parsed = progress._parse_compose_event_time(
        {"time": "2026-03-11T03:58:14.2193853Z"}
    )

    assert parsed > 0


def test_docker_compose_up_falls_back_to_running_helpers_when_events_unavailable(
    tmp_path: Path, monkeypatch
) -> None:
    progress = MultiTaskProgress(tasks=[], title="test")
    compose_path = tmp_path / "docker-compose.yml"
    compose_path.write_text(
        """
services:
  crs-codex_patcher:
    image: test
  oss-crs-exchange:
    image: test
    attach: false
    restart: on-failure:3
""".strip()
    )

    monkeypatch.setattr(
        progress,
        "run_command_with_streaming_output",
        lambda **_kwargs: TaskResult(success=True, output="compose up ok"),
    )
    monkeypatch.setattr(
        progress,
        "_start_compose_event_collection",
        lambda *_args, **_kwargs: ([], lambda: None, False),
    )

    def fake_subprocess_run(cmd, **_kwargs):
        if cmd[-2:] == ["--format", "{{.Service}}"]:
            return SimpleNamespace(
                returncode=0,
                stdout="oss-crs-exchange\n",
                stderr="",
            )
        if cmd[-1] == "stop":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[-2:] == ["--format", "{{.Service}}:{{.ExitCode}}:{{.Name}}"]:
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    "crs-codex_patcher:0:proj-crs-codex_patcher-1\n"
                    "oss-crs-exchange:143:proj-oss-crs-exchange-1\n"
                ),
                stderr="",
            )
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr("oss_crs.src.ui.subprocess.run", fake_subprocess_run)

    result = progress.docker_compose_up("proj", compose_path)

    assert result.success is True

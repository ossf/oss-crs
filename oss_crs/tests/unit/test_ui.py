"""Unit tests for MultiTaskProgress cleanup semantics."""

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
        if cmd[:6] == ["docker", "compose", "-p", "proj", "-f", "/tmp/docker-compose.yml"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[:5] == ["docker", "image", "ls", "--filter", "label=com.docker.compose.project=proj"]:
            return SimpleNamespace(returncode=0, stdout="proj-svc-label:latest\n", stderr="")
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
                return SimpleNamespace(returncode=0, stdout="sha256:svc1 proj\n", stderr="")
            if ref == "proj-svc2:latest":
                return SimpleNamespace(returncode=0, stdout="sha256:svc2 proj\n", stderr="")
            if ref == "proj-oss-crs-litellm-key-gen:latest":
                return SimpleNamespace(returncode=0, stdout="sha256:keygen proj\n", stderr="")
            return SimpleNamespace(returncode=0, stdout="sha256:other other\n", stderr="")
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
    assert ["docker", "image", "ls", "--format", "{{.Repository}}:{{.Tag}}"] in docker_cmds
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
        if cmd[:6] == ["docker", "compose", "-p", "proj", "-f", "/tmp/docker-compose.yml"]:
            return SimpleNamespace(returncode=1, stdout="x", stderr="y")
        if cmd[:5] == ["docker", "image", "ls", "--filter", "label=com.docker.compose.project=proj"]:
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
    assert ["docker", "image", "ls", "--format", "{{.Repository}}:{{.Tag}}"] in docker_cmds


def test_docker_compose_down_adds_warning_on_image_rm_failure(monkeypatch) -> None:
    progress = MultiTaskProgress(tasks=[], title="test")
    progress._current_task = "cleanup"
    progress.task_notes["cleanup"] = []

    def fake_subprocess_run(cmd, **_kwargs):
        if cmd[:6] == ["docker", "compose", "-p", "proj", "-f", "/tmp/docker-compose.yml"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[:5] == ["docker", "image", "ls", "--filter", "label=com.docker.compose.project=proj"]:
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


def test_docker_compose_down_does_not_remove_prefix_collision_images(monkeypatch) -> None:
    progress = MultiTaskProgress(tasks=[], title="test")
    docker_cmds: list[list[str]] = []

    def fake_subprocess_run(cmd, **_kwargs):
        docker_cmds.append(cmd)
        if cmd[:6] == ["docker", "compose", "-p", "proj", "-f", "/tmp/docker-compose.yml"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[:5] == ["docker", "image", "ls", "--filter", "label=com.docker.compose.project=proj"]:
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
                return SimpleNamespace(returncode=0, stdout="sha256:owned proj\n", stderr="")
            return SimpleNamespace(returncode=0, stdout="sha256:collision someone-else\n", stderr="")
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
        if cmd[:6] == ["docker", "compose", "-p", "proj", "-f", "/tmp/docker-compose.yml"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[:5] == ["docker", "image", "ls", "--filter", "label=com.docker.compose.project=proj"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="boom")
        if cmd[:3] == ["docker", "image", "ls"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="boom")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("oss_crs.src.ui.subprocess.run", fake_subprocess_run)
    result = progress.docker_compose_down("proj", Path("/tmp/docker-compose.yml"))

    assert result.success is True
    assert any("compose-labeled images" in n for n in progress.task_notes["cleanup"])
    assert any("cleanup fallback" in n for n in progress.task_notes["cleanup"])

# SPDX-License-Identifier: MIT
"""Unit tests for the oss-crs-infra lifecycle sidecar (oss-crs-infra/lifecycle/main.py).

Verifies service monitoring, ready status emission, Docker container event handling,
and signal shutdown.
"""

import importlib.util
import json
import signal
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
LIFECYCLE_DIR = REPO_ROOT / "oss-crs-infra" / "lifecycle"


@pytest.fixture
def lifecycle(monkeypatch, tmp_path) -> ModuleType:
    """Load lifecycle module with isolated exchange root."""
    spec = importlib.util.spec_from_file_location(
        "lifecycle_main", LIFECYCLE_DIR / "main.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    exchange_root = tmp_path / "OSS_CRS_EXCHANGE_DIR"
    exchange_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(module, "EXCHANGE_ROOT", exchange_root)

    return module


def test_write_ready(lifecycle: ModuleType) -> None:
    """_write_ready writes the JSON exit_info file into EXCHANGE_ROOT/status/ready."""
    exit_info = {
        "crs-alpha": {"exit_code": 0, "status": "exited"},
        "crs-beta": {"exit_code": 137, "status": "dead"},
    }

    lifecycle._write_ready(exit_info)

    ready_file = lifecycle.EXCHANGE_ROOT / "status" / "ready"
    assert ready_file.exists()
    content = json.loads(ready_file.read_text())
    assert content == exit_info


def test_main_missing_compose_project(lifecycle: ModuleType, monkeypatch) -> None:
    """When COMPOSE_PROJECT is not set, main exits with status code 1."""
    monkeypatch.delenv("COMPOSE_PROJECT", raising=False)
    monkeypatch.setenv("WATCH_SERVICES", "crs-1")

    with pytest.raises(SystemExit) as exc_info:
        lifecycle.main()
    assert exc_info.value.code == 1


def test_main_empty_watch_services(lifecycle: ModuleType, monkeypatch) -> None:
    """When WATCH_SERVICES is empty, main exits cleanly without error."""
    monkeypatch.setenv("COMPOSE_PROJECT", "test-project")
    monkeypatch.setenv("WATCH_SERVICES", "")

    mock_client = MagicMock()
    monkeypatch.setattr("docker.from_env", lambda: mock_client)

    # Should return without exception
    lifecycle.main()

    # Docker client should not be initialized
    assert not mock_client.containers.list.called


def test_main_all_containers_already_exited(lifecycle: ModuleType, monkeypatch) -> None:
    """If all watched containers have already exited at startup, ready signal is written immediately."""
    monkeypatch.setenv("COMPOSE_PROJECT", "test-project")
    monkeypatch.setenv("WATCH_SERVICES", "crs-1, crs-2")

    mock_c1 = MagicMock()
    mock_c1.labels = {
        "com.docker.compose.project": "test-project",
        "com.docker.compose.service": "crs-1",
    }
    mock_c1.status = "exited"
    mock_c1.attrs = {"State": {"ExitCode": 0}}

    mock_c2 = MagicMock()
    mock_c2.labels = {
        "com.docker.compose.project": "test-project",
        "com.docker.compose.service": "crs-2",
    }
    mock_c2.status = "dead"
    mock_c2.attrs = {"State": {"ExitCode": 1}}

    mock_client = MagicMock()
    mock_client.containers.list.return_value = [mock_c1, mock_c2]
    monkeypatch.setattr("docker.from_env", lambda: mock_client)

    lifecycle.main()

    ready_file = lifecycle.EXCHANGE_ROOT / "status" / "ready"
    assert ready_file.exists()
    data = json.loads(ready_file.read_text())
    assert data["crs-1"] == {"exit_code": 0, "status": "exited"}
    assert data["crs-2"] == {"exit_code": 1, "status": "dead"}
    # Event loop should not be called
    assert not mock_client.events.called


def test_main_dynamic_event_stream(lifecycle: ModuleType, monkeypatch) -> None:
    """When containers exit dynamically, events are consumed and ready signal is emitted."""
    monkeypatch.setenv("COMPOSE_PROJECT", "test-project")
    monkeypatch.setenv("WATCH_SERVICES", "crs-1, crs-2")

    # One container exited, one still running
    mock_c1 = MagicMock()
    mock_c1.labels = {
        "com.docker.compose.project": "test-project",
        "com.docker.compose.service": "crs-1",
    }
    mock_c1.status = "exited"
    mock_c1.attrs = {"State": {"ExitCode": 0}}

    mock_c2 = MagicMock()
    mock_c2.labels = {
        "com.docker.compose.project": "test-project",
        "com.docker.compose.service": "crs-2",
    }
    mock_c2.status = "running"
    mock_c2.attrs = {"State": {"ExitCode": 0}}

    mock_client = MagicMock()
    mock_client.containers.list.return_value = [mock_c1, mock_c2]

    # Events yield die event for crs-2
    mock_event = {
        "Action": "die",
        "Actor": {
            "Attributes": {
                "com.docker.compose.service": "crs-2",
                "exitCode": "0",
            }
        },
    }
    mock_client.events.return_value = [mock_event]
    monkeypatch.setattr("docker.from_env", lambda: mock_client)

    lifecycle.main()

    ready_file = lifecycle.EXCHANGE_ROOT / "status" / "ready"
    assert ready_file.exists()
    data = json.loads(ready_file.read_text())
    assert data["crs-1"]["status"] == "exited"
    assert data["crs-2"]["status"] == "exited"


def test_main_handles_container_list_exception(
    lifecycle: ModuleType, monkeypatch
) -> None:
    """If client.containers.list raises an exception, main continues to listen to events."""
    monkeypatch.setenv("COMPOSE_PROJECT", "test-project")
    monkeypatch.setenv("WATCH_SERVICES", "crs-1")

    mock_client = MagicMock()
    mock_client.containers.list.side_effect = Exception("Docker daemon busy")

    mock_event = {
        "Action": "die",
        "Actor": {
            "Attributes": {
                "com.docker.compose.service": "crs-1",
                "exitCode": "137",
            }
        },
    }
    mock_client.events.return_value = [mock_event]
    monkeypatch.setattr("docker.from_env", lambda: mock_client)

    lifecycle.main()

    ready_file = lifecycle.EXCHANGE_ROOT / "status" / "ready"
    assert ready_file.exists()
    data = json.loads(ready_file.read_text())
    assert data["crs-1"]["exit_code"] == 137


def test_main_signal_shutdown(lifecycle: ModuleType, monkeypatch) -> None:
    """Signal shutdown stops event processing gracefully."""
    monkeypatch.setenv("COMPOSE_PROJECT", "test-project")
    monkeypatch.setenv("WATCH_SERVICES", "crs-1")

    mock_client = MagicMock()
    mock_client.containers.list.return_value = []  # crs-1 still running

    signals_registered = {}

    def fake_signal(sig, handler):
        signals_registered[sig] = handler

    monkeypatch.setattr(signal, "signal", fake_signal)

    # Event generator that triggers shutdown signal on first iteration
    def event_stream(*args, **kwargs):
        handler = signals_registered[signal.SIGTERM]
        handler(signal.SIGTERM, None)
        yield {
            "Action": "die",
            "Actor": {
                "Attributes": {
                    "com.docker.compose.service": "unrelated",
                }
            },
        }

    mock_client.events.side_effect = event_stream
    monkeypatch.setattr("docker.from_env", lambda: mock_client)

    lifecycle.main()

    # Ready file should NOT be written because crs-1 is still remaining
    ready_file = lifecycle.EXCHANGE_ROOT / "status" / "ready"
    assert not ready_file.exists()

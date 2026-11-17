# SPDX-License-Identifier: MIT
"""Tests for the webui server endpoints and run_id validation."""

import importlib.util
from pathlib import Path
from types import ModuleType
import urllib.parse

import pytest
from starlette.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[3]
WEBUI_DIR = REPO_ROOT / "oss-crs-infra" / "webui"


@pytest.fixture()
def webui_module(tmp_path: Path) -> ModuleType:
    # Use isolated LOG_DIR and WORKDIR
    log_dir = tmp_path / "webui_logs"
    workdir = tmp_path / "workdir"
    log_dir.mkdir(parents=True)
    workdir.mkdir(parents=True)

    spec = importlib.util.spec_from_file_location("webui_main", WEBUI_DIR / "main.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    setattr(module, "LOG_DIR", log_dir)
    setattr(module, "WORKDIR", workdir)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def client(webui_module: ModuleType) -> TestClient:
    return TestClient(webui_module.app)


def test_health(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_register_and_get_status_valid_run_id(client: TestClient) -> None:
    valid_id = "run-123_test"
    register_payload = {
        "target": "target-a",
        "crs_names": ["crs-1", "crs-2"],
        "crs_resources": {},
        "harness": "fuzz_target",
    }
    resp = client.post(f"/api/runs/{valid_id}/register", json=register_payload)
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}

    status_resp = client.get(f"/api/runs/{valid_id}/status")
    assert status_resp.status_code == 200
    data = status_resp.json()
    assert data["run_id"] == valid_id
    assert data["target"] == "target-a"
    assert data["status"] == "live"


@pytest.mark.parametrize(
    "invalid_id",
    [
        "run;injection",
        "run*glob",
        "run space",
        "run$bad",
        "run|pipe",
        "run!exclamation",
        "run`backtick`",
    ],
)
def test_endpoints_reject_invalid_run_id(client: TestClient, invalid_id: str) -> None:
    encoded = urllib.parse.quote(invalid_id, safe="")
    resp = client.post(f"/api/runs/{encoded}/register", json={})
    assert resp.status_code == 400
    assert "Invalid run_id" in resp.text

    resp = client.get(f"/api/runs/{encoded}/status")
    assert resp.status_code == 400
    assert "Invalid run_id" in resp.text

    resp = client.get(f"/api/runs/{encoded}/history")
    assert resp.status_code == 400
    assert "Invalid run_id" in resp.text

    resp = client.get(f"/api/runs/{encoded}/artifacts")
    assert resp.status_code == 400
    assert "Invalid run_id" in resp.text

    resp = client.get(f"/api/runs/{encoded}/artifacts/povs")
    assert resp.status_code == 400
    assert "Invalid run_id" in resp.text

    resp = client.get(f"/api/runs/{encoded}/artifacts/povs/download?name=test")
    assert resp.status_code == 400
    assert "Invalid run_id" in resp.text

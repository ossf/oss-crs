# SPDX-License-Identifier: MIT
"""Unit tests for the oss-crs-infra webui server (oss-crs-infra/webui/main.py).

Verifies telemetry registration, snapshot ingestion, run finalization,
metric history streaming, artifact discovery, rehydration, and dashboard rendering.
"""

import importlib.util
import json
import time
from pathlib import Path
from types import ModuleType

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[3]
WEBUI_DIR = REPO_ROOT / "oss-crs-infra" / "webui"


@pytest.fixture
def webui(monkeypatch, tmp_path) -> ModuleType:
    """Load webui module with isolated directories and cleared in-memory state."""
    spec = importlib.util.spec_from_file_location("webui_main", WEBUI_DIR / "main.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    log_dir = tmp_path / "webui_logs"
    work_dir = tmp_path / "workdir"
    log_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(module, "LOG_DIR", log_dir)
    monkeypatch.setattr(module, "WORKDIR", work_dir)

    # Clear run registry
    module._runs.clear()

    return module


@pytest.fixture
def client(webui: ModuleType) -> TestClient:
    """Return a TestClient against the webui FastAPI app."""
    return TestClient(webui.app)


def test_health_check(client: TestClient) -> None:
    """GET /health returns 200 with status ok."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_register_run(client: TestClient, webui: ModuleType) -> None:
    """POST /api/runs/{run_id}/register registers run state with metadata."""
    payload = {
        "target": "mock-c",
        "crs_names": ["crs-libfuzzer"],
        "crs_resources": {"crs-libfuzzer": {"cpus": 4, "memory": "8G"}},
        "harness": "fuzz_parse_buffer",
    }
    resp = client.post("/api/runs/run-001/register", json=payload)
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}

    assert "run-001" in webui._runs
    run = webui._runs["run-001"]
    assert run.target == "mock-c"
    assert run.crs_names == ["crs-libfuzzer"]
    assert run.status() == "live"


def test_receive_snapshot_and_history(client: TestClient, webui: ModuleType) -> None:
    """POST /api/runs/{run_id}/snapshot records telemetry snapshots and updates latest state."""
    # Register first
    client.post(
        "/api/runs/run-002/register",
        json={"target": "mock-c", "crs_names": ["crs-libfuzzer"]},
    )

    snapshot_1 = {
        "elapsed_seconds": 10,
        "per_crs": {"crs-libfuzzer": {"povs": 1, "seeds": 5}},
        "cost": {"total": 0.05},
    }
    resp1 = client.post("/api/runs/run-002/snapshot", json=snapshot_1)
    assert resp1.status_code == 200

    snapshot_2 = {
        "elapsed_seconds": 20,
        "per_crs": {"crs-libfuzzer": {"povs": 2, "seeds": 10}},
        "cost": {"total": 0.12},
    }
    client.post("/api/runs/run-002/snapshot", json=snapshot_2)

    # Check status
    status_resp = client.get("/api/runs/run-002/status")
    assert status_resp.status_code == 200
    status_data = status_resp.json()
    assert status_data["run_id"] == "run-002"
    assert status_data["status"] == "live"
    assert status_data["per_crs"]["crs-libfuzzer"]["povs"] == 2

    # Check history
    history_resp = client.get("/api/runs/run-002/history")
    assert history_resp.status_code == 200
    history = history_resp.json()
    assert len(history) == 2
    assert history[0]["elapsed_seconds"] == 10
    assert history[1]["elapsed_seconds"] == 20


def test_finalize_run(client: TestClient, webui: ModuleType) -> None:
    """POST /api/runs/{run_id}/finalize merges authoritative metrics and marks run finished."""
    client.post(
        "/api/runs/run-003/register",
        json={"target": "mock-c", "crs_names": ["crs-libfuzzer"]},
    )
    client.post(
        "/api/runs/run-003/snapshot",
        json={"elapsed_seconds": 30, "per_crs": {"crs-libfuzzer": {"povs": 1}}},
    )

    finalize_payload = {
        "outcome": "success",
        "per_crs": {"crs-libfuzzer": {"povs": 3, "patches": 1}},
        "cost": {"total": 1.25},
    }
    resp = client.post("/api/runs/run-003/finalize", json=finalize_payload)
    assert resp.status_code == 200

    run = webui._runs["run-003"]
    assert run.done is True
    assert run.outcome == "success"
    assert run.status() == "finished"
    assert run.latest["per_crs"]["crs-libfuzzer"]["povs"] == 3


def test_list_runs(client: TestClient) -> None:
    """GET /api/runs lists all active and completed runs with headline metrics."""
    client.post(
        "/api/runs/run-A/register", json={"target": "target-A", "crs_names": ["crs1"]}
    )
    client.post(
        "/api/runs/run-A/snapshot",
        json={"per_crs": {"crs1": {"povs": 2, "seeds": 4, "patches": 1}}},
    )

    resp = client.get("/api/runs")
    assert resp.status_code == 200
    runs = resp.json()
    assert len(runs) >= 1
    run_a = next(r for r in runs if r["run_id"] == "run-A")
    assert run_a["target"] == "target-A"
    assert run_a["povs"] == 2
    assert run_a["seeds"] == 4
    assert run_a["patches"] == 1


def test_run_not_found(client: TestClient) -> None:
    """Unknown run IDs return 404 in status and history endpoints."""
    assert client.get("/api/runs/nonexistent/status").status_code == 404
    assert client.get("/api/runs/nonexistent/history").status_code == 404


def test_rehydrate_from_disk(webui: ModuleType, monkeypatch) -> None:
    """_rehydrate restores run state and jsonl snapshots from LOG_DIR."""
    meta = {
        "run_id": "run-persisted",
        "target": "target-rehydrate",
        "crs_names": ["crs-fuzz"],
        "crs_resources": {},
        "harness": "fuzz_target",
        "registered_at": time.time() - 100,
        "last_snapshot_at": time.time() - 50,
        "done": True,
        "outcome": "timeout",
    }
    meta_path = webui.LOG_DIR / "run-persisted.meta.json"
    meta_path.write_text(json.dumps(meta))

    snapshot_data = [
        {"elapsed_seconds": 10, "per_crs": {"crs-fuzz": {"povs": 1}}},
        {"elapsed_seconds": 20, "per_crs": {"crs-fuzz": {"povs": 2}}},
    ]
    jsonl_path = webui.LOG_DIR / "run-persisted.jsonl"
    jsonl_path.write_text("\n".join(json.dumps(s) for s in snapshot_data) + "\n")

    webui._runs.clear()
    webui._rehydrate()

    assert "run-persisted" in webui._runs
    run = webui._runs["run-persisted"]
    assert run.target == "target-rehydrate"
    assert run.done is True
    assert run.outcome == "timeout"
    assert len(run.history) == 2
    assert run.latest["per_crs"]["crs-fuzz"]["povs"] == 2


def test_artifact_discovery_and_download(client: TestClient, webui: ModuleType) -> None:
    """Artifact endpoints discover and serve files from the workdir hierarchy."""
    run_id = "run-artifacts"
    # Create expected artifact directory structure:
    # WORKDIR/crs_compose/<compose_hash>/<sanitizer>/runs/<run_id>/EXCHANGE_DIR/<target>/<harness>/<type>/
    artifact_dir = (
        webui.WORKDIR
        / "crs_compose"
        / "hash123"
        / "address"
        / "runs"
        / run_id
        / "EXCHANGE_DIR"
        / "mock_target"
        / "harness1"
        / "povs"
    )
    artifact_dir.mkdir(parents=True)
    pov_file = artifact_dir / "crash_sample.bin"
    pov_file.write_bytes(b"crash_artifact_payload")

    # Counts endpoint
    counts_resp = client.get(f"/api/runs/{run_id}/artifacts")
    assert counts_resp.status_code == 200
    assert counts_resp.json()["counts"]["povs"] == 1
    assert counts_resp.json()["counts"]["patches"] == 0

    # List endpoint
    list_resp = client.get(f"/api/runs/{run_id}/artifacts/povs")
    assert list_resp.status_code == 200
    data = list_resp.json()
    assert len(data["artifacts"]) == 1
    assert data["artifacts"][0]["name"] == "crash_sample.bin"
    assert data["artifacts"][0]["size"] == len(b"crash_artifact_payload")

    # Download endpoint
    download_resp = client.get(
        f"/api/runs/{run_id}/artifacts/povs/download?name=crash_sample.bin"
    )
    assert download_resp.status_code == 200
    assert download_resp.content == b"crash_artifact_payload"

    # 404 on missing artifact
    assert (
        client.get(
            f"/api/runs/{run_id}/artifacts/povs/download?name=missing.bin"
        ).status_code
        == 404
    )
    # 404 on invalid artifact type
    assert client.get(f"/api/runs/{run_id}/artifacts/unknown_type").status_code == 404


def test_dashboard_html_pages(client: TestClient, webui: ModuleType) -> None:
    """Dashboard HTML endpoints render successfully."""
    client.post(
        "/api/runs/run-html/register",
        json={"target": "mock-c", "crs_names": ["crs1"]},
    )

    # Main dashboard
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]

    # Run detail page
    resp_detail = client.get("/run/run-html")
    assert resp_detail.status_code == 200
    assert "mock-c" in resp_detail.text

    # Missing run detail page
    resp_missing = client.get("/run/missing-run")
    assert resp_missing.status_code == 404

    # Artifacts HTML page
    resp_art = client.get("/run/run-html/artifacts/povs")
    assert resp_art.status_code == 200

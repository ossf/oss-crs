"""Unit tests for runner-sidecar server.py.

Uses mocked subprocess and filesystem -- no real base-runner image or reproduce binary required.
Runner-sidecar code lives outside the oss_crs package tree; sys.path insertion
is required before any imports from the sidecar.

Note: both builder-sidecar and runner-sidecar have a module named ``server``.
To prevent sys.modules cache collisions when both test files run in the same
pytest session, we load runner_server via importlib with an explicit module
name ``runner_server``, never touching the cached ``server`` entry that may
belong to builder-sidecar.
"""

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
import subprocess

import pytest

# ---------------------------------------------------------------------------
# Module loading: load runner-sidecar server.py under alias "runner_server"
# so it does not collide with builder-sidecar's "server" in sys.modules.
#
# We do NOT modify sys.path here: runner-sidecar/server.py has no local
# relative imports, so importlib can load it by absolute file path without
# adding the runner-sidecar directory to sys.path.  Avoiding sys.path
# mutation prevents order-sensitive collisions when both test files run in
# the same pytest session.
# ---------------------------------------------------------------------------

_RUNNER_DIR = Path(__file__).resolve().parent.parent.parent.parent / "oss-crs-infra" / "runner-sidecar"
_RUNNER_SERVER_PATH = _RUNNER_DIR / "server.py"

# Load the module under a collision-safe alias.
_spec = importlib.util.spec_from_file_location("runner_server", _RUNNER_SERVER_PATH)
runner_server = importlib.util.module_from_spec(_spec)
sys.modules["runner_server"] = runner_server
_spec.loader.exec_module(runner_server)


# ---------------------------------------------------------------------------
# Helper: create a patched test client for runner-sidecar
# ---------------------------------------------------------------------------

def _make_test_client():
    """Return a TestClient for the runner-sidecar app."""
    from fastapi.testclient import TestClient
    return TestClient(runner_server.app)


# ===========================================================================
# TestHealthEndpoint
# ===========================================================================

class TestHealthEndpoint:
    """Tests for GET /health."""

    def test_health_returns_ok(self):
        client = _make_test_client()
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


# ===========================================================================
# TestRunPOVEndpoint
# ===========================================================================

class TestRunPOVEndpoint:
    """Tests for POST /run-pov endpoint (API-02)."""

    @patch("runner_server.subprocess.run")
    @patch("runner_server.os.path.exists", return_value=True)
    def test_successful_pov_returns_exit_code(self, mock_exists, mock_run):
        """Successful reproduce returns exit_code and stderr."""
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        client = _make_test_client()
        response = client.post("/run-pov", data={
            "harness_name": "my_fuzzer",
            "build_id": "build123",
            "pov_path": "/povs/crash.bin",
        })
        assert response.status_code == 200
        body = response.json()
        assert body["exit_code"] == 0
        assert "stderr" in body

    @patch("runner_server.subprocess.run")
    @patch("runner_server.os.path.exists", return_value=True)
    def test_crash_returns_nonzero(self, mock_exists, mock_run):
        """Non-zero exit code from reproduce indicates crash (POV-03)."""
        mock_run.return_value = MagicMock(returncode=1, stderr="SUMMARY: AddressSanitizer: heap-buffer-overflow")
        client = _make_test_client()
        response = client.post("/run-pov", data={
            "harness_name": "my_fuzzer",
            "build_id": "build123",
            "pov_path": "/povs/crash.bin",
        })
        body = response.json()
        assert body["exit_code"] == 1
        assert "AddressSanitizer" in body["stderr"]

    @patch("runner_server.subprocess.run")
    @patch("runner_server.os.path.exists", return_value=True)
    def test_timeout_returns_124(self, mock_exists, mock_run):
        """Timeout from subprocess returns exit_code=124."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="reproduce", timeout=30)
        client = _make_test_client()
        response = client.post("/run-pov", data={
            "harness_name": "my_fuzzer",
            "build_id": "build123",
            "pov_path": "/povs/crash.bin",
        })
        body = response.json()
        assert body["exit_code"] == 124
        assert "timed out" in body["stderr"].lower() or "timeout" in body["stderr"].lower()

    @patch("runner_server.os.path.exists")
    def test_missing_harness_returns_404(self, mock_exists):
        """Harness binary not found returns 404."""
        # First call (harness check) returns False, second call (pov check) irrelevant
        mock_exists.return_value = False
        client = _make_test_client()
        response = client.post("/run-pov", data={
            "harness_name": "nonexistent_fuzzer",
            "build_id": "build123",
            "pov_path": "/povs/crash.bin",
        })
        assert response.status_code == 404
        assert "error" in response.json()

    @patch("runner_server.os.path.exists")
    def test_missing_pov_returns_404(self, mock_exists):
        """POV binary not found returns 404."""
        # harness exists, pov does not
        mock_exists.side_effect = lambda p: p != "/povs/crash.bin"
        client = _make_test_client()
        response = client.post("/run-pov", data={
            "harness_name": "my_fuzzer",
            "build_id": "build123",
            "pov_path": "/povs/crash.bin",
        })
        assert response.status_code == 404
        assert "error" in response.json()


# ===========================================================================
# TestRunPOV (subprocess invocation details)
# ===========================================================================

class TestRunPOV:
    """Tests for POV reproduce script invocation details (POV-01, POV-02)."""

    @patch("runner_server.subprocess.run")
    @patch("runner_server.os.path.exists", return_value=True)
    def test_invokes_reproduce_with_harness_name(self, mock_exists, mock_run):
        """reproduce is called with the harness_name as argument (POV-01)."""
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        client = _make_test_client()
        client.post("/run-pov", data={
            "harness_name": "my_fuzzer",
            "build_id": "build123",
            "pov_path": "/povs/crash.bin",
        })
        mock_run.assert_called_once()
        call_args = mock_run.call_args
        cmd = call_args[0][0] if call_args[0] else call_args[1].get("args")
        assert cmd == ["/usr/local/bin/reproduce", "my_fuzzer"]

    @patch("runner_server.subprocess.run")
    @patch("runner_server.os.path.exists", return_value=True)
    def test_uses_out_dir(self, mock_exists, mock_run):
        """OUT env var is set to the configured out directory (POV-02)."""
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        client = _make_test_client()
        with patch.dict("os.environ", {"OUT_DIR": "/custom/out"}):
            client.post("/run-pov", data={
                "harness_name": "my_fuzzer",
                "build_id": "build123",
                "pov_path": "/povs/crash.bin",
            })
        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["env"]["OUT"] == "/custom/out"

    @patch("runner_server.subprocess.run")
    @patch("runner_server.os.path.exists", return_value=True)
    def test_sets_testcase_env(self, mock_exists, mock_run):
        """TESTCASE env var is set to pov_path."""
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        client = _make_test_client()
        client.post("/run-pov", data={
            "harness_name": "my_fuzzer",
            "build_id": "build123",
            "pov_path": "/povs/crash.bin",
        })
        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["env"]["TESTCASE"] == "/povs/crash.bin"

    @patch("runner_server.subprocess.run")
    @patch("runner_server.os.path.exists", return_value=True)
    def test_sets_fuzzer_args_default(self, mock_exists, mock_run):
        """FUZZER_ARGS defaults to OSS-Fuzz standard values."""
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        client = _make_test_client()
        client.post("/run-pov", data={
            "harness_name": "my_fuzzer",
            "build_id": "build123",
            "pov_path": "/povs/crash.bin",
        })
        call_kwargs = mock_run.call_args[1]
        assert "FUZZER_ARGS" in call_kwargs["env"]
        assert "-rss_limit_mb=2560" in call_kwargs["env"]["FUZZER_ARGS"]

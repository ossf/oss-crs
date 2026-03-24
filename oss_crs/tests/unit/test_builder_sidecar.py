"""Unit tests for builder-sidecar docker_ops.py and server.py.

Uses fully mocked Docker SDK — no real Docker daemon required.
Sidecar code lives outside the oss_crs package tree; sys.path insertion
is required before any imports from the sidecar.
"""

import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# sys.path setup: must precede any sidecar imports
# ---------------------------------------------------------------------------

_SIDECAR_DIR = Path(__file__).resolve().parent.parent.parent.parent / "oss-crs-infra" / "builder-sidecar"
sys.path.insert(0, str(_SIDECAR_DIR))

# ---------------------------------------------------------------------------
# Standard library / test framework imports
# ---------------------------------------------------------------------------

import hashlib
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# docker.errors must be importable for side-effect setup before importing
# the sidecar modules (which also import docker at module level)
# ---------------------------------------------------------------------------

import docker
import docker.errors

# ---------------------------------------------------------------------------
# Sidecar module imports
# ---------------------------------------------------------------------------

from docker_ops import get_build_image, get_test_image, next_version, run_ephemeral_build, run_ephemeral_test

# ---------------------------------------------------------------------------
# Test-wide builder name constant
# ---------------------------------------------------------------------------

TEST_BUILDER_NAME = "test-builder"


# ---------------------------------------------------------------------------
# Helper: create a patched server app for endpoint testing
# ---------------------------------------------------------------------------

def _make_test_client():
    """Return a TestClient for the server app with a clean job_results dict."""
    from fastapi.testclient import TestClient
    import server

    # Reset global state between tests so job_results don't bleed across tests
    server.job_results.clear()

    return TestClient(server.app)


# ===========================================================================
# TestNextVersion
# ===========================================================================

class TestNextVersion:
    """Tests for docker_ops.next_version() filesystem version counter."""

    def test_nonexistent_dir_returns_1(self, tmp_path):
        assert next_version(tmp_path / "nonexistent") == 1

    def test_empty_dir_returns_1(self, tmp_path):
        assert next_version(tmp_path) == 1

    def test_with_v1_returns_2(self, tmp_path):
        (tmp_path / "v1").mkdir()
        assert next_version(tmp_path) == 2

    def test_with_v1_v2_v3_returns_4(self, tmp_path):
        for v in ["v1", "v2", "v3"]:
            (tmp_path / v).mkdir()
        assert next_version(tmp_path) == 4

    def test_ignores_non_version_dirs(self, tmp_path):
        (tmp_path / "v1").mkdir()
        (tmp_path / "temp").mkdir()
        (tmp_path / "latest").mkdir()
        (tmp_path / "v2").mkdir()
        # Only v1 and v2 are valid; max is 2, so next is 3
        assert next_version(tmp_path) == 3


# ===========================================================================
# TestGetBuildImage
# ===========================================================================

class TestGetBuildImage:
    """Tests for docker_ops.get_build_image() snapshot lookup."""

    def test_returns_snapshot_when_exists(self):
        client = MagicMock()
        # images.get returns successfully (no exception) => snapshot exists
        client.images.get.return_value = MagicMock()
        result = get_build_image(client, "abc123", "base:latest", TEST_BUILDER_NAME)
        assert result == f"oss-crs-snapshot:build-{TEST_BUILDER_NAME}-abc123"
        client.images.get.assert_called_once_with(f"oss-crs-snapshot:build-{TEST_BUILDER_NAME}-abc123")

    def test_returns_base_when_not_found(self):
        client = MagicMock()
        client.images.get.side_effect = docker.errors.ImageNotFound("not found")
        result = get_build_image(client, "abc123", "base:latest", TEST_BUILDER_NAME)
        assert result == "base:latest"


# ===========================================================================
# TestRunEphemeralBuild
# ===========================================================================

class TestRunEphemeralBuild:
    """Tests for docker_ops.run_ephemeral_build() container lifecycle.

    Each test patches docker.from_env (called inside run_ephemeral_build) to
    return a fully mock Docker client with a mock container.
    """

    def _make_mock_client(self, exit_code=0, snapshot_exists=False):
        """Return (client_mock, container_mock) with configurable exit code and snapshot state."""
        client = MagicMock()
        container = MagicMock()

        container.wait.return_value = {"StatusCode": exit_code}
        # logs() is called twice: once for stdout, once for stderr
        container.logs.return_value = b"build output"

        client.containers.create.return_value = container

        if snapshot_exists:
            client.images.get.return_value = MagicMock()
        else:
            client.images.get.side_effect = docker.errors.ImageNotFound("nope")

        return client, container

    def test_successful_build_returns_exit_code_0(self, tmp_path):
        client, _ = self._make_mock_client(exit_code=0)
        with patch("docker_ops.docker.from_env", return_value=client):
            result = run_ephemeral_build("base:latest", "build123", TEST_BUILDER_NAME, b"patch", tmp_path)
        assert result["exit_code"] == 0
        assert result["build_id"] == "build123"

    def test_first_success_commits_snapshot(self, tmp_path):
        """First successful build with no existing snapshot: container.commit() called."""
        client, container = self._make_mock_client(exit_code=0, snapshot_exists=False)
        with patch("docker_ops.docker.from_env", return_value=client):
            run_ephemeral_build("base:latest", "build123", TEST_BUILDER_NAME, b"patch", tmp_path)
        container.commit.assert_called_once_with(
            repository="oss-crs-snapshot",
            tag=f"build-{TEST_BUILDER_NAME}-build123",
        )

    def test_existing_snapshot_skips_commit(self, tmp_path):
        """Snapshot already exists: container.commit() must NOT be called."""
        client, container = self._make_mock_client(exit_code=0, snapshot_exists=True)
        with patch("docker_ops.docker.from_env", return_value=client):
            run_ephemeral_build("base:latest", "build123", TEST_BUILDER_NAME, b"patch", tmp_path)
        container.commit.assert_not_called()

    def test_failed_build_no_commit(self, tmp_path):
        """Non-zero exit code: snapshot must NOT be committed."""
        client, container = self._make_mock_client(exit_code=1, snapshot_exists=False)
        with patch("docker_ops.docker.from_env", return_value=client):
            result = run_ephemeral_build("base:latest", "build123", TEST_BUILDER_NAME, b"patch", tmp_path)
        assert result["exit_code"] == 1
        container.commit.assert_not_called()

    def test_container_removed_on_success(self, tmp_path):
        """Container.remove(force=True) called after successful build."""
        client, container = self._make_mock_client(exit_code=0)
        with patch("docker_ops.docker.from_env", return_value=client):
            run_ephemeral_build("base:latest", "build123", TEST_BUILDER_NAME, b"patch", tmp_path)
        container.remove.assert_called_once_with(force=True)

    def test_container_removed_on_failure(self, tmp_path):
        """Container.remove(force=True) called even when build fails."""
        client, container = self._make_mock_client(exit_code=1)
        with patch("docker_ops.docker.from_env", return_value=client):
            run_ephemeral_build("base:latest", "build123", TEST_BUILDER_NAME, b"patch", tmp_path)
        container.remove.assert_called_once_with(force=True)

    def test_timeout_returns_error(self, tmp_path):
        """ReadTimeout during container.wait results in exit_code=1 with timeout message."""
        import requests.exceptions
        client, container = self._make_mock_client(exit_code=0)
        container.wait.side_effect = requests.exceptions.ReadTimeout()
        with patch("docker_ops.docker.from_env", return_value=client):
            result = run_ephemeral_build("base:latest", "build123", TEST_BUILDER_NAME, b"patch", tmp_path, timeout=1)
        assert result["exit_code"] == 1
        assert "timed out" in result["stderr"].lower() or "timeout" in result["stderr"].lower()

    def test_version_override(self, tmp_path):
        """When version_override provided, artifacts_version reflects the override string."""
        client, _ = self._make_mock_client(exit_code=0)
        with patch("docker_ops.docker.from_env", return_value=client):
            result = run_ephemeral_build(
                "base:latest", "build123", TEST_BUILDER_NAME, b"patch", tmp_path, version_override="final"
            )
        assert result["artifacts_version"] == "final"

    def test_creates_version_directory(self, tmp_path):
        """On success, a versioned directory is created under rebuild_out_dir."""
        client, _ = self._make_mock_client(exit_code=0)
        with patch("docker_ops.docker.from_env", return_value=client):
            result = run_ephemeral_build("base:latest", "build123", TEST_BUILDER_NAME, b"patch", tmp_path)
        version_str = result["artifacts_version"]
        assert (tmp_path / version_str).exists()

    def test_failed_build_does_not_create_version_directory(self, tmp_path):
        """ART-02: Failed builds must not consume version slots."""
        client, _ = self._make_mock_client(exit_code=1)
        with patch("docker_ops.docker.from_env", return_value=client):
            run_ephemeral_build("base:latest", "build123", TEST_BUILDER_NAME, b"patch", tmp_path)
        # No v{N} directories should exist in tmp_path
        version_re = __import__("re").compile(r"^v(\d+)$")
        versioned_dirs = [
            d for d in tmp_path.iterdir()
            if d.is_dir() and version_re.match(d.name)
        ]
        assert versioned_dirs == []


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
# TestBuildEndpoint
# ===========================================================================

class TestBuildEndpoint:
    """Tests for POST /build endpoint."""

    def _patch_bytes(self, content: bytes = b"diff --git a/foo"):
        """Return a (files, data) tuple suitable for TestClient multipart POST."""
        return (
            {"patch": ("patch.diff", content, "application/octet-stream")},
            {"build_id": "testbuild", "builder_name": TEST_BUILDER_NAME, "version": "v1"},
        )

    def test_submit_build_returns_queued(self):
        """POST /build returns 200 with status 'queued' and a job id."""
        client = _make_test_client()
        files, data = self._patch_bytes()
        response = client.post("/build", files=files, data=data)
        assert response.status_code == 200
        body = response.json()
        assert "id" in body
        assert body["status"] == "queued"

    def test_build_id_is_deterministic(self):
        """Same patch content produces same job_id on repeated calls."""
        patch_content = b"diff content here"
        client = _make_test_client()
        files1 = {"patch": ("p.diff", patch_content, "application/octet-stream")}
        data = {"build_id": "testbuild", "builder_name": TEST_BUILDER_NAME}

        resp1 = client.post("/build", files=files1, data=data)
        resp2 = client.post("/build", files={"patch": ("p.diff", patch_content, "application/octet-stream")}, data=data)
        assert resp1.json()["id"] == resp2.json()["id"]

    def test_dedup_returns_existing_status(self):
        """Second POST with same patch content returns the existing job entry (dedup)."""
        client = _make_test_client()
        patch_content = b"unique patch abc"
        files = {"patch": ("patch.diff", patch_content, "application/octet-stream")}
        data = {"build_id": "myproj", "builder_name": TEST_BUILDER_NAME}

        resp1 = client.post("/build", files=files, data=data)
        resp2 = client.post("/build", files={"patch": ("patch.diff", patch_content, "application/octet-stream")}, data=data)

        # Both calls return the same job id
        assert resp1.json()["id"] == resp2.json()["id"]

    def test_build_id_uses_sha256(self):
        """Build ID is a 12-char hex prefix of SHA256(project:sanitizer:patch)."""
        import os
        import server

        patch_content = b"my patch bytes"
        project = os.environ.get("PROJECT_NAME", "unknown")
        sanitizer = os.environ.get("SANITIZER", "unknown")
        hasher = hashlib.sha256()
        hasher.update(f"{project}:{sanitizer}:".encode())
        hasher.update(patch_content)
        expected_id = hasher.hexdigest()[:12]

        client = _make_test_client()
        files = {"patch": ("patch.diff", patch_content, "application/octet-stream")}
        data = {"build_id": "build1", "builder_name": TEST_BUILDER_NAME}
        resp = client.post("/build", files=files, data=data)
        assert resp.json()["id"] == expected_id

    def test_get_builds_returns_list(self):
        """GET /builds returns a dict with a 'builds' key containing job id list."""
        client = _make_test_client()
        files = {"patch": ("patch.diff", b"some patch", "application/octet-stream")}
        data = {"build_id": "proj1", "builder_name": TEST_BUILDER_NAME}
        client.post("/build", files=files, data=data)

        resp = client.get("/builds")
        assert resp.status_code == 200
        body = resp.json()
        assert "builds" in body
        assert isinstance(body["builds"], list)
        assert len(body["builds"]) >= 1


# ===========================================================================
# TestStatusEndpoint
# ===========================================================================

class TestStatusEndpoint:
    """Tests for GET /status/{id}."""

    def test_unknown_job_returns_404(self):
        client = _make_test_client()
        response = client.get("/status/nonexistent-job-id-xyz")
        assert response.status_code == 404

    def test_404_contains_error_key(self):
        client = _make_test_client()
        response = client.get("/status/nonexistent-job-id-xyz")
        body = response.json()
        assert "error" in body

    def test_returns_result_when_done(self):
        """When job_results contains a done entry, /status returns it."""
        import server

        client = _make_test_client()
        # Manually insert a completed result (simulates worker finishing)
        fake_result = {
            "id": "abc123",
            "status": "done",
            "result": {
                "exit_code": 0,
                "stdout": "ok",
                "stderr": "",
                "build_id": "abc123",
                "artifacts_version": "v1",
            },
        }
        server.job_results["abc123"] = fake_result

        response = client.get("/status/abc123")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "done"

    def test_returns_queued_status(self):
        """When job is queued, /status returns queued status."""
        import server

        client = _make_test_client()
        server.job_results["queuedjob"] = {"id": "queuedjob", "status": "queued"}

        response = client.get("/status/queuedjob")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "queued"


# ===========================================================================
# TestStubEndpoints
# ===========================================================================

class TestStubEndpoints:
    """Tests for stub endpoints (run-pov remains stub; /test is now implemented)."""

    def test_run_pov_returns_501(self):
        client = _make_test_client()
        response = client.post("/run-pov")
        assert response.status_code == 501

    def test_run_pov_has_error_key(self):
        client = _make_test_client()
        response = client.post("/run-pov")
        assert "error" in response.json()


# ===========================================================================
# TestGetTestImage
# ===========================================================================

class TestGetTestImage:
    """Tests for docker_ops.get_test_image() test snapshot lookup (TEST-03)."""

    def test_returns_test_snapshot_when_exists(self):
        client = MagicMock()
        client.images.get.return_value = MagicMock()
        result = get_test_image(client, "abc123", "base:latest", TEST_BUILDER_NAME)
        assert result == f"oss-crs-snapshot:test-{TEST_BUILDER_NAME}-abc123"
        client.images.get.assert_called_once_with(f"oss-crs-snapshot:test-{TEST_BUILDER_NAME}-abc123")

    def test_returns_base_when_not_found(self):
        client = MagicMock()
        client.images.get.side_effect = docker.errors.ImageNotFound("not found")
        result = get_test_image(client, "abc123", "base:latest", TEST_BUILDER_NAME)
        assert result == "base:latest"


# ===========================================================================
# TestSnapshotTagNaming
# ===========================================================================

class TestSnapshotTagNaming:
    """Tests verifying build-tag and test-tag separation (TEST-04)."""

    def test_build_snapshot_uses_build_prefix(self):
        """get_build_image looks up oss-crs-snapshot:build-{builder_name}-{id}."""
        client = MagicMock()
        client.images.get.side_effect = docker.errors.ImageNotFound("nope")
        get_build_image(client, "myid", "base:latest", TEST_BUILDER_NAME)
        client.images.get.assert_called_once_with(f"oss-crs-snapshot:build-{TEST_BUILDER_NAME}-myid")

    def test_test_snapshot_uses_test_prefix(self):
        """get_test_image looks up oss-crs-snapshot:test-{builder_name}-{id} (distinct from build)."""
        client = MagicMock()
        client.images.get.side_effect = docker.errors.ImageNotFound("nope")
        get_test_image(client, "myid", "base:latest", TEST_BUILDER_NAME)
        client.images.get.assert_called_once_with(f"oss-crs-snapshot:test-{TEST_BUILDER_NAME}-myid")

    def test_build_and_test_tags_are_distinct(self):
        """Build and test snapshot tags for same build_id must differ."""
        build_tag = f"oss-crs-snapshot:build-{TEST_BUILDER_NAME}-myid"
        test_tag = f"oss-crs-snapshot:test-{TEST_BUILDER_NAME}-myid"
        assert build_tag != test_tag


# ===========================================================================
# TestRunEphemeralTest
# ===========================================================================

class TestRunEphemeralTest:
    """Tests for docker_ops.run_ephemeral_test() container lifecycle (TEST-01, TEST-02)."""

    def _make_mock_client(self, exit_code=0, snapshot_exists=False):
        """Return (client_mock, container_mock) with configurable exit code and snapshot state."""
        client = MagicMock()
        container = MagicMock()

        container.wait.return_value = {"StatusCode": exit_code}
        container.logs.return_value = b"test output"
        client.containers.create.return_value = container

        if snapshot_exists:
            client.images.get.return_value = MagicMock()
        else:
            client.images.get.side_effect = docker.errors.ImageNotFound("nope")

        return client, container

    def test_successful_test_returns_exit_code_0(self):
        client, _ = self._make_mock_client(exit_code=0)
        with patch("docker_ops.docker.from_env", return_value=client):
            result = run_ephemeral_test("base:latest", "build123", TEST_BUILDER_NAME, b"patch")
        assert result["exit_code"] == 0
        assert result["build_id"] == "build123"

    def test_artifacts_version_is_none(self):
        """Test results always have artifacts_version=None (no versioned output)."""
        client, _ = self._make_mock_client(exit_code=0)
        with patch("docker_ops.docker.from_env", return_value=client):
            result = run_ephemeral_test("base:latest", "build123", TEST_BUILDER_NAME, b"patch")
        assert result["artifacts_version"] is None

    def test_first_success_commits_test_snapshot(self):
        """First successful test commits snapshot with test-{builder_name}- prefix (TEST-02)."""
        client, container = self._make_mock_client(exit_code=0, snapshot_exists=False)
        with patch("docker_ops.docker.from_env", return_value=client):
            run_ephemeral_test("base:latest", "build123", TEST_BUILDER_NAME, b"patch")
        container.commit.assert_called_once_with(
            repository="oss-crs-snapshot",
            tag=f"test-{TEST_BUILDER_NAME}-build123",
        )

    def test_existing_snapshot_skips_commit(self):
        """Test snapshot already exists: container.commit() must NOT be called."""
        client, container = self._make_mock_client(exit_code=0, snapshot_exists=True)
        with patch("docker_ops.docker.from_env", return_value=client):
            run_ephemeral_test("base:latest", "build123", TEST_BUILDER_NAME, b"patch")
        container.commit.assert_not_called()

    def test_failed_test_no_commit(self):
        """Non-zero exit code: test snapshot must NOT be committed."""
        client, container = self._make_mock_client(exit_code=1, snapshot_exists=False)
        with patch("docker_ops.docker.from_env", return_value=client):
            result = run_ephemeral_test("base:latest", "build123", TEST_BUILDER_NAME, b"patch")
        assert result["exit_code"] == 1
        container.commit.assert_not_called()

    def test_container_removed_on_success(self):
        """container.remove(force=True) called after successful test."""
        client, container = self._make_mock_client(exit_code=0)
        with patch("docker_ops.docker.from_env", return_value=client):
            run_ephemeral_test("base:latest", "build123", TEST_BUILDER_NAME, b"patch")
        container.remove.assert_called_once_with(force=True)

    def test_container_removed_on_failure(self):
        """container.remove(force=True) called even when test fails."""
        client, container = self._make_mock_client(exit_code=1)
        with patch("docker_ops.docker.from_env", return_value=client):
            run_ephemeral_test("base:latest", "build123", TEST_BUILDER_NAME, b"patch")
        container.remove.assert_called_once_with(force=True)

    def test_timeout_returns_error(self):
        """ReadTimeout during container.wait results in exit_code=1 with timeout message."""
        import requests.exceptions
        client, container = self._make_mock_client(exit_code=0)
        container.wait.side_effect = requests.exceptions.ReadTimeout()
        with patch("docker_ops.docker.from_env", return_value=client):
            result = run_ephemeral_test("base:latest", "build123", TEST_BUILDER_NAME, b"patch", timeout=1)
        assert result["exit_code"] == 1
        assert "timed out" in result["stderr"].lower() or "timeout" in result["stderr"].lower()
        assert result["artifacts_version"] is None

    def test_invokes_test_sh(self):
        """Container command must invoke test.sh (not oss_crs_handler.sh)."""
        client, _ = self._make_mock_client(exit_code=0)
        with patch("docker_ops.docker.from_env", return_value=client):
            run_ephemeral_test("base:latest", "build123", TEST_BUILDER_NAME, b"patch")
        call_kwargs = client.containers.create.call_args
        command = call_kwargs[1].get("command") or call_kwargs[0][1]
        assert any("test.sh" in str(c) for c in command)
        assert not any("oss_crs_handler.sh" in str(c) for c in command)


# ===========================================================================
# TestTestEndpoint
# ===========================================================================

class TestTestEndpoint:
    """Tests for POST /test endpoint (API-03, TEST-01)."""

    def _patch_bytes(self, content: bytes = b"diff --git a/foo"):
        """Return (files, data) suitable for multipart POST to /test."""
        return (
            {"patch": ("patch.diff", content, "application/octet-stream")},
            {"build_id": "testbuild", "builder_name": TEST_BUILDER_NAME},
        )

    def test_submit_test_returns_queued(self):
        """POST /test returns 200 with status 'queued' and a job id."""
        client = _make_test_client()
        files, data = self._patch_bytes()
        response = client.post("/test", files=files, data=data)
        assert response.status_code == 200
        body = response.json()
        assert "id" in body
        assert body["status"] == "queued"

    def test_test_job_id_has_test_prefix(self):
        """Test job IDs must be namespaced with 'test:' prefix (Pitfall 5)."""
        client = _make_test_client()
        files, data = self._patch_bytes(b"my test patch")
        response = client.post("/test", files=files, data=data)
        assert response.json()["id"].startswith("test:")

    def test_test_job_id_differs_from_build_job_id(self):
        """Same patch content must yield different job_id for /test vs /build."""
        patch_content = b"same patch content"
        client = _make_test_client()

        build_resp = client.post(
            "/build",
            files={"patch": ("p.diff", patch_content, "application/octet-stream")},
            data={"build_id": "b1", "builder_name": TEST_BUILDER_NAME},
        )
        test_resp = client.post(
            "/test",
            files={"patch": ("p.diff", patch_content, "application/octet-stream")},
            data={"build_id": "b1", "builder_name": TEST_BUILDER_NAME},
        )
        assert build_resp.json()["id"] != test_resp.json()["id"]

    def test_does_not_return_501(self):
        """POST /test must NOT return 501 (stub removed)."""
        client = _make_test_client()
        files, data = self._patch_bytes()
        response = client.post("/test", files=files, data=data)
        assert response.status_code != 501

    def test_dedup_returns_existing_queued_status(self):
        """Second POST /test with same patch returns existing queued job."""
        client = _make_test_client()
        patch_content = b"unique test patch xyz"
        files = {"patch": ("patch.diff", patch_content, "application/octet-stream")}
        data = {"build_id": "myproj", "builder_name": TEST_BUILDER_NAME}

        resp1 = client.post("/test", files=files, data=data)
        resp2 = client.post("/test", files={"patch": ("patch.diff", patch_content, "application/octet-stream")}, data=data)
        assert resp1.json()["id"] == resp2.json()["id"]


# ===========================================================================
# TestErrorFormat
# ===========================================================================

class TestErrorFormat:
    """Tests for consistent error response format (API-06)."""

    def test_404_has_error_key(self):
        """404 responses (unknown job) must contain an 'error' key."""
        client = _make_test_client()
        response = client.get("/status/totally-unknown-id")
        assert response.status_code == 404
        assert "error" in response.json()

    def test_run_pov_501_has_error_key(self):
        """501 response for /run-pov stub must contain an 'error' key."""
        client = _make_test_client()
        response = client.post("/run-pov")
        assert response.status_code == 501
        assert "error" in response.json()

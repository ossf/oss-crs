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
import os
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

from docker_ops import get_build_image, get_test_image, next_rebuild_id, rebuild_output_dir, run_ephemeral_build, run_ephemeral_test

# ---------------------------------------------------------------------------
# Test-wide builder name constant
# ---------------------------------------------------------------------------

TEST_BUILDER_NAME = "test-builder"
TEST_CRS_NAME = "test-crs"


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

class TestNextRebuildId:
    """Tests for docker_ops.next_rebuild_id() filesystem counter."""

    def test_nonexistent_dir_returns_1(self, tmp_path):
        assert next_rebuild_id(tmp_path, "my-crs") == 1

    def test_empty_crs_dir_returns_1(self, tmp_path):
        (tmp_path / "my-crs").mkdir()
        assert next_rebuild_id(tmp_path, "my-crs") == 1

    def test_with_1_returns_2(self, tmp_path):
        (tmp_path / "my-crs" / "1").mkdir(parents=True)
        assert next_rebuild_id(tmp_path, "my-crs") == 2

    def test_with_1_2_3_returns_4(self, tmp_path):
        for v in ["1", "2", "3"]:
            (tmp_path / "my-crs" / v).mkdir(parents=True)
        assert next_rebuild_id(tmp_path, "my-crs") == 4

    def test_ignores_non_numeric_dirs(self, tmp_path):
        crs_dir = tmp_path / "my-crs"
        crs_dir.mkdir()
        (crs_dir / "1").mkdir()
        (crs_dir / "temp").mkdir()
        (crs_dir / "2").mkdir()
        assert next_rebuild_id(tmp_path, "my-crs") == 3


# ===========================================================================
# TestGetBuildImage
# ===========================================================================

class TestGetBuildImage:
    """Tests for docker_ops.get_build_image() snapshot lookup."""

    @patch.dict(os.environ, {"INCREMENTAL_BUILD": "true", "OSS_CRS_BUILD_ID": "bid123"})
    def test_returns_snapshot_when_exists(self):
        client = MagicMock()
        client.images.get.return_value = MagicMock()
        result = get_build_image(client, "base:latest", TEST_BUILDER_NAME, TEST_CRS_NAME)
        assert result == f"oss-crs-snapshot:build-{TEST_CRS_NAME}-{TEST_BUILDER_NAME}-bid123"
        client.images.get.assert_called_once_with(f"oss-crs-snapshot:build-{TEST_CRS_NAME}-{TEST_BUILDER_NAME}-bid123")

    @patch.dict(os.environ, {"INCREMENTAL_BUILD": "true", "OSS_CRS_BUILD_ID": "bid123"})
    def test_returns_base_when_not_found(self):
        client = MagicMock()
        client.images.get.side_effect = docker.errors.ImageNotFound("not found")
        result = get_build_image(client, "base:latest", TEST_BUILDER_NAME, TEST_CRS_NAME)
        assert result == "base:latest"

    def test_returns_base_when_incremental_not_set(self):
        client = MagicMock()
        client.images.get.return_value = MagicMock()
        result = get_build_image(client, "base:latest", TEST_BUILDER_NAME, TEST_CRS_NAME)
        assert result == "base:latest"
        client.images.get.assert_not_called()


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
        import io as _io
        import tarfile as _tarfile

        client = MagicMock()
        container = MagicMock()

        container.wait.return_value = {"StatusCode": exit_code}
        def _mock_logs(**kwargs):
            if kwargs.get("stream"):
                return iter([b"build output\n"])
            return b"build output"
        container.logs.side_effect = _mock_logs

        # Mock get_archive to return a valid tar stream
        tar_buf = _io.BytesIO()
        with _tarfile.open(fileobj=tar_buf, mode="w") as tf:
            data = b"mock artifact"
            info = _tarfile.TarInfo(name="fuzzer_binary")
            info.size = len(data)
            tf.addfile(info, _io.BytesIO(data))
        tar_bytes = tar_buf.getvalue()
        container.get_archive.return_value = (iter([tar_bytes]), {})

        client.containers.create.return_value = container

        if snapshot_exists:
            client.images.get.return_value = MagicMock()
        else:
            client.images.get.side_effect = docker.errors.ImageNotFound("nope")

        return client, container

    def test_successful_build_returns_exit_code_0(self, tmp_path):
        client, _ = self._make_mock_client(exit_code=0)
        with patch("docker_ops.docker.from_env", return_value=client):
            result = run_ephemeral_build("base:latest", 1, TEST_BUILDER_NAME, "test-crs", b"patch", tmp_path)
        assert result["exit_code"] == 0
        assert result["rebuild_id"] == 1

    def test_first_success_commits_snapshot(self, tmp_path):
        """First successful build with no existing snapshot: container.commit() called."""
        client, container = self._make_mock_client(exit_code=0, snapshot_exists=False)
        with patch("docker_ops.docker.from_env", return_value=client):
            run_ephemeral_build("base:latest", 1, TEST_BUILDER_NAME, "test-crs", b"patch", tmp_path)
        container.commit.assert_called_once_with(
            repository="oss-crs-snapshot",
            tag=f"build-test-crs-{TEST_BUILDER_NAME}-1",
        )

    def test_existing_snapshot_skips_commit(self, tmp_path):
        """Snapshot already exists: container.commit() must NOT be called."""
        client, container = self._make_mock_client(exit_code=0, snapshot_exists=True)
        with patch("docker_ops.docker.from_env", return_value=client):
            run_ephemeral_build("base:latest", 1, TEST_BUILDER_NAME, "test-crs", b"patch", tmp_path)
        container.commit.assert_not_called()

    def test_failed_build_no_commit(self, tmp_path):
        """Non-zero exit code: snapshot must NOT be committed."""
        client, container = self._make_mock_client(exit_code=1, snapshot_exists=False)
        with patch("docker_ops.docker.from_env", return_value=client):
            result = run_ephemeral_build("base:latest", 1, TEST_BUILDER_NAME, "test-crs", b"patch", tmp_path)
        assert result["exit_code"] == 1
        container.commit.assert_not_called()

    def test_container_removed_on_success(self, tmp_path):
        """Container.remove(force=True) called after successful build."""
        client, container = self._make_mock_client(exit_code=0)
        with patch("docker_ops.docker.from_env", return_value=client):
            run_ephemeral_build("base:latest", 1, TEST_BUILDER_NAME, "test-crs", b"patch", tmp_path)
        container.remove.assert_called_once_with(force=True)

    def test_container_removed_on_failure(self, tmp_path):
        """Container.remove(force=True) called even when build fails."""
        client, container = self._make_mock_client(exit_code=1)
        with patch("docker_ops.docker.from_env", return_value=client):
            run_ephemeral_build("base:latest", 1, TEST_BUILDER_NAME, "test-crs", b"patch", tmp_path)
        container.remove.assert_called_once_with(force=True)

    def test_timeout_returns_error(self, tmp_path):
        """ReadTimeout during container.wait results in exit_code=1 with timeout message."""
        import requests.exceptions
        client, container = self._make_mock_client(exit_code=0)
        container.wait.side_effect = requests.exceptions.ReadTimeout()
        with patch("docker_ops.docker.from_env", return_value=client):
            result = run_ephemeral_build("base:latest", 1, TEST_BUILDER_NAME, "test-crs", b"patch", tmp_path, timeout=1)
        assert result["exit_code"] == 1
        assert "timed out" in result["stderr"].lower() or "timeout" in result["stderr"].lower()

    def test_creates_output_directory(self, tmp_path):
        """On success, output directory is created under rebuild_out_dir/{crs_name}/{rebuild_id}/."""
        client, _ = self._make_mock_client(exit_code=0)
        with patch("docker_ops.docker.from_env", return_value=client):
            run_ephemeral_build("base:latest", 1, TEST_BUILDER_NAME, "test-crs", b"patch", tmp_path)
        assert (tmp_path / "test-crs" / "1").exists()

    def test_failed_build_does_not_create_version_directory(self, tmp_path):
        """ART-02: Failed builds must not consume version slots."""
        client, _ = self._make_mock_client(exit_code=1)
        with patch("docker_ops.docker.from_env", return_value=client):
            run_ephemeral_build("base:latest", 1, TEST_BUILDER_NAME, "test-crs", b"patch", tmp_path)
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
            {"crs_name": "test-crs", "builder_name": TEST_BUILDER_NAME},
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
        data = {"crs_name": "test-crs", "builder_name": TEST_BUILDER_NAME}

        resp1 = client.post("/build", files=files1, data=data)
        resp2 = client.post("/build", files={"patch": ("p.diff", patch_content, "application/octet-stream")}, data=data)
        assert resp1.json()["id"] == resp2.json()["id"]

    def test_dedup_returns_existing_status(self):
        """Second POST with same patch content returns the existing job entry (dedup)."""
        client = _make_test_client()
        patch_content = b"unique patch abc"
        files = {"patch": ("patch.diff", patch_content, "application/octet-stream")}
        data = {"crs_name": "test-crs", "builder_name": TEST_BUILDER_NAME}

        resp1 = client.post("/build", files=files, data=data)
        resp2 = client.post("/build", files={"patch": ("patch.diff", patch_content, "application/octet-stream")}, data=data)

        # Both calls return the same job id
        assert resp1.json()["id"] == resp2.json()["id"]

    def test_build_id_uses_sha256(self):
        """Build ID is a 12-char hex prefix of SHA256(project:sanitizer:builder_name:patch)."""
        import os
        import server

        patch_content = b"my patch bytes"
        project = os.environ.get("PROJECT_NAME", "unknown")
        sanitizer = os.environ.get("SANITIZER", "unknown")
        hasher = hashlib.sha256()
        hasher.update(f"{project}:{sanitizer}:{TEST_BUILDER_NAME}:".encode())
        hasher.update(patch_content)
        expected_id = hasher.hexdigest()[:12]

        client = _make_test_client()
        files = {"patch": ("patch.diff", patch_content, "application/octet-stream")}
        data = {"crs_name": "test-crs", "builder_name": TEST_BUILDER_NAME}
        resp = client.post("/build", files=files, data=data)
        assert resp.json()["id"] == expected_id

    def test_get_builds_returns_list(self):
        """GET /builds returns a dict with a 'builds' key containing job id list."""
        client = _make_test_client()
        files = {"patch": ("patch.diff", b"some patch", "application/octet-stream")}
        data = {"crs_name": "test-crs", "builder_name": TEST_BUILDER_NAME}
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
                "rebuild_id": 1,
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

    @patch.dict(os.environ, {"INCREMENTAL_BUILD": "true"})
    @patch.dict(os.environ, {"INCREMENTAL_BUILD": "true", "OSS_CRS_BUILD_ID": "bid123"})
    def test_returns_test_snapshot_when_exists(self):
        """get_test_image returns test-{build_id} snapshot (no builder_name, per D-05)."""
        client = MagicMock()
        client.images.get.return_value = MagicMock()
        result = get_test_image(client, "base:latest")
        assert result == "oss-crs-snapshot:test-bid123"
        client.images.get.assert_called_once_with("oss-crs-snapshot:test-bid123")

    @patch.dict(os.environ, {"INCREMENTAL_BUILD": "true", "OSS_CRS_BUILD_ID": "bid123"})
    def test_returns_base_when_not_found(self):
        client = MagicMock()
        client.images.get.side_effect = docker.errors.ImageNotFound("not found")
        result = get_test_image(client, "base:latest")
        assert result == "base:latest"

    def test_returns_base_when_incremental_not_set(self):
        client = MagicMock()
        result = get_test_image(client, "base:latest")
        assert result == "base:latest"
        client.images.get.assert_not_called()


# ===========================================================================
# TestSnapshotTagNaming
# ===========================================================================

class TestSnapshotTagNaming:
    """Tests verifying build-tag and test-tag separation (TEST-04)."""

    @patch.dict(os.environ, {"INCREMENTAL_BUILD": "true", "OSS_CRS_BUILD_ID": "myid"})
    def test_build_snapshot_uses_crs_prefix(self):
        """get_build_image looks up oss-crs-snapshot:{crs_name}-{builder_name}-{build_id}."""
        client = MagicMock()
        client.images.get.side_effect = docker.errors.ImageNotFound("nope")
        get_build_image(client, "base:latest", TEST_BUILDER_NAME, TEST_CRS_NAME)
        client.images.get.assert_called_once_with(f"oss-crs-snapshot:build-{TEST_CRS_NAME}-{TEST_BUILDER_NAME}-myid")

    @patch.dict(os.environ, {"INCREMENTAL_BUILD": "true", "OSS_CRS_BUILD_ID": "myid"})
    def test_test_snapshot_uses_test_prefix(self):
        """get_test_image looks up oss-crs-snapshot:test-{build_id} (no builder_name, per D-05)."""
        client = MagicMock()
        client.images.get.side_effect = docker.errors.ImageNotFound("nope")
        get_test_image(client, "base:latest")
        client.images.get.assert_called_once_with("oss-crs-snapshot:test-myid")

    def test_build_and_test_tags_are_distinct(self):
        """Build and test snapshot tags for same build_id must differ."""
        build_tag = f"oss-crs-snapshot:build-{TEST_CRS_NAME}-{TEST_BUILDER_NAME}-myid"
        test_tag = "oss-crs-snapshot:test-myid"
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
        def _mock_logs(**kwargs):
            if kwargs.get("stream"):
                return iter([b"test output\n"])
            return b"test output"
        container.logs.side_effect = _mock_logs
        client.containers.create.return_value = container

        if snapshot_exists:
            client.images.get.return_value = MagicMock()
        else:
            client.images.get.side_effect = docker.errors.ImageNotFound("nope")

        return client, container

    def test_successful_test_returns_exit_code_0(self):
        client, _ = self._make_mock_client(exit_code=0)
        with patch("docker_ops.docker.from_env", return_value=client):
            result = run_ephemeral_test("base:latest", 1, b"patch")
        assert result["exit_code"] == 0
        assert result["rebuild_id"] == 1

    def test_first_success_commits_test_snapshot(self):
        """First successful test commits snapshot with test-{build_id} tag per D-05 (no builder_name)."""
        client, container = self._make_mock_client(exit_code=0, snapshot_exists=False)
        with patch("docker_ops.docker.from_env", return_value=client):
            run_ephemeral_test("base:latest", 1, b"patch")
        container.commit.assert_called_once_with(
            repository="oss-crs-snapshot",
            tag="test-1",
        )

    def test_existing_snapshot_skips_commit(self):
        """Test snapshot already exists: container.commit() must NOT be called."""
        client, container = self._make_mock_client(exit_code=0, snapshot_exists=True)
        with patch("docker_ops.docker.from_env", return_value=client):
            run_ephemeral_test("base:latest", 1, b"patch")
        container.commit.assert_not_called()

    def test_failed_test_no_commit(self):
        """Non-zero exit code: test snapshot must NOT be committed."""
        client, container = self._make_mock_client(exit_code=1, snapshot_exists=False)
        with patch("docker_ops.docker.from_env", return_value=client):
            result = run_ephemeral_test("base:latest", 1, b"patch")
        assert result["exit_code"] == 1
        container.commit.assert_not_called()

    def test_container_removed_on_success(self):
        """container.remove(force=True) called after successful test."""
        client, container = self._make_mock_client(exit_code=0)
        with patch("docker_ops.docker.from_env", return_value=client):
            run_ephemeral_test("base:latest", 1, b"patch")
        container.remove.assert_called_once_with(force=True)

    def test_container_removed_on_failure(self):
        """container.remove(force=True) called even when test fails."""
        client, container = self._make_mock_client(exit_code=1)
        with patch("docker_ops.docker.from_env", return_value=client):
            run_ephemeral_test("base:latest", 1, b"patch")
        container.remove.assert_called_once_with(force=True)

    def test_timeout_returns_error(self):
        """ReadTimeout during container.wait results in exit_code=1 with timeout message."""
        import requests.exceptions
        client, container = self._make_mock_client(exit_code=0)
        container.wait.side_effect = requests.exceptions.ReadTimeout()
        with patch("docker_ops.docker.from_env", return_value=client):
            result = run_ephemeral_test("base:latest", 1, b"patch", timeout=1)
        assert result["exit_code"] == 1
        assert "timed out" in result["stderr"].lower() or "timeout" in result["stderr"].lower()

    def test_invokes_run_tests(self):
        """Container OSS_CRS_BUILD_CMD env var must reference run_tests.sh."""
        client, _ = self._make_mock_client(exit_code=0)
        with patch("docker_ops.docker.from_env", return_value=client):
            run_ephemeral_test("base:latest", 1, b"patch")
        call_kwargs = client.containers.create.call_args
        environment = call_kwargs[1].get("environment", {})
        assert "run_tests.sh" in environment.get("OSS_CRS_BUILD_CMD", "")


# ===========================================================================
# TestHandleTestProjectImage
# ===========================================================================

class TestHandleTestProjectImage:
    """Tests for _handle_test() PROJECT_BASE_IMAGE behavior (D-02, D-06, D-07)."""

    import os as _os

    def _make_mock_client(self):
        """Return a mock Docker client that simulates no existing test snapshot."""
        import docker as _docker
        mock_client = MagicMock()
        mock_client.images.get.side_effect = _docker.errors.ImageNotFound("nope")
        mock_container = MagicMock()
        mock_container.wait.return_value = {"StatusCode": 0}
        def _mock_logs(**kwargs):
            if kwargs.get("stream"):
                return iter([b"test output\n"])
            return b"test output"
        mock_container.logs.side_effect = _mock_logs
        mock_client.containers.create.return_value = mock_container
        return mock_client

    def test_handle_test_uses_project_base_image(self):
        """_handle_test() uses PROJECT_BASE_IMAGE to create the container (D-02)."""
        import server
        mock_client = self._make_mock_client()
        with patch("docker_ops.docker.from_env", return_value=mock_client):
            with patch.dict(__import__("os").environ, {"PROJECT_BASE_IMAGE": "project:latest"}, clear=False):
                server._handle_test("job1", b"patch", 1, "", "")
        mock_client.containers.create.assert_called_once()
        call_args = mock_client.containers.create.call_args
        image_arg = call_args[0][0] if call_args[0] else call_args[1].get("image")
        assert image_arg == "project:latest"

    def test_handle_test_raises_when_project_base_image_missing(self):
        """_handle_test() raises ValueError when PROJECT_BASE_IMAGE is not in os.environ."""
        import server
        import os as _os
        mock_client = self._make_mock_client()
        env_without_project_base = {k: v for k, v in _os.environ.items() if k != "PROJECT_BASE_IMAGE"}
        with patch("docker_ops.docker.from_env", return_value=mock_client):
            with patch.dict(_os.environ, env_without_project_base, clear=True):
                with pytest.raises(ValueError, match="PROJECT_BASE_IMAGE"):
                    server._handle_test("job1", b"patch", 1, "", "")

    def test_handle_test_ignores_builder_base_image(self):
        """_handle_test() does not read BASE_IMAGE_{builder_name} even when set."""
        import server
        mock_client = self._make_mock_client()
        env_vars = {
            "BASE_IMAGE_CRS_LIBFUZZER": "wrong:image",
            "PROJECT_BASE_IMAGE": "project:correct",
        }
        with patch("docker_ops.docker.from_env", return_value=mock_client):
            with patch.dict(__import__("os").environ, env_vars, clear=False):
                server._handle_test("job1", b"patch", 1, "", "")
        call_args = mock_client.containers.create.call_args
        image_arg = call_args[0][0] if call_args[0] else call_args[1].get("image")
        assert image_arg == "project:correct"
        assert image_arg != "wrong:image"

    def test_test_response_schema(self):
        """Response dict keys are exactly {exit_code, stdout, stderr, rebuild_id}."""
        client, _ = TestRunEphemeralTest()._make_mock_client(exit_code=0)
        with patch("docker_ops.docker.from_env", return_value=client):
            result = run_ephemeral_test("base:latest", 1, b"patch")
        assert set(result.keys()) == {"exit_code", "stdout", "stderr", "rebuild_id"}


# ===========================================================================
# TestTestEndpoint
# ===========================================================================

class TestTestEndpoint:
    """Tests for POST /test endpoint (API-03, TEST-01)."""

    def _patch_bytes(self, content: bytes = b"diff --git a/foo"):
        """Return (files, data) suitable for multipart POST to /test."""
        return (
            {"patch": ("patch.diff", content, "application/octet-stream")},
            {"crs_name": "test-crs", "builder_name": TEST_BUILDER_NAME},
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
            data={"crs_name": "test-crs", "builder_name": TEST_BUILDER_NAME},
        )
        test_resp = client.post(
            "/test",
            files={"patch": ("p.diff", patch_content, "application/octet-stream")},
            data={"crs_name": "test-crs"},
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
        data = {"crs_name": "test-crs", "builder_name": TEST_BUILDER_NAME}

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

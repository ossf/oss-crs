"""Unit tests for LocalCRSUtils sidecar integration.

Tests all INT-* requirements with mocked HTTP requests and DNS resolution.
No real Docker or sidecar services needed.
"""
import io
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Patch socket.gethostbyname before importing local to avoid DNS errors
with patch("socket.gethostbyname", return_value="127.0.0.1"):
    from libCRS.libCRS.local import LocalCRSUtils


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(status_code=200, json_data=None, content=b""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.content = content
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    return resp


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def crs_utils():
    with patch("libCRS.libCRS.local.socket.gethostbyname", return_value="127.0.0.1"):
        utils = LocalCRSUtils()
        # Pre-mark both builder and runner as healthy to skip health polling
        utils._builders_healthy = {"test-builder": True, "test-runner": True}
        # Mock get_service_domain so tests don't need OSS_CRS_NAME or real DNS
        utils.get_service_domain = lambda name: f"{name}.test-crs"
        return utils


# ---------------------------------------------------------------------------
# INT-01: apply_patch_build
# ---------------------------------------------------------------------------


class TestApplyPatchBuild:
    """INT-01: apply_patch_build calls /build and parses nested response."""

    @patch("libCRS.libCRS.local.http_requests")
    def test_successful_build_parses_nested_response(self, mock_requests, crs_utils, tmp_path):
        # Sidecar returns nested format
        submit_resp = _mock_response(json_data={"id": "job123", "status": "queued"})
        poll_resp = _mock_response(json_data={
            "id": "job123",
            "status": "done",
            "result": {
                "exit_code": 0,
                "stdout": "Build OK",
                "stderr": "",
                "build_id": "build-abc",
                "artifacts_version": "v1",
            },
        })
        mock_requests.post.return_value = submit_resp
        mock_requests.get.return_value = poll_resp

        patch_file = tmp_path / "test.patch"
        patch_file.write_text("diff content")
        response_dir = tmp_path / "response"

        with patch.dict("os.environ", {"OSS_CRS_BUILD_ID": "build-abc"}):
            exit_code = crs_utils.apply_patch_build(patch_file, response_dir, "test-builder")

        assert exit_code == 0
        assert (response_dir / "build_exit_code").read_text() == "0"
        assert (response_dir / "build_stdout.log").read_text() == "Build OK"
        assert (response_dir / "build_id").read_text() == "build-abc"

    @patch("libCRS.libCRS.local.http_requests")
    def test_failed_build(self, mock_requests, crs_utils, tmp_path):
        submit_resp = _mock_response(json_data={"id": "job123", "status": "queued"})
        poll_resp = _mock_response(json_data={
            "id": "job123",
            "status": "done",
            "result": {
                "exit_code": 1,
                "stdout": "",
                "stderr": "compile error",
                "build_id": "b1",
                "artifacts_version": "v1",
            },
        })
        mock_requests.post.return_value = submit_resp
        mock_requests.get.return_value = poll_resp

        patch_file = tmp_path / "test.patch"
        patch_file.write_text("bad diff")
        response_dir = tmp_path / "response"

        with patch.dict("os.environ", {"OSS_CRS_BUILD_ID": "b1"}):
            exit_code = crs_utils.apply_patch_build(patch_file, response_dir, "test-builder")

        assert exit_code == 1
        assert (response_dir / "build_exit_code").read_text() == "1"
        assert (response_dir / "build_stderr.log").read_text() == "compile error"

    @patch("libCRS.libCRS.local.http_requests")
    def test_sends_builder_name_in_form_data(self, mock_requests, crs_utils, tmp_path):
        submit_resp = _mock_response(json_data={"id": "j1", "status": "queued"})
        poll_resp = _mock_response(json_data={
            "id": "j1",
            "status": "done",
            "result": {
                "exit_code": 0,
                "stdout": "",
                "stderr": "",
                "build_id": "b1",
                "artifacts_version": "v1",
            },
        })
        mock_requests.post.return_value = submit_resp
        mock_requests.get.return_value = poll_resp

        patch_file = tmp_path / "test.patch"
        patch_file.write_text("diff")
        response_dir = tmp_path / "response"

        with patch.dict("os.environ", {"OSS_CRS_BUILD_ID": "b1"}):
            crs_utils.apply_patch_build(patch_file, response_dir, "test-builder")

        # Verify builder_name was sent in the POST data
        call_args = mock_requests.post.call_args
        data = call_args.kwargs.get("data") or (call_args[1].get("data", {}) if len(call_args) > 1 else {})
        assert "builder_name" in data

    def test_unavailable_builder_writes_exit_code_1(self, crs_utils, tmp_path):
        # Simulate health check failure by mocking _wait_for_service_health
        crs_utils._wait_for_service_health = lambda *a, **kw: False

        patch_file = tmp_path / "test.patch"
        patch_file.write_text("diff")
        response_dir = tmp_path / "response"

        exit_code = crs_utils.apply_patch_build(patch_file, response_dir, "test-builder")

        assert exit_code == 1
        assert (response_dir / "build_exit_code").read_text() == "1"

    @patch("libCRS.libCRS.local.http_requests")
    def test_stores_last_builder_and_build_id_on_success(self, mock_requests, crs_utils, tmp_path):
        submit_resp = _mock_response(json_data={"id": "j1", "status": "queued"})
        poll_resp = _mock_response(json_data={
            "id": "j1",
            "status": "done",
            "result": {
                "exit_code": 0,
                "stdout": "",
                "stderr": "",
                "build_id": "stored-build-id",
                "artifacts_version": "v1",
            },
        })
        mock_requests.post.return_value = submit_resp
        mock_requests.get.return_value = poll_resp

        patch_file = tmp_path / "test.patch"
        patch_file.write_text("diff")
        response_dir = tmp_path / "response"

        with patch.dict("os.environ", {"OSS_CRS_BUILD_ID": "stored-build-id"}):
            crs_utils.apply_patch_build(patch_file, response_dir, "test-builder")

        assert crs_utils._last_builder == "test-builder"
        assert crs_utils._last_build_id == "stored-build-id"


# ---------------------------------------------------------------------------
# INT-02: download_build_output
# ---------------------------------------------------------------------------


class TestDownloadBuildOutput:
    """INT-02: download_build_output retrieves artifacts via HTTP."""

    @patch("libCRS.libCRS.local.http_requests")
    def test_downloads_and_extracts_zip(self, mock_requests, crs_utils, tmp_path):
        # Set up state from prior build
        crs_utils._last_builder = "test-builder"
        crs_utils._last_build_id = "build-abc"

        # Create a mock zip file
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w") as zf:
            zf.writestr("fuzzer_binary", b"ELF binary content")
        zip_bytes = zip_buf.getvalue()

        mock_requests.get.return_value = _mock_response(content=zip_bytes)

        dst = tmp_path / "artifacts"
        crs_utils.download_build_output("v1", dst)

        assert (dst / "fuzzer_binary").exists()
        mock_requests.get.assert_called_once()
        call_url = mock_requests.get.call_args[0][0]
        assert "download-build-output" in call_url

    @patch("libCRS.libCRS.local.http_requests")
    def test_passes_version_and_build_id_as_params(self, mock_requests, crs_utils, tmp_path):
        crs_utils._last_builder = "test-builder"
        crs_utils._last_build_id = "build-xyz"

        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w") as zf:
            zf.writestr("artifact.txt", b"content")
        mock_requests.get.return_value = _mock_response(content=zip_buf.getvalue())

        crs_utils.download_build_output("v2", tmp_path / "dst")

        call_kwargs = mock_requests.get.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params", {})
        assert params.get("build_id") == "build-xyz"
        assert params.get("version") == "v2"

    def test_raises_without_prior_build(self, crs_utils, tmp_path):
        with pytest.raises(RuntimeError, match="prior successful apply_patch_build"):
            crs_utils.download_build_output("v1", tmp_path / "dst")

    def test_raises_when_build_id_missing(self, crs_utils, tmp_path):
        crs_utils._last_builder = "test-builder"
        # No _last_build_id set
        with pytest.raises(RuntimeError, match="prior successful apply_patch_build"):
            crs_utils.download_build_output("v1", tmp_path / "dst")


# ---------------------------------------------------------------------------
# INT-03: run_pov routes to runner-sidecar
# ---------------------------------------------------------------------------


class TestRunPov:
    """INT-03: run_pov routes to runner-sidecar, not builder-sidecar."""

    @patch("libCRS.libCRS.local.http_requests")
    def test_routes_to_runner_sidecar(self, mock_requests, crs_utils, tmp_path):
        mock_requests.post.return_value = _mock_response(json_data={"exit_code": 0, "stderr": ""})
        response_dir = tmp_path / "response"

        crs_utils.run_pov(Path("/pov/crash"), "fuzzer", "build-abc", response_dir, "test-runner")

        # Verify POST was to the runner URL (test-runner service)
        call_url = mock_requests.post.call_args[0][0]
        assert "test-runner" in call_url or "run-pov" in call_url

    @patch("libCRS.libCRS.local.http_requests")
    def test_sends_form_data_not_file(self, mock_requests, crs_utils, tmp_path):
        mock_requests.post.return_value = _mock_response(json_data={"exit_code": 1, "stderr": "CRASH"})
        response_dir = tmp_path / "response"

        crs_utils.run_pov(Path("/pov/crash"), "fuzzer", "build-abc", response_dir, "test-runner")

        call_kwargs = mock_requests.post.call_args
        data = call_kwargs.kwargs.get("data") or (call_kwargs[1].get("data", {}) if len(call_kwargs) > 1 else {})
        assert "pov_path" in data
        assert "harness_name" in data
        # Confirm no "files" argument was used (no file upload)
        files = call_kwargs.kwargs.get("files") or (call_kwargs[1].get("files") if len(call_kwargs) > 1 else None)
        assert files is None

    @patch("libCRS.libCRS.local.http_requests")
    def test_pov_path_sent_as_string_not_upload(self, mock_requests, crs_utils, tmp_path):
        mock_requests.post.return_value = _mock_response(json_data={"exit_code": 0, "stderr": ""})
        response_dir = tmp_path / "response"
        pov_path = Path("/container/pov/testcase")

        crs_utils.run_pov(pov_path, "fuzzer", "build-abc", response_dir, "test-runner")

        call_kwargs = mock_requests.post.call_args
        data = call_kwargs.kwargs.get("data") or (call_kwargs[1].get("data", {}) if len(call_kwargs) > 1 else {})
        assert data["pov_path"] == str(pov_path)

    @patch("libCRS.libCRS.local.http_requests")
    def test_writes_pov_exit_code(self, mock_requests, crs_utils, tmp_path):
        mock_requests.post.return_value = _mock_response(
            json_data={"exit_code": 1, "stderr": "AddressSanitizer"}
        )
        response_dir = tmp_path / "response"

        exit_code = crs_utils.run_pov(Path("/pov/crash"), "fuzzer", "build-abc", response_dir, "test-runner")

        assert exit_code == 1
        assert (response_dir / "pov_exit_code").read_text() == "1"
        assert (response_dir / "pov_stderr.log").read_text() == "AddressSanitizer"

    @patch("libCRS.libCRS.local.http_requests")
    def test_pov_success_writes_exit_code_0(self, mock_requests, crs_utils, tmp_path):
        mock_requests.post.return_value = _mock_response(json_data={"exit_code": 0, "stderr": ""})
        response_dir = tmp_path / "response"

        exit_code = crs_utils.run_pov(Path("/pov/crash"), "fuzzer", "build-abc", response_dir, "test-runner")

        assert exit_code == 0
        assert (response_dir / "pov_exit_code").read_text() == "0"

    @patch("libCRS.libCRS.local.http_requests")
    def test_does_not_use_submit_and_poll(self, mock_requests, crs_utils, tmp_path):
        """run_pov is synchronous: no job ID polling should occur."""
        mock_requests.post.return_value = _mock_response(json_data={"exit_code": 0, "stderr": ""})
        response_dir = tmp_path / "response"

        crs_utils.run_pov(Path("/pov/crash"), "fuzzer", "build-abc", response_dir, "test-runner")

        # Only one POST call (the /run-pov call), no GET for /status/{job_id}
        assert mock_requests.post.call_count == 1
        # No GET calls were made (no job polling)
        assert mock_requests.get.call_count == 0


# ---------------------------------------------------------------------------
# INT-04: apply_patch_test
# ---------------------------------------------------------------------------


class TestApplyPatchTest:
    """INT-04: apply_patch_test calls /test with patch and nested response parsing."""

    @patch("libCRS.libCRS.local.http_requests")
    def test_parses_nested_test_response(self, mock_requests, crs_utils, tmp_path):
        submit_resp = _mock_response(json_data={"id": "t1", "status": "queued"})
        poll_resp = _mock_response(json_data={
            "id": "t1",
            "status": "done",
            "result": {
                "exit_code": 0,
                "stdout": "tests passed",
                "stderr": "",
                "build_id": "b1",
                "artifacts_version": None,
            },
        })
        mock_requests.post.return_value = submit_resp
        mock_requests.get.return_value = poll_resp
        response_dir = tmp_path / "response"
        patch_file = tmp_path / "test.diff"
        patch_file.write_text("patch content")

        exit_code = crs_utils.apply_patch_test(patch_file, "b1", response_dir, "test-builder")

        assert exit_code == 0
        assert (response_dir / "test_exit_code").read_text() == "0"
        assert (response_dir / "test_stdout.log").read_text() == "tests passed"

    @patch("libCRS.libCRS.local.http_requests")
    def test_failed_test_writes_exit_code_1(self, mock_requests, crs_utils, tmp_path):
        submit_resp = _mock_response(json_data={"id": "t2", "status": "queued"})
        poll_resp = _mock_response(json_data={
            "id": "t2",
            "status": "done",
            "result": {
                "exit_code": 1,
                "stdout": "",
                "stderr": "test failure",
                "build_id": "b2",
                "artifacts_version": None,
            },
        })
        mock_requests.post.return_value = submit_resp
        mock_requests.get.return_value = poll_resp
        response_dir = tmp_path / "response"
        patch_file = tmp_path / "test.diff"
        patch_file.write_text("patch content")

        exit_code = crs_utils.apply_patch_test(patch_file, "b2", response_dir, "test-builder")

        assert exit_code == 1
        assert (response_dir / "test_exit_code").read_text() == "1"
        assert (response_dir / "test_stderr.log").read_text() == "test failure"

    @patch("libCRS.libCRS.local.http_requests")
    def test_sends_builder_name_and_build_id(self, mock_requests, crs_utils, tmp_path):
        submit_resp = _mock_response(json_data={"id": "t3", "status": "queued"})
        poll_resp = _mock_response(json_data={
            "id": "t3",
            "status": "done",
            "result": {"exit_code": 0, "stdout": "", "stderr": "", "build_id": None, "artifacts_version": None},
        })
        mock_requests.post.return_value = submit_resp
        mock_requests.get.return_value = poll_resp
        response_dir = tmp_path / "response"
        patch_file = tmp_path / "test.diff"
        patch_file.write_text("patch content")

        crs_utils.apply_patch_test(patch_file, "build-99", response_dir, "test-builder")

        call_kwargs = mock_requests.post.call_args
        data = call_kwargs.kwargs.get("data") or (call_kwargs[1].get("data", {}) if len(call_kwargs) > 1 else {})
        assert data.get("builder_name") == "test-builder"
        assert data.get("build_id") == "build-99"

    @patch("libCRS.libCRS.local.http_requests")
    def test_sends_patch_file_to_test_endpoint(self, mock_requests, crs_utils, tmp_path):
        """apply_patch_test sends the patch file to the /test endpoint."""
        submit_resp = _mock_response(json_data={"id": "t4", "status": "queued"})
        poll_resp = _mock_response(json_data={
            "id": "t4",
            "status": "done",
            "result": {"exit_code": 0, "stdout": "", "stderr": "", "build_id": None, "artifacts_version": None},
        })
        mock_requests.post.return_value = submit_resp
        mock_requests.get.return_value = poll_resp
        response_dir = tmp_path / "response"
        patch_file = tmp_path / "test.diff"
        patch_file.write_text("real patch content")

        crs_utils.apply_patch_test(patch_file, "build-99", response_dir, "test-builder")

        # Verify files dict was passed with a "patch" key
        call_kwargs = mock_requests.post.call_args
        files = call_kwargs.kwargs.get("files") or (call_kwargs[1].get("files") if len(call_kwargs) > 1 else None)
        assert files is not None
        assert "patch" in files


# ---------------------------------------------------------------------------
# INT-05: response directory file name compatibility
# ---------------------------------------------------------------------------


class TestResponseDirCompat:
    """INT-05: Response directory files maintain expected names."""

    @patch("libCRS.libCRS.local.http_requests")
    def test_build_response_files(self, mock_requests, crs_utils, tmp_path):
        submit_resp = _mock_response(json_data={"id": "j1", "status": "queued"})
        poll_resp = _mock_response(json_data={
            "id": "j1",
            "status": "done",
            "result": {
                "exit_code": 0,
                "stdout": "ok",
                "stderr": "warn",
                "build_id": "bid",
                "artifacts_version": "v2",
            },
        })
        mock_requests.post.return_value = submit_resp
        mock_requests.get.return_value = poll_resp

        patch_file = tmp_path / "p.diff"
        patch_file.write_text("")
        response_dir = tmp_path / "resp"

        with patch.dict("os.environ", {"OSS_CRS_BUILD_ID": "bid"}):
            crs_utils.apply_patch_build(patch_file, response_dir, "test-builder")

        # Verify exact file names that CRS modules expect
        assert (response_dir / "build_exit_code").exists()
        assert (response_dir / "build_stdout.log").exists()
        assert (response_dir / "build_stderr.log").exists()
        assert (response_dir / "build_id").exists()

    @patch("libCRS.libCRS.local.http_requests")
    def test_pov_response_files(self, mock_requests, crs_utils, tmp_path):
        mock_requests.post.return_value = _mock_response(
            json_data={"exit_code": 0, "stderr": "no crash"}
        )
        response_dir = tmp_path / "resp"

        crs_utils.run_pov(Path("/pov"), "fuzz", "b1", response_dir, "test-runner")

        assert (response_dir / "pov_exit_code").exists()
        assert (response_dir / "pov_stderr.log").exists()

    @patch("libCRS.libCRS.local.http_requests")
    def test_test_response_files(self, mock_requests, crs_utils, tmp_path):
        submit_resp = _mock_response(json_data={"id": "j2", "status": "queued"})
        poll_resp = _mock_response(json_data={
            "id": "j2",
            "status": "done",
            "result": {
                "exit_code": 0,
                "stdout": "pass",
                "stderr": "",
                "build_id": None,
                "artifacts_version": None,
            },
        })
        mock_requests.post.return_value = submit_resp
        mock_requests.get.return_value = poll_resp
        response_dir = tmp_path / "resp"
        patch_file = tmp_path / "test.diff"
        patch_file.write_text("")

        crs_utils.apply_patch_test(patch_file, "b1", response_dir, "test-builder")

        assert (response_dir / "test_exit_code").exists()
        assert (response_dir / "test_stdout.log").exists()

    @patch("libCRS.libCRS.local.http_requests")
    def test_build_exit_code_is_string_integer(self, mock_requests, crs_utils, tmp_path):
        """build_exit_code file contains a plain integer string, not JSON."""
        submit_resp = _mock_response(json_data={"id": "j3", "status": "queued"})
        poll_resp = _mock_response(json_data={
            "id": "j3",
            "status": "done",
            "result": {"exit_code": 0, "stdout": "", "stderr": "", "build_id": "b3", "artifacts_version": "v1"},
        })
        mock_requests.post.return_value = submit_resp
        mock_requests.get.return_value = poll_resp

        patch_file = tmp_path / "p.diff"
        patch_file.write_text("")
        response_dir = tmp_path / "resp"

        with patch.dict("os.environ", {"OSS_CRS_BUILD_ID": "b3"}):
            crs_utils.apply_patch_build(patch_file, response_dir, "test-builder")

        content = (response_dir / "build_exit_code").read_text()
        assert content == "0"
        int(content)  # Must be parseable as int

    @patch("libCRS.libCRS.local.http_requests")
    def test_pov_exit_code_is_string_integer(self, mock_requests, crs_utils, tmp_path):
        mock_requests.post.return_value = _mock_response(json_data={"exit_code": 77, "stderr": ""})
        response_dir = tmp_path / "resp"

        crs_utils.run_pov(Path("/pov"), "fuzz", "b1", response_dir, "test-runner")

        content = (response_dir / "pov_exit_code").read_text()
        assert content == "77"
        int(content)  # Must be parseable as int

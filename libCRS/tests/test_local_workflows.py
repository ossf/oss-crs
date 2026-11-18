# SPDX-License-Identifier: MIT
"""Unit tests for LocalCRSUtils core workflows, edge cases, and sidecar integration."""

import socket
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests
from libCRS.base import SourceType
from libCRS.local import LocalCRSUtils, _get_build_out_dir


@pytest.fixture
def crs_utils(monkeypatch):
    monkeypatch.setenv("OSS_CRS_NAME", "my-crs")
    monkeypatch.setenv("OSS_CRS_BUILD_OUT_DIR", "/fake/build_out")
    monkeypatch.setenv("OSS_CRS_REBUILD_OUT_DIR", "/fake/rebuild_out")
    monkeypatch.setenv("OSS_CRS_SUBMIT_DIR", "/fake/submit_dir")
    monkeypatch.setenv("BUILDER_MODULE", "default-builder")
    monkeypatch.setenv("RUNNER_MODULE", "default-runner")
    return LocalCRSUtils()


# ===========================================================================
# 1. Path Resolution & Environment Retrieval
# ===========================================================================


def test_get_build_out_dir(monkeypatch):
    monkeypatch.setenv("OSS_CRS_BUILD_OUT_DIR", "/app/custom_build_out")
    assert _get_build_out_dir() == Path("/app/custom_build_out")


def test_get_build_out_dir_missing_raises(monkeypatch):
    monkeypatch.delenv("OSS_CRS_BUILD_OUT_DIR", raising=False)
    with pytest.raises(KeyError, match="OSS_CRS_BUILD_OUT_DIR"):
        _get_build_out_dir()


# ===========================================================================
# 2. Source Download & Validation
# ===========================================================================


def test_download_source_unsupported_type_raises(crs_utils, tmp_path):
    with pytest.raises(ValueError, match="Unsupported source type"):
        crs_utils.download_source("invalid_source_type", tmp_path)


def test_download_source_missing_mount_raises(crs_utils, tmp_path):
    # Non-existent mount paths trigger RuntimeError
    with (
        patch("libCRS.local._FUZZ_PROJ_MOUNT", Path("/nonexistent/fuzz_proj")),
        pytest.raises(RuntimeError, match="does not exist"),
    ):
        crs_utils.download_source(SourceType.FUZZ_PROJ, tmp_path)


def test_download_source_success(crs_utils, tmp_path):
    src_mount = tmp_path / "mount_source"
    src_mount.mkdir()
    (src_mount / "file.c").write_text("code")

    dst_dir = tmp_path / "target_dir"

    with (
        patch("libCRS.local._TARGET_SOURCE_MOUNT", src_mount),
        patch("libCRS.local.rsync_copy") as mock_rsync,
    ):
        crs_utils.download_source(SourceType.TARGET_SOURCE, dst_dir)
        mock_rsync.assert_called_once_with(src_mount, dst_dir)


# ===========================================================================
# 3. Build Output & Artifact Management
# ===========================================================================


def test_submit_build_output_routing(crs_utils, monkeypatch):
    monkeypatch.setenv("OSS_CRS_BUILD_OUT_DIR", "/custom/out")
    with patch("libCRS.local.rsync_copy") as mock_rsync:
        crs_utils.submit_build_output("src/bin", Path("out/bin"))
        mock_rsync.assert_called_once_with(
            Path("src/bin"), Path("/custom/out") / "out/bin"
        )


def test_skip_build_output_creates_dot_skip_file(crs_utils):
    with patch.object(crs_utils, "submit_build_output") as mock_submit:
        crs_utils.skip_build_output("build/target.a")
        mock_submit.assert_called_once()
        src_tmp_file, dst_skip_path = mock_submit.call_args[0]
        assert dst_skip_path == Path("build/.target.a.skip")
        assert Path(src_tmp_file).name != ""


def test_download_build_output_default(crs_utils, monkeypatch, tmp_path):
    monkeypatch.setenv("OSS_CRS_BUILD_OUT_DIR", str(tmp_path / "build_root"))
    monkeypatch.delenv("OSS_CRS_REBUILD_ID", raising=False)

    dst = tmp_path / "local_dest"
    with patch("libCRS.local.rsync_copy") as mock_rsync:
        crs_utils.download_build_output("bin/app", dst)
        expected_src = (tmp_path / "build_root") / "bin/app"
        mock_rsync.assert_called_once_with(expected_src, dst)


def test_download_build_output_explicit_rebuild_id(crs_utils, monkeypatch, tmp_path):
    rebuild_root = tmp_path / "rebuild_root"
    monkeypatch.setenv("OSS_CRS_REBUILD_OUT_DIR", str(rebuild_root))
    monkeypatch.setenv("OSS_CRS_NAME", "my_crs_agent")

    dst = tmp_path / "local_dest"
    with patch("libCRS.local.rsync_copy") as mock_rsync:
        crs_utils.download_build_output("bin/app", dst, rebuild_id=99)
        expected_src = rebuild_root / "my_crs_agent" / "99" / "bin/app"
        mock_rsync.assert_called_once_with(expected_src, dst)


def test_download_build_output_from_environment_rebuild_id(
    crs_utils, monkeypatch, tmp_path
):
    rebuild_root = tmp_path / "rebuild_root"
    monkeypatch.setenv("OSS_CRS_REBUILD_OUT_DIR", str(rebuild_root))
    monkeypatch.setenv("OSS_CRS_NAME", "test_crs")
    monkeypatch.setenv("OSS_CRS_REBUILD_ID", "42")

    dst = tmp_path / "local_dest"
    with patch("libCRS.local.rsync_copy") as mock_rsync:
        crs_utils.download_build_output("bin/app", dst)
        expected_src = rebuild_root / "test_crs" / "42" / "bin/app"
        mock_rsync.assert_called_once_with(expected_src, dst)


# ===========================================================================
# 4. Harness Submission Validation
# ===========================================================================


def test_submit_harness_nonexistent_fuzz_proj_raises(crs_utils, tmp_path):
    missing_dir = tmp_path / "nonexistent_fuzz_proj"
    with pytest.raises(ValueError, match="fuzz_proj_dir does not exist"):
        crs_utils.submit_harness(missing_dir)


def test_submit_harness_invalid_names_raise(crs_utils, tmp_path):
    valid_fuzz_dir = tmp_path / "fuzz_proj"
    valid_fuzz_dir.mkdir()

    # Note: if name="", line 130 does `name = name or fuzz_proj_dir.name`, using "fuzz_proj"
    invalid_names = [".", "..", "invalid/name", "name/with/slash"]
    with patch("libCRS.local.DirSyncHelper.sync_once"):
        for bad_name in invalid_names:
            with pytest.raises(ValueError, match="Invalid harness name"):
                crs_utils.submit_harness(valid_fuzz_dir, name=bad_name)


def test_submit_harness_target_source_not_directory_raises(crs_utils, tmp_path):
    valid_fuzz_dir = tmp_path / "fuzz_proj"
    valid_fuzz_dir.mkdir()
    not_a_dir = tmp_path / "some_file.txt"
    not_a_dir.write_text("content")

    with (
        patch("libCRS.local.DirSyncHelper.sync_once"),
        pytest.raises(ValueError, match="target_source_dir is not a directory"),
    ):
        crs_utils.submit_harness(valid_fuzz_dir, target_source_dir=not_a_dir)


def test_submit_harness_success(crs_utils, monkeypatch, tmp_path):
    submit_base = tmp_path / "submit_area"
    monkeypatch.setenv("OSS_CRS_SUBMIT_DIR", str(submit_base))

    fuzz_dir = tmp_path / "harness_1"
    fuzz_dir.mkdir()
    (fuzz_dir / "Dockerfile").write_text("FROM base")

    target_dir = tmp_path / "target_src"
    target_dir.mkdir()
    (target_dir / "main.c").write_text("int main() {}")

    with patch("libCRS.local.DirSyncHelper.sync_once") as mock_sync:
        crs_utils.submit_harness(
            fuzz_dir, target_source_dir=target_dir, name="custom_harness"
        )
        assert mock_sync.call_count == 2


# ===========================================================================
# 5. Service Domain Resolution & Discovery
# ===========================================================================


def test_get_service_domain_success(crs_utils, monkeypatch):
    monkeypatch.setenv("OSS_CRS_NAME", "cluster_one")
    with patch("socket.gethostbyname", return_value="10.0.0.1"):
        domain = crs_utils.get_service_domain("runner-sidecar")
        assert domain == "runner-sidecar.cluster_one"


def test_get_service_domain_inaccessible_raises(crs_utils, monkeypatch):
    monkeypatch.setenv("OSS_CRS_NAME", "cluster_one")
    with (
        patch("socket.gethostbyname", side_effect=socket.gaierror("Name not found")),
        pytest.raises(RuntimeError, match="is not accessible"),
    ):
        crs_utils.get_service_domain("missing-service")


def test_resolve_builder_default_and_fallback(crs_utils, monkeypatch):
    # Explicit builder takes precedence
    assert crs_utils._resolve_builder("explicit-b") == "explicit-b"

    # Fallback to BUILDER_MODULE
    monkeypatch.setenv("BUILDER_MODULE", "env-builder")
    assert crs_utils._resolve_builder(None) == "env-builder"

    # Missing builder raises ValueError
    monkeypatch.delenv("BUILDER_MODULE", raising=False)
    with pytest.raises(ValueError, match="builder not specified"):
        crs_utils._resolve_builder(None)


def test_resolve_runner_fallback(crs_utils, monkeypatch):
    assert crs_utils._resolve_runner("explicit-r") == "explicit-r"
    monkeypatch.setenv("RUNNER_MODULE", "custom-runner")
    assert crs_utils._resolve_runner(None) == "custom-runner"

    monkeypatch.delenv("RUNNER_MODULE", raising=False)
    assert crs_utils._resolve_runner(None) == "runner-sidecar"


# ===========================================================================
# 6. Service Health Check & Polling Logic
# ===========================================================================


def test_wait_for_service_health_cached_true(crs_utils):
    crs_utils._builders_healthy["cached-service"] = True
    assert crs_utils._wait_for_service_health("cached-service") is True


def test_wait_for_service_health_success(crs_utils):
    with (
        patch.object(crs_utils, "_get_service_url", return_value="http://service:8080"),
        patch("requests.get") as mock_get,
    ):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_get.return_value = mock_resp

        assert crs_utils._wait_for_service_health("new-service", max_wait=5) is True
        assert crs_utils._builders_healthy["new-service"] is True


def test_wait_for_service_health_timeout(crs_utils):
    with (
        patch.object(crs_utils, "_get_service_url", return_value="http://service:8080"),
        patch(
            "requests.get", side_effect=requests.ConnectionError("Connection refused")
        ),
        patch("time.sleep"),
    ):
        assert (
            crs_utils._wait_for_service_health(
                "unreachable-service", max_wait=0.01, initial_interval=0.001
            )
            is False
        )


def test_submit_and_poll_unhealthy_returns_none(crs_utils):
    with patch.object(crs_utils, "_wait_for_service_health", return_value=False):
        result = crs_utils._submit_and_poll("/build", "unhealthy-builder")
        assert result is None


def test_submit_and_poll_immediate_done_cached(crs_utils):
    with (
        patch.object(crs_utils, "_wait_for_service_health", return_value=True),
        patch.object(crs_utils, "_get_service_url", return_value="http://builder:8080"),
        patch("requests.post") as mock_post,
    ):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "id": "job-123",
            "status": "done",
            "result": {"exit_code": 0},
        }
        mock_post.return_value = mock_resp

        res = crs_utils._submit_and_poll("/build", "builder")
        assert res["status"] == "done"
        assert res["result"]["exit_code"] == 0


def test_submit_and_poll_polling_until_done(crs_utils):
    with (
        patch.object(crs_utils, "_wait_for_service_health", return_value=True),
        patch.object(crs_utils, "_get_service_url", return_value="http://builder:8080"),
        patch("requests.post") as mock_post,
        patch("requests.get") as mock_get,
        patch("time.sleep"),
    ):
        mock_post_resp = MagicMock()
        mock_post_resp.json.return_value = {"id": "job-999", "status": "running"}
        mock_post.return_value = mock_post_resp

        resp_running = MagicMock(status_code=200)
        resp_running.json.return_value = {"id": "job-999", "status": "running"}

        resp_done = MagicMock(status_code=200)
        resp_done.json.return_value = {
            "id": "job-999",
            "status": "done",
            "result": {"exit_code": 0, "rebuild_id": 5},
        }

        mock_get.side_effect = [resp_running, resp_done]

        res = crs_utils._submit_and_poll("/build", "builder", poll_interval=0.001)
        assert res["status"] == "done"
        assert res["result"]["rebuild_id"] == 5


def test_submit_and_poll_job_not_found_returns_none(crs_utils):
    with (
        patch.object(crs_utils, "_wait_for_service_health", return_value=True),
        patch.object(crs_utils, "_get_service_url", return_value="http://builder:8080"),
        patch("requests.post") as mock_post,
        patch("requests.get") as mock_get,
        patch("time.sleep"),
    ):
        mock_post_resp = MagicMock()
        mock_post_resp.json.return_value = {"id": "job-404", "status": "running"}
        mock_post.return_value = mock_post_resp

        resp_404 = MagicMock(status_code=404)
        mock_get.return_value = resp_404

        res = crs_utils._submit_and_poll("/build", "builder")
        assert res is None


# ===========================================================================
# 7. Apply Patch, Rebuild & POV Execution
# ===========================================================================


def test_apply_patch_build_builder_failure(crs_utils, tmp_path):
    patch_file = tmp_path / "fix.diff"
    patch_file.write_text("+fix")
    resp_dir = tmp_path / "response_dir"

    with patch.object(crs_utils, "_submit_and_poll", return_value=None):
        exit_code = crs_utils.apply_patch_build(
            patch_file, resp_dir, builder="failing-builder"
        )
        assert exit_code == 1
        assert (resp_dir / "retcode").read_text() == "1"
        assert "Builder unavailable" in (resp_dir / "stderr.log").read_text()


def test_apply_patch_build_success(crs_utils, monkeypatch, tmp_path):
    rebuild_out = tmp_path / "rebuild_out"
    monkeypatch.setenv("OSS_CRS_REBUILD_OUT_DIR", str(rebuild_out))
    monkeypatch.setenv("OSS_CRS_NAME", "test-crs")

    # Seed sidecar output logs
    log_dir = rebuild_out / "test-crs" / "7"
    log_dir.mkdir(parents=True)
    (log_dir / "stdout.log").write_text("Build completed successfully")
    (log_dir / "stderr.log").write_text("")
    (log_dir / "exit_code").write_text("0")

    patch_file = tmp_path / "fix.diff"
    patch_file.write_text("+fix")
    resp_dir = tmp_path / "response_dir"

    mock_result = {
        "status": "done",
        "result": {"exit_code": 0, "rebuild_id": 7},
    }

    with (
        patch.object(crs_utils, "_submit_and_poll", return_value=mock_result),
        patch("libCRS.local.rsync_copy") as mock_rsync,
    ):
        exit_code = crs_utils.apply_patch_build(patch_file, resp_dir, builder="builder")
        assert exit_code == 0
        assert (resp_dir / "retcode").read_text() == "0"
        assert (resp_dir / "rebuild_id").read_text() == "7"
        assert crs_utils._last_rebuild_id == 7
        assert mock_rsync.call_count == 3  # stdout, stderr, exit_code copied


def test_run_pov_runner_unavailable(crs_utils, tmp_path):
    pov_file = tmp_path / "crash.bin"
    pov_file.write_bytes(b"\x90\x90")
    resp_dir = tmp_path / "pov_out"

    with patch.object(crs_utils, "_wait_for_service_health", return_value=False):
        exit_code = crs_utils.run_pov(
            pov_file, "fuzz_target", resp_dir, builder="unhealthy-runner"
        )
        assert exit_code == 1
        assert (resp_dir / "retcode").read_text() == "1"
        assert "Runner unavailable" in (resp_dir / "stderr.log").read_text()


def test_run_pov_crash_reproduced(crs_utils, tmp_path):
    pov_file = tmp_path / "crash.bin"
    pov_file.write_bytes(b"\x90\x90")
    resp_dir = tmp_path / "pov_out"

    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "exit_code": 139,
        "stdout": "Running harness...",
        "stderr": "AddressSanitizer: SEGV on address 0x0",
    }

    with (
        patch.object(crs_utils, "_wait_for_service_health", return_value=True),
        patch.object(crs_utils, "_get_service_url", return_value="http://runner:8080"),
        patch("requests.post", return_value=mock_resp),
    ):
        exit_code = crs_utils.run_pov(
            pov_file, "fuzz_target", resp_dir, rebuild_id="12", builder="runner"
        )
        assert exit_code == 139
        assert (resp_dir / "retcode").read_text() == "139"
        assert "AddressSanitizer: SEGV" in (resp_dir / "stderr.log").read_text()
        assert "Running harness..." in (resp_dir / "stdout.log").read_text()


def test_build_project_no_directories_raises(crs_utils, tmp_path):
    resp_dir = tmp_path / "resp"
    with pytest.raises(ValueError, match="At least one of fuzz_proj_dir"):
        crs_utils.build_project(resp_dir, fuzz_proj_dir=None, target_source_dir=None)

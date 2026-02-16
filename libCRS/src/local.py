import logging
import socket
import time
from pathlib import Path

import requests as http_requests

from .base import CRSUtils, DataType
from .common import rsync_copy, get_env
from .submit import SubmitHelper

logger = logging.getLogger(__name__)


def _get_build_out_dir() -> Path:
    return Path(get_env("OSS_CRS_BUILD_OUT_DIR"))


class LocalCRSUtils(CRSUtils):
    def __init__(self):
        super().__init__()

    def __init_submit_helper(self, data_type: DataType) -> SubmitHelper:
        OSS_CRS_SUBMIT_DIR = Path(get_env("OSS_CRS_SUBMIT_DIR"))
        shared_fs_dir = OSS_CRS_SUBMIT_DIR / data_type.value
        shared_fs_dir.mkdir(parents=True, exist_ok=True)
        return SubmitHelper(data_type, shared_fs_dir)

    def download_build_output(self, src_path: str, dst_path: Path) -> None:
        src = _get_build_out_dir() / src_path
        dst = Path(dst_path)
        rsync_copy(src, dst)

    def submit_build_output(self, src_path: str, dst_path: Path) -> None:
        src = Path(src_path)
        dst = _get_build_out_dir() / dst_path
        rsync_copy(src, dst)

    def skip_build_output(self, dst_path: str) -> None:
        with tempfile.NamedTemporaryFile() as tmp_file:
            tmp_file.write(b"skip")
            tmp_file.flush()
            dst = Path(dst_path)
            skip_file_path = dst.parent / f".{dst.name}.skip"
            self.submit_build_output(tmp_file.name, skip_file_path)

    def register_submit_dir(self, data_type: DataType, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        helper = self.__init_submit_helper(data_type)
        helper.register_dir(path, batch_time=10, batch_size=100)

    def register_shared_dir(self, local_path: Path, shared_path: str) -> None:
        if local_path.exists():
            raise FileExistsError(f"Local path '{local_path}' already exists")

        OSS_CRS_SHARED_DIR = Path(get_env("OSS_CRS_SHARED_DIR"))
        shared_path = OSS_CRS_SHARED_DIR / shared_path
        shared_path.mkdir(parents=True, exist_ok=True)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.symlink_to(shared_path)

    def register_fetch_dir(self, type: DataType, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        raise NotImplementedError("TODO: register_fetch_dir is not yet implemented")

    def submit(self, data_type: DataType, src: Path) -> None:
        helper = self.__init_submit_helper(data_type)
        helper.submit_file(src)

    def fetch(self, type: DataType, dst: Path) -> list[str]:
        raise NotImplementedError("TODO: fetch is not yet implemented")

    def get_service_domain(self, service_name: str) -> str:
        CRS_NAME = get_env("OSS_CRS_NAME")
        ret = f"{service_name}.{CRS_NAME}"

        # Check if the domain is accessible via DNS resolution
        try:
            socket.gethostbyname(ret)
        except socket.gaierror as e:
            raise RuntimeError(f"Service domain '{ret}' is not accessible: {e}")

        return ret

    def _get_builder_url(self) -> str:
        url = get_env("OSS_CRS_BUILDER_URL", allow_none=True)
        if not url:
            raise RuntimeError(
                "OSS_CRS_BUILDER_URL not set. "
                "Set 'incremental_build: true' in crs-compose.yaml."
            )
        return url

    def _submit_and_poll(
        self, endpoint: str, files: dict, data: dict | None = None,
        timeout: int = 600, poll_interval: int = 5,
    ) -> dict | None:
        """Submit a job to the builder sidecar and poll until done.

        Returns the result dict, or None on timeout/404.
        """
        builder_url = self._get_builder_url()
        try:
            resp = http_requests.post(
                f"{builder_url}{endpoint}",
                files=files, data=data or {}, timeout=30,
            )
            resp.raise_for_status()
        finally:
            for f in files.values():
                f.close()

        job_id = resp.json()["id"]
        logger.info("Submitted job %s to %s", job_id, endpoint)

        start = time.monotonic()
        while time.monotonic() - start < timeout:
            resp = http_requests.get(
                f"{builder_url}/status/{job_id}", timeout=10,
            )
            if resp.status_code == 404:
                logger.error("Job %s not found (builder may have restarted)", job_id)
                return None
            result = resp.json()
            if result["status"] == "done":
                return result
            time.sleep(poll_interval)

        logger.error("Job %s timed out after %ds", job_id, timeout)
        return None

    def apply_patch_build(
        self,
        patch_path: Path,
        response_dir: Path,
    ) -> int:
        """Apply a patch and rebuild via the builder sidecar."""
        files = {"patch": open(patch_path, "rb")}
        result = self._submit_and_poll("/build", files)

        response_dir.mkdir(parents=True, exist_ok=True)
        if result is None:
            (response_dir / "build_exit_code").write_text("1")
            (response_dir / "build.log").write_text("Builder unavailable or timed out")
            return 1

        build_exit_code = result.get("build_exit_code", 1)
        (response_dir / "build_exit_code").write_text(str(build_exit_code))
        if "build_log" in result:
            (response_dir / "build.log").write_text(result["build_log"])
        if "id" in result:
            (response_dir / "build_id").write_text(result["id"])

        return build_exit_code

    def run_pov(
        self,
        pov_path: Path,
        harness_name: str,
        build_id: str,
        response_dir: Path,
    ) -> int:
        """Run a POV binary via the builder sidecar."""
        files = {"pov": open(pov_path, "rb")}
        data = {"harness_name": harness_name, "build_id": build_id}
        result = self._submit_and_poll("/run-pov", files, data, timeout=180)

        response_dir.mkdir(parents=True, exist_ok=True)
        if result is None:
            (response_dir / "pov_exit_code").write_text("1")
            (response_dir / "pov_stderr.log").write_text("Builder unavailable or timed out")
            return 1

        pov_exit_code = result.get("pov_exit_code", 1)
        (response_dir / "pov_exit_code").write_text(str(pov_exit_code))
        if "pov_stderr" in result:
            (response_dir / "pov_stderr.log").write_text(result["pov_stderr"])

        return pov_exit_code

    def run_test(
        self,
        test_script: Path,
        build_id: str,
        response_dir: Path,
    ) -> int:
        """Run a test script via the builder sidecar."""
        files = {"test_script": open(test_script, "rb")}
        data = {"build_id": build_id}
        result = self._submit_and_poll("/run-test", files, data, timeout=600)

        response_dir.mkdir(parents=True, exist_ok=True)
        if result is None:
            (response_dir / "test_exit_code").write_text("1")
            (response_dir / "test_stderr.log").write_text("Builder unavailable or timed out")
            return 1

        test_exit_code = result.get("test_exit_code", 1)
        (response_dir / "test_exit_code").write_text(str(test_exit_code))
        if "test_stderr" in result:
            (response_dir / "test_stderr.log").write_text(result["test_stderr"])

        return test_exit_code

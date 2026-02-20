import logging
import socket
import tempfile
import time
from pathlib import Path

import requests as http_requests

from .base import CRSUtils, DataType
from .common import rsync_copy, get_env
from .fetch import FetchHelper
from .submit import SubmitHelper

logger = logging.getLogger(__name__)


def _get_build_out_dir() -> Path:
    return Path(get_env("OSS_CRS_BUILD_OUT_DIR"))


class LocalCRSUtils(CRSUtils):
    def __init__(self):
        super().__init__()
        self._builders_healthy: dict[str, bool] = {}

    def __init_submit_helper(self, data_type: DataType) -> SubmitHelper:
        OSS_CRS_SUBMIT_DIR = Path(get_env("OSS_CRS_SUBMIT_DIR"))
        shared_fs_dir = OSS_CRS_SUBMIT_DIR / data_type.dir_name
        shared_fs_dir.mkdir(parents=True, exist_ok=True)
        return SubmitHelper(shared_fs_dir)

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

    def __init_fetch_helper(self, data_type: DataType) -> FetchHelper:
        return FetchHelper(data_type, self.infra_client)

    def register_fetch_dir(self, data_type: DataType, path: Path) -> None:
        helper = self.__init_fetch_helper(data_type)
        helper.register_dir(path)

    def submit(self, data_type: DataType, src: Path) -> None:
        helper = self.__init_submit_helper(data_type)
        helper.submit_file(src)

    def fetch(self, data_type: DataType, dst: Path) -> list[str]:
        helper = self.__init_fetch_helper(data_type)
        return helper.fetch_once(dst)

    def get_service_domain(self, service_name: str) -> str:
        CRS_NAME = get_env("OSS_CRS_NAME")
        ret = f"{service_name}.{CRS_NAME}"

        # Check if the domain is accessible via DNS resolution
        try:
            socket.gethostbyname(ret)
        except socket.gaierror as e:
            raise RuntimeError(f"Service domain '{ret}' is not accessible: {e}")

        return ret

    def _get_builder_url(self, builder: str) -> str:
        domain = self.get_service_domain(builder)
        return f"http://{domain}:8080"

    def _wait_for_builder_health(
        self, builder: str, max_wait: int = 120, initial_interval: float = 1.0,
    ) -> bool:
        """Poll GET /health until the builder sidecar is reachable.

        Uses exponential backoff (1s, 2s, 4s, ..., capped at 10s).
        Returns True when healthy, False if max_wait exceeded.
        """
        if self._builders_healthy.get(builder, False):
            return True

        builder_url = self._get_builder_url(builder)
        interval = initial_interval
        start = time.monotonic()
        while time.monotonic() - start < max_wait:
            try:
                resp = http_requests.get(
                    f"{builder_url}/health", timeout=5,
                )
                if resp.status_code == 200:
                    logger.info("Builder sidecar '%s' is healthy", builder)
                    self._builders_healthy[builder] = True
                    return True
            except (http_requests.ConnectionError, http_requests.Timeout):
                pass
            elapsed = time.monotonic() - start
            logger.debug(
                "Builder '%s' not ready (%.0fs elapsed), retrying in %.1fs...",
                builder, elapsed, interval,
            )
            time.sleep(interval)
            interval = min(interval * 2, 10.0)

        logger.error("Builder sidecar '%s' not healthy after %ds", builder, max_wait)
        return False

    def _submit_and_poll(
        self, endpoint: str, builder: str,
        files: dict | None = None,
        data: dict | None = None,
        timeout: int = 600, poll_interval: int = 5,
    ) -> dict | None:
        """Submit a job to the builder sidecar and poll until done.

        Waits for the builder to be healthy before submitting.
        Returns the result dict, or None on timeout/connection error.
        """
        if not self._wait_for_builder_health(builder):
            return None

        builder_url = self._get_builder_url(builder)
        try:
            resp = http_requests.post(
                f"{builder_url}{endpoint}",
                files=files, data=data or {}, timeout=30,
            )
            resp.raise_for_status()
        except (http_requests.ConnectionError, http_requests.Timeout, http_requests.HTTPError) as e:
            logger.error("Failed to submit job to %s: %s", endpoint, e)
            return None

        job_id = resp.json()["id"]
        logger.info("Submitted job %s to %s", job_id, endpoint)

        start = time.monotonic()
        while time.monotonic() - start < timeout:
            try:
                resp = http_requests.get(
                    f"{builder_url}/status/{job_id}", timeout=10,
                )
            except (http_requests.ConnectionError, http_requests.Timeout) as e:
                logger.warning("Connection error polling %s: %s", job_id, e)
                time.sleep(poll_interval)
                continue
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
        builder: str,
    ) -> int:
        """Apply a patch and rebuild via the builder sidecar."""
        with open(patch_path, "rb") as patch_file:
            files = {"patch": patch_file}
            result = self._submit_and_poll("/build", builder, files)

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
        builder: str,
    ) -> int:
        """Run a POV binary via the builder sidecar."""
        with open(pov_path, "rb") as pov_file:
            files = {"pov": pov_file}
            data = {"harness_name": harness_name, "build_id": build_id}
            result = self._submit_and_poll("/run-pov", builder, files, data, timeout=180)

        response_dir.mkdir(parents=True, exist_ok=True)
        if result is None:
            (response_dir / "pov_exit_code").write_text("1")
            (response_dir / "pov_stderr.log").write_text("Builder unavailable or timed out")
            return 1

        pov_exit_code = result.get("pov_exit_code", 1)
        (response_dir / "pov_exit_code").write_text(str(pov_exit_code))
        if "pov_stderr" in result:
            (response_dir / "pov_stderr.log").write_text(result["pov_stderr"])
        if "pov_stdout" in result:
            (response_dir / "pov_stdout.log").write_text(result["pov_stdout"])

        return pov_exit_code

    def run_test(
        self,
        build_id: str,
        response_dir: Path,
        builder: str,
    ) -> int:
        """Run the project's bundled test.sh via the builder sidecar."""
        data = {"build_id": build_id}
        result = self._submit_and_poll("/run-test", builder, data=data, timeout=600)

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

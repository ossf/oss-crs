import logging
import os
import socket
import tempfile
import time
from pathlib import Path

import requests as http_requests

from .base import CRSUtils, DataType, SourceType
from .common import rsync_copy, get_env
from .fetch import FetchHelper
from .submit import SubmitHelper

logger = logging.getLogger(__name__)

_FUZZ_PROJ_MOUNT = Path("/OSS_CRS_FUZZ_PROJ")
_TARGET_SOURCE_MOUNT = Path("/OSS_CRS_TARGET_SOURCE")


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

    def download_source(self, source_type: SourceType, dst_path: Path) -> None:
        if source_type == SourceType.FUZZ_PROJ:
            src = _FUZZ_PROJ_MOUNT
        elif source_type == SourceType.TARGET_SOURCE:
            src = _TARGET_SOURCE_MOUNT
        else:
            raise ValueError(f"Unsupported source type: {source_type}")

        if not src.exists():
            raise RuntimeError(f"download-source {source_type}: {src} does not exist")
        rsync_copy(src, Path(dst_path))

    def submit_build_output(
        self, src_path: str, dst_path: Path, rebuild_id: "int | None" = None
    ) -> None:
        """Write build output to the build output directory.

        Both build-target and run phases use the same mechanism: rsync to the
        mounted OSS_CRS_BUILD_OUT_DIR. The ephemeral container mounts the
        rebuild output dir at the same path, so no HTTP upload is needed.
        """
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

        shared_root = Path(get_env("OSS_CRS_SHARED_DIR"))
        target_shared_path = shared_root / shared_path
        target_shared_path.mkdir(parents=True, exist_ok=True)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.symlink_to(target_shared_path)

    def register_log_dir(self, local_path: Path) -> None:
        if local_path.exists():
            raise FileExistsError(f"Local path '{local_path}' already exists")

        OSS_CRS_LOG_DIR = Path(get_env("OSS_CRS_LOG_DIR"))
        log_target = OSS_CRS_LOG_DIR / local_path.name
        log_target.mkdir(parents=True, exist_ok=True)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.symlink_to(log_target)

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

    def _get_service_url(self, service_name: str) -> str:
        domain = self.get_service_domain(service_name)
        return f"http://{domain}:8080"

    def _get_builder_url(self, builder: str) -> str:
        return self._get_service_url(builder)

    def _resolve_builder(self, builder: "str | None") -> str:
        if builder:
            return builder
        default = os.environ.get("BUILDER_MODULE")
        if default:
            return default
        raise ValueError(
            "builder not specified and BUILDER_MODULE env var not set. "
            "Pass --builder explicitly or set BUILDER_MODULE in crs.yaml additional_env."
        )

    def _resolve_runner(self, runner: "str | None") -> str:
        if runner:
            return runner
        return os.environ.get("RUNNER_MODULE", "runner-sidecar")

    def _wait_for_service_health(
        self,
        service_name: str,
        max_wait: int = 120,
        initial_interval: float = 1.0,
    ) -> bool:
        """Poll GET /health until the service sidecar is reachable.

        Uses exponential backoff (1s, 2s, 4s, ..., capped at 10s).
        Returns True when healthy, False if max_wait exceeded.
        """
        if self._builders_healthy.get(service_name, False):
            return True

        service_url = self._get_service_url(service_name)
        interval = initial_interval
        start = time.monotonic()
        while time.monotonic() - start < max_wait:
            try:
                resp = http_requests.get(
                    f"{service_url}/health",
                    timeout=5,
                )
                if resp.status_code == 200:
                    logger.info("Service sidecar '%s' is healthy", service_name)
                    self._builders_healthy[service_name] = True
                    return True
            except (http_requests.ConnectionError, http_requests.Timeout):
                pass
            elapsed = time.monotonic() - start
            logger.debug(
                "Service '%s' not ready (%.0fs elapsed), retrying in %.1fs...",
                service_name,
                elapsed,
                interval,
            )
            time.sleep(interval)
            interval = min(interval * 2, 10.0)

        logger.error(
            "Service sidecar '%s' not healthy after %ds", service_name, max_wait
        )
        return False

    def _wait_for_builder_health(
        self,
        builder: str,
        max_wait: int = 120,
        initial_interval: float = 1.0,
    ) -> bool:
        """Thin wrapper around _wait_for_service_health for backward compatibility."""
        return self._wait_for_service_health(builder, max_wait, initial_interval)

    def _submit_and_poll(
        self,
        endpoint: str,
        builder: str,
        files: dict | None = None,
        data: dict | None = None,
        timeout: int = 600,
        poll_interval: int = 5,
    ) -> dict | None:
        """Submit a job to the builder sidecar and poll until done.

        Waits for the service to be healthy before submitting.
        Returns the result dict, or None on timeout/connection error.
        """
        if not self._wait_for_service_health(builder):
            return None

        service_url = self._get_service_url(builder)
        try:
            resp = http_requests.post(
                f"{service_url}{endpoint}",
                files=files,
                data=data or {},
                timeout=30,
            )
            resp.raise_for_status()
        except (
            http_requests.ConnectionError,
            http_requests.Timeout,
            http_requests.HTTPError,
        ) as e:
            logger.error("Failed to submit job to %s: %s", endpoint, e)
            return None

        job_id = resp.json()["id"]
        logger.info("Submitted job %s to %s", job_id, endpoint)

        start = time.monotonic()
        while time.monotonic() - start < timeout:
            try:
                resp = http_requests.get(
                    f"{service_url}/status/{job_id}",
                    timeout=10,
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
        builder: "str | None" = None,
        builder_name: "str | None" = None,
        rebuild_id: "int | None" = None,
    ) -> int:
        """Apply a patch and rebuild via the builder sidecar."""
        builder = self._resolve_builder(builder)
        crs_name = os.environ.get("OSS_CRS_NAME", "unknown")
        cpuset = os.environ.get("OSS_CRS_CPUSET", "")
        mem_limit = os.environ.get("OSS_CRS_MEMORY_LIMIT", "")
        with open(patch_path, "rb") as patch_file:
            files = {"patch": patch_file}
            data = {
                "crs_name": crs_name,
                "builder_name": builder_name or "",
                "cpuset": cpuset,
                "mem_limit": mem_limit,
            }
            if rebuild_id is not None:
                data["rebuild_id"] = str(rebuild_id)
            result = self._submit_and_poll("/build", builder, files, data)

        response_dir.mkdir(parents=True, exist_ok=True)
        if result is None:
            (response_dir / "retcode").write_text("1")
            (response_dir / "stderr.log").write_text("Builder unavailable or timed out")
            return 1

        inner = result.get("result") or {}
        exit_code = inner.get("exit_code", 1)
        rid = inner.get("rebuild_id")

        # Copy logs from the shared rebuild output dir (written by the sidecar)
        # rather than relying on HTTP-streamed stdout/stderr.
        if rid is not None:
            crs_name = os.environ.get("OSS_CRS_NAME", "unknown")
            rebuild_out = Path(get_env("OSS_CRS_REBUILD_OUT_DIR"))
            log_src = rebuild_out / crs_name / str(rid)
            for log_file in ("stdout.log", "stderr.log", "exit_code"):
                src = log_src / log_file
                if src.exists():
                    rsync_copy(src, response_dir / log_file)

        (response_dir / "retcode").write_text(str(exit_code))
        if exit_code == 0 and rid is not None:
            (response_dir / "rebuild_id").write_text(str(rid))
            self._last_rebuild_id = rid

        return exit_code

    def run_pov(
        self,
        pov_path: Path,
        harness_name: str,
        rebuild_id: int,
        response_dir: Path,
        builder: "str | None" = None,
    ) -> int:
        """Run a POV binary against a specific rebuild's output via the runner sidecar."""
        builder = self._resolve_runner(builder)
        if not self._wait_for_service_health(builder):
            response_dir.mkdir(parents=True, exist_ok=True)
            (response_dir / "retcode").write_text("1")
            (response_dir / "stderr.log").write_text("Runner unavailable")
            return 1

        crs_name = os.environ.get("OSS_CRS_NAME", "unknown")
        runner_url = self._get_service_url(builder)
        data = {
            "harness_name": harness_name,
            "rebuild_id": str(rebuild_id),
            "crs_name": crs_name,
        }
        try:
            with open(pov_path, "rb") as f:
                files = {
                    "pov": (
                        pov_path.name if hasattr(pov_path, "name") else str(pov_path),
                        f,
                        "application/octet-stream",
                    )
                }
                resp = http_requests.post(
                    f"{runner_url}/run-pov",
                    data=data,
                    files=files,
                    timeout=180,
                )
            resp.raise_for_status()
            result = resp.json()
        except (
            http_requests.ConnectionError,
            http_requests.Timeout,
            http_requests.HTTPError,
        ) as e:
            logger.error("Failed to run POV: %s", e)
            response_dir.mkdir(parents=True, exist_ok=True)
            (response_dir / "retcode").write_text("1")
            (response_dir / "stderr.log").write_text(str(e))
            return 1

        response_dir.mkdir(parents=True, exist_ok=True)
        exit_code = result.get("exit_code", 1)
        (response_dir / "retcode").write_text(str(exit_code))
        stdout = result.get("stdout", "")
        stderr = result.get("stderr", "")
        if stdout:
            (response_dir / "stdout.log").write_text(stdout)
        if stderr:
            (response_dir / "stderr.log").write_text(stderr)

        return exit_code

    def apply_patch_test(
        self,
        patch_path: Path,
        response_dir: Path,
        builder: "str | None" = None,
    ) -> int:
        """Apply a patch and run the project's bundled test.sh via the builder sidecar."""
        builder = self._resolve_builder(builder)
        crs_name = os.environ.get("OSS_CRS_NAME", "unknown")
        cpuset = os.environ.get("OSS_CRS_CPUSET", "")
        mem_limit = os.environ.get("OSS_CRS_MEMORY_LIMIT", "")
        with open(patch_path, "rb") as patch_file:
            files = {"patch": patch_file}
            data = {
                "crs_name": crs_name,
                "cpuset": cpuset,
                "mem_limit": mem_limit,
            }
            result = self._submit_and_poll("/test", builder, files, data, timeout=600)

        response_dir.mkdir(parents=True, exist_ok=True)
        if result is None:
            (response_dir / "retcode").write_text("1")
            (response_dir / "stderr.log").write_text("Builder unavailable or timed out")
            return 1

        inner = result.get("result") or {}
        exit_code = inner.get("exit_code", 1)
        rid = inner.get("rebuild_id")

        if rid is not None:
            crs_name = os.environ.get("OSS_CRS_NAME", "unknown")
            rebuild_out = Path(get_env("OSS_CRS_REBUILD_OUT_DIR"))
            log_src = rebuild_out / crs_name / str(rid)
            for log_file in ("stdout.log", "stderr.log", "exit_code"):
                src = log_src / log_file
                if src.exists():
                    rsync_copy(src, response_dir / log_file)

        (response_dir / "retcode").write_text(str(exit_code))

        return exit_code

    def download_build_output(
        self, src_path: str, dst_path: Path, rebuild_id: "int | None" = None
    ) -> None:
        """Download build artifacts.

        Args:
            src_path: Relative path within the build output directory (e.g. "build").
            dst_path: Local destination path.
            rebuild_id: If provided, copy from the rebuild output directory
                        (OSS_CRS_REBUILD_OUT_DIR/{crs_name}/{rebuild_id}/{src_path}).
                        If None, copy from build-target output (OSS_CRS_BUILD_OUT_DIR/{src_path}).

        Inside an ephemeral container (OSS_CRS_REBUILD_ID set), uses that
        rebuild_id automatically.
        """
        if rebuild_id is None:
            env_rid = os.environ.get("OSS_CRS_REBUILD_ID")
            if env_rid:
                rebuild_id = int(env_rid)

        if rebuild_id is not None:
            crs_name = os.environ.get("OSS_CRS_NAME", "unknown")
            rebuild_out = Path(get_env("OSS_CRS_REBUILD_OUT_DIR"))
            src = rebuild_out / crs_name / str(rebuild_id) / src_path
        else:
            src = _get_build_out_dir() / src_path

        dst = Path(dst_path)
        rsync_copy(src, dst)

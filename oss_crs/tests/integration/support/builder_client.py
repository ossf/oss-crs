import logging
import time
from pathlib import Path

import requests as http_requests

logger = logging.getLogger(__name__)


class BuilderClient:
    """Test-only HTTP client for the builder sidecar API."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self._healthy = False

    def wait_for_health(
        self, max_wait: int = 120, initial_interval: float = 1.0,
    ) -> bool:
        if self._healthy:
            return True

        interval = initial_interval
        start = time.monotonic()
        while time.monotonic() - start < max_wait:
            try:
                resp = http_requests.get(f"{self.base_url}/health", timeout=5)
                if resp.status_code == 200:
                    logger.info("Builder at '%s' is healthy", self.base_url)
                    self._healthy = True
                    return True
            except (http_requests.ConnectionError, http_requests.Timeout):
                pass
            time.sleep(interval)
            interval = min(interval * 2, 10.0)

        logger.error("Builder at '%s' not healthy after %ds", self.base_url, max_wait)
        return False

    def submit_and_poll(
        self,
        endpoint: str,
        files: dict | None = None,
        data: dict | None = None,
        timeout: int = 600,
        poll_interval: int = 5,
    ) -> dict | None:
        if not self.wait_for_health():
            return None

        try:
            resp = http_requests.post(
                f"{self.base_url}{endpoint}",
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
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            try:
                resp = http_requests.get(
                    f"{self.base_url}/status/{job_id}", timeout=10,
                )
            except (http_requests.ConnectionError, http_requests.Timeout):
                time.sleep(poll_interval)
                continue
            if resp.status_code == 404:
                return None
            result = resp.json()
            if result["status"] == "done":
                return result
            time.sleep(poll_interval)

        return None

    def apply_patch_build(self, patch_path: Path, response_dir: Path) -> int:
        with open(patch_path, "rb") as patch_file:
            result = self.submit_and_poll("/build", files={"patch": patch_file})

        response_dir.mkdir(parents=True, exist_ok=True)
        if result is None:
            (response_dir / "build_exit_code").write_text("1")
            (response_dir / "build.log").write_text("Builder unavailable or timed out")
            return 1

        build_exit_code = result.get("build_exit_code", 1)
        (response_dir / "build_exit_code").write_text(str(build_exit_code))
        if "build_log" in result:
            (response_dir / "build.log").write_text(result["build_log"])
        if "build_stdout" in result:
            (response_dir / "build_stdout.log").write_text(result["build_stdout"])
        if "build_stderr" in result:
            (response_dir / "build_stderr.log").write_text(result["build_stderr"])
        if build_exit_code == 0 and "id" in result:
            (response_dir / "build_id").write_text(result["id"])

        return build_exit_code

    def run_pov(
        self,
        pov_path: Path,
        harness_name: str,
        build_id: str,
        response_dir: Path,
    ) -> int:
        with open(pov_path, "rb") as pov_file:
            result = self.submit_and_poll(
                "/run-pov",
                files={"pov": pov_file},
                data={"harness_name": harness_name, "build_id": build_id},
                timeout=180,
            )

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

    def run_test(self, build_id: str, response_dir: Path) -> int:
        result = self.submit_and_poll(
            "/run-test", data={"build_id": build_id}, timeout=600,
        )

        response_dir.mkdir(parents=True, exist_ok=True)
        if result is None:
            (response_dir / "test_exit_code").write_text("1")
            (response_dir / "test_stderr.log").write_text("Builder unavailable or timed out")
            return 1

        test_exit_code = result.get("test_exit_code", 1)
        (response_dir / "test_exit_code").write_text(str(test_exit_code))
        if "test_stdout" in result:
            (response_dir / "test_stdout.log").write_text(result["test_stdout"])
        if "test_stderr" in result:
            (response_dir / "test_stderr.log").write_text(result["test_stderr"])

        return test_exit_code

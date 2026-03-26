"""Docker SDK container lifecycle module for the builder sidecar.

Provides five public functions:
    get_build_image   - Resolve base image or build snapshot for a given build_id and builder_name
    get_test_image    - Resolve base image or test snapshot for a given build_id (no builder_name, per D-05)
    next_version      - Determine the next version number by scanning v{N} dirs
    run_ephemeral_build - Full ephemeral container lifecycle: create/start/wait/commit/remove
    run_ephemeral_test  - Test container lifecycle: create/start/wait/commit/remove (no artifacts)
"""

import os
import re
import shutil
import tempfile
from pathlib import Path

import docker
import docker.errors
import requests.exceptions


def get_image_cmd(client: "docker.DockerClient", image: str) -> list[str]:
    """Extract CMD from a Docker image via inspect.

    Returns the image's Cmd as a list of strings, or ["compile"] as fallback.
    """
    try:
        img = client.images.get(image)
        cmd = img.attrs.get("Config", {}).get("Cmd")
        if cmd:
            return cmd
    except docker.errors.ImageNotFound:
        pass
    return ["compile"]


_OSS_FUZZ_ENV_KEYS = ("FUZZING_ENGINE", "SANITIZER", "ARCHITECTURE", "FUZZING_LANGUAGE")


def _oss_fuzz_env() -> dict[str, str]:
    """Forward OSS-Fuzz env vars from the sidecar into ephemeral containers."""
    return {k: v for k in _OSS_FUZZ_ENV_KEYS if (v := os.environ.get(k)) is not None}


def _stream_and_capture_logs(container, prefix: str) -> tuple[str, str]:
    """Stream container logs to sidecar stdout with a prefix, and return captured stdout/stderr.

    Streams combined output in real-time so it appears in docker compose logs,
    then captures stdout and stderr separately for the response.
    """
    # Stream combined output in real-time
    for chunk in container.logs(stream=True, follow=True):
        for line in chunk.decode("utf-8", errors="replace").splitlines():
            print(f"[{prefix}] {line}", flush=True)

    # Capture separated stdout/stderr for the response
    stdout = container.logs(stdout=True, stderr=False).decode("utf-8", errors="replace")
    stderr = container.logs(stdout=False, stderr=True).decode("utf-8", errors="replace")
    return stdout, stderr


def _incremental_build_enabled() -> bool:
    """Check if --incremental-build flag is active via INCREMENTAL_BUILD env var."""
    return os.environ.get("INCREMENTAL_BUILD", "").lower() in ("1", "true")


def get_build_image(client: "docker.DockerClient", build_id: str, base_image: str, builder_name: str) -> str:
    """Return the snapshot image if incremental build is enabled and snapshot exists, otherwise base_image."""
    if not _incremental_build_enabled():
        return base_image
    snapshot_tag = f"oss-crs-snapshot:build-{builder_name}-{build_id}"
    try:
        client.images.get(snapshot_tag)
        return snapshot_tag
    except docker.errors.ImageNotFound:
        return base_image


def get_test_image(client: "docker.DockerClient", build_id: str, base_image: str) -> str:
    """Return test snapshot if incremental build is enabled and snapshot exists, otherwise base_image."""
    if not _incremental_build_enabled():
        return base_image
    snapshot_tag = f"oss-crs-snapshot:test-{build_id}"
    try:
        client.images.get(snapshot_tag)
        return snapshot_tag
    except docker.errors.ImageNotFound:
        return base_image


def next_version(rebuild_out_dir: Path) -> int:
    """Determine the next version integer by scanning existing v{N} directories.

    Implements ART-01, ART-02, ART-05: version counter derived from filesystem state,
    so it persists across server restarts without a separate state file.

    Args:
        rebuild_out_dir: Host path containing versioned output directories (v1, v2, ...).

    Returns:
        Next version integer (1 if no v{N} directories exist).
    """
    if not rebuild_out_dir.exists():
        return 1

    version_re = re.compile(r"^v(\d+)$")
    existing = [
        int(m.group(1))
        for d in rebuild_out_dir.iterdir()
        if d.is_dir() and (m := version_re.match(d.name))
    ]
    return max(existing, default=0) + 1


def run_ephemeral_build(
    base_image: str,
    build_id: str,
    builder_name: str,
    patch_bytes: bytes,
    rebuild_out_dir: Path,
    version_override: "str | None" = None,
    timeout: int = 1800,
) -> dict:
    """Run a build inside an ephemeral Docker container.

    Full lifecycle: create -> start -> wait -> (commit on success) -> remove.
    Container is always removed regardless of success or failure (BUILD-02).
    Snapshot committed only on first success (BUILD-04, first-write-wins).
    Version directory created only on successful build (ART-02 compliance).

    Args:
        base_image: Docker image to use as base (already resolved via get_build_image).
        build_id: The CLI-provided build ID; used as snapshot tag and container env var.
        builder_name: Builder identifier (e.g. 'crs-libfuzzer') used in snapshot tag.
        patch_bytes: Raw bytes of the patch file to apply inside the container.
        rebuild_out_dir: Host path for versioned artifact output.
        version_override: Optional explicit version string (e.g. 'final', 'fix-cve-123').
                          If None, auto-increments based on filesystem scan (ART-03).
        timeout: Maximum seconds to wait for the build to complete (BUILD-06, default 30 min).

    Returns:
        Dict with keys: exit_code, stdout, stderr, build_id, artifacts_version.
    """
    # Step 1: Create Docker client inside function (not at module level — anti-pattern).
    client = docker.from_env()
    container = None

    # Step 2: Determine version string before build (counter snapshot before container starts).
    version_str = version_override if version_override is not None else f"v{next_version(rebuild_out_dir)}"

    # Step 3: Compute version_dir path but do NOT create it yet (ART-02: only on success).
    version_dir = rebuild_out_dir / version_str

    # Step 5: Check if snapshot already exists (first-write-wins logic).
    snapshot_tag = f"oss-crs-snapshot:build-{builder_name}-{build_id}"
    try:
        client.images.get(snapshot_tag)
        snapshot_exists = True
    except docker.errors.ImageNotFound:
        snapshot_exists = False

    # Extract the build command from the base image's CMD.
    build_cmd = get_image_cmd(client, base_image)

    # Steps 4 and 6: Use temp directories for patch delivery and /out bind mount.
    with tempfile.TemporaryDirectory() as tmpdir, tempfile.TemporaryDirectory() as tmp_out_dir:
        # Step 4: Write patch to temp dir for bind-mount into container.
        patch_path = Path(tmpdir) / "patch.diff"
        patch_path.write_bytes(patch_bytes)

        # Step 7: Create container with patch, out, and handler script bind mounts.
        handler_path = os.environ.get("HANDLER_SCRIPT_PATH", "/oss_crs_handler.sh")
        try:
            container = client.containers.create(
                base_image,
                command=["bash", "/usr/local/bin/oss_crs_handler.sh", "/patch/patch.diff", "/out"],
                volumes={
                    tmpdir: {"bind": "/patch", "mode": "ro"},
                    tmp_out_dir: {"bind": "/out", "mode": "rw"},
                    handler_path: {"bind": "/usr/local/bin/oss_crs_handler.sh", "mode": "ro"},
                },
                environment={
                    "OSS_CRS_BUILD_ID": build_id,
                    "OSS_CRS_BUILD_CMD": " ".join(build_cmd),
                    "REPLAY_ENABLED": "",
                    **_oss_fuzz_env(),
                },
                detach=True,
            )

            # Step 8: Start the container.
            container.start()

            # Step 9: Wait for container to finish; catch timeout.
            try:
                result = container.wait(timeout=timeout)
            except requests.exceptions.ReadTimeout:
                try:
                    container.kill()
                except Exception:
                    pass
                return {
                    "exit_code": 1,
                    "stdout": "",
                    "stderr": f"Build timed out after {timeout} seconds",
                    "build_id": build_id,
                    "artifacts_version": version_str,
                }

            # Step 10: Extract exit code.
            exit_code = result["StatusCode"]

            # Step 11: Stream logs to sidecar stdout and capture for response.
            stdout, stderr = _stream_and_capture_logs(container, f"build:{builder_name}")

            # Step 11a: Only on success — create versioned output dir and copy artifacts.
            if exit_code == 0:
                version_dir.mkdir(parents=True, exist_ok=True)
                shutil.copytree(tmp_out_dir, str(version_dir), dirs_exist_ok=True)

            # Step 12: Commit snapshot on first successful build (first-write-wins).
            if exit_code == 0 and not snapshot_exists:
                container.commit(repository="oss-crs-snapshot", tag=f"build-{builder_name}-{build_id}")

            # Step 13: Return result dict.
            return {
                "exit_code": exit_code,
                "stdout": stdout,
                "stderr": stderr,
                "build_id": build_id,
                "artifacts_version": version_str,
            }

        finally:
            # Step 14: Always remove container regardless of success or failure (BUILD-02).
            if container is not None:
                try:
                    container.remove(force=True)
                except docker.errors.NotFound:
                    pass


def run_ephemeral_test(
    base_image: str,
    build_id: str,
    builder_name: str,
    patch_bytes: bytes,
    timeout: int = 1800,
) -> dict:
    """Run a test inside an ephemeral Docker container.

    Full lifecycle: create -> start -> wait -> (commit on first success) -> remove.
    Container is always removed regardless of success or failure.
    Snapshot committed only on first success (first-write-wins), tagged test-{build_id} per D-05.
    No versioned artifact output — test.sh handles recompile + test internally.

    Implements TEST-01, TEST-02: separate ephemeral container, test snapshot on first success.

    Args:
        base_image: Docker image to use as base (already resolved via get_test_image).
        build_id: The CLI-provided build ID; used as snapshot tag and container env var.
        builder_name: Builder identifier (e.g. 'crs-libfuzzer') used in snapshot tag.
        patch_bytes: Raw bytes of the patch file to apply inside the container.
        timeout: Maximum seconds to wait for the test to complete (default 30 min).

    Returns:
        Dict with keys: exit_code, stdout, stderr, build_id, artifacts_version (always None).
    """
    client = docker.from_env()
    container = None

    # Check if test snapshot already exists (first-write-wins logic).
    # D-05: test snapshot is per-build (no builder_name) — project image is singular per build.
    snapshot_tag = f"oss-crs-snapshot:test-{build_id}"
    try:
        client.images.get(snapshot_tag)
        snapshot_exists = True
    except docker.errors.ImageNotFound:
        snapshot_exists = False

    with tempfile.TemporaryDirectory() as tmpdir:
        patch_path = Path(tmpdir) / "patch.diff"
        patch_path.write_bytes(patch_bytes)

        handler_path = os.environ.get("HANDLER_SCRIPT_PATH", "/oss_crs_handler.sh")
        try:
            container = client.containers.create(
                base_image,
                command=["bash", "/usr/local/bin/oss_crs_handler.sh", "/patch/patch.diff", "/out"],
                volumes={
                    tmpdir: {"bind": "/patch", "mode": "ro"},
                    handler_path: {"bind": "/usr/local/bin/oss_crs_handler.sh", "mode": "ro"},
                },
                environment={
                    "OSS_CRS_BUILD_ID": build_id,
                    "OSS_CRS_BUILD_CMD": "bash /OSS_CRS_PROJ_PATH/test.sh",
                    "REPLAY_ENABLED": "",
                    **_oss_fuzz_env(),
                },
                detach=True,
            )

            container.start()

            try:
                result = container.wait(timeout=timeout)
            except requests.exceptions.ReadTimeout:
                try:
                    container.kill()
                except Exception:
                    pass
                return {
                    "exit_code": 1,
                    "stdout": "",
                    "stderr": f"Test timed out after {timeout} seconds",
                    "build_id": build_id,
                    "artifacts_version": None,
                }

            exit_code = result["StatusCode"]

            stdout, stderr = _stream_and_capture_logs(container, f"test:{build_id}")

            # Commit test snapshot on first successful test (first-write-wins, TEST-02).
            # D-05: tag is test-{build_id} (no builder_name — project image is per-build).
            if exit_code == 0 and not snapshot_exists:
                container.commit(repository="oss-crs-snapshot", tag=f"test-{build_id}")

            return {
                "exit_code": exit_code,
                "stdout": stdout,
                "stderr": stderr,
                "build_id": build_id,
                "artifacts_version": None,
            }

        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except docker.errors.NotFound:
                    pass

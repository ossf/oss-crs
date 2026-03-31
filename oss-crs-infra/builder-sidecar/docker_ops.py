"""Docker SDK container lifecycle module for the builder sidecar.

Provides public functions:
    get_build_image   - Resolve base image or build snapshot (gated on INCREMENTAL_BUILD)
    get_test_image    - Resolve base image or test snapshot (gated on INCREMENTAL_BUILD)
    next_rebuild_id   - Determine next rebuild ID by scanning {crs_name}/ dirs
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


def _resource_kwargs(cpuset: "str | None", mem_limit: "str | None") -> dict:
    """Build Docker SDK resource limit kwargs from CRS resource config."""
    kwargs = {}
    if cpuset:
        kwargs["cpuset_cpus"] = cpuset
    if mem_limit:
        kwargs["mem_limit"] = mem_limit
    return kwargs


_OSS_FUZZ_ENV_KEYS = ("FUZZING_ENGINE", "SANITIZER", "ARCHITECTURE", "FUZZING_LANGUAGE")


def _oss_fuzz_env() -> dict[str, str]:
    """Forward OSS-Fuzz env vars from the sidecar into ephemeral containers."""
    return {k: v for k in _OSS_FUZZ_ENV_KEYS if (v := os.environ.get(k)) is not None}


def _incremental_build_enabled() -> bool:
    """Check if --incremental-build flag is active via INCREMENTAL_BUILD env var."""
    return os.environ.get("INCREMENTAL_BUILD", "").lower() in ("1", "true")


def _stream_and_capture_logs(container, prefix: str) -> tuple[str, str]:
    """Stream container logs to sidecar stdout with a prefix, and return captured stdout/stderr.

    Streams combined output in real-time so it appears in docker compose logs,
    then captures stdout and stderr separately for the response.
    """
    for chunk in container.logs(stream=True, follow=True):
        for line in chunk.decode("utf-8", errors="replace").splitlines():
            print(f"[{prefix}] {line}", flush=True)

    stdout = container.logs(stdout=True, stderr=False).decode("utf-8", errors="replace")
    stderr = container.logs(stdout=False, stderr=True).decode("utf-8", errors="replace")
    return stdout, stderr


def _get_build_id() -> str:
    """Get the CLI build_id from the sidecar's environment (set by compose template)."""
    return os.environ.get("OSS_CRS_BUILD_ID", "")


def _build_snapshot_tag(crs_name: str, builder_name: str, build_id: str) -> str:
    """Full image tag for a builder snapshot.

    Must match the format in oss_crs/src/utils.py:build_snapshot_tag().
    """
    return f"oss-crs-snapshot:build__{crs_name}__{builder_name}__{build_id}"


def get_build_image(client: "docker.DockerClient", base_image: str, builder_name: str, crs_name: str) -> str:
    """Return snapshot image if incremental build enabled and snapshot exists, otherwise base_image.

    Snapshot tag uses the CLI build_id (from build-target --incremental-build),
    not the rebuild_id (which versions rebuild artifacts).
    """
    if not _incremental_build_enabled():
        return base_image
    build_id = _get_build_id()
    if not build_id:
        return base_image
    snapshot_tag = _build_snapshot_tag(crs_name, builder_name, build_id)
    try:
        client.images.get(snapshot_tag)
        return snapshot_tag
    except docker.errors.ImageNotFound:
        return base_image


def get_test_image(client: "docker.DockerClient", base_image: str) -> str:
    """Return test snapshot if incremental build enabled and snapshot exists, otherwise base_image."""
    if not _incremental_build_enabled():
        return base_image
    build_id = _get_build_id()
    if not build_id:
        return base_image
    snapshot_tag = f"oss-crs-snapshot:test-{build_id}"
    try:
        client.images.get(snapshot_tag)
        return snapshot_tag
    except docker.errors.ImageNotFound:
        return base_image


def rebuild_output_dir(rebuild_out_dir: Path, crs_name: str, rebuild_id: int) -> Path:
    """Resolve the output directory for a specific CRS rebuild."""
    return rebuild_out_dir / crs_name / str(rebuild_id)


def next_rebuild_id(rebuild_out_dir: Path, crs_name: str) -> int:
    """Determine next rebuild ID by scanning existing {N} directories under {crs_name}/.

    Returns:
        Next rebuild ID integer (1 if no directories exist).
    """
    crs_dir = rebuild_out_dir / crs_name
    if not crs_dir.exists():
        return 1

    id_re = re.compile(r"^(\d+)$")
    existing = [
        int(m.group(1))
        for d in crs_dir.iterdir()
        if d.is_dir() and (m := id_re.match(d.name))
    ]
    return max(existing, default=0) + 1


def run_ephemeral_build(
    base_image: str,
    rebuild_id: int,
    builder_name: str,
    crs_name: str,
    patch_bytes: bytes,
    rebuild_out_dir: Path,
    timeout: int = 1800,
    cpuset: "str | None" = None,
    mem_limit: "str | None" = None,
) -> dict:
    """Run a build inside an ephemeral Docker container.

    Full lifecycle: create -> start -> wait -> (commit on success) -> remove.
    Output dir: rebuild_out_dir/{crs_name}/{rebuild_id}/
    Same rebuild_id overwrites previous artifacts.

    Returns:
        Dict with keys: exit_code, stdout, stderr, rebuild_id.
    """
    client = docker.from_env()
    container = None

    output_dir = rebuild_output_dir(rebuild_out_dir, crs_name, rebuild_id)

    # Resolve the host path for bind-mounting into ephemeral containers.
    # The sidecar sees rebuild_out_dir as /rebuild_out (container path),
    # but Docker volume mounts need the actual host path.
    host_rebuild_out_dir = Path(os.environ.get("HOST_REBUILD_OUT_DIR", str(rebuild_out_dir)))
    host_output_dir = host_rebuild_out_dir / crs_name / str(rebuild_id)

    snapshot_tag = _build_snapshot_tag(crs_name, builder_name, str(rebuild_id))
    try:
        client.images.get(snapshot_tag)
        snapshot_exists = True
    except docker.errors.ImageNotFound:
        snapshot_exists = False

    build_cmd = get_image_cmd(client, base_image)

    # Prepare output dir — mounted into the container so
    # submit-build-output writes directly (same mechanism as build-target).
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Write patch to the output dir (host-visible) so the ephemeral container
    # can access it. tmpdir inside the sidecar container is not visible to the
    # Docker daemon (Docker-in-Docker via socket mount).
    patch_local_path = output_dir / "patch.diff"
    patch_local_path.write_bytes(patch_bytes)

    handler_path = os.environ.get("HANDLER_SCRIPT_PATH", "/oss_crs_handler.sh")
    try:
        container = client.containers.create(
            base_image,
            command=["bash", "/usr/local/bin/oss_crs_handler.sh", "/OSS_CRS_BUILD_OUT_DIR/patch.diff", "/out"],
            volumes={
                handler_path: {"bind": "/usr/local/bin/oss_crs_handler.sh", "mode": "ro"},
                str(host_output_dir): {"bind": "/OSS_CRS_BUILD_OUT_DIR", "mode": "rw"},
            },
            environment={
                "OSS_CRS_BUILD_ID": str(rebuild_id),
                "OSS_CRS_REBUILD_ID": str(rebuild_id),
                "OSS_CRS_BUILD_CMD": " ".join(build_cmd),
                "OSS_CRS_BUILD_OUT_DIR": "/OSS_CRS_BUILD_OUT_DIR",
                "OSS_CRS_NAME": crs_name,
                "OSS_CRS_RUN_ENV_TYPE": "local",
                "REPLAY_ENABLED": "",
                **_oss_fuzz_env(),
            },
            detach=True,
            **_resource_kwargs(cpuset, mem_limit),
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
                "stderr": f"Build timed out after {timeout} seconds",
                "rebuild_id": rebuild_id,
            }

        exit_code = result["StatusCode"]

        stdout, stderr = _stream_and_capture_logs(container, f"build:{builder_name}")

        if exit_code == 0 and not snapshot_exists:
            container.commit(repository="oss-crs-snapshot", tag=f"build__{crs_name}__{builder_name}__{rebuild_id}")

        return {
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
            "rebuild_id": rebuild_id,
        }

    finally:
        if container is not None:
            try:
                container.remove(force=True)
            except docker.errors.NotFound:
                pass


def run_ephemeral_test(
    base_image: str,
    rebuild_id: int,
    crs_name: str,
    patch_bytes: bytes,
    timeout: int = 1800,
    cpuset: "str | None" = None,
    mem_limit: "str | None" = None,
) -> dict:
    """Run a test inside an ephemeral Docker container.

    Full lifecycle: create -> start -> wait -> (commit on first success) -> remove.
    No artifacts produced — only exit code and logs.

    Returns:
        Dict with keys: exit_code, stdout, stderr, rebuild_id.
    """
    client = docker.from_env()
    container = None

    snapshot_tag = f"oss-crs-snapshot:test-{rebuild_id}"
    try:
        client.images.get(snapshot_tag)
        snapshot_exists = True
    except docker.errors.ImageNotFound:
        snapshot_exists = False

    with tempfile.TemporaryDirectory() as tmpdir:
        patch_path = Path(tmpdir) / "patch.diff"
        patch_path.write_bytes(patch_bytes)

        handler_path = os.environ.get("HANDLER_SCRIPT_PATH", "/oss_crs_handler.sh")
        run_tests_path = os.environ.get("RUN_TESTS_SCRIPT_PATH", "/run_tests.sh")
        try:
            container = client.containers.create(
                base_image,
                command=["bash", "/usr/local/bin/oss_crs_handler.sh", "/patch/patch.diff", "/out"],
                volumes={
                    tmpdir: {"bind": "/patch", "mode": "ro"},
                    handler_path: {"bind": "/usr/local/bin/oss_crs_handler.sh", "mode": "ro"},
                    run_tests_path: {"bind": "/usr/local/bin/run_tests.sh", "mode": "ro"},
                },
                environment={
                    "OSS_CRS_BUILD_ID": str(rebuild_id),
                    "OSS_CRS_REBUILD_ID": str(rebuild_id),
                    "OSS_CRS_BUILD_CMD": "bash /usr/local/bin/run_tests.sh",
                    "OSS_CRS_NAME": crs_name,
                    "OSS_CRS_RUN_ENV_TYPE": "local",
                    "REPLAY_ENABLED": "",
                    **_oss_fuzz_env(),
                },
                detach=True,
                **_resource_kwargs(cpuset, mem_limit),
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
                    "rebuild_id": rebuild_id,
                }

            exit_code = result["StatusCode"]

            stdout, stderr = _stream_and_capture_logs(container, f"test:{rebuild_id}")

            if exit_code == 0 and not snapshot_exists:
                container.commit(repository="oss-crs-snapshot", tag=f"test-{rebuild_id}")

            return {
                "exit_code": exit_code,
                "stdout": stdout,
                "stderr": stderr,
                "rebuild_id": rebuild_id,
            }

        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except docker.errors.NotFound:
                    pass

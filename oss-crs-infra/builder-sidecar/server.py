"""FastAPI builder sidecar server.

Host-side build orchestrator that accepts build requests, launches ephemeral Docker
containers via the docker_ops module, and manages versioned artifact storage.

Endpoints:
    GET  /health                - Liveness probe (API-05)
    POST /build                 - Submit a build job (API-01, BUILD-03)
    GET  /status/{job_id}       - Poll job status (API-04)
    GET  /builds                - List all known job IDs
    POST /run-pov               - Stub: POV handled by runner-sidecar service
    POST /test                  - Submit a test job (API-03, TEST-01)
    GET  /download-build-output - Download versioned build artifacts as zip (ART-04)

Environment variables:
    PROJECT_NAME    - Project name for build ID computation (default: "unknown")
    SANITIZER       - Sanitizer type (default: "unknown")
    REBUILD_OUT_DIR - Host path for versioned artifact output (required)
    BUILD_TIMEOUT   - Build timeout in seconds (default: "1800")
    BASE_IMAGE_{BUILDER_NAME} - Docker base image per builder (e.g. BASE_IMAGE_CRS_LIBFUZZER).
                                Resolved per-request from builder_name Form field.
                                Falls back to BASE_IMAGE env var for backward compatibility.
    PROJECT_BASE_IMAGE  - OSS-Fuzz project image for test.sh execution (required for /test).
                          Always the project image, never a CRS builder image.
"""

from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
import hashlib
import io
import os
import queue
import re
import threading
import zipfile
from pathlib import Path

from docker_ops import get_build_image, get_test_image, run_ephemeral_build, run_ephemeral_test
import docker


# ---------------------------------------------------------------------------
# Application instance
# ---------------------------------------------------------------------------

app = FastAPI(title="Builder Sidecar", description="Host-side ephemeral container build orchestrator")


# ---------------------------------------------------------------------------
# Pydantic response models (API-06)
# ---------------------------------------------------------------------------

class BuildResult(BaseModel):
    exit_code: int
    stdout: str
    stderr: str
    build_id: str | None = None
    artifacts_version: str | None = None


class JobResponse(BaseModel):
    id: str
    status: str  # "queued" | "running" | "done" | "error"
    result: BuildResult | None = None


# ---------------------------------------------------------------------------
# Global job state
# ---------------------------------------------------------------------------

job_queue: queue.Queue = queue.Queue()
job_results: dict[str, dict] = {}


# ---------------------------------------------------------------------------
# Build ID computation (BUILD-03)
# ---------------------------------------------------------------------------

def _make_build_id(patch_content: bytes) -> str:
    """Deterministic job ID: SHA256 of PROJECT_NAME:SANITIZER:patch_content, 12-char hex."""
    project = os.environ.get("PROJECT_NAME", "unknown")
    sanitizer = os.environ.get("SANITIZER", "unknown")
    hasher = hashlib.sha256()
    hasher.update(f"{project}:{sanitizer}:".encode())
    hasher.update(patch_content)
    return hasher.hexdigest()[:12]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    """Liveness probe — returns 200 with status ok (API-05)."""
    return {"status": "ok"}


@app.post("/build")
async def submit_build(
    patch: UploadFile = File(...),
    build_id: str = Form(...),
    builder_name: str = Form(...),
    version: str = Form(None),
):
    """Submit a build job.

    Accepts a patch file, a CLI-provided build_id, and a builder_name identifying
    which builder to use for base image resolution. Returns a job ID and initial
    status 'queued'. Deduplicates in-flight and successfully completed builds.
    """
    patch_content = await patch.read()
    job_id = _make_build_id(patch_content)

    # Dedup: return existing result for in-flight or successfully completed builds.
    if job_id in job_results:
        existing = job_results[job_id]
        if existing["status"] in ("queued", "running"):
            return JobResponse(id=job_id, status=existing["status"])
        if existing["status"] == "done":
            result = existing.get("result")
            if result is not None and result.get("exit_code") == 0:
                return existing

    # Enqueue new build job.
    job_results[job_id] = {"id": job_id, "status": "queued"}
    job_queue.put(("build", job_id, patch_content, build_id, builder_name, version))
    return JobResponse(id=job_id, status="queued")


@app.get("/status/{job_id}")
def get_job_status(job_id: str):
    """Poll job status by job_id (API-04).

    Returns 404 if job is unknown (e.g. after server restart).
    """
    result = job_results.get(job_id)
    if result is None:
        return JSONResponse(
            status_code=404,
            content={"error": "Job not found", "id": job_id},
        )
    return result


@app.get("/builds")
def list_builds():
    """List all known job IDs."""
    return {"builds": list(job_results.keys())}


@app.post("/run-pov")
async def run_pov():
    """Stub endpoint — implemented in Phase 2."""
    return JSONResponse(
        status_code=501,
        content={"error": "Not implemented. Available in Phase 2."},
    )


@app.post("/test")
async def run_test(
    patch: UploadFile = File(...),
    build_id: str = Form(...),
    builder_name: str = Form(...),
):
    """Submit a test job (TEST-01, TEST-02, TEST-03, TEST-04, API-03).

    Test runs in an ephemeral container separate from build. Accepts patch file,
    build_id, and builder_name for base image resolution. Test container runs
    test.sh which recompiles with RTS flags.
    """
    patch_content = await patch.read()
    job_id = "test:" + _make_build_id(patch_content)

    if job_id in job_results:
        existing = job_results[job_id]
        if existing["status"] in ("queued", "running"):
            return JobResponse(id=job_id, status=existing["status"])
        if existing["status"] == "done":
            result = existing.get("result")
            if result is not None and result.get("exit_code") == 0:
                return existing

    job_results[job_id] = {"id": job_id, "status": "queued"}
    job_queue.put(("test", job_id, patch_content, build_id, builder_name))
    return JobResponse(id=job_id, status="queued")


@app.get("/download-build-output")
def download_build_output(build_id: str, version: str = "latest"):
    """Download versioned build artifacts as a zip archive (ART-04).

    Query params:
        build_id: The build identifier (used for zip filename).
        version:  Version string (e.g. 'v1', 'v2') or 'latest' for the highest version.
    """
    rebuild_out_dir = Path(os.environ.get("REBUILD_OUT_DIR", "/rebuild_out"))

    # Resolve 'latest' to the highest v{N} directory.
    if version == "latest":
        version_re = re.compile(r"^v(\d+)$")
        if not rebuild_out_dir.exists():
            return JSONResponse(status_code=404, content={"error": "No build outputs found"})
        versions = [
            (int(m.group(1)), d.name)
            for d in rebuild_out_dir.iterdir()
            if d.is_dir() and (m := version_re.match(d.name))
        ]
        if not versions:
            return JSONResponse(status_code=404, content={"error": "No versioned build outputs found"})
        _, version_str = max(versions, key=lambda x: x[0])
    else:
        version_str = version

    version_dir = rebuild_out_dir / version_str
    if not version_dir.exists():
        return JSONResponse(
            status_code=404,
            content={"error": f"Version {version_str!r} not found"},
        )

    # Build in-memory zip of all files in the version directory.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zipf:
        for file_path in version_dir.rglob("*"):
            if file_path.is_file():
                arcname = file_path.relative_to(version_dir)
                zipf.write(file_path, arcname=str(arcname))
    buf.seek(0)

    filename = f"{build_id}_{version_str}.zip"
    return StreamingResponse(
        content=buf,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ---------------------------------------------------------------------------
# Job worker
# ---------------------------------------------------------------------------

def _handle_build(_job_id: str, patch_content: bytes, build_id: str, builder_name: str, version_override: "str | None") -> dict:
    """Execute a build job inside an ephemeral Docker container."""
    env_key = f"BASE_IMAGE_{builder_name.upper().replace('-', '_')}"
    base_image = os.environ.get(env_key)
    if not base_image:
        base_image = os.environ.get("BASE_IMAGE")
    if not base_image:
        raise ValueError(f"No base image configured for builder '{builder_name}'. Set {env_key} or BASE_IMAGE env var.")
    rebuild_out_dir = Path(os.environ["REBUILD_OUT_DIR"])
    timeout = int(os.environ.get("BUILD_TIMEOUT", "1800"))

    image = get_build_image(docker.from_env(), build_id, base_image, builder_name)
    result = run_ephemeral_build(
        base_image=image,
        build_id=build_id,
        builder_name=builder_name,
        patch_bytes=patch_content,
        rebuild_out_dir=rebuild_out_dir,
        version_override=version_override,
        timeout=timeout,
    )
    return result


def _handle_test(_job_id: str, patch_content: bytes, build_id: str, builder_name: str) -> dict:
    """Execute a test job inside an ephemeral Docker container."""
    base_image = os.environ.get("PROJECT_BASE_IMAGE")
    if not base_image:
        raise ValueError("PROJECT_BASE_IMAGE env var not set on sidecar service.")
    timeout = int(os.environ.get("BUILD_TIMEOUT", "1800"))
    image = get_test_image(docker.from_env(), build_id, base_image)
    return run_ephemeral_test(
        base_image=image,
        build_id=build_id,
        builder_name=builder_name,
        patch_bytes=patch_content,
        timeout=timeout,
    )


def job_worker():
    """Sequential worker — processes one job at a time to prevent Docker races."""
    while True:
        action, job_id, *args = job_queue.get()
        job_results[job_id]["status"] = "running"
        try:
            if action == "build":
                result = _handle_build(job_id, *args)
                job_results[job_id] = {
                    "id": job_id,
                    "status": "done",
                    "result": result,
                }
            elif action == "test":
                result = _handle_test(job_id, *args)
                job_results[job_id] = {
                    "id": job_id,
                    "status": "done",
                    "result": result,
                }
            else:
                job_results[job_id] = {
                    "id": job_id,
                    "status": "done",
                    "error": f"Unknown action: {action}",
                }
        except Exception as e:
            job_results[job_id] = {
                "id": job_id,
                "status": "done",
                "result": None,
                "error": str(e),
            }
        finally:
            job_queue.task_done()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    threading.Thread(target=job_worker, daemon=True).start()
    uvicorn.run(app, host="0.0.0.0", port=8080)

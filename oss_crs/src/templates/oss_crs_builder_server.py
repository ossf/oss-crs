#!/usr/bin/env python3
"""HTTP builder server for incremental patch builds.

Endpoints:
    POST /build       - Apply patch and compile (calls oss_crs_handler.sh)
    POST /run-pov     - Run POV binary via oss-fuzz reproduce script
    POST /run-test    - Run project's bundled test.sh against a specific build
    GET  /status/<id> - Poll job status
    GET  /health      - Healthcheck

All jobs are processed sequentially through a single worker thread
to prevent races on shared source tree state. Build artifacts are
saved per job ID at /builds/{job_id}/out/ so multiple builds can
coexist and be tested independently.
"""

from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import hashlib
import os
import queue
import re
import shutil
import subprocess
import threading
import uuid
from pathlib import Path

app = FastAPI(title="Builder Sidecar", description="Incremental patch build server")
job_queue: queue.Queue = queue.Queue()
job_results: dict = {}

BUILDS_DIR = Path("/builds")
PREBUILT_OUT_DIR = Path("/OSS_CRS_BUILD_OUT_DIR/build")
OSS_CRS_PROJ_PATH = Path(os.environ.get("OSS_CRS_PROJ_PATH", "/OSS_CRS_PROJ_PATH"))


def _ignore_build_junk(directory, contents):
    """Skip source/git directories when copying build artifacts — only binaries needed."""
    return [c for c in contents if c in (".git", "src")]


def _seed_base_out_if_needed() -> tuple[bool, str]:
    """Create /builds/base/out from prebuilt incremental build artifacts."""
    base_out = BUILDS_DIR / "base" / "out"
    if base_out.exists():
        return True, f"Base build artifacts already exist: {base_out}"
    if not PREBUILT_OUT_DIR.exists():
        return False, f"Prebuilt output path is missing: {PREBUILT_OUT_DIR}"
    if not PREBUILT_OUT_DIR.is_dir():
        return False, f"Prebuilt output path is not a directory: {PREBUILT_OUT_DIR}"
    try:
        if not any(PREBUILT_OUT_DIR.iterdir()):
            return False, f"Prebuilt output directory is empty: {PREBUILT_OUT_DIR}"
    except Exception as exc:
        return False, f"Failed to inspect prebuilt output directory: {exc}"
    base_out.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(str(PREBUILT_OUT_DIR), str(base_out), dirs_exist_ok=True)
    except Exception as exc:
        return False, f"Failed to copy prebuilt artifacts into {base_out}: {exc}"
    msg = f"Seeded base build artifacts from prebuilt output: {PREBUILT_OUT_DIR}"
    print(msg)
    return True, msg


class JobSubmitResponse(BaseModel):
    id: str
    status: str


def _make_job_dirs(job_id: str) -> tuple[Path, Path]:
    """Create and return (req_dir, resp_dir) for a job."""
    work_dir = Path(f"/tmp/jobs/{job_id}")
    req_dir = work_dir / "request"
    resp_dir = work_dir / "response"
    req_dir.mkdir(parents=True, exist_ok=True)
    resp_dir.mkdir(parents=True, exist_ok=True)
    return req_dir, resp_dir


@app.get("/health")
def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# POST /build — apply patch + compile
# ---------------------------------------------------------------------------
def _make_build_id(patch_content: bytes) -> str:
    """Deterministic build ID from project + sanitizer + patch content."""
    project = os.environ.get("PROJECT_NAME", "unknown")
    sanitizer = os.environ.get("SANITIZER", "unknown")
    hasher = hashlib.sha256()
    hasher.update(f"{project}:{sanitizer}:".encode())
    hasher.update(patch_content)
    return hasher.hexdigest()[:12]


@app.post("/build", response_model=JobSubmitResponse)
async def submit_build(patch: UploadFile = File(...)):
    patch_content = await patch.read()
    job_id = _make_build_id(patch_content)

    # Dedup: return existing result if this build is in-flight or succeeded.
    # Failed builds are NOT cached — allow retry with the same patch.
    if job_id in job_results:
        existing = job_results[job_id]
        if existing["status"] in ("queued", "running"):
            return JobSubmitResponse(id=job_id, status=existing["status"])
        if existing["status"] == "done" and existing.get("build_exit_code") == 0:
            return JobSubmitResponse(id=job_id, status="done")

    req_dir, resp_dir = _make_job_dirs(job_id)

    (req_dir / "patch.diff").write_bytes(patch_content)

    job_results[job_id] = {"id": job_id, "status": "queued"}
    job_queue.put(("build", job_id, req_dir, resp_dir))
    return JobSubmitResponse(id=job_id, status="queued")


# ---------------------------------------------------------------------------
# POST /run-pov — run POV via oss-fuzz reproduce script
# ---------------------------------------------------------------------------
_HARNESS_NAME_RE = re.compile(r"^[a-zA-Z0-9_\-\.]+$")
_BUILD_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")


@app.post("/run-pov", response_model=JobSubmitResponse)
async def submit_run_pov(
    pov: UploadFile = File(...),
    harness_name: str = Form(...),
    build_id: str = Form(...),
):
    if not _HARNESS_NAME_RE.match(harness_name):
        return JSONResponse(
            status_code=400,
            content={
                "error": "Invalid harness_name: must be alphanumeric, hyphens, underscores, or dots only"
            },
        )
    if not _BUILD_ID_RE.match(build_id):
        return JSONResponse(
            status_code=400,
            content={
                "error": "Invalid build_id: must be alphanumeric, hyphens, or underscores only"
            },
        )
    build_out = BUILDS_DIR / build_id / "out"
    if not build_out.exists():
        return JSONResponse(
            status_code=404,
            content={"error": f"Build {build_id} not found. Call /build first."},
        )

    job_id = uuid.uuid4().hex[:12]
    req_dir, resp_dir = _make_job_dirs(job_id)

    (req_dir / "pov.bin").write_bytes(await pov.read())
    (req_dir / "harness_name").write_text(harness_name)
    (req_dir / "build_id").write_text(build_id)

    job_results[job_id] = {"id": job_id, "status": "queued"}
    job_queue.put(("run-pov", job_id, req_dir, resp_dir))
    return JobSubmitResponse(id=job_id, status="queued")


# ---------------------------------------------------------------------------
# POST /run-test — run the project's bundled test.sh
# ---------------------------------------------------------------------------
@app.post("/run-test", response_model=JobSubmitResponse)
async def submit_run_test(
    build_id: str = Form(...),
):
    if not _BUILD_ID_RE.match(build_id):
        return JSONResponse(
            status_code=400,
            content={
                "error": "Invalid build_id: must be alphanumeric, hyphens, or underscores only"
            },
        )
    build_out = BUILDS_DIR / build_id / "out"
    if not build_out.exists():
        return JSONResponse(
            status_code=404,
            content={"error": f"Build {build_id} not found. Call /build first."},
        )

    job_id = uuid.uuid4().hex[:12]
    req_dir, resp_dir = _make_job_dirs(job_id)

    (req_dir / "build_id").write_text(build_id)

    job_results[job_id] = {"id": job_id, "status": "queued"}
    job_queue.put(("run-test", job_id, req_dir, resp_dir))
    return JobSubmitResponse(id=job_id, status="queued")


# ---------------------------------------------------------------------------
# GET /status/<job_id> — poll any job
# ---------------------------------------------------------------------------
@app.get("/status/{job_id}")
def get_job_status(job_id: str):
    result = job_results.get(job_id)
    if not result:
        return JSONResponse(status_code=404, content={"error": "not found"})
    return result


# ---------------------------------------------------------------------------
# Job handlers (run inside the worker thread)
# ---------------------------------------------------------------------------


def _handle_build(job_id: str, req_dir: Path, resp_dir: Path) -> dict:
    """Apply patch and compile via oss_crs_handler.sh."""
    try:
        result = subprocess.run(
            ["/usr/local/bin/oss_crs_handler.sh", str(req_dir), str(resp_dir)],
            capture_output=True,
            text=True,
            timeout=600,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "build_exit_code": 1,
            "build_log": "Build timed out",
            "build_stdout": exc.stdout or "",
            "build_stderr": exc.stderr or "",
        }

    response = {}
    for name, key, parse in [
        ("build_exit_code", "build_exit_code", int),
        ("build.log", "build_log", str),
    ]:
        f = resp_dir / name
        if f.exists():
            response[key] = parse(f.read_text().strip())
    response.setdefault("build_exit_code", 1)
    response["build_stdout"] = result.stdout or ""
    response["build_stderr"] = result.stderr or ""
    response.setdefault(
        "build_log",
        "\n".join(
            part
            for part in [response["build_stdout"], response["build_stderr"]]
            if part
        ),
    )

    # Save build artifacts for later POV/test runs
    if response["build_exit_code"] == 0:
        build_out = BUILDS_DIR / job_id / "out"
        if build_out.exists():
            shutil.rmtree(build_out)
        build_out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree("/out", str(build_out), ignore=_ignore_build_junk)

    return response


def _handle_run_pov(job_id: str, req_dir: Path, resp_dir: Path) -> dict:
    """Run POV binary via oss-fuzz reproduce script."""
    harness_name = (req_dir / "harness_name").read_text().strip()
    build_id = (req_dir / "build_id").read_text().strip()
    pov_path = req_dir / "pov.bin"
    pov_timeout = int(os.environ.get("POV_TIMEOUT", "30"))

    build_out = BUILDS_DIR / build_id / "out"
    if not (build_out / harness_name).exists():
        return {
            "pov_exit_code": 127,
            "pov_stderr": f"Harness binary not found: {build_out / harness_name}",
        }

    env = os.environ.copy()
    env["OUT"] = str(build_out)
    env["TESTCASE"] = str(pov_path)
    # Snapshot image derives from the builder (not base-runner), so
    # FUZZER_ARGS is not inherited from Docker ENV.  Use oss-fuzz defaults.
    env.setdefault("FUZZER_ARGS", "-rss_limit_mb=2560 -timeout=25")

    try:
        result = subprocess.run(
            ["/usr/local/bin/reproduce", harness_name],
            capture_output=True,
            text=True,
            timeout=pov_timeout,
            env=env,
            cwd=str(build_out),
        )
        return {
            "pov_exit_code": result.returncode,
            "pov_stderr": result.stderr,
            "pov_stdout": result.stdout,
        }
    except subprocess.TimeoutExpired:
        return {
            "pov_exit_code": 124,
            "pov_stdout": "",
            "pov_stderr": "POV execution timed out",
        }


def _handle_run_test(job_id: str, req_dir: Path, resp_dir: Path) -> dict:
    """Run the project's bundled test.sh against a specific build's artifacts."""
    build_id = (req_dir / "build_id").read_text().strip()
    test_timeout = int(os.environ.get("TEST_TIMEOUT", "120"))

    test_path = OSS_CRS_PROJ_PATH / "test.sh"
    if not test_path.exists():
        return {
            "test_exit_code": 0,
            "test_stdout": "",
            "test_stderr": f"skipped: no test.sh found in {OSS_CRS_PROJ_PATH}/",
        }

    build_out = BUILDS_DIR / build_id / "out"
    env = os.environ.copy()
    env["OUT"] = str(build_out)

    try:
        result = subprocess.run(
            ["bash", str(test_path)],
            capture_output=True,
            text=True,
            timeout=test_timeout,
            env=env,
            cwd=str(build_out),
        )
        return {
            "test_exit_code": result.returncode,
            "test_stdout": result.stdout,
            "test_stderr": result.stderr,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "test_exit_code": 124,
            "test_stdout": exc.stdout or "",
            "test_stderr": "Test execution timed out",
        }


_HANDLERS = {
    "build": _handle_build,
    "run-pov": _handle_run_pov,
    "run-test": _handle_run_test,
}


def job_worker():
    """Sequential worker — processes one job at a time."""
    while True:
        action, job_id, req_dir, resp_dir = job_queue.get()
        job_results[job_id]["status"] = "running"
        try:
            handler = _HANDLERS[action]
            result = handler(job_id, req_dir, resp_dir)
            job_results[job_id] = {"id": job_id, "status": "done", **result}
        except Exception as e:
            job_results[job_id] = {
                "id": job_id,
                "status": "done",
                "error": str(e),
            }
        finally:
            shutil.rmtree(req_dir.parent, ignore_errors=True)
            job_queue.task_done()


if __name__ == "__main__":
    import uvicorn

    # Register /builds as a shared directory via libCRS so artifacts
    # are visible to CRS modules through the shared filesystem.
    try:
        result = subprocess.run(
            ["libCRS", "register-shared-dir", str(BUILDS_DIR), "builds"],
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        print(
            f"Warning: libCRS register-shared-dir failed (rc={exc.returncode}): {exc.stderr.strip()}"
        )
        BUILDS_DIR.mkdir(parents=True, exist_ok=True)
    except FileNotFoundError:
        print("Warning: libCRS not found, falling back to local mkdir")
        BUILDS_DIR.mkdir(parents=True, exist_ok=True)

    # Seed the unpatched base build from prebuilt incremental build artifacts
    # mounted at /OSS_CRS_BUILD_OUT_DIR/build by oss-crs compose.
    # This avoids relying on startup compile side effects to populate /out.
    base_out = BUILDS_DIR / "base" / "out"
    if not base_out.exists():
        seeded, reason = _seed_base_out_if_needed()
        if not seeded:
            raise RuntimeError(
                "Failed to initialize base build from prebuilt artifacts.\n"
                f"Reason: {reason}\n"
                "Expected: /OSS_CRS_BUILD_OUT_DIR/build is a non-empty directory "
                "mounted by oss-crs compose."
            )

    threading.Thread(target=job_worker, daemon=True).start()
    uvicorn.run(app, host="0.0.0.0", port=8080)

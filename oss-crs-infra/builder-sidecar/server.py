# SPDX-License-Identifier: MIT
"""FastAPI builder sidecar server.

Host-side build orchestrator that accepts build requests, launches ephemeral Docker
containers via the docker_ops module, and manages rebuild artifact storage.

Endpoints:
    GET  /health                - Liveness probe
    POST /build                 - Submit a rebuild job
    GET  /status/{job_id}       - Poll job status
    GET  /builds                - List all known job IDs
    POST /test                  - Submit a test job


Environment variables:
    REBUILD_OUT_DIR         - Host path for artifact output (required)
    BUILD_TIMEOUT           - Build timeout in seconds (default: "1800")
    BASE_IMAGE_{BUILDER}    - Docker base image per builder (e.g. BASE_IMAGE_DEFAULT_BUILD)
    BASE_IMAGE              - Fallback base image
    PROJECT_BASE_IMAGE      - OSS-Fuzz project image for test.sh execution
    INCREMENTAL_BUILD       - "true" to use snapshot images, "false" for fresh
    MAX_PARALLEL_JOBS       - Thread pool size (default: "4")
"""

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import copy
import hashlib
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from docker_ops import (
    get_build_image,
    get_test_image,
    next_rebuild_id,
    run_ephemeral_build,
    run_ephemeral_test,
    stage_cached_rebuild,
)
import docker


app = FastAPI(
    title="Builder Sidecar",
    description="Host-side ephemeral container build orchestrator",
)

_SAFE_PATH_COMPONENT_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


class BuildResult(BaseModel):
    exit_code: int
    stdout: str
    stderr: str
    rebuild_id: int | None = None


class JobResponse(BaseModel):
    id: str
    status: str  # "queued" | "running" | "done" | "error"
    result: BuildResult | None = None


job_results: dict[str, dict] = {}
_executor = ThreadPoolExecutor(
    max_workers=int(os.environ.get("MAX_PARALLEL_JOBS", "4"))
)


# ---------------------------------------------------------------------------
# API call logging — one JSONL line per API call, written per-CRS to the
# host-mounted LOG_DIR (SIDECAR_LOG_DIR/{crs_name}/libcrs-sidecar-metrics.jsonl)
# so CRS evaluation/monitoring can see every build/test invocation.
# Best-effort: logging never blocks or fails an API call.
# ---------------------------------------------------------------------------

_API_LOG_DIR = Path(os.environ.get("SIDECAR_LOG_DIR", "/sidecar-logs"))
_API_LOG_NAME = "libcrs-sidecar-metrics.jsonl"
_SERVICE = "builder-sidecar"

# Maps the internal job action to the canonical event name consumed by the
# run-meta aggregation (oss_crs/src/crs_compose.py:_read_sidecar_counts_for_crs).
_ACTION_TO_EVENT = {
    "build": "apply-patch-build",
    "test": "apply-patch-test",
}


def _init_api_log() -> None:
    """Ensure the base API log directory exists (best-effort)."""
    try:
        _API_LOG_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass


def _make_log_entry(
    event: str,
    job_id: str,
    result: dict,
    duration_ms: int,
    ts_start: float,
    crs_name: str,
    service: str,
) -> dict:
    """Build a structured log entry for one sidecar API call.

    ``exit_code`` defaults to the ``-1`` sentinel when the handler raised before
    producing one, so derived ``crash``/``build_success`` flags stay correct.
    """
    exit_code = result.get("exit_code", -1)
    entry: dict = {
        "ts": ts_start,
        "crs": crs_name,
        "event": event,
        "service": service,
        "job_id": job_id,
        "exit_code": exit_code,
        "duration_ms": duration_ms,
    }
    # Pass through request/identity params returned by handlers.
    for key in ("harness", "build_id", "rebuild_id"):
        if key in result:
            entry[key] = result[key]
    if "error" in result:
        entry["error"] = result["error"]
    # Derived fields for consumer convenience. The ``> 0`` guard on crash keeps
    # the ``-1`` exception sentinel from being classified as a crash.
    if event == "apply-patch-build":
        entry["build_success"] = exit_code == 0
    elif event == "run-pov":
        entry["crash"] = exit_code > 0 and exit_code != 124
        entry["timeout"] = exit_code == 124
    elif event == "apply-patch-test":
        entry["test_passed"] = exit_code == 0
        entry["skipped"] = bool(result.get("test_skipped"))
    return entry


def _log_api_call(crs_name: str, entry: dict) -> None:
    """Append one compact JSON line to the per-CRS metrics file (best-effort)."""
    try:
        crs_dir = _API_LOG_DIR / crs_name
        crs_dir.mkdir(parents=True, exist_ok=True)
        with (crs_dir / _API_LOG_NAME).open("a") as f:
            f.write(json.dumps(entry, separators=(",", ":")) + "\n")
    except Exception:
        pass  # best-effort logging


def _make_job_id(patch_content: bytes, builder_name: str = "") -> str:
    """Deterministic job ID: SHA256 of PROJECT_NAME:SANITIZER:builder_name:patch_content, 12-char hex."""
    project = os.environ.get("PROJECT_NAME", "unknown")
    sanitizer = os.environ.get("SANITIZER", "unknown")
    hasher = hashlib.sha256()
    hasher.update(f"{project}:{sanitizer}:{builder_name}:".encode())
    hasher.update(patch_content)
    return hasher.hexdigest()[:12]


def _validate_path_component(value: str, field_name: str) -> None:
    """Reject path separators and traversal components from API path fields."""
    if not _SAFE_PATH_COMPONENT_RE.fullmatch(value) or value == "." or value == "..":
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}")


def _cached_response_for_rebuild_id(
    cached: dict, crs_name: str, requested_rid: "int | None"
) -> dict:
    """Return a cache-hit response honoring a caller-supplied ``rebuild_id``.

    When the caller passes an explicit ``rebuild_id`` that differs from the
    cached one, hardlink-stage the cached rebuild tree into the requested
    slot and return a *copy* of the cached entry with ``rebuild_id`` rewritten
    to the requested value. The cached entry itself is left untouched so
    subsequent callers still see the canonical (original) rebuild_id.

    When the caller passes no ``rebuild_id`` (or it already matches), the
    cached entry is returned as-is.
    """
    result = cached.get("result") or {}
    cached_rid = result.get("rebuild_id")
    if requested_rid is None or cached_rid is None or requested_rid == cached_rid:
        return cached

    rebuild_out_dir = Path(os.environ.get("REBUILD_OUT_DIR", "/rebuild_out"))
    stage_cached_rebuild(rebuild_out_dir, crs_name, cached_rid, requested_rid)

    response = copy.deepcopy(cached)
    response["result"]["rebuild_id"] = requested_rid
    return response


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/build")
async def submit_build(
    patch: UploadFile = File(...),
    crs_name: str = Form(...),
    builder_name: str = Form(""),
    rebuild_id: int = Form(None),
    cpuset: str = Form(""),
    mem_limit: str = Form(""),
):
    """Submit a rebuild job. rebuild_id auto-increments if not provided."""
    _validate_path_component(crs_name, "crs_name")
    patch_content = await patch.read()
    job_id = _make_job_id(patch_content, builder_name)

    if job_id in job_results:
        existing = job_results[job_id]
        if existing["status"] in ("queued", "running"):
            return JobResponse(id=job_id, status=existing["status"])
        if existing["status"] == "done":
            result = existing.get("result")
            if result is not None and result.get("exit_code") == 0:
                return _cached_response_for_rebuild_id(existing, crs_name, rebuild_id)

    # Resolve rebuild_id if not provided
    if rebuild_id is None:
        rebuild_out_dir = Path(os.environ.get("REBUILD_OUT_DIR", "/rebuild_out"))
        rebuild_id = next_rebuild_id(rebuild_out_dir, crs_name)

    job_results[job_id] = {"id": job_id, "status": "queued"}
    _executor.submit(
        _run_job,
        "build",
        job_id,
        patch_content,
        crs_name,
        builder_name,
        rebuild_id,
        cpuset,
        mem_limit,
    )
    return JobResponse(id=job_id, status="queued")


@app.get("/status/{job_id}")
def get_job_status(job_id: str):
    result = job_results.get(job_id)
    if result is None:
        return JSONResponse(
            status_code=404, content={"error": "Job not found", "id": job_id}
        )
    return result


@app.get("/builds")
def list_builds():
    return {"builds": list(job_results.keys())}


@app.post("/test")
async def run_test(
    patch: UploadFile = File(...),
    crs_name: str = Form(...),
    rebuild_id: int = Form(None),
    cpuset: str = Form(""),
    mem_limit: str = Form(""),
):
    """Submit a test job. Uses PROJECT_BASE_IMAGE."""
    _validate_path_component(crs_name, "crs_name")
    patch_content = await patch.read()
    job_id = "test:" + _make_job_id(patch_content)

    if job_id in job_results:
        existing = job_results[job_id]
        if existing["status"] in ("queued", "running"):
            return JobResponse(id=job_id, status=existing["status"])
        if existing["status"] == "done":
            result = existing.get("result")
            if result is not None and result.get("exit_code") == 0:
                return _cached_response_for_rebuild_id(existing, crs_name, rebuild_id)

    if rebuild_id is None:
        rebuild_out_dir = Path(os.environ.get("REBUILD_OUT_DIR", "/rebuild_out"))
        rebuild_id = next_rebuild_id(rebuild_out_dir, crs_name)

    job_results[job_id] = {"id": job_id, "status": "queued"}
    _executor.submit(
        _run_job, "test", job_id, patch_content, crs_name, rebuild_id, cpuset, mem_limit
    )
    return JobResponse(id=job_id, status="queued")


# ---------------------------------------------------------------------------
# Job handlers
# ---------------------------------------------------------------------------


def _resolve_builder_name(builder_name: str) -> tuple[str, str]:
    """Resolve builder_name to (builder_name, base_image).

    If builder_name is provided, looks up BASE_IMAGE_{NAME}.
    If empty, auto-detects from available BASE_IMAGE_* env vars (uses first if only one).
    Falls back to BASE_IMAGE env var.
    """
    if builder_name:
        env_key = f"BASE_IMAGE_{builder_name.upper().replace('-', '_')}"
        base_image = os.environ.get(env_key)
        if base_image:
            return builder_name, base_image

    # Auto-detect: find all BASE_IMAGE_* env vars
    candidates = {
        k[len("BASE_IMAGE_") :].lower().replace("_", "-"): v
        for k, v in os.environ.items()
        if k.startswith("BASE_IMAGE_") and k != "BASE_IMAGE"
    }

    if not builder_name and len(candidates) == 1:
        name, image = next(iter(candidates.items()))
        return name, image

    # Fallback to BASE_IMAGE
    base = os.environ.get("BASE_IMAGE")
    if base:
        return builder_name or "default", base

    avail = ", ".join(candidates.keys()) if candidates else "(none)"
    raise ValueError(
        f"Cannot resolve builder '{builder_name}'. "
        f"Available builders: {avail}. Set BASE_IMAGE_{{NAME}} or pass --builder-name."
    )


def _handle_build(
    _job_id: str,
    patch_content: bytes,
    crs_name: str,
    builder_name: str,
    rebuild_id: int,
    cpuset: str,
    mem_limit: str,
) -> dict:
    builder_name, base_image = _resolve_builder_name(builder_name)
    rebuild_out_dir = Path(os.environ["REBUILD_OUT_DIR"])
    timeout = int(os.environ.get("BUILD_TIMEOUT", "1800"))

    image = get_build_image(docker.from_env(), base_image, builder_name, crs_name)
    return run_ephemeral_build(
        base_image=image,
        rebuild_id=rebuild_id,
        builder_name=builder_name,
        crs_name=crs_name,
        patch_bytes=patch_content,
        rebuild_out_dir=rebuild_out_dir,
        timeout=timeout,
        cpuset=cpuset or None,
        mem_limit=mem_limit or None,
    )


def _handle_test(
    _job_id: str,
    patch_content: bytes,
    crs_name: str,
    rebuild_id: int,
    cpuset: str,
    mem_limit: str,
) -> dict:
    base_image = os.environ.get("PROJECT_BASE_IMAGE")
    if not base_image:
        raise ValueError("PROJECT_BASE_IMAGE env var not set on sidecar service.")
    rebuild_out_dir = Path(os.environ["REBUILD_OUT_DIR"])
    timeout = int(os.environ.get("BUILD_TIMEOUT", "1800"))
    image = get_test_image(docker.from_env(), base_image)
    return run_ephemeral_test(
        base_image=image,
        rebuild_id=rebuild_id,
        crs_name=crs_name,
        patch_bytes=patch_content,
        rebuild_out_dir=rebuild_out_dir,
        timeout=timeout,
        cpuset=cpuset or None,
        mem_limit=mem_limit or None,
    )


def _run_job(action: str, job_id: str, *args):
    """Execute a job in the thread pool."""
    job_results[job_id]["status"] = "running"
    # crs_name is the second positional arg for both build and test jobs.
    crs_name = args[1] if len(args) > 1 else "unknown"
    ts_start = time.time()
    t0 = time.monotonic()
    result: dict = {}
    try:
        if action == "build":
            result = _handle_build(job_id, *args)
        elif action == "test":
            result = _handle_test(job_id, *args)
        else:
            result = {"error": f"Unknown action: {action}"}
            job_results[job_id] = {
                "id": job_id,
                "status": "done",
                "error": f"Unknown action: {action}",
            }
            return
        job_results[job_id] = {"id": job_id, "status": "done", "result": result}
    except Exception as e:
        result = {"error": str(e)}
        job_results[job_id] = {
            "id": job_id,
            "status": "done",
            "result": None,
            "error": str(e),
        }
    finally:
        event = _ACTION_TO_EVENT.get(action)
        if event is not None:
            duration_ms = int((time.monotonic() - t0) * 1000)
            _log_api_call(
                crs_name,
                _make_log_entry(
                    event, job_id, result, duration_ms, ts_start, crs_name, _SERVICE
                ),
            )


if __name__ == "__main__":
    import uvicorn

    _init_api_log()
    uvicorn.run(app, host="0.0.0.0", port=8080)

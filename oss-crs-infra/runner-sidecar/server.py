# SPDX-License-Identifier: MIT
"""FastAPI runner sidecar server.

Long-running service for POV (Proof of Vulnerability) reproduction using the
OSS-Fuzz base-runner's reproduce script. Build artifacts are bind-mounted from
the host at /out. POV binaries are accessible via bind-mounted paths.

Endpoints:
    GET  /health   - Liveness probe
    POST /run-pov  - Execute POV reproduction (API-02, POV-01, POV-02, POV-03)

Environment variables:
    OUT_DIR     - Path to build artifacts directory inside container (default: "/out")
    POV_TIMEOUT - Maximum seconds for reproduce script (default: "30")
"""

import json
import os
import re
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Form, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn


app = FastAPI(
    title="Runner Sidecar",
    description="POV reproduction service using OSS-Fuzz reproduce",
)

_SAFE_PATH_COMPONENT_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


class POVResult(BaseModel):
    exit_code: int
    stdout: str = ""
    stderr: str


def _validate_path_component(value: str, field_name: str) -> None:
    """Reject path separators and traversal components from API path fields."""
    if not _SAFE_PATH_COMPONENT_RE.fullmatch(value) or value == "." or value == "..":
        raise HTTPException(status_code=400, detail=f"Invalid {field_name}")


def _validate_rebuild_id(value: str | None) -> None:
    if value is not None and not re.fullmatch(r"[0-9]+", value):
        raise HTTPException(status_code=400, detail="Invalid rebuild_id")


# ---------------------------------------------------------------------------
# API call logging — one JSONL line per /run-pov call, written per-CRS to the
# host-mounted LOG_DIR (SIDECAR_LOG_DIR/{crs_name}/libcrs-sidecar-metrics.jsonl)
# so CRS evaluation/monitoring can see every POV reproduction.
# Best-effort: logging never blocks or fails an API call.
# ---------------------------------------------------------------------------

_API_LOG_DIR = Path(os.environ.get("SIDECAR_LOG_DIR", "/sidecar-logs"))
_API_LOG_NAME = "libcrs-sidecar-metrics.jsonl"
_SERVICE = "runner-sidecar"


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
    producing one, so derived ``crash``/``timeout`` flags stay correct.
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
    for key in ("harness", "build_id", "rebuild_id"):
        if key in result:
            entry[key] = result[key]
    if "error" in result:
        entry["error"] = result["error"]
    # Derived fields. The ``> 0`` guard keeps the ``-1`` exception sentinel from
    # being classified as a crash; 124 is the timeout sentinel.
    if event == "run-pov":
        entry["crash"] = exit_code > 0 and exit_code != 124
        entry["timeout"] = exit_code == 124
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


@app.get("/health")
def health():
    """Liveness probe -- returns 200 with status ok."""
    return {"status": "ok"}


def _reproduce_pov(
    pov_content: bytes,
    harness_name: str,
    crs_name: str,
    rebuild_id: "str | None",
    pov_timeout: int,
) -> dict:
    """Run the OSS-Fuzz reproduce script for one POV and return a result dict.

    The dict always carries ``exit_code``; a real run also carries
    ``stdout``/``stderr``. The missing-harness case returns exit 127 plus
    ``harness_path`` so the caller can render a 404. This is a plain synchronous
    helper so the endpoint stays thin (validate, time, log, map to response).
    """
    if rebuild_id is not None:
        # Rebuild artifacts: /out/{crs_name}/{rebuild_id}/build/{harness_name}
        out_dir = os.environ.get("OUT_DIR", "/out")
        harness_dir = os.path.join(out_dir, crs_name, str(rebuild_id), "build")
    else:
        # Base build artifacts: /build_out/{crs_name}/build/{harness_name}
        build_out_dir = os.environ.get("BUILD_OUT_DIR", "/build_out")
        harness_dir = os.path.join(build_out_dir, crs_name, "build")

    harness_path = os.path.join(harness_dir, harness_name)
    if not os.path.exists(harness_path):
        # Missing harness binary -> exit 127, classified as a crash to match
        # the upstream reproduce-handler semantics.
        return {"exit_code": 127, "harness_path": harness_path}

    with tempfile.NamedTemporaryFile(suffix=".pov", delete=False) as tmp:
        tmp.write(pov_content)
        tmp_pov_path = tmp.name

    # When running against the base build (no rebuild_id), harness_dir is on
    # a read-only mount.  The OSS-Fuzz reproduce/run_fuzzer script creates
    # output directories under $OUT, so we need a writable overlay.  Copy
    # the harness binary (and any sibling files the fuzzer may need) into a
    # temp dir and point OUT there.
    writable_out = None
    if rebuild_id is None:
        writable_out = tempfile.mkdtemp(prefix="run-pov-out-")
        # Symlink every file from the read-only harness dir so the runner
        # can find harness binary + seed corpus etc. without a full copy.
        for entry in os.listdir(harness_dir):
            os.symlink(
                os.path.join(harness_dir, entry),
                os.path.join(writable_out, entry),
            )
        out_for_env = writable_out
    else:
        out_for_env = harness_dir

    try:
        os.chmod(tmp_pov_path, 0o755)
        env = os.environ.copy()
        env["OUT"] = out_for_env
        env["TESTCASE"] = tmp_pov_path
        env.setdefault("FUZZER_ARGS", "-rss_limit_mb=2560 -timeout=25")

        try:
            result = subprocess.run(
                ["/usr/local/bin/reproduce", harness_name],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=pov_timeout,
                env=env,
                cwd=out_for_env,
            )
            return {
                "exit_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        except subprocess.TimeoutExpired:
            return {
                "exit_code": 124,
                "stdout": "",
                "stderr": "POV execution timed out",
            }
    finally:
        os.unlink(tmp_pov_path)
        if writable_out is not None:
            import shutil

            shutil.rmtree(writable_out, ignore_errors=True)


@app.post("/run-pov")
async def run_pov(
    pov: UploadFile = File(...),
    harness_name: str = Form(...),
    crs_name: str = Form(...),
    rebuild_id: str = Form(None),
):
    """Execute POV reproduction via OSS-Fuzz reproduce script.

    The POV binary is uploaded as a file (not a path) so the runner-sidecar
    doesn't need access to the patcher's filesystem.

    Args (form/file fields):
        pov: The POV binary to reproduce.
        harness_name: Name of the fuzzer harness binary (e.g., "my_fuzzer").
        rebuild_id: Rebuild identifier (locates artifacts under /out/{crs}/{rebuild_id}/).
                    When None, runs against the original unpatched base build.
        crs_name: CRS identifier.

    Returns:
        JSON with exit_code (0=no crash, non-zero=crash) and stderr from reproduce.
    """
    pov_timeout = int(os.environ.get("POV_TIMEOUT", "30"))
    _validate_path_component(harness_name, "harness_name")
    _validate_path_component(crs_name, "crs_name")
    _validate_rebuild_id(rebuild_id)

    pov_content = await pov.read()

    job_id = uuid.uuid4().hex[:12]
    ts_start = time.time()
    t0 = time.monotonic()
    result: dict = {"harness": harness_name}
    if rebuild_id is not None:
        result["rebuild_id"] = rebuild_id
    try:
        result.update(
            _reproduce_pov(pov_content, harness_name, crs_name, rebuild_id, pov_timeout)
        )
    except Exception as e:
        result["error"] = str(e)
        raise
    finally:
        duration_ms = int((time.monotonic() - t0) * 1000)
        _log_api_call(
            crs_name,
            _make_log_entry(
                "run-pov", job_id, result, duration_ms, ts_start, crs_name, _SERVICE
            ),
        )

    if "harness_path" in result:
        return JSONResponse(
            status_code=404,
            content={"error": f"Harness binary not found: {result['harness_path']}"},
        )
    return POVResult(
        exit_code=result["exit_code"],
        stdout=result.get("stdout", ""),
        stderr=result.get("stderr", ""),
    )


if __name__ == "__main__":
    _init_api_log()
    uvicorn.run(app, host="0.0.0.0", port=8080)

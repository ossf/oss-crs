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

import os
import subprocess

from fastapi import FastAPI, Form
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn


app = FastAPI(title="Runner Sidecar", description="POV reproduction service using OSS-Fuzz reproduce")


class POVResult(BaseModel):
    exit_code: int
    stderr: str


@app.get("/health")
def health():
    """Liveness probe -- returns 200 with status ok."""
    return {"status": "ok"}


@app.post("/run-pov")
def run_pov(
    harness_name: str = Form(...),
    build_id: str = Form(...),  # noqa: accepted but unused — for API parity with libCRS
    pov_path: str = Form(...),
):
    """Execute POV reproduction via OSS-Fuzz reproduce script.

    Args (form fields):
        harness_name: Name of the fuzzer harness binary (e.g., "my_fuzzer").
        build_id: Build identifier (used to locate artifacts if organized by build).
        pov_path: Path to the POV binary inside the container (bind-mounted from host).

    Returns:
        JSON with exit_code (0=no crash, non-zero=crash) and stderr from reproduce.
    """
    out_dir = os.environ.get("OUT_DIR", "/out")
    pov_timeout = int(os.environ.get("POV_TIMEOUT", "30"))

    # Validate harness binary exists
    harness_path = os.path.join(out_dir, harness_name)
    if not os.path.exists(harness_path):
        return JSONResponse(
            status_code=404,
            content={"error": f"Harness binary not found: {harness_path}"},
        )

    # Validate POV binary exists
    if not os.path.exists(pov_path):
        return JSONResponse(
            status_code=404,
            content={"error": f"POV binary not found: {pov_path}"},
        )

    env = os.environ.copy()
    env["OUT"] = out_dir
    env["TESTCASE"] = pov_path
    env.setdefault("FUZZER_ARGS", "-rss_limit_mb=2560 -timeout=25")

    try:
        result = subprocess.run(
            ["/usr/local/bin/reproduce", harness_name],
            capture_output=True,
            text=True,
            timeout=pov_timeout,
            env=env,
            cwd=out_dir,
        )
        return POVResult(exit_code=result.returncode, stderr=result.stderr)
    except subprocess.TimeoutExpired:
        return POVResult(exit_code=124, stderr="POV execution timed out")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8080)

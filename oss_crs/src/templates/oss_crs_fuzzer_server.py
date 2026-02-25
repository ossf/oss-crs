#!/usr/bin/env python3
"""HTTP fuzzer server for running fuzzers in a sidecar container.

Endpoints:
    POST /start       - Start a fuzzer (returns fuzzer_id immediately)
    GET  /status/<id> - Poll fuzzer status (running/stopped/crashed, stats)
    POST /stop/<id>   - Stop running fuzzer (blocks until terminated)
    GET  /list        - List all fuzzer instances
    GET  /health      - Healthcheck

Key differences from builder server:
    - Fuzzers run concurrently (not sequential job queue)
    - Long-running processes (vs. finite build jobs)
    - Stats parsing from libfuzzer stderr
"""
import os
import re
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI(title="Fuzzer Sidecar", description="Fuzzing server for CRS")

# Global registry of running fuzzers
_fuzzers: dict[str, dict] = {}
_fuzzers_lock = threading.Lock()

# Directory containing compiled harness binaries
# In CRS compose: snapshot output is mounted at /out
OUT_DIR = Path("/out")


class StartFuzzerRequest(BaseModel):
    harness_name: str
    corpus_dir: str
    crashes_dir: str
    engine: str = "libfuzzer"
    timeout: int = 0
    extra_args: Optional[list[str]] = None


class StartFuzzerResponse(BaseModel):
    fuzzer_id: str
    pid: int
    status: str


class FuzzerStatusResponse(BaseModel):
    state: str  # "running", "stopped", "crashed"
    runtime_seconds: float
    execs: int
    corpus_size: int
    crashes_found: int
    pid: int


class FuzzerResultResponse(BaseModel):
    exit_code: int
    runtime_seconds: float
    corpus_size: int
    crashes_found: int


class FuzzerListItem(BaseModel):
    fuzzer_id: str
    pid: int


# Regex patterns for parsing libfuzzer output
_LIBFUZZER_EXECS_RE = re.compile(r"#(\d+)")
_LIBFUZZER_CORPUS_RE = re.compile(r"cov:\s*(\d+)")


def _parse_libfuzzer_stats(stderr_lines: list[str]) -> dict:
    """Parse libfuzzer stderr for statistics."""
    execs = 0
    corpus_size = 0

    for line in reversed(stderr_lines[-100:]):  # Check last 100 lines
        if execs == 0:
            match = _LIBFUZZER_EXECS_RE.search(line)
            if match:
                execs = int(match.group(1))

        if corpus_size == 0:
            match = _LIBFUZZER_CORPUS_RE.search(line)
            if match:
                corpus_size = int(match.group(1))

        if execs > 0 and corpus_size > 0:
            break

    return {"execs": execs, "corpus_size": corpus_size}


def _count_crashes(crashes_dir: Path) -> int:
    """Count crash files in the crashes directory."""
    if not crashes_dir.exists():
        return 0
    return len([f for f in crashes_dir.iterdir() if f.is_file() and f.name.startswith("crash-")])


def _build_fuzzer_cmd(
    engine: str,
    harness: Path,
    corpus_dir: Path,
    crashes_dir: Path,
    timeout: int = 0,
    extra_args: list[str] | None = None,
) -> list[str]:
    """Build engine-specific command line for running a fuzzer."""
    if engine == "libfuzzer":
        cmd = [str(harness)]

        # Artifact prefix for crashes
        cmd.append(f"-artifact_prefix={crashes_dir}/")

        # Disable leak detection (leaks are not POVs)
        cmd.append("-detect_leaks=0")

        # Timeout per input
        if timeout > 0:
            cmd.append(f"-max_total_time={timeout}")

        # Add extra args if provided
        if extra_args:
            cmd.extend(extra_args)

        # Corpus directory as positional argument
        cmd.append(str(corpus_dir))

        return cmd

    elif engine == "afl":
        cmd = ["afl-fuzz"]
        cmd.extend(["-i", str(corpus_dir)])
        cmd.extend(["-o", str(crashes_dir)])
        if timeout > 0:
            cmd.extend(["-V", str(timeout)])
        if extra_args:
            cmd.extend(extra_args)
        cmd.append("--")
        cmd.append(str(harness))
        return cmd

    else:
        raise ValueError(f"Unsupported fuzzing engine: {engine}")


def _fuzzer_monitor(fuzzer_id: str):
    """Background thread to monitor fuzzer process and capture output."""
    with _fuzzers_lock:
        fuzzer = _fuzzers.get(fuzzer_id)
        if not fuzzer:
            return

    proc = fuzzer["process"]
    stderr_lines = fuzzer["stderr_lines"]

    # Read stderr line by line
    try:
        for line in iter(proc.stderr.readline, ""):
            if not line:
                break
            stderr_lines.append(line.rstrip())
            # Keep only last 1000 lines
            if len(stderr_lines) > 1000:
                stderr_lines.pop(0)
    except Exception:
        pass

    # Process has exited
    proc.wait()

    with _fuzzers_lock:
        if fuzzer_id in _fuzzers:
            _fuzzers[fuzzer_id]["end_time"] = time.time()
            exit_code = proc.returncode
            if exit_code is None:
                _fuzzers[fuzzer_id]["state"] = "stopped"
            elif exit_code == 0 or exit_code == -signal.SIGTERM:
                _fuzzers[fuzzer_id]["state"] = "stopped"
            else:
                _fuzzers[fuzzer_id]["state"] = "crashed"
            _fuzzers[fuzzer_id]["exit_code"] = exit_code if exit_code is not None else 0


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/start", response_model=StartFuzzerResponse)
def start_fuzzer(request: StartFuzzerRequest):
    harness_path = OUT_DIR / request.harness_name
    if not harness_path.exists():
        return JSONResponse(
            status_code=404,
            content={"error": f"Harness not found: {harness_path}"},
        )

    corpus_dir = Path(request.corpus_dir)
    crashes_dir = Path(request.crashes_dir)

    # Ensure directories exist
    corpus_dir.mkdir(parents=True, exist_ok=True)
    crashes_dir.mkdir(parents=True, exist_ok=True)

    try:
        cmd = _build_fuzzer_cmd(
            engine=request.engine,
            harness=harness_path,
            corpus_dir=corpus_dir,
            crashes_dir=crashes_dir,
            timeout=request.timeout,
            extra_args=request.extra_args,
        )
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

    fuzzer_id = uuid.uuid4().hex[:12]

    # Start the fuzzer process
    env = os.environ.copy()
    # Set common fuzzer environment
    env.setdefault("FUZZER_ARGS", "-rss_limit_mb=2560 -timeout=25")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        cwd=str(OUT_DIR),
    )

    with _fuzzers_lock:
        _fuzzers[fuzzer_id] = {
            "fuzzer_id": fuzzer_id,
            "pid": proc.pid,
            "process": proc,
            "state": "running",
            "start_time": time.time(),
            "end_time": None,
            "exit_code": None,
            "stderr_lines": [],
            "corpus_dir": corpus_dir,
            "crashes_dir": crashes_dir,
            "engine": request.engine,
        }

    # Start monitor thread
    monitor_thread = threading.Thread(
        target=_fuzzer_monitor,
        args=(fuzzer_id,),
        daemon=True,
    )
    monitor_thread.start()

    return StartFuzzerResponse(
        fuzzer_id=fuzzer_id,
        pid=proc.pid,
        status="running",
    )


@app.get("/status/{fuzzer_id}", response_model=FuzzerStatusResponse)
def get_status(fuzzer_id: str):
    with _fuzzers_lock:
        fuzzer = _fuzzers.get(fuzzer_id)
        if not fuzzer:
            return JSONResponse(
                status_code=404,
                content={"error": f"Fuzzer not found: {fuzzer_id}"},
            )

        end_time = fuzzer["end_time"] or time.time()
        runtime = end_time - fuzzer["start_time"]

        # Parse stats from stderr
        stats = _parse_libfuzzer_stats(fuzzer["stderr_lines"])
        crashes = _count_crashes(fuzzer["crashes_dir"])

        return FuzzerStatusResponse(
            state=fuzzer["state"],
            runtime_seconds=runtime,
            execs=stats["execs"],
            corpus_size=stats["corpus_size"],
            crashes_found=crashes,
            pid=fuzzer["pid"],
        )


@app.post("/stop/{fuzzer_id}", response_model=FuzzerResultResponse)
def stop_fuzzer(fuzzer_id: str):
    with _fuzzers_lock:
        fuzzer = _fuzzers.get(fuzzer_id)
        if not fuzzer:
            return JSONResponse(
                status_code=404,
                content={"error": f"Fuzzer not found: {fuzzer_id}"},
            )

        proc = fuzzer["process"]
        state = fuzzer["state"]

    # If still running, terminate it
    if state == "running":
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    # Wait for monitor thread to update state
    time.sleep(0.1)

    with _fuzzers_lock:
        fuzzer = _fuzzers.get(fuzzer_id)
        if not fuzzer:
            return JSONResponse(
                status_code=404,
                content={"error": f"Fuzzer not found: {fuzzer_id}"},
            )

        end_time = fuzzer["end_time"] or time.time()
        runtime = end_time - fuzzer["start_time"]
        stats = _parse_libfuzzer_stats(fuzzer["stderr_lines"])
        crashes = _count_crashes(fuzzer["crashes_dir"])
        exit_code = fuzzer["exit_code"] if fuzzer["exit_code"] is not None else 0

        return FuzzerResultResponse(
            exit_code=exit_code,
            runtime_seconds=runtime,
            corpus_size=stats["corpus_size"],
            crashes_found=crashes,
        )


@app.get("/list", response_model=list[FuzzerListItem])
def list_fuzzers():
    with _fuzzers_lock:
        return [
            FuzzerListItem(fuzzer_id=f["fuzzer_id"], pid=f["pid"])
            for f in _fuzzers.values()
        ]


if __name__ == "__main__":
    import uvicorn

    # Ensure /out exists (should be mounted from snapshot output)
    if not OUT_DIR.exists():
        print(f"Warning: {OUT_DIR} does not exist")

    uvicorn.run(app, host="0.0.0.0", port=8080)

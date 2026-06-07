# SPDX-License-Identifier: MIT
"""WebUI server: standalone monitoring dashboard for OSS-CRS.

Receives metrics from publisher sidecars via HTTP push.
Manages multiple concurrent runs with per-run history.
Serves a run list page and per-run drill-down dashboards.
"""

import json
import logging
import os
import signal
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from jinja2 import Environment, FileSystemLoader

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
WEBUI_PORT = int(os.environ.get("WEBUI_PORT", "9090"))
LOG_DIR = Path(os.environ.get("WEBUI_LOG_DIR", "/webui_logs"))
HISTORY_SIZE = 720  # ~1 hour at 5s intervals
# A run with no snapshot for this long is considered "stale" (publisher gone /
# unreachable). Publishers push every ~5s, so this tolerates a few missed polls.
STALE_AFTER_SECONDS = 25

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [webui] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("webui")

_shutdown = threading.Event()

# ---------------------------------------------------------------------------
# Run state
# ---------------------------------------------------------------------------


@dataclass
class RunState:
    run_id: str
    target: str = ""
    crs_names: list = field(default_factory=list)
    crs_resources: dict = field(default_factory=dict)
    harness: str = ""
    registered_at: float = field(default_factory=time.time)
    last_snapshot_at: float = field(default_factory=time.time)
    done: bool = False
    history: deque = field(default_factory=lambda: deque(maxlen=HISTORY_SIZE))
    latest: dict = field(default_factory=dict)

    def status(self) -> str:
        """Lifecycle status derived from the publisher's done flag and recency.

        - ``finished``: the publisher reported the run ended (final snapshot).
        - ``stale``: no snapshot for a while; the publisher likely went away
          (run torn down, container killed, or network lost).
        - ``live``: receiving fresh snapshots.
        """
        if self.done:
            return "finished"
        if time.time() - self.last_snapshot_at > STALE_AFTER_SECONDS:
            return "stale"
        return "live"


_lock = threading.Lock()
_runs: dict[str, RunState] = {}


def _get_or_create_run(run_id: str) -> RunState:
    if run_id not in _runs:
        _runs[run_id] = RunState(run_id=run_id)
    return _runs[run_id]


def _headline_count(per_crs: dict, exchange: dict, dtype: str) -> int:
    """Authoritative artifact count for a run summary.

    Prefer the sum of per-CRS SUBMIT_DIR counts (the source of truth that
    ``meta.json`` also uses); fall back to the shared EXCHANGE_DIR count when
    no per-CRS data is present.
    """
    if per_crs:
        return sum(c.get(dtype, 0) for c in per_crs.values())
    return exchange.get(dtype, 0)


# ---------------------------------------------------------------------------
# JSONL logging
# ---------------------------------------------------------------------------


def _log_snapshot(run_id: str, snapshot: dict) -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path = LOG_DIR / f"{run_id}.jsonl"
        with open(log_path, "a") as f:
            f.write(json.dumps(snapshot) + "\n")
            f.flush()
    except OSError:
        pass


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
app = FastAPI(title="OSS-CRS WebUI", docs_url=None, redoc_url=None)

_templates = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=True,
)


# --- Health ---


@app.get("/health")
def health():
    return {"status": "ok"}


# --- Receive endpoints (from publisher sidecars) ---


@app.post("/api/runs/{run_id}/register")
async def register_run(run_id: str, request: Request):
    body = await request.json()
    with _lock:
        run = _get_or_create_run(run_id)
        run.target = body.get("target", "")
        run.crs_names = body.get("crs_names", [])
        run.crs_resources = body.get("crs_resources", {})
        run.harness = body.get("harness", "")
    log.info(
        "registered run %s (target=%s, %d CRSs)", run_id, run.target, len(run.crs_names)
    )
    return {"status": "ok"}


@app.post("/api/runs/{run_id}/snapshot")
async def receive_snapshot(run_id: str, request: Request):
    body = await request.json()
    meta = body.pop("_meta", None)
    is_final = bool(body.pop("done", False))
    with _lock:
        run = _get_or_create_run(run_id)
        # Fill in metadata from snapshot if register was missed
        if meta and not run.target:
            run.target = meta.get("target", "")
            run.crs_names = meta.get("crs_names", [])
            run.crs_resources = meta.get("crs_resources", {})
            run.harness = meta.get("harness", "")
        run.latest = body
        run.history.append(body)
        run.last_snapshot_at = time.time()
        if is_final:
            run.done = True
    _log_snapshot(run_id, body)
    return {"status": "ok"}


@app.post("/api/runs/{run_id}/finalize")
async def finalize_run(run_id: str, request: Request):
    """Authoritative end-of-run update, pushed by the host after teardown.

    The publisher sidecar dies with the run, so artifacts harvested during the
    final teardown (e.g. a late libFuzzer crash, or the exchange merge) never
    reach it. The host re-reads the final on-disk counts and posts them here.

    Unlike /snapshot this MERGES onto the latest snapshot — only the provided
    keys are overwritten — so live-only fields such as coverage are preserved.
    The run is then marked finished.
    """
    body = await request.json()
    meta = body.pop("_meta", None)
    with _lock:
        run = _get_or_create_run(run_id)
        if meta and not run.target:
            run.target = meta.get("target", "")
            run.crs_names = meta.get("crs_names", [])
            run.crs_resources = meta.get("crs_resources", {})
            run.harness = meta.get("harness", "")
        merged = dict(run.latest) if run.latest else {}
        for key in ("per_crs", "exchange", "cost", "elapsed_seconds", "coverage"):
            if body.get(key) is not None:
                merged[key] = body[key]
        run.latest = merged
        run.history.append(merged)
        run.last_snapshot_at = time.time()
        run.done = True
    log.info("finalized run %s", run_id)
    _log_snapshot(run_id, {**merged, "done": True})
    return {"status": "ok"}


# --- Query endpoints (from dashboard) ---


@app.get("/api/runs")
def list_runs():
    with _lock:
        result = []
        for run in _runs.values():
            latest = run.latest
            per_crs = latest.get("per_crs", {}) if latest else {}
            exchange = latest.get("exchange", {}) if latest else {}
            coverage = latest.get("coverage")
            cost = latest.get("cost") if latest else None

            # Headline counts come from the per-CRS SUBMIT_DIR totals (what each
            # CRS actually produced) rather than the shared EXCHANGE_DIR. The
            # exchange is a deduped pool the exchange sidecar fills on its own
            # cycle, so it lags SUBMIT and undercounts near teardown. Summing
            # per-CRS matches what `meta.json` reports as the run's results.
            result.append(
                {
                    "run_id": run.run_id,
                    "target": run.target,
                    "status": run.status(),
                    "crs_count": len(run.crs_names),
                    "crs_names": run.crs_names,
                    "elapsed_seconds": latest.get("elapsed_seconds", 0)
                    if latest
                    else 0,
                    "povs": _headline_count(per_crs, exchange, "povs"),
                    "seeds": _headline_count(per_crs, exchange, "seeds"),
                    "patches": _headline_count(per_crs, exchange, "patches"),
                    "snapshots": len(run.history),
                    "coverage_pct": round(coverage["lines"]["pct"], 1)
                    if coverage
                    else None,
                    "cost": round(cost["total"], 4)
                    if cost and cost.get("total") is not None
                    else None,
                }
            )
    return result


@app.get("/api/runs/{run_id}/status")
def run_status(run_id: str):
    with _lock:
        run = _runs.get(run_id)
        if not run:
            return JSONResponse(status_code=404, content={"error": "run not found"})
        snapshot = run.latest.copy() if run.latest else {}
        snapshot["status"] = run.status()
    snapshot["run_id"] = run.run_id
    snapshot["target"] = run.target
    snapshot["crs_resources"] = run.crs_resources
    return snapshot


@app.get("/api/runs/{run_id}/history")
def run_history(run_id: str, last: int = 360):
    with _lock:
        run = _runs.get(run_id)
        if not run:
            return JSONResponse(status_code=404, content={"error": "run not found"})
        entries = list(run.history)
    return entries[-last:]


# --- Dashboard pages ---


@app.get("/", response_class=HTMLResponse)
def dashboard():
    template = _templates.get_template("dashboard.html")
    return template.render()


@app.get("/run/{run_id}", response_class=HTMLResponse)
def run_detail(run_id: str):
    with _lock:
        run = _runs.get(run_id)
        if not run:
            return HTMLResponse(status_code=404, content="Run not found")
    template = _templates.get_template("run_detail.html")
    return template.render(
        run_id=run.run_id,
        target=run.target,
        crs_names=run.crs_names,
        crs_resources=run.crs_resources,
    )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def main():
    log.info("webui server starting on port %d", WEBUI_PORT)

    def _handle_signal(signum, _frame):
        log.info("received signal %d, shutting down", signum)
        _shutdown.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    uvicorn.run(app, host="0.0.0.0", port=WEBUI_PORT, log_level="warning")


if __name__ == "__main__":
    main()

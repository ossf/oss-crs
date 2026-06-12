# SPDX-License-Identifier: MIT
"""WebUI publisher sidecar: scans CRS artifacts and pushes metrics to the web-ui service.

Runs inside the docker-compose alongside CRS containers. Reads submit/exchange
directories (same volume mounts as the old webui sidecar) and periodically
POSTs snapshots to the standalone web-ui service via HTTP.

Also handles coverage collection when a coverage build is available.
"""

import glob as globmod
import json
import logging
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------
CRS_NAMES = [n.strip() for n in os.environ.get("CRS_NAMES", "").split(",") if n.strip()]
CRS_RESOURCES = json.loads(os.environ.get("CRS_RESOURCES", "{}"))
RUN_ID = os.environ.get("RUN_ID", "unknown")
TARGET_NAME = os.environ.get("TARGET_NAME", "unknown")
HARNESS_NAME = os.environ.get("HARNESS_NAME", "")
WEBUI_URL = os.environ.get("WEBUI_URL", "http://host.docker.internal:9090").rstrip("/")

SUBMIT_ROOT = Path("/submit")
EXCHANGE_ROOT = Path("/OSS_CRS_EXCHANGE_DIR")
# Post-processed (triaged / filtered) artifacts, mounted only when the run
# includes a post-processor CRS. PROCESSED_EXCHANGE_TYPES lists the data types
# whose net count should come from here instead of the raw exchange.
PROCESSED_EXCHANGE_ROOT = Path("/OSS_CRS_PROCESSED_EXCHANGE_DIR")
PROCESSED_EXCHANGE_TYPES = tuple(
    t.strip()
    for t in os.environ.get("PROCESSED_EXCHANGE_TYPES", "").split(",")
    if t.strip()
)
COVERAGE_BUILD_DIR = Path("/coverage_build")
LOG_DIR = Path("/webui_logs")
# LiteLLM spend report (mounted read-only when an LLM proxy is in the run).
# Written periodically by the litellm-key-gen sidecar; absent for LLM-free runs.
SPEND_REPORT_PATH = Path("/litellm-spend-report.json")

POLL_INTERVAL = 5  # seconds
COVERAGE_INTERVAL = 30  # seconds

ALLOWED_DATA_TYPES = ("povs", "seeds", "bug-candidates", "patches", "diffs")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [publisher] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("publisher")

_start_time = time.monotonic()
_shutdown = threading.Event()
_coverage_data: dict = {}
_coverage_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Artifact scanning (same logic as old webui sidecar)
# ---------------------------------------------------------------------------


def _count_files(directory: Path) -> int:
    if not directory.is_dir():
        return 0
    count = 0
    try:
        for entry in os.scandir(directory):
            if entry.is_file(follow_symlinks=False):
                count += 1
    except OSError:
        pass
    return count


def _scan_dir(root: Path) -> dict[str, int]:
    counts = {}
    for dtype in ALLOWED_DATA_TYPES:
        counts[dtype] = _count_files(root / dtype)
    return counts


def read_cost() -> dict | None:
    """Read LLM spend from the litellm-key-gen spend report, if present.

    Returns ``{"total": float, "per_crs": {crs_name: float}}`` or ``None`` when
    no spend report exists yet (e.g. LLM-free runs or before the first write).
    """
    if not SPEND_REPORT_PATH.is_file():
        return None
    try:
        data = json.loads(SPEND_REPORT_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    total = data.get("totals", {}).get("credits_used")
    per_crs = {
        name: entry.get("credits_used", 0.0)
        for name, entry in data.get("crs", {}).items()
        if isinstance(entry, dict)
    }
    if total is None and not per_crs:
        return None
    return {"total": total, "per_crs": per_crs}


def build_snapshot() -> dict:
    now = datetime.now(timezone.utc).isoformat()
    elapsed = time.monotonic() - _start_time

    per_crs = {}
    for name in CRS_NAMES:
        per_crs[name] = _scan_dir(SUBMIT_ROOT / name)

    exchange = _scan_dir(EXCHANGE_ROOT)

    # Net counts: per data type, prefer the post-processed (triaged/filtered)
    # count when a processor handles that type (e.g. triage -> povs, seed-filter
    # -> seeds); all other types fall back to the raw exchange count. With no
    # post-processor in the run, net is just the raw exchange.
    if PROCESSED_EXCHANGE_TYPES:
        processed = _scan_dir(PROCESSED_EXCHANGE_ROOT)
        net = {
            dtype: (
                processed[dtype]
                if dtype in PROCESSED_EXCHANGE_TYPES
                else exchange[dtype]
            )
            for dtype in ALLOWED_DATA_TYPES
        }
    else:
        net = dict(exchange)

    with _coverage_lock:
        cov = _coverage_data.copy() if _coverage_data else None

    return {
        "timestamp": now,
        "elapsed_seconds": round(elapsed, 1),
        "per_crs": per_crs,
        "exchange": exchange,
        "net": net,
        "coverage": cov,
        "cost": read_cost(),
    }


# ---------------------------------------------------------------------------
# Coverage collection (moved from old webui sidecar)
# ---------------------------------------------------------------------------


def _find_llvm_tool(name: str) -> str | None:
    bundled = COVERAGE_BUILD_DIR / "coverage-build" / name
    if bundled.is_file() and os.access(bundled, os.X_OK):
        return str(bundled)
    if shutil.which(name):
        return name
    for v in range(20, 10, -1):
        versioned = f"{name}-{v}"
        if shutil.which(versioned):
            return versioned
    return None


def _find_coverage_binary() -> Path | None:
    cov_bin_dir = COVERAGE_BUILD_DIR / "coverage-build"
    if not cov_bin_dir.is_dir():
        return None
    if HARNESS_NAME:
        candidate = cov_bin_dir / HARNESS_NAME
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    try:
        for entry in os.scandir(cov_bin_dir):
            if entry.is_file() and os.access(entry.path, os.X_OK):
                return Path(entry.path)
    except OSError:
        pass
    return None


def _collect_corpus() -> list[Path]:
    files = []
    seeds_dir = EXCHANGE_ROOT / "seeds"
    if seeds_dir.is_dir():
        for entry in os.scandir(seeds_dir):
            if entry.is_file(follow_symlinks=False):
                files.append(Path(entry.path))
    for name in CRS_NAMES:
        crs_seeds = SUBMIT_ROOT / name / "seeds"
        if crs_seeds.is_dir():
            for entry in os.scandir(crs_seeds):
                if entry.is_file(follow_symlinks=False):
                    files.append(Path(entry.path))
    return files


def _run_coverage_once(binary: Path, profdata_tool: str, cov_tool: str) -> dict | None:
    corpus_files = _collect_corpus()
    if not corpus_files:
        return None

    with tempfile.TemporaryDirectory(prefix="cov-") as tmpdir:
        tmpdir_path = Path(tmpdir)
        corpus_dir = tmpdir_path / "corpus"
        corpus_dir.mkdir()

        seen = set()
        for f in corpus_files:
            if f.name not in seen:
                seen.add(f.name)
                try:
                    shutil.copy2(f, corpus_dir / f.name)
                except OSError:
                    pass

        if not any(corpus_dir.iterdir()):
            return None

        profraw = tmpdir_path / "cov.%m.profraw"
        profdata = tmpdir_path / "merged.profdata"

        env = os.environ.copy()
        env["LLVM_PROFILE_FILE"] = str(profraw)
        try:
            subprocess.run(
                [str(binary), str(corpus_dir), "-runs=0"],
                env=env,
                timeout=120,
                capture_output=True,
            )
        except (subprocess.TimeoutExpired, OSError) as e:
            log.warning("coverage run failed: %s", e)
            return None

        profraw_files = globmod.glob(str(tmpdir_path / "cov.*.profraw"))
        if not profraw_files:
            single = tmpdir_path / "cov.profraw"
            if single.exists():
                profraw_files = [str(single)]
        if not profraw_files:
            log.warning("no profraw files produced")
            return None

        try:
            subprocess.run(
                [profdata_tool, "merge", "-sparse"]
                + profraw_files
                + ["-o", str(profdata)],
                timeout=60,
                capture_output=True,
                check=True,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
            log.warning("profdata merge failed: %s", e)
            return None

        if not profdata.exists():
            return None

        try:
            result = subprocess.run(
                [
                    cov_tool,
                    "export",
                    "-summary-only",
                    f"-instr-profile={profdata}",
                    f"-object={binary}",
                ],
                timeout=60,
                capture_output=True,
                check=True,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as e:
            log.warning("llvm-cov export failed: %s", e)
            return None

        try:
            cov_json = json.loads(result.stdout)
            totals = cov_json["data"][0]["totals"]
            return {
                "lines": {
                    "covered": totals["lines"]["covered"],
                    "total": totals["lines"]["count"],
                    "pct": totals["lines"]["percent"],
                },
                "functions": {
                    "covered": totals["functions"]["covered"],
                    "total": totals["functions"]["count"],
                    "pct": totals["functions"]["percent"],
                },
                "branches": {
                    "covered": totals.get("branches", {}).get("covered", 0),
                    "total": totals.get("branches", {}).get("count", 0),
                    "pct": totals.get("branches", {}).get("percent", 0),
                },
                "corpus_files": len(seen),
            }
        except (KeyError, IndexError, json.JSONDecodeError):
            log.warning("failed to parse coverage JSON")
            return None


def coverage_loop():
    binary = _find_coverage_binary()
    if not binary:
        log.info("no coverage binary found, coverage collection disabled")
        return

    profdata_tool = _find_llvm_tool("llvm-profdata")
    cov_tool = _find_llvm_tool("llvm-cov")
    if not profdata_tool or not cov_tool:
        log.warning("llvm tools not found, coverage disabled")
        return

    log.info(
        "coverage collector started: binary=%s, interval=%ds",
        binary.name,
        COVERAGE_INTERVAL,
    )
    _shutdown.wait(COVERAGE_INTERVAL)

    while not _shutdown.is_set():
        try:
            result = _run_coverage_once(binary, profdata_tool, cov_tool)
            if result:
                with _coverage_lock:
                    global _coverage_data
                    _coverage_data = result
                log.info(
                    "coverage: %.1f%% lines (%d/%d), %d corpus files",
                    result["lines"]["pct"],
                    result["lines"]["covered"],
                    result["lines"]["total"],
                    result["corpus_files"],
                )
        except Exception:
            log.exception("coverage collection failed")
        _shutdown.wait(COVERAGE_INTERVAL)


# ---------------------------------------------------------------------------
# HTTP push to web-ui
# ---------------------------------------------------------------------------


def _post(path: str, data: dict) -> bool:
    url = f"{WEBUI_URL}{path}"
    try:
        resp = requests.post(url, json=data, timeout=5)
        if resp.status_code >= 400:
            log.warning("POST %s returned %d", path, resp.status_code)
            return False
        return True
    except requests.RequestException as e:
        log.warning("POST %s failed: %s", path, e)
        return False


def register_run() -> bool:
    return _post(
        f"/api/runs/{RUN_ID}/register",
        {
            "target": TARGET_NAME,
            "crs_names": CRS_NAMES,
            "crs_resources": CRS_RESOURCES,
            "harness": HARNESS_NAME,
        },
    )


def push_snapshot(snapshot: dict) -> bool:
    # Include registration metadata so the webui can populate it
    # even if the initial register call failed
    snapshot["_meta"] = {
        "target": TARGET_NAME,
        "crs_names": CRS_NAMES,
        "crs_resources": CRS_RESOURCES,
        "harness": HARNESS_NAME,
    }
    return _post(f"/api/runs/{RUN_ID}/snapshot", snapshot)


# ---------------------------------------------------------------------------
# JSONL local fallback log
# ---------------------------------------------------------------------------


def _log_snapshot(snapshot: dict) -> None:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path = LOG_DIR / "metrics.jsonl"
        with open(log_path, "a") as f:
            f.write(json.dumps(snapshot) + "\n")
            f.flush()
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def publisher_loop():
    log.info("publisher started (poll every %ds, target=%s)", POLL_INTERVAL, WEBUI_URL)

    # Register with web-ui (retry a few times on startup)
    for attempt in range(12):
        if _shutdown.is_set():
            return
        if register_run():
            log.info("registered run %s with web-ui", RUN_ID)
            break
        log.info("web-ui not reachable (attempt %d/12), retrying in 5s...", attempt + 1)
        _shutdown.wait(5)
    else:
        log.warning("could not register with web-ui, will push without registration")

    while not _shutdown.is_set():
        try:
            snapshot = build_snapshot()
            push_snapshot(snapshot)
            _log_snapshot(snapshot)
        except Exception:
            log.exception("publisher loop failed")
        _shutdown.wait(POLL_INTERVAL)


def main():
    log.info(
        "publisher starting: %d CRSs, run_id=%s, webui=%s",
        len(CRS_NAMES),
        RUN_ID,
        WEBUI_URL,
    )

    def _handle_signal(signum, _frame):
        log.info("received signal %d, shutting down", signum)
        _shutdown.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    coverage_thread = threading.Thread(target=coverage_loop, daemon=True)
    coverage_thread.start()

    publisher_loop()

    # The loop exited because we were signalled to stop, which (under
    # docker compose down) means the run is ending. Push one final snapshot
    # flagged done=True so the dashboard marks the run finished instead of
    # leaving it to time out into "stale".
    try:
        final = build_snapshot()
        final["done"] = True
        push_snapshot(final)
        _log_snapshot(final)
        log.info("pushed final snapshot for run %s", RUN_ID)
    except Exception:
        log.exception("failed to push final snapshot")


if __name__ == "__main__":
    main()

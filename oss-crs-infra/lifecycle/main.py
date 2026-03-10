"""Lifecycle sidecar: signals when all watched CRS containers have exited.

Monitors Docker container events for a set of Compose services and writes
a ready signal to the exchange directory once every watched service has
stopped.  This lets an ensemble CRS know that no more artifacts will arrive.
"""

import json
import logging
import os
import signal
import sys
import time
from pathlib import Path

import docker

EXCHANGE_ROOT = Path("/exchange")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [lifecycle] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("lifecycle")


def _write_ready(exit_info: dict) -> None:
    """Write the ready signal file to the exchange status directory."""
    status_dir = EXCHANGE_ROOT / "status"
    status_dir.mkdir(parents=True, exist_ok=True)
    ready_path = status_dir / "ready"
    ready_path.write_text(json.dumps(exit_info, indent=2) + "\n")
    log.info("wrote ready signal to %s", ready_path)


def main() -> None:
    compose_project = os.environ.get("COMPOSE_PROJECT", "")
    watch_raw = os.environ.get("WATCH_SERVICES", "")

    if not compose_project:
        log.error("COMPOSE_PROJECT not set")
        sys.exit(1)
    if not watch_raw:
        log.info("WATCH_SERVICES is empty, nothing to watch — exiting")
        return

    watch_services = set(s.strip() for s in watch_raw.split(",") if s.strip())
    log.info(
        "watching %d service(s): %s",
        len(watch_services),
        ", ".join(sorted(watch_services)),
    )

    client = docker.from_env()

    remaining = set(watch_services)
    exit_info: dict[str, dict] = {}

    shutdown_requested = False

    def _shutdown(signum, _frame):
        nonlocal shutdown_requested
        log.info("received signal %d, shutting down", signum)
        shutdown_requested = True

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    # Record timestamp before checking current state to avoid race condition:
    # any container that exits between this timestamp and the event listener
    # will be caught by the `since` parameter on the event stream.
    start_time = int(time.time())

    # Check already-exited containers
    try:
        containers = client.containers.list(
            all=True,
            filters={
                "label": f"com.docker.compose.project={compose_project}",
            },
        )
        for container in containers:
            service = container.labels.get("com.docker.compose.service", "")
            if service in remaining and container.status in ("exited", "dead"):
                exit_code = container.attrs.get("State", {}).get("ExitCode", -1)
                exit_info[service] = {
                    "exit_code": exit_code,
                    "status": container.status,
                }
                remaining.discard(service)
                log.info("service %s already exited (code=%s)", service, exit_code)
    except Exception:
        log.exception("failed to list containers, will rely on events")

    if not remaining:
        log.info("all watched services already exited")
        _write_ready(exit_info)
        return

    log.info(
        "waiting for %d service(s): %s",
        len(remaining),
        ", ".join(sorted(remaining)),
    )

    # Listen for container die events
    try:
        for event in client.events(
            decode=True,
            since=start_time,
            filters={
                "event": "die",
                "label": f"com.docker.compose.project={compose_project}",
            },
        ):
            if shutdown_requested:
                break

            attrs = event.get("Actor", {}).get("Attributes", {})
            service = attrs.get("com.docker.compose.service", "")
            if service in remaining:
                raw_code = attrs.get("exitCode", "unknown")
                exit_code = int(raw_code) if str(raw_code).isdigit() else -1
                exit_info[service] = {
                    "exit_code": exit_code,
                    "status": "exited",
                }
                remaining.discard(service)
                log.info(
                    "service %s exited (code=%s), %d remaining",
                    service,
                    exit_code,
                    len(remaining),
                )

                if not remaining:
                    break
    except Exception:
        log.exception("event stream error")

    if not remaining:
        _write_ready(exit_info)
    else:
        log.warning(
            "exiting with %d service(s) still running: %s",
            len(remaining),
            ", ".join(sorted(remaining)),
        )


if __name__ == "__main__":
    main()

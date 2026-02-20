"""Exchange sidecar: polls SUBMIT_DIR trees and copies new files to EXCHANGE_DIR.

Layout expected:
  /submit/<crs_name>/<data_type_dir>/<hash_file>
  /exchange/<data_type_dir>/<hash_file>

Directory names are plural (povs, seeds, bug-candidates, patches, diffs).
Files are already hash-named, so dedup is by filename.

Uses polling (not inotify) because inotify does not work reliably
across Docker bind-mount boundaries on all storage drivers.
"""

import logging
import os
import shutil
import signal
import stat
import tempfile
import time
from pathlib import Path

SUBMIT_ROOT = Path("/submit")
EXCHANGE_ROOT = Path("/exchange")
POLL_INTERVAL = 2  # seconds

ALLOWED_DATA_TYPES = {"povs", "seeds", "bug-candidates", "patches", "diffs"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [exchange] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("exchange")


def _is_safe_name(name: str) -> bool:
    """Reject path components that could escape the expected directory."""
    return bool(name) and "/" not in name and name not in ("..", ".")


def sync_once(created_dirs: set[str], warned_types: set[str]) -> None:
    """Scan every /submit/<crs>/<type>/ dir and copy unseen files to /exchange/<type>/."""
    if not SUBMIT_ROOT.is_dir():
        return

    for crs_entry in os.scandir(SUBMIT_ROOT):
        if not crs_entry.is_dir(follow_symlinks=False):
            continue
        for type_entry in os.scandir(crs_entry.path):
            if not type_entry.is_dir(follow_symlinks=False):
                continue
            data_type = type_entry.name

            if data_type not in ALLOWED_DATA_TYPES:
                if data_type not in warned_types:
                    log.warning("ignoring unknown data_type: %r", data_type)
                    warned_types.add(data_type)
                continue

            dst_dir = EXCHANGE_ROOT / data_type
            if data_type not in created_dirs:
                dst_dir.mkdir(parents=True, exist_ok=True)
                created_dirs.add(data_type)

            for f_entry in os.scandir(type_entry.path):
                if f_entry.is_symlink():
                    continue
                if not f_entry.is_file(follow_symlinks=False):
                    continue
                if not _is_safe_name(f_entry.name):
                    continue

                dst = dst_dir / f_entry.name
                if dst.exists():
                    continue

                tmp_path = None
                try:
                    # Open with O_NOFOLLOW to prevent TOCTOU symlink race
                    fd_src = os.open(f_entry.path, os.O_RDONLY | os.O_NOFOLLOW)
                    try:
                        src_stat = os.fstat(fd_src)
                        if not stat.S_ISREG(src_stat.st_mode):
                            os.close(fd_src)
                            continue
                        fsrc = os.fdopen(fd_src, "rb")
                    except BaseException:
                        os.close(fd_src)
                        raise
                    # fd_src is now owned by fsrc — never close fd_src directly
                    fd, tmp_path = tempfile.mkstemp(dir=str(dst_dir), prefix=".")
                    os.close(fd)
                    with fsrc, open(tmp_path, "wb") as fdst:
                        shutil.copyfileobj(fsrc, fdst)
                    os.rename(tmp_path, str(dst))
                    os.chmod(str(dst), 0o644)
                    log.info("copied %s/%s", data_type, f_entry.name)
                except OSError as exc:
                    log.warning("copy failed %s: %s", f_entry.path, exc)
                    if tmp_path is not None:
                        try:
                            os.unlink(tmp_path)
                        except OSError:
                            pass


def main() -> None:
    log.info("exchange sidecar started (poll every %ds)", POLL_INTERVAL)
    created_dirs: set[str] = set()
    warned_types: set[str] = set()
    shutdown_requested = False

    def _shutdown(signum, _frame):
        nonlocal shutdown_requested
        log.info("received signal %d, will sync and exit", signum)
        shutdown_requested = True

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    while not shutdown_requested:
        try:
            sync_once(created_dirs, warned_types)
        except Exception:
            log.exception("sync_once failed, will retry")
        time.sleep(POLL_INTERVAL)

    # Final sync after clean exit from loop
    sync_once(created_dirs, warned_types)


if __name__ == "__main__":
    main()

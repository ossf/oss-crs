"""Seed ensembler sidecar entry point.

Replaces the exchange sidecar with coverage-aware seed filtering.
Non-seed data types (povs, patches, etc.) use passthrough copy.
Seeds go through libfuzzer -merge=1 when harnesses are available,
with automatic fallback to passthrough when BUILD_OUT_DIR is empty.
"""

from __future__ import annotations

import logging
import signal
import time
from pathlib import Path

from .config import Configuration
from .harness_discovery import discover_harnesses
from .passthrough import (
    sync_non_seed_types,
    sync_seeds_passthrough,
    copy_new_files,
    safe_copy,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [ensembler] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ensembler")


def _drain_new_seeds(pool, exchange_seeds_dir: Path) -> int:
    """Move coverage-gaining seeds from the pool queue to EXCHANGE_DIR/seeds/."""
    from queue import Empty

    count = 0
    while True:
        try:
            harness, seed_paths = pool.new_seeds_queue.get_nowait()
        except Empty:
            break
        for seed_path in seed_paths:
            if not seed_path.exists():
                continue
            dst = exchange_seeds_dir / seed_path.name
            if not dst.exists():
                safe_copy(str(seed_path), dst)
                log.info("seeds/%s (coverage+, harness=%s)", seed_path.name, harness.name)
                count += 1
    return count


def _drain_crashes(pool, exchange_povs_dir: Path) -> int:
    """Move crash-triggering seeds from the pool queue to EXCHANGE_DIR/povs/."""
    from queue import Empty
    import hashlib

    count = 0
    while True:
        try:
            harness, failures = pool.crash_queue.get_nowait()
        except Empty:
            break
        for failure in failures:
            if failure.input_path is None or not failure.input_path.exists():
                continue
            content = failure.input_path.read_bytes()
            name = hashlib.md5(content).hexdigest()
            dst = exchange_povs_dir / name
            if not dst.exists():
                safe_copy(str(failure.input_path), dst)
                log.info(
                    "povs/%s (crash, harness=%s, sanitizer=%s)",
                    name, harness.name, failure.sanitizer,
                )
                count += 1
    return count


def _scan_and_batch_seeds(
    config: Configuration,
    pool,
    harness,
    seen_seeds: set[str],
    batch_dir_counter: list[int],
) -> None:
    """Scan SUBMIT_DIR/*/seeds/ and submit new batches to the pool."""
    import shutil

    batch_size = 32
    pending: list[Path] = []

    for crs_entry in config.submit_root.iterdir():
        if not crs_entry.is_dir():
            continue
        seeds_dir = crs_entry / "seeds"
        if not seeds_dir.is_dir():
            continue
        for f in seeds_dir.iterdir():
            if f.is_symlink() or not f.is_file():
                continue
            key = f"{crs_entry.name}/{f.name}"
            if key in seen_seeds:
                continue
            seen_seeds.add(key)
            pending.append(f)

    # Create batches
    for i in range(0, len(pending), batch_size):
        chunk = pending[i : i + batch_size]
        batch_dir_counter[0] += 1
        batch_dir = config.temp_dir / f"batch_{batch_dir_counter[0]:06d}"
        batch_dir.mkdir(parents=True, exist_ok=True)

        for src in chunk:
            dst = batch_dir / src.name
            try:
                shutil.copy2(src, dst)
            except OSError:
                pass

        if any(batch_dir.iterdir()):
            pool.add_seeds_batch(batch_dir, harness)
        else:
            shutil.rmtree(batch_dir, ignore_errors=True)


def main() -> None:
    config = Configuration.from_env()
    config.temp_dir.mkdir(parents=True, exist_ok=True)

    log.info(
        "ensembler started (mode=%s, workers=%d, poll=%.1fs)",
        config.mode, config.worker_pool_size, config.poll_interval,
    )

    shutdown_requested = False

    def _shutdown(signum, _frame):
        nonlocal shutdown_requested
        log.info("received signal %d, will sync and exit", signum)
        shutdown_requested = True

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    created_dirs: set[str] = set()
    warned_types: set[str] = set()
    seen_seeds: set[str] = set()
    batch_dir_counter = [0]

    pool = None
    harnesses = []
    harness = None  # single-harness mode for now

    while not shutdown_requested:
        try:
            # Always handle non-seed types via passthrough
            sync_non_seed_types(
                config.submit_root, config.exchange_root,
                created_dirs, warned_types,
            )

            # Discover harnesses if not yet done
            if config.mode == "coverage" and not harnesses:
                harnesses = discover_harnesses(config.build_out_dir)
                if harnesses:
                    harness = harnesses[0]  # single-harness per compose stack
                    log.info(
                        "coverage mode active: %d harness(es), using %s",
                        len(harnesses), harness.name,
                    )

            # Handle seeds
            if config.mode == "coverage" and harness is not None:
                # Lazy-init pool
                if pool is None:
                    from .pool import LibfuzzerPool

                    pool_config = Configuration(
                        temp_dir=config.temp_dir / "pool",
                        worker_pool_size=config.worker_pool_size,
                        runner_image=config.runner_image,
                        verbose=config.verbose,
                    )
                    pool = LibfuzzerPool(pool_config, direct=True)

                _scan_and_batch_seeds(
                    config, pool, harness, seen_seeds, batch_dir_counter,
                )

                # Drain results
                exchange_seeds_dir = config.exchange_root / "seeds"
                exchange_seeds_dir.mkdir(parents=True, exist_ok=True)
                exchange_povs_dir = config.exchange_root / "povs"
                exchange_povs_dir.mkdir(parents=True, exist_ok=True)

                _drain_new_seeds(pool, exchange_seeds_dir)
                _drain_crashes(pool, exchange_povs_dir)
            else:
                # Passthrough: no coverage filtering
                sync_seeds_passthrough(
                    config.submit_root, config.exchange_root, created_dirs,
                )

        except Exception:
            log.exception("scan cycle failed, will retry")

        time.sleep(config.poll_interval)

    # Final sync
    log.info("shutting down, final sync...")
    sync_non_seed_types(
        config.submit_root, config.exchange_root,
        created_dirs, warned_types,
    )

    if pool is not None:
        exchange_seeds_dir = config.exchange_root / "seeds"
        exchange_povs_dir = config.exchange_root / "povs"
        _drain_new_seeds(pool, exchange_seeds_dir)
        _drain_crashes(pool, exchange_povs_dir)
    else:
        sync_seeds_passthrough(
            config.submit_root, config.exchange_root, created_dirs,
        )

    log.info("ensembler stopped")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Fuzzer sidecar CRS agent.

This agent demonstrates the fuzzer sidecar pattern:
1. Registers shared directories for corpus and crashes
2. Starts fuzzer via libCRS HTTP API to fuzzer sidecar
3. Polls for crashes and submits them as POVs
4. Stops fuzzer on timeout or when crashes found
"""
import os
import sys
import time
import threading
from pathlib import Path

from libCRS.base import DataType
from libCRS.local import LocalCRSUtils


def main():
    # Get configuration from environment
    harness_name = os.environ.get("OSS_CRS_TARGET_HARNESS", "")
    if not harness_name:
        print("ERROR: OSS_CRS_TARGET_HARNESS not set", file=sys.stderr)
        sys.exit(1)

    # Initialize libCRS
    crs = LocalCRSUtils()

    # Set up directories
    work_dir = Path("/work")
    corpus_dir = work_dir / "corpus"
    crashes_dir = work_dir / "crashes"

    # Register shared directories so fuzzer sidecar can access them
    crs.register_shared_dir(corpus_dir, "corpus")
    crs.register_shared_dir(crashes_dir, "crashes")

    # Register crashes directory for automatic POV submission (runs in background thread)
    submit_thread = threading.Thread(
        target=crs.register_submit_dir,
        args=(DataType.POV, crashes_dir),
        daemon=True,
    )
    submit_thread.start()

    print(f"Starting fuzzer for harness: {harness_name}")
    print(f"Corpus dir: {corpus_dir}")
    print(f"Crashes dir: {crashes_dir}")

    # Wait for fuzzer sidecar to be ready
    import requests
    fuzzer_host = "fuzzer.fuzzer-sidecar-crs"
    print(f"Waiting for fuzzer sidecar at {fuzzer_host}:8080...")

    # Poll until the fuzzer sidecar is ready
    for attempt in range(30):  # Try for up to 30 seconds
        try:
            resp = requests.get(f"http://{fuzzer_host}:8080/health", timeout=2)
            if resp.status_code == 200:
                print(f"Fuzzer sidecar is ready (attempt {attempt + 1})")
                break
        except Exception as e:
            if attempt % 5 == 0:  # Print every 5 attempts
                print(f"Waiting for fuzzer... (attempt {attempt + 1})")
        time.sleep(1)
    else:
        print("ERROR: Fuzzer sidecar did not become ready in time")
        sys.exit(1)

    # Start fuzzer via sidecar
    # The corpus_dir and crashes_dir paths need to be the shared filesystem paths
    shared_dir = Path(os.environ.get("OSS_CRS_SHARED_DIR", "/OSS_CRS_SHARED_DIR"))
    shared_corpus = shared_dir / "corpus"
    shared_crashes = shared_dir / "crashes"

    try:
        handle = crs.start_fuzzer(
            harness_name=harness_name,
            corpus_dir=shared_corpus,
            crashes_dir=shared_crashes,
            fuzzer="fuzzer",  # module name from crs.yaml
            engine="libfuzzer",
            timeout=0,  # No timeout - we'll stop manually
            extra_args=["-max_len=256", "-rss_limit_mb=2560"],
        )
        print(f"Fuzzer started: id={handle.fuzzer_id}, pid={handle.pid}")
    except Exception as e:
        print(f"ERROR: Failed to start fuzzer: {e}", file=sys.stderr)
        sys.exit(1)

    # Poll until we find crashes or hit timeout
    max_runtime = 30  # seconds
    poll_interval = 1  # seconds
    start_time = time.time()

    try:
        while True:
            elapsed = time.time() - start_time
            if elapsed >= max_runtime:
                print(f"Timeout reached after {elapsed:.1f}s")
                break

            try:
                status = crs.fuzzer_status(handle.fuzzer_id, "fuzzer")
                print(
                    f"Status: state={status.state}, "
                    f"runtime={status.runtime_seconds:.1f}s, "
                    f"execs={status.execs}, "
                    f"corpus={status.corpus_size}, "
                    f"crashes={status.crashes_found}"
                )

                if status.state != "running":
                    print(f"Fuzzer stopped with state: {status.state}")
                    break

                if status.crashes_found > 0:
                    print(f"Found {status.crashes_found} crash(es)!")
                    # Give a moment for crash files to be written
                    time.sleep(1)
                    break

            except Exception as e:
                print(f"Warning: Failed to get status: {e}", file=sys.stderr)

            time.sleep(poll_interval)

    finally:
        # Stop the fuzzer
        print("Stopping fuzzer...")
        try:
            result = crs.stop_fuzzer(handle.fuzzer_id, "fuzzer")
            print(
                f"Fuzzer stopped: exit_code={result.exit_code}, "
                f"runtime={result.runtime_seconds:.1f}s, "
                f"corpus={result.corpus_size}, "
                f"crashes={result.crashes_found}"
            )
        except Exception as e:
            print(f"Warning: Failed to stop fuzzer: {e}", file=sys.stderr)

    # List crash files (POVs)
    crash_files = list(crashes_dir.glob("crash-*")) if crashes_dir.exists() else []
    print(f"Crash files found: {len(crash_files)}")
    for f in crash_files:
        print(f"  - {f.name} ({f.stat().st_size} bytes)")

    # Keep running briefly to allow submit daemon to sync crashes
    # The submit helper flushes every batch_time (10s), so wait at least that long
    if crash_files:
        print("Waiting for crash submission...")
        time.sleep(12)  # > batch_time to ensure at least one flush cycle

    print("Agent finished")


if __name__ == "__main__":
    main()

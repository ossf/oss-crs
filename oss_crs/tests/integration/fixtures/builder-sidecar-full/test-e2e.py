#!/usr/bin/env python3
"""E2E sidecar test using the libCRS Python API.

Mirrors test-e2e.sh but exercises the Python interface directly.
Runs inside the patcher container where libCRS is installed.
FAST-FAIL: every step must succeed or the test aborts.
"""

import os
import sys
from pathlib import Path

from libCRS.base import DataType
from libCRS.local import LocalCRSUtils

RESPONSE_DIR = Path("/tmp/response-py")


def log(msg: str):
    print(f"[test-e2e-py] {msg}", flush=True)


def fail(msg: str):
    log(f"FAIL: {msg}")
    sys.exit(1)


def main() -> int:
    crs = LocalCRSUtils()

    harness_name = os.environ.get("OSS_CRS_TARGET_HARNESS", "")
    if not harness_name:
        fail("OSS_CRS_TARGET_HARNESS not set")

    fetch_dir = os.environ.get("OSS_CRS_FETCH_DIR", "")
    if not fetch_dir:
        fail("OSS_CRS_FETCH_DIR not set")

    log(f"OSS_CRS_NAME={os.environ.get('OSS_CRS_NAME', '(unset)')}")
    log(f"BUILDER_MODULE={os.environ.get('BUILDER_MODULE', '(unset)')}")

    # --- Fetch diff from FETCH_DIR ---
    log("Fetching diff from FETCH_DIR...")
    diff_dir = Path("/tmp/fetched-diffs-py")
    diff_dir.mkdir(parents=True, exist_ok=True)
    crs.fetch(DataType.DIFF, diff_dir)
    diffs = sorted(f for f in diff_dir.iterdir() if f.is_file())
    if not diffs:
        fail("No diff files fetched from FETCH_DIR")
    PATCH = diffs[0]
    log(f"Using patch: {PATCH.name}")

    # --- Download coverage build outputs from build-target phase ---
    log("Downloading coverage build outputs from build-target phase...")
    coverage_dir = Path("/tmp/coverage-artifacts-py")

    crs.download_build_output("coverage-build", coverage_dir / "build")
    if not (coverage_dir / "build").exists():
        fail("coverage-build download produced no directory")
    log(f"Coverage build artifacts: {len(list((coverage_dir / 'build').iterdir()))} files")

    crs.download_build_output("coverage-src", coverage_dir / "src")
    if not (coverage_dir / "src").exists():
        fail("coverage-src download produced no directory")
    log(f"Coverage src artifacts: {len(list((coverage_dir / 'src').iterdir()))} files")

    # --- Verify coverage instrumentation on harness binary ---
    harness_bin = coverage_dir / "build" / harness_name
    if not harness_bin.exists():
        fail(f"Harness {harness_name} not found in coverage-build")
    log(f"Verifying coverage instrumentation on {harness_name}...")
    if b"__llvm_profile" not in harness_bin.read_bytes():
        fail(f"{harness_name} missing __llvm_profile symbols — not coverage instrumented")
    log(f"PASS: {harness_name} contains __llvm_profile symbols")

    # --- Apply patch + build (default builder) ---
    log("Applying patch and building via Python API...")
    build_response = RESPONSE_DIR / "build"
    build_rc = crs.apply_patch_build(PATCH, build_response, builder_name="default-build")
    if build_rc != 0:
        fail(f"Build failed with exit code {build_rc}")
    log(f"Build exit code: {build_rc}")

    rid_file = build_response / "rebuild_id"
    if not rid_file.exists():
        fail("No rebuild_id returned from build")
    rebuild_id = int(rid_file.read_text().strip())
    log(f"Rebuild ID: {rebuild_id}")

    # --- Rebuild with coverage builder ---
    log("Rebuilding with coverage builder via Python API...")
    cov_rebuild_response = RESPONSE_DIR / "coverage-rebuild"
    cov_rebuild_rc = crs.apply_patch_build(PATCH, cov_rebuild_response, builder_name="coverage-build")
    if cov_rebuild_rc != 0:
        fail(f"Coverage rebuild failed with exit code {cov_rebuild_rc}")

    cov_rid_file = cov_rebuild_response / "rebuild_id"
    if not cov_rid_file.exists():
        fail("No rebuild_id returned from coverage rebuild")
    cov_rebuild_id = int(cov_rid_file.read_text().strip())
    log(f"Coverage Rebuild ID: {cov_rebuild_id}")

    # --- Download and verify coverage rebuild artifacts ---
    log(f"Downloading coverage rebuild artifacts (rebuild_id={cov_rebuild_id})...")
    cov_rebuild_dir = Path("/tmp/coverage-rebuild-artifacts-py")
    crs.download_build_output("coverage-build", cov_rebuild_dir, rebuild_id=cov_rebuild_id)

    rebuilt_harness = cov_rebuild_dir / harness_name
    if not rebuilt_harness.exists():
        fail(f"Rebuilt harness {harness_name} not found in rebuild artifacts")
    log(f"Verifying coverage instrumentation on rebuilt {harness_name}...")
    if b"__llvm_profile" not in rebuilt_harness.read_bytes():
        fail(f"Rebuilt {harness_name} missing __llvm_profile symbols")
    log(f"PASS: rebuilt {harness_name} contains __llvm_profile symbols")

    # --- Apply patch + test ---
    log("Running test.sh via Python API...")
    test_response = RESPONSE_DIR / "test"
    test_rc = crs.apply_patch_test(PATCH, test_response)
    if test_rc != 0:
        fail(f"Tests returned exit code {test_rc}")
    log(f"Test exit code: {test_rc}")

    # --- Run POV ---
    pov_dir = Path(fetch_dir) / "povs"
    if not pov_dir.is_dir():
        fail(f"POV directory {pov_dir} does not exist")

    pov_files = sorted(f for f in pov_dir.iterdir() if f.is_file())
    if not pov_files:
        fail(f"No POV files found in {pov_dir}")

    for pov_file in pov_files:
        log(f"Running POV {pov_file.name} (harness={harness_name}, rebuild_id={rebuild_id})...")
        pov_response = RESPONSE_DIR / "pov"
        pov_rc = crs.run_pov(pov_file, harness_name, rebuild_id, pov_response)

        log(f"POV exit code: {pov_rc}")
        stderr_file = pov_response / "stderr.log"
        if stderr_file.exists():
            log(f"  stderr: {stderr_file.read_text().strip()[:200]}")

        if pov_rc != 0:
            fail(f"POV {pov_file.name}: runner returned exit code {pov_rc}")
        else:
            log(f"POV {pov_file.name}: no crash — ground-truth patch fixed the vulnerability")

    log("SUCCESS: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

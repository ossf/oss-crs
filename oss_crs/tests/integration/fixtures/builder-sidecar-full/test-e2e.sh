#!/bin/bash
# E2E sidecar test: apply ground-truth patch via libCRS, build, then test.
# Runs inside the patcher container where libCRS is installed.
# FAST-FAIL: every step must succeed or the test aborts.
set -euo pipefail

RESPONSE_DIR="/tmp/response"
HARNESS_NAME="${OSS_CRS_TARGET_HARNESS:-}"

log() { echo "[test-e2e] $*"; }
fail() { log "FAIL: $*"; exit 1; }

# --- Fetch diff from OSS_CRS_FETCH_DIR ---
FETCH_DIR="${OSS_CRS_FETCH_DIR:-}"
[ -n "$FETCH_DIR" ] || fail "OSS_CRS_FETCH_DIR not set"
DIFF_DIR="/tmp/fetched-diffs"
mkdir -p "$DIFF_DIR"
libCRS fetch diff "$DIFF_DIR"
PATCH=$(find "$DIFF_DIR" -type f | head -1)
[ -n "$PATCH" ] || fail "No diff files fetched from FETCH_DIR"
log "Using patch: $PATCH"

# --- Download coverage build outputs from build-target phase ---
log "Downloading coverage build outputs from build-target phase..."
COVERAGE_DIR="/tmp/coverage-artifacts"
mkdir -p "$COVERAGE_DIR"
libCRS download-build-output coverage-build "$COVERAGE_DIR/build"
[ -d "$COVERAGE_DIR/build" ] || fail "coverage-build download produced no directory"
log "Coverage build artifacts: $(ls "$COVERAGE_DIR/build/" | wc -l) files"

libCRS download-build-output coverage-src "$COVERAGE_DIR/src"
[ -d "$COVERAGE_DIR/src" ] || fail "coverage-src download produced no directory"
log "Coverage src artifacts: $(ls "$COVERAGE_DIR/src/" | wc -l) files"

# --- Verify coverage instrumentation on harness binary ---
[ -n "$HARNESS_NAME" ] || fail "OSS_CRS_TARGET_HARNESS not set"
[ -f "$COVERAGE_DIR/build/$HARNESS_NAME" ] || fail "Harness $HARNESS_NAME not found in coverage-build"
log "Verifying coverage instrumentation on $HARNESS_NAME..."
grep -c "__llvm_profile" "$COVERAGE_DIR/build/$HARNESS_NAME" > /dev/null 2>&1 \
    || fail "$HARNESS_NAME missing __llvm_profile symbols — not coverage instrumented"
log "PASS: $HARNESS_NAME contains __llvm_profile symbols"

# --- Apply patch + build (default builder) ---
log "Applying patch and building via libCRS..."
log "libCRS location: $(which libCRS 2>&1)"
log "OSS_CRS_NAME=$OSS_CRS_NAME"
rm -rf "$RESPONSE_DIR/build"
mkdir -p "$RESPONSE_DIR/build"
libCRS apply-patch-build "$PATCH" "$RESPONSE_DIR/build" --builder-name default-build 2>&1
BUILD_RC=$?
[ "$BUILD_RC" -eq 0 ] || fail "Build failed with exit code $BUILD_RC"
log "Build exit code: $BUILD_RC"

[ -f "$RESPONSE_DIR/build/rebuild_id" ] || fail "No rebuild_id returned from build"
REBUILD_ID=$(cat "$RESPONSE_DIR/build/rebuild_id")
log "Rebuild ID: $REBUILD_ID"

# --- Rebuild with coverage builder ---
log "Rebuilding with coverage builder..."
rm -rf "$RESPONSE_DIR/coverage-rebuild"
mkdir -p "$RESPONSE_DIR/coverage-rebuild"
libCRS apply-patch-build "$PATCH" "$RESPONSE_DIR/coverage-rebuild" --builder-name coverage-build 2>&1
COV_REBUILD_RC=$?
[ "$COV_REBUILD_RC" -eq 0 ] || fail "Coverage rebuild failed with exit code $COV_REBUILD_RC"

[ -f "$RESPONSE_DIR/coverage-rebuild/rebuild_id" ] || fail "No rebuild_id returned from coverage rebuild"
COV_REBUILD_ID=$(cat "$RESPONSE_DIR/coverage-rebuild/rebuild_id")
log "Coverage Rebuild ID: $COV_REBUILD_ID"

# --- Download and verify coverage rebuild artifacts ---
log "Downloading coverage rebuild artifacts (rebuild_id=$COV_REBUILD_ID)..."
COV_REBUILD_DIR="/tmp/coverage-rebuild-artifacts"
rm -rf "$COV_REBUILD_DIR"
libCRS download-build-output "coverage-build" "$COV_REBUILD_DIR" --rebuild-id "$COV_REBUILD_ID"
[ -f "$COV_REBUILD_DIR/$HARNESS_NAME" ] || fail "Rebuilt harness $HARNESS_NAME not found in rebuild artifacts"
log "Verifying coverage instrumentation on rebuilt $HARNESS_NAME..."
grep -c "__llvm_profile" "$COV_REBUILD_DIR/$HARNESS_NAME" > /dev/null 2>&1 \
    || fail "Rebuilt $HARNESS_NAME missing __llvm_profile symbols"
log "PASS: rebuilt $HARNESS_NAME contains __llvm_profile symbols"

# --- Apply patch + test ---
log "Running test.sh via libCRS..."
rm -rf "$RESPONSE_DIR/test"
libCRS apply-patch-test "$PATCH" "$RESPONSE_DIR/test"
TEST_RC=$?
[ "$TEST_RC" -eq 0 ] || fail "Tests returned exit code $TEST_RC"
log "Test exit code: $TEST_RC"

# --- Run POV ---
POV_DIR="$FETCH_DIR/povs"
[ -d "$POV_DIR" ] || fail "POV directory $POV_DIR does not exist"

POV_COUNT=0
for POV_FILE in "$POV_DIR"/*; do
    [ -f "$POV_FILE" ] || continue
    POV_COUNT=$((POV_COUNT + 1))
    log "Running POV $(basename "$POV_FILE") (harness=$HARNESS_NAME, rebuild_id=$REBUILD_ID)..."
    rm -rf "$RESPONSE_DIR/pov"
    set +e
    libCRS run-pov "$POV_FILE" "$RESPONSE_DIR/pov" --harness "$HARNESS_NAME" --rebuild-id "$REBUILD_ID"
    POV_RC=$?
    set -e

    log "POV exit code: $POV_RC"
    cat "$RESPONSE_DIR/pov/stderr.log" 2>/dev/null || true

    if [ "$POV_RC" -ne 0 ]; then
        fail "POV $(basename "$POV_FILE"): runner returned exit code $POV_RC"
    else
        log "POV $(basename "$POV_FILE"): no crash — ground-truth patch fixed the vulnerability"
    fi
done
[ "$POV_COUNT" -gt 0 ] || fail "No POV files found in $POV_DIR"

log "SUCCESS: all checks passed"
exit 0

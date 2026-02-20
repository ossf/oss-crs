#!/bin/bash
set -euo pipefail

REQUEST_DIR=${1:-/request}
RESPONSE_DIR=${2:-/response}

# 0. Discover the git repo under $SRC and reset to clean state
REPO_DIR=""
for dir in "$SRC"/*/; do
    if [ -d "$dir/.git" ]; then
        REPO_DIR="$dir"
        break
    fi
done
if [ -z "$REPO_DIR" ]; then
    echo "ERROR: No git repository found under $SRC" >&2
    exit 1
fi

cd "$REPO_DIR"

# Use git.real if available — the replay build system (make_build_replayable.py)
# replaces /usr/bin/git with a wrapper that stubs out 'apply', 'clean', 'reset'.
# We need the real git for checkout and apply to work correctly.
if [ -x /usr/bin/git.real ]; then
    GIT=/usr/bin/git.real
else
    GIT=/usr/bin/git
fi
$GIT config --global --add safe.directory "$REPO_DIR"
$GIT checkout -f
# NOTE: intentionally no 'git clean' here — preserving build artifacts
# (object files, etc.) in the source tree enables incremental builds.

# 1. Apply patch
if [ -f "$REQUEST_DIR/patch.diff" ] && [ -s "$REQUEST_DIR/patch.diff" ]; then
    if ! $GIT apply "$REQUEST_DIR/patch.diff" 2>"$RESPONSE_DIR/patch_error.log"; then
        echo "1" > "$RESPONSE_DIR/build_exit_code"
        cat "$RESPONSE_DIR/patch_error.log" > "$RESPONSE_DIR/build.log"
        exit 1
    fi
fi

# 2. Compile (disable exit-on-error so we capture the exit code)
set +e
compile 2>&1 | tee "$RESPONSE_DIR/build.log"
BUILD_EXIT=${PIPESTATUS[0]}
set -e

echo "$BUILD_EXIT" > "$RESPONSE_DIR/build_exit_code"

exit "$BUILD_EXIT"

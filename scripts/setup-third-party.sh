#!/bin/bash
# One-time setup: fetch required oss-fuzz scripts via sparse checkout.
# Run this after cloning the repo:
#   bash scripts/setup-third-party.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
THIRD_PARTY="$REPO_ROOT/third_party"

OSS_FUZZ_REPO="https://github.com/google/oss-fuzz.git"
OSS_FUZZ_COMMIT="1f5c75e09c7b8b98a0e4f21859602a89d41602c2"
OSS_FUZZ_DIR="$THIRD_PARTY/oss-fuzz"

# Files we need from oss-fuzz (relative to repo root)
OSS_FUZZ_FILES=(
    infra/base-images/base-builder/compile
    infra/base-images/base-builder/replay_build.sh
    infra/base-images/base-builder/make_build_replayable.py
    infra/base-images/base-runner/reproduce
    infra/base-images/base-runner/run_fuzzer
    infra/base-images/base-runner/parse_options.py
)

if [ -d "$OSS_FUZZ_DIR" ] && [ -f "$OSS_FUZZ_DIR/compile" ]; then
    echo "oss-fuzz scripts already present in $OSS_FUZZ_DIR"
    echo "To re-fetch, remove the directory first: rm -rf $OSS_FUZZ_DIR"
    exit 0
fi

echo "Fetching oss-fuzz scripts..."
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

git clone --filter=blob:none --sparse "$OSS_FUZZ_REPO" "$TMPDIR/oss-fuzz"
cd "$TMPDIR/oss-fuzz"
git checkout "$OSS_FUZZ_COMMIT"
git sparse-checkout set infra/base-images/base-builder infra/base-images/base-runner

mkdir -p "$OSS_FUZZ_DIR"
for file in "${OSS_FUZZ_FILES[@]}"; do
    cp "$TMPDIR/oss-fuzz/$file" "$OSS_FUZZ_DIR/$(basename "$file")"
done

echo "Done. oss-fuzz scripts installed to $OSS_FUZZ_DIR:"
ls -1 "$OSS_FUZZ_DIR"

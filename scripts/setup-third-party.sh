#!/bin/bash
# One-time setup: fetch required oss-fuzz scripts via sparse checkout.
# Keeps the original directory structure so LICENSE/NOTICE files are preserved.
#
# Run this after cloning the repo:
#   bash scripts/setup-third-party.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
THIRD_PARTY="$REPO_ROOT/third_party"

OSS_FUZZ_REPO="https://github.com/google/oss-fuzz.git"
OSS_FUZZ_COMMIT="1f5c75e09c7b8b98a0e4f21859602a89d41602c2"
OSS_FUZZ_DIR="$THIRD_PARTY/oss-fuzz"

if [ -d "$OSS_FUZZ_DIR/.git" ]; then
    echo "oss-fuzz already checked out at $OSS_FUZZ_DIR"
    echo "To re-fetch, remove the directory first: rm -rf $OSS_FUZZ_DIR"
    exit 0
fi

echo "Fetching oss-fuzz via sparse checkout..."
mkdir -p "$THIRD_PARTY"
git clone --filter=blob:none --sparse "$OSS_FUZZ_REPO" "$OSS_FUZZ_DIR"
cd "$OSS_FUZZ_DIR"
git checkout "$OSS_FUZZ_COMMIT"
git sparse-checkout set \
    infra/base-images/base-builder \
    infra/base-images/base-runner

echo "Done. oss-fuzz checked out to $OSS_FUZZ_DIR"

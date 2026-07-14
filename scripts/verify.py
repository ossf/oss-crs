#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Run lint, type-check, and unit-test verification."""

import subprocess
import sys

RUFF_PATHS = ["oss_crs", "oss-crs-infra", "libCRS"]
PYRIGHT_PATHS = ["oss_crs/src", "libCRS/libCRS", "oss-crs-infra"]


def run(cmd: list[str]) -> int:
    print(f"\n>>> {' '.join(cmd)}", flush=True)
    return subprocess.call(cmd)


def _run() -> int:
    passed: list[str] = []
    failed: list[str] = []

    steps: list[tuple[str, list[str]]] = [
        ("ruff check", ["ruff", "check", *RUFF_PATHS]),
        ("ruff format", ["ruff", "format", "--check", *RUFF_PATHS]),
        ("pyright", ["pyright", *PYRIGHT_PATHS]),
        ("unit tests", ["pytest", "-m", "not integration", "-v", "oss_crs"]),
        # libCRS runs in its own pytest process: its tests import the `libCRS`
        # package as a top-level module (via libCRS/conftest.py adding it to
        # sys.path), which is incompatible with the `libCRS.libCRS.*` import path
        # the oss_crs tests use, so the two cannot share one interpreter.
        ("libCRS unit tests", ["pytest", "-m", "not integration", "-v", "libCRS/tests"]),
    ]

    for name, cmd in steps:
        if run(cmd) != 0:
            failed.append(name)
        else:
            passed.append(name)

    print("\n--- Summary ---")
    for name in passed:
        print(f"  PASS  {name}")
    for name in failed:
        print(f"  FAIL  {name}")

    if failed:
        return 1

    return 0


def main() -> None:
    sys.exit(_run())

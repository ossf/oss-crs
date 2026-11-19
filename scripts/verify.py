#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Run lint, type-check, and unit-test verification."""

import argparse
import shutil
import subprocess
import sys

RUFF_PATHS = ["oss_crs", "oss-crs-infra", "libCRS"]
PYRIGHT_PATHS = ["oss_crs/src", "libCRS/libCRS", "oss-crs-infra"]

COV_ARGS = [
    "--cov=oss_crs",
    "--cov=libCRS",
    "--cov=oss-crs-infra",
    "--cov-report=term",
    "--cov-report=xml",
]


def resolve_cmd(cmd: list[str]) -> list[str]:
    tool = cmd[0]
    if shutil.which(tool) is None and tool in ("ruff", "pytest", "pyright"):
        return [sys.executable, "-m", tool, *cmd[1:]]
    return cmd


def run(cmd: list[str]) -> int:
    resolved = resolve_cmd(cmd)
    print(f"\n>>> {' '.join(resolved)}", flush=True)
    return subprocess.call(resolved)


def _run(enable_cov: bool = True) -> int:
    passed: list[str] = []
    failed: list[str] = []

    cov_flags = list(COV_ARGS) if enable_cov else []

    steps: list[tuple[str, list[str]]] = [
        ("ruff check", ["ruff", "check", *RUFF_PATHS]),
        ("ruff format", ["ruff", "format", "--check", *RUFF_PATHS]),
        ("pyright", ["pyright", *PYRIGHT_PATHS]),
        (
            "unit tests",
            ["pytest", "-m", "not integration", *cov_flags, "-v", "oss_crs"],
        ),
        # libCRS is a standalone sibling package whose tests import it as
        # `libCRS` (the installed name), while oss_crs's own tests reach it via
        # the repo-root namespace as `libCRS.libCRS`. The two conventions can't
        # share one sys.path, so run libCRS's suite separately with its own
        # root on the path.
        (
            "libCRS unit tests",
            [
                "pytest",
                "-m",
                "not integration",
                "-o",
                "pythonpath=libCRS",
                *cov_flags,
                *(["--cov-append"] if cov_flags else []),
                "-v",
                "libCRS/tests",
            ],
        ),
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-cov",
        action="store_true",
        help="Disable pytest coverage measurement and reporting.",
    )
    args = parser.parse_args()
    sys.exit(_run(enable_cov=not args.no_cov))


if __name__ == "__main__":
    main()

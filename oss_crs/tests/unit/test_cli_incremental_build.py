"""Unit tests for --incremental-build CLI flag parsing on build-target and run subcommands."""
import argparse

import pytest

from oss_crs.src.cli.crs_compose import add_build_target_command, add_run_command


def _make_parser():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    add_build_target_command(sub)
    add_run_command(sub)
    return parser


# Required args for build-target subcommand
_BUILD_TARGET_REQUIRED = [
    "build-target",
    "--compose-file", "c.yaml",
    "--fuzz-proj-path", "/tmp/t",
]

# Required args for run subcommand
_RUN_REQUIRED = [
    "run",
    "--compose-file", "c.yaml",
    "--fuzz-proj-path", "/tmp/t",
    "--target-harness", "h",
]


def test_build_target_incremental_build_flag_present():
    """--incremental-build on build-target sets args.incremental_build == True."""
    parser = _make_parser()
    args = parser.parse_args([*_BUILD_TARGET_REQUIRED, "--incremental-build"])
    assert args.incremental_build is True


def test_build_target_incremental_build_flag_absent():
    """Omitting --incremental-build on build-target defaults to False."""
    parser = _make_parser()
    args = parser.parse_args(_BUILD_TARGET_REQUIRED)
    assert args.incremental_build is False


def test_run_incremental_build_flag_present():
    """--incremental-build on run sets args.incremental_build == True."""
    parser = _make_parser()
    args = parser.parse_args([*_RUN_REQUIRED, "--incremental-build"])
    assert args.incremental_build is True


def test_run_incremental_build_flag_absent():
    """Omitting --incremental-build on run defaults to False."""
    parser = _make_parser()
    args = parser.parse_args(_RUN_REQUIRED)
    assert args.incremental_build is False

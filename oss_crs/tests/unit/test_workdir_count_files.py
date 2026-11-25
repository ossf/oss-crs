# SPDX-License-Identifier: MIT
"""Unit tests for WorkDir.count_data_files in oss_crs.src.workdir."""

import sys
from pathlib import Path
from unittest.mock import MagicMock
import pytest

# Polyfill fcntl on platforms (such as Windows) where it is missing
if "fcntl" not in sys.modules:
    try:
        import fcntl  # noqa: F401
    except ImportError:
        sys.modules["fcntl"] = MagicMock()

from oss_crs.src.workdir import WorkDir


def test_count_data_files_flat(tmp_path: Path):
    d = tmp_path / "data"
    d.mkdir()
    (d / "file1.txt").write_text("a")
    (d / "file2.txt").write_text("b")
    (d / ".hidden").write_text("c")

    assert WorkDir.count_data_files(d, recursive=False) == 2


def test_count_data_files_recursive(tmp_path: Path):
    d = tmp_path / "data"
    sub = d / "subdir"
    sub.mkdir(parents=True)
    (d / "file1.txt").write_text("a")
    (sub / "file2.txt").write_text("b")
    (sub / ".hidden").write_text("c")

    assert WorkDir.count_data_files(d, recursive=True) == 2


def test_count_data_files_ignores_symlinks(tmp_path: Path):
    d = tmp_path / "data"
    d.mkdir()
    (d / "real.txt").write_text("content")

    outside = tmp_path / "outside.txt"
    outside.write_text("outside")

    symlink_file = d / "link.txt"
    try:
        symlink_file.symlink_to(outside)
    except OSError:
        pytest.skip("Symlinks not supported on this platform without privileges")

    assert WorkDir.count_data_files(d, recursive=False) == 1
    assert WorkDir.count_data_files(d, recursive=True) == 1


def test_count_data_files_nonexistent(tmp_path: Path):
    assert WorkDir.count_data_files(tmp_path / "nonexistent") == 0

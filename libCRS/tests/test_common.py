# SPDX-License-Identifier: MIT
"""Unit tests for libCRS common helper utilities."""

import os
import subprocess
from pathlib import Path

import pytest
from libCRS.common import (
    EnvType,
    file_hash,
    get_env,
    get_run_env_type,
    is_data_file,
    rsync_copy,
    rsync_copy_files,
)

from libCRS import common


def test_get_env_present(monkeypatch):
    monkeypatch.setenv("TEST_KEY_123", "sample_val")
    assert get_env("TEST_KEY_123") == "sample_val"


def test_get_env_missing_raises():
    if "NON_EXISTENT_ENV_KEY_XYZ" in os.environ:
        del os.environ["NON_EXISTENT_ENV_KEY_XYZ"]
    with pytest.raises(
        KeyError, match="Environment variable 'NON_EXISTENT_ENV_KEY_XYZ' not found"
    ):
        get_env("NON_EXISTENT_ENV_KEY_XYZ")


def test_get_env_missing_allow_none():
    if "NON_EXISTENT_ENV_KEY_XYZ" in os.environ:
        del os.environ["NON_EXISTENT_ENV_KEY_XYZ"]
    assert get_env("NON_EXISTENT_ENV_KEY_XYZ", allow_none=True) == ""


def test_get_run_env_type(monkeypatch):
    monkeypatch.setattr(common, "_OSS_CRS_RUN_ENV_TYPE", None)
    monkeypatch.setenv("OSS_CRS_RUN_ENV_TYPE", "local")
    env_type = get_run_env_type()
    assert env_type == EnvType.LOCAL
    assert isinstance(env_type, EnvType)

    # Cached value is returned on subsequent calls
    monkeypatch.delenv("OSS_CRS_RUN_ENV_TYPE", raising=False)
    assert get_run_env_type() == EnvType.LOCAL


def test_is_data_file(tmp_path: Path):
    regular_file = tmp_path / "valid_data.txt"
    regular_file.write_text("content")

    dot_file = tmp_path / ".hidden_data"
    dot_file.write_text("hidden")

    directory = tmp_path / "sub_dir"
    directory.mkdir()

    non_existent = tmp_path / "does_not_exist.txt"

    assert is_data_file(regular_file) is True
    assert is_data_file(dot_file) is False
    assert is_data_file(directory) is False
    assert is_data_file(non_existent) is False


def test_file_hash(tmp_path: Path):
    test_file = tmp_path / "hash_sample.bin"
    test_file.write_bytes(b"hello world")

    # md5("hello world") = 5eb63bbbe01eeed093cb22bb8f5acdc3
    expected_hash = "5eb63bbbe01eeed093cb22bb8f5acdc3"
    assert file_hash(test_file) == expected_hash

    empty_file = tmp_path / "empty.bin"
    empty_file.write_bytes(b"")
    # md5("") = d41d8cd98f00b204e9800998ecf8427e
    assert file_hash(empty_file) == "d41d8cd98f00b204e9800998ecf8427e"


def test_rsync_copy_invokes_subprocess(monkeypatch, tmp_path: Path):
    calls = []

    def fake_run(cmd, check=True):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    # File copy
    src_file = tmp_path / "source.txt"
    src_file.write_text("text")
    dst_file = tmp_path / "dest" / "target.txt"

    rsync_copy(src_file, dst_file)
    assert len(calls) == 1
    assert calls[0] == ["rsync", "-a", "--", str(src_file), str(dst_file)]

    # Directory copy
    src_dir = tmp_path / "src_dir"
    src_dir.mkdir()
    dst_dir = tmp_path / "dst_dir"

    rsync_copy(src_dir, dst_dir)
    assert len(calls) == 2
    assert calls[1] == ["rsync", "-a", "--", f"{src_dir}/", f"{dst_dir}/"]


def test_rsync_copy_files_empty_list():
    # Should be a no-op when names list is empty
    rsync_copy_files(Path("any_src"), [], Path("any_dst"))


def test_rsync_copy_files_invokes_subprocess(monkeypatch, tmp_path: Path):
    captured = {}

    def fake_run(cmd, input=None, check=True):
        captured["cmd"] = cmd
        captured["input"] = input
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    src_dir = tmp_path / "src"
    dst_dir = tmp_path / "dst"
    file_names = ["a.txt", "b.bin"]

    rsync_copy_files(src_dir, file_names, dst_dir)

    assert captured["cmd"] == [
        "rsync",
        "-a",
        "--from0",
        "--files-from=-",
        "--",
        f"{src_dir}/",
        f"{dst_dir}/",
    ]
    expected_input = b"a.txt\0b.bin\0"
    assert captured["input"] == expected_input
    assert dst_dir.exists()

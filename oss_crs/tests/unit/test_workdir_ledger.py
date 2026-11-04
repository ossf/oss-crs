# SPDX-License-Identifier: MIT
"""Tests for per-CRS cross-run ledger paths (issue #255)."""

import os
from pathlib import Path

import pytest

from oss_crs.src.env_policy import build_run_service_env
from oss_crs.src.workdir import WorkDir


def _work_dir(tmp_path: Path, compose_hash: str = "abc") -> WorkDir:
    return WorkDir(tmp_path / "crs_compose" / compose_hash)


def _symlinks_supported(tmp_path: Path) -> bool:
    probe = tmp_path / ".probe"
    try:
        probe.symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        return False
    probe.unlink()
    return True


def test_ledger_root_is_outside_compose_hash(tmp_path):
    work_dir = _work_dir(tmp_path)
    assert work_dir.get_ledgers_root() == tmp_path / "ledgers"


def test_ledger_shared_across_compose_files(tmp_path):
    a = _work_dir(tmp_path, "hash-a")
    b = _work_dir(tmp_path, "hash-b")
    assert a.get_crs_ledger_history_dir("crs-x") == b.get_crs_ledger_history_dir(
        "crs-x"
    )


def test_ledger_dir_layout_and_creation(tmp_path):
    work_dir = _work_dir(tmp_path)
    ledger = work_dir.get_crs_ledger_dir("crs-x", "run-1")
    assert ledger == tmp_path / "ledgers" / "crs-x" / "runs" / "run-1"
    assert ledger.is_dir()


def test_ledger_dir_no_create(tmp_path):
    work_dir = _work_dir(tmp_path)
    ledger = work_dir.get_crs_ledger_dir("crs-x", "run-1", create=False)
    assert not ledger.exists()
    assert not work_dir.get_ledgers_root().exists()


def test_ledgers_isolated_per_crs(tmp_path):
    work_dir = _work_dir(tmp_path)
    assert work_dir.get_crs_ledger_dir("crs-a", "r") != work_dir.get_crs_ledger_dir(
        "crs-b", "r"
    )


def test_latest_run_id_is_reserved(tmp_path):
    with pytest.raises(ValueError):
        _work_dir(tmp_path).get_crs_ledger_dir("crs-x", WorkDir.LEDGER_LATEST_LINK)


def test_latest_symlink_tracks_most_recent_run(tmp_path):
    if not _symlinks_supported(tmp_path):
        pytest.skip("symlinks not supported on this filesystem")
    work_dir = _work_dir(tmp_path)
    history = work_dir.get_crs_ledger_history_dir("crs-x")

    assert work_dir.update_crs_ledger_latest("crs-x", "run-1")
    latest = history / WorkDir.LEDGER_LATEST_LINK
    assert os.readlink(latest) == "run-1"

    (history / "run-1" / "note.txt").write_text("kb")
    assert work_dir.update_crs_ledger_latest("crs-x", "run-2")
    assert os.readlink(latest) == "run-2"
    # Previous ledgers are preserved.
    assert (history / "run-1" / "note.txt").read_text() == "kb"
    assert not any(p.name.endswith(".tmp") for p in history.iterdir())


def test_run_env_exposes_ledger_dirs():
    plan = build_run_service_env(
        target_env={
            "name": "proj",
            "repo_path": "/src/proj",
            "engine": "libfuzzer",
            "sanitizer": "address",
            "architecture": "x86_64",
            "language": "c",
        },
        sanitizer="address",
        run_env_type="local",
        crs_name="crs-x",
        module_name="runner",
        run_id="run-1",
        cpuset="0",
        memory_limit="1G",
        module_additional_env={"OSS_CRS_LEDGER_DIR": "/elsewhere"},
        crs_additional_env=None,
        scope="crs-x/runner",
    )
    env = plan.effective_env
    assert env["OSS_CRS_LEDGER_DIR"] == "/OSS_CRS_LEDGER_DIR"
    assert env["OSS_CRS_LEDGER_HISTORY_DIR"] == "/OSS_CRS_LEDGER_HISTORY_DIR"

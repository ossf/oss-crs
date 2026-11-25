# SPDX-License-Identifier: MIT
"""Unit tests for CRS revision provenance recording in run metadata."""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

if "fcntl" not in sys.modules:
    try:
        import fcntl  # noqa: F401
    except ImportError:
        sys.modules["fcntl"] = MagicMock()

from oss_crs.src.config.artifacts import RunMeta
from oss_crs.src.crs import CRS
from oss_crs.src.crs_compose import CRSCompose
from oss_crs.src.workdir import WorkDir


def _init_git_repo(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=path, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True)
    (path / "file.txt").write_text("content")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "--no-gpg-sign", "-m", "initial commit"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def test_crs_get_git_revision_in_git_repo(tmp_path: Path):
    crs_dir = tmp_path / "my-crs"
    expected_sha = _init_git_repo(crs_dir)

    crs = CRS.__new__(CRS)
    crs.crs_path = crs_dir

    rev = crs.get_git_revision()
    assert rev == expected_sha


def test_crs_get_git_revision_in_non_git_dir(tmp_path: Path):
    crs_dir = tmp_path / "not-a-repo"
    crs_dir.mkdir()

    crs = CRS.__new__(CRS)
    crs.crs_path = crs_dir

    assert crs.get_git_revision() is None


def test_collect_run_meta_records_crs_git_commit(tmp_path: Path, monkeypatch):
    run_id = "test-prov-run"
    sanitizer = "address"

    crs_mock = MagicMock()
    crs_mock.name = "crs-test"
    crs_mock.get_git_revision.return_value = "0123456789abcdef0123456789abcdef01234567"

    compose = CRSCompose.__new__(CRSCompose)
    compose.work_dir = WorkDir(tmp_path / "work")
    compose.crs_list = [crs_mock]  # pyright: ignore[reportAttributeAccessIssue]
    compose._read_litellm_spend_summary = lambda *args, **kwargs: {}
    compose._read_sidecar_counts_for_crs = lambda *args, **kwargs: {}

    target_mock = MagicMock()

    meta = compose._collect_run_meta(target_mock, run_id, sanitizer)
    assert (
        meta["crs"]["crs-test"]["git_commit"]
        == "0123456789abcdef0123456789abcdef01234567"
    )

    # Also test RunMeta schema parses git_commit
    run_meta_file = compose.work_dir.get_run_meta_file(run_id, sanitizer)
    run_meta_file.parent.mkdir(parents=True, exist_ok=True)
    run_meta_file.write_text(json.dumps(meta))

    parsed = RunMeta.from_work_dir(compose.work_dir, run_id, sanitizer)
    assert (
        parsed.crs["crs-test"].git_commit == "0123456789abcdef0123456789abcdef01234567"
    )

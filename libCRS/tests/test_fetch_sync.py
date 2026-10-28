# SPDX-License-Identifier: MIT
"""Unit tests for libCRS fetch, directory sync, and directory registration utilities."""

import shutil
from pathlib import Path

import libCRS.infra_client as infra_mod
import libCRS.local as local_mod
import libCRS.sync as sync_mod
import pytest
from libCRS.base import DataType
from libCRS.fetch import FetchHelper
from libCRS.infra_client import InfraClient
from libCRS.local import LocalCRSUtils
from libCRS.sync import DirSyncHelper


@pytest.fixture(autouse=True)
def mock_rsync(monkeypatch):
    """Mock rsync calls for cross-platform test compatibility."""

    def _fake_rsync(src: Path, dst: Path):
        dst.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
        else:
            shutil.copy2(src, dst)

    def _fake_rsync_files(src_dir: Path, names: list[str], dst_dir: Path):
        dst_dir.mkdir(parents=True, exist_ok=True)
        for name in names:
            src_file = src_dir / name
            if src_file.exists():
                shutil.copy2(src_file, dst_dir / name)

    monkeypatch.setattr(sync_mod, "rsync_copy", _fake_rsync)
    monkeypatch.setattr(local_mod, "rsync_copy", _fake_rsync)
    monkeypatch.setattr(infra_mod, "rsync_copy_files", _fake_rsync_files)


def test_fetch_helper_fetch_once(tmp_path, monkeypatch):
    fetch_root = tmp_path / "fetch"
    monkeypatch.setenv("OSS_CRS_FETCH_DIR", str(fetch_root))

    pov_source = fetch_root / "povs"
    pov_source.mkdir(parents=True)
    (pov_source / "test_pov_1").write_text("crash data 1")
    (pov_source / "test_pov_2").write_text("crash data 2")

    infra_client = InfraClient()
    helper = FetchHelper(DataType.POV, infra_client)

    dst = tmp_path / "local_povs"
    fetched = helper.fetch_once(dst)

    assert sorted(fetched) == ["test_pov_1", "test_pov_2"]
    assert (dst / "test_pov_1").read_text() == "crash data 1"
    assert (dst / "test_pov_2").read_text() == "crash data 2"

    # Second fetch returns only new files
    assert helper.fetch_once(dst) == []

    (pov_source / "test_pov_3").write_text("crash data 3")
    assert helper.fetch_once(dst) == ["test_pov_3"]
    assert (dst / "test_pov_3").read_text() == "crash data 3"


def test_fetch_helper_register_dir_loop(tmp_path, monkeypatch):
    fetch_root = tmp_path / "fetch"
    monkeypatch.setenv("OSS_CRS_FETCH_DIR", str(fetch_root))

    patch_source = fetch_root / "patches"
    patch_source.mkdir(parents=True)
    (patch_source / "fix.diff").write_text("+fix")

    infra_client = InfraClient()
    helper = FetchHelper(DataType.PATCH, infra_client)

    dst = tmp_path / "patches"

    sleep_called = []

    def mock_sleep(interval):
        sleep_called.append(interval)
        raise KeyboardInterrupt("Stop polling loop")

    monkeypatch.setattr("libCRS.fetch.time.sleep", mock_sleep)

    with pytest.raises(KeyboardInterrupt):
        helper.register_dir(dst)

    assert len(sleep_called) == 1
    assert (dst / "fix.diff").read_text() == "+fix"


def test_dir_sync_helper_sync_once(tmp_path):
    src = tmp_path / "source"
    dst = tmp_path / "destination"
    src.mkdir()
    (src / "file1.txt").write_text("hello")
    (src / "nested").mkdir()
    (src / "nested" / "file2.txt").write_text("world")

    syncer = DirSyncHelper(src, dst)
    syncer.sync_once()

    assert (dst / "file1.txt").read_text() == "hello"
    assert (dst / "nested" / "file2.txt").read_text() == "world"


def test_dir_sync_helper_register_dir_loop(tmp_path, monkeypatch):
    src = tmp_path / "sync_src"
    dst = tmp_path / "sync_dst"
    src.mkdir()
    (src / "item.txt").write_text("initial")

    syncer = DirSyncHelper(src, dst)

    sleep_called = []

    def mock_sleep(interval):
        sleep_called.append(interval)
        raise KeyboardInterrupt("Stop loop")

    monkeypatch.setattr("libCRS.sync.time.sleep", mock_sleep)

    with pytest.raises(KeyboardInterrupt):
        syncer.register_dir()

    assert len(sleep_called) == 1
    assert (dst / "item.txt").read_text() == "initial"


def test_local_crs_utils_fetch(tmp_path, monkeypatch):
    fetch_root = tmp_path / "fetch"
    monkeypatch.setenv("OSS_CRS_FETCH_DIR", str(fetch_root))

    seeds_source = fetch_root / "seeds"
    seeds_source.mkdir(parents=True)
    (seeds_source / "seed_1").write_text("inputA")

    utils = LocalCRSUtils()
    dst = tmp_path / "downloaded_seeds"
    res = utils.fetch(DataType.SEED, dst)

    assert res == ["seed_1"]
    assert (dst / "seed_1").read_text() == "inputA"


def test_local_crs_utils_register_shared_dir(tmp_path, monkeypatch):
    shared_root = tmp_path / "shared"
    monkeypatch.setenv("OSS_CRS_SHARED_DIR", str(shared_root))

    utils = LocalCRSUtils()
    local_path = tmp_path / "work" / "my_shared"

    utils.register_shared_dir(local_path, "subfolder/resource")

    assert local_path.is_symlink()
    assert local_path.resolve() == (shared_root / "subfolder" / "resource").resolve()

    # Re-registering existing path raises FileExistsError
    with pytest.raises(FileExistsError, match="already exists"):
        utils.register_shared_dir(local_path, "subfolder/resource")


def test_local_crs_utils_register_log_dir(tmp_path, monkeypatch):
    log_root = tmp_path / "logs"
    monkeypatch.setenv("OSS_CRS_LOG_DIR", str(log_root))

    utils = LocalCRSUtils()
    local_path = tmp_path / "work" / "fuzzer_logs"

    utils.register_log_dir(local_path)

    assert local_path.is_symlink()
    assert local_path.resolve() == (log_root / "fuzzer_logs").resolve()

    # Re-registering existing path raises FileExistsError
    with pytest.raises(FileExistsError, match="already exists"):
        utils.register_log_dir(local_path)

# SPDX-License-Identifier: MIT
"""Unit tests for the oss-crs-infra exchange sidecar (oss-crs-infra/exchange/main.py).

Verifies polling, directory validation, filename safety, atomic file copies,
deduplication, and graceful shutdown handling.
"""

import importlib.util
import os
import signal
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
EXCHANGE_DIR = REPO_ROOT / "oss-crs-infra" / "exchange"


@pytest.fixture
def exchange(monkeypatch, tmp_path) -> ModuleType:
    """Load exchange module with isolated root directories."""
    # Ensure os.O_NOFOLLOW exists on all platforms (e.g. Windows)
    if not hasattr(os, "O_NOFOLLOW"):
        monkeypatch.setattr(os, "O_NOFOLLOW", 0, raising=False)

    spec = importlib.util.spec_from_file_location(
        "exchange_main", EXCHANGE_DIR / "main.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    submit_root = tmp_path / "submit"
    exchange_root = tmp_path / "OSS_CRS_EXCHANGE_DIR"
    submit_root.mkdir(parents=True, exist_ok=True)
    exchange_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(module, "SUBMIT_ROOT", submit_root)
    monkeypatch.setattr(module, "EXCHANGE_ROOT", exchange_root)

    return module


def test_is_safe_name(exchange: ModuleType) -> None:
    """Validate filename safety checks."""
    assert exchange._is_safe_name("pov_123.bin")
    assert exchange._is_safe_name("patch_0.diff")
    assert exchange._is_safe_name("seed_abc")

    assert not exchange._is_safe_name("")
    assert not exchange._is_safe_name(".")
    assert not exchange._is_safe_name("..")
    assert not exchange._is_safe_name("../escape.txt")
    assert not exchange._is_safe_name("sub/dir")


def test_sync_once_no_submit_dir(exchange: ModuleType, tmp_path: Path) -> None:
    """When SUBMIT_ROOT is missing, sync_once returns early without errors."""
    nonexistent = tmp_path / "nonexistent_submit"
    exchange.SUBMIT_ROOT = nonexistent

    created: set[str] = set()
    warned: set[str] = set()
    exchange.sync_once(created, warned)

    assert created == set()
    assert warned == set()


def test_sync_once_ignores_non_directory_in_submit(exchange: ModuleType) -> None:
    """Non-directories at the top level of SUBMIT_ROOT are ignored."""
    stray_file = exchange.SUBMIT_ROOT / "stray_file.txt"
    stray_file.write_text("ignore me")

    created: set[str] = set()
    warned: set[str] = set()
    exchange.sync_once(created, warned)

    assert created == set()
    assert not (exchange.EXCHANGE_ROOT / "stray_file.txt").exists()


def test_sync_once_warns_unknown_data_types(exchange: ModuleType) -> None:
    """Unknown data type directories are skipped and recorded in warned_types."""
    crs_dir = exchange.SUBMIT_ROOT / "crs_1" / "unsupported_type"
    crs_dir.mkdir(parents=True)
    (crs_dir / "item.bin").write_text("data")

    created: set[str] = set()
    warned: set[str] = set()
    exchange.sync_once(created, warned)

    assert "unsupported_type" in warned
    assert created == set()
    assert not (exchange.EXCHANGE_ROOT / "unsupported_type").exists()


def test_sync_once_syncs_allowed_files_atomically(exchange: ModuleType) -> None:
    """Allowed data type files are copied to EXCHANGE_ROOT."""
    crs1_povs = exchange.SUBMIT_ROOT / "crs_alpha" / "povs"
    crs1_povs.mkdir(parents=True)
    pov_file = crs1_povs / "pov_1.bin"
    pov_file.write_bytes(b"crash_payload_data")

    crs2_patches = exchange.SUBMIT_ROOT / "crs_beta" / "patches"
    crs2_patches.mkdir(parents=True)
    patch_file = crs2_patches / "fix_0.diff"
    patch_file.write_text("diff --git a/foo.c b/foo.c\n")

    created: set[str] = set()
    warned: set[str] = set()
    exchange.sync_once(created, warned)

    assert created == {"povs", "patches"}
    dest_pov = exchange.EXCHANGE_ROOT / "povs" / "pov_1.bin"
    dest_patch = exchange.EXCHANGE_ROOT / "patches" / "fix_0.diff"

    assert dest_pov.exists()
    assert dest_pov.read_bytes() == b"crash_payload_data"
    assert dest_patch.exists()
    assert dest_patch.read_text() == "diff --git a/foo.c b/foo.c\n"


def test_sync_once_deduplication(exchange: ModuleType) -> None:
    """Files that already exist in EXCHANGE_ROOT are not overwritten."""
    dest_dir = exchange.EXCHANGE_ROOT / "seeds"
    dest_dir.mkdir(parents=True)
    dest_file = dest_dir / "seed_1.bin"
    dest_file.write_bytes(b"initial_seed_version")

    crs_seeds = exchange.SUBMIT_ROOT / "crs_1" / "seeds"
    crs_seeds.mkdir(parents=True)
    (crs_seeds / "seed_1.bin").write_bytes(b"different_content_ignored")

    created: set[str] = set()
    warned: set[str] = set()
    exchange.sync_once(created, warned)

    # Content remains the original
    assert dest_file.read_bytes() == b"initial_seed_version"


def test_sync_once_skips_symlinks(exchange: ModuleType, tmp_path: Path) -> None:
    """Symlinks in submission directories are ignored for security."""
    crs_povs = exchange.SUBMIT_ROOT / "crs_1" / "povs"
    crs_povs.mkdir(parents=True)

    target_file = tmp_path / "secret.txt"
    target_file.write_text("secret_data")

    link_path = crs_povs / "symlink_pov.bin"
    symlink_created = False
    try:
        link_path.symlink_to(target_file)
        symlink_created = True
    except (OSError, NotImplementedError):
        pass

    if not symlink_created:
        # On platforms where creating real symlinks requires elevated rights,
        # test the is_symlink() check using a mock entry.
        mock_entry = MagicMock()
        mock_entry.is_symlink.return_value = True
        mock_entry.name = "mock_symlink.bin"
        with patch("os.scandir") as mock_scandir:
            # Level 1: crs_1 dir
            crs_mock = MagicMock()
            crs_mock.is_dir.return_value = True
            crs_mock.path = str(crs_povs.parent)
            # Level 2: povs dir
            type_mock = MagicMock()
            type_mock.is_dir.return_value = True
            type_mock.name = "povs"
            type_mock.path = str(crs_povs)

            mock_scandir.side_effect = [
                iter([crs_mock]),
                iter([type_mock]),
                iter([mock_entry]),
            ]

            created: set[str] = set()
            warned: set[str] = set()
            exchange.sync_once(created, warned)

            assert not (exchange.EXCHANGE_ROOT / "povs" / "mock_symlink.bin").exists()
            return

    created: set[str] = set()
    warned: set[str] = set()
    exchange.sync_once(created, warned)

    assert not (exchange.EXCHANGE_ROOT / "povs" / "symlink_pov.bin").exists()


def test_sync_once_copy_failure_cleanup(exchange: ModuleType, monkeypatch) -> None:
    """If an error occurs during file copy, temp files are cleaned up gracefully."""
    crs_povs = exchange.SUBMIT_ROOT / "crs_1" / "povs"
    crs_povs.mkdir(parents=True)
    (crs_povs / "bad.bin").write_bytes(b"data")

    # Simulate OSError during os.rename
    def failing_rename(src, dst):
        raise OSError("Permission denied / disk full")

    monkeypatch.setattr(os, "rename", failing_rename)

    created: set[str] = set()
    warned: set[str] = set()
    # Should not raise exception
    exchange.sync_once(created, warned)

    assert not (exchange.EXCHANGE_ROOT / "povs" / "bad.bin").exists()


def test_main_shutdown(exchange: ModuleType, monkeypatch) -> None:
    """main() loop registers signal handlers and performs final sync on exit."""
    signals_registered = {}

    def fake_signal(sig, handler):
        signals_registered[sig] = handler

    monkeypatch.setattr(signal, "signal", fake_signal)

    sync_calls = 0

    def fake_sync(created_dirs, warned_types):
        nonlocal sync_calls
        sync_calls += 1

    monkeypatch.setattr(exchange, "sync_once", fake_sync)

    # Let the first sleep trigger the shutdown handler
    def fake_sleep(duration):
        # Trigger the SIGTERM handler
        handler = signals_registered[signal.SIGTERM]
        handler(signal.SIGTERM, None)

    monkeypatch.setattr("time.sleep", fake_sleep)

    exchange.main()

    assert signal.SIGTERM in signals_registered
    assert signal.SIGINT in signals_registered
    # sync_once called during loop and once more after shutdown
    assert sync_calls >= 2

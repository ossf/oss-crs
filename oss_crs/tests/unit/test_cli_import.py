# SPDX-License-Identifier: MIT
"""Unit tests for importing Docker images and CRS source."""

import json
import shutil
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from oss_crs.src.cli import import_cmd as import_mod
from oss_crs.src.cli.import_cmd import handle_import

_HASH = "deadbeef0000"


@pytest.fixture(autouse=True)
def _stub_rm_with_docker(monkeypatch) -> list[Path]:
    removed: list[Path] = []

    def fake_rm(path: Path) -> None:
        removed.append(Path(path))
        shutil.rmtree(path, ignore_errors=True)

    monkeypatch.setattr(import_mod, "rm_with_docker", fake_rm)
    return removed


def _patch_config(monkeypatch) -> None:
    fake_config = SimpleNamespace(md5_hash=lambda: _HASH)
    monkeypatch.setattr(
        import_mod,
        "CRSComposeConfig",
        SimpleNamespace(from_yaml_file=lambda _path: fake_config),
    )


def _capture_load(monkeypatch) -> list[Path]:
    loaded: list[Path] = []

    def fake_load(staging: Path) -> bool:
        loaded.append(Path(staging))
        return True

    monkeypatch.setattr(import_mod, "_load_images", fake_load)
    return loaded


def _make_bundle(
    path: Path,
    *,
    machine: str = "x86_64",
    images: bool = True,
    crs_sources: tuple[str, ...] = ("crs-foo",),
    with_manifest: bool = True,
) -> None:
    stage = path.parent / f".stage-{path.name}"
    stage.mkdir(parents=True, exist_ok=True)
    manifest = {
        "oss_crs_export_version": 1,
        "platform": machine,
        "images": ["oss-crs-deps:latest", "crs-image:latest"] if images else [],
        "crs_sources": list(crs_sources),
        "compression": "zstd",
    }

    with tarfile.open(path, "w") as tar:
        if with_manifest:
            manifest_file = stage / "export-manifest.json"
            manifest_file.write_text(json.dumps(manifest))
            tar.add(manifest_file, arcname="export-manifest.json")
        if images:
            images_tar = stage / "images.tar.zst"
            images_tar.write_bytes(b"FAKE_DOCKER_SAVE")
            tar.add(images_tar, arcname="images.tar.zst")
        for name in crs_sources:
            src = stage / "crs_src" / name
            src.mkdir(parents=True, exist_ok=True)
            (src / "build.sh").write_text("#!/bin/sh\n")
            tar.add(src, arcname=f"crs_src/{name}")


def _args(bundle: Path, tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        in_path=str(bundle),
        compose_file=tmp_path / "compose.yaml",
        work_dir=tmp_path / "work",
    )


def _crs_src_base(tmp_path: Path) -> Path:
    return tmp_path / "work" / "crs_compose" / "crs_src"


def test_import_restores_images_and_sources(tmp_path: Path, monkeypatch) -> None:
    _patch_config(monkeypatch)
    monkeypatch.setattr(import_mod.platform, "machine", lambda: "x86_64")
    loaded = _capture_load(monkeypatch)
    bundle = tmp_path / "prepared.tar"
    _make_bundle(bundle, crs_sources=("crs-foo", "crs-bar"))

    assert handle_import(_args(bundle, tmp_path)) is True
    assert len(loaded) == 1
    base = _crs_src_base(tmp_path)
    assert (base / "crs-foo" / "build.sh").exists()
    assert (base / "crs-bar" / "build.sh").exists()


def test_import_missing_bundle(tmp_path: Path, monkeypatch) -> None:
    _patch_config(monkeypatch)
    assert handle_import(_args(tmp_path / "missing.tar", tmp_path)) is False


def test_import_missing_manifest(tmp_path: Path, monkeypatch) -> None:
    _patch_config(monkeypatch)
    _capture_load(monkeypatch)
    bundle = tmp_path / "bad.tar"
    _make_bundle(bundle, with_manifest=False)

    assert handle_import(_args(bundle, tmp_path)) is False


def test_import_image_platform_mismatch_errors(tmp_path: Path, monkeypatch) -> None:
    _patch_config(monkeypatch)
    monkeypatch.setattr(import_mod.platform, "machine", lambda: "x86_64")
    _capture_load(monkeypatch)
    bundle = tmp_path / "arm.tar"
    _make_bundle(bundle, machine="aarch64")

    assert handle_import(_args(bundle, tmp_path)) is False
    assert not (_crs_src_base(tmp_path) / "crs-foo").exists()


def test_import_source_only_allows_platform_mismatch(
    tmp_path: Path, monkeypatch
) -> None:
    _patch_config(monkeypatch)
    monkeypatch.setattr(import_mod.platform, "machine", lambda: "x86_64")
    loaded = _capture_load(monkeypatch)
    bundle = tmp_path / "source.tar"
    _make_bundle(bundle, machine="aarch64", images=False)

    assert handle_import(_args(bundle, tmp_path)) is True
    # _load_images is still called, but the bundle has no images.tar.zst,
    # so it should return True without actually loading anything.
    assert len(loaded) == 1
    assert (_crs_src_base(tmp_path) / "crs-foo" / "build.sh").exists()


def test_import_replaces_existing_source_with_docker(
    tmp_path: Path, monkeypatch, _stub_rm_with_docker: list[Path]
) -> None:
    _patch_config(monkeypatch)
    monkeypatch.setattr(import_mod.platform, "machine", lambda: "x86_64")
    _capture_load(monkeypatch)
    bundle = tmp_path / "prepared.tar"
    _make_bundle(bundle)
    source = _crs_src_base(tmp_path) / "crs-foo"
    source.mkdir(parents=True)
    (source / "stale.txt").write_text("old")

    assert handle_import(_args(bundle, tmp_path)) is True
    assert source in _stub_rm_with_docker
    assert not (source / "stale.txt").exists()
    assert (source / "build.sh").exists()


def test_import_load_failure_returns_false(tmp_path: Path, monkeypatch) -> None:
    _patch_config(monkeypatch)
    monkeypatch.setattr(import_mod.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(import_mod, "_load_images", lambda _path: False)
    bundle = tmp_path / "prepared.tar"
    _make_bundle(bundle)

    assert handle_import(_args(bundle, tmp_path)) is False

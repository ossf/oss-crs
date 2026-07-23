# SPDX-License-Identifier: MIT
"""Unit tests for exporting prepared images and CRS source."""

import json
import tarfile
from pathlib import Path
from types import SimpleNamespace

from oss_crs.src.cli import export as export_mod
from oss_crs.src.cli.export import handle_export


def _make_compose(crs_list: list | None = None) -> SimpleNamespace:
    return SimpleNamespace(crs_list=crs_list or [])


def _make_crs(tmp_path: Path, name: str) -> SimpleNamespace:
    crs_path = tmp_path / "crs_src" / name
    crs_path.mkdir(parents=True, exist_ok=True)
    (crs_path / "build.sh").write_text("#!/bin/sh\n")
    return SimpleNamespace(name=name, crs_path=crs_path)


def _members(tar_path: Path) -> set[str]:
    with tarfile.open(tar_path, "r") as tar:
        return {member.name for member in tar.getmembers()}


def test_export_source_without_images(tmp_path: Path, monkeypatch) -> None:
    compose = _make_compose([_make_crs(tmp_path, "crs-codex")])
    monkeypatch.setattr(export_mod, "discover_prepare_images", lambda _c: [])
    out = tmp_path / "prepared.tar"

    assert handle_export(SimpleNamespace(out=str(out)), compose) is True
    assert "images.tar" not in _members(out)
    assert "crs_src/crs-codex/build.sh" in _members(out)

    with tarfile.open(out, "r") as tar:
        manifest = json.loads(tar.extractfile("export-manifest.json").read())
    assert manifest["images"] == []
    assert manifest["crs_sources"] == ["crs-codex"]
    assert "libcrs" not in manifest


def test_export_includes_images_and_source(tmp_path: Path, monkeypatch) -> None:
    compose = _make_compose([_make_crs(tmp_path, "crs-codex")])
    monkeypatch.setattr(
        export_mod,
        "discover_prepare_images",
        lambda _c: ["oss-crs-deps:latest", "crs-image:latest"],
    )

    def fake_stream(tags, dest):
        assert tags == ["oss-crs-deps:latest", "crs-image:latest"]
        Path(dest).write_bytes(b"FAKE_DOCKER_SAVE")
        return True

    monkeypatch.setattr(export_mod, "_stream_images", fake_stream)
    out = tmp_path / "prepared.tar"

    assert handle_export(SimpleNamespace(out=str(out)), compose) is True
    members = _members(out)
    assert "images.tar.zst" in members
    assert "crs_src/crs-codex/build.sh" in members
    assert not any(name.startswith("libcrs/") for name in members)

    with tarfile.open(out, "r") as tar:
        manifest = json.loads(tar.extractfile("export-manifest.json").read())
    assert manifest["images"] == ["oss-crs-deps:latest", "crs-image:latest"]
    assert manifest["crs_sources"] == ["crs-codex"]
    assert manifest["compression"] == "zstd"


def test_export_nothing_to_export(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(export_mod, "discover_prepare_images", lambda _c: [])
    out = tmp_path / "prepared.tar"

    assert handle_export(SimpleNamespace(out=str(out)), _make_compose()) is False
    assert not out.exists()


def test_export_save_failure_returns_false(tmp_path: Path, monkeypatch) -> None:
    compose = _make_compose([_make_crs(tmp_path, "crs-codex")])
    monkeypatch.setattr(export_mod, "discover_prepare_images", lambda _c: ["img:1"])
    monkeypatch.setattr(export_mod, "_stream_images", lambda tags, dest: False)
    out = tmp_path / "prepared.tar"

    assert handle_export(SimpleNamespace(out=str(out)), compose) is False
    assert not out.exists()

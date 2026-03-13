"""Unit tests for libCRS download-source behavior."""

from pathlib import Path

import pytest

from libCRS.libCRS.base import SourceType
from libCRS.libCRS.local import LocalCRSUtils


def test_download_source_requires_env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("OSS_CRS_PROJ_PATH", raising=False)
    utils = LocalCRSUtils()
    with pytest.raises(RuntimeError, match="OSS_CRS_PROJ_PATH"):
        utils.download_source(SourceType.TARGET, tmp_path / "dst")


def test_download_source_requires_existing_source(monkeypatch, tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    monkeypatch.setenv("OSS_CRS_REPO_PATH", str(missing))
    utils = LocalCRSUtils()
    with pytest.raises(RuntimeError, match="does not exist"):
        utils.download_source(SourceType.REPO, tmp_path / "dst")


def test_download_source_requires_available_repo_source(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("OSS_CRS_REPO_PATH", raising=False)
    monkeypatch.delenv("OSS_CRS_BUILD_OUT_DIR", raising=False)
    monkeypatch.setenv("SRC", str(tmp_path / "missing-src-root"))
    utils = LocalCRSUtils()
    with pytest.raises(RuntimeError, match="git repository"):
        utils.download_source(SourceType.REPO, tmp_path / "dst")


def test_download_source_rejects_overlapping_paths(monkeypatch, tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    monkeypatch.setenv("OSS_CRS_PROJ_PATH", str(src))
    utils = LocalCRSUtils()
    with pytest.raises(RuntimeError, match="overlapping paths"):
        utils.download_source(SourceType.TARGET, src / "nested")


def test_download_source_calls_rsync_copy(monkeypatch, tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "file.txt").write_text("ok")
    dst = tmp_path / "dst"
    monkeypatch.setenv("OSS_CRS_REPO_PATH", str(src))

    calls: dict[str, Path] = {}

    def _fake_rsync(s: Path, d: Path) -> None:
        calls["src"] = s
        calls["dst"] = d

    monkeypatch.setattr("libCRS.libCRS.local.rsync_copy", _fake_rsync)

    utils = LocalCRSUtils()
    utils.download_source(SourceType.REPO, dst)

    assert calls["src"] == src.resolve()
    assert calls["dst"] == dst.resolve()


def test_download_source_repo_hint_under_src_uses_src_root(
    monkeypatch, tmp_path: Path
) -> None:
    src_root = tmp_path / "runtime-src"
    repo = src_root / "nested" / "project"
    repo.mkdir(parents=True)
    (src_root / ".git").mkdir()
    dst = tmp_path / "dst"

    monkeypatch.setenv("SRC", str(src_root))
    monkeypatch.setenv("OSS_CRS_REPO_PATH", str(repo))

    calls: dict[str, Path] = {}

    def _fake_rsync(s: Path, d: Path) -> None:
        calls["src"] = s
        calls["dst"] = d

    monkeypatch.setattr("libCRS.libCRS.local.rsync_copy", _fake_rsync)

    utils = LocalCRSUtils()
    resolved = utils.download_source(SourceType.REPO, dst)

    assert calls["src"] == src_root.resolve()
    assert calls["dst"] == dst.resolve()
    assert resolved == (dst / "nested" / "project").resolve()


def test_download_source_repo_hint_under_workspace_without_root_git_returns_nested_path(
    monkeypatch, tmp_path: Path
) -> None:
    src_root = tmp_path / "runtime-src"
    repo = src_root / "mock-c"
    sibling = src_root / "vendored"
    (repo / ".git").mkdir(parents=True)
    sibling.mkdir(parents=True)
    dst = tmp_path / "dst"

    monkeypatch.setenv("SRC", str(src_root))
    monkeypatch.setenv("OSS_CRS_REPO_PATH", str(repo))

    calls: dict[str, Path] = {}

    def _fake_rsync(s: Path, d: Path) -> None:
        calls["src"] = s
        calls["dst"] = d

    monkeypatch.setattr("libCRS.libCRS.local.rsync_copy", _fake_rsync)

    utils = LocalCRSUtils()
    resolved = utils.download_source(SourceType.REPO, dst)

    assert calls["src"] == src_root.resolve()
    assert calls["dst"] == dst.resolve()
    assert resolved == (dst / "mock-c").resolve()


def test_download_source_repo_falls_back_to_runtime_src_when_hint_missing(
    monkeypatch, tmp_path: Path
) -> None:
    src_root = tmp_path / "runtime-src"
    repo = src_root / "nested" / "project"
    (repo / ".git").mkdir(parents=True)
    (repo / "file.txt").write_text("ok")
    dst = tmp_path / "dst"

    monkeypatch.setenv("SRC", str(src_root))
    monkeypatch.setenv("OSS_CRS_REPO_PATH", str(tmp_path / "missing-repo-path"))

    calls: dict[str, Path] = {}

    def _fake_rsync(s: Path, d: Path) -> None:
        calls["src"] = s
        calls["dst"] = d

    monkeypatch.setattr("libCRS.libCRS.local.rsync_copy", _fake_rsync)

    utils = LocalCRSUtils()
    utils.download_source(SourceType.REPO, dst)

    assert calls["src"] == src_root.resolve()
    assert calls["dst"] == dst.resolve()


def test_download_source_repo_falls_back_to_build_output_src_when_hint_missing(
    monkeypatch, tmp_path: Path
) -> None:
    build_out_dir = tmp_path / "build-out"
    repo = build_out_dir / "src" / "nested" / "project"
    (repo / ".git").mkdir(parents=True)
    (repo / "file.txt").write_text("ok")
    dst = tmp_path / "dst"

    monkeypatch.setenv("OSS_CRS_BUILD_OUT_DIR", str(build_out_dir))
    monkeypatch.setenv("OSS_CRS_REPO_PATH", str(tmp_path / "missing-repo-path"))
    monkeypatch.setenv("SRC", str(tmp_path / "missing-src-root"))

    calls: dict[str, Path] = {}

    def _fake_rsync(s: Path, d: Path) -> None:
        calls["src"] = s
        calls["dst"] = d

    monkeypatch.setattr("libCRS.libCRS.local.rsync_copy", _fake_rsync)

    utils = LocalCRSUtils()
    utils.download_source(SourceType.REPO, dst)

    assert calls["src"] == (build_out_dir / "src").resolve()
    assert calls["dst"] == dst.resolve()


def test_download_source_repo_translates_stale_src_hint_to_build_output(
    monkeypatch, tmp_path: Path
) -> None:
    build_out_dir = tmp_path / "build-out"
    repo = build_out_dir / "src" / "mock-c"
    repo.mkdir(parents=True)
    (repo / "mock.c").write_text("ok")
    dst = tmp_path / "dst"

    monkeypatch.setenv("SRC", str(tmp_path / "missing-src-root"))
    monkeypatch.setenv("OSS_CRS_REPO_PATH", "/src/mock-c")
    monkeypatch.setenv("OSS_CRS_BUILD_OUT_DIR", str(build_out_dir))

    calls: dict[str, Path] = {}

    def _fake_rsync(s: Path, d: Path) -> None:
        calls["src"] = s
        calls["dst"] = d

    monkeypatch.setattr("libCRS.libCRS.local.rsync_copy", _fake_rsync)

    utils = LocalCRSUtils()
    resolved = utils.download_source(SourceType.REPO, dst)

    assert calls["src"] == repo.resolve()
    assert calls["dst"] == dst.resolve()
    assert resolved == dst.resolve()


def test_download_source_repo_prefers_runtime_src_over_build_output(
    monkeypatch, tmp_path: Path
) -> None:
    src_root = tmp_path / "runtime-src"
    (src_root / "nested" / "runtime-project" / ".git").mkdir(parents=True)
    build_out_dir = tmp_path / "build-out"
    (build_out_dir / "src" / "nested" / "build-project" / ".git").mkdir(parents=True)
    dst = tmp_path / "dst"

    monkeypatch.setenv("SRC", str(src_root))
    monkeypatch.setenv("OSS_CRS_BUILD_OUT_DIR", str(build_out_dir))
    monkeypatch.setenv("OSS_CRS_REPO_PATH", str(tmp_path / "missing-repo-path"))

    calls: dict[str, Path] = {}

    def _fake_rsync(s: Path, d: Path) -> None:
        calls["src"] = s
        calls["dst"] = d

    monkeypatch.setattr("libCRS.libCRS.local.rsync_copy", _fake_rsync)

    utils = LocalCRSUtils()
    utils.download_source(SourceType.REPO, dst)

    assert calls["src"] == src_root.resolve()
    assert calls["dst"] == dst.resolve()


def test_download_source_repo_prefers_runtime_src_over_stale_src_hint(
    monkeypatch, tmp_path: Path
) -> None:
    src_root = tmp_path / "runtime-src"
    (src_root / "repo" / ".git").mkdir(parents=True)
    build_out_dir = tmp_path / "build-out"
    (build_out_dir / "src" / "mock-c").mkdir(parents=True)
    dst = tmp_path / "dst"

    monkeypatch.setenv("SRC", str(src_root))
    monkeypatch.setenv("OSS_CRS_REPO_PATH", "/src/mock-c")
    monkeypatch.setenv("OSS_CRS_BUILD_OUT_DIR", str(build_out_dir))

    calls: dict[str, Path] = {}

    def _fake_rsync(s: Path, d: Path) -> None:
        calls["src"] = s
        calls["dst"] = d

    monkeypatch.setattr("libCRS.libCRS.local.rsync_copy", _fake_rsync)

    utils = LocalCRSUtils()
    utils.download_source(SourceType.REPO, dst)

    assert calls["src"] == src_root.resolve()
    assert calls["dst"] == dst.resolve()


def test_download_source_repo_translates_stale_hint_using_runtime_src_root(
    monkeypatch, tmp_path: Path
) -> None:
    build_out_dir = tmp_path / "build-out"
    repo = build_out_dir / "src" / "mock-c"
    repo.mkdir(parents=True)
    (repo / "mock.c").write_text("ok")
    dst = tmp_path / "dst"

    monkeypatch.setenv("SRC", "/custom/src")
    monkeypatch.setenv("OSS_CRS_REPO_PATH", "/custom/src/mock-c")
    monkeypatch.setenv("OSS_CRS_BUILD_OUT_DIR", str(build_out_dir))

    calls: dict[str, Path] = {}

    def _fake_rsync(s: Path, d: Path) -> None:
        calls["src"] = s
        calls["dst"] = d

    monkeypatch.setattr("libCRS.libCRS.local.rsync_copy", _fake_rsync)

    utils = LocalCRSUtils()
    utils.download_source(SourceType.REPO, dst)

    assert calls["src"] == repo.resolve()
    assert calls["dst"] == dst.resolve()


def test_download_source_repo_ignores_empty_build_out_dir_env(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SRC", str(tmp_path / "missing-src-root"))
    monkeypatch.setenv("OSS_CRS_REPO_PATH", "/src/mock-c")
    monkeypatch.setenv("OSS_CRS_BUILD_OUT_DIR", "")

    utils = LocalCRSUtils()
    with pytest.raises(RuntimeError, match="does not exist"):
        utils.download_source(SourceType.REPO, tmp_path / "dst")


def test_download_source_repo_skips_empty_runtime_src_and_uses_build_output(
    monkeypatch, tmp_path: Path
) -> None:
    src_root = tmp_path / "runtime-src"
    src_root.mkdir()
    build_out_dir = tmp_path / "build-out"
    (build_out_dir / "src" / "nested" / "build-project" / ".git").mkdir(parents=True)
    dst = tmp_path / "dst"

    monkeypatch.setenv("SRC", str(src_root))
    monkeypatch.setenv("OSS_CRS_BUILD_OUT_DIR", str(build_out_dir))
    monkeypatch.setenv("OSS_CRS_REPO_PATH", str(tmp_path / "missing-repo-path"))

    calls: dict[str, Path] = {}

    def _fake_rsync(s: Path, d: Path) -> None:
        calls["src"] = s
        calls["dst"] = d

    monkeypatch.setattr("libCRS.libCRS.local.rsync_copy", _fake_rsync)

    utils = LocalCRSUtils()
    utils.download_source(SourceType.REPO, dst)

    assert calls["src"] == (build_out_dir / "src").resolve()
    assert calls["dst"] == dst.resolve()


def test_download_source_repo_falls_back_to_first_git_repo_under_build_output_src(
    monkeypatch, tmp_path: Path
) -> None:
    build_out_dir = tmp_path / "build-out"
    repo = build_out_dir / "src" / "nested" / "project"
    (repo / ".git").mkdir(parents=True)
    (repo / "file.txt").write_text("ok")
    dst = tmp_path / "dst"

    monkeypatch.setenv("OSS_CRS_BUILD_OUT_DIR", str(build_out_dir))
    monkeypatch.setenv("OSS_CRS_REPO_PATH", str(tmp_path / "missing-repo-path"))
    monkeypatch.setenv("SRC", str(tmp_path / "missing-src-root"))

    calls: dict[str, Path] = {}

    def _fake_rsync(s: Path, d: Path) -> None:
        calls["src"] = s
        calls["dst"] = d

    monkeypatch.setattr("libCRS.libCRS.local.rsync_copy", _fake_rsync)

    utils = LocalCRSUtils()
    utils.download_source(SourceType.REPO, dst)

    assert calls["src"] == (build_out_dir / "src").resolve()
    assert calls["dst"] == dst.resolve()


def test_download_source_repo_falls_back_to_first_git_repo_under_src(
    monkeypatch, tmp_path: Path
) -> None:
    src_root = tmp_path / "runtime-src"
    repo = src_root / "nested" / "project"
    (repo / ".git").mkdir(parents=True)
    (repo / "file.txt").write_text("ok")
    dst = tmp_path / "dst"

    monkeypatch.setenv("SRC", str(src_root))
    monkeypatch.setenv("OSS_CRS_REPO_PATH", str(tmp_path / "missing-repo-path"))

    calls: dict[str, Path] = {}

    def _fake_rsync(s: Path, d: Path) -> None:
        calls["src"] = s
        calls["dst"] = d

    monkeypatch.setattr("libCRS.libCRS.local.rsync_copy", _fake_rsync)

    utils = LocalCRSUtils()
    utils.download_source(SourceType.REPO, dst)

    assert calls["src"] == src_root.resolve()
    assert calls["dst"] == dst.resolve()


def test_download_source_rejects_unknown_source_type(tmp_path: Path) -> None:
    utils = LocalCRSUtils()
    with pytest.raises(ValueError, match="Unsupported source type"):
        utils.download_source("invalid-type", tmp_path / "dst")  # type: ignore[arg-type]

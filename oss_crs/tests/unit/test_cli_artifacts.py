"""Unit tests for artifact ID resolution helpers."""

from pathlib import Path

from oss_crs.src.utils import normalize_run_id
from oss_crs.src.workdir import WorkDir


def _mkdir(dirpath: Path, name: str) -> None:
    (dirpath / name).mkdir(parents=True, exist_ok=True)


def test_resolve_existing_id_prefers_exact_match(tmp_path: Path) -> None:
    _mkdir(tmp_path, "1771880673bo")
    _mkdir(tmp_path, "foo-123abc")
    assert WorkDir._resolve_existing_id("1771880673bo", tmp_path) == "1771880673bo"


def test_resolve_existing_id_falls_back_to_normalized(tmp_path: Path) -> None:
    normalized = normalize_run_id("my run")
    _mkdir(tmp_path, normalized)
    assert WorkDir._resolve_existing_id("my run", tmp_path) == normalized


def test_resolve_existing_id_returns_none_when_missing(tmp_path: Path) -> None:
    _mkdir(tmp_path, "abc123")
    assert WorkDir._resolve_existing_id("does-not-exist", tmp_path) is None


def test_resolve_existing_id_prefers_exact_when_both_exist(tmp_path: Path) -> None:
    raw = "my-exact-id"
    normalized = normalize_run_id(raw)
    _mkdir(tmp_path, raw)
    _mkdir(tmp_path, normalized)
    assert WorkDir._resolve_existing_id(raw, tmp_path) == raw

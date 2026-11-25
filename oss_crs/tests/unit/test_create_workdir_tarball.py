# SPDX-License-Identifier: MIT
"""Unit tests for scripts/create_workdir_tarball.py (used by CI to archive workdirs)."""

import importlib.util
import sys
import tarfile
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "create_workdir_tarball.py"
_spec = importlib.util.spec_from_file_location("create_workdir_tarball", _SCRIPT)
tarball = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tarball)


def _mkdir(*parts: Path | str) -> Path:
    path = Path(*parts)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _touch(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _names(tar_path: Path) -> list[str]:
    with tarfile.open(tar_path, "r:gz") as tar:
        return sorted(tar.getnames())


# --------------------------------------------------------------------------
# should_include
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/w/address/builds/b1/crs/c/key/BUILD_OUT_DIR/build",
        "/w/address/runs/r1/logs",
        "/w/address/runs/r1/EXCHANGE_DIR",
        "/w/address/runs/r1/crs/c/key/SUBMIT_DIR",
    ],
)
def test_should_include_matches_artifact_dirs(path):
    assert tarball.should_include(Path(path)) is True


@pytest.mark.parametrize(
    "path",
    [
        # build output dir must be under /builds/ and end in BUILD_OUT_DIR/build
        "/w/address/other/BUILD_OUT_DIR/build",
        "/w/address/builds/b1/crs/c/key/BUILD_OUT_DIR/src",
        # SUBMIT_DIR only counts under runs/<id>/crs/<name>/
        "/w/address/runs/r1/SUBMIT_DIR",
        "/w/address/builds/b1/crs/c/key/SUBMIT_DIR",
        # unrelated directories
        "/w/address/runs/r1/SHARED_DIR",
        "/w/address/runs/r1/logs/extra",
        "/w/address/runs/r1/LOG_DIR",
    ],
)
def test_should_include_rejects_other_dirs(path):
    assert tarball.should_include(Path(path)) is False


# --------------------------------------------------------------------------
# find_roots
# --------------------------------------------------------------------------


def test_find_roots_empty_workdir(tmp_path):
    assert tarball.find_roots(tmp_path) == []


def test_find_roots_collects_all_artifact_kinds(tmp_path):
    build = _mkdir(
        tmp_path, "address/builds/b1/crs/c1/key/BUILD_OUT_DIR/build"
    )
    logs = _mkdir(tmp_path, "address/runs/r1/logs")
    exchange = _mkdir(tmp_path, "address/runs/r1/EXCHANGE_DIR")
    submit = _mkdir(tmp_path, "address/runs/r1/crs/c1/key/SUBMIT_DIR")
    # Dirs that must be ignored.
    _mkdir(tmp_path, "address/runs/r1/crs/c1/key/SHARED_DIR")
    _mkdir(tmp_path, "address/runs/r1/crs/c1/key/LOG_DIR")

    roots = tarball.find_roots(tmp_path)

    assert roots == sorted([build, logs, exchange, submit])


def test_find_roots_result_is_sorted(tmp_path):
    for san in ("undefined", "address", "memory"):
        _mkdir(tmp_path, san, "runs/r1/logs")

    roots = tarball.find_roots(tmp_path)

    assert roots == sorted(roots)
    assert len(roots) == 3


def test_find_roots_skips_build_out_dir_src(tmp_path):
    build_out = _mkdir(tmp_path, "address/builds/b1/crs/c1/key/BUILD_OUT_DIR")
    build = _mkdir(build_out, "build")
    src = _mkdir(build_out, "src")
    # A "logs" dir inside src must not be discovered.
    _mkdir(src, "project/logs")

    assert tarball.find_roots(tmp_path) == [build]


def test_find_roots_build_out_dir_without_build_subdir(tmp_path):
    build_out = _mkdir(tmp_path, "address/builds/b1/crs/c1/key/BUILD_OUT_DIR")
    _mkdir(build_out, "other/logs")

    assert tarball.find_roots(tmp_path) == []


def test_find_roots_does_not_descend_into_matched_dirs(tmp_path):
    logs = _mkdir(tmp_path, "address/runs/r1/logs")
    _mkdir(logs, "nested/logs")
    _mkdir(logs, "EXCHANGE_DIR")

    assert tarball.find_roots(tmp_path) == [logs]


def test_find_roots_multiple_builds_and_runs(tmp_path):
    b1 = _mkdir(tmp_path, "address/builds/b1/crs/c1/key/BUILD_OUT_DIR/build")
    b2 = _mkdir(tmp_path, "address/builds/b2/crs/c1/key/BUILD_OUT_DIR/build")
    r1 = _mkdir(tmp_path, "address/runs/r1/EXCHANGE_DIR")
    r2 = _mkdir(tmp_path, "address/runs/r2/EXCHANGE_DIR")

    assert tarball.find_roots(tmp_path) == sorted([b1, b2, r1, r2])


def test_find_roots_ignores_regular_files_named_like_artifacts(tmp_path):
    _touch(tmp_path / "address/runs/r1/logs")  # a file, not a directory

    assert tarball.find_roots(tmp_path) == []


def test_find_roots_missing_dir_returns_empty(tmp_path):
    # os.walk errors are swallowed instead of raised.
    assert tarball.find_roots(tmp_path / "does-not-exist") == []


# --------------------------------------------------------------------------
# write_note_tarball
# --------------------------------------------------------------------------


def test_write_note_tarball_contains_single_readme(tmp_path):
    out = tmp_path / "note.tar.gz"

    tarball.write_note_tarball(out, "nothing to archive")

    assert _names(out) == ["README.txt"]
    with tarfile.open(out, "r:gz") as tar:
        content = tar.extractfile("README.txt").read().decode()
    # write_text() uses the platform newline, so compare line-wise.
    assert content.splitlines() == ["nothing to archive"]


# --------------------------------------------------------------------------
# write_roots_tarball
# --------------------------------------------------------------------------


def test_write_roots_tarball_single_root_uses_its_name(tmp_path):
    root = _mkdir(tmp_path, "work/address/runs/r1/logs")
    _touch(root / "a.log", "hello")
    out = tmp_path / "out.tar.gz"

    tarball.write_roots_tarball(out, [root])

    assert _names(out) == ["logs", "logs/a.log"]


def test_write_roots_tarball_preserves_layout_relative_to_common_parent(tmp_path):
    exchange = _mkdir(tmp_path, "work/address/runs/r1/EXCHANGE_DIR")
    logs = _mkdir(tmp_path, "work/address/runs/r1/logs")
    _touch(exchange / "povs" / "p1", "pov")
    _touch(logs / "run.log", "log")
    out = tmp_path / "out.tar.gz"

    tarball.write_roots_tarball(out, [exchange, logs])

    # common root is runs/r1, so archive paths start at "r1/".
    assert _names(out) == sorted(
        [
            "r1/EXCHANGE_DIR",
            "r1/EXCHANGE_DIR/povs",
            "r1/EXCHANGE_DIR/povs/p1",
            "r1/logs",
            "r1/logs/run.log",
        ]
    )


def test_write_roots_tarball_file_contents_roundtrip(tmp_path):
    root = _mkdir(tmp_path, "work/address/runs/r1/logs")
    _touch(root / "a.log", "payload")
    out = tmp_path / "out.tar.gz"

    tarball.write_roots_tarball(out, [root])

    with tarfile.open(out, "r:gz") as tar:
        assert tar.extractfile("logs/a.log").read() == b"payload"


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------


def _run_main(monkeypatch, *args: str) -> int:
    monkeypatch.setattr(sys, "argv", ["create_workdir_tarball.py", *args])
    return tarball.main()


def test_main_archives_found_roots(monkeypatch, tmp_path, capsys):
    workdir = tmp_path / "work"
    logs = _mkdir(workdir, "address/runs/r1/logs")
    _touch(logs / "a.log")
    out = tmp_path / "artifacts" / "out.tar.gz"

    rc = _run_main(
        monkeypatch,
        "--workdir-root",
        str(workdir),
        "--output",
        str(out),
        "--empty-message",
        "empty",
    )

    assert rc == 0
    assert capsys.readouterr().out.strip() == str(out)
    assert _names(out) == ["logs", "logs/a.log"]


def test_main_writes_note_when_no_roots(monkeypatch, tmp_path):
    workdir = _mkdir(tmp_path, "work")
    out = tmp_path / "out.tar.gz"

    rc = _run_main(
        monkeypatch,
        "--workdir-root",
        str(workdir),
        "--output",
        str(out),
        "--empty-message",
        "no artifacts found",
    )

    assert rc == 0
    assert _names(out) == ["README.txt"]
    with tarfile.open(out, "r:gz") as tar:
        content = tar.extractfile("README.txt").read().decode()
    assert content.splitlines() == ["no artifacts found"]


def test_main_writes_note_when_workdir_missing(monkeypatch, tmp_path):
    out = tmp_path / "out.tar.gz"

    rc = _run_main(
        monkeypatch,
        "--workdir-root",
        str(tmp_path / "missing"),
        "--output",
        str(out),
        "--empty-message",
        "workdir missing",
    )

    assert rc == 0
    assert _names(out) == ["README.txt"]


def test_main_creates_output_parent_dirs(monkeypatch, tmp_path):
    out = tmp_path / "a" / "b" / "c" / "out.tar.gz"

    _run_main(
        monkeypatch,
        "--workdir-root",
        str(tmp_path / "missing"),
        "--output",
        str(out),
        "--empty-message",
        "m",
    )

    assert out.is_file()


@pytest.mark.parametrize(
    "missing",
    ["--output", "--empty-message"],
)
def test_main_requires_output_and_empty_message(monkeypatch, tmp_path, missing):
    args = {
        "--output": str(tmp_path / "out.tar.gz"),
        "--empty-message": "m",
    }
    del args[missing]
    argv = [item for pair in args.items() for item in pair]

    with pytest.raises(SystemExit) as exc:
        _run_main(monkeypatch, *argv)

    assert exc.value.code == 2

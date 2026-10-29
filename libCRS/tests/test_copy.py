# SPDX-License-Identifier: MIT
import os
import shutil
import stat
import subprocess

import pytest

import libCRS.common as common
from libCRS.common import copy_files, copy_path


def _snapshot(root):
    """Map each relative path under root to (kind, mode, mtime_ns, payload)."""
    result = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root)
            st = os.lstat(path)
            if stat.S_ISLNK(st.st_mode):
                result[rel] = ("link", None, None, os.readlink(path))
            elif stat.S_ISDIR(st.st_mode):
                result[rel] = ("dir", stat.S_IMODE(st.st_mode), None, None)
            else:
                with open(path, "rb") as f:
                    payload = f.read()
                result[rel] = (
                    "file",
                    stat.S_IMODE(st.st_mode),
                    st.st_mtime_ns,
                    payload,
                )
    return result


def _make_tree(root):
    (root / "sub" / "deeper").mkdir(parents=True)
    (root / "top.txt").write_text("top")
    (root / "sub" / "a.bin").write_bytes(b"\x00\x01binary")
    (root / "sub" / "deeper" / "b.txt").write_text("deep")
    script = root / "run.sh"
    script.write_text("#!/bin/sh\n")
    script.chmod(0o755)
    (root / "link").symlink_to("top.txt")
    (root / "sub" / ".git").mkdir()
    (root / "sub" / ".git" / "HEAD").write_text("ref")
    os.utime(root / "top.txt", ns=(1_000_000_000, 1_234_567_891))


def test_copy_file_preserves_content_mode_and_mtime(tmp_path):
    src = tmp_path / "src.sh"
    src.write_text("echo hi")
    src.chmod(0o750)
    os.utime(src, ns=(5_000_000_000, 6_000_000_123))

    copy_path(src, tmp_path / "out" / "dst.sh")

    dst = tmp_path / "out" / "dst.sh"
    assert dst.read_text() == "echo hi"
    assert stat.S_IMODE(dst.stat().st_mode) == 0o750
    assert dst.stat().st_mtime_ns == 6_000_000_123


def test_copy_file_into_existing_directory(tmp_path):
    src = tmp_path / "pov"
    src.write_text("crash")
    dst_dir = tmp_path / "dst"
    dst_dir.mkdir()

    copy_path(src, dst_dir)

    assert (dst_dir / "pov").read_text() == "crash"


def test_copy_file_into_directory_reached_through_symlink(tmp_path):
    src = tmp_path / "pov"
    src.write_text("crash")
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)

    copy_path(src, link)

    assert link.is_symlink()
    assert (real / "pov").read_text() == "crash"


def test_copy_dir_merges_and_keeps_extra_dst_files(tmp_path):
    src = tmp_path / "src"
    _make_tree(src)
    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / "keep.txt").write_text("mine")

    copy_path(src, dst)

    snap = _snapshot(dst)
    assert snap.pop("keep.txt")[3] == b"mine"
    assert snap == _snapshot(src)


def test_copy_dir_follows_top_level_dst_symlink(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "f").write_text("x")
    real = tmp_path / "shared"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)

    copy_path(src, link)

    assert link.is_symlink()
    assert (real / "f").read_text() == "x"


def test_copy_dir_replaces_nested_symlink_with_directory(tmp_path):
    src = tmp_path / "src"
    (src / "d").mkdir(parents=True)
    (src / "d" / "f").write_text("x")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / "d").symlink_to(elsewhere)

    copy_path(src, dst)

    assert not (dst / "d").is_symlink()
    assert (dst / "d" / "f").read_text() == "x"
    assert list(elsewhere.iterdir()) == []


def test_copy_skips_unchanged_files_and_recopies_changed_ones(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "same").write_text("same")
    (src / "changed").write_text("v1")
    dst = tmp_path / "dst"
    copy_path(src, dst)

    copied = []
    real_copyfile = shutil.copyfile

    def counting_copyfile(a, b, *args, **kwargs):
        copied.append(os.path.basename(a))
        return real_copyfile(a, b, *args, **kwargs)

    monkeypatch.setattr(common.shutil, "copyfile", counting_copyfile)
    (src / "changed").write_text("v2")
    os.utime(src / "changed", ns=(1, 2_000_000_000))

    copy_path(src, dst)

    assert copied == ["changed"]
    assert (dst / "changed").read_text() == "v2"


def test_copy_leaves_no_temp_files_and_keeps_dst_on_failure(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.write_text("new")
    dst = tmp_path / "dst"
    dst.write_text("old")
    os.utime(src, ns=(1, 3_000_000_000))

    def failing_copyfile(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(common.shutil, "copyfile", failing_copyfile)

    with pytest.raises(OSError, match="disk full"):
        copy_path(src, dst)

    assert dst.read_text() == "old"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["dst", "src"]


def test_copy_exclude_applies_at_every_depth(tmp_path):
    src = tmp_path / "src"
    _make_tree(src)
    (src / ".git").mkdir()
    (src / ".git" / "config").write_text("cfg")
    dst = tmp_path / "dst"

    copy_path(src, dst, exclude=frozenset({".git"}))

    assert not (dst / ".git").exists()
    assert not (dst / "sub" / ".git").exists()
    assert (dst / "sub" / "deeper" / "b.txt").read_text() == "deep"


def test_copy_delete_removes_stale_entries_but_keeps_excluded(tmp_path):
    src = tmp_path / "src"
    (src / "kept").mkdir(parents=True)
    (src / "kept" / "f").write_text("f")
    (src / "was_dir").write_text("now a file")
    (src / "was_file").mkdir()
    (src / "was_file" / "inner").write_text("inner")
    dst = tmp_path / "dst"
    (dst / "kept").mkdir(parents=True)
    (dst / "kept" / "stale").write_text("stale")
    (dst / "gone_dir" / "x").mkdir(parents=True)
    (dst / "gone_file").write_text("gone")
    (dst / "was_dir").mkdir()
    (dst / "was_dir" / "old").write_text("old")
    (dst / "was_file").write_text("old")
    (dst / ".git").mkdir()
    (dst / ".git" / "HEAD").write_text("ref")

    copy_path(src, dst, exclude=frozenset({".git"}), delete=True)

    assert sorted(_snapshot(dst)) == [
        ".git",
        os.path.join(".git", "HEAD"),
        "kept",
        os.path.join("kept", "f"),
        "was_dir",
        "was_file",
        os.path.join("was_file", "inner"),
    ]
    assert (dst / "was_dir").read_text() == "now a file"


def test_copy_skips_special_files(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "data").write_text("d")
    os.mkfifo(src / "pipe")
    dst = tmp_path / "dst"

    copy_path(src, dst)

    assert sorted(p.name for p in dst.iterdir()) == ["data"]


def test_copy_files_copies_only_named_children(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    for name in ("a", "b", "c"):
        (src / name).write_text(name)
    dst = tmp_path / "dst"

    copy_files(src, ["a", "c"], dst)

    assert sorted(p.name for p in dst.iterdir()) == ["a", "c"]
    assert (dst / "c").read_text() == "c"


def test_copy_files_with_no_names_is_a_noop(tmp_path):
    copy_files(tmp_path, [], tmp_path / "dst")

    assert not (tmp_path / "dst").exists()


@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync not installed")
@pytest.mark.parametrize("delete", [False, True])
def test_copy_path_matches_rsync(tmp_path, delete):
    src = tmp_path / "src"
    _make_tree(src)
    expected = tmp_path / "rsync-dst"
    actual = tmp_path / "copy-dst"
    for dst in (expected, actual):
        dst.mkdir()
        (dst / "extra.txt").write_text("extra")
        (dst / "sub").mkdir()
        (dst / "sub" / "stale.txt").write_text("stale")
        (dst / ".git").mkdir()

    cmd = ["rsync", "-a", "--exclude=.git"]
    if delete:
        cmd.append("--delete")
    subprocess.run([*cmd, f"{src}/", f"{expected}/"], check=True)
    copy_path(src, actual, exclude=frozenset({".git"}), delete=delete)

    # Compare mtimes to the second: rsync protocol < 31 (e.g. macOS openrsync)
    # does not carry nanoseconds, while GNU rsync 3.x and copy_path do.
    def _to_seconds(snap):
        return {
            rel: (kind, mode, None if mtime is None else mtime // 10**9, payload)
            for rel, (kind, mode, mtime, payload) in snap.items()
        }

    assert _to_seconds(_snapshot(actual)) == _to_seconds(_snapshot(expected))

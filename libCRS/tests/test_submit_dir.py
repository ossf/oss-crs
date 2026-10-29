# SPDX-License-Identifier: MIT
import hashlib
import os

import pytest

import libCRS.submit as submit_mod
from libCRS.submit import SubmitHelper


def _submitted(submit_dir):
    if not submit_dir.exists():
        return set()
    return {p.name for p in submit_dir.iterdir()}


def _name_for(content: bytes, suffix: str = "") -> str:
    return hashlib.md5(content).hexdigest() + suffix


def test_poll_dir_submits_data_files_and_skips_dotfiles_and_dirs(tmp_path):
    watch = tmp_path / "watch"
    watch.mkdir()
    (watch / "pov.bin").write_bytes(b"crash")
    (watch / ".partial").write_bytes(b"hidden")
    (watch / "subdir").mkdir()
    (watch / "subdir" / "nested").write_bytes(b"nested")
    submit_dir = tmp_path / "submit"
    helper = SubmitHelper(submit_dir)

    helper._poll_dir(watch, {}, batch_time=0, batch_size=100)

    assert _submitted(submit_dir) == {_name_for(b"crash", ".bin")}


def test_poll_dir_picks_up_files_added_between_polls(tmp_path):
    watch = tmp_path / "watch"
    watch.mkdir()
    submit_dir = tmp_path / "submit"
    helper = SubmitHelper(submit_dir)

    seen = helper._poll_dir(watch, {}, batch_time=0, batch_size=100)
    assert _submitted(submit_dir) == set()

    (watch / "seed").write_bytes(b"seed-1")
    helper._poll_dir(watch, seen, batch_time=0, batch_size=100)

    assert _submitted(submit_dir) == {_name_for(b"seed-1")}


def test_poll_dir_resubmits_file_once_its_contents_change(tmp_path):
    watch = tmp_path / "watch"
    watch.mkdir()
    target = watch / "pov"
    # An empty file (e.g. just created, not yet written) is skipped.
    target.write_bytes(b"")
    submit_dir = tmp_path / "submit"
    helper = SubmitHelper(submit_dir)

    seen = helper._poll_dir(watch, {}, batch_time=0, batch_size=100)
    assert _submitted(submit_dir) == set()

    target.write_bytes(b"full contents")
    os.utime(target, ns=(1, 1))
    helper._poll_dir(watch, seen, batch_time=0, batch_size=100)

    assert _submitted(submit_dir) == {_name_for(b"full contents")}


def test_poll_dir_does_not_rehash_unchanged_files(tmp_path, monkeypatch):
    watch = tmp_path / "watch"
    watch.mkdir()
    (watch / "a").write_bytes(b"a")
    (watch / "b").write_bytes(b"b")
    helper = SubmitHelper(tmp_path / "submit")
    hashed = []
    real_file_hash = submit_mod.file_hash

    def counting_file_hash(path):
        hashed.append(path.name)
        return real_file_hash(path)

    monkeypatch.setattr(submit_mod, "file_hash", counting_file_hash)

    seen = helper._poll_dir(watch, {}, batch_time=0, batch_size=100)
    helper._poll_dir(watch, seen, batch_time=0, batch_size=100)

    assert sorted(hashed) == ["a", "b"]


def test_poll_dir_waits_for_batch_size_before_flushing(tmp_path):
    watch = tmp_path / "watch"
    watch.mkdir()
    submit_dir = tmp_path / "submit"
    helper = SubmitHelper(submit_dir)

    (watch / "one").write_bytes(b"1")
    seen = helper._poll_dir(watch, {}, batch_time=3600, batch_size=2)
    assert _submitted(submit_dir) == set()

    (watch / "two").write_bytes(b"2")
    helper._poll_dir(watch, seen, batch_time=3600, batch_size=2)

    assert _submitted(submit_dir) == {_name_for(b"1"), _name_for(b"2")}


def test_poll_dir_tolerates_missing_directory(tmp_path):
    helper = SubmitHelper(tmp_path / "submit")
    seen = {tmp_path / "x": (1, 1)}

    assert helper._poll_dir(tmp_path / "gone", seen, batch_time=0, batch_size=1) == seen


class _StopLoop(Exception):
    pass


def test_register_dir_polls_until_stopped_then_does_final_poll(tmp_path, monkeypatch):
    watch = tmp_path / "watch"
    watch.mkdir()
    (watch / "existing").write_bytes(b"existing")
    submit_dir = tmp_path / "submit"
    helper = SubmitHelper(submit_dir)
    sleeps = []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 1:
            (watch / "added").write_bytes(b"added")
            return
        # Written after the last loop poll: only the final poll can see it.
        (watch / "late").write_bytes(b"late")
        raise _StopLoop

    monkeypatch.setattr(submit_mod.time, "sleep", fake_sleep)

    with pytest.raises(_StopLoop):
        helper.register_dir(watch, batch_time=0, batch_size=100, poll_interval=7)

    assert sleeps == [7, 7]
    assert _submitted(submit_dir) == {
        _name_for(b"existing"),
        _name_for(b"added"),
        _name_for(b"late"),
    }


def test_register_dir_rejects_second_registration(tmp_path):
    helper = SubmitHelper(tmp_path / "submit")
    helper.registered_dir = tmp_path

    with pytest.raises(RuntimeError, match="already registered"):
        helper.register_dir(tmp_path, batch_time=10, batch_size=100)

# SPDX-License-Identifier: MIT
import tarfile

import pytest

from libCRS.base import DataType
from libCRS.local import LocalCRSUtils
from libCRS.submit import submit_report


def _members(tarball):
    with tarfile.open(tarball, "r:gz") as tar:
        return sorted(m.name for m in tar.getmembers())


def test_file_tarball_named_after_stem(tmp_path):
    src = tmp_path / "foo.md"
    src.write_text("hello")
    reports = tmp_path / "reports"

    dst = submit_report(src, reports)

    assert dst == reports / "foo.tar.gz"
    assert _members(dst) == ["foo.md"]


def test_file_collision_gets_counter_suffix(tmp_path):
    src = tmp_path / "foo.md"
    src.write_text("hello")
    reports = tmp_path / "reports"

    first = submit_report(src, reports)
    second = submit_report(src, reports)
    third = submit_report(src, reports)

    assert first == reports / "foo.tar.gz"
    assert second == reports / "foo_1.tar.gz"
    assert third == reports / "foo_2.tar.gz"
    assert all(p.exists() for p in (first, second, third))


def test_directory_tarball_strips_top_level_and_preserves_nesting(tmp_path):
    src = tmp_path / "run-1"
    (src / "sub").mkdir(parents=True)
    (src / "a.txt").write_text("a")
    (src / "sub" / "b.txt").write_text("b")
    reports = tmp_path / "reports"

    dst = submit_report(src, reports)

    assert dst == reports / "run-1.tar.gz"
    # Contents sit at the tarball root (no leading "run-1/"), nesting kept.
    assert _members(dst) == ["a.txt", "sub", "sub/b.txt"]


def test_directory_collision_gets_counter_suffix(tmp_path):
    src = tmp_path / "run-1"
    src.mkdir()
    (src / "a.txt").write_text("a")
    reports = tmp_path / "reports"

    first = submit_report(src, reports)
    second = submit_report(src, reports)

    assert first == reports / "run-1.tar.gz"
    assert second == reports / "run-1_1.tar.gz"


def test_missing_path_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        submit_report(tmp_path / "nope", tmp_path / "reports")


def test_submit_report_routes_through_local_crs_utils(tmp_path, monkeypatch):
    submit_dir = tmp_path / "submit"
    monkeypatch.setenv("OSS_CRS_SUBMIT_DIR", str(submit_dir))
    src = tmp_path / "audit.json"
    src.write_text("{}")

    LocalCRSUtils().submit(DataType.REPORT, src)

    assert (submit_dir / "reports" / "audit.tar.gz").exists()


def test_register_submit_dir_rejects_report(tmp_path):
    with pytest.raises(ValueError, match="not supported for reports"):
        LocalCRSUtils().register_submit_dir(DataType.REPORT, tmp_path / "reports")

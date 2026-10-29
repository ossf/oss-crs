# SPDX-License-Identifier: MIT
from libCRS.infra_client import InfraClient
import libCRS.infra_client as infra_client


def test_fetch_new_copies_new_files_into_dst(monkeypatch, tmp_path):
    fetch_dir = tmp_path / "fetch"
    type_dir = fetch_dir / "pov"
    dst = tmp_path / "dst"
    type_dir.mkdir(parents=True)
    dst.mkdir()

    (type_dir / "one").write_text("1")
    (type_dir / "two").write_text("2")

    monkeypatch.setenv("OSS_CRS_FETCH_DIR", str(fetch_dir))

    assert sorted(InfraClient().fetch_new("pov", dst)) == ["one", "two"]
    assert sorted(p.name for p in dst.iterdir()) == ["one", "two"]
    assert (dst / "one").read_text() == "1"
    assert (dst / "two").read_text() == "2"


def test_fetch_new_batches_only_new_regular_data_files(monkeypatch, tmp_path):
    fetch_dir = tmp_path / "fetch"
    type_dir = fetch_dir / "pov"
    dst = tmp_path / "dst"
    type_dir.mkdir(parents=True)
    dst.mkdir()

    (type_dir / "new-file").write_text("new")
    (type_dir / "already-there").write_text("source")
    (type_dir / ".hidden").write_text("hidden")
    (type_dir / "directory").mkdir()
    (dst / "already-there").write_text("dst")

    copies = []

    def record_batch(src_dir, names, dst_dir):
        copies.append((src_dir, names, dst_dir))

    monkeypatch.setenv("OSS_CRS_FETCH_DIR", str(fetch_dir))
    monkeypatch.setattr(infra_client, "copy_files", record_batch)

    assert InfraClient().fetch_new("pov", dst) == ["new-file"]
    assert copies == [(type_dir, ["new-file"], dst)]

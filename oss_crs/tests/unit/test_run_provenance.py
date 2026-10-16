# SPDX-License-Identifier: MIT
"""run() writes run-info.json and links the run under <work_dir>/index."""

import json
from pathlib import Path

import oss_crs.src.crs_compose as crs_compose_module
from oss_crs.src.crs_compose import CRSCompose
from oss_crs.src.target import Target
from oss_crs.src.workdir import WorkDir

COMPOSE_HASH = "0123456789ab"


def _make_run_compose(tmp_path: Path) -> CRSCompose:
    compose = CRSCompose.__new__(CRSCompose)
    compose.crs_list = []
    compose.work_dir = WorkDir(tmp_path / "work" / "crs_compose" / COMPOSE_HASH)
    compose.compose_file = tmp_path / "compose.yaml"
    return compose


def _stub_run(compose: CRSCompose, target: Target, monkeypatch, captured: dict) -> None:
    monkeypatch.setattr(
        compose,
        "_resolve_target_build_options",
        lambda target, sanitizer=None: ("address", None, None),
    )
    monkeypatch.setattr(
        "oss_crs.src.crs_compose.check_cgroup_parent_available",
        lambda: (False, None),
    )
    monkeypatch.setattr(
        compose, "_CRSCompose__validate_before_run", lambda *args, **kwargs: True
    )
    monkeypatch.setattr(
        compose, "_CRSCompose__check_target_built", lambda *args, **kwargs: True
    )
    monkeypatch.setattr(target, "init_repo", lambda: True)

    def _capture_run(*args, **kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(compose, "_CRSCompose__run", _capture_run)


def _single_link(group_dir: Path) -> Path:
    entries = list(group_dir.iterdir())
    assert len(entries) == 1, entries
    return entries[0]


def test_run_writes_run_info_and_index_links(tmp_path: Path, monkeypatch) -> None:
    compose = _make_run_compose(tmp_path)
    (tmp_path / "proj").mkdir()
    target = Target(tmp_path / "work", tmp_path / "proj", None, "fuzz_target")
    captured: dict = {}
    _stub_run(compose, target, monkeypatch, captured)

    assert compose.run(target, run_id="exp 1", build_id="b", sanitizer="address")

    run_id = captured["run_id"]
    run_dir = compose.work_dir.get_run_dir(run_id, "address")
    info = json.loads((run_dir / "run-info.json").read_text())
    assert info["run_id"] == run_id
    assert info["build_id"] == captured["build_id"]
    assert info["compose_hash"] == COMPOSE_HASH
    assert info["compose_file"] == str((tmp_path / "compose.yaml").resolve())
    assert info["harness"] == "fuzz_target"
    assert info["sanitizer"] == "address"
    assert info["crs_names"] == []
    assert info["target"]["name"] == "proj"
    assert info["target"]["key"] == target.get_workdir_target_key()
    assert info["target"]["source_only"] is False
    assert info["started_at_epoch"] > 0

    link = _single_link(tmp_path / "work" / "index" / "by-target" / "proj")
    assert link.name.endswith(f"_{run_id}")
    assert link.is_symlink()
    assert link.resolve() == run_dir.resolve()


def test_run_source_only_records_null_build_id_and_harness(
    tmp_path: Path, monkeypatch
) -> None:
    compose = _make_run_compose(tmp_path)
    source = tmp_path / "my-source"
    source.mkdir()
    target = Target(tmp_path / "work", source, source, None, source_only=True)
    captured: dict = {}
    _stub_run(compose, target, monkeypatch, captured)

    assert compose.run(target, run_id="audit", sanitizer="address")

    run_id = captured["run_id"]
    run_dir = compose.work_dir.get_run_dir(run_id, "address")
    assert not (run_dir / "BUILD_ID").exists()
    info = json.loads((run_dir / "run-info.json").read_text())
    assert info["build_id"] is None
    assert info["harness"] is None
    assert info["target"]["source_only"] is True
    assert info["target"]["name"] == "my-source"

    link = _single_link(tmp_path / "work" / "index" / "by-target" / "my-source")
    assert link.resolve() == run_dir.resolve()


def test_run_index_failure_does_not_fail_run(tmp_path: Path, monkeypatch) -> None:
    compose = _make_run_compose(tmp_path)
    (tmp_path / "proj").mkdir()
    target = Target(tmp_path / "work", tmp_path / "proj", None, "fuzz_target")
    captured: dict = {}
    _stub_run(compose, target, monkeypatch, captured)
    notes: list[str] = []
    monkeypatch.setattr(crs_compose_module, "log_dim", notes.append)

    def _boom(*args, **kwargs):
        raise OSError("symlinks unsupported")

    monkeypatch.setattr(crs_compose_module, "link_run", _boom)

    assert compose.run(target, run_id="r", build_id="b", sanitizer="address")

    assert captured["run_id"].startswith("r-")
    assert any("Failed to index run" in note for note in notes)
    # Provenance is independent of the index and still lands.
    run_dir = compose.work_dir.get_run_dir(captured["run_id"], "address")
    assert (run_dir / "run-info.json").exists()
    assert not (tmp_path / "work" / "index").exists()


def test_from_yaml_file_records_compose_file(tmp_path: Path, monkeypatch) -> None:
    compose_path = tmp_path / "compose.yaml"
    compose_path.write_text("x: 1\n")
    monkeypatch.setattr(
        crs_compose_module.CRSComposeConfig,
        "from_yaml_file",
        classmethod(lambda cls, path: object()),
    )

    def _init(self, config, work_dir, skip_crs_init=False, offline=False, **_):
        self.compose_file = None

    monkeypatch.setattr(CRSCompose, "__init__", _init)

    compose = CRSCompose.from_yaml_file(compose_path, tmp_path / "work")

    assert compose.compose_file == compose_path.resolve()

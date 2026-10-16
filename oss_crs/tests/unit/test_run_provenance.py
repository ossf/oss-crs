# SPDX-License-Identifier: MIT
"""CRSCompose.from_yaml_file records the compose file path."""

from pathlib import Path

import oss_crs.src.crs_compose as crs_compose_module
from oss_crs.src.crs_compose import CRSCompose


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

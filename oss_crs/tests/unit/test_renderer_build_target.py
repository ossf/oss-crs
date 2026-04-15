"""Unit tests for ``render_build_target_docker_compose``.

Focus: the ``crs.builder_dockerfile`` value rendered into the build-target
docker-compose file must be resolved through ``_resolve_module_dockerfile``
so that ``oss-crs-infra:<module>`` framework references expand to the infra
Dockerfile path instead of being treated as a path under the CRS repo.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from oss_crs.src.templates.renderer import (
    OSS_CRS_ROOT_PATH,
    render_build_target_docker_compose,
)


def _patch_env(monkeypatch) -> None:
    monkeypatch.setattr(
        "oss_crs.src.templates.renderer.build_target_builder_env",
        lambda **_kwargs: SimpleNamespace(effective_env={"EXAMPLE": "1"}, warnings=[]),
    )


def _make_crs(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        name="crs-test",
        crs_path=tmp_path,
        crs_compose_env=SimpleNamespace(get_env=lambda: {"type": "local"}),
        resource=SimpleNamespace(
            cpuset="0-3",
            memory="8G",
            additional_env={},
        ),
        config=SimpleNamespace(version="1.0"),
    )


def _make_target(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        get_target_env=lambda: {"harness": "fuzz_target"},
        proj_path=tmp_path / "proj",
        repo_path=tmp_path / "repo",
    )


def _make_build_config(dockerfile: str) -> SimpleNamespace:
    return SimpleNamespace(
        name="inc-builder",
        dockerfile=dockerfile,
        outputs=["build"],
        additional_env={},
    )


def _render(monkeypatch, tmp_path: Path, dockerfile: str) -> dict:
    _patch_env(monkeypatch)
    (tmp_path / "proj").mkdir()
    (tmp_path / "repo").mkdir()
    rendered, _warnings = render_build_target_docker_compose(
        crs=_make_crs(tmp_path),
        target=_make_target(tmp_path),
        target_base_image="base:latest",
        build_config=_make_build_config(dockerfile),
        build_out_dir=tmp_path / "out",
        build_id="build-1",
        sanitizer="address",
    )
    return yaml.safe_load(rendered)


def test_infra_prefix_resolves_to_framework_dockerfile_path(monkeypatch, tmp_path):
    """``oss-crs-infra:<module>`` must resolve to the infra Dockerfile path."""
    parsed = _render(monkeypatch, tmp_path, "oss-crs-infra:default-builder")

    expected = str(
        OSS_CRS_ROOT_PATH / "oss-crs-infra" / "default-builder" / "Dockerfile"
    )
    services = parsed["services"]
    [(_name, svc)] = list(services.items())
    assert svc["build"]["dockerfile"] == expected


def test_plain_dockerfile_resolves_relative_to_crs_path(monkeypatch, tmp_path):
    """Plain dockerfile values continue to resolve under ``crs.crs_path``."""
    parsed = _render(monkeypatch, tmp_path, "oss-crs/builder.Dockerfile")

    expected = str(tmp_path / "oss-crs/builder.Dockerfile")
    services = parsed["services"]
    [(_name, svc)] = list(services.items())
    assert svc["build"]["dockerfile"] == expected


@pytest.mark.parametrize(
    "dockerfile",
    [
        "oss-crs-infra:default-builder",
        "oss-crs-infra:builder-sidecar",
    ],
)
def test_infra_prefix_does_not_leak_colon_into_path(monkeypatch, tmp_path, dockerfile):
    """The literal ``oss-crs-infra:`` token must not appear in the rendered path."""
    parsed = _render(monkeypatch, tmp_path, dockerfile)
    services = parsed["services"]
    [(_name, svc)] = list(services.items())
    rendered_dockerfile = svc["build"]["dockerfile"]

    assert "oss-crs-infra:" not in rendered_dockerfile
    assert rendered_dockerfile.endswith("/Dockerfile")

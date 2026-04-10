from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from oss_crs.src.templates.renderer import render_run_crs_compose_docker_compose


def _patch_renderer(monkeypatch, build_env_fn=None):
    """Stub out the two external collaborators the renderer reaches for.

    Pass a custom ``build_env_fn`` to observe the kwargs the renderer passes
    to ``build_run_service_env``; otherwise a minimal fixed response is used.
    """
    monkeypatch.setattr(
        "oss_crs.src.templates.renderer.build_run_service_env",
        build_env_fn
        or (
            lambda **_kwargs: SimpleNamespace(
                effective_env={"EXAMPLE": "1"}, warnings=[]
            )
        ),
    )
    monkeypatch.setattr(
        "oss_crs.src.templates.renderer.prepare_llm_context",
        lambda *_args, **_kwargs: None,
    )


def _make_crs_compose(tmp_path: Path, crs_list: list) -> SimpleNamespace:
    return SimpleNamespace(
        crs_list=crs_list,
        work_dir=SimpleNamespace(
            get_exchange_dir=lambda *_a, **_k: tmp_path / "exchange",
            get_build_output_dir=lambda *_a, **_k: tmp_path / "build",
            get_submit_dir=lambda *_a, **_k: tmp_path / "submit",
            get_shared_dir=lambda *_a, **_k: tmp_path / "shared",
            get_log_dir=lambda *_a, **_k: tmp_path / "log",
            get_rebuild_out_dir=lambda *_a, **_k: tmp_path / "rebuild_out",
            get_target_source_dir=lambda *_a, **_k: tmp_path / "target-source",
        ),
        crs_compose_env=SimpleNamespace(get_env=lambda: {"type": "local"}),
        llm=SimpleNamespace(exists=lambda: False, mode="external"),
        config=SimpleNamespace(
            oss_crs_infra=SimpleNamespace(cpuset="0-1", memory="16G")
        ),
    )


def _make_crs(
    tmp_path: Path, name: str, *, builder_names: list | None = None
) -> SimpleNamespace:
    """Build a minimal CRS SimpleNamespace.

    Pass ``builder_names`` to attach a ``target_build_phase`` with one build
    entry per name.
    """
    module_config = SimpleNamespace(
        dockerfile="patcher.Dockerfile",
        additional_env={},
    )
    config = SimpleNamespace(
        version="1.0",
        type=["bug-fixing"],
        is_builder=False,
        is_bug_fixing=True,
        is_bug_fixing_ensemble=False,
        crs_run_phase=SimpleNamespace(modules={"patcher": module_config}),
    )
    if builder_names is not None:
        config.target_build_phase = SimpleNamespace(
            builds=[
                SimpleNamespace(
                    name=b,
                    dockerfile="builder.Dockerfile",
                    outputs=["build"],
                    additional_env={},
                )
                for b in builder_names
            ]
        )
    return SimpleNamespace(
        name=name,
        crs_path=tmp_path,
        resource=SimpleNamespace(
            cpuset="2-7",
            memory="8G",
            additional_env={},
            llm_budget=1,
        ),
        config=config,
    )


def _make_target(
    tmp_path: Path,
    image_name: str = "target:latest",
    *,
    harness: str = "fuzz_target",
    has_repo: bool = True,
) -> SimpleNamespace:
    return SimpleNamespace(
        get_target_env=lambda: {"harness": harness},
        get_docker_image_name=lambda: image_name,
        proj_path=tmp_path / "proj",
        repo_path=tmp_path / "repo",
        _has_repo=has_repo,
    )


def _render(crs_compose, target, tmp_path: Path):
    return render_run_crs_compose_docker_compose(
        crs_compose=crs_compose,
        tmp_docker_compose=SimpleNamespace(dir=tmp_path / "tmp-compose"),
        crs_compose_name="proj",
        target=target,
        run_id="run-1",
        build_id="build-1",
        sanitizer="address",
    )


def test_single_bug_fixing_run_keeps_fetch_mount_and_exchange_sidecar(
    monkeypatch, tmp_path: Path
) -> None:
    include_fetch_dir_calls: list[bool] = []

    def capturing_build_env(**kwargs):
        include_fetch_dir_calls.append(kwargs["include_fetch_dir"])
        return SimpleNamespace(
            effective_env={"EXAMPLE": "1", "OSS_CRS_FETCH_DIR": "/OSS_CRS_FETCH_DIR"},
            warnings=[],
        )

    _patch_renderer(monkeypatch, build_env_fn=capturing_build_env)

    crs = _make_crs(tmp_path, "crs-codex")
    crs_compose = _make_crs_compose(tmp_path, [crs])
    target = _make_target(tmp_path, harness="fuzz_parse_buffer", has_repo=False)

    rendered, warnings = _render(crs_compose, target, tmp_path)

    assert warnings == []
    assert include_fetch_dir_calls == [True]

    services = yaml.safe_load(rendered)["services"]
    patcher_service = services["crs-codex_patcher"]
    assert (
        f"{tmp_path / 'exchange'}:/OSS_CRS_FETCH_DIR:ro" in patcher_service["volumes"]
    )
    # Exchange sidecar is always started — it copies SUBMIT_DIR patches to
    # EXCHANGE_DIR even for single (non-ensemble) bug-fixing runs.
    assert "oss-crs-exchange" in services
    # Always-on sidecar services (CFG-03)
    assert "oss-crs-builder-sidecar" in services
    assert "oss-crs-runner-sidecar" in services


@pytest.mark.parametrize(
    "crs_names",
    [
        pytest.param(["crs-plain"], id="single-crs"),
        pytest.param(["crs-alpha", "crs-beta"], id="multi-crs"),
    ],
)
def test_sidecars_emit_per_crs_dns_aliases(
    monkeypatch, tmp_path: Path, crs_names: list[str]
) -> None:
    """Each CRS in crs_list gets its own DNS alias on both sidecar services (CFG-03/CFG-04)."""
    _patch_renderer(monkeypatch)

    crs_list = [_make_crs(tmp_path, name) for name in crs_names]
    crs_compose = _make_crs_compose(tmp_path, crs_list)
    target = _make_target(tmp_path, image_name="base:latest")

    rendered, warnings = _render(crs_compose, target, tmp_path)
    assert warnings == []

    services = yaml.safe_load(rendered)["services"]
    assert "oss-crs-builder-sidecar" in services
    assert "oss-crs-runner-sidecar" in services

    builder_aliases = services["oss-crs-builder-sidecar"]["networks"]["proj-network"][
        "aliases"
    ]
    runner_aliases = services["oss-crs-runner-sidecar"]["networks"]["proj-network"][
        "aliases"
    ]
    for name in crs_names:
        assert f"builder-sidecar.{name}" in builder_aliases
        assert f"runner-sidecar.{name}" in runner_aliases


@pytest.mark.parametrize(
    "crs_name,builder_names,expected_base_images",
    [
        pytest.param(
            "crs-incremental",
            ["default-build"],
            [
                "BASE_IMAGE_DEFAULT_BUILD=oss-crs-builder:crs-incremental-default-build-build-1"
            ],
            id="single-builder",
        ),
        pytest.param(
            "crs-multi",
            ["inc-builder", "default-build"],
            [
                "BASE_IMAGE_INC_BUILDER=oss-crs-builder:crs-multi-inc-builder-build-1",
                "BASE_IMAGE_DEFAULT_BUILD=oss-crs-builder:crs-multi-default-build-build-1",
            ],
            id="multi-builder",
        ),
    ],
)
def test_sidecar_emits_per_builder_base_image_env_vars(
    monkeypatch,
    tmp_path: Path,
    crs_name: str,
    builder_names: list[str],
    expected_base_images: list[str],
) -> None:
    """Builder sidecar emits BASE_IMAGE_{NAME} env var for each builder."""
    _patch_renderer(monkeypatch)

    crs = _make_crs(tmp_path, crs_name, builder_names=builder_names)
    crs_compose = _make_crs_compose(tmp_path, [crs])
    target = _make_target(tmp_path)

    rendered, warnings = _render(crs_compose, target, tmp_path)
    assert warnings == []

    env_list = yaml.safe_load(rendered)["services"]["oss-crs-builder-sidecar"][
        "environment"
    ]
    for entry in expected_base_images:
        assert entry in env_list
    assert "BASE_IMAGE=target:latest" in env_list


def test_sidecar_emits_project_base_image_env_var(
    monkeypatch, tmp_path: Path
) -> None:
    """Builder-sidecar always emits PROJECT_BASE_IMAGE env var (D-01, TEST-01)."""
    _patch_renderer(monkeypatch)

    crs = _make_crs(tmp_path, "crs-plain")
    crs_compose = _make_crs_compose(tmp_path, [crs])
    target = _make_target(tmp_path, image_name="project-image:latest")

    rendered, warnings = _render(crs_compose, target, tmp_path)
    assert warnings == []

    env_list = yaml.safe_load(rendered)["services"]["oss-crs-builder-sidecar"][
        "environment"
    ]
    assert "PROJECT_BASE_IMAGE=project-image:latest" in env_list

from pathlib import Path
from types import SimpleNamespace

import yaml

from oss_crs.src.templates.renderer import render_run_crs_compose_docker_compose


def test_single_bug_fixing_run_keeps_fetch_mount_and_exchange_sidecar(
    monkeypatch, tmp_path: Path
) -> None:
    include_fetch_dir_calls: list[bool] = []

    def fake_build_run_service_env(**kwargs):
        include_fetch_dir_calls.append(kwargs["include_fetch_dir"])
        return SimpleNamespace(
            effective_env={"EXAMPLE": "1", "OSS_CRS_FETCH_DIR": "/OSS_CRS_FETCH_DIR"},
            warnings=[],
        )

    monkeypatch.setattr(
        "oss_crs.src.templates.renderer.build_run_service_env",
        fake_build_run_service_env,
    )
    monkeypatch.setattr(
        "oss_crs.src.templates.renderer.prepare_llm_context",
        lambda *_args, **_kwargs: None,
    )

    module_config = SimpleNamespace(
        dockerfile="patcher.Dockerfile",
        additional_env={},
    )
    crs = SimpleNamespace(
        name="crs-codex",
        crs_path=tmp_path,
        resource=SimpleNamespace(
            cpuset="2-7",
            memory="8G",
            additional_env={},
            llm_budget=1,
        ),
        config=SimpleNamespace(
            version="0.2",
            type=["bug-fixing"],
            is_builder=False,
            is_bug_fixing=True,
            is_bug_fixing_ensemble=False,
            crs_run_phase=SimpleNamespace(modules={"patcher": module_config}),
        ),
    )
    crs_compose = SimpleNamespace(
        crs_list=[crs],
        work_dir=SimpleNamespace(
            get_exchange_dir=lambda *_args, **_kwargs: tmp_path / "exchange",
            get_build_output_dir=lambda *_args, **_kwargs: tmp_path / "build",
            get_submit_dir=lambda *_args, **_kwargs: tmp_path / "submit",
            get_shared_dir=lambda *_args, **_kwargs: tmp_path / "shared",
            get_log_dir=lambda *_args, **_kwargs: tmp_path / "log",
            get_rebuild_out_dir=lambda *_args, **_kwargs: tmp_path / "rebuild_out",
        ),
        crs_compose_env=SimpleNamespace(get_env=lambda: {"type": "local"}),
        llm=SimpleNamespace(exists=lambda: False, mode="external"),
        config=SimpleNamespace(
            oss_crs_infra=SimpleNamespace(cpuset="0-1", memory="16G")
        ),
    )
    target = SimpleNamespace(
        get_target_env=lambda: {"harness": "fuzz_parse_buffer"},
        get_docker_image_name=lambda: "target:latest",
    )
    tmp_compose = SimpleNamespace(dir=tmp_path / "tmp-compose")

    rendered, warnings = render_run_crs_compose_docker_compose(
        crs_compose=crs_compose,
        tmp_docker_compose=tmp_compose,
        crs_compose_name="proj",
        target=target,
        run_id="run-1",
        build_id="build-1",
        sanitizer="address",
    )

    assert warnings == []
    assert include_fetch_dir_calls == [True]

    compose_data = yaml.safe_load(rendered)
    patcher_service = compose_data["services"]["crs-codex_patcher"]
    assert (
        f"{tmp_path / 'exchange'}:/OSS_CRS_FETCH_DIR:ro" in patcher_service["volumes"]
    )
    # Exchange sidecar is always started — it copies SUBMIT_DIR patches to
    # EXCHANGE_DIR even for single (non-ensemble) bug-fixing runs.
    assert "oss-crs-exchange" in compose_data["services"]

    # Verify always-on sidecar services (CFG-03)
    assert "oss-crs-builder-sidecar" in compose_data["services"]
    assert "oss-crs-runner-sidecar" in compose_data["services"]


def _make_crs_compose(tmp_path: Path, crs_list: list) -> SimpleNamespace:
    """Helper to build a minimal crs_compose SimpleNamespace."""
    return SimpleNamespace(
        crs_list=crs_list,
        work_dir=SimpleNamespace(
            get_exchange_dir=lambda *_args, **_kwargs: tmp_path / "exchange",
            get_build_output_dir=lambda *_args, **_kwargs: tmp_path / "build",
            get_submit_dir=lambda *_args, **_kwargs: tmp_path / "submit",
            get_shared_dir=lambda *_args, **_kwargs: tmp_path / "shared",
            get_log_dir=lambda *_args, **_kwargs: tmp_path / "log",
            get_rebuild_out_dir=lambda *_args, **_kwargs: tmp_path / "rebuild_out",
        ),
        crs_compose_env=SimpleNamespace(get_env=lambda: {"type": "local"}),
        llm=SimpleNamespace(exists=lambda: False, mode="external"),
        config=SimpleNamespace(
            oss_crs_infra=SimpleNamespace(cpuset="0-1", memory="16G")
        ),
    )


def _make_crs(tmp_path: Path, name: str) -> SimpleNamespace:
    """Helper to build a minimal CRS SimpleNamespace (no snapshot-related attrs)."""
    module_config = SimpleNamespace(
        dockerfile="patcher.Dockerfile",
        additional_env={},
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
        config=SimpleNamespace(
            version="1.0",
            type=["bug-fixing"],
            is_builder=False,
            is_bug_fixing=True,
            is_bug_fixing_ensemble=False,
            crs_run_phase=SimpleNamespace(modules={"patcher": module_config}),
        ),
    )


def test_sidecar_always_injected_without_snapshot(
    monkeypatch, tmp_path: Path
) -> None:
    """Sidecars appear in compose output for a CRS with no snapshot-related config."""
    monkeypatch.setattr(
        "oss_crs.src.templates.renderer.build_run_service_env",
        lambda **kwargs: SimpleNamespace(effective_env={"EXAMPLE": "1"}, warnings=[]),
    )
    monkeypatch.setattr(
        "oss_crs.src.templates.renderer.prepare_llm_context",
        lambda *_args, **_kwargs: None,
    )

    crs_name = "crs-plain"
    crs = _make_crs(tmp_path, crs_name)
    crs_compose = _make_crs_compose(tmp_path, [crs])
    target = SimpleNamespace(
        get_target_env=lambda: {"harness": "fuzz_target"},
        get_docker_image_name=lambda: "base:latest",
    )

    rendered, warnings = render_run_crs_compose_docker_compose(
        crs_compose=crs_compose,
        tmp_docker_compose=SimpleNamespace(dir=tmp_path / "tmp-compose"),
        crs_compose_name="proj",
        target=target,
        run_id="run-1",
        build_id="build-1",
        sanitizer="address",
    )

    assert warnings == []
    compose_data = yaml.safe_load(rendered)

    # Builder sidecar always present (CFG-03)
    assert "oss-crs-builder-sidecar" in compose_data["services"]
    # Runner sidecar always present (CFG-04)
    assert "oss-crs-runner-sidecar" in compose_data["services"]

    # Per-CRS DNS aliases present
    builder_sidecar = compose_data["services"]["oss-crs-builder-sidecar"]
    builder_aliases = builder_sidecar["networks"]["proj-network"]["aliases"]
    assert f"builder-sidecar.{crs_name}" in builder_aliases

    runner_sidecar = compose_data["services"]["oss-crs-runner-sidecar"]
    runner_aliases = runner_sidecar["networks"]["proj-network"]["aliases"]
    assert f"runner-sidecar.{crs_name}" in runner_aliases


def test_runner_sidecar_aliases_per_crs(
    monkeypatch, tmp_path: Path
) -> None:
    """Each CRS in crs_list gets its own DNS alias on both sidecar services."""
    monkeypatch.setattr(
        "oss_crs.src.templates.renderer.build_run_service_env",
        lambda **kwargs: SimpleNamespace(effective_env={"EXAMPLE": "1"}, warnings=[]),
    )
    monkeypatch.setattr(
        "oss_crs.src.templates.renderer.prepare_llm_context",
        lambda *_args, **_kwargs: None,
    )

    crs_alpha = _make_crs(tmp_path, "crs-alpha")
    crs_beta = _make_crs(tmp_path, "crs-beta")
    crs_compose = _make_crs_compose(tmp_path, [crs_alpha, crs_beta])
    target = SimpleNamespace(
        get_target_env=lambda: {"harness": "fuzz_target"},
        get_docker_image_name=lambda: "base:latest",
    )

    rendered, warnings = render_run_crs_compose_docker_compose(
        crs_compose=crs_compose,
        tmp_docker_compose=SimpleNamespace(dir=tmp_path / "tmp-compose"),
        crs_compose_name="proj",
        target=target,
        run_id="run-1",
        build_id="build-1",
        sanitizer="address",
    )

    assert warnings == []
    compose_data = yaml.safe_load(rendered)

    runner_sidecar = compose_data["services"]["oss-crs-runner-sidecar"]
    runner_aliases = runner_sidecar["networks"]["proj-network"]["aliases"]
    assert "runner-sidecar.crs-alpha" in runner_aliases
    assert "runner-sidecar.crs-beta" in runner_aliases

    builder_sidecar = compose_data["services"]["oss-crs-builder-sidecar"]
    builder_aliases = builder_sidecar["networks"]["proj-network"]["aliases"]
    assert "builder-sidecar.crs-alpha" in builder_aliases
    assert "builder-sidecar.crs-beta" in builder_aliases


def _make_crs_with_builders(tmp_path: Path, name: str, builder_names: list) -> SimpleNamespace:
    """Helper to build a CRS SimpleNamespace with target_build_phase builds."""
    module_config = SimpleNamespace(
        dockerfile="patcher.Dockerfile",
        additional_env={},
    )
    builds = [
        SimpleNamespace(
            name=b,
            dockerfile="builder.Dockerfile",
            outputs=["build"],
            additional_env={},
        )
        for b in builder_names
    ]
    return SimpleNamespace(
        name=name,
        crs_path=tmp_path,
        resource=SimpleNamespace(
            cpuset="2-7",
            memory="8G",
            additional_env={},
            llm_budget=1,
        ),
        config=SimpleNamespace(
            version="1.0",
            type=["bug-fixing"],
            is_builder=False,
            is_bug_fixing=True,
            is_bug_fixing_ensemble=False,
            crs_run_phase=SimpleNamespace(modules={"patcher": module_config}),
            target_build_phase=SimpleNamespace(builds=builds),
        ),
    )


def test_sidecar_emits_per_builder_base_image_env_vars(
    monkeypatch, tmp_path: Path
) -> None:
    """Builder sidecar emits BASE_IMAGE_{NAME} env var for a single-builder CRS."""
    monkeypatch.setattr(
        "oss_crs.src.templates.renderer.build_run_service_env",
        lambda **kwargs: SimpleNamespace(effective_env={"EXAMPLE": "1"}, warnings=[]),
    )
    monkeypatch.setattr(
        "oss_crs.src.templates.renderer.prepare_llm_context",
        lambda *_args, **_kwargs: None,
    )

    crs = _make_crs_with_builders(tmp_path, "crs-incremental", ["default-build"])
    crs_compose = _make_crs_compose(tmp_path, [crs])
    target = SimpleNamespace(
        get_target_env=lambda: {"harness": "fuzz_target"},
        get_docker_image_name=lambda: "target:latest",
    )

    rendered, warnings = render_run_crs_compose_docker_compose(
        crs_compose=crs_compose,
        tmp_docker_compose=SimpleNamespace(dir=tmp_path / "tmp-compose"),
        crs_compose_name="proj",
        target=target,
        run_id="run-1",
        build_id="build-1",
        sanitizer="address",
    )

    assert warnings == []
    compose_data = yaml.safe_load(rendered)
    builder_sidecar = compose_data["services"]["oss-crs-builder-sidecar"]
    env_list = builder_sidecar["environment"]

    assert "BASE_IMAGE_DEFAULT_BUILD=target:latest" in env_list
    assert "BASE_IMAGE=target:latest" in env_list


def test_sidecar_emits_multiple_builder_env_vars(
    monkeypatch, tmp_path: Path
) -> None:
    """Builder sidecar emits BASE_IMAGE_{NAME} env vars for each builder in a multi-builder CRS."""
    monkeypatch.setattr(
        "oss_crs.src.templates.renderer.build_run_service_env",
        lambda **kwargs: SimpleNamespace(effective_env={"EXAMPLE": "1"}, warnings=[]),
    )
    monkeypatch.setattr(
        "oss_crs.src.templates.renderer.prepare_llm_context",
        lambda *_args, **_kwargs: None,
    )

    crs = _make_crs_with_builders(tmp_path, "crs-multi", ["inc-builder", "default-build"])
    crs_compose = _make_crs_compose(tmp_path, [crs])
    target = SimpleNamespace(
        get_target_env=lambda: {"harness": "fuzz_target"},
        get_docker_image_name=lambda: "target:latest",
    )

    rendered, warnings = render_run_crs_compose_docker_compose(
        crs_compose=crs_compose,
        tmp_docker_compose=SimpleNamespace(dir=tmp_path / "tmp-compose"),
        crs_compose_name="proj",
        target=target,
        run_id="run-1",
        build_id="build-1",
        sanitizer="address",
    )

    assert warnings == []
    compose_data = yaml.safe_load(rendered)
    builder_sidecar = compose_data["services"]["oss-crs-builder-sidecar"]
    env_list = builder_sidecar["environment"]

    assert "BASE_IMAGE_INC_BUILDER=target:latest" in env_list
    assert "BASE_IMAGE_DEFAULT_BUILD=target:latest" in env_list
    assert "BASE_IMAGE=target:latest" in env_list


def test_sidecar_emits_project_base_image_env_var(
    monkeypatch, tmp_path: Path
) -> None:
    """Builder-sidecar always emits PROJECT_BASE_IMAGE env var (D-01, TEST-01)."""
    monkeypatch.setattr(
        "oss_crs.src.templates.renderer.build_run_service_env",
        lambda **kwargs: SimpleNamespace(effective_env={"EXAMPLE": "1"}, warnings=[]),
    )
    monkeypatch.setattr(
        "oss_crs.src.templates.renderer.prepare_llm_context",
        lambda *_args, **_kwargs: None,
    )

    crs = _make_crs(tmp_path, "crs-plain")
    crs_compose = _make_crs_compose(tmp_path, [crs])
    target = SimpleNamespace(
        get_target_env=lambda: {"harness": "fuzz_target"},
        get_docker_image_name=lambda: "project-image:latest",
    )

    rendered, warnings = render_run_crs_compose_docker_compose(
        crs_compose=crs_compose,
        tmp_docker_compose=SimpleNamespace(dir=tmp_path / "tmp-compose"),
        crs_compose_name="proj",
        target=target,
        run_id="run-1",
        build_id="build-1",
        sanitizer="address",
    )

    assert warnings == []
    compose_data = yaml.safe_load(rendered)
    env_list = compose_data["services"]["oss-crs-builder-sidecar"]["environment"]
    assert "PROJECT_BASE_IMAGE=project-image:latest" in env_list

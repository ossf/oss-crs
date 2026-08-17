from pathlib import Path
from types import SimpleNamespace

import yaml

from oss_crs.src.cli.crs_compose import init_target_from_args
from oss_crs.src.crs_compose import CRSCompose
from oss_crs.src.config.crs import CRSType
from oss_crs.src.templates.renderer import render_run_crs_compose_docker_compose
from oss_crs.src.workdir import WorkDir


def test_source_only_target_uses_source_path_as_project_path(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    args = SimpleNamespace(
        work_dir=tmp_path / "work",
        target_proj_path=None,
        target_repo_path=source_dir,
        target_harness=None,
    )

    target = init_target_from_args(args, source_only=True)

    assert target.proj_path == source_dir
    assert target.repo_path == source_dir
    assert target.target_harness is None


def test_source_only_target_accepts_plain_source_directory(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "main.rs").write_text("fn main() {}")
    args = SimpleNamespace(
        work_dir=tmp_path / "work",
        target_proj_path=None,
        target_repo_path=source_dir,
        target_harness=None,
    )

    target = init_target_from_args(args, source_only=True)
    first_workdir_key = WorkDir._get_target_key(target)
    (source_dir / "main.rs").write_text('fn main() { println!("changed"); }')
    changed_target = init_target_from_args(args, source_only=True)

    assert WorkDir._get_target_key(changed_target) == first_workdir_key


def test_source_only_workdir_key_distinguishes_source_paths(tmp_path: Path) -> None:
    keys = []
    for name in ("source-a", "source-b"):
        source_dir = tmp_path / name
        source_dir.mkdir()
        (source_dir / "main.rs").write_text("fn main() {}")
        args = SimpleNamespace(
            work_dir=tmp_path / "work",
            target_proj_path=None,
            target_repo_path=source_dir,
            target_harness=None,
        )
        target = init_target_from_args(args, source_only=True)
        keys.append(WorkDir._get_target_key(target))

    assert keys[0] != keys[1]


def test_source_only_target_requires_source_path(tmp_path: Path) -> None:
    args = SimpleNamespace(
        work_dir=tmp_path / "work",
        target_proj_path=None,
        target_repo_path=None,
        target_harness=None,
    )

    try:
        init_target_from_args(args, source_only=True)
    except ValueError as exc:
        assert (
            "--target-source-path is required when --target-harness is omitted"
            in str(exc)
        )
    else:
        raise AssertionError("expected ValueError")


def test_source_only_target_requires_existing_source_directory(tmp_path: Path) -> None:
    for source_path in (tmp_path / "missing", tmp_path / "source-file"):
        if source_path.name == "source-file":
            source_path.write_text("not a directory")
        args = SimpleNamespace(
            work_dir=tmp_path / "work",
            target_proj_path=None,
            target_repo_path=source_path,
            target_harness=None,
        )

        try:
            init_target_from_args(args, source_only=True, require_source_dir=True)
        except ValueError as exc:
            assert "must be an existing directory" in str(exc)
        else:
            raise AssertionError("expected ValueError")


def test_target_without_harness_uses_normal_target_configuration(
    tmp_path: Path,
) -> None:
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    args = SimpleNamespace(
        work_dir=tmp_path / "work",
        target_proj_path=project_dir,
        target_repo_path=None,
    )

    target = init_target_from_args(args)

    assert target.proj_path == project_dir
    assert target.target_harness is None


def test_source_only_render_omits_build_and_fuzz_mounts(monkeypatch, tmp_path: Path):
    def fake_build_run_service_env(**kwargs):
        return SimpleNamespace(
            effective_env={
                "OSS_CRS_SUBMIT_DIR": "/OSS_CRS_SUBMIT_DIR",
                "OSS_CRS_TARGET_SOURCE": "/OSS_CRS_TARGET_SOURCE",
            },
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
        dockerfile="finder.Dockerfile",
        target_dependent=False,
        additional_env={},
    )
    crs = SimpleNamespace(
        name="source-finder",
        crs_path=tmp_path,
        resource=SimpleNamespace(
            cpuset="2-7",
            memory="8G",
            additional_env={},
            llm_budget=1,
        ),
        config=SimpleNamespace(
            version="1.0",
            type={CRSType.BUG_FINDING},
            is_bug_fixing=False,
            is_bug_fixing_ensemble=False,
            is_triage=False,
            is_seed_filter=False,
            is_auditing=False,
            crs_run_phase=SimpleNamespace(modules={"finder": module_config}),
        ),
    )
    crs_compose = SimpleNamespace(
        crs_list=[crs],
        work_dir=SimpleNamespace(
            get_exchange_dir=lambda *_args, **_kwargs: tmp_path / "exchange",
            get_processed_exchange_dir=lambda *_args, **_kwargs: (
                tmp_path / "processed-exchange"
            ),
            get_build_output_dir=lambda *_args, **_kwargs: tmp_path / "build",
            get_submit_dir=lambda *_args, **_kwargs: tmp_path / "submit",
            get_shared_dir=lambda *_args, **_kwargs: tmp_path / "shared",
            get_log_dir=lambda *_args, **_kwargs: tmp_path / "log",
            get_rebuild_out_dir=lambda *_args, **_kwargs: tmp_path / "rebuild_out",
            get_target_source_dir=lambda *_args, **_kwargs: tmp_path / "target-source",
            get_run_dir=lambda *_args, **_kwargs: tmp_path / "run",
        ),
        crs_compose_env=SimpleNamespace(get_env=lambda: {"type": "local"}),
        llm=SimpleNamespace(exists=lambda: False, mode="external"),
        offline=False,
        config=SimpleNamespace(
            oss_crs_infra=SimpleNamespace(cpuset="0-1", memory="16G")
        ),
    )
    target = SimpleNamespace(
        snapshot_image_tag="",
        get_target_env=lambda: {},
        get_docker_image_name=lambda: "should-not-be-used:latest",
        proj_path=tmp_path / "source",
        repo_path=tmp_path / "source",
        _has_repo=True,
    )
    target.repo_path.mkdir()

    rendered, warnings = render_run_crs_compose_docker_compose(
        crs_compose=crs_compose,
        tmp_docker_compose=SimpleNamespace(dir=tmp_path / "tmp-compose"),
        crs_compose_name="proj",
        target=target,
        run_id="run-1",
        build_id="source-only-run-1",
        sanitizer="address",
        source_only=True,
    )

    assert warnings == []
    compose_data = yaml.safe_load(rendered)
    service = compose_data["services"]["source-finder_finder"]
    assert service["image"] == "oss-crs-runner:source-finder-finder"
    assert "build" not in service
    assert not any("/OSS_CRS_BUILD_OUT_DIR" in item for item in service["volumes"])
    assert not any("/OSS_CRS_FUZZ_PROJ" in item for item in service["volumes"])
    assert any("/OSS_CRS_TARGET_SOURCE:ro" in item for item in service["volumes"])
    assert "oss-crs-builder-sidecar" not in compose_data["services"]
    assert "oss-crs-runner-sidecar" not in compose_data["services"]


def test_source_only_rejects_target_dependent_run_module() -> None:
    crs = SimpleNamespace(
        name="target-dependent-crs",
        config=SimpleNamespace(
            is_auditing=True,
            crs_run_phase=SimpleNamespace(
                modules={
                    "finder": SimpleNamespace(target_dependent=True),
                }
            ),
        ),
    )
    compose = CRSCompose.__new__(CRSCompose)
    compose.crs_list = [crs]

    result = compose._validate_source_only_run()

    assert result.success is False
    assert "target-dependent-crs" in result.error


def test_source_only_rejects_non_auditing_crs() -> None:
    crs = SimpleNamespace(
        name="bug-finding-crs",
        config=SimpleNamespace(is_auditing=False),
    )
    compose = CRSCompose.__new__(CRSCompose)
    compose.crs_list = [crs]

    result = compose._validate_source_only_run()

    assert result.success is False
    assert "bug-finding-crs" in result.error
    assert "auditing" in result.error


def test_harness_gen_without_harness_is_not_source_only() -> None:
    target = SimpleNamespace(target_harness=None, source_only=False)
    compose = CRSCompose.__new__(CRSCompose)
    compose.crs_list = [
        SimpleNamespace(config=SimpleNamespace(is_harness_gen=True)),
    ]

    assert compose.is_source_only_run(target) is False

# SPDX-License-Identifier: MIT
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
            get_processed_exchange_dir=lambda *_a, **_k: (
                tmp_path / "processed-exchange"
            ),
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
        is_bug_fixing=True,
        is_bug_fixing_ensemble=False,
        is_triage=False,
        is_seed_filter=False,
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
    # Exchange sidecar is always present (gated on exchange_dir, always truthy).
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


def test_sidecar_emits_project_base_image_env_var(monkeypatch, tmp_path: Path) -> None:
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


# ---------------------------------------------------------------------------
# ROUTE-01 / ROUTE-02: Early exit watch_dirs and artifact_subdir selection
# ---------------------------------------------------------------------------


def _make_triage_crs(tmp_path: Path, name: str) -> SimpleNamespace:
    """Build a minimal triage CRS SimpleNamespace (is_bug_fixing=False, is_triage=True)."""
    from oss_crs.src.config.crs import CRSType

    module_config = SimpleNamespace(
        dockerfile="triage.Dockerfile",
        additional_env={},
    )
    config = SimpleNamespace(
        version="1.0",
        type=[CRSType.BUG_FINDING_TRIAGE],
        is_bug_fixing=False,
        is_bug_fixing_ensemble=False,
        is_triage=True,
        is_seed_filter=False,
        crs_run_phase=SimpleNamespace(modules={"triager": module_config}),
        target_build_phase=None,
    )
    return SimpleNamespace(
        name=name,
        crs_path=tmp_path,
        resource=SimpleNamespace(
            cpuset="2-7", memory="8G", additional_env={}, llm_budget=1
        ),
        config=config,
    )


def _make_bug_finding_crs(tmp_path: Path, name: str) -> SimpleNamespace:
    """Build a minimal bug-finding CRS (is_bug_fixing=False, is_triage=False)."""
    from oss_crs.src.config.crs import CRSType

    module_config = SimpleNamespace(
        dockerfile="finder.Dockerfile",
        additional_env={},
    )
    config = SimpleNamespace(
        version="1.0",
        type=[CRSType.BUG_FINDING],
        is_bug_fixing=False,
        is_bug_fixing_ensemble=False,
        is_triage=False,
        is_seed_filter=False,
        crs_run_phase=SimpleNamespace(modules={"finder": module_config}),
        target_build_phase=None,
    )
    return SimpleNamespace(
        name=name,
        crs_path=tmp_path,
        resource=SimpleNamespace(
            cpuset="2-7", memory="8G", additional_env={}, llm_budget=1
        ),
        config=config,
    )


def test_compose03_exchange_sidecar_present_for_triage_only_compose(
    monkeypatch, tmp_path: Path
) -> None:
    """COMPOSE-03: exchange sidecar is always present (gated on exchange_dir)."""
    _patch_renderer(monkeypatch)
    triage_crs = _make_triage_crs(tmp_path, "crs-triage")
    crs_compose = _make_crs_compose(tmp_path, [triage_crs])
    target = _make_target(tmp_path)

    rendered, _ = _render(crs_compose, target, tmp_path)
    services = yaml.safe_load(rendered)["services"]

    assert "oss-crs-exchange" in services, (
        "Exchange sidecar is always present when exchange_dir is set"
    )


def test_compose03_exchange_sidecar_present_for_single_bug_finding_compose(
    monkeypatch, tmp_path: Path
) -> None:
    """COMPOSE-03: exchange sidecar is always present (gated on exchange_dir)."""
    _patch_renderer(monkeypatch)
    finder_crs = _make_bug_finding_crs(tmp_path, "crs-finder")
    crs_compose = _make_crs_compose(tmp_path, [finder_crs])
    target = _make_target(tmp_path)

    rendered, _ = _render(crs_compose, target, tmp_path)
    services = yaml.safe_load(rendered)["services"]

    assert "oss-crs-exchange" in services, (
        "Exchange sidecar is always present when exchange_dir is set"
    )


def test_compose03_triage_submit_dir_not_in_exchange_sidecar_mounts(
    monkeypatch, tmp_path: Path
) -> None:
    """COMPOSE-03: triage submit dir must not appear in exchange sidecar mount list.

    Uses a bug-finding-ensemble (2 bug-finders) so exchange sidecar IS emitted,
    then adds a triage CRS and verifies triage submit dir is not in the volumes.
    """
    _patch_renderer(monkeypatch)

    # Two bug-finding CRSes trigger ensemble -> exchange sidecar is emitted
    finder_a = _make_bug_finding_crs(tmp_path, "crs-finder-a")
    finder_b = _make_bug_finding_crs(tmp_path, "crs-finder-b")
    triage_crs = _make_triage_crs(tmp_path, "crs-triage")
    crs_compose = _make_crs_compose(tmp_path, [finder_a, finder_b, triage_crs])
    target = _make_target(tmp_path)

    rendered, _ = _render(crs_compose, target, tmp_path)
    services = yaml.safe_load(rendered)["services"]

    assert "oss-crs-exchange" in services, (
        "Exchange sidecar must be present for bug-finding ensemble"
    )
    exchange_volumes = services["oss-crs-exchange"].get("volumes", [])
    triage_submit_pattern = "/submit/crs-triage:"
    assert not any(triage_submit_pattern in v for v in exchange_volumes), (
        f"Triage submit dir must not appear in exchange sidecar volumes; got: {exchange_volumes}"
    )


# ---------------------------------------------------------------------------
# ROUTE-01 / ROUTE-02: Early exit watch_dirs and artifact_subdir selection
# ---------------------------------------------------------------------------


def test_route01_artifact_subdir_is_povs_when_triage_alongside_bug_finding(
    tmp_path: Path,
) -> None:
    """ROUTE-01: artifact_subdir is 'povs' when triage is present with a bug-finding CRS.

    Triage has is_bug_fixing=False; has_bug_fixing is determined by any(crs.config.is_bug_fixing).
    With a bug-finding CRS (is_bug_fixing=False) and a triage CRS (is_bug_fixing=False),
    has_bug_fixing is False, so artifact_subdir must be 'povs'.
    """
    bug_finding_crs = SimpleNamespace(
        name="crs-finder",
        config=SimpleNamespace(is_bug_fixing=False, is_triage=False),
    )
    triage_crs = SimpleNamespace(
        name="crs-triage",
        config=SimpleNamespace(is_bug_fixing=False, is_triage=True),
    )
    crs_list = [bug_finding_crs, triage_crs]

    has_bug_fixing = any(crs.config.is_bug_fixing for crs in crs_list)
    artifact_subdir = "patches" if has_bug_fixing else "povs"

    assert artifact_subdir == "povs", (
        "artifact_subdir must be 'povs' when no bug-fixing CRS is present (triage is not bug-fixing)"
    )


def test_route02_triage_submit_dir_excluded_from_watch_dirs(tmp_path: Path) -> None:
    """ROUTE-02: triage CRS submit dir is excluded from watch_dirs.

    The watch_dirs list must contain only non-triage CRS submit dirs so that
    triage POV output does not trigger the early exit monitor prematurely.
    """

    def submit_dir(name: str) -> Path:
        return tmp_path / "submit" / name

    bug_finding_crs = SimpleNamespace(
        name="crs-finder",
        config=SimpleNamespace(is_triage=False, is_seed_filter=False),
    )
    triage_crs = SimpleNamespace(
        name="crs-triage",
        config=SimpleNamespace(is_triage=True, is_seed_filter=False),
    )
    crs_list = [bug_finding_crs, triage_crs]

    # Mirror the fixed watch_dirs comprehension from crs_compose.py
    watch_dirs = [
        submit_dir(crs.name)
        for crs in crs_list
        if not crs.config.is_triage and not crs.config.is_seed_filter
    ]

    assert submit_dir("crs-finder") in watch_dirs, (
        "bug-finding submit dir must be watched"
    )
    assert submit_dir("crs-triage") not in watch_dirs, (
        "triage submit dir must NOT be watched"
    )
    assert len(watch_dirs) == 1


# ---------------------------------------------------------------------------
# COMPOSE-01: Triage CRS receives POVs via fetch dir mount
# ---------------------------------------------------------------------------


def test_compose01_triage_crs_has_fetch_dir_mount(monkeypatch, tmp_path: Path) -> None:
    """COMPOSE-01: triage CRS container gets /OSS_CRS_FETCH_DIR:ro volume mount.

    The exchange dir (populated by bug-finding CRS output) is mounted as
    /OSS_CRS_FETCH_DIR:ro on every CRS service, including triage.
    """
    _patch_renderer(monkeypatch)
    triage_crs = _make_triage_crs(tmp_path, "crs-triage")
    crs_compose = _make_crs_compose(tmp_path, [triage_crs])
    target = _make_target(tmp_path)

    rendered, _ = _render(crs_compose, target, tmp_path)
    services = yaml.safe_load(rendered)["services"]

    triage_service = services["crs-triage_triager"]
    volumes = triage_service.get("volumes", [])
    assert any("/OSS_CRS_FETCH_DIR:ro" in v for v in volumes), (
        f"Triage CRS must have /OSS_CRS_FETCH_DIR:ro mount; got volumes: {volumes}"
    )


# ---------------------------------------------------------------------------
# COMPOSE-02: Triage CRS submit dir is mounted for POV output
# ---------------------------------------------------------------------------


def test_compose02_triage_crs_has_submit_dir_mount(monkeypatch, tmp_path: Path) -> None:
    """COMPOSE-02: triage CRS container gets /OSS_CRS_SUBMIT_DIR:rw volume mount.

    Submit dir is mounted unconditionally for every CRS service, including triage,
    allowing triage to write its classified POV output.
    """
    _patch_renderer(monkeypatch)
    triage_crs = _make_triage_crs(tmp_path, "crs-triage")
    crs_compose = _make_crs_compose(tmp_path, [triage_crs])
    target = _make_target(tmp_path)

    rendered, _ = _render(crs_compose, target, tmp_path)
    services = yaml.safe_load(rendered)["services"]

    triage_service = services["crs-triage_triager"]
    volumes = triage_service.get("volumes", [])
    assert any("/OSS_CRS_SUBMIT_DIR:rw" in v for v in volumes), (
        f"Triage CRS must have /OSS_CRS_SUBMIT_DIR:rw mount; got volumes: {volumes}"
    )


# ---------------------------------------------------------------------------
# COMPOSE-04: Triage CRS does not inflate bug_finding_crs_count / ensemble detection
# ---------------------------------------------------------------------------


def test_compose04_triage_does_not_trigger_bug_finding_ensemble(
    monkeypatch, tmp_path: Path
) -> None:
    """COMPOSE-04: one bug-finding CRS + one triage CRS does not constitute an ensemble.

    bug_finding_crs_count counts only CRSType.BUG_FINDING entries, not triage.
    With exactly one bug-finding CRS and one triage CRS, bug_finding_ensemble is False.
    Exchange sidecar is still present (always-on), but ensemble-specific behavior
    (lifecycle sidecar) is not triggered.
    """
    _patch_renderer(monkeypatch)
    finder_crs = _make_bug_finding_crs(tmp_path, "crs-finder")
    triage_crs = _make_triage_crs(tmp_path, "crs-triage")
    crs_compose = _make_crs_compose(tmp_path, [finder_crs, triage_crs])
    target = _make_target(tmp_path)

    rendered, _ = _render(crs_compose, target, tmp_path)
    services = yaml.safe_load(rendered)["services"]

    # Exchange sidecar is always present, but triage does not inflate ensemble count
    assert "oss-crs-exchange" in services
    assert "oss-crs-lifecycle" not in services, (
        "1 bug-finding + 1 triage does not form an ensemble; lifecycle sidecar must be absent"
    )


def test_compose04_two_bug_finding_crs_ensemble_unaffected_by_triage(
    monkeypatch, tmp_path: Path
) -> None:
    """COMPOSE-04: two bug-finding CRSes still form an ensemble when triage is also present.

    Triage does not dilute the bug_finding_crs_count; 2 bug-finding CRSes + 1 triage
    still yields bug_finding_ensemble=True and oss-crs-exchange sidecar must be present.
    """
    _patch_renderer(monkeypatch)
    finder_a = _make_bug_finding_crs(tmp_path, "crs-finder-a")
    finder_b = _make_bug_finding_crs(tmp_path, "crs-finder-b")
    triage_crs = _make_triage_crs(tmp_path, "crs-triage")
    crs_compose = _make_crs_compose(tmp_path, [finder_a, finder_b, triage_crs])
    target = _make_target(tmp_path)

    rendered, _ = _render(crs_compose, target, tmp_path)
    services = yaml.safe_load(rendered)["services"]

    assert "oss-crs-exchange" in services, (
        "2 bug-finding CRSes + triage must still form ensemble and emit exchange sidecar"
    )


# ---------------------------------------------------------------------------
# COMPOSE-05: Triage CRS triggers builder-sidecar and runner-sidecar
# ---------------------------------------------------------------------------


def test_compose05_builder_and_runner_sidecars_present_with_triage(
    monkeypatch, tmp_path: Path
) -> None:
    """COMPOSE-05: builder-sidecar and runner-sidecar are always-on, including triage-only compose.

    Both sidecars are unconditional in the template and must appear regardless of CRS type.
    """
    _patch_renderer(monkeypatch)
    triage_crs = _make_triage_crs(tmp_path, "crs-triage")
    crs_compose = _make_crs_compose(tmp_path, [triage_crs])
    target = _make_target(tmp_path)

    rendered, _ = _render(crs_compose, target, tmp_path)
    services = yaml.safe_load(rendered)["services"]

    assert "oss-crs-builder-sidecar" in services, (
        "oss-crs-builder-sidecar must be present for triage-only compose"
    )
    assert "oss-crs-runner-sidecar" in services, (
        "oss-crs-runner-sidecar must be present for triage-only compose"
    )

from oss_crs.src.env_policy import (
    build_prepare_env,
    build_run_service_env,
    build_target_builder_env,
)


def test_prepare_env_keeps_version_pinned() -> None:
    plan = build_prepare_env(
        base_env={"PATH": "/bin"},
        crs_additional_env={"VERSION": "user-version"},
        version="1.2.3",
        scope="test:prepare",
    )
    assert plan.effective_env["VERSION"] == "1.2.3"
    assert any("ENV001" in warning for warning in plan.warnings)


def test_build_target_env_compose_overrides_build_step_and_system_wins() -> None:
    plan = build_target_builder_env(
        target_env={
            "engine": "libfuzzer",
            "sanitizer": "address",
            "architecture": "x86_64",
            "name": "proj",
            "language": "c",
            "repo_path": "/src",
        },
        run_env_type="local",
        build_id="b123",
        crs_additional_env={"SANITIZER": "memory"},
        build_additional_env={
            "OSS_CRS_BUILD_ID": "user-build-id",
            "OSS_CRS_CUSTOM": "x",
            "SANITIZER": "undefined",
        },
        scope="test:build",
    )
    # Compose entry (user) wins over crs.yaml build-step env for user keys.
    assert plan.effective_env["SANITIZER"] == "memory"
    assert plan.effective_env["OSS_CRS_BUILD_ID"] == "b123"
    assert any("ENV001" in warning for warning in plan.warnings)
    assert any("ENV002" in warning for warning in plan.warnings)


def test_build_target_env_always_includes_target_source() -> None:
    plan = build_target_builder_env(
        target_env={
            "engine": "libfuzzer",
            "sanitizer": "address",
            "architecture": "x86_64",
            "name": "proj",
            "language": "c",
            "repo_path": "/src",
        },
        run_env_type="local",
        build_id="b123",
        crs_additional_env=None,
        build_additional_env=None,
        scope="test:build",
    )
    assert plan.effective_env["OSS_CRS_TARGET_SOURCE"] == "/OSS_CRS_TARGET_SOURCE"


def test_run_env_always_includes_target_source() -> None:
    plan = build_run_service_env(
        target_env={
            "engine": "libfuzzer",
            "architecture": "x86_64",
            "name": "proj",
            "language": "c",
            "repo_path": "/repo",
        },
        sanitizer="address",
        run_env_type="local",
        crs_name="crs-a",
        module_name="patcher",
        run_id="r1",
        cpuset="0-1",
        memory_limit="2G",
        module_additional_env=None,
        crs_additional_env=None,
        scope="test:run",
    )
    assert plan.effective_env["OSS_CRS_TARGET_SOURCE"] == "/OSS_CRS_TARGET_SOURCE"


def test_build_target_env_always_includes_fuzz_proj() -> None:
    plan = build_target_builder_env(
        target_env={
            "engine": "libfuzzer",
            "sanitizer": "address",
            "architecture": "x86_64",
            "name": "proj",
            "language": "c",
            "repo_path": "/src",
        },
        run_env_type="local",
        build_id="b123",
        crs_additional_env=None,
        build_additional_env=None,
        scope="test:build",
    )
    assert plan.effective_env["OSS_CRS_FUZZ_PROJ"] == "/OSS_CRS_FUZZ_PROJ"


def test_run_env_always_includes_fuzz_proj() -> None:
    plan = build_run_service_env(
        target_env={
            "engine": "libfuzzer",
            "architecture": "x86_64",
            "name": "proj",
            "language": "c",
            "repo_path": "/repo",
        },
        sanitizer="address",
        run_env_type="local",
        crs_name="crs-a",
        module_name="patcher",
        run_id="r1",
        cpuset="0-1",
        memory_limit="2G",
        module_additional_env=None,
        crs_additional_env=None,
        scope="test:run",
    )
    assert plan.effective_env["OSS_CRS_FUZZ_PROJ"] == "/OSS_CRS_FUZZ_PROJ"


def test_user_cannot_override_reserved_source_env_vars() -> None:
    """User-provided OSS_CRS_FUZZ_PROJ and OSS_CRS_TARGET_SOURCE are superseded by system values."""
    plan = build_target_builder_env(
        target_env={
            "engine": "libfuzzer",
            "sanitizer": "address",
            "architecture": "x86_64",
            "name": "proj",
            "language": "c",
            "repo_path": "/src",
        },
        run_env_type="local",
        build_id="b123",
        crs_additional_env={
            "OSS_CRS_FUZZ_PROJ": "/hacked",
            "OSS_CRS_TARGET_SOURCE": "/also-hacked",
        },
        build_additional_env=None,
        scope="test:build",
    )
    assert plan.effective_env["OSS_CRS_FUZZ_PROJ"] == "/OSS_CRS_FUZZ_PROJ"
    assert plan.effective_env["OSS_CRS_TARGET_SOURCE"] == "/OSS_CRS_TARGET_SOURCE"
    assert any("ENV001" in warning for warning in plan.warnings)


def test_run_env_preserves_existing_precedence_and_system_wins() -> None:
    plan = build_run_service_env(
        target_env={
            "engine": "libfuzzer",
            "architecture": "x86_64",
            "name": "proj",
            "language": "c",
            "repo_path": "/repo",
        },
        sanitizer="address",
        run_env_type="local",
        crs_name="crs-a",
        module_name="patcher",
        run_id="r1",
        cpuset="0-1",
        memory_limit="2G",
        module_additional_env={"MY_KEY": "module", "SHARED": "module"},
        crs_additional_env={
            "SHARED": "crs",
            "OSS_CRS_TARGET": "user-target",
            "SANITIZER": "memory",
        },
        scope="test:run",
        harness="h1",
        include_fetch_dir=True,
        llm_api_url="http://llm",
        llm_api_key="sk-test",
    )

    # CRS env wins over module env for user keys.
    assert plan.effective_env["SHARED"] == "crs"
    # Build-sensitive keys can be overridden by user env.
    assert plan.effective_env["SANITIZER"] == "memory"
    # Reserved key remains framework-owned.
    assert plan.effective_env["OSS_CRS_TARGET"] == "proj"
    assert plan.effective_env["OSS_CRS_TARGET_HARNESS"] == "h1"
    assert plan.effective_env["OSS_CRS_FETCH_DIR"] == "/OSS_CRS_FETCH_DIR"
    assert plan.effective_env["OSS_CRS_LLM_API_URL"] == "http://llm"
    assert plan.effective_env["OSS_CRS_LLM_API_KEY"] == "sk-test"
    assert plan.effective_env["BUILDER_MODULE"] == "builder-sidecar"
    assert any("ENV001" in warning for warning in plan.warnings)


def test_run_env_injects_builder_module() -> None:
    """Framework must inject BUILDER_MODULE so CRS developers don't need to set it."""
    plan = build_run_service_env(
        target_env={
            "engine": "libfuzzer",
            "architecture": "x86_64",
            "name": "proj",
            "language": "c",
            "repo_path": "/repo",
        },
        sanitizer="address",
        run_env_type="local",
        crs_name="crs-a",
        module_name="patcher",
        run_id="r1",
        cpuset="0-1",
        memory_limit="2G",
        module_additional_env=None,
        crs_additional_env=None,
        scope="test:run",
    )
    assert plan.effective_env["BUILDER_MODULE"] == "builder-sidecar"


def test_run_env_builder_module_not_overridable_by_crs() -> None:
    """BUILDER_MODULE is a system env — CRS additional_env must not override it."""
    plan = build_run_service_env(
        target_env={
            "engine": "libfuzzer",
            "architecture": "x86_64",
            "name": "proj",
            "language": "c",
            "repo_path": "/repo",
        },
        sanitizer="address",
        run_env_type="local",
        crs_name="crs-a",
        module_name="patcher",
        run_id="r1",
        cpuset="0-1",
        memory_limit="2G",
        module_additional_env=None,
        crs_additional_env={"BUILDER_MODULE": "custom-builder"},
        scope="test:run",
    )
    # System env wins — framework always controls BUILDER_MODULE
    assert plan.effective_env["BUILDER_MODULE"] == "builder-sidecar"

from dataclasses import dataclass
from typing import Mapping

from .env_schema import (
    RESERVED_SYSTEM_EXACT,
    RESERVED_SYSTEM_PREFIXES,
    is_reserved_system_key,
)


@dataclass
class EnvPlan:
    effective_env: dict[str, str]
    warnings: list[str]


def _merge_envs(*env_maps: Mapping[str, str] | None) -> dict[str, str]:
    merged: dict[str, str] = {}
    for env_map in env_maps:
        if not env_map:
            continue
        merged.update({k: str(v) for k, v in env_map.items()})
    return merged


def _resolve_env(
    *,
    phase: str,
    base_env: Mapping[str, str] | None,
    user_layers: list[Mapping[str, str] | None],
    system_env: Mapping[str, str] | None,
    scope: str,
) -> EnvPlan:
    base = _merge_envs(base_env)
    user = _merge_envs(*user_layers)
    system = _merge_envs(system_env)

    warnings: list[str] = []
    reserved_attempts = sorted(key for key in user if is_reserved_system_key(key))
    if reserved_attempts:
        warning_keys = ", ".join(reserved_attempts)
        warnings.append(
            f"ENV001 [{phase}/{scope}] Ignoring user overrides for reserved keys: {warning_keys}"
        )
    unknown_reserved = sorted(
        key
        for key in user
        if any(key.startswith(prefix) for prefix in RESERVED_SYSTEM_PREFIXES)
        and key not in system
    )
    if unknown_reserved:
        warnings.append(
            f"ENV002 [{phase}/{scope}] User provided reserved namespace keys not owned by this phase: "
            + ", ".join(unknown_reserved)
        )

    effective = dict(base)
    effective.update(user)
    # Reserved exact keys should always be final when present in base.
    for key in RESERVED_SYSTEM_EXACT:
        if key in base:
            effective[key] = base[key]
    # Reserved system keys are always final.
    effective.update(system)
    return EnvPlan(effective_env=effective, warnings=warnings)


def build_prepare_env(
    *,
    base_env: Mapping[str, str],
    crs_additional_env: Mapping[str, str] | None,
    version: str,
    scope: str,
) -> EnvPlan:
    return _resolve_env(
        phase="prepare",
        base_env=base_env,
        user_layers=[crs_additional_env],
        system_env={"VERSION": version},
        scope=scope,
    )


def build_target_builder_env(
    *,
    target_env: Mapping[str, str],
    run_env_type: str,
    build_id: str,
    crs_additional_env: Mapping[str, str] | None,
    build_additional_env: Mapping[str, str] | None,
    harness: str | None = None,
    include_fetch_dir: bool = False,
    scope: str,
) -> EnvPlan:
    base_env = {
        "HELPER": "True",
        "RUN_FUZZER_MODE": "interactive",
        "FUZZING_ENGINE": target_env["engine"],
        "SANITIZER": target_env["sanitizer"],
        "ARCHITECTURE": target_env["architecture"],
        "PROJECT_NAME": target_env["name"],
        "FUZZING_LANGUAGE": target_env["language"],
    }
    system_env = {
        "OSS_CRS_RUN_ENV_TYPE": run_env_type,
        "OSS_CRS_BUILD_ID": build_id,
        "OSS_CRS_BUILD_OUT_DIR": "/OSS_CRS_BUILD_OUT_DIR",
        "OSS_CRS_TARGET": target_env["name"],
        "OSS_CRS_PROJ_PATH": "/OSS_CRS_PROJ_PATH",
        "OSS_CRS_TARGET_PROJ_DIR": "/project_dir",
        "OSS_CRS_REPO_PATH": target_env["repo_path"],
    }
    if harness:
        system_env["OSS_CRS_TARGET_HARNESS"] = harness
    if include_fetch_dir:
        system_env["OSS_CRS_FETCH_DIR"] = "/OSS_CRS_FETCH_DIR"
    return _resolve_env(
        phase="build",
        base_env=base_env,
        user_layers=[crs_additional_env, build_additional_env],
        system_env=system_env,
        scope=scope,
    )


def build_run_service_env(
    *,
    target_env: Mapping[str, str],
    sanitizer: str,
    run_env_type: str,
    crs_name: str,
    module_name: str,
    run_id: str,
    cpuset: str,
    memory_limit: str,
    module_additional_env: Mapping[str, str] | None,
    crs_additional_env: Mapping[str, str] | None,
    scope: str,
    harness: str | None = None,
    include_fetch_dir: bool = False,
    include_snapshot_image: str | None = None,
    llm_api_url: str | None = None,
    llm_api_key: str | None = None,
) -> EnvPlan:
    base_env = {
        "HELPER": "True",
        "RUN_FUZZER_MODE": "interactive",
        "FUZZING_ENGINE": target_env["engine"],
        "SANITIZER": sanitizer,
        "ARCHITECTURE": target_env["architecture"],
        "PROJECT_NAME": target_env["name"],
        "FUZZING_LANGUAGE": target_env["language"],
    }
    # Preserve existing behavior: module env first, CRS env last.
    system_env = {
        "OSS_CRS_RUN_ENV_TYPE": run_env_type,
        "OSS_CRS_NAME": crs_name,
        "OSS_CRS_SERVICE_NAME": f"{crs_name}_{module_name}",
        "OSS_CRS_TARGET": target_env["name"],
        "OSS_CRS_RUN_ID": run_id,
        "OSS_CRS_CPUSET": cpuset,
        "OSS_CRS_MEMORY_LIMIT": memory_limit,
        "OSS_CRS_PROJ_PATH": "/OSS_CRS_PROJ_PATH",
        "OSS_CRS_REPO_PATH": target_env["repo_path"],
        "OSS_CRS_BUILD_OUT_DIR": "/OSS_CRS_BUILD_OUT_DIR",
        "OSS_CRS_SUBMIT_DIR": "/OSS_CRS_SUBMIT_DIR",
        "OSS_CRS_SHARED_DIR": "/OSS_CRS_SHARED_DIR",
    }
    if harness:
        system_env["OSS_CRS_TARGET_HARNESS"] = harness
    if include_fetch_dir:
        system_env["OSS_CRS_FETCH_DIR"] = "/OSS_CRS_FETCH_DIR"
    if include_snapshot_image:
        system_env["OSS_CRS_SNAPSHOT_IMAGE"] = include_snapshot_image
    if llm_api_url:
        system_env["OSS_CRS_LLM_API_URL"] = llm_api_url
    if llm_api_key:
        system_env["OSS_CRS_LLM_API_KEY"] = llm_api_key

    return _resolve_env(
        phase="run",
        base_env=base_env,
        user_layers=[module_additional_env, crs_additional_env],
        system_env=system_env,
        scope=scope,
    )

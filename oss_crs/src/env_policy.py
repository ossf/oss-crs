# SPDX-License-Identifier: MIT
from dataclasses import dataclass
import re
from typing import Mapping

from .env_schema import (
    RESERVED_SYSTEM_EXACT,
    RESERVED_SYSTEM_PREFIXES,
    is_reserved_system_key,
)

# OSS-Fuzz env vars that must be set in every build/test container.
# Mapping: env var name -> target_env dict key.
OSS_FUZZ_TARGET_ENV = {
    "FUZZING_ENGINE": "engine",
    "SANITIZER": "sanitizer",
    "ARCHITECTURE": "architecture",
    "FUZZING_LANGUAGE": "language",
}

ENV_INTERPOLATION_RE = re.compile(
    r"(?<!\$)\$(?:\{([A-Za-z_][A-Za-z0-9_]*)(?:(:?[-?+])[^}]*)?\}|([A-Za-z_][A-Za-z0-9_]*))"
)


@dataclass
class EnvPlan:
    effective_env: dict[str, str]
    warnings: list[str]


def unresolved_env_references(value: object, host_envs: set[str]) -> set[str]:
    """Return host env names referenced by value that are not available now."""
    unresolved: set[str] = set()
    for match in ENV_INTERPOLATION_RE.finditer(str(value)):
        env_name = match.group(1) or match.group(3)
        operator = match.group(2)
        if operator in ("-", ":-"):
            continue
        if env_name not in host_envs:
            unresolved.add(env_name)
    return unresolved


def additional_env_value_is_resolved(value: object, host_envs: set[str]) -> bool:
    """Return whether a compose additional_env value can be resolved now."""
    return not unresolved_env_references(value, host_envs)


# Substitution variant of ENV_INTERPOLATION_RE that also captures the
# default/alternate word so ${VAR:-default} style references can be resolved.
_ENV_SUBST_RE = re.compile(
    r"(?<!\$)\$(?:\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?:(?P<op>:?[-?+])(?P<word>[^}]*))?\}"
    r"|(?P<simple>[A-Za-z_][A-Za-z0-9_]*))"
)


def resolve_env_references(
    value: object, host_env: Mapping[str, str]
) -> tuple[str, set[str]]:
    """Substitute ``$VAR`` / ``${VAR}`` references in ``value`` from ``host_env``.

    Follows shell/compose semantics for the ``${VAR:-default}`` / ``${VAR-default}``,
    ``${VAR:+alt}`` / ``${VAR+alt}`` and ``${VAR:?msg}`` / ``${VAR?msg}`` operators
    (``:`` variants treat an empty value as unset). ``$$`` is left untouched.

    Returns ``(resolved_string, unresolved_names)`` where ``unresolved_names`` are
    bare references with no default that are absent from ``host_env`` — the caller
    can warn about these and leave them for downstream resolution.

    Used to bake controller-side environment values (e.g. ``CLAUDE_CODE_OAUTH_TOKEN``)
    into a compose that will run on a *remote* host whose shell does not carry the
    launching environment.
    """
    unresolved: set[str] = set()

    def _repl(match: "re.Match[str]") -> str:
        name = match.group("braced") or match.group("simple")
        op = match.group("op")
        word = match.group("word") or ""
        raw = host_env.get(name)
        set_any = raw is not None
        set_nonempty = bool(raw)
        if op in ("-", ":-"):
            use = set_nonempty if op == ":-" else set_any
            return raw if use and raw is not None else word
        if op in ("+", ":+"):
            use = set_nonempty if op == ":+" else set_any
            return word if use else ""
        if op in ("?", ":?"):
            use = set_nonempty if op == ":?" else set_any
            if use and raw is not None:
                return raw
            unresolved.add(name)
            return match.group(0)
        if raw is not None:
            return raw
        unresolved.add(name)
        return match.group(0)

    return _ENV_SUBST_RE.sub(_repl, str(value)), unresolved


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
            f"ENV001 [{phase}/{scope}] Reserved keys were provided; framework-owned values override user-provided values: {warning_keys}"
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
        **{k: target_env[v] for k, v in OSS_FUZZ_TARGET_ENV.items()},
        "PROJECT_NAME": target_env["name"],
    }
    system_env = {
        "OSS_CRS_RUN_ENV_TYPE": run_env_type,
        "OSS_CRS_CURRENT_PHASE": "build-target",
        "OSS_CRS_BUILD_ID": build_id,
        "OSS_CRS_BUILD_OUT_DIR": "/OSS_CRS_BUILD_OUT_DIR",
        "OSS_CRS_TARGET": target_env["name"],
        "OSS_CRS_PROJ_PATH": "/OSS_CRS_PROJ_PATH",
        "OSS_CRS_TARGET_PROJ_DIR": "/OSS_CRS_PROJ_PATH",
        "OSS_CRS_REPO_PATH": target_env["repo_path"],
        "OSS_CRS_FUZZ_PROJ": "/OSS_CRS_FUZZ_PROJ",
        "OSS_CRS_TARGET_SOURCE": "/OSS_CRS_TARGET_SOURCE",
    }
    if harness:
        system_env["OSS_CRS_TARGET_HARNESS"] = harness
    if include_fetch_dir:
        system_env["OSS_CRS_FETCH_DIR"] = "/OSS_CRS_FETCH_DIR"
    return _resolve_env(
        phase="build",
        base_env=base_env,
        # Keep user (compose entry) precedence consistent across phases.
        # build_additional_env from crs.yaml acts as default/fallback.
        user_layers=[build_additional_env, crs_additional_env],
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
    llm_api_url: str | None = None,
    llm_api_key: str | None = None,
) -> EnvPlan:
    base_env = {
        "HELPER": "True",
        "RUN_FUZZER_MODE": "interactive",
        **{
            k: target_env[v] for k, v in OSS_FUZZ_TARGET_ENV.items() if k != "SANITIZER"
        },
        "SANITIZER": sanitizer,  # override: run phase uses resolved sanitizer
        "PROJECT_NAME": target_env["name"],
    }
    # Preserve existing behavior: module env first, CRS env last.
    system_env = {
        "OSS_CRS_RUN_ENV_TYPE": run_env_type,
        "OSS_CRS_CURRENT_PHASE": "run",
        "OSS_CRS_NAME": crs_name,
        "OSS_CRS_SERVICE_NAME": f"{crs_name}_{module_name}",
        "OSS_CRS_TARGET": target_env["name"],
        "OSS_CRS_RUN_ID": run_id,
        "OSS_CRS_CPUSET": cpuset,
        "OSS_CRS_MEMORY_LIMIT": memory_limit,
        "OSS_CRS_PROJ_PATH": "/OSS_CRS_PROJ_PATH",
        "OSS_CRS_REPO_PATH": target_env["repo_path"],
        "OSS_CRS_BUILD_OUT_DIR": "/OSS_CRS_BUILD_OUT_DIR",
        "OSS_CRS_REBUILD_OUT_DIR": "/OSS_CRS_REBUILD_OUT_DIR",
        "OSS_CRS_SUBMIT_DIR": "/OSS_CRS_SUBMIT_DIR",
        "OSS_CRS_SHARED_DIR": "/OSS_CRS_SHARED_DIR",
        "OSS_CRS_LOG_DIR": "/OSS_CRS_LOG_DIR",
        "BUILDER_MODULE": "builder-sidecar",
        "OSS_CRS_FUZZ_PROJ": "/OSS_CRS_FUZZ_PROJ",
        "OSS_CRS_TARGET_SOURCE": "/OSS_CRS_TARGET_SOURCE",
    }
    if harness:
        system_env["OSS_CRS_TARGET_HARNESS"] = harness
    if include_fetch_dir:
        system_env["OSS_CRS_FETCH_DIR"] = "/OSS_CRS_FETCH_DIR"
    if llm_api_url:
        system_env["OSS_CRS_LLM_API_URL"] = llm_api_url
    if llm_api_key:
        system_env["OSS_CRS_LLM_API_KEY_FILE"] = "/run/secrets/oss_crs_llm_api_key"

    return _resolve_env(
        phase="run",
        base_env=base_env,
        user_layers=[module_additional_env, crs_additional_env],
        system_env=system_env,
        scope=scope,
    )

from pathlib import Path
from jinja2 import Environment, FileSystemLoader, select_autoescape
from typing import TYPE_CHECKING, Optional
import os
import string
import secrets
import yaml

from ..config.crs import CRSType, OSS_CRS_INFRA_PREFIX
from ..env_policy import build_run_service_env, build_target_builder_env
from ..llm import DEFAULT_LITELLM_CONFIG_PATH

RAND_CHARS = string.ascii_lowercase + string.digits

if TYPE_CHECKING:
    from ..crs import CRS
    from ..target import Target
    from ..config.crs import BuildConfig
    from ..utils import TmpDockerCompose
    from ..crs_compose import CRSCompose

CUR_DIR = Path(__file__).parent
OSS_CRS_ROOT_PATH = (CUR_DIR / "../../../").resolve()
LIBCRS_PATH = (OSS_CRS_ROOT_PATH / "libCRS").resolve()


def _resolve_module_dockerfile(crs_path: Path, dockerfile: str) -> str:
    """Resolve a module's dockerfile path.

    Handles two cases:
    - Framework module reference: "oss-crs-infra:<module-name>" resolves to
      OSS_CRS_ROOT_PATH/oss-crs-infra/<module-name>/Dockerfile
    - File path: resolved relative to crs_path
    """
    if dockerfile.startswith(OSS_CRS_INFRA_PREFIX):
        module_name = dockerfile[len(OSS_CRS_INFRA_PREFIX) :]
        return str(OSS_CRS_ROOT_PATH / "oss-crs-infra" / module_name / "Dockerfile")
    return str(crs_path / dockerfile)


def _generate_random_key(length: int = 10) -> str:
    """Generate a random alphanumeric string."""
    return "".join(secrets.choice(RAND_CHARS) for _ in range(length))


def render_template(template_path: Path, context: dict) -> str:
    """Render a Jinja2 template with the given context.

    Args:
        template_path (str | Path): Path to the Jinja2 template file.
        context (dict): Context variables for rendering the template.

    Returns:
        str: Rendered template content.
    """
    template_dir = Path(template_path).parent
    template_file = Path(template_path).name

    env = Environment(
        loader=FileSystemLoader(searchpath=str(template_dir)),
        autoescape=select_autoescape(),
    )
    template = env.get_template(template_file)
    rendered_content = template.render(context)
    return rendered_content


def render_build_target_docker_compose(
    crs: "CRS",
    target: "Target",
    target_base_image: str,
    build_config: "BuildConfig",
    build_out_dir: Path,
    build_id: str,
    sanitizer: str,
    build_fetch_dir: Optional[Path] = None,
) -> tuple[str, list[str]]:
    """Render the docker-compose file for building a target.

    Args:
        crs (CRS): CRS instance.
        target (Target): Target instance.
        build_config (BuildConfig): Build configuration.
        build_out_dir (Path): Output directory for the build.
        build_id (str): Build identifier.
        sanitizer (str): Sanitizer to use (e.g., "address", "undefined").
        build_fetch_dir: Optional path to build-time fetch directory.

    Returns:
        tuple[str, list[str]]: Rendered docker-compose content and warning notes.
    """
    if crs.crs_compose_env is None or crs.resource is None:
        raise ValueError(
            f"CRS '{crs.name}' is missing compose environment or resource configuration"
        )
    crs_compose_env = crs.crs_compose_env
    resource = crs.resource
    template_path = CUR_DIR / "build-target.docker-compose.yaml.j2"
    target_env = target.get_target_env()
    target_env["image"] = target_base_image
    # Override sanitizer with the explicitly-passed value
    target_env["sanitizer"] = sanitizer
    env_plan = build_target_builder_env(
        target_env=target_env,
        run_env_type=crs_compose_env.get_env()["type"],
        build_id=build_id,
        crs_additional_env=resource.additional_env,
        build_additional_env=build_config.additional_env,
        harness=target_env.get("harness"),
        include_fetch_dir=bool(build_fetch_dir),
        include_target_source=target._has_repo,
        scope=f"{crs.name}:build:{build_config.name}",
    )
    context = {
        "crs": {
            "name": crs.name,
            "path": str(crs.crs_path),
            "builder_dockerfile": str(crs.crs_path / build_config.dockerfile),
            "version": crs.config.version,
        },
        "effective_env": env_plan.effective_env,
        "target": target_env,
        "build_out_dir": str(build_out_dir),
        "build_id": build_id,
        "crs_compose_env": crs_compose_env.get_env(),
        "libCRS_path": str(LIBCRS_PATH),
        "resource": {
            "cpuset": resource.cpuset,
            "memory": resource.memory,
        },
        "build_fetch_dir": str(build_fetch_dir) if build_fetch_dir else None,
        "fuzz_proj_path": str(target.proj_path.resolve()),
        "target_source_path": str(target.repo_path.resolve()) if target._has_repo else None,
    }
    return render_template(template_path, context), env_plan.warnings


def prepare_llm_context(
    tmp_docker_compose: "TmpDockerCompose", crs_compose: "CRSCompose"
) -> Optional[dict]:
    if not crs_compose.llm.exists():
        return None
    llm_config = crs_compose.llm.llm_config
    if llm_config is None:
        raise RuntimeError("LLM mode is enabled but llm_config is missing")

    if crs_compose.llm.mode == "external":
        url = crs_compose.llm.get_crs_api_url()
        key = crs_compose.llm.get_crs_api_key()
        if not url:
            raise RuntimeError("External LiteLLM URL is required.")
        if not key:
            raise RuntimeError("External LiteLLM API key is required.")
        keys = {}
        for crs in crs_compose.crs_list:
            if crs.config.is_builder:
                continue
            keys[crs.name] = key
        return {
            "mode": "external",
            "llm_api_url": url,
            "api_keys": keys,
        }

    if crs_compose.llm.mode == "internal":
        # Prepare LiteLLM environment variables for internal sidecar mode
        tmp_dir = tmp_docker_compose.dir
        if tmp_dir is None:
            raise RuntimeError("Temporary docker compose directory was not initialized")
        litellm_env = {}
        for name in crs_compose.llm.extract_envs():
            tmp = os.environ.get(name)
            if tmp is None:
                raise RuntimeError(
                    f"Environment variable '{name}' required by LiteLLM config is not set."
                )
            litellm_env[name] = tmp
        # Generate keys for each CRS
        keys = {}
        key_info = {}
        for crs in crs_compose.crs_list:
            if crs.config.is_builder:
                continue
            if crs.resource is None:
                raise RuntimeError(
                    f"CRS '{crs.name}' is missing resource configuration for LiteLLM"
                )
            key = "sk-" + _generate_random_key(16)
            keys[crs.name] = key
            key_info[crs.name] = {
                "api_key": key,
                "required_llms": crs.config.required_llms,
                "llm_budget": crs.resource.llm_budget,
            }
        key_gen_request_path = tmp_dir / "key_gen_request.yaml"
        key_gen_request_path.write_text(
            yaml.dump(key_info, default_flow_style=False, sort_keys=False)
        )

        return {
            "mode": "internal",
            "llm_api_url": "http://litellm.oss-crs:4000",
            "litellm_master_key": "sk-" + _generate_random_key(16),
            "postgres_password": _generate_random_key(16),
            "litellm_config_path": llm_config.litellm.internal.config_path
            if llm_config.litellm.internal and llm_config.litellm.internal.config_path
            else str(DEFAULT_LITELLM_CONFIG_PATH),
            "api_keys": keys,
            "litellm_env": litellm_env,
            "key_gen_request_path": str(key_gen_request_path),
        }

    raise RuntimeError(f"Unsupported LLM mode: {crs_compose.llm.mode}")


def render_run_crs_compose_docker_compose(
    crs_compose: "CRSCompose",
    tmp_docker_compose: "TmpDockerCompose",
    crs_compose_name: str,
    target: "Target",
    run_id: str,
    build_id: str,
    sanitizer: str,
    cgroup_parents: Optional[dict[str, str]] = None,
) -> tuple[str, list[str]]:
    template_path = CUR_DIR / "run-crs-compose.docker-compose.yaml.j2"
    compose_env = crs_compose.crs_compose_env

    # Deployment conditions for infra sidecars
    bug_finding_crs_count = sum(
        1
        for crs in crs_compose.crs_list
        if CRSType.BUG_FINDING in crs.config.type and not crs.config.is_builder
    )
    bug_finding_ensemble = bug_finding_crs_count > 1
    bug_fix_ensemble = any(
        crs.config.is_bug_fixing_ensemble for crs in crs_compose.crs_list
    )
    fetch_dir = str(crs_compose.work_dir.get_exchange_dir(target, run_id, sanitizer))
    exchange_dir = fetch_dir

    target_env = target.get_target_env()
    context = {
        "libCRS_path": str(LIBCRS_PATH),
        "crs_compose_name": crs_compose_name,
        "crs_list": crs_compose.crs_list,
        "crs_compose_env": compose_env.get_env(),
        "target_env": target_env,
        "target": target,
        "work_dir": crs_compose.work_dir,
        "run_id": run_id,
        "build_id": build_id,
        "sanitizer": sanitizer,
        "oss_crs_infra_root_path": str(OSS_CRS_ROOT_PATH / "oss-crs-infra"),
        "snapshot_image_tag": target.snapshot_image_tag or "",
        "resolve_dockerfile": _resolve_module_dockerfile,
        "fetch_dir": fetch_dir,
        "exchange_dir": exchange_dir,
        "bug_finding_ensemble": bug_finding_ensemble,
        "bug_fix_ensemble": bug_fix_ensemble,
        "cgroup_parents": cgroup_parents,  # Dict mapping CRS name to cgroup_parent path
        "fuzz_proj_path": str(target.proj_path.resolve()),
        "target_source_path": str(target.repo_path.resolve()) if target._has_repo else None,
    }

    llm_context = prepare_llm_context(tmp_docker_compose, crs_compose)
    if llm_context:
        context["llm_context"] = llm_context

    module_envs: dict[str, dict[str, str]] = {}
    warnings: list[str] = []
    for crs in crs_compose.crs_list:
        for module_name, module_config in crs.config.crs_run_phase.modules.items():
            if not module_config.dockerfile:
                continue
            if crs.resource is None:
                raise ValueError(
                    f"CRS '{crs.name}' is missing resource configuration for run compose"
                )
            resource = crs.resource
            service_name = f"{crs.name}_{module_name}"
            include_snapshot = (
                target.snapshot_image_tag
                if (
                    target.snapshot_image_tag
                    and not module_config.run_snapshot
                    and not crs.config.is_builder
                )
                else None
            )
            llm_url = None
            llm_key = None
            if llm_context is not None and not crs.config.is_builder:
                llm_url = llm_context.get("llm_api_url")
                llm_key = llm_context.get("api_keys", {}).get(crs.name)
            env_plan = build_run_service_env(
                target_env=target_env,
                sanitizer=sanitizer,
                run_env_type=compose_env.get_env()["type"],
                crs_name=crs.name,
                module_name=module_name,
                run_id=run_id,
                cpuset=resource.cpuset,
                memory_limit=resource.memory,
                module_additional_env=module_config.additional_env,
                crs_additional_env=resource.additional_env,
                harness=target_env.get("harness"),
                include_fetch_dir=bool(fetch_dir),
                include_target_source=target._has_repo,
                include_snapshot_image=include_snapshot,
                llm_api_url=llm_url,
                llm_api_key=llm_key,
                scope=f"{crs.name}:run:{module_name}",
            )
            module_envs[service_name] = env_plan.effective_env
            warnings.extend(env_plan.warnings)
    context["module_envs"] = module_envs

    rendered = render_template(template_path, context)

    # Load as YAML and add common config for oss-crs-* services
    compose_data = yaml.safe_load(rendered)
    oss_crs_infra = crs_compose.config.oss_crs_infra
    common_config = {
        "cpuset": oss_crs_infra.cpuset,
        "mem_limit": oss_crs_infra.memory,
    }

    if "services" in compose_data:
        for service_name, service_config in compose_data["services"].items():
            if service_name.startswith("oss-crs"):
                service_config.update(common_config)

    return yaml.dump(compose_data, default_flow_style=False, sort_keys=False), warnings

from pathlib import Path
from jinja2 import Environment, FileSystemLoader, select_autoescape
from typing import TYPE_CHECKING, Optional
import os
import string
import secrets
import yaml

from ..config.crs import OSS_CRS_INFRA_PREFIX

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
        module_name = dockerfile[len(OSS_CRS_INFRA_PREFIX):]
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
) -> str:
    """Render the docker-compose file for building a target.

    Args:
        crs (CRS): CRS instance.
        target (Target): Target instance.
        build_config (BuildConfig): Build configuration.
        build_out_dir (Path): Output directory for the build.
        build_id (str): Build identifier.
        sanitizer (str): Sanitizer to use (e.g., "address", "undefined").

    Returns:
        str: Rendered docker-compose content as a string.
    """
    template_path = CUR_DIR / "build-target.docker-compose.yaml.j2"
    target_env = target.get_target_env()
    target_env["image"] = target_base_image
    # Override sanitizer with the explicitly-passed value
    target_env["sanitizer"] = sanitizer
    context = {
        "crs": {
            "name": crs.name,
            "path": str(crs.crs_path),
            "builder_dockerfile": str(crs.crs_path / build_config.dockerfile),
            "version": crs.config.version,
        },
        "additional_env": build_config.additional_env,
        "target": target_env,
        "build_out_dir": str(build_out_dir),
        "build_id": build_id,
        "crs_compose_env": crs.crs_compose_env.get_env(),
        "libCRS_path": str(LIBCRS_PATH),
    }
    return render_template(template_path, context)


def prepare_llm_context(
    tmp_docker_compose: "TmpDockerCompose", crs_compose: "CRSCompose"
) -> Optional[dict]:
    if crs_compose.llm.exists():
        # Prepare LiteLLM environment variables
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
            key = "sk-" + _generate_random_key(16)
            keys[crs.name] = key
            key_info[crs.name] = {
                "api_key": key,
                "required_llms": crs.config.required_llms,
                "llm_budget": crs.resource.llm_budget,
            }
        key_gen_request_path = tmp_docker_compose.dir / "key_gen_request.yaml"
        key_gen_request_path.write_text(
            yaml.dump(key_info, default_flow_style=False, sort_keys=False)
        )

        return {
            "litellm_master_key": "sk-" + _generate_random_key(16),
            "postgres_password": _generate_random_key(16),
            "litellm_config_path": crs_compose.config.llm_config.litellm_config,
            "api_keys": keys,
            "litellm_env": litellm_env,
            "key_gen_request_path": str(key_gen_request_path),
        }
    return None


def render_run_crs_compose_docker_compose(
    crs_compose: "CRSCompose",
    tmp_docker_compose: "TmpDockerCompose",
    crs_compose_name: str,
    target: "Target",
    run_id: str,
    build_id: str,
    sanitizer: str,
) -> str:
    template_path = CUR_DIR / "run-crs-compose.docker-compose.yaml.j2"

    # Exchange dir is shared across all CRSs
    exchange_dir = str(crs_compose.work_dir.get_exchange_dir(target, run_id, sanitizer))

    context = {
        "libCRS_path": str(LIBCRS_PATH),
        "crs_compose_name": crs_compose_name,
        "crs_list": crs_compose.crs_list,
        "crs_compose_env": crs_compose.crs_compose_env.get_env(),
        "target_env": target.get_target_env(),
        "target": target,
        "work_dir": crs_compose.work_dir,
        "run_id": run_id,
        "build_id": build_id,
        "sanitizer": sanitizer,
        "oss_crs_infra_root_path": str(OSS_CRS_ROOT_PATH / "oss-crs-infra"),
        "snapshot_image_tag": target.snapshot_image_tag or "",
        "resolve_dockerfile": _resolve_module_dockerfile,
        "exchange_dir": exchange_dir,
    }

    llm_context = prepare_llm_context(tmp_docker_compose, crs_compose)
    if llm_context:
        context["llm_context"] = llm_context

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

    return yaml.dump(compose_data, default_flow_style=False, sort_keys=False)

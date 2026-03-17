import json
import os
import re
import urllib.error
import urllib.request
import yaml
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from .config.crs_compose import LLMConfig
from .ui import TaskResult

if TYPE_CHECKING:
    from .crs import CRS


DEFAULT_LITELLM_CONFIG_PATH = (
    Path(__file__).resolve().parents[1] / "defaults" / "litellm" / "default-models.yaml"
)


class LLM:
    def __init__(self, llm_config: Optional[LLMConfig]):
        self.llm_config = llm_config
        self.mode = "disabled"
        self.model_check_enabled = False
        self.config = {}
        self.available_models: set[str] = set()

        self.external_url: Optional[str] = None
        self.external_url_env: Optional[str] = None
        self.external_key: Optional[str] = None
        self.external_key_env: Optional[str] = None

        if llm_config is None:
            return

        self.mode = llm_config.litellm.mode.value
        self.model_check_enabled = llm_config.litellm.model_check

        if self.mode == "internal":
            config_path = (
                llm_config.litellm.internal.config_path
                if llm_config.litellm.internal
                else None
            )
            if config_path is None:
                config_path = str(DEFAULT_LITELLM_CONFIG_PATH)
            litellm_path = Path(config_path).expanduser().resolve()
            if litellm_path.exists() and litellm_path.is_file():
                with open(litellm_path, "r") as f:
                    self.config = yaml.safe_load(f) or {}
                self.available_models = {
                    model.get("model_name", "")
                    for model in self.config.get("model_list", [])
                }
            else:
                # Defer hard failure to validation step for better error messages.
                self.config = {}
                self.available_models = set()
            return

        external = llm_config.litellm.external
        assert external is not None
        self.external_url = external.url
        self.external_url_env = external.url_env
        self.external_key = external.key
        self.external_key_env = external.key_env

    def exists(self) -> bool:
        return self.mode != "disabled"

    def extract_envs(self) -> list[str]:
        """Extract env var names referenced by internal LiteLLM config."""
        env_vars: set[str] = set()
        pattern = re.compile(r"os\.environ/(\w+)")

        model_list = self.config.get("model_list", [])
        for model in model_list:
            litellm_params = model.get("litellm_params", {})
            for value in litellm_params.values():
                if isinstance(value, str):
                    match = pattern.search(value)
                    if match:
                        env_vars.add(match.group(1))

        return sorted(env_vars)

    def get_crs_api_url(self) -> Optional[str]:
        if self.mode == "internal":
            return "http://litellm.oss-crs:4000"
        if self.mode == "external":
            if self.external_url_env:
                return os.environ.get(self.external_url_env)
            return self.external_url
        return None

    def get_crs_api_key(self) -> Optional[str]:
        if self.mode == "internal":
            return None
        if self.mode == "external":
            if self.external_key_env:
                return os.environ.get(self.external_key_env)
            return self.external_key
        return None

    def validate_required_envs(self) -> TaskResult:
        """Validate required env vars for selected LiteLLM mode."""
        if self.mode == "internal":
            required_envs = self.extract_envs()
            missing_envs = [env for env in required_envs if env not in os.environ]
            if missing_envs:
                msg = "The following environment variables required by the LiteLLM config are not set:\n"
                for env in missing_envs:
                    msg += f"  - {env}\n"
                return TaskResult(success=False, error=msg.strip())
            return TaskResult(success=True)

        if self.mode == "external":
            missing = []
            if self.external_url_env and self.external_url_env not in os.environ:
                missing.append(self.external_url_env)
            if self.external_key_env and self.external_key_env not in os.environ:
                missing.append(self.external_key_env)
            if missing:
                msg = "The following environment variables required by external LiteLLM are not set:\n"
                for env in missing:
                    msg += f"  - {env}\n"
                return TaskResult(success=False, error=msg.strip())
            if not self.get_crs_api_url():
                return TaskResult(success=False, error="External LiteLLM URL is empty")
            if not self.get_crs_api_key():
                return TaskResult(
                    success=False, error="External LiteLLM API key is empty"
                )
            return TaskResult(success=True)

        return TaskResult(success=True)

    def validate_required_llms(self, crs_list: list["CRS"]) -> TaskResult:
        """Validate required_llms in selected mode.

        required_llms is treated as minimum dependency list, not an allowlist.
        """
        required_llms: set[str] = set()
        for crs in crs_list:
            if crs.config.required_llms:
                required_llms.update(crs.config.required_llms)

        if not self.model_check_enabled:
            return TaskResult(success=True)

        if not required_llms:
            return TaskResult(success=True)

        if self.mode == "internal":
            if (
                self.llm_config
                and self.llm_config.litellm.internal
                and self.llm_config.litellm.internal.config_path is None
            ):
                if not DEFAULT_LITELLM_CONFIG_PATH.exists():
                    return TaskResult(
                        success=False,
                        error=(
                            "Default LiteLLM config not found: "
                            f"{DEFAULT_LITELLM_CONFIG_PATH}"
                        ),
                    )
            return self._validate_missing_models(
                required_llms,
                self.available_models,
                "the LiteLLM config",
            )

        if self.mode == "external":
            external_models = self._fetch_external_models()
            if external_models is None:
                return TaskResult(
                    success=False,
                    error=(
                        "Failed to fetch available models from external LiteLLM endpoint: "
                        f"{self.get_crs_api_url()}/models"
                    ),
                )
            return self._validate_missing_models(
                required_llms,
                external_models,
                "external LiteLLM endpoint",
            )

        return TaskResult(success=True)

    @staticmethod
    def _validate_missing_models(
        required_llms: set[str],
        available_models: set[str],
        source_name: str,
    ) -> TaskResult:
        missing_models = required_llms - available_models
        if not missing_models:
            return TaskResult(success=True)

        msg = (
            "The following LLMs are required by the CRS targets but not defined in "
            f"{source_name}:\n"
        )
        for model in sorted(missing_models):
            msg += f"  - {model}\n"
        return TaskResult(success=False, error=msg.strip())

    def _fetch_external_models(self) -> Optional[set[str]]:
        base_url = self.get_crs_api_url()
        api_key = self.get_crs_api_key()
        if not base_url or not api_key:
            return None

        headers = {"Authorization": f"Bearer {api_key}"}
        request = urllib.request.Request(
            f"{base_url.rstrip('/')}/models",
            headers=headers,
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (
            urllib.error.URLError,
            urllib.error.HTTPError,
            TimeoutError,
            json.JSONDecodeError,
        ):
            return None

        model_entries = payload.get("data", [])
        return {
            entry.get("id")
            for entry in model_entries
            if isinstance(entry, dict) and entry.get("id")
        }

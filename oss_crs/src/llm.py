import os
import re
import yaml
from pathlib import Path
from .ui import TaskResult
from .config.crs_compose import LLMConfig
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .crs import CRS


class LLM:
    def __init__(self, llm_config: Optional[LLMConfig]):
        if llm_config is None or llm_config.litellm_config is None:
            self.config = {}
        else:
            config_path = Path(llm_config.litellm_config).expanduser()
            with open(config_path, "r") as f:
                self.config = yaml.safe_load(f) or {}
        self.available_models = {
            model.get("model_name", "") for model in self.config.get("model_list", [])
        }

    def exists(self) -> bool:
        return self.config != {}

    def extract_envs(self) -> list[str]:
        """
        Extract environment variable names from the LiteLLM config file.
        """
        env_vars: set[str] = set()
        pattern = re.compile(r"os\.environ/(\w+)")

        model_list = self.config.get("model_list", [])
        for model in model_list:
            litellm_params = model.get("litellm_params", {})
            # Check all string fields in litellm_params for env var references
            for field, value in litellm_params.items():
                if isinstance(value, str):
                    match = pattern.search(value)
                    if match:
                        env_vars.add(match.group(1))

        return sorted(env_vars)

    def validate_required_envs(self) -> TaskResult:
        """
        Validate that all environment variables required by the LiteLLM config are set in the current environment.
        """
        required_envs = self.extract_envs()
        missing_envs = [env for env in required_envs if env not in os.environ]
        if missing_envs:
            msg = "The following environment variables required by the LiteLLM config are not set:\n"
            for env in missing_envs:
                msg += f"  - {env}\n"
            return TaskResult(success=False, error=msg.strip())
        return TaskResult(success=True)

    def validate_required_llms(self, crs_list: list["CRS"]) -> TaskResult:
        """
        Validate that all LLMs required by the CRS list are present in the LiteLLM config.
        """
        required_llms = set()
        for crs in crs_list:
            if crs.config.required_llms:
                required_llms.update(crs.config.required_llms)
        missing_models = required_llms - self.available_models
        if missing_models:
            msg = "The following LLMs are required by the CRS targets but not defined in the LiteLLM config:\n"
            for model in missing_models:
                msg += f"  - {model}\n"
            return TaskResult(success=False, error=msg.strip())
        return TaskResult(success=True)

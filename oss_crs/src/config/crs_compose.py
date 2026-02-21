import hashlib
import re
import json
from enum import Enum
from pathlib import Path
from typing import Optional
from pydantic import BaseModel, Field, field_validator, model_validator, HttpUrl
import yaml

from ..cpuset import parse_cpuset, map_cpuset, create_cpu_mapping


class CRSSource(BaseModel):
    """Source configuration for a CRS entry."""

    url: Optional[str] = None
    ref: Optional[str] = None
    local_path: Optional[str] = None

    @model_validator(mode="after")
    def validate_source(self):
        if self.local_path is not None:
            if self.url is not None or self.ref is not None:
                raise ValueError("'local_path' cannot be combined with 'url' or 'ref'")
        if self.url is not None:
            if self.local_path is not None:
                raise ValueError("'url' cannot be combined with 'local_path'")
            if self.ref is None:
                raise ValueError("'ref' is required when 'url' is provided")
        if self.url is None and self.local_path is None:
            raise ValueError("Either 'url' or 'local_path' must be provided")

        return self


def resolve_source_from_registry(crs_name: str) -> CRSSource:
    """Resolve CRS source from the registry directory.

    Looks up registry/<crs-name>.yaml and returns a CRSSource.
    """
    registry_dir = Path(__file__).resolve().parents[3] / "registry"
    registry_file = registry_dir / f"{crs_name}.yaml"
    if not registry_file.exists():
        raise ValueError(
            f"CRS '{crs_name}' has no 'source' and is not in the registry. "
            f"Expected registry file: {registry_file}"
        )
    with open(registry_file) as f:
        data = yaml.safe_load(f)
    source_data = data.get("source")
    if source_data is None:
        raise ValueError(
            f"Registry file {registry_file} is missing 'source' field"
        )
    return CRSSource(**source_data)


class ResourceConfig(BaseModel):
    """Base resource configuration with cpuset and memory."""

    cpuset: str
    memory: str
    llm_budget: Optional[int] = Field(default=None, gt=0)

    @field_validator("cpuset")
    @classmethod
    def validate_cpuset(cls, v: str) -> str:
        # Matches patterns like "0-3", "0,1,2,3", "0-3,5,7-9"
        pattern = r"^(\d+(-\d+)?)(,\d+(-\d+)?)*$"
        if not re.match(pattern, v):
            raise ValueError(
                f"Invalid cpuset format: '{v}'. "
                "Expected format like '0-3', '0,1,2,3', or '0-3,5,7-9'"
            )
        return v

    @field_validator("memory")
    @classmethod
    def validate_memory(cls, v: str) -> str:
        # Matches patterns like "8G", "16GB", "1024M", "2048MB"
        pattern = r"^\d+(\.\d+)?\s*(B|K|KB|M|MB|G|GB|T|TB)$"
        if not re.match(pattern, v, re.IGNORECASE):
            raise ValueError(
                f"Invalid memory format: '{v}'. "
                "Expected format like '8G', '16GB', '1024M', '2048MB'"
            )
        return v


class CRSEntry(ResourceConfig):
    """Configuration for a single CRS entry.

    When 'source' is omitted, it will be resolved from the registry
    during CRSComposeConfig.from_dict() using the CRS entry name.
    """

    source: Optional[CRSSource] = None
    additional_env: dict[str, str] = Field(default_factory=dict)

    @field_validator("additional_env", mode="before")
    @classmethod
    def coerce_none_env(cls, v):
        return v if v is not None else {}


class RunEnv(Enum):
    LOCAL = "local"
    AZURE = "azure"


class LLMConfig(BaseModel):
    """Configuration for LLMs used in CRS."""

    litellm_config: str

    @field_validator("litellm_config")
    @classmethod
    def validate_litellm_config(cls, v: str) -> str:
        path = Path(v).expanduser().resolve()
        if not path.exists():
            raise ValueError(f"litellm_config path does not exist: '{v}'")
        if not path.is_file():
            raise ValueError(f"litellm_config path is not a file: '{v}'")
        return str(path)


class CRSComposeConfig(BaseModel):
    """Root configuration for CRS Compose."""

    run_env: RunEnv
    docker_registry: str
    oss_crs_infra: ResourceConfig
    crs_entries: dict[str, CRSEntry] = Field(default_factory=dict)
    llm_config: Optional[LLMConfig] = None

    @field_validator("docker_registry")
    @classmethod
    def validate_docker_registry(cls, v: str) -> str:
        # TODO: Add more robust validation for docker registry URL if needed
        return v

    @field_validator("crs_entries")
    @classmethod
    def validate_crs_entries_keys(cls, v: dict[str, CRSEntry]) -> dict[str, CRSEntry]:
        uppercase_keys = [key for key in v.keys() if key != key.lower()]
        if uppercase_keys:
            raise ValueError(
                f"CRS entry names must be lowercase. "
                f"Invalid names: {', '.join(uppercase_keys)}"
            )
        return v

    @classmethod
    def from_yaml(cls, yaml_content: str) -> "CRSComposeConfig":
        """Parse CRS Compose config from YAML string."""
        data = yaml.safe_load(yaml_content)
        return cls.from_dict(data)

    @classmethod
    def from_yaml_file(cls, filepath: str) -> "CRSComposeConfig":
        """Parse CRS Compose config from YAML file."""
        with open(filepath, "r") as f:
            return cls.from_yaml(f.read())

    @classmethod
    def from_dict(cls, data: dict) -> "CRSComposeConfig":
        """Parse CRS Compose config from dictionary."""
        RUN_ENV = "run_env"
        DOCKER_REGISTRY = "docker_registry"
        OSS_CRS_INFRA = "oss_crs_infra"
        LLM_CONFIG = "llm_config"
        run_env = data.get(RUN_ENV)
        docker_registry = data.get(DOCKER_REGISTRY)
        oss_crs_infra = data.get(OSS_CRS_INFRA)
        llm_config = data.get(LLM_CONFIG)

        reserved_keys = {RUN_ENV, DOCKER_REGISTRY, OSS_CRS_INFRA, LLM_CONFIG}
        crs_entries = {
            key: value for key, value in data.items() if key not in reserved_keys
        }

        config = cls(
            run_env=run_env,  # Pydantic will convert string to enum automatically
            docker_registry=docker_registry,
            oss_crs_infra=oss_crs_infra,
            crs_entries=crs_entries,
            llm_config=llm_config,
        )

        # Resolve missing sources from registry
        for name, entry in config.crs_entries.items():
            if entry.source is None:
                entry.source = resolve_source_from_registry(name)

        return config

    def md5_hash(self) -> str:
        """Compute MD5 hash of the configuration."""
        config_json = self.model_dump(exclude_none=True, mode="json")
        config_json = remove_keys(config_json, ["cpuset", "llm_budget", "memory", "additional_env", "llm_config"])
        config_json = json.dumps(config_json)
        return hashlib.md5(config_json.encode()).hexdigest()[:12]

    def to_dict(self) -> dict:
        """Convert config to dictionary suitable for YAML output.

        Flattens crs_entries back to top-level keys (inverse of from_dict).
        Uses Pydantic's model_dump for serialization, then restructures for YAML format.
        """
        # Use Pydantic's model_dump for serialization
        # - mode='json': converts enums to values
        # - exclude_none=True: omits None fields
        # - exclude_defaults=True: omits fields with default values (e.g., empty additional_env)
        raw = self.model_dump(exclude_none=True, exclude_defaults=True, mode="json")

        # Extract and remove crs_entries - they'll be flattened to top-level
        crs_entries = raw.pop("crs_entries", {})

        # Flatten CRS entries to top-level keys
        for name, entry in crs_entries.items():
            raw[name] = entry

        return raw

    def to_yaml(self) -> str:
        """Serialize config to YAML string."""
        return yaml.dump(self.to_dict(), default_flow_style=False, sort_keys=False)

    def to_yaml_file(self, filepath: Path) -> None:
        """Write config to YAML file."""
        filepath.parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, "w") as f:
            f.write(self.to_yaml())

    def map_cpus(self, cpus_pool: str) -> None:
        """Map virtual cpusets to a real CPU pool in-place.

        Args:
            cpus_pool: Real CPU pool to map to (e.g., '20-31' or '1-3,5,8-11')

        Raises:
            ValueError: If cpus_pool format is invalid or pool is too small
        """
        # Validate cpus_pool format
        parse_cpuset(cpus_pool)

        # Collect all cpusets from config
        all_cpusets = [self.oss_crs_infra.cpuset]
        for entry in self.crs_entries.values():
            all_cpusets.append(entry.cpuset)

        # Create and apply mapping
        cpu_mapping = create_cpu_mapping(all_cpusets, cpus_pool)
        self.oss_crs_infra.cpuset = map_cpuset(self.oss_crs_infra.cpuset, cpu_mapping)
        for entry in self.crs_entries.values():
            entry.cpuset = map_cpuset(entry.cpuset, cpu_mapping)


def remove_keys(d, keys_to_remove):
    if isinstance(d, dict):
        return {
            k: remove_keys(v, keys_to_remove)
            for k, v in d.items()
            if k not in keys_to_remove
        }
    elif isinstance(d, list):
        return [remove_keys(item, keys_to_remove) for item in d]
    return d


class CRSComposeEnv:
    def __init__(self, run_env: RunEnv):
        self.run_env = run_env

    def get_env(self) -> dict[str, str]:
        return {"type": self.run_env.value}


if __name__ == "__main__":
    import sys

    config = CRSComposeConfig.from_yaml_file(sys.argv[1])
    print(config)

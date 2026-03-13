from pathlib import Path
from enum import Enum
from typing import Optional, Set

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from ..env_schema import validate_additional_env_keys
from .target import FuzzingEngine, TargetLangauge, TargetSanitizer, TargetArch

# Prefix for framework-provided infrastructure modules (e.g., "oss-crs-infra:default-builder")
OSS_CRS_INFRA_PREFIX = "oss-crs-infra:"


class PreparePhase(BaseModel):
    """Configuration for the prepare phase."""

    hcl: str

    @field_validator("hcl")
    @classmethod
    def validate_hcl(cls, v: str) -> str:
        if not v.endswith(".hcl"):
            raise ValueError("hcl file must have .hcl extension")
        return v


def _validate_dockerfile_value(v: Optional[str]) -> Optional[str]:
    """Validate a dockerfile field value.

    Accepts:
    - None (optional dockerfile)
    - Framework module reference: "oss-crs-infra:<module-name>"
    - File path containing "Dockerfile" or ending with ".Dockerfile"
    """
    if v is None:
        return None
    if v.startswith(OSS_CRS_INFRA_PREFIX):
        module_name = v[len(OSS_CRS_INFRA_PREFIX):]
        if not module_name:
            raise ValueError("oss-crs-infra: module name cannot be empty")
        return v
    if not v.endswith(".Dockerfile") and "Dockerfile" not in v:
        raise ValueError(
            "must be a valid Dockerfile path or oss-crs-infra:<module> reference"
        )
    return v


class BuildConfig(BaseModel):
    """Configuration for a single build step.

    A build with snapshot=True creates a snapshot image during the build phase.
    The dockerfile field is always required — use an oss-crs-infra:<module>
    reference for framework-provided builds (e.g., oss-crs-infra:default-builder).
    """

    name: str
    dockerfile: str
    outputs: list[str] = Field(default_factory=list)
    snapshot: bool = False
    additional_env: dict[str, str] = Field(default_factory=dict)

    @field_validator("dockerfile")
    @classmethod
    def validate_dockerfile(cls, v: str) -> str:
        result = _validate_dockerfile_value(v)
        assert result is not None
        return result

    @field_validator("outputs")
    @classmethod
    def validate_outputs(cls, v: list[str]) -> list[str]:
        for output in v:
            if ".." in output:
                raise ValueError(f"output path cannot contain '..': {output}")
        return v

    @field_validator("additional_env")
    @classmethod
    def validate_additional_env(cls, v: dict[str, str]) -> dict[str, str]:
        return validate_additional_env_keys(v, scope="BuildConfig.additional_env")


class TargetBuildPhase(BaseModel):
    """Configuration for the target build phase."""

    builds: list[BuildConfig] = Field(default_factory=[])

    @model_validator(mode="before")
    @classmethod
    def parse_builds(cls, data) -> dict:
        """Parse builds from raw dictionary data."""
        # Handle bare list format (backward compat)
        if isinstance(data, list):
            return {"builds": data}
        if isinstance(data, dict):
            return data
        raise ValueError("target_build_phase must be a list or dict")


class CRSRunPhaseModule(BaseModel):
    """Configuration for a single CRS run phase module.

    A module with run_snapshot=True uses the snapshot image from the build phase
    as its base image. The dockerfile can be:
    - Omitted: no service for this module (snapshot only used as base)
    - "oss-crs-infra:<module>": framework-provided service (e.g., default-builder)
    - A file path: CRS developer's custom service Dockerfile
    """

    dockerfile: Optional[str] = None
    run_snapshot: bool = False
    additional_env: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_dockerfile_requirement(self):
        """Ensure dockerfile is provided for non-snapshot modules."""
        if not self.run_snapshot and self.dockerfile is None:
            raise ValueError("dockerfile is required for non-snapshot modules")
        return self

    @field_validator("dockerfile")
    @classmethod
    def validate_dockerfile(cls, v: Optional[str]) -> Optional[str]:
        return _validate_dockerfile_value(v)

    @field_validator("additional_env")
    @classmethod
    def validate_additional_env(cls, v: dict[str, str]) -> dict[str, str]:
        return validate_additional_env_keys(v, scope="CRSRunPhaseModule.additional_env")


class CRSRunPhase(BaseModel):
    """Configuration for the CRS run phase."""

    modules: dict[str, CRSRunPhaseModule] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def parse_modules(cls, data: dict) -> dict:
        """Parse modules from raw dictionary data."""
        return {"modules": {k: v for k, v in data.items()}}


class TargetMode(Enum):
    FULL = "full"
    DELTA = "delta"


class SupportedTarget(BaseModel):
    """Configuration for supported targets."""

    mode: Set[TargetMode]
    language: Set[TargetLangauge]
    sanitizer: Set[TargetSanitizer]
    architecture: Set[TargetArch]
    fuzzing_engine: Set[FuzzingEngine] = Field(default_factory=lambda: set(FuzzingEngine))


class CRSType(Enum):
    BUG_FINDING = "bug-finding"
    BUG_FIXING = "bug-fixing"
    BUILDER = "builder"
    BUG_FIXING_ENSEMBLE = "bug-fixing-ensemble"


VALID_REQUIRED_INPUT_NAMES: set[str] = {"diff", "pov", "seed", "bug-candidate"}


class CRSConfig(BaseModel):
    """Root configuration for a CRS."""

    name: str
    type: Set[CRSType]
    version: str
    docker_registry: str = ""
    prepare_phase: Optional[PreparePhase] = None
    target_build_phase: Optional[TargetBuildPhase] = None

    crs_run_phase: CRSRunPhase
    supported_target: SupportedTarget

    required_llms: Optional[list[str]] = Field(default=None)
    required_inputs: Optional[list[str]] = Field(default=None)

    @property
    def is_builder(self) -> bool:
        return CRSType.BUILDER in self.type

    @property
    def is_bug_fixing(self) -> bool:
        return CRSType.BUG_FIXING in self.type or CRSType.BUG_FIXING_ENSEMBLE in self.type

    @property
    def is_bug_fixing_ensemble(self) -> bool:
        return CRSType.BUG_FIXING_ENSEMBLE in self.type

    @property
    def snapshot_builds(self) -> list[BuildConfig]:
        """Return build configs that produce snapshots."""
        if self.target_build_phase is None:
            return []
        return [b for b in self.target_build_phase.builds if b.snapshot]

    @property
    def has_snapshot(self) -> bool:
        """Check if this CRS has any builds that produce snapshots."""
        return len(self.snapshot_builds) > 0

    @property
    def has_builder_module(self) -> bool:
        """Check if this CRS has a run phase module that uses a snapshot."""
        return any(m.run_snapshot for m in self.crs_run_phase.modules.values())

    @field_validator("version")
    @classmethod
    def validate_version(cls, v: str) -> str:
        # TODO: Improve version validation if needed
        v = v.strip()  # Remove leading/trailing whitespace
        if not v:
            raise ValueError("version cannot be empty")
        return v

    @field_validator("docker_registry")
    @classmethod
    def validate_docker_registry(cls, v: str) -> str:
        # TODO: Improve docker_registry validation if needed
        return v.strip()

    @field_validator("required_llms")
    @classmethod
    def validate_required_llms(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        # TODO: Add specific validation for LLM names if needed
        if v is None:
            return v
        return list(set(v))

    @field_validator("required_inputs")
    @classmethod
    def validate_required_inputs(cls, v: Optional[list[str]]) -> Optional[list[str]]:
        if v is None:
            return v
        invalid = set(v) - VALID_REQUIRED_INPUT_NAMES
        if invalid:
            raise ValueError(
                f"Unknown input names: {sorted(invalid)}. "
                f"Valid names: {sorted(VALID_REQUIRED_INPUT_NAMES)}"
            )
        return list(set(v))  # Remove duplicates

    @classmethod
    def from_yaml(cls, yaml_content: str) -> "CRSConfig":
        """Parse CRS config from YAML string."""
        data = yaml.safe_load(yaml_content)
        return cls.from_dict(data)

    @classmethod
    def from_yaml_file(cls, filepath: Path) -> "CRSConfig":
        """Parse CRS config from YAML file."""
        with open(filepath.resolve(), "r") as f:
            return cls.from_yaml(f.read())

    @classmethod
    def from_dict(cls, data: dict) -> "CRSConfig":
        """Parse CRS config from dictionary."""
        return cls.model_validate(data)


if __name__ == "__main__":
    import sys

    config = CRSConfig.from_yaml_file(sys.argv[1])
    print(config)

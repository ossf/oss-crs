# SPDX-License-Identifier: MIT
"""Load CRS metadata from the repository registry."""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator


class CRSRegistryEntry(BaseModel):
    """User-facing metadata for a registered CRS."""

    name: str
    description: str | None = None
    types: list[str] = Field(default_factory=list, alias="type")

    @field_validator("types", mode="before")
    @classmethod
    def normalize_types(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            return value
        raise ValueError("CRS registry 'type' must be a string or list of strings")


def load_crs_registry(registry_dir: Path | None = None) -> list[CRSRegistryEntry]:
    """Load and validate top-level CRS registry manifests."""
    if registry_dir is None:
        registry_dir = Path(__file__).resolve().parents[3] / "registry"
    if not registry_dir.is_dir():
        raise ValueError(f"CRS registry directory does not exist: {registry_dir}")

    entries = []
    for registry_file in sorted(registry_dir.glob("*.yaml")):
        try:
            data = yaml.safe_load(registry_file.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ValueError(
                f"Failed to load CRS registry entry {registry_file}: {exc}"
            ) from exc
        if not isinstance(data, dict):
            raise ValueError(
                f"CRS registry entry must contain a YAML mapping: {registry_file}"
            )
        try:
            entries.append(CRSRegistryEntry.model_validate(data))
        except ValidationError as exc:
            raise ValueError(
                f"Invalid CRS registry entry {registry_file}: {exc}"
            ) from exc

    return sorted(entries, key=lambda entry: entry.name.casefold())

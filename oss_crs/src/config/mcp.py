# SPDX-License-Identifier: MIT
"""MCP server configuration and registry.

This module provides the schema for MCP server definitions (stored in
``registry/mcp/<name>.yaml``) and the registry that loads and validates them.
"""

from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, field_validator


class MCPServerConfig(BaseModel):
    """Configuration for a single MCP server.

    Each MCP server is defined by a YAML file in ``registry/mcp/``.
    """

    model_config = {"extra": "forbid"}

    name: str
    image: str
    url: str
    source_path: Optional[str] = None
    command: Optional[list[str]] = None
    transport: Optional[str] = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not v or not v.replace("_", "").isalnum():
            raise ValueError(
                "MCP server name must be a non-empty alphanumeric string "
                "with optional underscores"
            )
        return v

    @field_validator("source_path")
    @classmethod
    def validate_source_path(cls, v: Optional[str]) -> Optional[str]:
        if not v:
            return None
        if not v.startswith("/"):
            raise ValueError(
                f"MCP server source_path must be an absolute path, got: {v}"
            )
        return v

    @field_validator("transport")
    @classmethod
    def validate_transport(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if v not in ("http", "sse", "streamable-http"):
            raise ValueError(
                f"MCP server transport must be one of 'http', 'sse', "
                f"'streamable-http', got: {v}"
            )
        return v

    @field_validator("image")
    @classmethod
    def validate_image(cls, v: str) -> str:
        if not v:
            raise ValueError("MCP server image cannot be empty")
        return v

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("MCP server URL must start with http:// or https://")
        return v


class MCPRegistry:
    """Registry of available MCP servers.

    Loads MCP server definitions from ``registry/mcp/*.yaml`` and provides
    lookup by name.
    """

    def __init__(self, registry_dir: Path):
        self.registry_dir = registry_dir
        self._servers: dict[str, MCPServerConfig] = {}
        self._load()

    def _load(self) -> None:
        if not self.registry_dir.exists():
            return

        for yaml_file in sorted(self.registry_dir.glob("*.yaml")):
            with open(yaml_file) as f:
                data = yaml.safe_load(f)

            if not data:
                continue

            server = MCPServerConfig(**data)
            if server.name in self._servers:
                raise ValueError(
                    f"Duplicate MCP server name '{server.name}' found in {yaml_file}"
                )
            self._servers[server.name] = server

    def get(self, name: str) -> MCPServerConfig:
        """Get an MCP server configuration by name.

        Raises:
            ValueError: If the MCP server is not found in the registry.
        """
        if name not in self._servers:
            available = sorted(self._servers.keys())
            raise ValueError(
                f"MCP server '{name}' not found in registry. "
                f"Available MCP servers: {available}"
            )
        return self._servers[name]

    def list_servers(self) -> list[MCPServerConfig]:
        """List all registered MCP servers."""
        return list(self._servers.values())

    def __len__(self) -> int:
        return len(self._servers)

    def __contains__(self, name: str) -> bool:
        return name in self._servers


def get_default_registry_dir() -> Path:
    """Get the default MCP registry directory."""
    return Path(__file__).resolve().parents[3] / "registry" / "mcp"


def load_mcp_servers(
    mcp_server_names: Optional[list[str]],
    registry_dir: Optional[Path] = None,
) -> list[MCPServerConfig]:
    """Load MCP server configurations by name.

    Args:
        mcp_server_names: List of MCP server names to load.
        registry_dir: Optional path to the registry directory.
            Defaults to ``registry/mcp`` in the repo root.

    Returns:
        List of resolved MCPServerConfig objects.

    Raises:
        ValueError: If any MCP server name is not found in the registry.
    """
    if not mcp_server_names:
        return []

    if registry_dir is None:
        registry_dir = get_default_registry_dir()

    registry = MCPRegistry(registry_dir)

    servers = []
    for name in mcp_server_names:
        server = registry.get(name)
        servers.append(server)

    return servers

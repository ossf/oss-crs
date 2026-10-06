# SPDX-License-Identifier: MIT
"""MCP server configuration and registry.

This module provides the schema for MCP server definitions (stored in
``registry/mcp/<name>.yaml``) and the registry that loads and validates them.
"""

import subprocess
import re
import hashlib
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, field_validator


def _is_dockerfile_path(image: str) -> bool:
    """Distinguish explicit filesystem paths from Docker image references."""
    return (
        image.startswith(("/", "./", "../", "~/"))
        or Path(image).name.startswith("Dockerfile")
        or Path(image).suffix == ".dockerfile"
    )


def validate_mcp_server_name(name: str) -> str:
    """Validate names before they reach LiteLLM's MCP gateway."""
    if not re.fullmatch(r"[A-Za-z0-9_]+", name):
        raise ValueError(
            f"Invalid MCP server name {name!r}: use only ASCII letters, digits "
            "and underscores; hyphens are not allowed by LiteLLM"
        )
    return name


class MCPServerConfig(BaseModel):
    """Configuration for a single MCP server.

    Each MCP server is defined by a YAML file in ``registry/mcp/``.
    """

    name: str
    image: str
    url: str
    transport: str = "http"
    requires_source: bool = False
    command: Optional[list[str]] = None
    artifacts_path: Optional[str] = None

    @property
    def dockerfile_path(self) -> Optional[Path]:
        if not _is_dockerfile_path(self.image):
            return None
        path = Path(self.image).expanduser().resolve()
        if not path.is_file():
            raise ValueError(
                f"MCP server '{self.name}' Dockerfile does not exist or is not a file: {path}"
            )
        return path

    @property
    def resolved_image(self) -> str:
        """Image reference shared by preparation and Compose rendering."""
        dockerfile = self.dockerfile_path
        if dockerfile is None:
            return self.image
        digest = hashlib.sha256(str(dockerfile).encode()).hexdigest()[:12]
        return f"oss-crs-mcp/{self.name.lower()}:{digest}"

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        return validate_mcp_server_name(v)

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

    @field_validator("transport")
    @classmethod
    def validate_transport(cls, v: str) -> str:
        if v not in ("http", "sse", "streamable-http"):
            raise ValueError(
                "MCP server transport must be one of 'http', 'sse', "
                f"or 'streamable-http', got: {v!r}"
            )
        return "http" if v == "streamable-http" else v

    @field_validator("artifacts_path")
    @classmethod
    def validate_artifacts_path(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        if not v or not v.startswith("/"):
            raise ValueError(
                "MCP server artifacts_path must be an absolute container path "
                f"starting with '/': {v!r}"
            )
        if ":" in v:
            raise ValueError(f"MCP server artifacts_path must not contain ':': {v!r}")
        parts = v.split("/")
        if ".." in parts:
            raise ValueError(f"MCP server artifacts_path must not contain '..': {v!r}")
        return v


def mcp_service_name(crs_name: str, server_name: str) -> str:
    """Compose service name for a per-CRS MCP server instance."""
    return f"mcp-{crs_name}-{server_name}"


def mcp_gateway_name(crs_name: str, server_name: str) -> str:
    """LiteLLM gateway key for a per-CRS MCP server instance."""
    validate_mcp_server_name(server_name)
    return validate_mcp_server_name(f"{crs_name.replace('-', '_')}_{server_name}")


def mcp_instance_alias(crs_name: str, server_name: str) -> str:
    """DNS alias a per-CRS MCP server instance is reachable at."""
    return mcp_gateway_name(crs_name, server_name)


def remap_mcp_url_for_crs(url: str, crs_name: str, server_name: str) -> str:
    """Rewrite a registry MCP server URL to its per-CRS instance alias.

    Preserves scheme, port, and path; only the hostname is replaced with
    the namespaced alias so two CRSs using the same registry server do not
    collide on shared infra-only-network DNS.
    """
    from urllib.parse import urlparse, urlunparse

    parsed = urlparse(url)
    alias = mcp_instance_alias(crs_name, server_name)
    netloc = alias
    if parsed.port:
        netloc = f"{alias}:{parsed.port}"
    elif parsed.hostname and ":" in parsed.netloc and "@" not in parsed.netloc:
        # Preserve explicit port edge cases urlparse misses (should be rare).
        netloc = parsed.netloc.replace(parsed.hostname, alias, 1)
    return urlunparse(
        (
            parsed.scheme,
            netloc,
            parsed.path,
            parsed.params,
            parsed.query,
            parsed.fragment,
        )
    )


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
            if _is_dockerfile_path(server.image):
                path = Path(server.image).expanduser()
                if not path.is_absolute():
                    path = yaml_file.parent / path
                server.image = str(path.resolve())
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


def mcp_source_dir_for_image(image: str, infra_root: Path) -> Optional[Path]:
    """Locate the local build source for an MCP server image, if any.

    By convention an in-repo server image ``<prefix>/<dirname>[:tag]`` is
    built from ``oss-crs-infra/mcp/<dirname>/Dockerfile``. The directory is
    derived from the image reference (not the registry name, which may use
    different separators, e.g. ``ast_grep`` vs ``ast-grep``).

    Args:
        image: Docker image reference from the registry entry.
        infra_root: Path to ``oss-crs-infra``.

    Returns:
        The source directory if it contains a Dockerfile, else None (the
        image is remote-only and must be pulled).
    """
    ref = image.strip()
    # Strip :tag (only when it follows the last /, so registry ports survive).
    name = ref.rsplit("/", 1)[-1]
    if ":" in name:
        name = name.rsplit(":", 1)[0]
    if not name:
        return None
    candidate = infra_root / "mcp" / name
    if (candidate / "Dockerfile").is_file():
        return candidate
    return None


def ensure_mcp_server_image(
    server: MCPServerConfig,
    infra_root: Path,
    no_pull: bool = False,
) -> Optional[str]:
    """Make one MCP server image available locally.

    Servers with a local source are (re)built every time: Docker layer
    caching makes no-change rebuilds seconds-cheap while source changes
    rebuild correctly, so a stale ``latest`` tag can never linger the way
    skip-if-exists schemes allow. Remote-only images are pulled when missing.

    Args:
        server: Resolved registry entry.
        infra_root: Path to ``oss-crs-infra``.
        no_pull: Skip pulls (offline mode); missing remote images are an
            error instead.

    Returns:
        None on success, else an error message naming the server.
    """
    dockerfile = server.dockerfile_path
    source_dir = (
        dockerfile.parent
        if dockerfile is not None
        else mcp_source_dir_for_image(server.image, infra_root)
    )
    if source_dir is not None:
        command = ["docker", "build"]
        if dockerfile is not None:
            command.extend(["-f", str(dockerfile)])
        command.extend(["-t", server.resolved_image, str(source_dir)])
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return (
                f"Failed to build MCP server image '{server.image}' "
                f"for server '{server.name}' from {source_dir}:\n"
                f"{result.stderr}"
            )
        return None
    inspect = subprocess.run(
        ["docker", "image", "inspect", server.image],
        capture_output=True,
    )
    if inspect.returncode == 0:
        return None
    if no_pull:
        return (
            f"MCP server image '{server.image}' for server '{server.name}' "
            f"is not present locally and pulls are disabled (--no-pull)"
        )
    pull = subprocess.run(
        ["docker", "pull", server.image],
        capture_output=True,
        text=True,
    )
    if pull.returncode != 0:
        return (
            f"Failed to pull MCP server image '{server.image}' "
            f"for server '{server.name}':\n{pull.stderr}"
        )
    return None


def prepare_mcp_server_images(
    mcp_server_names: Optional[list[str]],
    registry_dir: Optional[Path] = None,
    infra_root: Optional[Path] = None,
    no_pull: bool = False,
) -> Optional[str]:
    """Build or pull every MCP server image a compose references.

    Scoped to the referenced servers only (never the whole registry) so
    prepare pays for what the run will use.

    Args:
        mcp_server_names: Compose ``mcp_servers`` names (None/empty = no-op).
        registry_dir: Registry directory (defaults to ``registry/mcp``).
        infra_root: ``oss-crs-infra`` root (derived from the registry dir
            by default).
        no_pull: Skip pulls for remote-only images.

    Returns:
        None on success, else an error message. Unknown server names raise
        ValueError (same as :func:`load_mcp_servers`).
    """
    servers = load_mcp_servers(mcp_server_names, registry_dir)
    if not servers:
        return None
    if registry_dir is None:
        registry_dir = get_default_registry_dir()
    if infra_root is None:
        infra_root = registry_dir.parent.parent / "oss-crs-infra"
    for server in servers:
        error = ensure_mcp_server_image(server, infra_root, no_pull=no_pull)
        if error is not None:
            return error
    return None

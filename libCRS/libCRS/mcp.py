# SPDX-License-Identifier: MIT
"""Client for the OSS-CRS MCP gateway.

The MCP gateway is the same LiteLLM proxy that fronts the LLM API: CRS
containers reach it at ``OSS_CRS_LLM_API_URL`` with their per-CRS key (read
from ``OSS_CRS_LLM_API_KEY_FILE``, falling back to ``OSS_CRS_LLM_API_KEY``).
No CRS source changes are needed to use it -- the framework injects both the
endpoint and the key, and this module discovers whatever MCP servers the
framework registered.

Discovery is two-level:

- ``list_tools`` hits ``GET /mcp-rest/tools/list``. Each entry carries its
  schema (``name``, ``description``, ``inputSchema``) plus ``mcp_info`` with
  the owning server's ``server_id``/``alias``.
- ``call_tool`` hits ``POST /mcp-rest/tools/call`` with ``server_id``,
  ``name`` and ``arguments``. ``server_id`` accepts a UUID, server name or
  alias; when omitted it is resolved from ``list_tools`` (the call fails if
  the tool name is ambiguous across servers).

Typical use from inside a CRS (or via the ``libCRS mcp`` CLI)::

    from libCRS.mcp import MCPClient

    client = MCPClient()
    if client.is_enabled():
        for tool in client.list_tools():
            print(tool["name"], "-", tool.get("description", ""))
        result = client.call_tool("semgrep_scan", {"code_files": [...]})
"""

import logging
import os
from pathlib import Path
from typing import Any

import requests as http_requests

logger = logging.getLogger(__name__)

# Env vars injected by the framework (see oss_crs/src/env_policy.py). The key
# file takes precedence: the framework mounts the per-CRS key as a secret file
# and only sets the direct env var as a fallback.
LLM_API_URL_ENV = "OSS_CRS_LLM_API_URL"
LLM_API_KEY_ENV = "OSS_CRS_LLM_API_KEY"
LLM_API_KEY_FILE_ENV = "OSS_CRS_LLM_API_KEY_FILE"

# LiteLLM MCP REST endpoints (no LiteLLM source changes required).
TOOLS_LIST_PATH = "/mcp-rest/tools/list"
TOOLS_CALL_PATH = "/mcp-rest/tools/call"

# Default request timeouts (seconds). Tool calls (e.g. scans) can take a
# while, so the call timeout is generous; override per call as needed.
LIST_TIMEOUT = 30
CALL_TIMEOUT = 300


class MCPGatewayError(RuntimeError):
    """Raised when the MCP gateway is unconfigured or a request fails."""


def _read_api_key(key: str | None = None, key_file: str | None = None) -> str:
    """Resolve the per-CRS LiteLLM key, preferring the secret file."""
    if key:
        return key
    key_file = (
        key_file if key_file is not None else os.environ.get(LLM_API_KEY_FILE_ENV)
    )
    if key_file:
        try:
            return Path(key_file).read_text().strip()
        except OSError as e:
            raise MCPGatewayError(
                f"Failed to read MCP gateway key file '{key_file}': {e}"
            ) from e
    return os.environ.get(LLM_API_KEY_ENV, "")


class MCPClient:
    """Thin client for the MCP gateway exposed to a CRS container.

    The client is inert until used: it can always be instantiated (even when
    the gateway is disabled and the env vars are absent), and :meth:`is_enabled`
    reports whether a usable endpoint + key are present.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        *,
        timeout: int = LIST_TIMEOUT,
    ):
        resolved_url = (
            base_url if base_url is not None else os.environ.get(LLM_API_URL_ENV, "")
        )
        self.base_url = resolved_url.rstrip("/")
        self.api_key = _read_api_key(api_key)
        self.timeout = timeout

    @classmethod
    def from_env(cls, *, timeout: int = LIST_TIMEOUT) -> "MCPClient":
        """Construct a client from the framework-injected environment."""
        return cls(timeout=timeout)

    def is_enabled(self) -> bool:
        """Whether a usable gateway endpoint and key are configured."""
        return bool(self.base_url and self.api_key)

    def headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        """Auth headers for a gateway request."""
        if not self.api_key:
            raise MCPGatewayError(
                f"Neither {LLM_API_KEY_FILE_ENV} nor {LLM_API_KEY_ENV} is set; "
                "MCP gateway is unavailable"
            )
        result = {"Authorization": f"Bearer {self.api_key}"}
        if extra:
            result.update(extra)
        return result

    def _url(self, path: str) -> str:
        if not self.base_url:
            raise MCPGatewayError(
                f"{LLM_API_URL_ENV} is not set; MCP gateway is unavailable"
            )
        return f"{self.base_url}/{path.lstrip('/')}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        timeout: int | None = None,
        **kwargs: Any,
    ) -> Any:
        """Make an authenticated gateway request, returning parsed JSON."""
        if not self.is_enabled():
            raise MCPGatewayError(
                f"MCP gateway is not configured; set {LLM_API_URL_ENV} and "
                f"{LLM_API_KEY_FILE_ENV} (or {LLM_API_KEY_ENV})"
            )
        try:
            response = http_requests.request(
                method,
                self._url(path),
                headers=self.headers(),
                timeout=timeout if timeout is not None else self.timeout,
                **kwargs,
            )
            response.raise_for_status()
        except http_requests.RequestException as e:
            raise MCPGatewayError(
                f"MCP gateway request {method} {path} failed: {e}"
            ) from e
        try:
            return response.json()
        except ValueError as e:
            raise MCPGatewayError(
                f"MCP gateway returned non-JSON response for {method} {path}: {e}"
            ) from e

    def list_tools(self, server: str | None = None) -> list[dict[str, Any]]:
        """List tools available to this key, optionally filtered to one server.

        Args:
            server: MCP server name, alias or id to filter by (optional).

        Returns:
            List of tool dicts with ``name``, ``description``,
            ``inputSchema`` and ``mcp_info`` (owning server's ``server_id`` /
            ``alias``).
        """
        params = {"mcp_server_name": server} if server else None
        payload = self._request("GET", TOOLS_LIST_PATH, params=params)
        tools = payload.get("tools", []) if isinstance(payload, dict) else []
        return tools if isinstance(tools, list) else []

    def available_names(self, server: str | None = None) -> list[str]:
        """Sorted tool names available to this key."""
        return sorted(
            t["name"]
            for t in self.list_tools(server)
            if isinstance(t, dict) and t.get("name")
        )

    def resolve_server(self, tool_name: str, server: str | None = None) -> str:
        """Resolve the ``server_id`` to call ``tool_name`` on.

        Uses the ``mcp_info`` attached by ``list_tools``. Fails if the tool is
        unknown or ambiguous without an explicit ``server`` hint.
        """
        candidates = [
            t
            for t in self.list_tools(server)
            if isinstance(t, dict) and t.get("name") == tool_name
        ]
        if not candidates:
            scope = f" on server '{server}'" if server else ""
            raise MCPGatewayError(
                f"Tool '{tool_name}' not found{scope}. "
                "Run `libCRS mcp list` to see available tools."
            )
        if len(candidates) > 1 and server is None:
            owners = sorted(
                {
                    (
                        (c.get("mcp_info") or {}).get("alias")
                        or (c.get("mcp_info") or {}).get("server_id")
                        or "?"
                    )
                    for c in candidates
                }
            )
            raise MCPGatewayError(
                f"Tool '{tool_name}' is provided by multiple servers "
                f"({', '.join(owners)}); pass --server to disambiguate."
            )
        info = candidates[0].get("mcp_info") or {}
        return info.get("alias") or info.get("server_id") or server or ""

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        server: str | None = None,
        *,
        timeout: int | None = None,
    ) -> Any:
        """Call an MCP tool through the gateway, returning the parsed result.

        Args:
            name: Tool name (as shown by ``list_tools``).
            arguments: Tool arguments (JSON object).
            server: Server name/alias/id hint; resolved automatically when the
                tool name is unambiguous.
            timeout: Request timeout in seconds (defaults to generous
                :data:`CALL_TIMEOUT`).

        Returns:
            Parsed JSON result (MCP ``CallToolResult`` object).
        """
        server_id = server or self.resolve_server(name)
        if not server_id:
            raise MCPGatewayError(f"Could not resolve a server for tool '{name}'.")
        return self._request(
            "POST",
            TOOLS_CALL_PATH,
            timeout=CALL_TIMEOUT if timeout is None else timeout,
            json={"server_id": server_id, "name": name, "arguments": arguments or {}},
        )

    def describe(self, name: str, server: str | None = None) -> dict[str, Any]:
        """Return the schema entry for one tool (for ``libCRS mcp describe``)."""
        for tool in self.list_tools(server):
            if isinstance(tool, dict) and tool.get("name") == name:
                return tool
        scope = f" on server '{server}'" if server else ""
        raise MCPGatewayError(
            f"Tool '{name}' not found{scope}. "
            "Run `libCRS mcp list` to see available tools."
        )

# SPDX-License-Identifier: MIT
"""oss-crs LiteLLM hook: MCP CLI usage injection.

Loaded by the LiteLLM proxy via ``litellm_settings.callbacks`` (written by
``oss_crs.src.templates.renderer.modify_litellm_config_for_mcp``). On every
request it appends a short system note teaching the agent the ``libCRS mcp``
CLI, through which all framework-registered MCP tools are reachable.

This module must stay self-contained (stdlib + litellm only): LiteLLM loads
it with ``importlib``/``exec_module`` from beside ``config.yaml``.
"""

from typing import Any, Dict, List, Optional

# litellm is intentionally not a project dependency: this module is loaded
# at runtime inside the LiteLLM sidecar
from litellm.integrations.custom_logger import CustomLogger  # type: ignore[import-not-found]

INSTRUCTION = (
    "[oss-crs] Additional analysis tools are available via the `libCRS mcp` "
    "CLI:\n"
    "- `libCRS mcp list` -- list available tools\n"
    "- `libCRS mcp describe <name>` -- show a tool's input schema\n"
    "- `libCRS mcp call <name> --args '<json>'` -- invoke a tool "
    "(add --json for raw output; large outputs are truncated, rerun with "
    "--max-output-chars 0 for the full output).\n"
    "You are strongly encouraged to make them part of your workflow -- they"
    "return targeted results far more cheaply than dumping whole files into"
    "context.\n"
)

# Substring guard for idempotency (also matched by user-supplied text that
# already documents the CLI, in which case injection is redundant).
_SENTINEL = "libCRS mcp"


def _system_has_instruction(system: Any) -> bool:
    if system is None:
        return False
    if isinstance(system, str):
        return _SENTINEL in system
    if isinstance(system, list):
        for block in system:
            if isinstance(block, dict):
                if _SENTINEL in str(block.get("text", "")):
                    return True
            elif isinstance(block, str) and _SENTINEL in block:
                return True
    return False


def _with_instruction(system: Any) -> Any:
    if system is None:
        return INSTRUCTION
    if isinstance(system, str):
        return system + "\n\n" + INSTRUCTION
    if isinstance(system, list):
        return [*system, {"type": "text", "text": INSTRUCTION}]
    return system


def _completion_system_content(messages: List[Any]) -> Optional[str]:
    for message in messages:
        if isinstance(message, dict) and message.get("role") == "system":
            content = message.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                texts = [
                    p.get("text", "")
                    for p in content
                    if isinstance(p, dict) and p.get("type") == "text"
                ]
                return "\n".join(texts)
    return None


class OssCrsMcpHook(CustomLogger):
    """Appends MCP CLI usage docs to the system prompt on every turn."""

    async def async_pre_call_hook(
        self,
        user_api_key_dict: Any,
        cache: Any,
        data: Dict[str, Any],
        call_type: Any,
    ) -> Optional[Dict[str, Any]]:
        """Hook for ``/chat/completions``-family requests."""
        try:
            if not isinstance(data, dict):
                return data
            messages = data.get("messages")
            if not isinstance(messages, list):
                return data
            if _system_has_instruction(data.get("system")) or _system_has_instruction(
                _completion_system_content(messages)
            ):
                return data
            if "system" in data:
                data["system"] = _with_instruction(data.get("system"))
                return data
            for message in messages:
                if isinstance(message, dict) and message.get("role") == "system":
                    content = message.get("content")
                    if isinstance(content, str):
                        message["content"] = content + "\n\n" + INSTRUCTION
                    elif isinstance(content, list):
                        content.append({"type": "text", "text": INSTRUCTION})
                    else:
                        message["content"] = INSTRUCTION
                    break
            else:
                messages.insert(0, {"role": "system", "content": INSTRUCTION})
            return data
        except Exception:
            return data

    async def async_pre_request_hook(
        self, model: str, messages: List[Any], kwargs: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Hook for ``/v1/messages``-family (Anthropic passthrough) requests.

        ``messages`` itself is not re-read downstream, so only ``kwargs``
        (notably ``system``) is modified.
        """
        try:
            if not isinstance(kwargs, dict):
                return kwargs
            if _system_has_instruction(kwargs.get("system")):
                return kwargs
            kwargs["system"] = _with_instruction(kwargs.get("system"))
            return kwargs
        except Exception:
            return kwargs


proxy_handler_instance = OssCrsMcpHook()

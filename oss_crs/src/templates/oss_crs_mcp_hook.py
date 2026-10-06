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

# LiteLLM is installed in the proxy image, not the host development environment.
from litellm.integrations.custom_logger import CustomLogger  # pyright: ignore[reportMissingImports]

INSTRUCTION = (
    "[oss-crs] Additional analysis tools are available via the `libCRS mcp` "
    "CLI (the MCP gateway is preconfigured for this CRS; no setup needed):\n"
    "- `libCRS mcp list` -- list available tools\n"
    "- `libCRS mcp describe <name>` -- show a tool's input schema\n"
    "- `libCRS mcp call <name> --args '<json>'` -- invoke a tool "
    "(add --json for raw output; large outputs are truncated, rerun with "
    "--max-output-chars 0 for the full output).\n"
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
        """Hook for ``/chat/completions``-family and ``/v1/messages`` requests."""
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
            if call_type == "anthropic_messages" or "system" in data:
                # Anthropic natively uses top-level ``system``, never a
                # ``role == "system"`` message, so create/extend
                # ``data["system"]`` even when the key is missing instead of
                # falling through to the chat message logic below.
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


proxy_handler_instance = OssCrsMcpHook()

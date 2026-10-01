# SPDX-License-Identifier: MIT
"""Tests for libCRS.mcp (MCP gateway client) and the `libCRS mcp` CLI."""

import json

import pytest

import libCRS.mcp as mcp_mod
from libCRS.mcp import MCPClient, MCPGatewayError


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests as http_requests

            raise http_requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _make_client(monkeypatch, key="sk-test"):
    monkeypatch.setenv("OSS_CRS_LLM_API_URL", "http://litellm:4000")
    monkeypatch.setenv("OSS_CRS_LLM_API_KEY", key)
    monkeypatch.delenv("OSS_CRS_LLM_API_KEY_FILE", raising=False)
    return MCPClient.from_env()


def _stub_request(monkeypatch, handler):
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return handler(method, url, kwargs)

    monkeypatch.setattr(mcp_mod.http_requests, "request", fake_request)
    return calls


def test_disabled_without_env(monkeypatch):
    monkeypatch.delenv("OSS_CRS_LLM_API_URL", raising=False)
    monkeypatch.delenv("OSS_CRS_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OSS_CRS_LLM_API_KEY_FILE", raising=False)
    client = MCPClient.from_env()
    assert not client.is_enabled()
    with pytest.raises(MCPGatewayError):
        client.list_tools()


def test_key_prefers_secret_file(monkeypatch, tmp_path):
    key_file = tmp_path / "api_key"
    key_file.write_text("sk-from-file\n")
    monkeypatch.setenv("OSS_CRS_LLM_API_URL", "http://litellm:4000")
    monkeypatch.setenv("OSS_CRS_LLM_API_KEY_FILE", str(key_file))
    monkeypatch.setenv("OSS_CRS_LLM_API_KEY", "sk-from-env")
    assert MCPClient.from_env().api_key == "sk-from-file"


def test_list_tools_parses_names(monkeypatch):
    client = _make_client(monkeypatch)
    payload = {
        "tools": [
            {
                "name": "b_tool",
                "description": "B",
                "mcp_info": {"alias": "semgrep", "server_id": "uuid-1"},
            },
            {
                "name": "a_tool",
                "description": "A",
                "mcp_info": {"alias": "semgrep", "server_id": "uuid-1"},
            },
        ]
    }
    calls = _stub_request(monkeypatch, lambda m, u, k: _FakeResponse(payload))
    assert client.available_names() == ["a_tool", "b_tool"]
    assert calls[0][0] == "GET"
    assert calls[0][1].endswith("/mcp-rest/tools/list")
    assert calls[0][2]["headers"]["Authorization"] == "Bearer sk-test"


def test_list_tools_server_filter(monkeypatch):
    client = _make_client(monkeypatch)
    calls = _stub_request(monkeypatch, lambda m, u, k: _FakeResponse({"tools": []}))
    client.list_tools("semgrep")
    assert calls[0][2]["params"] == {"mcp_server_name": "semgrep"}


def test_call_tool_posts_server_name_and_args(monkeypatch):
    client = _make_client(monkeypatch)
    seen = {}

    def handler(method, url, kwargs):
        if method == "GET":
            return _FakeResponse(
                {
                    "tools": [
                        {
                            "name": "semgrep_scan",
                            "mcp_info": {"alias": "semgrep", "server_id": "uuid-1"},
                        }
                    ]
                }
            )
        seen.update(kwargs.get("json", {}))
        return _FakeResponse({"content": [{"type": "text", "text": "ok"}]})

    _stub_request(monkeypatch, handler)
    out = client.call_tool("semgrep_scan", {"code_files": []})
    assert out == {"content": [{"type": "text", "text": "ok"}]}
    assert seen == {
        "server_id": "semgrep",
        "name": "semgrep_scan",
        "arguments": {"code_files": []},
    }


def test_call_tool_ambiguous_without_server(monkeypatch):
    client = _make_client(monkeypatch)
    _stub_request(
        monkeypatch,
        lambda m, u, k: _FakeResponse(
            {
                "tools": [
                    {"name": "scan", "mcp_info": {"alias": "a", "server_id": "1"}},
                    {"name": "scan", "mcp_info": {"alias": "b", "server_id": "2"}},
                ]
            }
        ),
    )
    with pytest.raises(MCPGatewayError, match="multiple servers"):
        client.call_tool("scan", {})


def test_call_tool_unknown(monkeypatch):
    client = _make_client(monkeypatch)
    _stub_request(monkeypatch, lambda m, u, k: _FakeResponse({"tools": []}))
    with pytest.raises(MCPGatewayError, match="not found"):
        client.call_tool("nope", {})


def test_http_error_surfaces_as_gateway_error(monkeypatch):
    client = _make_client(monkeypatch)
    _stub_request(monkeypatch, lambda m, u, k: _FakeResponse({}, status=500))
    with pytest.raises(MCPGatewayError, match="failed"):
        client.list_tools()


def test_extract_result_text_prefers_text_blocks():
    from libCRS.cli.main import _extract_result_text

    result = {
        "content": [{"type": "text", "text": "hello"}, {"type": "image", "data": "x"}]
    }
    text = _extract_result_text(result)
    assert "hello" in text
    assert '"image"' in text or "image" in text


def test_extract_result_text_falls_back_to_structured():
    from libCRS.cli.main import _extract_result_text

    assert _extract_result_text({"structuredContent": {"a": 1}}) == (
        json.dumps({"a": 1}, indent=2)
    )
    assert _extract_result_text("plain") == "plain"


def test_print_truncated(capsys):
    from libCRS.cli.main import _print_truncated

    _print_truncated("abcdef", 4)
    out = capsys.readouterr().out
    assert out.startswith("abcd")
    assert "truncated" in out
    _print_truncated("abcdef", 0)
    assert capsys.readouterr().out == "abcdef\n"

# SPDX-License-Identifier: MIT
"""Tests for MCP server configuration and registry."""

from pathlib import Path

import pytest
import yaml

from oss_crs.src.config.mcp import (
    MCPRegistry,
    MCPServerConfig,
    get_default_registry_dir,
    load_mcp_servers,
)


class TestMCPServerConfig:
    def test_valid_config(self):
        config = MCPServerConfig(
            name="ripgrep",
            image="ghcr.io/oss-crs/mcp-ripgrep:latest",
            url="http://ripgrep:8000/mcp",
            source_path="/OSS_CRS_TARGET_SOURCE",
        )
        assert config.name == "ripgrep"
        assert config.source_path == "/OSS_CRS_TARGET_SOURCE"

    def test_source_path_defaults_none(self):
        config = MCPServerConfig(
            name="test",
            image="some-image",
            url="http://test:8000/mcp",
        )
        assert config.source_path is None

    def test_source_path_empty_string_is_none(self):
        # empty string means "do not bind mount source"
        config = MCPServerConfig(
            name="test",
            image="some-image",
            url="http://test:8000/mcp",
            source_path="",
        )
        assert config.source_path is None

    def test_source_path_relative_rejected(self):
        with pytest.raises(ValueError, match="absolute"):
            MCPServerConfig(
                name="test",
                image="some-image",
                url="http://test:8000/mcp",
                source_path="src",
            )

    def test_unknown_field_rejected(self):
        # extra="forbid" catches stale/unknown registry fields at load time
        with pytest.raises(ValueError):
            MCPServerConfig(
                name="test",
                image="some-image",
                url="http://test:8000/mcp",
                requires_source=True,
            )

    def test_command_field(self):
        config = MCPServerConfig(
            name="semgrep",
            image="semgrep/semgrep",
            url="http://semgrep:8000/mcp",
            command=["semgrep", "mcp"],
        )
        assert config.command == ["semgrep", "mcp"]

    def test_command_defaults_none(self):
        config = MCPServerConfig(
            name="test",
            image="some-image",
            url="http://test:8000/mcp",
        )
        assert config.command is None

    def test_invalid_name_rejected(self):
        with pytest.raises(ValueError):
            MCPServerConfig(
                name="invalid name with spaces",
                image="some-image",
                url="http://test:8000/mcp",
            )

    def test_hyphenated_name_rejected(self):
        with pytest.raises(ValueError, match="alphanumeric"):
            MCPServerConfig(
                name="ast-grep",
                image="some-image",
                url="http://ast-grep:8000/mcp",
            )

    def test_underscore_name_accepted(self):
        config = MCPServerConfig(
            name="ast_grep",
            image="some-image",
            url="http://ast_grep:8000/mcp",
        )
        assert config.name == "ast_grep"

    def test_invalid_url_rejected(self):
        with pytest.raises(ValueError):
            MCPServerConfig(
                name="test",
                image="some-image",
                url="ftp://test:8000/mcp",
            )

    def test_empty_image_rejected(self):
        with pytest.raises(ValueError):
            MCPServerConfig(
                name="test",
                image="",
                url="http://test:8000/mcp",
            )


class TestMCPRegistry:
    def _write_registry(self, tmp_path: Path, servers: list) -> Path:
        registry_dir = tmp_path / "registry" / "mcp"
        registry_dir.mkdir(parents=True)
        for server in servers:
            name = server["name"]
            with open(registry_dir / f"{name}.yaml", "w") as f:
                yaml.dump(server, f)
        return registry_dir

    def test_load_and_get(self, tmp_path):
        registry_dir = self._write_registry(
            tmp_path,
            [
                {
                    "name": "ripgrep",
                    "image": "ghcr.io/oss-crs/mcp-ripgrep:latest",
                    "url": "http://ripgrep:8000/mcp",
                    "source_path": "/OSS_CRS_TARGET_SOURCE",
                }
            ],
        )
        registry = MCPRegistry(registry_dir)
        assert len(registry) == 1
        assert "ripgrep" in registry

        config = registry.get("ripgrep")
        assert config.name == "ripgrep"
        assert config.source_path == "/OSS_CRS_TARGET_SOURCE"

    def test_get_missing_server_raises(self, tmp_path):
        registry_dir = self._write_registry(tmp_path, [])
        registry = MCPRegistry(registry_dir)
        with pytest.raises(ValueError, match="not found in registry"):
            registry.get("nonexistent")

    def test_duplicate_names_rejected(self, tmp_path):
        registry_dir = tmp_path / "registry" / "mcp"
        registry_dir.mkdir(parents=True)
        for filename in ("dup.yaml", "dup-2.yaml"):
            with open(registry_dir / filename, "w") as f:
                yaml.dump(
                    {
                        "name": "dup",
                        "image": "some-image",
                        "url": "http://dup:8000/mcp",
                    },
                    f,
                )
        with pytest.raises(ValueError, match="Duplicate MCP server name"):
            MCPRegistry(registry_dir)

    def test_empty_registry_dir(self, tmp_path):
        registry = MCPRegistry(tmp_path / "nonexistent")
        assert len(registry) == 0

    def test_list_servers(self, tmp_path):
        registry_dir = self._write_registry(
            tmp_path,
            [
                {
                    "name": "a",
                    "image": "img-a",
                    "url": "http://a:8000/mcp",
                },
                {
                    "name": "b",
                    "image": "img-b",
                    "url": "http://b:8000/mcp",
                },
            ],
        )
        registry = MCPRegistry(registry_dir)
        servers = registry.list_servers()
        assert len(servers) == 2
        assert {s.name for s in servers} == {"a", "b"}


class TestLoadMCPServers:
    def test_load_none_returns_empty(self):
        assert load_mcp_servers(None) == []

    def test_load_empty_list_returns_empty(self):
        assert load_mcp_servers([]) == []

    def test_load_resolves_servers(self, tmp_path):
        registry_dir = tmp_path / "registry" / "mcp"
        registry_dir.mkdir(parents=True)
        with open(registry_dir / "ripgrep.yaml", "w") as f:
            yaml.dump(
                {
                    "name": "ripgrep",
                    "image": "ghcr.io/oss-crs/mcp-ripgrep:latest",
                    "url": "http://ripgrep:8000/mcp",
                    "source_path": "/OSS_CRS_TARGET_SOURCE",
                },
                f,
            )

        servers = load_mcp_servers(["ripgrep"], registry_dir)
        assert len(servers) == 1
        assert servers[0].name == "ripgrep"

    def test_load_missing_server_raises(self, tmp_path):
        with pytest.raises(ValueError, match="not found in registry"):
            load_mcp_servers(["missing"], tmp_path)


class TestDefaultRegistryDir:
    def test_default_registry_dir_exists_check(self):
        registry_dir = get_default_registry_dir()
        # Just verify the path is constructed correctly
        assert registry_dir.name == "mcp"
        assert registry_dir.parent.name == "registry"


class TestModifyLitellmConfigForMcp:
    """Test that modify_litellm_config_for_mcp produces a valid LiteLLM config."""

    def _setup(self, tmp_path):
        from oss_crs.src.templates.renderer import modify_litellm_config_for_mcp

        litellm_file = tmp_path / "litellm.yaml"
        litellm_file.write_text("model_list: []\n")
        output_file = tmp_path / "litellm-config-mcp.yaml"
        return litellm_file, output_file, modify_litellm_config_for_mcp

    def test_modifies_litellm_config(self, tmp_path):
        litellm_file, output_file, fn = self._setup(tmp_path)
        mcp_servers = [
            MCPServerConfig(
                name="ripgrep",
                image="ghcr.io/oss-crs/mcp-ripgrep:latest",
                url="http://ripgrep:8000/mcp",
            )
        ]
        result_path, hook_path = fn(litellm_file, mcp_servers, output_file)

        assert Path(result_path).exists()
        with open(output_file) as f:
            config = yaml.safe_load(f)
        # auto_register_tools is not a LiteLLM setting and must not be emitted
        assert "auto_register_tools" not in config.get("general_settings", {})
        assert "ripgrep" in config["mcp_servers"]
        entry = config["mcp_servers"]["ripgrep"]
        assert entry["url"] == "http://ripgrep:8000/mcp"
        # Streamable HTTP: LiteLLM defaults to SSE
        assert entry["transport"] == "http"
        # Per-CRS virtual keys need gateway access without per-key grants
        assert entry["allow_all_keys"] is True
        # Prompt hook registration (merged, pointing at the instance)
        callbacks = config["litellm_settings"]["callbacks"]
        assert "oss_crs_mcp_hook.proxy_handler_instance" in callbacks
        # Hook module is copied next to the generated config
        assert Path(hook_path).exists()
        assert hook_path.parent == output_file.parent
        assert "proxy_handler_instance" in Path(hook_path).read_text()

    def test_preserves_existing_config(self, tmp_path):
        litellm_file, output_file, fn = self._setup(tmp_path)
        mcp_servers = [
            MCPServerConfig(
                name="test",
                image="some-image",
                url="http://test:8000/mcp",
            )
        ]
        fn(litellm_file, mcp_servers, output_file)

        # Original file should not have MCP servers
        with open(litellm_file) as f:
            original_config = yaml.safe_load(f)
        assert "mcp_servers" not in original_config
        assert "general_settings" not in original_config

    def test_merges_with_existing_litellm_settings(self, tmp_path):
        from oss_crs.src.templates.renderer import MCP_HOOK_CALLBACK

        litellm_file = tmp_path / "litellm.yaml"
        output_file = tmp_path / "litellm-config-mcp.yaml"
        fn = self._setup(tmp_path)[2]
        # _setup writes a minimal litellm.yaml; replace it with settings to merge
        litellm_file.write_text(
            "model_list: []\n"
            "litellm_settings:\n"
            "  ssl_verify: false\n"
            "  callbacks: [other_module.instance]\n"
        )
        from oss_crs.src.config.mcp import MCPServerConfig as Cfg

        fn(
            litellm_file,
            [Cfg(name="s", image="img", url="http://s:8000/mcp")],
            output_file,
        )
        with open(output_file) as f:
            config = yaml.safe_load(f)
        # Existing settings preserved, hook appended (not replacing)
        assert config["litellm_settings"]["ssl_verify"] is False
        assert config["litellm_settings"]["callbacks"] == [
            "other_module.instance",
            MCP_HOOK_CALLBACK,
        ]


class TestMcpPromptHook:
    """Tests for the LiteLLM prompt hook (litellm stubbed out)."""

    @staticmethod
    def _load_hook():
        import importlib.util
        import sys
        import types

        if "litellm" not in sys.modules:
            litellm_pkg = types.ModuleType("litellm")
            integrations = types.ModuleType("litellm.integrations")
            custom_logger = types.ModuleType("litellm.integrations.custom_logger")

            class CustomLogger:  # minimal stand-in for isinstance checks
                pass

            custom_logger.CustomLogger = CustomLogger
            litellm_pkg.integrations = integrations
            integrations.custom_logger = custom_logger
            sys.modules["litellm"] = litellm_pkg
            sys.modules["litellm.integrations"] = integrations
            sys.modules["litellm.integrations.custom_logger"] = custom_logger

        from oss_crs.src.templates import renderer

        hook_src = renderer._hook_source_path()
        spec = importlib.util.spec_from_file_location(
            "oss_crs_mcp_hook_under_test", hook_src
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_hook_module_defines_instance(self):
        hook = self._load_hook()
        assert isinstance(hook.proxy_handler_instance, hook.OssCrsMcpHook)
        assert "libCRS mcp" in hook.INSTRUCTION

    def test_pre_call_hook_injects_on_first_turn(self):
        import asyncio

        hook = self._load_hook()
        inst = hook.OssCrsMcpHook()
        data = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
        out = asyncio.run(inst.async_pre_call_hook(None, None, data, "completion"))
        assert out["messages"][0]["role"] == "system"
        assert "libCRS mcp" in out["messages"][0]["content"]

    def test_pre_call_hook_injects_with_history(self):
        import asyncio

        hook = self._load_hook()
        inst = hook.OssCrsMcpHook()
        data = {
            "model": "m",
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
                {"role": "user", "content": "again"},
            ],
        }
        out = asyncio.run(inst.async_pre_call_hook(None, None, data, "completion"))
        assert out["messages"][0]["role"] == "system"
        assert "libCRS mcp" in out["messages"][0]["content"]

    def test_pre_call_hook_idempotent(self):
        import asyncio

        hook = self._load_hook()
        inst = hook.OssCrsMcpHook()
        data = {
            "model": "m",
            "messages": [{"role": "user", "content": "hi"}],
            "system": "existing libCRS mcp docs",
        }
        out = asyncio.run(inst.async_pre_call_hook(None, None, data, "completion"))
        assert out["system"] == "existing libCRS mcp docs"

    def test_pre_request_hook_appends_system(self):
        import asyncio

        hook = self._load_hook()
        inst = hook.OssCrsMcpHook()
        kwargs = {"system": "base instructions"}
        out = asyncio.run(
            inst.async_pre_request_hook(
                "m", [{"role": "user", "content": "hi"}], kwargs
            )
        )
        assert "base instructions" in out["system"]
        assert "libCRS mcp" in out["system"]

    def test_pre_request_hook_injects_with_history(self):
        import asyncio

        hook = self._load_hook()
        inst = hook.OssCrsMcpHook()
        kwargs = {}
        messages = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
            {"role": "user", "content": "c"},
        ]
        out = asyncio.run(inst.async_pre_request_hook("m", messages, kwargs))
        assert "libCRS mcp" in out["system"]

    def test_hooks_fail_open(self):
        import asyncio

        hook = self._load_hook()
        inst = hook.OssCrsMcpHook()
        assert asyncio.run(inst.async_pre_call_hook(None, None, None, "x")) is None
        kwargs = {"system": "s"}
        assert asyncio.run(inst.async_pre_request_hook("m", None, kwargs)) is kwargs


class TestComposeSchemaWithMcpServers:
    """Test that the compose schema handles mcp_servers correctly."""

    def _base_compose_data(self, tmp_path):
        litellm_file = tmp_path / "litellm.yaml"
        litellm_file.write_text("model_list: []\n")
        return {
            "run_env": "local",
            "docker_registry": "local",
            "oss_crs_infra": {"cpuset": "0-1", "memory": "8G"},
            "crs-claude-code": {
                "cpuset": "2-7",
                "memory": "16G",
                "llm_budget": 10,
            },
            "llm_config": {
                "litellm": {
                    "mode": "internal",
                    "internal": {"config_path": str(litellm_file)},
                }
            },
        }

    def test_compose_with_mcp_servers(self, tmp_path):
        from oss_crs.src.config.crs_compose import CRSComposeConfig

        data = self._base_compose_data(tmp_path)
        data["mcp_servers"] = ["ripgrep", "tree-sitter"]

        config = CRSComposeConfig.from_dict(data)
        assert config.mcp_servers == ["ripgrep", "tree-sitter"]

    def test_compose_without_mcp_servers(self, tmp_path):
        from oss_crs.src.config.crs_compose import CRSComposeConfig

        data = self._base_compose_data(tmp_path)
        config = CRSComposeConfig.from_dict(data)
        assert config.mcp_servers is None

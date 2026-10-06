# SPDX-License-Identifier: MIT
"""Tests for MCP server configuration and registry."""

from pathlib import Path

import pytest
import yaml

from oss_crs.src.config.mcp import (
    MCPRegistry,
    MCPServerConfig,
    ensure_mcp_server_image,
    get_default_registry_dir,
    load_mcp_servers,
    mcp_source_dir_for_image,
    prepare_mcp_server_images,
)


class TestMCPServerConfig:
    def test_valid_config(self):
        config = MCPServerConfig(
            name="ripgrep",
            image="ghcr.io/oss-crs/mcp-ripgrep:latest",
            url="http://ripgrep:8000/mcp",
            requires_source=True,
        )
        assert config.name == "ripgrep"
        assert config.requires_source is True

    def test_requires_source_defaults_false(self):
        config = MCPServerConfig(
            name="test",
            image="some-image",
            url="http://test:8000/mcp",
        )
        assert config.requires_source is False

    def test_transport_defaults_http(self):
        config = MCPServerConfig(name="test", image="img", url="http://test/mcp")
        assert config.transport == "http"

    @pytest.mark.parametrize(
        ("transport", "expected"),
        [("http", "http"), ("sse", "sse"), ("streamable-http", "http")],
    )
    def test_transport_validated_and_normalized(self, transport, expected):
        config = MCPServerConfig(
            name="test", image="img", url="http://test/mcp", transport=transport
        )
        assert config.transport == expected

    @pytest.mark.parametrize("transport", ["", "stdio", "HTTP", None, 42])
    def test_invalid_transport_rejected(self, transport):
        with pytest.raises(ValueError, match="transport"):
            MCPServerConfig(
                name="test", image="img", url="http://test/mcp", transport=transport
            )

    def test_command_field(self):
        config = MCPServerConfig(
            name="semgrep",
            image="semgrep/semgrep",
            url="http://semgrep:8000/mcp",
            requires_source=True,
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
                    "requires_source": True,
                }
            ],
        )
        registry = MCPRegistry(registry_dir)
        assert len(registry) == 1
        assert "ripgrep" in registry

        config = registry.get("ripgrep")
        assert config.name == "ripgrep"
        assert config.requires_source is True

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
                    "requires_source": True,
                },
                f,
            )

        servers = load_mcp_servers(["ripgrep"], registry_dir)
        assert len(servers) == 1
        assert servers[0].name == "ripgrep"

    def test_load_missing_server_raises(self, tmp_path):
        with pytest.raises(ValueError, match="not found in registry"):
            load_mcp_servers(["missing"], tmp_path)


@pytest.mark.parametrize(
    "filename", ["Dockerfile", "Dockerfile.custom", "custom.recipe"]
)
def test_explicit_dockerfile_build_uses_parent_context(tmp_path, monkeypatch, filename):
    import oss_crs.src.config.mcp as mcp_mod
    from types import SimpleNamespace

    dockerfile = tmp_path / "source with spaces" / filename
    dockerfile.parent.mkdir()
    dockerfile.write_text("FROM scratch\n")
    server = MCPServerConfig(
        name="TestServer", image=str(dockerfile), url="http://test/mcp"
    )
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(mcp_mod.subprocess, "run", run)
    assert ensure_mcp_server_image(server, tmp_path, no_pull=True) is None
    assert calls == [
        [
            "docker",
            "build",
            "-f",
            str(dockerfile),
            "-t",
            server.resolved_image,
            str(dockerfile.parent),
        ]
    ]
    assert server.resolved_image.startswith("oss-crs-mcp/testserver:")
    assert server.resolved_image == server.model_copy().resolved_image


def test_registry_resolves_relative_dockerfile_from_yaml_directory(tmp_path):
    registry = tmp_path / "registry"
    registry.mkdir()
    dockerfile = tmp_path / "server" / "Dockerfile"
    dockerfile.parent.mkdir()
    dockerfile.write_text("FROM scratch\n")
    (registry / "test.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "test",
                "image": "../server/Dockerfile",
                "url": "http://test/mcp",
            }
        )
    )
    server = MCPRegistry(registry).get("test")
    assert server.dockerfile_path == dockerfile
    other = MCPServerConfig(
        name="test", image=str(tmp_path / "other" / "Dockerfile"), url="http://test/mcp"
    )
    with pytest.raises(ValueError, match="Dockerfile.*not.*file"):
        _ = other.resolved_image


def test_missing_dockerfile_fails_without_pulling(tmp_path, monkeypatch):
    import oss_crs.src.config.mcp as mcp_mod

    server = MCPServerConfig(
        name="test", image=str(tmp_path / "Dockerfile"), url="http://test/mcp"
    )
    monkeypatch.setattr(
        mcp_mod.subprocess, "run", lambda *a, **k: pytest.fail("must not pull")
    )
    with pytest.raises(ValueError, match="Dockerfile.*not.*file"):
        ensure_mcp_server_image(server, tmp_path)


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
                requires_source=True,
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

    @pytest.mark.parametrize(
        ("transport", "expected"),
        [("http", "http"), ("sse", "sse"), ("streamable-http", "http")],
    )
    @pytest.mark.parametrize("namespaced", [False, True])
    def test_renders_server_transport(self, tmp_path, transport, expected, namespaced):
        litellm_file, output_file, fn = self._setup(tmp_path)
        server = MCPServerConfig(
            name="test", image="img", url="http://test/mcp", transport=transport
        )
        fn(litellm_file, [("crs-a", server)] if namespaced else [server], output_file)
        config = yaml.safe_load(output_file.read_text())
        key = "crs_a_test" if namespaced else "test"
        assert config["mcp_servers"][key]["transport"] == expected

    def test_preserves_existing_config(self, tmp_path):
        litellm_file, output_file, fn = self._setup(tmp_path)
        mcp_servers = [
            MCPServerConfig(
                name="test",
                image="some-image",
                url="http://test:8000/mcp",
                requires_source=False,
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

    def test_per_crs_tuples_namespaced(self, tmp_path):
        litellm_file, output_file, fn = self._setup(tmp_path)
        from oss_crs.src.config.mcp import MCPServerConfig as Cfg

        servers = [
            Cfg(name="ast_grep", image="img", url="http://ast_grep:3101/sse"),
        ]
        fn(litellm_file, [("crs-a", servers[0])], output_file)
        with open(output_file) as f:
            config = yaml.safe_load(f)
        assert "crs_a_ast_grep" in config["mcp_servers"]
        assert (
            config["mcp_servers"]["crs_a_ast_grep"]["url"]
            == "http://crs_a_ast_grep:3101/sse"
        )


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

    def test_pre_call_hook_anthropic_messages_appends_system(self):
        import asyncio

        hook = self._load_hook()
        inst = hook.OssCrsMcpHook()
        data = {
            "model": "m",
            "messages": [{"role": "user", "content": "hi"}],
            "system": "base instructions",
        }
        out = asyncio.run(
            inst.async_pre_call_hook(None, None, data, "anthropic_messages")
        )
        assert "base instructions" in out["system"]
        assert "libCRS mcp" in out["system"]
        # Anthropic messages must stay untouched (no system role inserted).
        assert all(m.get("role") != "system" for m in out["messages"])

    def test_pre_call_hook_anthropic_messages_injects_when_missing(self):
        import asyncio

        hook = self._load_hook()
        inst = hook.OssCrsMcpHook()
        data = {
            "model": "m",
            "messages": [
                {"role": "user", "content": "a"},
                {"role": "assistant", "content": "b"},
                {"role": "user", "content": "c"},
            ],
        }
        out = asyncio.run(
            inst.async_pre_call_hook(None, None, data, "anthropic_messages")
        )
        assert "libCRS mcp" in out["system"]
        assert all(m.get("role") != "system" for m in out["messages"])

    def test_pre_call_hook_anthropic_messages_idempotent(self):
        import asyncio

        hook = self._load_hook()
        inst = hook.OssCrsMcpHook()
        data = {
            "model": "m",
            "messages": [{"role": "user", "content": "hi"}],
            "system": "existing libCRS mcp docs",
        }
        out = asyncio.run(
            inst.async_pre_call_hook(None, None, data, "anthropic_messages")
        )
        assert out["system"] == "existing libCRS mcp docs"

    def test_no_pre_request_hook_override(self):
        hook = self._load_hook()
        assert "async_pre_request_hook" not in hook.OssCrsMcpHook.__dict__

    def test_hooks_fail_open(self):
        import asyncio

        hook = self._load_hook()
        inst = hook.OssCrsMcpHook()
        assert asyncio.run(inst.async_pre_call_hook(None, None, None, "x")) is None


class TestComposeSchemaWithMcpServers:
    """Test that the compose schema handles per-CRS mcp_servers correctly."""

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
        data["crs-claude-code"]["mcp_servers"] = ["ripgrep", "tree_sitter"]

        config = CRSComposeConfig.from_dict(data)
        assert config.crs_entries["crs-claude-code"].mcp_servers == [
            "ripgrep",
            "tree_sitter",
        ]

    def test_compose_without_mcp_servers(self, tmp_path):
        from oss_crs.src.config.crs_compose import CRSComposeConfig

        data = self._base_compose_data(tmp_path)
        config = CRSComposeConfig.from_dict(data)
        assert config.crs_entries["crs-claude-code"].mcp_servers is None

    def test_compose_rejects_root_mcp_servers(self, tmp_path):
        from oss_crs.src.config.crs_compose import CRSComposeConfig

        data = self._base_compose_data(tmp_path)
        data["mcp_servers"] = ["ripgrep"]
        with pytest.raises(ValueError, match="per-CRS"):
            CRSComposeConfig.from_dict(data)

    @pytest.mark.parametrize("name", ["reach-check", "", "bad.name", "réachcheck"])
    def test_invalid_server_names_rejected_before_source_resolution(
        self, tmp_path, monkeypatch, name
    ):
        from oss_crs.src.config.crs_compose import CRSComposeConfig

        def unexpected_resolution(*args, **kwargs):
            pytest.fail("Invalid MCP names must fail before resolving CRS sources")

        monkeypatch.setattr(
            "oss_crs.src.config.crs_compose.resolve_source_from_registry",
            unexpected_resolution,
        )
        data = self._base_compose_data(tmp_path)
        data["crs-claude-code"]["mcp_servers"] = [name]
        with pytest.raises(ValueError, match="Invalid MCP server name"):
            CRSComposeConfig.from_dict(data)

    def test_normalized_gateway_collision_rejected(self, tmp_path):
        from oss_crs.src.config.crs_compose import CRSComposeConfig

        data = self._base_compose_data(tmp_path)
        data["crs-claude-code"]["mcp_servers"] = ["reachcheck"]
        data["crs_claude_code"] = dict(data["crs-claude-code"])
        with pytest.raises(ValueError, match="Duplicate MCP gateway name"):
            CRSComposeConfig.from_dict(data)


class TestMCPServerArtifactsPath:
    def test_valid(self):
        config = MCPServerConfig(
            name="test",
            image="some-image",
            url="http://test:8000/mcp",
            artifacts_path="/artifacts",
        )
        assert config.artifacts_path == "/artifacts"

    def test_defaults_none(self):
        config = MCPServerConfig(
            name="test",
            image="some-image",
            url="http://test:8000/mcp",
        )
        assert config.artifacts_path is None

    @pytest.mark.parametrize("bad", ["", "relative", "artifacts", "/a:b", "/a/../b"])
    def test_invalid_rejected(self, bad):
        with pytest.raises(ValueError):
            MCPServerConfig(
                name="test",
                image="some-image",
                url="http://test:8000/mcp",
                artifacts_path=bad,
            )


class TestMCPNamingHelpers:
    def test_service_and_gateway_names(self):
        from oss_crs.src.config.mcp import (
            mcp_gateway_name,
            mcp_instance_alias,
            mcp_service_name,
            remap_mcp_url_for_crs,
        )

        assert mcp_service_name("crs-a", "ast_grep") == "mcp-crs-a-ast_grep"
        assert mcp_gateway_name("crs-a", "ast_grep") == "crs_a_ast_grep"
        assert mcp_instance_alias("crs-a", "ast_grep") == "crs_a_ast_grep"
        assert (
            remap_mcp_url_for_crs("http://ast_grep:3101/sse", "crs-a", "ast_grep")
            == "http://crs_a_ast_grep:3101/sse"
        )

    def test_registry_name_with_hyphen_rejected(self):
        with pytest.raises(ValueError, match="hyphens are not allowed"):
            MCPServerConfig(
                name="reach-check", image="img:latest", url="http://server:3102/mcp"
            )


class _FakeCompleted:
    def __init__(self, returncode=0, stderr=""):
        self.returncode = returncode
        self.stderr = stderr


def _write_registry(registry_dir: Path, entries: list[dict]) -> None:
    registry_dir.mkdir(parents=True, exist_ok=True)
    for i, entry in enumerate(entries):
        (registry_dir / f"srv{i}.yaml").write_text(yaml.dump(entry))


def _write_source(infra_root: Path, dirname: str) -> Path:
    d = infra_root / "mcp" / dirname
    d.mkdir(parents=True, exist_ok=True)
    (d / "Dockerfile").write_text("FROM scratch\n")
    return d


class TestMcpSourceDirForImage:
    def test_local_source_found(self, tmp_path):
        infra = tmp_path / "oss-crs-infra"
        _write_source(infra, "ast-grep")
        assert (
            mcp_source_dir_for_image("oss-crs-mcp/ast-grep:latest", infra)
            == infra / "mcp" / "ast-grep"
        )

    def test_registry_name_not_used(self, tmp_path):
        # The directory follows the image tag (ast-grep), not the registry
        # name (ast_grep).
        infra = tmp_path / "oss-crs-infra"
        _write_source(infra, "ast-grep")
        assert (
            mcp_source_dir_for_image("oss-crs-mcp/ast-grep", infra)
            == infra / "mcp" / "ast-grep"
        )

    def test_missing_dockerfile_is_remote(self, tmp_path):
        infra = tmp_path / "oss-crs-infra"
        (infra / "mcp" / "foo").mkdir(parents=True)
        assert mcp_source_dir_for_image("example.com/foo:1.0", infra) is None

    def test_missing_dir_is_remote(self, tmp_path):
        infra = tmp_path / "oss-crs-infra"
        infra.mkdir()
        assert mcp_source_dir_for_image("ghcr.io/org/remote:latest", infra) is None

    def test_registry_port_survives(self, tmp_path):
        infra = tmp_path / "oss-crs-infra"
        _write_source(infra, "foo")
        assert (
            mcp_source_dir_for_image("localhost:5000/foo", infra)
            == infra / "mcp" / "foo"
        )


class TestEnsureMcpServerImage:
    def _server(self, **kwargs):
        base = {
            "name": "ast_grep",
            "image": "oss-crs-mcp/ast-grep:latest",
            "url": "http://ast_grep:3101/sse",
        }
        base.update(kwargs)
        return MCPServerConfig(**base)

    def test_builds_local_source(self, tmp_path, monkeypatch):
        import oss_crs.src.config.mcp as mcp_mod

        infra = tmp_path / "oss-crs-infra"
        src = _write_source(infra, "ast-grep")
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return _FakeCompleted(0)

        monkeypatch.setattr(mcp_mod.subprocess, "run", fake_run)
        assert ensure_mcp_server_image(self._server(), infra) is None
        assert calls == [
            ["docker", "build", "-t", "oss-crs-mcp/ast-grep:latest", str(src)]
        ]

    def test_build_failure_names_server(self, tmp_path, monkeypatch):
        import oss_crs.src.config.mcp as mcp_mod

        infra = tmp_path / "oss-crs-infra"
        _write_source(infra, "ast-grep")
        monkeypatch.setattr(
            mcp_mod.subprocess,
            "run",
            lambda cmd, **kwargs: _FakeCompleted(1, "boom"),
        )
        error = ensure_mcp_server_image(self._server(), infra)
        assert error is not None
        assert "ast_grep" in error and "boom" in error

    def test_remote_present_skips_pull(self, tmp_path, monkeypatch):
        import oss_crs.src.config.mcp as mcp_mod

        infra = tmp_path / "oss-crs-infra"
        infra.mkdir()
        calls = []
        monkeypatch.setattr(
            mcp_mod.subprocess,
            "run",
            lambda cmd, **kwargs: calls.append(cmd) or _FakeCompleted(0),
        )
        server = self._server(image="ghcr.io/org/remote:1.0")
        assert ensure_mcp_server_image(server, infra) is None
        assert calls == [["docker", "image", "inspect", "ghcr.io/org/remote:1.0"]]

    def test_remote_missing_pulls(self, tmp_path, monkeypatch):
        import oss_crs.src.config.mcp as mcp_mod

        infra = tmp_path / "oss-crs-infra"
        infra.mkdir()
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[1] == "image":
                return _FakeCompleted(1)
            return _FakeCompleted(0)

        monkeypatch.setattr(mcp_mod.subprocess, "run", fake_run)
        server = self._server(image="ghcr.io/org/remote:1.0")
        assert ensure_mcp_server_image(server, infra) is None
        assert ["docker", "pull", "ghcr.io/org/remote:1.0"] in calls

    def test_remote_missing_no_pull_errors(self, tmp_path, monkeypatch):
        import oss_crs.src.config.mcp as mcp_mod

        infra = tmp_path / "oss-crs-infra"
        infra.mkdir()
        monkeypatch.setattr(
            mcp_mod.subprocess,
            "run",
            lambda cmd, **kwargs: _FakeCompleted(1),
        )
        server = self._server(image="ghcr.io/org/remote:1.0")
        error = ensure_mcp_server_image(server, infra, no_pull=True)
        assert error is not None and "--no-pull" in error


class TestPrepareMcpServerImages:
    def test_empty_is_noop(self, tmp_path, monkeypatch):
        import oss_crs.src.config.mcp as mcp_mod

        def fail_on_call(*args, **kwargs):
            raise AssertionError("no docker calls expected")

        monkeypatch.setattr(mcp_mod.subprocess, "run", fail_on_call)
        assert prepare_mcp_server_images([]) is None
        assert prepare_mcp_server_images(None) is None

    def test_unknown_server_raises(self, tmp_path):
        registry = tmp_path / "registry" / "mcp"
        registry.mkdir(parents=True)
        with pytest.raises(ValueError, match="not found in registry"):
            prepare_mcp_server_images(["nope"], registry_dir=registry)

    def test_referenced_only(self, tmp_path, monkeypatch):
        import oss_crs.src.config.mcp as mcp_mod

        registry = tmp_path / "registry" / "mcp"
        infra = tmp_path / "oss-crs-infra"
        _write_registry(
            registry,
            [
                {"name": "a", "image": "oss-crs-mcp/a:latest", "url": "http://a:1/sse"},
                {"name": "b", "image": "ghcr.io/org/b:1.0", "url": "http://b:1/sse"},
            ],
        )
        _write_source(infra, "a")
        built = []

        def fake_run(cmd, **kwargs):
            if cmd[:2] == ["docker", "build"]:
                built.append(cmd[3])
            return _FakeCompleted(0)

        monkeypatch.setattr(mcp_mod.subprocess, "run", fake_run)
        assert (
            prepare_mcp_server_images(["a"], registry_dir=registry, infra_root=infra)
            is None
        )
        # Only the referenced server was built; b was never touched.
        assert built == ["oss-crs-mcp/a:latest"]


def test_prepare_reads_mcp_servers_from_compose_entries(monkeypatch):
    from types import SimpleNamespace

    from oss_crs.src.config.crs_compose import CRSEntry
    from oss_crs.src.crs_compose import CRSCompose

    compose = CRSCompose.__new__(CRSCompose)
    compose.crs_list = [
        SimpleNamespace(
            config=SimpleNamespace(),
            resource=CRSEntry(
                cpuset="2-7", memory="16G", mcp_servers=["reachcheck", "ast_grep"]
            ),
        ),
        SimpleNamespace(
            config=SimpleNamespace(),
            resource=CRSEntry(cpuset="2-7", memory="16G", mcp_servers=["reachcheck"]),
        ),
        SimpleNamespace(config=SimpleNamespace(), resource=None),
    ]
    calls = []

    def fake_prepare(names, *, no_pull):
        calls.append((names, no_pull))

    monkeypatch.setattr(
        "oss_crs.src.crs_compose.prepare_mcp_server_images", fake_prepare
    )
    result = compose._CRSCompose__prepare_mcp_servers(no_pull=True)
    assert result.success
    assert calls == [(["reachcheck", "ast_grep"], True)]

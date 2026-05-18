# SPDX-License-Identifier: MIT
"""Unit tests for llm.py."""

import json
import copy
import pytest

from oss_crs.src.config.crs_compose import LLMConfig
from oss_crs.src.llm import (
    LLM,
    override_litellm_proxy,
    validate_providers,
    _provider_for_model,
    _provider_for_key_env,
)


class _FakeCRSConfig:
    def __init__(self, required_llms):
        self.required_llms = required_llms


class _FakeCRS:
    def __init__(self, required_llms):
        self.config = _FakeCRSConfig(required_llms)


class _FakeHTTPResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_external_mode_requires_env_sources(monkeypatch):
    llm = LLM(
        LLMConfig(
            litellm={
                "mode": "external",
                "external": {
                    "url_env": "LITELLM_URL",
                    "key_env": "LITELLM_API_KEY",
                },
            }
        )
    )
    monkeypatch.delenv("LITELLM_URL", raising=False)
    monkeypatch.delenv("LITELLM_API_KEY", raising=False)

    result = llm.validate_required_envs()

    assert result.success is False
    assert "LITELLM_URL" in (result.error or "")
    assert "LITELLM_API_KEY" in (result.error or "")


def test_external_mode_validates_required_llms_via_models_endpoint(monkeypatch):
    llm = LLM(
        LLMConfig(
            litellm={
                "mode": "external",
                "external": {
                    "url_env": "LITELLM_URL",
                    "key_env": "LITELLM_API_KEY",
                },
            }
        )
    )
    monkeypatch.setenv("LITELLM_URL", "https://litellm.example.com")
    monkeypatch.setenv("LITELLM_API_KEY", "sk-test")

    def _fake_urlopen(request, timeout=30):
        assert request.full_url == "https://litellm.example.com/models"
        return _FakeHTTPResponse(
            {
                "data": [
                    {"id": "claude-sonnet-4-6"},
                    {"id": "gpt-5"},
                ]
            }
        )

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)

    crs_list = [_FakeCRS(["claude-sonnet-4-6"])]
    result = llm.validate_required_llms(crs_list)

    assert result.success is True


def test_external_mode_can_skip_model_check(monkeypatch):
    llm = LLM(
        LLMConfig(
            litellm={
                "mode": "external",
                "model_check": False,
                "external": {
                    "url_env": "LITELLM_URL",
                    "key_env": "LITELLM_API_KEY",
                },
            }
        )
    )
    monkeypatch.setenv("LITELLM_URL", "https://litellm.example.com")
    monkeypatch.setenv("LITELLM_API_KEY", "sk-test")

    def _fake_urlopen(*args, **kwargs):
        raise AssertionError("urlopen must not be called when model_check is false")

    monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)

    crs_list = [_FakeCRS(["claude-sonnet-4-6"])]
    result = llm.validate_required_llms(crs_list)

    assert result.success is True


# =============================================================================
# Provider helpers
# =============================================================================


class TestProviderHelpers:
    def test_provider_for_model_known(self):
        assert _provider_for_model("openai/gpt-4o") == "openai"
        assert _provider_for_model("anthropic/claude-opus-4-6") == "anthropic"
        assert _provider_for_model("gemini/gemini-2.5-pro") == "gemini"
        assert _provider_for_model("xai/grok-3") == "xai"

    def test_provider_for_model_unknown(self):
        assert _provider_for_model("mistral/mixtral-8x7b") is None
        assert _provider_for_model("") is None

    def test_provider_for_key_env(self):
        assert _provider_for_key_env("OPENAI_API_KEY") == "openai"
        assert _provider_for_key_env("ANTHROPIC_API_KEY") == "anthropic"
        assert _provider_for_key_env("GEMINI_API_KEY") == "gemini"
        assert _provider_for_key_env("XAI_API_KEY") == "xai"
        assert _provider_for_key_env("UNKNOWN_KEY") is None


class TestValidateProviders:
    def test_valid_providers(self):
        validate_providers(["openai", "anthropic"])  # should not raise

    def test_invalid_provider_raises(self):
        with pytest.raises(ValueError, match="Unknown LLM provider.*mistral"):
            validate_providers(["openai", "mistral"])

    def test_empty_list(self):
        validate_providers([])  # should not raise


# =============================================================================
# override_litellm_proxy
# =============================================================================

SAMPLE_LITELLM_CONFIG = {
    "model_list": [
        {
            "model_name": "gpt-4o",
            "litellm_params": {
                "model": "openai/gpt-4o",
                "api_key": "os.environ/OPENAI_API_KEY",
            },
        },
        {
            "model_name": "claude-opus-4-6",
            "litellm_params": {
                "model": "anthropic/claude-opus-4-6",
                "api_key": "os.environ/ANTHROPIC_API_KEY",
            },
        },
        {
            "model_name": "gemini-2.5-pro",
            "litellm_params": {
                "model": "gemini/gemini-2.5-pro",
                "api_key": "os.environ/GEMINI_API_KEY",
            },
        },
    ],
    "litellm_settings": {"json_logs": True},
}


class TestOverrideLitellmProxy:
    def test_override_all_providers_key_only(self):
        result = override_litellm_proxy(SAMPLE_LITELLM_CONFIG, key_env="MY_PROXY_KEY")

        for entry in result["model_list"]:
            assert entry["litellm_params"]["api_key"] == "os.environ/MY_PROXY_KEY"
            assert "api_base" not in entry["litellm_params"]

    def test_override_all_providers_key_and_base(self):
        result = override_litellm_proxy(
            SAMPLE_LITELLM_CONFIG,
            key_env="MY_KEY",
            base_url_env="MY_BASE",
        )

        for entry in result["model_list"]:
            assert entry["litellm_params"]["api_key"] == "os.environ/MY_KEY"
            assert entry["litellm_params"]["api_base"] == "os.environ/MY_BASE"

    def test_override_subset_of_providers(self):
        result = override_litellm_proxy(
            SAMPLE_LITELLM_CONFIG,
            key_env="MY_KEY",
            base_url_env="MY_BASE",
            providers=["anthropic"],
        )

        # Anthropic model should be overridden
        claude = result["model_list"][1]
        assert claude["litellm_params"]["api_key"] == "os.environ/MY_KEY"
        assert claude["litellm_params"]["api_base"] == "os.environ/MY_BASE"

        # OpenAI should be untouched
        gpt = result["model_list"][0]
        assert gpt["litellm_params"]["api_key"] == "os.environ/OPENAI_API_KEY"
        assert "api_base" not in gpt["litellm_params"]

        # Gemini should be untouched
        gemini = result["model_list"][2]
        assert gemini["litellm_params"]["api_key"] == "os.environ/GEMINI_API_KEY"

    def test_does_not_mutate_original(self):
        original = copy.deepcopy(SAMPLE_LITELLM_CONFIG)
        override_litellm_proxy(SAMPLE_LITELLM_CONFIG, key_env="MY_KEY")
        assert SAMPLE_LITELLM_CONFIG == original

    def test_removes_existing_api_base_when_no_base_url(self):
        config = copy.deepcopy(SAMPLE_LITELLM_CONFIG)
        config["model_list"][0]["litellm_params"]["api_base"] = "os.environ/OLD_BASE"

        result = override_litellm_proxy(config, key_env="MY_KEY")

        assert "api_base" not in result["model_list"][0]["litellm_params"]

    def test_invalid_provider_raises(self):
        with pytest.raises(ValueError, match="Unknown LLM provider"):
            override_litellm_proxy(
                SAMPLE_LITELLM_CONFIG,
                key_env="MY_KEY",
                providers=["bogus"],
            )

    def test_preserves_non_model_list_keys(self):
        result = override_litellm_proxy(SAMPLE_LITELLM_CONFIG, key_env="MY_KEY")
        assert result["litellm_settings"] == {"json_logs": True}

    def test_skips_unknown_provider_models(self):
        config = copy.deepcopy(SAMPLE_LITELLM_CONFIG)
        config["model_list"].append(
            {
                "model_name": "mixtral",
                "litellm_params": {
                    "model": "mistral/mixtral-8x7b",
                    "api_key": "os.environ/MISTRAL_KEY",
                },
            }
        )
        result = override_litellm_proxy(config, key_env="MY_KEY")

        # Unknown provider model should be untouched
        mixtral = result["model_list"][3]
        assert mixtral["litellm_params"]["api_key"] == "os.environ/MISTRAL_KEY"

    def test_skips_custom_non_default_keys(self):
        """Entries with custom keys (e.g. VLLM_KEY) are never touched."""
        config = {
            "model_list": [
                {
                    "model_name": "local-model",
                    "litellm_params": {
                        "model": "openai/Qwen/Qwen3-0.6B",
                        "api_key": "os.environ/VLLM_KEY",
                        "api_base": "http://localhost:8000/v1",
                    },
                },
                {
                    "model_name": "gpt-4o",
                    "litellm_params": {
                        "model": "openai/gpt-4o",
                        "api_key": "os.environ/OPENAI_API_KEY",
                    },
                },
            ]
        }
        result = override_litellm_proxy(
            config, key_env="PROXY_KEY", base_url_env="PROXY_BASE"
        )

        # VLLM_KEY entry: untouched
        local = result["model_list"][0]
        assert local["litellm_params"]["api_key"] == "os.environ/VLLM_KEY"
        assert local["litellm_params"]["api_base"] == "http://localhost:8000/v1"

        # OPENAI_API_KEY entry: overridden
        gpt = result["model_list"][1]
        assert gpt["litellm_params"]["api_key"] == "os.environ/PROXY_KEY"
        assert gpt["litellm_params"]["api_base"] == "os.environ/PROXY_BASE"

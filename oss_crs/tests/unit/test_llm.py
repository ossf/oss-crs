"""Unit tests for llm.py."""

import json

from oss_crs.src.config.crs_compose import LLMConfig
from oss_crs.src.llm import LLM


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

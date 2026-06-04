# SPDX-License-Identifier: MIT
"""Unit tests for required_envs validation in CRSCompose."""

from types import SimpleNamespace
from unittest.mock import patch

from oss_crs.src.crs_compose import CRSCompose


class _FakeCRSConfig:
    def __init__(
        self,
        required_envs,
        *,
        build_env=None,
        module_env=None,
    ):
        self.required_envs = required_envs
        self.target_build_phase = SimpleNamespace(
            builds=[SimpleNamespace(additional_env=build_env or {})]
        )
        self.crs_run_phase = SimpleNamespace(
            modules={"main": SimpleNamespace(additional_env=module_env or {})}
        )


class _FakeCRS:
    def __init__(
        self,
        name,
        required_envs,
        *,
        resource_env=None,
        build_env=None,
        module_env=None,
    ):
        self.name = name
        self.config = _FakeCRSConfig(
            required_envs,
            build_env=build_env,
            module_env=module_env,
        )
        self.resource = SimpleNamespace(additional_env=resource_env or {})


def _make_compose(crs_list):
    """Create a minimal CRSCompose-like object with a crs_list for validation."""
    compose = object.__new__(CRSCompose)
    compose.crs_list = crs_list
    return compose


def test_no_required_envs_always_passes(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    compose = _make_compose([_FakeCRS("crs-a", None)])
    assert compose._validate_required_envs().success is True


def test_empty_required_envs_always_passes(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    compose = _make_compose([_FakeCRS("crs-a", [])])
    assert compose._validate_required_envs().success is True


def test_required_env_passes_when_in_host_env(monkeypatch):
    monkeypatch.setenv("API_KEY", "secret")
    compose = _make_compose([_FakeCRS("crs-a", ["API_KEY"])])
    assert compose._validate_required_envs().success is True


def test_required_env_passes_when_in_resource_additional_env(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    compose = _make_compose(
        [_FakeCRS("crs-a", ["API_KEY"], resource_env={"API_KEY": "secret"})]
    )
    assert compose._validate_required_envs().success is True


def test_required_env_passes_when_additional_env_placeholder_resolves(monkeypatch):
    monkeypatch.setenv("API_KEY", "secret")
    compose = _make_compose(
        [_FakeCRS("crs-a", ["API_KEY"], resource_env={"API_KEY": "${API_KEY}"})]
    )
    assert compose._validate_required_envs().success is True


def test_required_env_fails_when_additional_env_placeholder_is_missing(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    compose = _make_compose(
        [_FakeCRS("crs-a", ["API_KEY"], resource_env={"API_KEY": "${API_KEY}"})]
    )
    with patch("oss_crs.src.crs_compose.log_warning") as mock_warn:
        result = compose._validate_required_envs()
    assert result.success is False
    assert result.error is not None
    assert "API_KEY" in result.error
    mock_warn.assert_not_called()


def test_required_env_passes_when_additional_env_placeholder_has_default(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    compose = _make_compose(
        [_FakeCRS("crs-a", ["API_KEY"], resource_env={"API_KEY": "${API_KEY:-test}"})]
    )
    assert compose._validate_required_envs().success is True


def test_required_env_passes_when_additional_env_escapes_dollar(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    compose = _make_compose(
        [_FakeCRS("crs-a", ["API_KEY"], resource_env={"API_KEY": "$$API_KEY"})]
    )
    assert compose._validate_required_envs().success is True


def test_required_env_passes_when_in_build_additional_env(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    compose = _make_compose(
        [_FakeCRS("crs-a", ["API_KEY"], build_env={"API_KEY": "secret"})]
    )
    assert compose._validate_required_envs().success is True


def test_required_env_passes_when_in_module_additional_env(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    compose = _make_compose(
        [_FakeCRS("crs-a", ["API_KEY"], module_env={"API_KEY": "secret"})]
    )
    assert compose._validate_required_envs().success is True


def test_required_env_fails_when_missing(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    compose = _make_compose([_FakeCRS("crs-a", ["API_KEY"])])
    result = compose._validate_required_envs()
    assert result.success is False
    assert result.error is not None
    assert "crs-a" in result.error
    assert "API_KEY" in result.error
    assert "host environment" in result.error
    assert "additional_env" in result.error


def test_multiple_crs_reports_only_unsatisfied_crs(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("TOKEN", raising=False)
    compose = _make_compose(
        [
            _FakeCRS("crs-a", ["API_KEY"], resource_env={"API_KEY": "secret"}),
            _FakeCRS("crs-b", ["TOKEN"]),
        ]
    )
    result = compose._validate_required_envs()
    assert result.success is False
    assert result.error is not None
    assert "crs-a" not in result.error
    assert "crs-b" in result.error
    assert "TOKEN" in result.error


def test_optional_additional_env_placeholder_warns_when_missing(monkeypatch):
    monkeypatch.delenv("AIXCC_LITELLM_HOSTNAME", raising=False)
    compose = _make_compose(
        [
            _FakeCRS(
                "crs-a",
                ["API_KEY"],
                resource_env={
                    "API_KEY": "secret",
                    "AIXCC_LITELLM_HOSTNAME": "${AIXCC_LITELLM_HOSTNAME}",
                },
            )
        ]
    )

    with patch("oss_crs.src.crs_compose.log_warning") as mock_warn:
        result = compose._validate_required_envs()

    assert result.success is True
    mock_warn.assert_called_once()
    warning = mock_warn.call_args[0][0]
    assert "crs-a" in warning
    assert "AIXCC_LITELLM_HOSTNAME" in warning
    assert "optional CRS entry additional_env" in warning
    assert "required_envs" in warning


def test_optional_additional_env_placeholder_default_does_not_warn(monkeypatch):
    monkeypatch.delenv("OPTIONAL_TOKEN", raising=False)
    compose = _make_compose(
        [
            _FakeCRS(
                "crs-a",
                [],
                resource_env={"OPTIONAL_TOKEN": "${OPTIONAL_TOKEN:-unused}"},
            )
        ]
    )

    with patch("oss_crs.src.crs_compose.log_warning") as mock_warn:
        result = compose._validate_required_envs()

    assert result.success is True
    mock_warn.assert_not_called()

# SPDX-License-Identifier: MIT
from pathlib import Path

from oss_crs.src.host_env import expand_path, resolve_option

ENV = "OSS_CRS_TEST_OPTION"


class TestResolveOption:
    def test_cli_wins_over_compose_and_env(self, monkeypatch) -> None:
        monkeypatch.setenv(ENV, "env")
        assert resolve_option(ENV, cli="cli", compose="compose") == "cli"

    def test_compose_wins_over_env(self, monkeypatch) -> None:
        monkeypatch.setenv(ENV, "env")
        assert resolve_option(ENV, compose="compose") == "compose"

    def test_env_used_as_fallback(self, monkeypatch) -> None:
        monkeypatch.setenv(ENV, "env")
        assert resolve_option(ENV) == "env"

    def test_none_when_nothing_configured(self, monkeypatch) -> None:
        monkeypatch.delenv(ENV, raising=False)
        assert resolve_option(ENV) is None

    def test_empty_compose_and_env_count_as_unset(self, monkeypatch) -> None:
        monkeypatch.setenv(ENV, "")
        assert resolve_option(ENV, compose="") is None

    def test_cli_path_returned_as_str(self) -> None:
        assert resolve_option(ENV, cli=Path("/x/y")) == "/x/y"


class TestExpandPath:
    def test_expands_env_vars_and_user(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("CORP_CA_DIR", str(tmp_path))
        monkeypatch.setenv("HOME", str(tmp_path))
        assert expand_path("${CORP_CA_DIR}/corp.pem") == tmp_path / "corp.pem"
        assert expand_path("~/corp.pem") == tmp_path / "corp.pem"

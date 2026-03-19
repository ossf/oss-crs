"""Tests for CLI debug flag and environment handling."""

from pathlib import Path
from types import SimpleNamespace

from oss_crs.src.cli import crs_compose as cli_module


def _fake_prepare_compose() -> SimpleNamespace:
    return SimpleNamespace(prepare=lambda publish=False: True)


def test_cli_debug_flag_enables_debug_logging(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[tuple[bool, Path | None]] = []

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli_module.signal, "signal", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli_module, "load_dotenv", lambda: None)
    monkeypatch.setattr(
        cli_module.CRSCompose,
        "from_yaml_file",
        lambda *args, **kwargs: _fake_prepare_compose(),
    )
    monkeypatch.setattr(
        cli_module,
        "configure_debug_logging",
        lambda enabled, log_path=None: calls.append((enabled, log_path)),
    )
    monkeypatch.setattr(
        cli_module.sys,
        "argv",
        ["oss-crs", "prepare", "--compose-file", "compose.yaml", "--debug"],
    )

    assert cli_module.cli() is True
    assert calls == [(True, tmp_path / "debug.log")]


def test_cli_debug_env_enables_debug_logging(
    tmp_path: Path, monkeypatch
) -> None:
    calls: list[tuple[bool, Path | None]] = []

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OSS_CRS_DEBUG", "1")
    monkeypatch.setattr(cli_module.signal, "signal", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(cli_module, "load_dotenv", lambda: None)
    monkeypatch.setattr(
        cli_module.CRSCompose,
        "from_yaml_file",
        lambda *args, **kwargs: _fake_prepare_compose(),
    )
    monkeypatch.setattr(
        cli_module,
        "configure_debug_logging",
        lambda enabled, log_path=None: calls.append((enabled, log_path)),
    )
    monkeypatch.setattr(
        cli_module.sys,
        "argv",
        ["oss-crs", "prepare", "--compose-file", "compose.yaml"],
    )

    assert cli_module.cli() is True
    assert calls == [(True, tmp_path / "debug.log")]

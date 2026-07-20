# SPDX-License-Identifier: MIT
import signal
from types import SimpleNamespace

import pytest

from oss_crs.src.cli import crs_compose


@pytest.mark.parametrize(
    ("signum", "expected_exit_code"),
    ((signal.SIGINT, 130), (signal.SIGTERM, 143)),
)
def test_main_returns_signal_exit_code_after_cleanup_message(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    signum: int,
    expected_exit_code: int,
) -> None:
    monkeypatch.setattr(
        crs_compose,
        "cli",
        lambda: (_ for _ in ()).throw(crs_compose._SignalInterrupt(signum)),
    )

    assert crs_compose.main() == expected_exit_code
    assert "Run interrupted; cleanup was attempted." in capsys.readouterr().err


def test_azure_cleanup_command_normalizes_run_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleaned: list[str] = []

    class FakeConfig:
        @classmethod
        def from_env(cls):
            return "config"

    class FakeSubmitter:
        def __init__(self, config):
            assert config == "config"

        def cleanup_run(self, run_id: str) -> None:
            cleaned.append(run_id)

    monkeypatch.setattr(crs_compose, "AzureSpotVmConfig", FakeConfig)
    monkeypatch.setattr(crs_compose, "AzureSpotVmRunSubmitter", FakeSubmitter)

    assert crs_compose.handle_azure_cleanup(SimpleNamespace(run_id="Run 1")) is True
    assert cleaned == [crs_compose.normalize_run_id("Run 1")]

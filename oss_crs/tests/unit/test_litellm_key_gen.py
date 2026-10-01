# SPDX-License-Identifier: MIT
"""Tests for the litellm-key-gen spend/token orchestration.

The sidecar is a standalone image, so it is loaded from its file path with
the master-key secret stubbed (only during import). HTTP is mocked; date
handling is asserted against the real clock.
"""

import hashlib
import importlib.util
import json
from datetime import date, timedelta
from pathlib import Path
from types import ModuleType
from unittest import mock

import pytest
import requests

REPO_ROOT = Path(__file__).resolve().parents[3]
KEYGEN_DIR = REPO_ROOT / "oss-crs-infra" / "litellm-key-gen"


@pytest.fixture(scope="module")
def keygen() -> ModuleType:
    with mock.patch("builtins.open", mock.mock_open(read_data="test-master")):
        spec = importlib.util.spec_from_file_location(
            "litellm_key_gen_main", KEYGEN_DIR / "main.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    yield module


def _spend_response(spend: float) -> mock.MagicMock:
    response = mock.MagicMock()
    response.json.return_value = {"info": {"spend": spend}}
    response.raise_for_status.return_value = None
    return response


def _logs_response(rows: object) -> mock.MagicMock:
    response = mock.MagicMock()
    response.json.return_value = rows
    response.raise_for_status.return_value = None
    return response


def test_collect_spend_summary_aggregates_tokens(keygen: ModuleType) -> None:
    with (
        mock.patch.object(keygen, "get_key_spend", return_value=1.5),
        mock.patch.object(keygen, "get_key_tokens", return_value=(100, 50)),
    ):
        summary = keygen.collect_spend_summary(
            {"a": {"api_key": "k1"}, "b": {"api_key": ""}}
        )
    assert summary["totals"] == {
        "credits_used": 1.5,
        "prompt_tokens": 100,
        "completion_tokens": 50,
    }
    assert summary["crs"]["a"] == {
        "credits_used": 1.5,
        "prompt_tokens": 100,
        "completion_tokens": 50,
    }
    assert summary["crs"]["b"]["prompt_tokens"] == 0
    assert "updated_at" in summary


def test_collect_uses_exclusive_end_date(keygen: ModuleType) -> None:
    """Regression test: LiteLLM treats end_date as exclusive, so querying
    with end == start == today returns zero rows. The collector must roll
    the end forward to tomorrow when the caller omits it."""
    seen: dict = {}

    def fake_get(url: str, **kwargs: object) -> mock.MagicMock:
        params = kwargs.get("params")
        assert isinstance(params, dict)
        if url.endswith("/spend/logs"):
            seen.update(params)
            return _logs_response([])
        return _spend_response(0.0)

    with mock.patch.object(keygen.requests, "get", side_effect=fake_get):
        keygen.collect_spend_summary({"a": {"api_key": "k1"}}, start_date="2026-09-04")
    assert seen["start_date"] == "2026-09-04"
    assert seen["end_date"] == (date.today() + timedelta(days=1)).isoformat()
    assert seen["end_date"] > seen["start_date"]


def test_get_key_tokens_fail_open(keygen: ModuleType) -> None:
    with mock.patch.object(
        keygen.requests,
        "get",
        side_effect=requests.exceptions.ConnectionError("down"),
    ):
        assert keygen.get_key_tokens("k1") == (0, 0)
        assert keygen.get_key_spend("k1") == 0.0
    assert keygen.get_key_tokens("") == (0, 0)


def test_collect_matches_spend_logs_end_to_end(
    keygen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Full loop against the live v1.94.0 shapes: /key/info spend plus
    /spend/logs rows filtered by key hash, written to the report file."""
    raw = "sk-live-key"
    key_hash = hashlib.sha256(raw.encode()).hexdigest()
    rows = [
        {"api_key": key_hash, "prompt_tokens": 30055, "completion_tokens": 2650},
        {"api_key": key_hash, "prompt_tokens": 21693, "completion_tokens": 19},
        {"api_key": "other", "prompt_tokens": 99999, "completion_tokens": 99999},
    ]

    def fake_get(url: str, **kwargs: object) -> mock.MagicMock:
        if url.endswith("/key/info"):
            return _spend_response(2.0)
        return _logs_response(rows)

    report = tmp_path / "spend.json"
    monkeypatch.setattr(keygen, "SPEND_REPORT_PATH", str(report))
    with mock.patch.object(keygen.requests, "get", side_effect=fake_get):
        summary = keygen.collect_spend_summary(
            {"crs-a": {"api_key": raw}}, start_date="2026-09-04"
        )
        keygen.write_spend_summary(summary)
    on_disk = json.loads(report.read_text())
    assert on_disk["crs"]["crs-a"] == {
        "credits_used": 2.0,
        "prompt_tokens": 51748,
        "completion_tokens": 2669,
    }
    assert on_disk["totals"]["prompt_tokens"] == 51748
    assert on_disk["totals"]["completion_tokens"] == 2669


def test_poll_and_persist_delegates(keygen: ModuleType) -> None:
    with (
        mock.patch.object(
            keygen, "collect_spend_summary", return_value={"ok": True}
        ) as collect,
        mock.patch.object(keygen, "write_spend_summary") as write,
    ):
        keygen._poll_and_persist({"a": {"api_key": "k"}}, "2026-09-04")
    collect.assert_called_once_with({"a": {"api_key": "k"}}, start_date="2026-09-04")
    write.assert_called_once_with({"ok": True})


def test_write_spend_summary_is_atomic(
    keygen: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The report file must never be left truncated: payload goes to a temp
    file first, then moves into place."""
    report = tmp_path / "spend.json"
    monkeypatch.setattr(keygen, "SPEND_REPORT_PATH", str(report))
    keygen.write_spend_summary({"totals": {"credits_used": 1.0}})
    assert json.loads(report.read_text()) == {"totals": {"credits_used": 1.0}}
    assert not Path(str(report) + ".tmp").exists()


def test_main_flushes_on_shutdown(keygen: ModuleType) -> None:
    """A forced final poll runs when the loop exits via shutdown, so the
    report is not stuck a poll interval behind at teardown."""
    keygen._SHUTDOWN = True
    try:
        request_yaml = "{crs-a: {api_key: k, required_llms: [], llm_budget: 1}}\n"
        with (
            mock.patch("builtins.open", mock.mock_open(read_data=request_yaml)),
            mock.patch.object(keygen, "get_available_models", return_value=[]),
            mock.patch.object(keygen, "create_llm_key", return_value="k"),
            mock.patch.object(
                keygen, "collect_spend_summary", return_value={"totals": {}}
            ) as collect,
        ):
            assert keygen.main() == 0
    finally:
        keygen._SHUTDOWN = False
    collect.assert_called_once()

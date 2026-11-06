# SPDX-License-Identifier: MIT
"""Resilience tests for LiteLLM token rate limiting, budget exhaustion, and spend data recovery."""

import importlib.util
import json
import sys
import urllib.error
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, mock_open, patch

import pytest
import requests

# Ensure fcntl is mocked on Windows platforms for target/workdir imports
if "fcntl" not in sys.modules:
    try:
        import fcntl  # noqa: F401
    except ImportError:
        sys.modules["fcntl"] = MagicMock()

from oss_crs.src.crs_compose import CRSCompose
from oss_crs.src.llm import LLM

REPO_ROOT = Path(__file__).resolve().parents[3]
KEYGEN_DIR = REPO_ROOT / "oss-crs-infra" / "litellm-key-gen"


@pytest.fixture(scope="module")
def keygen_mod() -> ModuleType:
    with patch("builtins.open", mock_open(read_data="test-master")):
        spec = importlib.util.spec_from_file_location(
            "litellm_key_gen_main", KEYGEN_DIR / "main.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


# =====================================================================
# Rate Limiting (HTTP 429) Handling & Graceful Recovery
# =====================================================================


def test_litellm_fetch_models_rate_limited_429():
    """When LiteLLM returns HTTP 429 Too Many Requests, it is captured in fetch_error without crashing."""
    manager = LLM(llm_config=None)
    manager.mode = "external"
    manager.external_url = "https://litellm.example.com"
    manager.external_key = "secret-key"

    http_429_err = urllib.error.HTTPError(
        url="https://litellm.example.com/models",
        code=429,
        msg="Too Many Requests",
        hdrs=None,
        fp=None,
    )

    with patch("urllib.request.urlopen", side_effect=http_429_err):
        models = manager._fetch_external_models()
        assert models is None
        assert "Too Many Requests" in (manager.fetch_error or "")


def test_keygen_rate_limited_token_and_spend_queries(keygen_mod: ModuleType):
    """When LiteLLM rate limits /spend/logs or /key/info (429), keygen falls back gracefully."""
    response_429 = MagicMock()
    response_429.status_code = 429
    response_429.raise_for_status.side_effect = requests.exceptions.HTTPError(
        "429 Client Error: Too Many Requests", response=response_429
    )

    with patch("requests.get", return_value=response_429):
        # Rate-limited spend query returns 0.0 without unhandled crash
        spend = keygen_mod.get_key_spend("key-1")
        assert spend == 0.0

        # Rate-limited token query returns (0, 0) without unhandled crash
        tokens = keygen_mod.get_key_tokens("key-1")
        assert tokens == (0, 0)


def test_keygen_create_llm_key_rate_limited_429(keygen_mod: ModuleType):
    """When key generation is rate-limited, create_llm_key returns None."""
    response_429 = MagicMock()
    response_429.status_code = 429
    response_429.raise_for_status.side_effect = requests.exceptions.HTTPError(
        "429 Client Error: Too Many Requests", response=response_429
    )

    with patch("requests.post", return_value=response_429):
        key = keygen_mod.create_llm_key("crs-key", budget=50)
        assert key is None


# =====================================================================
# Budget Exhaustion & Usage Aggregation
# =====================================================================


def test_keygen_budget_exhaustion_recovery(keygen_mod: ModuleType):
    """Test spend aggregation when CRS keys hit their max_budget."""
    key_requests = {
        "crs-exhausted": {"api_key": "key-exhausted", "budget": 10.0},
        "crs-active": {"api_key": "key-active", "budget": 20.0},
        "crs-idle": {"api_key": "key-idle", "budget": 10.0},
    }

    def mock_get_spend(key: str) -> float:
        if key == "key-exhausted":
            return 10.0  # Budget exhausted
        if key == "key-active":
            return 4.5
        return 0.0

    def mock_get_tokens(key: str, start=None, end=None) -> tuple[int, int]:
        if key == "key-exhausted":
            return (50000, 15000)
        if key == "key-active":
            return (20000, 6000)
        return (0, 0)

    with (
        patch.object(keygen_mod, "get_key_spend", side_effect=mock_get_spend),
        patch.object(keygen_mod, "get_key_tokens", side_effect=mock_get_tokens),
    ):
        summary = keygen_mod.collect_spend_summary(key_requests)

    # Verify per-CRS metrics are recorded correctly
    assert summary["crs"]["crs-exhausted"] == {
        "credits_used": 10.0,
        "prompt_tokens": 50000,
        "completion_tokens": 15000,
    }
    assert summary["crs"]["crs-active"] == {
        "credits_used": 4.5,
        "prompt_tokens": 20000,
        "completion_tokens": 6000,
    }
    assert summary["crs"]["crs-idle"] == {
        "credits_used": 0.0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
    }

    # Verify totals across all CRSes
    assert summary["totals"]["credits_used"] == 14.5
    assert summary["totals"]["prompt_tokens"] == 70000
    assert summary["totals"]["completion_tokens"] == 21000


# =====================================================================
# Corrupted / Missing Spend Report File Recovery in crs_compose
# =====================================================================


def test_crs_compose_read_spend_summary_missing_file():
    """Missing spend file returns default empty totals structure without error."""
    mock_workdir = MagicMock()
    mock_workdir.get_litellm_spend_report_file.return_value = Path(
        "/nonexistent/spend.json"
    )

    compose = object.__new__(CRSCompose)
    compose.work_dir = mock_workdir

    summary = compose._read_litellm_spend_summary("run-1", "address")
    assert summary == {
        "totals": {
            "credits_used": 0.0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
        },
        "crs": {},
    }


def test_crs_compose_read_spend_summary_corrupted_json(tmp_path: Path):
    """Truncated/corrupted JSON file (e.g. from container killed mid-write) is handled gracefully."""
    spend_file = tmp_path / "litellm-spend-report.json"
    spend_file.write_text(
        '{"totals": {"credits_used": 12.5, "prompt_tok'
    )  # Incomplete JSON

    mock_workdir = MagicMock()
    mock_workdir.get_litellm_spend_report_file.return_value = spend_file

    compose = object.__new__(CRSCompose)
    compose.work_dir = mock_workdir

    summary = compose._read_litellm_spend_summary("run-1", "address")
    assert summary["totals"] == {
        "credits_used": 0.0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
    }
    assert summary["crs"] == {}


def test_crs_compose_read_spend_summary_partial_fields(tmp_path: Path):
    """Valid JSON missing totals or with malformed CRS rows defaults safely."""
    spend_file = tmp_path / "litellm-spend-report.json"
    data = {
        "totals": {"credits_used": 5.25},  # prompt_tokens, completion_tokens missing
        "crs": {
            "crs-1": {"credits_used": 5.25},
            "crs-malformed": "not-a-dict",  # Should be skipped
        },
    }
    spend_file.write_text(json.dumps(data))

    mock_workdir = MagicMock()
    mock_workdir.get_litellm_spend_report_file.return_value = spend_file

    compose = object.__new__(CRSCompose)
    compose.work_dir = mock_workdir

    summary = compose._read_litellm_spend_summary("run-1", "address")
    assert summary["totals"]["credits_used"] == 5.25
    assert summary["totals"]["prompt_tokens"] == 0
    assert summary["totals"]["completion_tokens"] == 0
    assert "crs-1" in summary["crs"]
    assert "crs-malformed" not in summary["crs"]

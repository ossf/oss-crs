# SPDX-License-Identifier: MIT
"""Tests for the webui-publisher spend-report reader.

The publisher is a standalone sidecar image, so it is loaded from its file
path rather than imported as a package.
"""

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
PUB_DIR = REPO_ROOT / "oss-crs-infra" / "webui-publisher"


@pytest.fixture()
def publisher() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "webui_publisher_main", PUB_DIR / "main.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    yield module


def _write_report(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload))
    return path


def test_read_cost_new_schema(publisher: ModuleType, tmp_path: Path) -> None:
    report = _write_report(
        tmp_path / "spend.json",
        {
            "totals": {
                "credits_used": 1.5,
                "prompt_tokens": 100,
                "completion_tokens": 50,
            },
            "crs": {
                "crs-a": {
                    "credits_used": 1.5,
                    "prompt_tokens": 100,
                    "completion_tokens": 50,
                }
            },
        },
    )
    cost = publisher.read_cost(report)
    assert cost is not None
    assert cost["total"] == 1.5
    assert cost["per_crs"] == {"crs-a": 1.5}
    assert cost["prompt_tokens"] == 100
    assert cost["completion_tokens"] == 50
    assert "total_tokens" not in cost
    assert cost["per_crs_tokens"] == {
        "crs-a": {"prompt_tokens": 100, "completion_tokens": 50}
    }


def test_read_cost_old_schema_defaults_tokens(
    publisher: ModuleType, tmp_path: Path
) -> None:
    report = _write_report(
        tmp_path / "spend.json",
        {"totals": {"credits_used": 2.0}, "crs": {"a": {"credits_used": 2.0}}},
    )
    cost = publisher.read_cost(report)
    assert cost is not None
    assert cost["total"] == 2.0
    assert cost["prompt_tokens"] == 0
    assert cost["completion_tokens"] == 0
    assert cost["per_crs_tokens"] == {"a": {"prompt_tokens": 0, "completion_tokens": 0}}


def test_read_cost_sums_totals_when_missing(
    publisher: ModuleType, tmp_path: Path
) -> None:
    report = _write_report(
        tmp_path / "spend.json",
        {
            "totals": {"credits_used": 3.0},
            "crs": {
                "a": {
                    "credits_used": 1.0,
                    "prompt_tokens": 10,
                    "completion_tokens": 4,
                },
                "b": {
                    "credits_used": 2.0,
                    "prompt_tokens": 20,
                    "completion_tokens": 6,
                },
            },
        },
    )
    cost = publisher.read_cost(report)
    assert cost is not None
    assert cost["prompt_tokens"] == 30
    assert cost["completion_tokens"] == 10
    assert cost["prompt_tokens"] + cost["completion_tokens"] == 40


def test_read_cost_missing_or_corrupt(publisher: ModuleType, tmp_path: Path) -> None:
    assert publisher.read_cost(tmp_path / "absent.json") is None
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not json")
    assert publisher.read_cost(corrupt) is None
    # Default path (/spend/litellm-spend-report.json) is absent outside containers.
    assert publisher.read_cost() is None

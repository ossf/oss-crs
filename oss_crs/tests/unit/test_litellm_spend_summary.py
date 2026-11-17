# SPDX-License-Identifier: MIT
"""Unit tests for litellm spend summary reading and token fallback."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

if "fcntl" not in sys.modules:
    try:
        import fcntl  # noqa: F401
    except ImportError:
        sys.modules["fcntl"] = MagicMock()

from oss_crs.src.crs_compose import CRSCompose
from oss_crs.src.workdir import WorkDir


def _make_compose(tmp_path: Path) -> CRSCompose:
    compose = CRSCompose.__new__(CRSCompose)
    compose.work_dir = WorkDir(tmp_path / "work")
    return compose


def test_read_litellm_spend_summary_computes_sums_when_totals_missing(
    tmp_path: Path,
) -> None:
    run_id = "test-sums-run"
    sanitizer = "address"
    compose = _make_compose(tmp_path)
    report_file = compose.work_dir.get_litellm_spend_report_file(
        run_id, sanitizer, create_parent=True
    )
    report_file.write_text(
        json.dumps(
            {
                # "totals" has no token fields or explicit credits_used
                "totals": {},
                "crs": {
                    "crs-1": {
                        "credits_used": 1.25,
                        "prompt_tokens": 10,
                        "completion_tokens": 20,
                    },
                    "crs-2": {
                        "credits_used": 2.50,
                        "prompt_tokens": 30,
                        "completion_tokens": 40,
                    },
                },
            }
        )
    )

    summary = compose._read_litellm_spend_summary(run_id, sanitizer)
    assert summary["totals"]["credits_used"] == 3.75
    assert summary["totals"]["prompt_tokens"] == 40
    assert summary["totals"]["completion_tokens"] == 60
    assert summary["crs"]["crs-1"]["credits_used"] == 1.25
    assert summary["crs"]["crs-2"]["completion_tokens"] == 40


def test_read_litellm_spend_summary_prefers_explicit_totals(tmp_path: Path) -> None:
    run_id = "test-explicit-totals"
    sanitizer = "address"
    compose = _make_compose(tmp_path)
    report_file = compose.work_dir.get_litellm_spend_report_file(
        run_id, sanitizer, create_parent=True
    )
    report_file.write_text(
        json.dumps(
            {
                "totals": {
                    "credits_used": 10.0,
                    "prompt_tokens": 100,
                    "completion_tokens": 200,
                },
                "crs": {
                    "crs-1": {
                        "credits_used": 1.0,
                        "prompt_tokens": 10,
                        "completion_tokens": 20,
                    }
                },
            }
        )
    )

    summary = compose._read_litellm_spend_summary(run_id, sanitizer)
    assert summary["totals"]["credits_used"] == 10.0
    assert summary["totals"]["prompt_tokens"] == 100
    assert summary["totals"]["completion_tokens"] == 200


def test_read_litellm_spend_summary_missing_file_defaults(tmp_path: Path) -> None:
    run_id = "missing-report-run"
    sanitizer = "address"
    compose = _make_compose(tmp_path)

    summary = compose._read_litellm_spend_summary(run_id, sanitizer)
    assert summary["totals"]["credits_used"] == 0.0
    assert summary["totals"]["prompt_tokens"] == 0
    assert summary["totals"]["completion_tokens"] == 0
    assert summary["crs"] == {}

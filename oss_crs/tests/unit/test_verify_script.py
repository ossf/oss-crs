# SPDX-License-Identifier: MIT
"""Unit tests for verify script coverage enforcement and codecov config."""

from pathlib import Path
from unittest.mock import patch
import yaml

from scripts import verify


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_cov_args_matches_specification():
    """Verify scripts.verify.COV_ARGS contains required coverage targets and report flags."""
    expected_args = [
        "--cov=oss_crs",
        "--cov=libCRS",
        "--cov=oss-crs-infra",
        "--cov-report=term",
        "--cov-report=xml",
    ]
    assert verify.COV_ARGS == expected_args


def test_verify_run_includes_coverage_flags_by_default():
    """Verify that _run(enable_cov=True) passes coverage flags to pytest steps."""
    executed_commands: list[list[str]] = []

    def mock_run(cmd: list[str]) -> int:
        executed_commands.append(cmd)
        return 0

    with patch.object(verify, "run", side_effect=mock_run):
        rc = verify._run(enable_cov=True)
        assert rc == 0

    # Locate unit test steps
    unit_test_cmd = next(
        cmd for cmd in executed_commands if "oss_crs" in cmd and "pytest" in cmd
    )
    libcrs_test_cmd = next(
        cmd for cmd in executed_commands if "libCRS/tests" in cmd and "pytest" in cmd
    )

    for flag in verify.COV_ARGS:
        assert flag in unit_test_cmd
        assert flag in libcrs_test_cmd

    assert "--cov-append" in libcrs_test_cmd


def test_verify_run_excludes_coverage_when_disabled():
    """Verify that _run(enable_cov=False) omits coverage flags."""
    executed_commands: list[list[str]] = []

    def mock_run(cmd: list[str]) -> int:
        executed_commands.append(cmd)
        return 0

    with patch.object(verify, "run", side_effect=mock_run):
        rc = verify._run(enable_cov=False)
        assert rc == 0

    unit_test_cmd = next(
        cmd for cmd in executed_commands if "oss_crs" in cmd and "pytest" in cmd
    )
    libcrs_test_cmd = next(
        cmd for cmd in executed_commands if "libCRS/tests" in cmd and "pytest" in cmd
    )

    for flag in verify.COV_ARGS:
        assert flag not in unit_test_cmd
        assert flag not in libcrs_test_cmd

    assert "--cov-append" not in libcrs_test_cmd


def test_verify_reports_failure_when_step_fails():
    """Verify that _run returns 1 when any step fails."""
    with patch.object(verify, "run", return_value=1):
        rc = verify._run(enable_cov=False)
        assert rc == 1


def test_resolve_cmd():
    """Verify resolve_cmd returns list intact or resolves to python -m when not found."""
    cmd = ["unknown-tool-xyz", "--arg"]
    assert verify.resolve_cmd(cmd) == cmd

    cmd_pytest = ["pytest", "-v"]
    resolved = verify.resolve_cmd(cmd_pytest)
    assert (
        resolved[0].endswith("python")
        or resolved[0].endswith("python.exe")
        or resolved[0] == "pytest"
    )


def test_codecov_yaml_configuration():
    """Verify .codecov.yml exists and enforces the 80% coverage threshold."""
    codecov_file = REPO_ROOT / ".codecov.yml"
    assert codecov_file.is_file(), f"Missing {codecov_file}"

    with open(codecov_file, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    assert "coverage" in data
    cov = data["coverage"]

    # Target 80% threshold for project status
    assert cov["status"]["project"]["default"]["target"] == "80%"

    # Target 80% threshold for patch status
    assert cov["status"]["patch"]["default"]["target"] == "80%"

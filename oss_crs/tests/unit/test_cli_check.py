# SPDX-License-Identifier: MIT
"""Unit tests for the `oss-crs check` command."""

import argparse
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from rich.console import Console

from oss_crs.src.cli.check import (
    DEFAULT_WORK_DIR,
    CheckReport,
    add_check_command,
    handle_check,
    is_git_url,
    print_report,
    resolve_crs_target,
    validate_crs_config,
    validate_crs_files,
    validate_crs_functional,
)

FIXTURES_DIR = (
    Path(__file__).parent.parent / "integration" / "fixtures" / "builder-sidecar-full"
)


@pytest.fixture
def test_console():
    """Return a Rich Console writing to a string buffer."""
    return Console(record=True, width=120)


@pytest.fixture
def minimal_crs_dict():
    """Return a minimal valid CRS config dictionary."""
    return {
        "name": "test-crs",
        "type": ["bug-finding"],
        "version": "1.0.0",
        "supported_target": {
            "mode": ["full"],
            "language": ["c"],
        },
        "crs_run_phase": {
            "fuzzer": {
                "dockerfile": "oss-crs/fuzzer.Dockerfile",
            }
        },
    }


def test_is_git_url():
    """Verify Git URL detection handles various protocols and local paths."""
    assert is_git_url("https://github.com/ossf/oss-crs.git")
    assert is_git_url("http://example.com/repo.git")
    assert is_git_url("git@github.com:ossf/oss-crs.git")
    assert is_git_url("ssh://git@server.com/repo.git")
    assert is_git_url("git://github.com/ossf/oss-crs.git")
    assert not is_git_url("/path/to/local/dir")
    assert not is_git_url("relative/path/to/crs")


def test_add_check_command():
    """Verify add_check_command registers all expected CLI arguments."""
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    add_check_command(subparsers)

    args = parser.parse_args(
        ["check", "my-crs", "--branch", "dev", "--functional", "--offline"]
    )
    assert args.command == "check"
    assert args.crs_path == "my-crs"
    assert args.branch == "dev"
    assert args.functional is True
    assert args.offline is True
    assert args.work_dir == DEFAULT_WORK_DIR


def test_check_valid_fixture():
    """Verify that an existing valid CRS fixture passes validation."""
    args = argparse.Namespace(
        crs_path=str(FIXTURES_DIR),
        branch="main",
        work_dir=DEFAULT_WORK_DIR,
        functional=False,
        offline=False,
    )
    result = handle_check(args)
    assert result is True


def test_check_direct_crs_yaml_path():
    """Verify check accepts a direct path pointing to a crs.yaml file."""
    yaml_file = FIXTURES_DIR / "oss-crs" / "crs.yaml"
    args = argparse.Namespace(
        crs_path=str(yaml_file),
        branch="main",
        work_dir=DEFAULT_WORK_DIR,
        functional=False,
        offline=False,
    )
    result = handle_check(args)
    assert result is True


def test_check_nonexistent_path(test_console):
    """Verify check reports failure when given a nonexistent path."""
    report = CheckReport("does_not_exist_path")
    repo_root, config_path = resolve_crs_target("does_not_exist_path", report=report)
    assert repo_root is None
    assert config_path is None
    assert report.has_errors
    assert "Path does not exist" in report.errors[0].message
    assert print_report(report, console=test_console) is False


def test_check_missing_crs_yaml(tmp_path, test_console):
    """Verify check reports failure when directory lacks crs.yaml."""
    report = CheckReport(str(tmp_path))
    repo_root, config_path = resolve_crs_target(str(tmp_path), report=report)
    assert repo_root is None
    assert config_path is None
    assert report.has_errors
    assert "No CRS configuration file found" in report.errors[0].message
    assert print_report(report, console=test_console) is False


def test_check_invalid_yaml_syntax(tmp_path, test_console):
    """Verify check reports syntax error line/column for invalid YAML."""
    config_file = tmp_path / "crs.yaml"
    config_file.write_text("name: broken-crs\ntype: [unclosed list", encoding="utf-8")

    report = CheckReport(str(tmp_path))
    config = validate_crs_config(config_file, report)
    assert config is None
    assert report.has_errors
    assert "Invalid YAML syntax" in report.errors[0].message
    assert print_report(report, console=test_console) is False


def test_check_non_dict_yaml(tmp_path, test_console):
    """Verify check reports error when YAML does not parse to a dictionary."""
    config_file = tmp_path / "crs.yaml"
    config_file.write_text("- item1\n- item2", encoding="utf-8")

    report = CheckReport(str(tmp_path))
    config = validate_crs_config(config_file, report)
    assert config is None
    assert report.has_errors
    assert "must be a YAML mapping" in report.errors[0].message
    assert print_report(report, console=test_console) is False


def test_check_schema_missing_name(tmp_path, minimal_crs_dict):
    """Verify check flags missing CRS name."""
    del minimal_crs_dict["name"]
    config_file = tmp_path / "crs.yaml"
    config_file.write_text(yaml.dump(minimal_crs_dict), encoding="utf-8")

    report = CheckReport(str(tmp_path))
    config = validate_crs_config(config_file, report)
    assert config is None
    assert report.has_errors
    assert any("name" in e.message for e in report.errors)


def test_check_schema_invalid_type(tmp_path, minimal_crs_dict):
    """Verify check flags invalid CRS type and provides allowed types hint."""
    minimal_crs_dict["type"] = ["invalid-type-name"]
    config_file = tmp_path / "crs.yaml"
    config_file.write_text(yaml.dump(minimal_crs_dict), encoding="utf-8")

    report = CheckReport(str(tmp_path))
    config = validate_crs_config(config_file, report)
    assert config is None
    assert report.has_errors
    error = next(e for e in report.errors if "type" in e.message)
    assert "Valid types:" in error.hint


def test_check_schema_missing_supported_target(tmp_path, minimal_crs_dict):
    """Verify check flags missing supported_target."""
    del minimal_crs_dict["supported_target"]
    config_file = tmp_path / "crs.yaml"
    config_file.write_text(yaml.dump(minimal_crs_dict), encoding="utf-8")

    report = CheckReport(str(tmp_path))
    config = validate_crs_config(config_file, report)
    assert config is None
    assert report.has_errors
    assert any("supported_target" in e.message for e in report.errors)


def test_check_schema_invalid_required_inputs(tmp_path, minimal_crs_dict):
    """Verify check flags invalid required_inputs."""
    minimal_crs_dict["required_inputs"] = ["nonexistent-input"]
    config_file = tmp_path / "crs.yaml"
    config_file.write_text(yaml.dump(minimal_crs_dict), encoding="utf-8")

    report = CheckReport(str(tmp_path))
    config = validate_crs_config(config_file, report)
    assert config is None
    assert report.has_errors
    assert any("required_inputs" in e.message for e in report.errors)


def test_check_schema_invalid_required_envs(tmp_path, minimal_crs_dict):
    """Verify check flags invalid environment variable names."""
    minimal_crs_dict["required_envs"] = ["INVALID-ENV-NAME!"]
    config_file = tmp_path / "crs.yaml"
    config_file.write_text(yaml.dump(minimal_crs_dict), encoding="utf-8")

    report = CheckReport(str(tmp_path))
    config = validate_crs_config(config_file, report)
    assert config is None
    assert report.has_errors
    assert any("required_envs" in e.message for e in report.errors)


def test_check_missing_dockerfile_references(tmp_path, minimal_crs_dict):
    """Verify check identifies missing referenced Dockerfiles."""
    config_file = tmp_path / "crs.yaml"
    config_file.write_text(yaml.dump(minimal_crs_dict), encoding="utf-8")

    report = CheckReport(str(tmp_path))
    config = validate_crs_config(config_file, report)
    assert config is not None

    validate_crs_files(tmp_path, config_file, config, report)
    assert report.has_errors
    assert any("references Dockerfile" in e.message for e in report.errors)


def test_check_missing_hcl_file(tmp_path, minimal_crs_dict):
    """Verify check flags missing prepare_phase HCL file."""
    minimal_crs_dict["prepare_phase"] = {"hcl": "docker-bake.hcl"}
    # Create the referenced dockerfile so only HCL is missing
    (tmp_path / "oss-crs").mkdir(parents=True)
    (tmp_path / "oss-crs" / "fuzzer.Dockerfile").write_text(
        "FROM alpine\n", encoding="utf-8"
    )
    config_file = tmp_path / "crs.yaml"
    config_file.write_text(yaml.dump(minimal_crs_dict), encoding="utf-8")

    report = CheckReport(str(tmp_path))
    config = validate_crs_config(config_file, report)
    assert config is not None

    validate_crs_files(tmp_path, config_file, config, report)
    assert report.has_errors
    assert any("Referenced HCL file not found" in e.message for e in report.errors)


def test_check_infra_module_dockerfile(tmp_path, minimal_crs_dict):
    """Verify oss-crs-infra:<module> references pass without requiring local files."""
    minimal_crs_dict["crs_run_phase"]["fuzzer"]["dockerfile"] = (
        "oss-crs-infra:default-builder"
    )
    config_file = tmp_path / "crs.yaml"
    config_file.write_text(yaml.dump(minimal_crs_dict), encoding="utf-8")

    report = CheckReport(str(tmp_path))
    config = validate_crs_config(config_file, report)
    assert config is not None

    validate_crs_files(tmp_path, config_file, config, report)
    assert not report.has_errors
    assert any("uses framework module" in p for p in report.passed_checks)


def test_check_remote_git_offline():
    """Verify remote Git URL with --offline fails early with actionable error."""
    report = CheckReport("https://github.com/ossf/oss-crs.git")
    repo_root, config_path = resolve_crs_target(
        "https://github.com/ossf/oss-crs.git",
        offline=True,
        report=report,
    )
    assert repo_root is None
    assert config_path is None
    assert report.has_errors
    assert "--offline enabled" in report.errors[0].message


def test_check_remote_git_clone_failure(tmp_path):
    """Verify handling when git clone fails."""
    report = CheckReport("https://github.com/nonexistent/repo.git")
    with (
        patch("shutil.which", return_value="/usr/bin/git"),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["git", "clone"],
            returncode=128,
            stdout="",
            stderr="fatal: repository not found",
        )
        repo_root, config_path = resolve_crs_target(
            "https://github.com/nonexistent/repo.git",
            work_dir=tmp_path,
            offline=False,
            report=report,
        )
        assert repo_root is None
        assert config_path is None
        assert report.has_errors
        assert "Failed to clone Git repository" in report.errors[0].message
        assert "fatal: repository not found" in report.errors[0].message


def test_check_functional_docker_not_found(tmp_path, minimal_crs_dict):
    """Verify functional validation reports error if Docker is not installed."""
    (tmp_path / "oss-crs").mkdir(parents=True)
    df = tmp_path / "oss-crs" / "fuzzer.Dockerfile"
    df.write_text("FROM alpine:latest\n", encoding="utf-8")
    config_file = tmp_path / "crs.yaml"
    config_file.write_text(yaml.dump(minimal_crs_dict), encoding="utf-8")

    report = CheckReport(str(tmp_path))
    config = validate_crs_config(config_file, report)
    assert config is not None

    with patch("shutil.which", return_value=None):
        validate_crs_functional(tmp_path, config_file, config, report)
        assert report.has_errors
        assert "Docker CLI executable not found" in report.errors[0].message


def test_check_functional_invalid_dockerfile(tmp_path, minimal_crs_dict):
    """Verify functional validation flags Dockerfile missing FROM instruction."""
    (tmp_path / "oss-crs").mkdir(parents=True)
    df = tmp_path / "oss-crs" / "fuzzer.Dockerfile"
    df.write_text("# Just comments\nENV FOO=bar\n", encoding="utf-8")
    config_file = tmp_path / "crs.yaml"
    config_file.write_text(yaml.dump(minimal_crs_dict), encoding="utf-8")

    report = CheckReport(str(tmp_path))
    config = validate_crs_config(config_file, report)
    assert config is not None

    with (
        patch("shutil.which", return_value="/usr/bin/docker"),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["docker", "info"],
            returncode=0,
            stdout="Server Version: 24.0.0",
            stderr="",
        )
        validate_crs_functional(tmp_path, config_file, config, report)
        assert report.has_errors
        assert any(
            "missing 'FROM'" in e.hint or "FROM" in e.message for e in report.errors
        )


def test_check_remote_git_missing_git_bin():
    """Verify error when git executable is not found."""
    report = CheckReport("https://github.com/ossf/oss-crs.git")
    with patch("shutil.which", return_value=None):
        repo_root, config_path = resolve_crs_target(
            "https://github.com/ossf/oss-crs.git",
            offline=False,
            report=report,
        )
        assert repo_root is None
        assert config_path is None
        assert report.has_errors
        assert "Git executable not found" in report.errors[0].message


def test_check_remote_git_clone_success(tmp_path):
    """Verify successful git clone resolution."""
    report = CheckReport("https://github.com/example/crs.git")
    dest_dir = tmp_path / "check_repos" / "crs_main"

    def fake_git_clone(*args, **kwargs):
        dest_dir.mkdir(parents=True, exist_ok=True)
        (dest_dir / "crs.yaml").write_text("name: cloned\n", encoding="utf-8")
        return subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout="", stderr=""
        )

    with (
        patch("shutil.which", return_value="/usr/bin/git"),
        patch("subprocess.run", side_effect=fake_git_clone),
    ):
        repo_root, config_path = resolve_crs_target(
            "https://github.com/example/crs.git",
            work_dir=tmp_path,
            offline=False,
            report=report,
        )
        assert repo_root == dest_dir
        assert config_path == dest_dir / "crs.yaml"


def test_check_target_is_non_yaml_file(tmp_path):
    """Verify error when given path is a file but not crs.yaml."""
    other_file = tmp_path / "readme.txt"
    other_file.write_text("hello", encoding="utf-8")
    report = CheckReport(str(other_file))
    repo_root, config_path = resolve_crs_target(str(other_file), report=report)
    assert repo_root is None
    assert config_path is None
    assert report.has_errors
    assert "not a CRS configuration file" in report.errors[0].message


def test_check_compose_syntax_warning(tmp_path, minimal_crs_dict, test_console):
    """Verify malformed optional compose file generates a warning."""
    (tmp_path / "oss-crs").mkdir(parents=True)
    (tmp_path / "oss-crs" / "fuzzer.Dockerfile").write_text(
        "FROM alpine\n", encoding="utf-8"
    )
    (tmp_path / "oss-crs" / "compose.yaml").write_text(
        "invalid: [broken", encoding="utf-8"
    )
    config_file = tmp_path / "crs.yaml"
    config_file.write_text(yaml.dump(minimal_crs_dict), encoding="utf-8")

    report = CheckReport(str(tmp_path))
    config = validate_crs_config(config_file, report)
    assert config is not None

    validate_crs_files(tmp_path, config_file, config, report)
    assert report.has_warnings
    assert not report.has_errors
    assert any("has YAML syntax errors" in w.message for w in report.warnings)
    # Warnings do not cause validation failure
    assert print_report(report, console=test_console) is True


def test_check_functional_success(tmp_path, minimal_crs_dict):
    """Verify functional validation passes when Docker and Dockerfiles are valid."""
    (tmp_path / "oss-crs").mkdir(parents=True)
    df = tmp_path / "oss-crs" / "fuzzer.Dockerfile"
    df.write_text('FROM alpine:latest\nENTRYPOINT ["/bin/sh"]\n', encoding="utf-8")
    config_file = tmp_path / "crs.yaml"
    config_file.write_text(yaml.dump(minimal_crs_dict), encoding="utf-8")

    report = CheckReport(str(tmp_path))
    config = validate_crs_config(config_file, report)
    assert config is not None

    with (
        patch("shutil.which", return_value="/usr/bin/docker"),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["docker", "info"],
            returncode=0,
            stdout="Server Version: 24.0.0",
            stderr="",
        )
        validate_crs_functional(tmp_path, config_file, config, report)
        assert not report.has_errors
        assert any("Dockerfile syntax confirmed" in p for p in report.passed_checks)


def test_check_functional_docker_daemon_down(tmp_path, minimal_crs_dict):
    """Verify functional validation reports error when docker daemon returns non-zero."""
    (tmp_path / "oss-crs").mkdir(parents=True)
    df = tmp_path / "oss-crs" / "fuzzer.Dockerfile"
    df.write_text("FROM alpine:latest\n", encoding="utf-8")
    config_file = tmp_path / "crs.yaml"
    config_file.write_text(yaml.dump(minimal_crs_dict), encoding="utf-8")

    report = CheckReport(str(tmp_path))
    config = validate_crs_config(config_file, report)
    assert config is not None

    with (
        patch("shutil.which", return_value="/usr/bin/docker"),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["docker", "info"],
            returncode=1,
            stdout="",
            stderr="Cannot connect to Docker daemon",
        )
        validate_crs_functional(tmp_path, config_file, config, report)
        assert report.has_errors
        assert "Docker daemon is not running" in report.errors[0].message

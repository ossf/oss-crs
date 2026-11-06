# SPDX-License-Identifier: MIT
"""Unit tests for the interactive setup CLI wizard (oss_crs/src/cli/setup.py)."""

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import oss_crs.src.cli.setup as setup_mod
from oss_crs.src.cli.setup import (
    CheckResult,
    SetupRunner,
    SetupStep,
    _litellm_config_would_change,
    add_setup_command,
    cgroup_setup_step,
    check_controllers,
    check_delegation,
    check_directory,
    check_docker_driver,
    controller_setup_step,
    docker_setup_step,
    handle_setup,
)


@pytest.fixture(autouse=True)
def mock_uid_gid(monkeypatch):
    """Ensure os.getuid and os.getgid are deterministic across platforms."""
    monkeypatch.setattr(os, "getuid", lambda: 1000, raising=False)
    monkeypatch.setattr(os, "getgid", lambda: 1000, raising=False)


# =====================================================================
# Data Classes & Helper Functions
# =====================================================================


def test_check_result_defaults():
    res = CheckResult(ok=True)
    assert res.ok is True
    assert res.detail == ""
    assert res.needs_fix is False

    res_fix = CheckResult(ok=False, detail="Missing driver", needs_fix=True)
    assert res_fix.ok is False
    assert res_fix.detail == "Missing driver"
    assert res_fix.needs_fix is True


def test_setup_step():
    step = SetupStep(
        title="Test Step",
        description="A step description",
        commands=lambda: [("action 1", "echo 1")],
        verify=lambda: True,
        skip_message="Skip step message",
    )
    assert step.title == "Test Step"
    assert step.commands() == [("action 1", "echo 1")]
    assert step.verify() is True
    assert step.skip_message == "Skip step message"


def test_litellm_config_would_change(tmp_path: Path):
    cfg_file = tmp_path / "litellm-config.yaml"
    cfg_file.write_text("model_list:\n  - model_name: gpt-4\n")

    with patch(
        "oss_crs.src.cli.setup.override_litellm_proxy",
        return_value={"model_list": [{"model_name": "gpt-4-changed"}]},
    ):
        changed = _litellm_config_would_change(
            cfg_file, "KEY_ENV", "BASE_ENV", ["openai"]
        )
        assert changed is True

    with patch(
        "oss_crs.src.cli.setup.override_litellm_proxy",
        return_value={"model_list": [{"model_name": "gpt-4"}]},
    ):
        changed = _litellm_config_would_change(
            cfg_file, "KEY_ENV", "BASE_ENV", ["openai"]
        )
        assert changed is False


# =====================================================================
# Requirement Check Functions
# =====================================================================


def test_check_docker_driver():
    with patch(
        "oss_crs.src.cli.setup.check_docker_cgroup_driver",
        return_value=(False, "docker_not_found"),
    ):
        res = check_docker_driver()
        assert res.ok is False
        assert "Docker not found in PATH" in res.detail
        assert res.needs_fix is False

    with patch(
        "oss_crs.src.cli.setup.check_docker_cgroup_driver",
        return_value=(False, "timeout"),
    ):
        res = check_docker_driver()
        assert res.ok is False
        assert "Docker daemon not responding" in res.detail
        assert res.needs_fix is False

    with patch(
        "oss_crs.src.cli.setup.check_docker_cgroup_driver",
        return_value=(True, "cgroupfs"),
    ):
        res = check_docker_driver()
        assert res.ok is True
        assert res.detail == "cgroupfs"
        assert res.needs_fix is False

    with patch(
        "oss_crs.src.cli.setup.check_docker_cgroup_driver",
        return_value=(False, "systemd"),
    ):
        res = check_docker_driver()
        assert res.ok is False
        assert "using 'systemd', needs 'cgroupfs'" in res.detail
        assert res.needs_fix is True


def test_check_delegation():
    with patch(
        "oss_crs.src.cli.setup.check_cgroup_delegation",
        return_value=(True, []),
    ):
        res = check_delegation()
        assert res.ok is True
        assert "cpuset, memory enabled" in res.detail
        assert res.needs_fix is False

    with patch(
        "oss_crs.src.cli.setup.check_cgroup_delegation",
        return_value=(False, ["memory"]),
    ):
        res = check_delegation()
        assert res.ok is False
        assert "missing: memory" in res.detail
        assert res.needs_fix is True

    with patch(
        "oss_crs.src.cli.setup.check_cgroup_delegation",
        return_value=(False, []),
    ):
        res = check_delegation()
        assert res.ok is False
        assert res.detail == "not configured"
        assert res.needs_fix is True


def test_check_directory():
    with (
        patch(
            "oss_crs.src.cli.setup.check_oss_crs_directory",
            return_value=(True, "ok"),
        ),
        patch(
            "oss_crs.src.cli.setup.get_user_cgroup_base",
            return_value=Path("/cgroup/base"),
        ),
    ):
        res = check_directory()
        assert res.ok is True
        assert str(Path("/cgroup/base")) in res.detail
        assert res.needs_fix is False

    with patch(
        "oss_crs.src.cli.setup.check_oss_crs_directory",
        return_value=(False, "not_exists"),
    ):
        res = check_directory()
        assert res.ok is False
        assert res.detail == "directory does not exist"
        assert res.needs_fix is True

    with patch(
        "oss_crs.src.cli.setup.check_oss_crs_directory",
        return_value=(False, "permission_denied"),
    ):
        res = check_directory()
        assert res.ok is False
        assert res.detail == "permission denied"
        assert res.needs_fix is True

    with patch(
        "oss_crs.src.cli.setup.check_oss_crs_directory",
        return_value=(False, "custom_error"),
    ):
        res = check_directory()
        assert res.ok is False
        assert res.detail == "custom_error"
        assert res.needs_fix is True


def test_check_controllers():
    with patch(
        "oss_crs.src.cli.setup.check_oss_crs_controllers",
        return_value=(True, []),
    ):
        res = check_controllers()
        assert res.ok is True
        assert "cpuset, memory enabled" in res.detail

    with patch(
        "oss_crs.src.cli.setup.check_oss_crs_controllers",
        return_value=(False, ["cpuset"]),
    ):
        res = check_controllers()
        assert res.ok is False
        assert "missing: cpuset" in res.detail
        assert res.needs_fix is True

    with patch(
        "oss_crs.src.cli.setup.check_oss_crs_controllers",
        return_value=(False, []),
    ):
        res = check_controllers()
        assert res.ok is False
        assert res.detail == "not enabled"
        assert res.needs_fix is True


# =====================================================================
# Setup Steps Definitions
# =====================================================================


def test_step_factories():
    docker_step = docker_setup_step()
    assert "cgroupfs" in docker_step.title
    assert docker_step.commands is not None
    assert docker_step.skip_message != ""

    with patch(
        "oss_crs.src.cli.setup.check_docker_cgroup_driver",
        return_value=(True, "cgroupfs"),
    ):
        assert docker_step.verify() is True

    cgroup_step = cgroup_setup_step()
    assert "delegation" in cgroup_step.title
    assert cgroup_step.commands is not None

    ctrl_step = controller_setup_step()
    assert "controllers" in ctrl_step.title
    cmds = ctrl_step.commands()
    assert len(cmds) == 1
    assert "+cpuset +memory" in cmds[0][1]


# =====================================================================
# SetupRunner Tests
# =====================================================================


def test_setup_runner_print_status():
    runner = SetupRunner(yes=True)
    with patch.object(runner.console, "print") as mock_print:
        runner.print_status("Test Label", True, "ok detail")
        mock_print.assert_called_once()
        assert "Test Label" in str(mock_print.call_args)


def test_setup_runner_run_command_user_aborted():
    runner = SetupRunner(yes=False)
    with patch("oss_crs.src.cli.setup.confirm", return_value=None):
        assert runner.run_command("Test action", "echo test") is False


def test_setup_runner_run_command_user_skipped():
    runner = SetupRunner(yes=False)
    with patch("oss_crs.src.cli.setup.confirm", return_value=False):
        assert runner.run_command("Test action", "echo test") is False


def test_setup_runner_run_command_success():
    runner = SetupRunner(yes=True)
    with (
        patch("oss_crs.src.cli.setup.confirm", return_value=True),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0)
        assert runner.run_command("Test action", "echo test") is True


def test_setup_runner_run_command_failure():
    runner = SetupRunner(yes=True)
    with (
        patch("oss_crs.src.cli.setup.confirm", return_value=True),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=1, stderr="permission denied")
        assert runner.run_command("Test action", "echo test") is False


def test_setup_runner_run_command_timeout():
    runner = SetupRunner(yes=True)
    with (
        patch("oss_crs.src.cli.setup.confirm", return_value=True),
        patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired("cmd", 120),
        ),
    ):
        assert runner.run_command("Test action", "echo test") is False


def test_setup_runner_run_command_generic_error():
    runner = SetupRunner(yes=True)
    with (
        patch("oss_crs.src.cli.setup.confirm", return_value=True),
        patch("subprocess.run", side_effect=OSError("OS error")),
    ):
        assert runner.run_command("Test action", "echo test") is False


def test_setup_runner_execute_step():
    runner = SetupRunner(yes=True)

    # 1. User aborts step
    with patch("oss_crs.src.cli.setup.confirm", return_value=None):
        step = SetupStep("Step 1", "Desc", lambda: [("Action", "cmd")])
        assert runner.execute_step(step) is False

    # 2. User skips step
    with patch("oss_crs.src.cli.setup.confirm", return_value=False):
        step = SetupStep(
            "Step 2", "Desc", lambda: [("Action", "cmd")], skip_message="Manual"
        )
        assert runner.execute_step(step) is False

    # 3. Command inside step fails
    with (
        patch("oss_crs.src.cli.setup.confirm", return_value=True),
        patch.object(runner, "run_command", return_value=False),
    ):
        step = SetupStep("Step 3", "Desc", lambda: [("Action", "cmd")])
        assert runner.execute_step(step) is False

    # 4. Step verification fails
    with (
        patch("oss_crs.src.cli.setup.confirm", return_value=True),
        patch.object(runner, "run_command", return_value=True),
    ):
        step = SetupStep(
            "Step 4", "Desc", lambda: [("Action", "cmd")], verify=lambda: False
        )
        assert runner.execute_step(step) is False

    # 5. Full step success
    with (
        patch("oss_crs.src.cli.setup.confirm", return_value=True),
        patch.object(runner, "run_command", return_value=True),
    ):
        step = SetupStep(
            "Step 5", "Desc", lambda: [("Action", "cmd")], verify=lambda: True
        )
        assert runner.execute_step(step) is True


def test_setup_runner_run_checks_skips_controllers_if_dir_failed():
    runner = SetupRunner(yes=True)
    mock_ctrl = MagicMock()
    mock_reqs = [
        ("Docker cgroup driver", lambda: CheckResult(ok=True), "docker"),
        ("Cgroup v2 delegation", lambda: CheckResult(ok=True), "delegation"),
        (
            "oss-crs cgroup directory",
            lambda: CheckResult(ok=False, needs_fix=True),
            "directory",
        ),
        ("oss-crs controllers", mock_ctrl, "controllers"),
    ]

    with patch.object(setup_mod, "REQUIREMENTS", mock_reqs):
        results = runner.run_checks()
        # Controllers check must be skipped because directory check failed
        mock_ctrl.assert_not_called()
        assert "controllers" not in results
        assert runner.needs_fix("directory") is True
        assert runner.all_ok() is False


# =====================================================================
# SetupRunner Cgroup & LLM Flows
# =====================================================================


def test_setup_runner_cgroup_fatal_docker_error():
    runner = SetupRunner(yes=True)
    mock_reqs = [
        (
            "Docker cgroup driver",
            lambda: CheckResult(
                ok=False, detail="Docker not found in PATH", needs_fix=False
            ),
            "docker",
        ),
    ]
    with patch.object(setup_mod, "REQUIREMENTS", mock_reqs):
        assert runner._run_cgroup_setup(check_only=False) is False


def test_setup_runner_cgroup_all_ok():
    runner = SetupRunner(yes=True)
    mock_reqs = [
        ("Docker cgroup driver", lambda: CheckResult(ok=True), "docker"),
        ("Cgroup v2 delegation", lambda: CheckResult(ok=True), "delegation"),
        ("oss-crs cgroup directory", lambda: CheckResult(ok=True), "directory"),
        ("oss-crs controllers", lambda: CheckResult(ok=True), "controllers"),
    ]
    with patch.object(setup_mod, "REQUIREMENTS", mock_reqs):
        assert runner._run_cgroup_setup(check_only=False) is True


def test_setup_runner_cgroup_check_only_failure():
    runner = SetupRunner(yes=True)
    mock_reqs = [
        (
            "Docker cgroup driver",
            lambda: CheckResult(ok=False, needs_fix=True),
            "docker",
        ),
        ("Cgroup v2 delegation", lambda: CheckResult(ok=True), "delegation"),
        ("oss-crs cgroup directory", lambda: CheckResult(ok=True), "directory"),
        ("oss-crs controllers", lambda: CheckResult(ok=True), "controllers"),
    ]
    with patch.object(setup_mod, "REQUIREMENTS", mock_reqs):
        assert runner._run_cgroup_setup(check_only=True) is False


def test_setup_runner_cgroup_interactive_fixes():
    runner = SetupRunner(yes=True)

    # Initial check fails docker and delegation; second check (after step) passes all
    docker_results = [CheckResult(ok=False, needs_fix=True), CheckResult(ok=True)]
    deleg_results = [CheckResult(ok=False, needs_fix=True), CheckResult(ok=True)]

    mock_reqs = [
        ("Docker cgroup driver", lambda: docker_results.pop(0), "docker"),
        ("Cgroup v2 delegation", lambda: deleg_results.pop(0), "delegation"),
        ("oss-crs cgroup directory", lambda: CheckResult(ok=True), "directory"),
        ("oss-crs controllers", lambda: CheckResult(ok=True), "controllers"),
    ]

    with (
        patch.object(setup_mod, "REQUIREMENTS", mock_reqs),
        patch.object(runner, "execute_step", return_value=True) as mock_exec,
    ):
        success = runner._run_cgroup_setup(check_only=False)
        assert success is True
        assert mock_exec.call_count == 2


def test_setup_runner_cgroup_controller_direct_write():
    runner = SetupRunner(yes=True)
    ctrl_results = [CheckResult(ok=False, needs_fix=True), CheckResult(ok=True)]

    mock_reqs = [
        ("Docker cgroup driver", lambda: CheckResult(ok=True), "docker"),
        ("Cgroup v2 delegation", lambda: CheckResult(ok=True), "delegation"),
        ("oss-crs cgroup directory", lambda: CheckResult(ok=True), "directory"),
        ("oss-crs controllers", lambda: ctrl_results.pop(0), "controllers"),
    ]

    with (
        patch.object(setup_mod, "REQUIREMENTS", mock_reqs),
        patch(
            "oss_crs.src.cli.setup.enable_oss_crs_controllers",
            return_value=(True, "Enabled"),
        ),
    ):
        success = runner._run_cgroup_setup(check_only=False)
        assert success is True


def test_setup_runner_cgroup_controller_fallback_to_step():
    runner = SetupRunner(yes=True)
    ctrl_results = [CheckResult(ok=False, needs_fix=True), CheckResult(ok=True)]

    mock_reqs = [
        ("Docker cgroup driver", lambda: CheckResult(ok=True), "docker"),
        ("Cgroup v2 delegation", lambda: CheckResult(ok=True), "delegation"),
        ("oss-crs cgroup directory", lambda: CheckResult(ok=True), "directory"),
        ("oss-crs controllers", lambda: ctrl_results.pop(0), "controllers"),
    ]

    with (
        patch.object(setup_mod, "REQUIREMENTS", mock_reqs),
        patch(
            "oss_crs.src.cli.setup.enable_oss_crs_controllers",
            return_value=(False, "Permission denied"),
        ),
        patch.object(runner, "execute_step", return_value=True) as mock_exec,
    ):
        success = runner._run_cgroup_setup(check_only=False)
        assert success is True
        mock_exec.assert_called_once()


def test_setup_runner_llm_proxy_setup_flows(tmp_path: Path):
    runner = SetupRunner(yes=True)

    # 1. User skips proxy
    with patch("oss_crs.src.cli.setup.confirm", return_value=False):
        runner._run_llm_proxy_setup()

    # 2. No example configs found
    with (
        patch("oss_crs.src.cli.setup.confirm", return_value=True),
        patch.object(runner, "_find_example_litellm_configs", return_value=[]),
    ):
        runner._run_llm_proxy_setup()

    # 3. User selects no providers
    cfg1 = tmp_path / "litellm-config.yaml"
    cfg1.write_text("dummy")
    with (
        patch("oss_crs.src.cli.setup.confirm", return_value=True),
        patch.object(runner, "_find_example_litellm_configs", return_value=[cfg1]),
        patch("questionary.checkbox") as mock_cb,
    ):
        mock_cb.return_value.ask.return_value = []
        runner._run_llm_proxy_setup()

    # 4. User aborts key_env prompt
    with (
        patch("oss_crs.src.cli.setup.confirm", return_value=True),
        patch.object(runner, "_find_example_litellm_configs", return_value=[cfg1]),
        patch("questionary.checkbox") as mock_cb,
        patch("questionary.text") as mock_text,
    ):
        mock_cb.return_value.ask.return_value = ["openai"]
        mock_text.return_value.ask.return_value = ""  # empty key_env
        runner._run_llm_proxy_setup()

    # 5. User aborts base_url prompt
    with (
        patch("oss_crs.src.cli.setup.confirm", side_effect=[True, True]),
        patch.object(runner, "_find_example_litellm_configs", return_value=[cfg1]),
        patch("questionary.checkbox") as mock_cb,
        patch("questionary.text") as mock_text,
    ):
        mock_cb.return_value.ask.return_value = ["openai"]
        mock_text.return_value.ask.side_effect = ["KEY_ENV", ""]  # empty base
        runner._run_llm_proxy_setup()

    # 6. No affected configs
    with (
        patch("oss_crs.src.cli.setup.confirm", side_effect=[True, False]),
        patch.object(runner, "_find_example_litellm_configs", return_value=[cfg1]),
        patch("questionary.checkbox") as mock_cb,
        patch("questionary.text") as mock_text,
        patch(
            "oss_crs.src.cli.setup._litellm_config_would_change",
            return_value=False,
        ),
    ):
        mock_cb.return_value.ask.return_value = ["openai"]
        mock_text.return_value.ask.return_value = "KEY_ENV"
        runner._run_llm_proxy_setup()

    # 7. User skips final confirmation
    with (
        patch("oss_crs.src.cli.setup.confirm", side_effect=[True, False, False]),
        patch.object(runner, "_find_example_litellm_configs", return_value=[cfg1]),
        patch("questionary.checkbox") as mock_cb,
        patch("questionary.text") as mock_text,
        patch(
            "oss_crs.src.cli.setup._litellm_config_would_change",
            return_value=True,
        ),
    ):
        mock_cb.return_value.ask.return_value = ["openai"]
        mock_text.return_value.ask.return_value = "KEY_ENV"
        runner._run_llm_proxy_setup()

    # 8. Complete successful application
    with (
        patch("oss_crs.src.cli.setup.confirm", side_effect=[True, True, True]),
        patch.object(runner, "_find_example_litellm_configs", return_value=[cfg1]),
        patch("questionary.checkbox") as mock_cb,
        patch("questionary.text") as mock_text,
        patch(
            "oss_crs.src.cli.setup._litellm_config_would_change",
            return_value=True,
        ),
        patch("oss_crs.src.cli.setup.apply_litellm_proxy_to_file") as mock_apply,
    ):
        mock_cb.return_value.ask.return_value = ["openai"]
        mock_text.return_value.ask.side_effect = ["KEY_ENV", "BASE_ENV"]
        runner._run_llm_proxy_setup()
        mock_apply.assert_called_once_with(cfg1, "KEY_ENV", "BASE_ENV", ["openai"])


def test_setup_runner_run_method():
    runner = SetupRunner(yes=True)
    with (
        patch.object(runner, "_run_llm_proxy_setup") as mock_llm,
        patch.object(runner, "_run_cgroup_setup", return_value=True) as mock_cg,
    ):
        res = runner.run(check_only=False)
        assert res is True
        mock_llm.assert_called_once()
        mock_cg.assert_called_once_with(False)

    with (
        patch.object(runner, "_run_llm_proxy_setup") as mock_llm,
        patch.object(runner, "_run_cgroup_setup", return_value=False) as mock_cg,
    ):
        res = runner.run(check_only=True)
        assert res is False
        mock_llm.assert_not_called()
        mock_cg.assert_called_once_with(True)


# =====================================================================
# CLI Entrypoint Tests
# =====================================================================


def test_add_setup_command():
    import argparse

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="subcommand")
    add_setup_command(subparsers)

    args = parser.parse_args(["setup", "--check", "-y"])
    assert args.subcommand == "setup"
    assert args.check is True
    assert args.yes is True


def test_handle_setup():
    args = SimpleNamespace(yes=True, check=False)
    with patch.object(SetupRunner, "run", return_value=True) as mock_run:
        result = handle_setup(args)
        assert result is True
        mock_run.assert_called_once_with(check_only=False)

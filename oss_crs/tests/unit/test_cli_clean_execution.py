# SPDX-License-Identifier: MIT
"""Unit tests for the clean CLI command execution, planning, and pruning (oss_crs/src/cli/clean.py)."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import docker.errors

# Ensure fcntl is mocked on Windows platforms for target/workdir imports
if "fcntl" not in sys.modules:
    try:
        import fcntl  # noqa: F401
    except ImportError:
        sys.modules["fcntl"] = MagicMock()

from oss_crs.src.cli.clean import (
    CleanPlan,
    _dir_size,
    add_clean_command,
    build_clean_plan,
    display_clean_plan,
    execute_clean_plan,
    handle_clean,
)

# =====================================================================
# CleanPlan Dataclass Tests
# =====================================================================


def test_clean_plan_properties():
    plan = CleanPlan()
    assert plan.all_images == []
    assert plan.is_empty is True

    plan.prepare_images = ["img-prep:latest"]
    plan.builder_images = ["img-builder:1"]
    plan.snapshot_images = ["img-snapshot:1"]
    plan.target_images = ["img-target:1"]
    plan.run_images = ["img-run:1"]
    plan.artifact_dirs = [Path("/data/run-1")]

    assert len(plan.all_images) == 5
    assert plan.is_empty is False


# =====================================================================
# _dir_size Tests
# =====================================================================


def test_dir_size_normal(tmp_path: Path):
    d = tmp_path / "test_dir"
    d.mkdir()
    (d / "file1.txt").write_bytes(b"A" * 500)
    (d / "file2.txt").write_bytes(b"B" * 600)

    size_str = _dir_size(d)
    assert "1.1 KB" in size_str or "1.0 KB" in size_str or "KB" in size_str


def test_dir_size_permission_error_docker_success(tmp_path: Path):
    d = tmp_path / "test_dir"
    d.mkdir()

    with (
        patch.object(Path, "rglob", side_effect=PermissionError("root owned")),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="2048000\t/data\n")
        size_str = _dir_size(d)
        assert "MB" in size_str or "KB" in size_str


def test_dir_size_permission_error_docker_failure(tmp_path: Path):
    d = tmp_path / "test_dir"
    d.mkdir()

    with (
        patch.object(Path, "rglob", side_effect=PermissionError("root owned")),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        assert _dir_size(d) == "?"

    with (
        patch.object(Path, "rglob", side_effect=PermissionError("root owned")),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="not-an-int\n")
        assert _dir_size(d) == "?"


# =====================================================================
# display_clean_plan Tests
# =====================================================================


def test_display_clean_plan():
    plan = CleanPlan(
        prepare_images=["prep:1"],
        builder_images=["builder:1"],
        snapshot_images=["snap:1"],
        target_images=["target:1"],
        run_images=["run:1"],
        artifact_dirs=[Path("/tmp/artifacts/1")],
    )

    with (
        patch("oss_crs.src.cli.clean.get_console") as mock_console,
        patch("oss_crs.src.cli.clean._dir_size", return_value="10 MB"),
    ):
        display_clean_plan(plan)
        assert mock_console.return_value.print.called


# =====================================================================
# execute_clean_plan Tests (Docker Prune & Artifact Cleanup)
# =====================================================================


def test_execute_clean_plan_success(tmp_path: Path):
    dir1 = tmp_path / "art1"
    dir1.mkdir()

    plan = CleanPlan(
        prepare_images=["img-a:latest"],
        run_images=["img-b:latest"],
        artifact_dirs=[dir1],
    )

    with patch("oss_crs.src.cli.clean.docker.from_env") as mock_docker:
        mock_client = mock_docker.return_value
        success = execute_clean_plan(plan)
        assert success is True
        assert mock_client.images.remove.call_count == 2
        mock_client.images.remove.assert_any_call("img-a:latest", force=True)
        mock_client.images.remove.assert_any_call("img-b:latest", force=True)
        assert not dir1.exists()


def test_execute_clean_plan_image_not_found_is_skipped(tmp_path: Path):
    plan = CleanPlan(prepare_images=["already-gone:latest"])

    with patch("oss_crs.src.cli.clean.docker.from_env") as mock_docker:
        mock_client = mock_docker.return_value
        mock_client.images.remove.side_effect = docker.errors.ImageNotFound("gone")
        success = execute_clean_plan(plan)
        # ImageNotFound should be skipped and not treated as a failure
        assert success is True


def test_execute_clean_plan_image_api_error_recorded():
    plan = CleanPlan(prepare_images=["locked-img:latest"])

    with patch("oss_crs.src.cli.clean.docker.from_env") as mock_docker:
        mock_client = mock_docker.return_value
        mock_client.images.remove.side_effect = docker.errors.APIError("conflict")
        success = execute_clean_plan(plan)
        assert success is False


def test_execute_clean_plan_directory_permission_retry_with_docker(tmp_path: Path):
    dir1 = tmp_path / "perm_dir"
    dir1.mkdir()
    plan = CleanPlan(artifact_dirs=[dir1])

    with (
        patch("oss_crs.src.cli.clean.docker.from_env"),
        patch("shutil.rmtree", side_effect=PermissionError("denied")),
        patch("oss_crs.src.cli.clean.rm_with_docker") as mock_docker_rm,
    ):
        success = execute_clean_plan(plan)
        assert success is True
        mock_docker_rm.assert_called_once_with(dir1)


def test_execute_clean_plan_directory_docker_retry_failure(tmp_path: Path):
    dir1 = tmp_path / "perm_dir"
    dir1.mkdir()
    plan = CleanPlan(artifact_dirs=[dir1])

    with (
        patch("oss_crs.src.cli.clean.docker.from_env"),
        patch("shutil.rmtree", side_effect=PermissionError("denied")),
        patch(
            "oss_crs.src.cli.clean.rm_with_docker",
            side_effect=RuntimeError("docker fail"),
        ),
    ):
        success = execute_clean_plan(plan)
        assert success is False


def test_execute_clean_plan_directory_generic_exception(tmp_path: Path):
    dir1 = tmp_path / "err_dir"
    dir1.mkdir()
    plan = CleanPlan(artifact_dirs=[dir1])

    with (
        patch("oss_crs.src.cli.clean.docker.from_env"),
        patch("shutil.rmtree", side_effect=OSError("filesystem corrupted")),
    ):
        success = execute_clean_plan(plan)
        assert success is False


# =====================================================================
# build_clean_plan Tests
# =====================================================================


def test_build_clean_plan():
    mock_compose = MagicMock()
    mock_compose.work_dir = Path("/work")

    with (
        patch(
            "oss_crs.src.cli.clean.discover_prepare_images",
            return_value=["prep:1"],
        ),
        patch(
            "oss_crs.src.cli.clean.discover_build_target_images",
            return_value=(["b:1"], ["s:1"], ["t:1"]),
        ),
        patch(
            "oss_crs.src.cli.clean.discover_run_images",
            return_value=["run:1"],
        ),
        patch(
            "oss_crs.src.cli.clean.discover_artifact_dirs",
            return_value=[Path("/work/run-1")],
        ),
    ):
        # 1. All phases + artifacts
        plan_all = build_clean_plan(
            mock_compose, subcommand=None, target=None, include_artifacts=True
        )
        assert plan_all.prepare_images == ["prep:1"]
        assert plan_all.builder_images == ["b:1"]
        assert plan_all.snapshot_images == ["s:1"]
        assert plan_all.target_images == ["t:1"]
        assert plan_all.run_images == ["run:1"]
        assert len(plan_all.artifact_dirs) == 1

        # 2. Prepare only
        plan_prep = build_clean_plan(
            mock_compose, subcommand="prepare", target=None, include_artifacts=False
        )
        assert plan_prep.prepare_images == ["prep:1"]
        assert plan_prep.builder_images == []
        assert plan_prep.run_images == []

        # 3. Build-target only
        plan_bt = build_clean_plan(
            mock_compose,
            subcommand="build-target",
            target=None,
            include_artifacts=False,
        )
        assert plan_bt.builder_images == ["b:1"]
        assert plan_bt.prepare_images == []

        # 4. Run only
        plan_run = build_clean_plan(
            mock_compose, subcommand="run", target=None, include_artifacts=False
        )
        assert plan_run.run_images == ["run:1"]
        assert plan_run.prepare_images == []


# =====================================================================
# handle_clean CLI Entrypoint Tests
# =====================================================================


def test_handle_clean_missing_compose_file():
    args = SimpleNamespace(compose_file=None)
    assert handle_clean(args) is False


def test_handle_clean_empty_plan():
    args = SimpleNamespace(
        compose_file=Path("compose.yaml"),
        work_dir=Path(".workdir"),
        clean_subcommand=None,
        artifacts=False,
    )

    with (
        patch("oss_crs.src.crs_compose.CRSCompose.from_yaml_file"),
        patch(
            "oss_crs.src.cli.clean.build_clean_plan",
            return_value=CleanPlan(),
        ),
    ):
        result = handle_clean(args)
        assert result is True


def test_handle_clean_aborted_by_user():
    args = SimpleNamespace(
        compose_file=Path("compose.yaml"),
        work_dir=Path(".workdir"),
        clean_subcommand="run",
        artifacts=False,
        yes=False,
    )
    non_empty_plan = CleanPlan(run_images=["run:1"])

    with (
        patch("oss_crs.src.crs_compose.CRSCompose.from_yaml_file"),
        patch(
            "oss_crs.src.cli.clean.build_clean_plan",
            return_value=non_empty_plan,
        ),
        patch("oss_crs.src.cli.clean.display_clean_plan"),
        patch("oss_crs.src.cli.clean.confirm", return_value=False),
    ):
        result = handle_clean(args)
        assert result is True  # Aborting clean is not a fatal process error


def test_handle_clean_confirmed_and_executed():
    args = SimpleNamespace(
        compose_file=Path("compose.yaml"),
        work_dir=Path(".workdir"),
        clean_subcommand="run",
        artifacts=True,
        target_proj_path=Path("/target/proj"),
        target_repo_path=Path("/target/repo"),
        yes=True,
    )
    non_empty_plan = CleanPlan(run_images=["run:1"])

    with (
        patch("oss_crs.src.crs_compose.CRSCompose.from_yaml_file"),
        patch("oss_crs.src.target.Target"),
        patch(
            "oss_crs.src.cli.clean.build_clean_plan",
            return_value=non_empty_plan,
        ),
        patch("oss_crs.src.cli.clean.display_clean_plan"),
        patch("oss_crs.src.cli.clean.confirm", return_value=True),
        patch(
            "oss_crs.src.cli.clean.execute_clean_plan", return_value=True
        ) as mock_exec,
    ):
        result = handle_clean(args)
        assert result is True
        mock_exec.assert_called_once_with(non_empty_plan)


# =====================================================================
# CLI Registration
# =====================================================================


def test_add_clean_command():
    import argparse

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="subcommand")

    def dummy_common_args(p):
        p.add_argument("--compose-file", type=Path)
        p.add_argument("--work-dir", type=Path)

    add_clean_command(subparsers, dummy_common_args)

    # Test top-level clean parser
    args_clean = parser.parse_args(["clean", "-y", "--artifacts"])
    assert args_clean.subcommand == "clean"
    assert args_clean.yes is True
    assert args_clean.artifacts is True

    # Test subcommand parser
    args_run = parser.parse_args(["clean", "run", "-y"])
    assert args_run.subcommand == "clean"
    assert args_run.clean_subcommand == "run"
    assert args_run.yes is True

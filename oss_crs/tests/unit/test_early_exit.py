"""Tests for early exit functionality."""

import time
from pathlib import Path


from oss_crs.src.ui import EarlyExitConfig, MultiTaskProgress


class TestEarlyExitCLIFlag:
    """Tests for --early-exit CLI flag."""

    def test_early_exit_flag_accepted_by_argparse(self):
        """--early-exit flag is accepted by argparse for run command."""
        import argparse
        from oss_crs.src.cli.crs_compose import add_run_command

        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        add_run_command(subparsers)

        # Parse with --early-exit flag
        args = parser.parse_args(
            [
                "run",
                "--compose-file",
                "compose.yaml",
                "--fuzz-proj-path",
                "/tmp/target",
                "--target-harness",
                "test_harness",
                "--early-exit",
            ]
        )
        assert args.early_exit is True

    def test_early_exit_flag_default_false(self):
        """early_exit defaults to False when not provided."""
        import argparse
        from oss_crs.src.cli.crs_compose import add_run_command

        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="command")
        add_run_command(subparsers)

        # Parse without --early-exit flag
        args = parser.parse_args(
            [
                "run",
                "--compose-file",
                "compose.yaml",
                "--fuzz-proj-path",
                "/tmp/target",
                "--target-harness",
                "test_harness",
            ]
        )
        assert args.early_exit is False


class TestEarlyExitConfig:
    """Tests for EarlyExitConfig dataclass."""

    def test_basic_instantiation(self, tmp_path: Path):
        """EarlyExitConfig can be instantiated with required fields."""
        config = EarlyExitConfig(
            watch_dirs=[tmp_path / "submit1", tmp_path / "submit2"],
            artifact_subdir="povs",
        )
        assert len(config.watch_dirs) == 2
        assert config.artifact_subdir == "povs"
        assert config.poll_interval == 2.0  # default

    def test_custom_poll_interval(self, tmp_path: Path):
        """Poll interval can be customized."""
        config = EarlyExitConfig(
            watch_dirs=[tmp_path],
            artifact_subdir="patches",
            poll_interval=0.5,
        )
        assert config.poll_interval == 0.5


class TestCheckEarlyExit:
    """Tests for _check_early_exit method."""

    def test_returns_false_when_no_config(self):
        """Returns False when early_exit_config is None."""
        progress = MultiTaskProgress(tasks=[], title="Test")
        assert progress._check_early_exit() is False

    def test_returns_false_when_dir_not_exists(self, tmp_path: Path):
        """Returns False when watch directory doesn't exist."""
        config = EarlyExitConfig(
            watch_dirs=[tmp_path / "nonexistent"],
            artifact_subdir="povs",
        )
        progress = MultiTaskProgress(tasks=[], title="Test", early_exit_config=config)
        assert progress._check_early_exit() is False

    def test_returns_false_when_artifact_dir_empty(self, tmp_path: Path):
        """Returns False when artifact subdirectory exists but is empty."""
        submit_dir = tmp_path / "submit"
        submit_dir.mkdir()
        (submit_dir / "povs").mkdir()

        config = EarlyExitConfig(
            watch_dirs=[submit_dir],
            artifact_subdir="povs",
        )
        progress = MultiTaskProgress(tasks=[], title="Test", early_exit_config=config)
        assert progress._check_early_exit() is False

    def test_returns_true_when_artifact_exists(self, tmp_path: Path):
        """Returns True when artifact file exists in subdirectory."""
        submit_dir = tmp_path / "submit"
        submit_dir.mkdir()
        povs_dir = submit_dir / "povs"
        povs_dir.mkdir()
        (povs_dir / "crash_001.pov").write_text("crash data")

        config = EarlyExitConfig(
            watch_dirs=[submit_dir],
            artifact_subdir="povs",
        )
        progress = MultiTaskProgress(tasks=[], title="Test", early_exit_config=config)
        assert progress._check_early_exit() is True

    def test_ignores_hidden_files(self, tmp_path: Path):
        """Hidden files (starting with .) are ignored."""
        submit_dir = tmp_path / "submit"
        submit_dir.mkdir()
        patches_dir = submit_dir / "patches"
        patches_dir.mkdir()
        (patches_dir / ".gitkeep").write_text("")

        config = EarlyExitConfig(
            watch_dirs=[submit_dir],
            artifact_subdir="patches",
        )
        progress = MultiTaskProgress(tasks=[], title="Test", early_exit_config=config)
        assert progress._check_early_exit() is False

    def test_checks_multiple_watch_dirs(self, tmp_path: Path):
        """Returns True if any watch directory has artifacts."""
        submit1 = tmp_path / "submit1"
        submit2 = tmp_path / "submit2"
        submit1.mkdir()
        submit2.mkdir()
        (submit1 / "povs").mkdir()  # empty
        povs2 = submit2 / "povs"
        povs2.mkdir()
        (povs2 / "crash.pov").write_text("data")

        config = EarlyExitConfig(
            watch_dirs=[submit1, submit2],
            artifact_subdir="povs",
        )
        progress = MultiTaskProgress(tasks=[], title="Test", early_exit_config=config)
        assert progress._check_early_exit() is True

    def test_patches_subdir_for_bug_fixing(self, tmp_path: Path):
        """Uses patches subdirectory when configured for bug-fixing."""
        submit_dir = tmp_path / "submit"
        submit_dir.mkdir()
        patches_dir = submit_dir / "patches"
        patches_dir.mkdir()
        (patches_dir / "fix_001.patch").write_text("diff content")

        config = EarlyExitConfig(
            watch_dirs=[submit_dir],
            artifact_subdir="patches",
        )
        progress = MultiTaskProgress(tasks=[], title="Test", early_exit_config=config)
        assert progress._check_early_exit() is True


class TestEarlyExitMonitor:
    """Tests for the early exit monitoring thread."""

    def test_monitor_sets_event_when_artifact_appears(self, tmp_path: Path):
        """Monitor thread sets event when artifact appears."""
        submit_dir = tmp_path / "submit"
        submit_dir.mkdir()
        povs_dir = submit_dir / "povs"
        povs_dir.mkdir()

        config = EarlyExitConfig(
            watch_dirs=[submit_dir],
            artifact_subdir="povs",
            poll_interval=0.1,  # Fast polling for test
        )
        progress = MultiTaskProgress(tasks=[], title="Test", early_exit_config=config)

        # Start monitor
        thread = progress._start_early_exit_monitor()

        # Initially not triggered
        assert not progress._early_exit_triggered.is_set()

        # Create artifact file
        time.sleep(0.05)
        (povs_dir / "crash.pov").write_text("crash")

        # Wait for monitor to detect
        time.sleep(0.3)
        assert progress._early_exit_triggered.is_set()

        thread.join(timeout=1.0)

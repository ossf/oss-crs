"""Tests for direct libfuzzer execution handler."""

from pathlib import Path
from unittest.mock import patch, MagicMock
import subprocess

import pytest

from seed_ensembler.direct_handler import DirectEnvironment


class TestDirectEnvironment:
    def test_set_up_creates_artifact_dir(self, tmp_path):
        artifact_dir = tmp_path / "artifacts"
        env = DirectEnvironment(artifact_dir)
        env.set_up()
        assert artifact_dir.is_dir()

    def test_run_merge_requires_two_dirs(self, tmp_path):
        env = DirectEnvironment(tmp_path / "artifacts")
        env.set_up()
        with pytest.raises(RuntimeError, match="at least two"):
            env.run_merge(Path("/out/fuzzer"), [Path("/corpus")])

    @patch("seed_ensembler.direct_handler.subprocess.run")
    def test_run_merge_builds_correct_command(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(
            stderr=b"MERGE-OUTER: successful in 1 attempt(s)\n"
                   b"MERGE-OUTER: 2 new files with 5 new features added; 3 new coverage edges\n",
            returncode=0,
        )

        artifact_dir = tmp_path / "artifacts"
        env = DirectEnvironment(artifact_dir)
        env.set_up()

        corpus = tmp_path / "corpus"
        new_seeds = tmp_path / "new_seeds"
        corpus.mkdir()
        new_seeds.mkdir()

        result = env.run_merge(
            Path("/out/my_fuzzer"),
            [corpus, new_seeds],
            per_seed_timeout=5.0,
            overall_timeout=30.0,
        )

        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "/out/my_fuzzer"
        assert "-merge=1" in cmd
        assert f"-artifact_prefix={artifact_dir}/" in cmd
        assert "-timeout=5" in cmd
        assert str(corpus) in cmd
        assert str(new_seeds) in cmd

        assert mock_run.call_args[1]["timeout"] == 30.0
        assert mock_run.call_args[1]["stderr"] == subprocess.PIPE
        assert mock_run.call_args[1]["stdout"] == subprocess.DEVNULL

    @patch("seed_ensembler.direct_handler.subprocess.run")
    def test_run_merge_parses_result(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(
            stderr=b"MERGE-OUTER: successful in 1 attempt(s)\n"
                   b"MERGE-OUTER: 0 new files with 0 new features added; 0 new coverage edges\n",
            returncode=0,
        )

        env = DirectEnvironment(tmp_path / "artifacts")
        env.set_up()

        result = env.run_merge(
            Path("/out/fuzzer"),
            [tmp_path / "a", tmp_path / "b"],
        )

        assert len(result.failures) == 0
        assert result.was_aborted is False

    @patch("seed_ensembler.direct_handler.subprocess.run")
    def test_run_merge_handles_timeout(self, mock_run, tmp_path):
        mock_run.side_effect = subprocess.TimeoutExpired(
            cmd=["fuzzer"], timeout=10, stderr=b""
        )

        env = DirectEnvironment(tmp_path / "artifacts")
        env.set_up()

        result = env.run_merge(
            Path("/out/fuzzer"),
            [tmp_path / "a", tmp_path / "b"],
            overall_timeout=10.0,
        )

        assert result.was_aborted is True

    @patch("seed_ensembler.direct_handler.subprocess.run")
    def test_run_merge_detects_crash(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(
            stderr=(
                b"==12345==ERROR: AddressSanitizer: heap-buffer-overflow\n"
                b"SUMMARY: AddressSanitizer: heap-buffer-overflow /src/foo.c:42\n"
                b"Test unit written to /artifacts/crash-abc123\n"
            ),
            returncode=1,
        )

        env = DirectEnvironment(tmp_path / "artifacts")
        env.set_up()

        result = env.run_merge(
            Path("/out/fuzzer"),
            [tmp_path / "a", tmp_path / "b"],
        )

        assert len(result.failures) > 0

    @patch("seed_ensembler.direct_handler.subprocess.run")
    def test_run_single_exec_correct_command(self, mock_run, tmp_path):
        mock_run.return_value = MagicMock(
            stderr=b"", returncode=0,
        )

        artifact_dir = tmp_path / "artifacts"
        env = DirectEnvironment(artifact_dir)
        env.set_up()

        seed = tmp_path / "seed.bin"
        seed.write_bytes(b"data")

        result = env.run_single_exec(
            Path("/out/fuzzer"), seed, per_seed_timeout=10.0,
        )

        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "/out/fuzzer"
        assert f"-artifact_prefix={artifact_dir}/" in cmd
        assert "-timeout=10" in cmd
        assert str(seed) in cmd
        assert result.failure is None

    @patch("seed_ensembler.direct_handler.subprocess.run")
    def test_run_single_exec_handles_timeout(self, mock_run, tmp_path):
        mock_run.side_effect = subprocess.TimeoutExpired(
            cmd=["fuzzer"], timeout=5, stderr=b""
        )

        env = DirectEnvironment(tmp_path / "artifacts")
        env.set_up()

        seed = tmp_path / "seed.bin"
        seed.write_bytes(b"data")

        result = env.run_single_exec(
            Path("/out/fuzzer"), seed, overall_timeout=5.0,
        )

        assert result.was_aborted is True

"""Integration tests for the gen-compose CLI command."""

import sys
import pytest
import subprocess
import yaml
from pathlib import Path

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).parent.parent.parent.parent


@pytest.fixture
def cli_runner():
    """Return a function to run the oss-crs CLI."""

    def run(*args, check=False):
        cmd = [sys.executable, "-m", "oss_crs.src.cli.crs_compose"] + list(args)
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        if check and result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode, cmd, result.stdout, result.stderr
            )
        return result

    return run


@pytest.fixture
def simple_compose(tmp_dir):
    """Create a simple compose template with one CRS."""
    content = {
        "run_env": "local",
        "docker_registry": "local",
        "oss_crs_infra": {"cpuset": "0-3", "memory": "16G"},
        "crs-libfuzzer": {"cpuset": "4-7", "memory": "8G"},
    }
    path = tmp_dir / "compose.yaml"
    path.write_text(yaml.dump(content))
    return path


@pytest.fixture
def ensemble_compose(tmp_dir):
    """Create an ensemble compose template with multiple CRSes."""
    content = {
        "run_env": "local",
        "docker_registry": "local",
        "oss_crs_infra": {"cpuset": "0-3", "memory": "16G"},
        "crs-libfuzzer": {"cpuset": "4-7", "memory": "8G"},
        "crs-codex": {"cpuset": "8-11", "memory": "8G", "llm_budget": 100},
    }
    path = tmp_dir / "ensemble.yaml"
    path.write_text(yaml.dump(content))
    return path


class TestGenComposeCommand:
    """Integration tests for oss-crs gen-compose command."""

    def test_generate_simple_compose(self, cli_runner, tmp_dir, simple_compose):
        """Generate a compose file with mapped cpusets."""
        output_file = tmp_dir / "output.yaml"

        result = cli_runner(
            "gen-compose",
            "--compose-template",
            str(simple_compose),
            "--cpus",
            "20-27",
            "--compose-output",
            str(output_file),
        )

        assert result.returncode == 0
        assert output_file.exists()

        with open(output_file) as f:
            data = yaml.safe_load(f)

        assert data["oss_crs_infra"]["cpuset"] == "20-23"
        assert data["crs-libfuzzer"]["cpuset"] == "24-27"

    def test_generate_ensemble_compose(self, cli_runner, tmp_dir, ensemble_compose):
        """Generate compose file for ensemble (multiple CRSes)."""
        output_file = tmp_dir / "output.yaml"

        result = cli_runner(
            "gen-compose",
            "--compose-template",
            str(ensemble_compose),
            "--cpus",
            "100-111",
            "--compose-output",
            str(output_file),
        )

        assert result.returncode == 0

        with open(output_file) as f:
            data = yaml.safe_load(f)

        # Original: infra=0-3, crs-libfuzzer=4-7, crs-codex=8-11
        # Mapped to: 100-111
        assert data["oss_crs_infra"]["cpuset"] == "100-103"
        assert data["crs-libfuzzer"]["cpuset"] == "104-107"
        assert data["crs-codex"]["cpuset"] == "108-111"

    def test_non_contiguous_pool(self, cli_runner, tmp_dir, simple_compose):
        """Generate compose file with non-contiguous CPU pool."""
        output_file = tmp_dir / "output.yaml"

        result = cli_runner(
            "gen-compose",
            "--compose-template",
            str(simple_compose),
            "--cpus",
            "1-4,10-13",
            "--compose-output",
            str(output_file),
        )

        assert result.returncode == 0

        with open(output_file) as f:
            data = yaml.safe_load(f)

        assert data["oss_crs_infra"]["cpuset"] == "1-4"
        assert data["crs-libfuzzer"]["cpuset"] == "10-13"

    def test_preserves_non_cpuset_fields(self, cli_runner, tmp_dir, ensemble_compose):
        """Verify that non-cpuset fields are preserved."""
        output_file = tmp_dir / "output.yaml"

        result = cli_runner(
            "gen-compose",
            "--compose-template",
            str(ensemble_compose),
            "--cpus",
            "0-11",
            "--compose-output",
            str(output_file),
        )

        assert result.returncode == 0

        with open(output_file) as f:
            data = yaml.safe_load(f)

        assert data["run_env"] == "local"
        assert data["docker_registry"] == "local"
        assert data["oss_crs_infra"]["memory"] == "16G"
        assert data["crs-codex"]["llm_budget"] == 100

    def test_error_insufficient_cpus(self, cli_runner, tmp_dir, ensemble_compose):
        """Error when pool has fewer CPUs than required."""
        output_file = tmp_dir / "output.yaml"

        result = cli_runner(
            "gen-compose",
            "--compose-template",
            str(ensemble_compose),
            "--cpus",
            "0-3",  # Only 4 CPUs, but needs 12
            "--compose-output",
            str(output_file),
        )

        assert result.returncode != 0
        assert "requires at least 12" in result.stderr
        assert not output_file.exists()

    def test_error_invalid_cpus_format(self, cli_runner, tmp_dir, simple_compose):
        """Error when --cpus format is invalid."""
        output_file = tmp_dir / "output.yaml"

        result = cli_runner(
            "gen-compose",
            "--compose-template",
            str(simple_compose),
            "--cpus",
            "invalid-format",
            "--compose-output",
            str(output_file),
        )

        assert result.returncode != 0
        assert "Invalid" in result.stderr
        assert not output_file.exists()

    def test_error_template_not_found(self, cli_runner, tmp_dir):
        """Error when template file doesn't exist."""
        output_file = tmp_dir / "output.yaml"

        result = cli_runner(
            "gen-compose",
            "--compose-template",
            "/nonexistent/file.yaml",
            "--cpus",
            "0-7",
            "--compose-output",
            str(output_file),
        )

        assert result.returncode != 0
        assert "not found" in result.stderr
        assert not output_file.exists()

    def test_creates_output_directory(self, cli_runner, tmp_dir, simple_compose):
        """Verify output directory is created if it doesn't exist."""
        nested_output = tmp_dir / "nested" / "deep" / "output.yaml"

        result = cli_runner(
            "gen-compose",
            "--compose-template",
            str(simple_compose),
            "--cpus",
            "0-7",
            "--compose-output",
            str(nested_output),
        )

        assert result.returncode == 0
        assert nested_output.exists()

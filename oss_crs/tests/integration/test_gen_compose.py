# SPDX-License-Identifier: MIT
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


class TestGenComposeCommand:
    """Integration tests for oss-crs gen-compose command."""

    def test_generate_from_example(self, cli_runner, tmp_dir):
        """Generate a compose file from an example."""
        output_file = tmp_dir / "output.yaml"

        result = cli_runner(
            "gen-compose",
            "--example",
            "crs-libfuzzer",
            "--compose-output",
            str(output_file),
        )

        assert result.returncode == 0
        assert output_file.exists()

        with open(output_file) as f:
            data = yaml.safe_load(f)

        assert data["run_env"] == "local"
        assert "crs-libfuzzer" in data
        assert "cpuset" in data["oss_crs_infra"]
        assert "cpuset" in data["crs-libfuzzer"]

    def test_scale_cpus_proportional(self, cli_runner, tmp_dir):
        """Scale existing CPU allocations to fit a larger pool."""
        output_file = tmp_dir / "output.yaml"

        # crs-libfuzzer template has infra=4 (0-3), crs=4 (4-7) = 8 total
        # Pool of 16 should scale each by 2x
        result = cli_runner(
            "gen-compose",
            "--example",
            "crs-libfuzzer",
            "--cpus",
            "0-15",
            "--compose-output",
            str(output_file),
        )

        assert result.returncode == 0

        with open(output_file) as f:
            data = yaml.safe_load(f)

        from oss_crs.src.cpuset import parse_cpuset

        infra_cpus = parse_cpuset(data["oss_crs_infra"]["cpuset"])
        crs_cpus = parse_cpuset(data["crs-libfuzzer"]["cpuset"])

        # Total should be 16
        assert len(infra_cpus) + len(crs_cpus) == 16
        # No overlap
        assert infra_cpus.isdisjoint(crs_cpus)

    def test_scale_cpus_remainder_to_crs(self, cli_runner, tmp_dir):
        """Remainder CPUs go to CRS entries, not infra."""
        output_file = tmp_dir / "output.yaml"

        # crs-libfuzzer: infra=4, crs=4 (equal ratio)
        # Pool of 9: each gets floor(4/8 * 9)=4, remainder=1 goes to CRS
        result = cli_runner(
            "gen-compose",
            "--example",
            "crs-libfuzzer",
            "--cpus",
            "0-8",
            "--compose-output",
            str(output_file),
        )

        assert result.returncode == 0

        with open(output_file) as f:
            data = yaml.safe_load(f)

        from oss_crs.src.cpuset import parse_cpuset

        infra_cpus = parse_cpuset(data["oss_crs_infra"]["cpuset"])
        crs_cpus = parse_cpuset(data["crs-libfuzzer"]["cpuset"])

        assert len(infra_cpus) == 4
        assert len(crs_cpus) == 5  # Gets the remainder

    def test_scale_memory(self, cli_runner, tmp_dir):
        """Scale memory allocations proportionally."""
        output_file = tmp_dir / "output.yaml"

        # crs-libfuzzer: infra=16G, crs=16G (equal ratio)
        # Total 64G: each should get 32G
        result = cli_runner(
            "gen-compose",
            "--example",
            "crs-libfuzzer",
            "--memory",
            "64G",
            "--compose-output",
            str(output_file),
        )

        assert result.returncode == 0

        with open(output_file) as f:
            data = yaml.safe_load(f)

        assert data["oss_crs_infra"]["memory"] == "32G"
        assert data["crs-libfuzzer"]["memory"] == "32G"

    def test_litellm_external_override(self, cli_runner, tmp_dir):
        """Override litellm to external mode."""
        output_file = tmp_dir / "output.yaml"

        result = cli_runner(
            "gen-compose",
            "--example",
            "crs-claude-code",
            "--litellm-external",
            "AIXCC_LITELLM_HOSTNAME",
            "LITELLM_KEY",
            "--compose-output",
            str(output_file),
        )

        assert result.returncode == 0

        with open(output_file) as f:
            data = yaml.safe_load(f)

        assert data["llm_config"]["litellm"]["mode"] == "external"
        assert (
            data["llm_config"]["litellm"]["external"]["url_env"]
            == "AIXCC_LITELLM_HOSTNAME"
        )
        assert data["llm_config"]["litellm"]["external"]["key_env"] == "LITELLM_KEY"

    def test_all_overrides_together(self, cli_runner, tmp_dir):
        """CPU, memory, and litellm overrides applied together."""
        output_file = tmp_dir / "output.yaml"

        result = cli_runner(
            "gen-compose",
            "--example",
            "crs-libfuzzer",
            "--cpus",
            "100-115",
            "--memory",
            "128G",
            "--litellm-external",
            "MY_URL",
            "MY_KEY",
            "--compose-output",
            str(output_file),
        )

        assert result.returncode == 0

        with open(output_file) as f:
            data = yaml.safe_load(f)

        from oss_crs.src.cpuset import parse_cpuset

        all_cpus = parse_cpuset(data["oss_crs_infra"]["cpuset"]) | parse_cpuset(
            data["crs-libfuzzer"]["cpuset"]
        )
        assert len(all_cpus) == 16
        assert all_cpus.issubset(set(range(100, 116)))
        assert data["llm_config"]["litellm"]["mode"] == "external"
        assert data["llm_config"]["litellm"]["external"]["url_env"] == "MY_URL"

    def test_preserves_non_resource_fields(self, cli_runner, tmp_dir):
        """Non-resource fields are preserved through overrides."""
        output_file = tmp_dir / "output.yaml"

        result = cli_runner(
            "gen-compose",
            "--example",
            "crs-libfuzzer",
            "--cpus",
            "0-15",
            "--compose-output",
            str(output_file),
        )

        assert result.returncode == 0

        with open(output_file) as f:
            data = yaml.safe_load(f)

        assert data["run_env"] == "local"
        assert data["docker_registry"] == "local"

    def test_error_invalid_example(self, cli_runner, tmp_dir):
        """Error when example doesn't exist."""
        output_file = tmp_dir / "output.yaml"

        result = cli_runner(
            "gen-compose",
            "--example",
            "nonexistent-example",
            "--compose-output",
            str(output_file),
        )

        assert result.returncode != 0
        assert "not found" in result.stderr
        assert not output_file.exists()

    def test_error_invalid_cpus_format(self, cli_runner, tmp_dir):
        """Error when --cpus format is invalid."""
        output_file = tmp_dir / "output.yaml"

        result = cli_runner(
            "gen-compose",
            "--example",
            "crs-libfuzzer",
            "--cpus",
            "invalid-format",
            "--compose-output",
            str(output_file),
        )

        assert result.returncode != 0
        assert "Invalid" in result.stderr
        assert not output_file.exists()

    def test_creates_output_directory(self, cli_runner, tmp_dir):
        """Verify output directory is created if it doesn't exist."""
        nested_output = tmp_dir / "nested" / "deep" / "output.yaml"

        result = cli_runner(
            "gen-compose",
            "--example",
            "crs-libfuzzer",
            "--compose-output",
            str(nested_output),
        )

        assert result.returncode == 0
        assert nested_output.exists()

    def test_ensemble_scale_cpus(self, cli_runner, tmp_dir):
        """Scale ensemble with multiple CRSes."""
        output_file = tmp_dir / "output.yaml"

        # ensemble: infra=4 (0-3), crs-libfuzzer=4 (8-11),
        # atlantis-multilang-wo-concolic=4 (4-7) = 12 total
        # Pool of 24: each should get 8
        result = cli_runner(
            "gen-compose",
            "--example",
            "ensemble",
            "--cpus",
            "0-23",
            "--compose-output",
            str(output_file),
        )

        assert result.returncode == 0

        with open(output_file) as f:
            data = yaml.safe_load(f)

        from oss_crs.src.cpuset import parse_cpuset

        infra_cpus = parse_cpuset(data["oss_crs_infra"]["cpuset"])
        total_cpus = len(infra_cpus)
        for key in data:
            if key not in {"run_env", "docker_registry", "oss_crs_infra", "llm_config"}:
                total_cpus += len(parse_cpuset(data[key]["cpuset"]))

        assert total_cpus == 24

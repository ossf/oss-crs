"""Shared pytest fixtures for oss_crs tests."""

import pytest
from pathlib import Path
import tempfile
import shutil


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line("markers", "integration: mark test as integration test")
    config.addinivalue_line("markers", "docker: mark test as requiring Docker")
    config.addinivalue_line("markers", "smoke: mark test as a smoke test")
    config.addinivalue_line(
        "markers",
        "cgroup_parent_required: mark tests that require cgroup-parent support",
    )


@pytest.fixture
def tmp_dir():
    """Create a temporary directory that is cleaned up after the test."""
    d = tempfile.mkdtemp()
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def example_compose_dir():
    """Return the path to the example compose files directory."""
    return Path(__file__).parent.parent.parent / "example"


@pytest.fixture
def sample_compose_content():
    """Return sample CRS compose YAML content for testing."""
    return """
run_env: local
docker_registry: local

oss_crs_infra:
  cpuset: "0-3"
  memory: "16G"

crs-libfuzzer:
  cpuset: "4-7"
  memory: "16G"
"""


@pytest.fixture
def sample_ensemble_compose_content():
    """Return sample ensemble compose YAML with multiple CRSes."""
    return """
run_env: local
docker_registry: local

oss_crs_infra:
  cpuset: "0-3"
  memory: "16G"

crs-libfuzzer:
  cpuset: "8-11"
  memory: "16G"

atlantis-multilang-wo-concolic:
  cpuset: "4-7"
  memory: "16G"
  llm_budget: 100
"""

"""Unit tests for template rendering."""

import pytest
from jinja2 import Environment, FileSystemLoader
from pathlib import Path


TEMPLATES_DIR = Path(__file__).parent.parent.parent / "src" / "templates"


@pytest.fixture
def jinja_env():
    """Create Jinja2 environment for template testing."""
    return Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))


class TestBuildTargetTemplate:
    """Tests for build-target.docker-compose.yaml.j2 template."""

    def test_resource_limits_included_when_specified(self, jinja_env):
        """cpuset and mem_limit should be included when resource values are set."""
        template = jinja_env.get_template("build-target.docker-compose.yaml.j2")

        context = {
            "crs": {
                "path": "/crs",
                "builder_dockerfile": "Dockerfile",
                "version": "1.0",
            },
            "effective_env": {},
            "target": {
                "engine": "libfuzzer",
                "sanitizer": "address",
                "architecture": "x86_64",
                "name": "test",
                "language": "c",
                "image": "base:latest",
            },
            "build_out_dir": "/out",
            "build_id": "test-build",
            "crs_compose_env": {"type": "local"},
            "libCRS_path": "/libcrs",
            "resource": {
                "cpuset": "0-3",
                "memory": "8G",
            },
        }

        rendered = template.render(context)

        assert 'cpuset: "0-3"' in rendered
        assert 'mem_limit: "8G"' in rendered

    def test_resource_limits_omitted_when_none(self, jinja_env):
        """cpuset and mem_limit should NOT appear when resource values are None."""
        template = jinja_env.get_template("build-target.docker-compose.yaml.j2")

        context = {
            "crs": {
                "path": "/crs",
                "builder_dockerfile": "Dockerfile",
                "version": "1.0",
            },
            "effective_env": {},
            "target": {
                "engine": "libfuzzer",
                "sanitizer": "address",
                "architecture": "x86_64",
                "name": "test",
                "language": "c",
                "image": "base:latest",
            },
            "build_out_dir": "/out",
            "build_id": "test-build",
            "crs_compose_env": {"type": "local"},
            "libCRS_path": "/libcrs",
            "resource": {
                "cpuset": None,
                "memory": None,
            },
        }

        rendered = template.render(context)

        assert "cpuset:" not in rendered
        assert "mem_limit:" not in rendered

    def test_partial_resource_limits(self, jinja_env):
        """Only specified resource limits should appear."""
        template = jinja_env.get_template("build-target.docker-compose.yaml.j2")

        context = {
            "crs": {
                "path": "/crs",
                "builder_dockerfile": "Dockerfile",
                "version": "1.0",
            },
            "effective_env": {},
            "target": {
                "engine": "libfuzzer",
                "sanitizer": "address",
                "architecture": "x86_64",
                "name": "test",
                "language": "c",
                "image": "base:latest",
            },
            "build_out_dir": "/out",
            "build_id": "test-build",
            "crs_compose_env": {"type": "local"},
            "libCRS_path": "/libcrs",
            "resource": {
                "cpuset": "0",
                "memory": None,
            },
        }

        rendered = template.render(context)

        assert 'cpuset: "0"' in rendered
        assert "mem_limit:" not in rendered

"""Unit tests for mount infrastructure template rendering.

Tests volume mount definitions in build-target and run-crs-compose templates.
"""

import pytest
from jinja2 import Environment, FileSystemLoader
from pathlib import Path


TEMPLATES_DIR = Path(__file__).parent.parent.parent / "src" / "templates"


@pytest.fixture
def jinja_env():
    """Create Jinja2 environment for template testing."""
    return Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))


def _build_target_context(fuzz_proj_path="/proj/test", target_source_path=None, build_fetch_dir=None):
    """Build a minimal context dict for build-target template rendering."""
    return {
        "crs": {
            "name": "test-crs",
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
        "build_fetch_dir": build_fetch_dir,
        "fuzz_proj_path": fuzz_proj_path,
        "target_source_path": target_source_path,
    }


class TestMountInfrastructure:
    """Tests for volume mount definitions in build-target and run-crs-compose templates."""

    def test_fuzz_proj_mount_in_build_target(self, jinja_env):
        """fuzz_proj_path should render as a read-only volume mount in build-target template."""
        template = jinja_env.get_template("build-target.docker-compose.yaml.j2")
        context = _build_target_context(fuzz_proj_path="/home/user/myproject")

        rendered = template.render(context)

        assert "/home/user/myproject:/OSS_CRS_FUZZ_PROJ:ro" in rendered

    def test_fuzz_proj_mount_in_run_crs(self, jinja_env):
        """fuzz_proj_path should render as a read-only volume mount in run-crs-compose template."""
        template = jinja_env.get_template("run-crs-compose.docker-compose.yaml.j2")
        context = {
            "libCRS_path": "/libcrs",
            "crs_compose_name": "test-compose",
            "crs_list": [],
            "crs_compose_env": {"type": "local"},
            "target_env": {},
            "target": _MockTarget(proj_path="/home/user/myproject", has_repo=False),
            "work_dir": _MockWorkDir(),
            "run_id": "run-123",
            "build_id": "build-123",
            "sanitizer": "address",
            "oss_crs_infra_root_path": "/oss-crs-infra",
            "snapshot_image_tag": "",
            "resolve_dockerfile": lambda crs_path, dockerfile: str(crs_path) + "/" + dockerfile,
            "fetch_dir": "",
            "exchange_dir": "",
            "bug_finding_ensemble": False,
            "bug_fix_ensemble": False,
            "cgroup_parents": None,
            "module_envs": {},
            "fuzz_proj_path": "/home/user/myproject",
            "target_source_path": None,
        }

        rendered = template.render(context)

        assert "/home/user/myproject:/OSS_CRS_FUZZ_PROJ:ro" in rendered

    def test_target_source_mount_when_provided(self, jinja_env):
        """target_source_path should render as a read-only volume mount when provided."""
        template = jinja_env.get_template("build-target.docker-compose.yaml.j2")
        context = _build_target_context(
            fuzz_proj_path="/proj/test",
            target_source_path="/home/user/source",
        )

        rendered = template.render(context)

        assert "/home/user/source:/OSS_CRS_TARGET_SOURCE:ro" in rendered

    def test_target_source_mount_omitted_when_none(self, jinja_env):
        """target_source_path volume mount should not appear when target_source_path is None."""
        template = jinja_env.get_template("build-target.docker-compose.yaml.j2")
        context = _build_target_context(
            fuzz_proj_path="/proj/test",
            target_source_path=None,
        )

        rendered = template.render(context)

        assert "/OSS_CRS_TARGET_SOURCE" not in rendered

    def test_fuzz_proj_mount_uses_ro_flag(self, jinja_env):
        """fuzz_proj_path mount should use the :ro read-only flag."""
        template = jinja_env.get_template("build-target.docker-compose.yaml.j2")
        context = _build_target_context(fuzz_proj_path="/proj/test")

        rendered = template.render(context)

        assert "/OSS_CRS_FUZZ_PROJ:ro" in rendered

    def test_target_source_mount_uses_ro_flag(self, jinja_env):
        """target_source_path mount should use the :ro read-only flag."""
        template = jinja_env.get_template("build-target.docker-compose.yaml.j2")
        context = _build_target_context(
            fuzz_proj_path="/proj/test",
            target_source_path="/home/user/source",
        )

        rendered = template.render(context)

        assert "/OSS_CRS_TARGET_SOURCE:ro" in rendered


class _MockTarget:
    """Minimal mock target for template rendering tests."""

    def __init__(self, proj_path: str = "/proj/test", has_repo: bool = False):
        self._proj_path = proj_path
        self._has_repo = has_repo

    def get_docker_image_name(self) -> str:
        return "test:latest"

    @property
    def snapshot_image_tag(self):
        return None


class _MockWorkDir:
    """Minimal mock work_dir for template rendering tests."""

    def get_build_output_dir(self, crs_name, target, build_id, sanitizer) -> str:
        return f"/work/build-out/{crs_name}"

    def get_submit_dir(self, crs_name, target, run_id, sanitizer) -> str:
        return f"/work/submit/{crs_name}"

    def get_shared_dir(self, crs_name, target, run_id, sanitizer) -> str:
        return f"/work/shared/{crs_name}"

    def get_log_dir(self, crs_name, target, run_id, sanitizer) -> str:
        return f"/work/log/{crs_name}"

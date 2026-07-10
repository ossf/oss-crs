# SPDX-License-Identifier: MIT
"""Unit tests for mount infrastructure template rendering.

Tests volume mount definitions in build-target and run-crs-compose templates.
"""

import pytest
from jinja2 import Environment, FileSystemLoader
from pathlib import Path

from oss_crs.src.constants import OSS_CRS_INFRA_SIDECAR_IMAGES


TEMPLATES_DIR = Path(__file__).parent.parent.parent / "src" / "templates"


@pytest.fixture
def jinja_env():
    """Create Jinja2 environment for template testing."""
    return Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))


def _build_target_context(
    fuzz_proj_path="/proj/test",
    target_source_path="/default/source",
    build_fetch_dir=None,
):
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
        from types import SimpleNamespace

        template = jinja_env.get_template("run-crs-compose.docker-compose.yaml.j2")

        module_config = SimpleNamespace(
            dockerfile="patcher.Dockerfile",
            target_dependent=False,
            run_snapshot=False,
            additional_env={},
        )
        crs = SimpleNamespace(
            name="test-crs",
            crs_path=Path("/crs"),
            resource=SimpleNamespace(cpuset="0-3", memory="8G", additional_env={}),
            config=SimpleNamespace(
                version="1.0",
                type=["bug-fixing"],
                is_bug_fixing=True,
                is_bug_fixing_ensemble=False,
                is_triage=False,
                is_seed_filter=False,
                crs_run_phase=SimpleNamespace(modules={"patcher": module_config}),
            ),
        )
        mock_work_dir = _MockWorkDir()
        context = {
            "libCRS_path": "/libcrs",
            "crs_compose_name": "test-compose",
            "crs_list": [crs],
            "crs_compose_env": {"type": "local"},
            "target_env": {},
            "target": _MockTarget(proj_path="/home/user/myproject", has_repo=False),
            "work_dir": mock_work_dir,
            "run_id": "run-123",
            "build_id": "build-123",
            "sanitizer": "address",
            "oss_crs_infra_root_path": "/oss-crs-infra",
            "infra_sidecar_images": OSS_CRS_INFRA_SIDECAR_IMAGES,
            "snapshot_image_tag": "",
            "resolve_dockerfile": lambda crs_path, dockerfile: (
                str(crs_path) + "/" + str(dockerfile)
            ),
            "run_module_image": lambda crs_name, module_name, mc: (
                f"oss-crs-runner:{crs_name}-{module_name}"
            ),
            "fetch_dir": "",
            "exchange_dir": "",
            "fetch_dir_mounts": {},
            "processed_exchange_dir": None,
            "bug_finding_ensemble": False,
            "bug_fix_ensemble": False,
            "cgroup_parents": None,
            "module_envs": {"test-crs_patcher": {}},
            "fuzz_proj_path": "/home/user/myproject",
            "target_source_path": "/extracted/source",
        }

        rendered = template.render(context)

        assert "/home/user/myproject:/OSS_CRS_FUZZ_PROJ:ro" in rendered

    def test_target_source_unconditional_in_run_crs(self, jinja_env):
        """target_source_path should always render as a read-only volume mount in run-crs-compose template."""
        from types import SimpleNamespace

        template = jinja_env.get_template("run-crs-compose.docker-compose.yaml.j2")

        module_config = SimpleNamespace(
            dockerfile="patcher.Dockerfile",
            target_dependent=False,
            run_snapshot=False,
            additional_env={},
        )
        crs = SimpleNamespace(
            name="test-crs",
            crs_path=Path("/crs"),
            resource=SimpleNamespace(cpuset="0-3", memory="8G", additional_env={}),
            config=SimpleNamespace(
                version="1.0",
                type=["bug-fixing"],
                is_bug_fixing=True,
                is_bug_fixing_ensemble=False,
                is_triage=False,
                is_seed_filter=False,
                crs_run_phase=SimpleNamespace(modules={"patcher": module_config}),
            ),
        )
        mock_work_dir = _MockWorkDir()
        context = {
            "libCRS_path": "/libcrs",
            "crs_compose_name": "test-compose",
            "crs_list": [crs],
            "crs_compose_env": {"type": "local"},
            "target_env": {},
            "target": _MockTarget(proj_path="/home/user/myproject", has_repo=False),
            "work_dir": mock_work_dir,
            "run_id": "run-123",
            "build_id": "build-123",
            "sanitizer": "address",
            "oss_crs_infra_root_path": "/oss-crs-infra",
            "infra_sidecar_images": OSS_CRS_INFRA_SIDECAR_IMAGES,
            "snapshot_image_tag": "",
            "resolve_dockerfile": lambda crs_path, dockerfile: (
                str(crs_path) + "/" + str(dockerfile)
            ),
            "run_module_image": lambda crs_name, module_name, mc: (
                f"oss-crs-runner:{crs_name}-{module_name}"
            ),
            "fetch_dir": "",
            "exchange_dir": "",
            "fetch_dir_mounts": {},
            "processed_exchange_dir": None,
            "bug_finding_ensemble": False,
            "bug_fix_ensemble": False,
            "cgroup_parents": None,
            "module_envs": {"test-crs_patcher": {}},
            "fuzz_proj_path": "/home/user/myproject",
            "target_source_path": "/extracted/source",
        }

        rendered = template.render(context)

        assert "/extracted/source:/OSS_CRS_TARGET_SOURCE:ro" in rendered

    def test_litellm_key_gen_uses_stable_prepare_time_tag(self, jinja_env):
        """In internal-LLM mode the key-gen sidecar uses the stable infra tag.

        Folding litellm-key-gen into the prepare-time scheme means it is tagged
        run-independently (``oss-crs-litellm-key-gen:latest``) instead of the
        old per-run ``{{ crs_compose_name }}-oss-crs-litellm-key-gen:latest``.
        """
        from types import SimpleNamespace

        from oss_crs.src.constants import OSS_CRS_INTERNAL_LLM_SIDECAR_IMAGES

        template = jinja_env.get_template("run-crs-compose.docker-compose.yaml.j2")

        module_config = SimpleNamespace(
            dockerfile="patcher.Dockerfile",
            target_dependent=False,
            run_snapshot=False,
            additional_env={},
        )
        crs = SimpleNamespace(
            name="test-crs",
            crs_path=Path("/crs"),
            resource=SimpleNamespace(cpuset="0-3", memory="8G", additional_env={}),
            config=SimpleNamespace(
                version="1.0",
                type=["bug-fixing"],
                is_bug_fixing=True,
                is_bug_fixing_ensemble=False,
                is_triage=False,
                is_seed_filter=False,
                crs_run_phase=SimpleNamespace(modules={"patcher": module_config}),
            ),
        )
        llm_context = SimpleNamespace(
            mode="internal",
            litellm_env_secret_files={},
            litellm_config_path="/cfg/litellm.yaml",
            key_gen_request_path="/req/key_gen_request.yaml",
            secret_files={},
            master_key_file="/secrets/master_key",
            postgres_password_file="/secrets/pg_password",
        )
        context = {
            "libCRS_path": "/libcrs",
            "crs_compose_name": "crs_compose_run123",
            "crs_list": [crs],
            "crs_compose_env": {"type": "local"},
            "target_env": {},
            "target": _MockTarget(proj_path="/home/user/myproject", has_repo=False),
            "work_dir": _MockWorkDir(),
            "run_id": "run-123",
            "build_id": "build-123",
            "sanitizer": "address",
            "oss_crs_infra_root_path": "/oss-crs-infra",
            "infra_sidecar_images": {
                **OSS_CRS_INFRA_SIDECAR_IMAGES,
                **OSS_CRS_INTERNAL_LLM_SIDECAR_IMAGES,
            },
            "snapshot_image_tag": "",
            "resolve_dockerfile": lambda crs_path, dockerfile: (
                str(crs_path) + "/" + str(dockerfile)
            ),
            "run_module_image": lambda crs_name, module_name, mc: (
                f"oss-crs-runner:{crs_name}-{module_name}"
            ),
            "fetch_dir": "",
            "exchange_dir": "",
            "fetch_dir_mounts": {},
            "processed_exchange_dir": None,
            "bug_finding_ensemble": False,
            "bug_fix_ensemble": False,
            "cgroup_parents": None,
            "module_envs": {"test-crs_patcher": {}},
            "fuzz_proj_path": "/home/user/myproject",
            "target_source_path": "/extracted/source",
            "llm_context": llm_context,
            "litellm_image": "litellm@sha256:abc",
            "postgres_image": "postgres@sha256:def",
            "postgres_user": "crs",
            "postgres_port": 5432,
            "postgres_host": "postgres.oss-crs-infra-only",
            "litellm_internal_url": "http://litellm.oss-crs:4000",
            "litellm_spend_report_path": "/spend/litellm-spend-report.json",
            "sidecar_env": {},
        }
        rendered = template.render(context)

        assert "image: oss-crs-litellm-key-gen:latest" in rendered

    def test_target_source_mount_always_present(self, jinja_env):
        """target_source_path volume mount should always appear unconditionally."""
        template = jinja_env.get_template("build-target.docker-compose.yaml.j2")
        context = _build_target_context(
            fuzz_proj_path="/proj/test",
            target_source_path="/some/source/path",
        )

        rendered = template.render(context)

        assert "/some/source/path:/OSS_CRS_TARGET_SOURCE:ro" in rendered

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

    def get_target_source_dir(self, target, build_id, sanitizer, create=True) -> str:
        name = target.name if hasattr(target, "name") else "test"
        return f"/work/target-source/{name}"

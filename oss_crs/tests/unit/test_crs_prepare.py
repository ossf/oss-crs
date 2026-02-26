import textwrap

from oss_crs.src.config.crs_compose import CRSEntry, CRSSource
from oss_crs.src.crs import CRS
from oss_crs.src.ui import TaskResult
from oss_crs.src.workdir import WorkDir


class _CaptureProgress:
    def __init__(self):
        self.calls = []

    def run_command_with_streaming_output(self, cmd, cwd=None, env=None, info_text=None):
        self.calls.append(
            {
                "cmd": cmd,
                "cwd": cwd,
                "env": env,
                "info_text": info_text,
            }
        )
        return TaskResult(success=True)


def _write_minimal_crs_yaml(crs_root, version: str = "1.2.3"):
    crs_yaml = textwrap.dedent(
        f"""\
        name: test-crs
        type: [bug-fixing]
        version: {version}
        prepare_phase:
          hcl: docker-bake.hcl
        crs_run_phase:
          patcher:
            dockerfile: oss-crs/patcher.Dockerfile
        supported_target:
          mode: [full]
          language: [c]
          sanitizer: [address]
          architecture: [x86_64]
        """
    )
    (crs_root / "oss-crs").mkdir(parents=True)
    (crs_root / "oss-crs" / "crs.yaml").write_text(crs_yaml)


def _make_crs(tmp_path, additional_env: dict[str, str]) -> CRS:
    crs_root = tmp_path / "crs"
    _write_minimal_crs_yaml(crs_root)

    resource = CRSEntry(
        cpuset="0-1",
        memory="2G",
        source=CRSSource(local_path=str(crs_root)),
        additional_env=additional_env,
    )
    return CRS(
        name="test-crs",
        crs_path=crs_root,
        work_dir=WorkDir(tmp_path / "work"),
        resource=resource,
        crs_compose_env=None,
    )


def test_prepare_forwards_resource_additional_env(tmp_path):
    crs = _make_crs(tmp_path, {"CODEX_CLI_VERSION": "0.105.0"})
    progress = _CaptureProgress()

    result = crs.prepare(multi_task_progress=progress)

    assert result.success is True
    assert len(progress.calls) == 1
    env = progress.calls[0]["env"]
    assert env["CODEX_CLI_VERSION"] == "0.105.0"
    assert env["VERSION"] == "1.2.3"


def test_prepare_keeps_crs_version_over_additional_env(tmp_path):
    crs = _make_crs(tmp_path, {"VERSION": "override-me"})
    progress = _CaptureProgress()

    result = crs.prepare(multi_task_progress=progress)

    assert result.success is True
    assert len(progress.calls) == 1
    env = progress.calls[0]["env"]
    assert env["VERSION"] == "1.2.3"

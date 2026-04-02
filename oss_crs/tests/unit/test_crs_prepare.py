import json
import textwrap
from unittest.mock import patch, MagicMock

from oss_crs.src.config.crs_compose import CRSEntry, CRSSource
from oss_crs.src.crs import CRS
from oss_crs.src.ui import TaskResult
from oss_crs.src.workdir import WorkDir


class _CaptureProgress:
    def __init__(self):
        self.calls = []
        self.notes = []

    def run_command_with_streaming_output(
        self, cmd, cwd=None, env=None, info_text=None
    ):
        self.calls.append(
            {
                "cmd": cmd,
                "cwd": cwd,
                "env": env,
                "info_text": info_text,
            }
        )
        return TaskResult(success=True)

    def add_note(self, text):
        self.notes.append(text)


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


def _fake_bake_plan(*targets):
    """Build a fake bake --print JSON response."""
    plan = {"target": {}}
    for name, registry_tag in targets:
        plan["target"][name] = {
            "tags": [registry_tag, f"{name}:latest"],
        }
    return json.dumps(plan)


def _mock_subprocess_for_pull(bake_plan, pull_succeeds=True):
    """Create a mock for subprocess.run that handles bake --print, pull, and tag."""
    calls_log = []

    def mock_run(cmd, **kwargs):
        calls_log.append(cmd)
        result = MagicMock()
        if "--print" in cmd:
            result.returncode = 0
            result.stdout = bake_plan
            return result
        if cmd[0:2] == ["docker", "pull"]:
            result.returncode = 0 if pull_succeeds else 1
            result.stderr = "" if pull_succeeds else "not found"
            return result
        if cmd[0:2] == ["docker", "tag"]:
            result.returncode = 0
            return result
        result.returncode = 1
        return result

    return mock_run, calls_log


# ---------------------------------------------------------------------------
# Original tests (env forwarding)
# ---------------------------------------------------------------------------


def test_prepare_forwards_resource_additional_env(tmp_path):
    crs = _make_crs(tmp_path, {"CODEX_CLI_VERSION": "0.105.0"})
    progress = _CaptureProgress()

    # Mock subprocess so _try_pull_prebuilt_images fails (bake --print fails),
    # falling through to the bake command captured by _CaptureProgress.
    with patch("oss_crs.src.crs.subprocess.run", return_value=MagicMock(returncode=1)):
        result = crs.prepare(multi_task_progress=progress)

    assert result.success is True
    assert len(progress.calls) == 1
    env = progress.calls[0]["env"]
    assert env["CODEX_CLI_VERSION"] == "0.105.0"
    assert env["VERSION"] == "1.2.3"


def test_prepare_keeps_crs_version_over_additional_env(tmp_path):
    crs = _make_crs(tmp_path, {"VERSION": "override-me"})
    progress = _CaptureProgress()

    with patch("oss_crs.src.crs.subprocess.run", return_value=MagicMock(returncode=1)):
        result = crs.prepare(multi_task_progress=progress)

    assert result.success is True
    assert len(progress.calls) == 1
    env = progress.calls[0]["env"]
    assert env["VERSION"] == "1.2.3"


# ---------------------------------------------------------------------------
# cache-from override removed
# ---------------------------------------------------------------------------


def test_prepare_does_not_override_hcl_cache_from(tmp_path):
    """Framework must not inject --set=*.cache-from; the HCL owns cache config."""
    crs = _make_crs(tmp_path, {})
    progress = _CaptureProgress()

    with patch("oss_crs.src.crs.subprocess.run", return_value=MagicMock(returncode=1)):
        result = crs.prepare(
            multi_task_progress=progress, docker_registry="ghcr.io/example"
        )

    assert result.success is True
    cmd = progress.calls[0]["cmd"]
    cmd_str = " ".join(cmd)
    assert "cache-from" not in cmd_str
    assert "cache-to" not in cmd_str


# ---------------------------------------------------------------------------
# Pull-first strategy
# ---------------------------------------------------------------------------


def test_prepare_pulls_prebuilt_images_when_available(tmp_path):
    """When prebuilt images exist in registry, pull them instead of building."""
    crs = _make_crs(tmp_path, {})
    progress = _CaptureProgress()

    bake_plan = _fake_bake_plan(
        ("my-clang", "ghcr.io/example/my-clang:1.0"),
        ("my-builder", "ghcr.io/example/my-builder:1.0"),
    )

    mock_run, calls_log = _mock_subprocess_for_pull(bake_plan, pull_succeeds=True)

    with patch("oss_crs.src.crs.subprocess.run", side_effect=mock_run):
        result = crs.prepare(multi_task_progress=progress)

    assert result.success is True
    # Should NOT have fallen through to bake
    assert len(progress.calls) == 0
    # Should have noted the pull
    assert any("Pulled 2 prebuilt images" in n for n in progress.notes)

    # Verify docker pull was called for each image
    pull_cmds = [c for c in calls_log if c[0:2] == ["docker", "pull"]]
    assert len(pull_cmds) == 2
    assert pull_cmds[0] == ["docker", "pull", "ghcr.io/example/my-clang:1.0"]
    assert pull_cmds[1] == ["docker", "pull", "ghcr.io/example/my-builder:1.0"]

    # Verify docker tag was called for local tags
    tag_cmds = [c for c in calls_log if c[0:2] == ["docker", "tag"]]
    assert len(tag_cmds) == 2
    assert [
        "docker",
        "tag",
        "ghcr.io/example/my-clang:1.0",
        "my-clang:latest",
    ] in tag_cmds
    assert [
        "docker",
        "tag",
        "ghcr.io/example/my-builder:1.0",
        "my-builder:latest",
    ] in tag_cmds


def test_prepare_falls_back_to_bake_when_pull_fails(tmp_path):
    """When pull fails, fall back to building via bake."""
    crs = _make_crs(tmp_path, {})
    progress = _CaptureProgress()

    bake_plan = _fake_bake_plan(
        ("my-clang", "ghcr.io/example/my-clang:1.0"),
    )

    mock_run, _ = _mock_subprocess_for_pull(bake_plan, pull_succeeds=False)

    with patch("oss_crs.src.crs.subprocess.run", side_effect=mock_run):
        result = crs.prepare(multi_task_progress=progress)

    assert result.success is True
    # Should have fallen through to bake
    assert len(progress.calls) == 1
    assert "bake" in progress.calls[0]["cmd"]


def test_prepare_skips_pull_when_publishing(tmp_path):
    """When publishing, always use bake (never pull)."""
    crs = _make_crs(tmp_path, {})
    progress = _CaptureProgress()

    with patch("oss_crs.src.crs.subprocess.run") as mock_run:
        result = crs.prepare(
            multi_task_progress=progress,
            publish=True,
            docker_registry="ghcr.io/example",
        )

    assert result.success is True
    # Should NOT have called subprocess.run (no pull attempt)
    mock_run.assert_not_called()
    # Should have used bake with --push
    assert len(progress.calls) == 1
    assert "--push" in progress.calls[0]["cmd"]


def test_prepare_falls_back_when_no_registry_tags(tmp_path):
    """When targets have only local tags (no domain), skip pull and bake."""
    crs = _make_crs(tmp_path, {})
    progress = _CaptureProgress()

    # Plan with only local tags (no domain with dot)
    plan = json.dumps(
        {"target": {"my-image": {"tags": ["my-image:latest", "my-image:v1"]}}}
    )

    def mock_run(cmd, **kwargs):
        result = MagicMock()
        if "--print" in cmd:
            result.returncode = 0
            result.stdout = plan
            return result
        result.returncode = 1
        return result

    with patch("oss_crs.src.crs.subprocess.run", side_effect=mock_run):
        result = crs.prepare(multi_task_progress=progress)

    assert result.success is True
    # Should have fallen through to bake
    assert len(progress.calls) == 1


def test_prepare_falls_back_when_bake_print_fails(tmp_path):
    """When bake --print fails, skip pull and bake."""
    crs = _make_crs(tmp_path, {})
    progress = _CaptureProgress()

    with patch("oss_crs.src.crs.subprocess.run", return_value=MagicMock(returncode=1)):
        result = crs.prepare(multi_task_progress=progress)

    assert result.success is True
    # Should have fallen through to bake
    assert len(progress.calls) == 1


def test_prepare_no_pull_skips_pull_and_builds(tmp_path):
    """--no-pull skips pulling and goes straight to bake."""
    crs = _make_crs(tmp_path, {})
    progress = _CaptureProgress()

    with patch("oss_crs.src.crs.subprocess.run") as mock_run:
        result = crs.prepare(multi_task_progress=progress, no_pull=True)

    assert result.success is True
    # Should NOT have called subprocess.run (no pull attempt)
    mock_run.assert_not_called()
    # Should have gone straight to bake
    assert len(progress.calls) == 1
    assert "bake" in progress.calls[0]["cmd"]

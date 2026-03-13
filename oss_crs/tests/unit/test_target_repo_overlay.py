"""Unit tests for repo override behavior in target Dockerfile generation."""

from pathlib import Path

from oss_crs.src.target import Target
from oss_crs.src.ui import TaskResult


class _CaptureProgress:
    def __init__(self) -> None:
        self.generated_dockerfile = ""
        self.cmd: list[str] = []
        self.cwd: Path | None = None

    def run_command_with_streaming_output(self, cmd, cwd=None, info_text=None):
        self.cmd = list(cmd)
        self.cwd = Path(cwd) if cwd is not None else None
        dockerfile_path = Path(self.cmd[self.cmd.index("-f") + 1])
        self.generated_dockerfile = dockerfile_path.read_text()
        return TaskResult(success=True)


def test_repo_override_uses_rsync_delete_overlay(tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "Dockerfile").write_text(
        "FROM base\n"
        "WORKDIR $SRC\n"
        "RUN tar -zxf $SRC/FreeRDP.tar.gz\n"
        "WORKDIR FreeRDP\n"
    )

    repo = tmp_path / "repo"
    repo.mkdir()

    target = Target.__new__(Target)
    target.proj_path = proj
    target.work_dir = tmp_path / "work"
    target.work_dir.mkdir()
    target.repo_path = repo

    progress = _CaptureProgress()
    result = target._Target__build_docker_image_with_repo("demo:tag", progress)

    assert result.success is True
    assert "--from=repo_path . /OSS_CRS_REPO_OVERRIDE" in progress.generated_dockerfile
    assert "RUN rsync -a --delete /OSS_CRS_REPO_OVERRIDE/ ./" in progress.generated_dockerfile
    assert "RUN rm -rf /OSS_CRS_REPO_OVERRIDE" in progress.generated_dockerfile
    assert "RUN find . -mindepth 1 -maxdepth 1 -exec rm -rf {} +" not in progress.generated_dockerfile
    assert progress.generated_dockerfile.index(
        "--from=repo_path . /OSS_CRS_REPO_OVERRIDE"
    ) < progress.generated_dockerfile.index("RUN rsync -a --delete /OSS_CRS_REPO_OVERRIDE/ ./")

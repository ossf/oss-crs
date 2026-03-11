"""Unit tests for Target cache-key hashing."""

from pathlib import Path
import subprocess

from oss_crs.src.target import Target


def _write_project_files(proj: Path, build_text: str = "echo build\n") -> None:
    proj.mkdir()
    (proj / "Dockerfile").write_text("FROM base\n")
    (proj / "build.sh").write_text(build_text)
    (proj / "test.sh").write_text("echo test\n")


def _init_repo_with_commit(repo: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def test_repo_hash_changes_when_project_scripts_change(tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    _write_project_files(proj, "echo build v1\n")

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README").write_text("repo\n")
    _init_repo_with_commit(repo)

    target_v1 = Target(tmp_path / "work1", proj, repo)
    hash_v1 = target_v1.get_docker_image_name()

    (proj / "build.sh").write_text("echo build v2\n")

    target_v2 = Target(tmp_path / "work2", proj, repo)
    hash_v2 = target_v2.get_docker_image_name()

    assert hash_v1 != hash_v2


def test_repo_hash_changes_when_repo_is_dirty(tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    _write_project_files(proj)

    repo = tmp_path / "repo"
    repo.mkdir()
    tracked = repo / "tracked.txt"
    tracked.write_text("v1\n")
    _init_repo_with_commit(repo)

    clean_target = Target(tmp_path / "work-clean", proj, repo)
    clean_hash = clean_target.get_docker_image_name()

    tracked.write_text("v2\n")

    dirty_target = Target(tmp_path / "work-dirty", proj, repo)
    dirty_hash = dirty_target.get_docker_image_name()

    assert clean_hash != dirty_hash

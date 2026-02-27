from pathlib import Path

from oss_crs.src.target import Target


def test_target_env_uses_fixed_defaults_without_project_yaml(tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    proj.mkdir(parents=True, exist_ok=True)
    target = Target(tmp_path / "work", proj, None)
    env = target.get_target_env()
    assert env["sanitizer"] == "address"
    assert env["engine"] == "libfuzzer"
    assert env["architecture"] == "x86_64"


def test_default_sanitizer_is_address() -> None:
    assert Target.DEFAULT_SANITIZER == "address"


def test_user_provided_missing_repo_path_fails_init(tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    proj.mkdir(parents=True, exist_ok=True)
    missing_repo = tmp_path / "missing-repo"
    target = Target(tmp_path / "work", proj, missing_repo)
    assert target.init_repo() is False

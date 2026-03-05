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


def test_target_env_uses_project_yaml_when_available(tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "project.yaml").write_text(
        "\n".join(
            [
                "language: jvm",
                "sanitizers: [memory, undefined]",
                "architectures: [i386]",
                "fuzzing_engines: [afl, libfuzzer]",
            ]
        )
        + "\n"
    )
    target = Target(tmp_path / "work", proj, None)
    env = target.get_target_env()
    assert env["language"] == "jvm"
    # When "address" is NOT in the list, use first entry
    assert env["sanitizer"] == "memory"
    assert env["architecture"] == "i386"
    assert env["engine"] == "afl"


def test_target_env_prefers_address_sanitizer_when_listed(tmp_path: Path) -> None:
    """When 'address' is in the sanitizers list, prefer it over first entry."""
    proj = tmp_path / "proj"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "project.yaml").write_text(
        "\n".join(
            [
                "language: c",
                "sanitizers: [memory, address, undefined]",
            ]
        )
        + "\n"
    )
    target = Target(tmp_path / "work", proj, None)
    env = target.get_target_env()
    # "address" is preferred even though "memory" is first
    assert env["sanitizer"] == "address"


def test_target_env_falls_back_when_project_yaml_invalid(tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "project.yaml").write_text("language: not-a-real-language\n")
    target = Target(tmp_path / "work", proj, None)
    env = target.get_target_env()
    assert env["language"] == "c"
    assert env["sanitizer"] == "address"
    assert env["architecture"] == "x86_64"
    assert env["engine"] == "libfuzzer"


def test_user_provided_missing_repo_path_fails_init(tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    proj.mkdir(parents=True, exist_ok=True)
    missing_repo = tmp_path / "missing-repo"
    target = Target(tmp_path / "work", proj, missing_repo)
    assert target.init_repo() is False

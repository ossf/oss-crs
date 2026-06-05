# SPDX-License-Identifier: MIT
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
    # When "libfuzzer" IS in the list, prefer it over first entry
    assert env["engine"] == "libfuzzer"


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


def test_target_env_prefers_libfuzzer_engine_when_listed(tmp_path: Path) -> None:
    """When 'libfuzzer' is in the fuzzing_engines list, prefer it over first entry."""
    proj = tmp_path / "proj"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "project.yaml").write_text(
        "\n".join(
            [
                "language: c",
                "fuzzing_engines: [afl, honggfuzz, libfuzzer]",
            ]
        )
        + "\n"
    )
    target = Target(tmp_path / "work", proj, None)
    env = target.get_target_env()
    # "libfuzzer" is preferred even though "afl" is first
    assert env["engine"] == "libfuzzer"


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


def test_base_runner_image_defaults_to_latest_without_base_os_version(
    tmp_path: Path,
) -> None:
    proj = tmp_path / "proj"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "project.yaml").write_text("language: c\n")
    target = Target(tmp_path / "work", proj, None)
    assert target.base_os_version == "legacy"
    assert target.base_runner_image == "gcr.io/oss-fuzz-base/base-runner:latest"


def test_base_runner_image_matches_pinned_base_os_version(tmp_path: Path) -> None:
    """A pinned base_os_version selects an OS-matched base-runner tag (issue #101)."""
    proj = tmp_path / "proj"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "project.yaml").write_text(
        "language: c\nbase_os_version: ubuntu-24-04\n"
    )
    target = Target(tmp_path / "work", proj, None)
    assert target.base_os_version == "ubuntu-24-04"
    assert (
        target.base_runner_image
        == "gcr.io/oss-fuzz-base/base-runner:ubuntu-24-04"
    )


def test_unknown_base_os_version_warns_but_passes_through(
    tmp_path: Path, capsys
) -> None:
    """An unrecognized base_os_version warns yet still maps to a runner tag."""
    proj = tmp_path / "proj"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "project.yaml").write_text(
        "language: c\nbase_os_version: ubuntu-22-04\n"
    )
    target = Target(tmp_path / "work", proj, None)
    assert target.base_os_version == "ubuntu-22-04"
    assert (
        target.base_runner_image
        == "gcr.io/oss-fuzz-base/base-runner:ubuntu-22-04"
    )
    captured = capsys.readouterr()
    assert "Unknown base_os_version 'ubuntu-22-04'" in (captured.out + captured.err)


def test_known_base_os_version_does_not_warn(tmp_path: Path, capsys) -> None:
    proj = tmp_path / "proj"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "project.yaml").write_text(
        "language: c\nbase_os_version: ubuntu-24-04\n"
    )
    Target(tmp_path / "work", proj, None)
    captured = capsys.readouterr()
    assert "Unknown base_os_version" not in (captured.out + captured.err)


def test_user_provided_missing_repo_path_fails_init(tmp_path: Path) -> None:
    proj = tmp_path / "proj"
    proj.mkdir(parents=True, exist_ok=True)
    missing_repo = tmp_path / "missing-repo"
    target = Target(tmp_path / "work", proj, missing_repo)
    assert target.init_repo() is False

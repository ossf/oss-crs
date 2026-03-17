"""Unit tests for effective WORKDIR resolution in target paths."""

from pathlib import Path

from oss_crs.src.target import Target


def _make_target_with_dockerfile(tmp_path: Path, dockerfile: str) -> Target:
    target = Target.__new__(Target)
    proj = tmp_path / "proj"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "Dockerfile").write_text(dockerfile)
    target.proj_path = proj
    return target


def test_resolve_effective_workdir_with_src_chain(tmp_path: Path) -> None:
    target = _make_target_with_dockerfile(
        tmp_path,
        "FROM base\nWORKDIR $SRC\nWORKDIR libxml2\n",
    )
    assert target._resolve_effective_workdir() == "/src/libxml2"


def test_resolve_effective_workdir_with_src_default_when_env_not_provided(
    tmp_path: Path,
) -> None:
    target = _make_target_with_dockerfile(
        tmp_path,
        "FROM base\nWORKDIR $SRC\n",
    )
    assert target._resolve_effective_workdir() == "/src"


def test_resolve_effective_workdir_with_absolute_path(tmp_path: Path) -> None:
    target = _make_target_with_dockerfile(
        tmp_path,
        "FROM base\nWORKDIR /custom/src\n",
    )
    assert target._resolve_effective_workdir() == "/custom/src"


def test_resolve_effective_workdir_with_relative_without_prior(tmp_path: Path) -> None:
    target = _make_target_with_dockerfile(
        tmp_path,
        "FROM base\nWORKDIR repo\n",
    )
    assert target._resolve_effective_workdir() == "/src/repo"


def test_resolve_effective_workdir_without_workdir_defaults_to_src(
    tmp_path: Path,
) -> None:
    target = _make_target_with_dockerfile(
        tmp_path,
        "FROM base\nRUN echo ok\n",
    )
    assert target._resolve_effective_workdir() == "/src"


def test_resolve_effective_workdir_strips_inline_comment(tmp_path: Path) -> None:
    target = _make_target_with_dockerfile(
        tmp_path,
        'FROM base\nWORKDIR $SRC/curl # WORKDIR is "curl"\n',
    )
    assert target._resolve_effective_workdir() == "/src/curl"


def test_resolve_effective_workdir_expands_env_vars(tmp_path: Path) -> None:
    target = _make_target_with_dockerfile(
        tmp_path,
        "FROM base\nENV OSS_FUZZ_ROOT=/src/oss-fuzz\nWORKDIR ${OSS_FUZZ_ROOT}/infra\n",
    )
    assert target._resolve_effective_workdir() == "/src/oss-fuzz/infra"

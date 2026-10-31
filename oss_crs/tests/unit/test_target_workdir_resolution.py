# SPDX-License-Identifier: MIT
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


def test_resolve_effective_workdir_multiple_workdir_absolute_override(
    tmp_path: Path,
) -> None:
    """Later absolute WORKDIR overrides earlier one."""
    target = _make_target_with_dockerfile(
        tmp_path,
        "FROM base\nWORKDIR /a\nWORKDIR /b\n",
    )
    assert target._resolve_effective_workdir() == "/b"


def test_resolve_effective_workdir_relative_appends_to_current(
    tmp_path: Path,
) -> None:
    """Relative WORKDIR appends to current directory."""
    target = _make_target_with_dockerfile(
        tmp_path,
        "FROM base\nWORKDIR /a\nWORKDIR sub\n",
    )
    assert target._resolve_effective_workdir() == "/a/sub"


def test_resolve_effective_workdir_arg_expansion(tmp_path: Path) -> None:
    """ARG with default value should be expanded in WORKDIR."""
    target = _make_target_with_dockerfile(
        tmp_path,
        "FROM base\nARG MY_DIR=/custom\nWORKDIR $MY_DIR\n",
    )
    assert target._resolve_effective_workdir() == "/custom"


def test_resolve_effective_workdir_quoted_value(tmp_path: Path) -> None:
    """Quoted WORKDIR values should have quotes stripped."""
    target = _make_target_with_dockerfile(
        tmp_path,
        'FROM base\nWORKDIR "/app"\n',
    )
    assert target._resolve_effective_workdir() == "/app"


def test_resolve_effective_workdir_single_quoted_value(tmp_path: Path) -> None:
    """Single-quoted WORKDIR values should have quotes stripped."""
    target = _make_target_with_dockerfile(
        tmp_path,
        "FROM base\nWORKDIR '/app'\n",
    )
    assert target._resolve_effective_workdir() == "/app"


def test_resolve_effective_workdir_missing_dockerfile_defaults_to_src(
    tmp_path: Path,
) -> None:
    """Missing Dockerfile should default to /src."""
    target = Target.__new__(Target)
    target.proj_path = tmp_path / "nonexistent-proj"
    assert target._resolve_effective_workdir() == "/src"


def test_resolve_effective_workdir_multistage_last_stage_wins(
    tmp_path: Path,
) -> None:
    """In multi-stage builds, WORKDIR from the last stage should be used.

    Note: the current parser does NOT reset state on FROM boundaries,
    so this test documents the current behavior (which may be wrong for
    some multi-stage patterns).
    """
    target = _make_target_with_dockerfile(
        tmp_path,
        "FROM ubuntu AS builder\nWORKDIR /build\nFROM base\nWORKDIR /src/proj\n",
    )
    # Last WORKDIR is /src/proj — parser returns it correctly
    assert target._resolve_effective_workdir() == "/src/proj"


def test_resolve_effective_workdir_multistage_no_workdir_in_final_stage(
    tmp_path: Path,
) -> None:
    """Multi-stage: no WORKDIR in final stage inherits from earlier stage (current parser behavior).

    Known limitation: the parser does not reset on FROM, so it returns
    the WORKDIR from the earlier stage rather than defaulting to /.
    This test documents the current behavior.
    """
    target = _make_target_with_dockerfile(
        tmp_path,
        "FROM ubuntu AS builder\nWORKDIR /build\nFROM base\nRUN echo ok\n",
    )
    # Current behavior: returns /build (leaked from builder stage)
    # Correct Docker behavior would be /src (default for oss-fuzz base)
    assert target._resolve_effective_workdir() == "/build"


def _make_target_with_main_repo(
    tmp_path: Path, dockerfile: str, main_repo: str | None
) -> Target:
    target = _make_target_with_dockerfile(tmp_path, dockerfile)
    target.main_repo = main_repo
    return target


def test_resolve_falls_back_to_main_repo_clone_when_workdir_is_work(
    tmp_path: Path,
) -> None:
    """alembic: clones into $SRC/alembic, then ends with WORKDIR $WORK/."""
    target = _make_target_with_main_repo(
        tmp_path,
        "FROM gcr.io/oss-fuzz-base/base-builder\n"
        "RUN git clone -b v3.2.1 --depth 1 https://github.com/AcademySoftwareFoundation/imath $SRC/imath && \\\n"
        "    cd $SRC/imath && \\\n"
        "    make install\n"
        "RUN git clone --depth 1 https://github.com/alembic/alembic\n"
        "COPY build.sh $SRC/\n"
        "WORKDIR $WORK/\n",
        "https://github.com/alembic/alembic",
    )
    assert target._resolve_effective_workdir() == "/src/alembic"


def test_resolve_uses_explicit_clone_destination(tmp_path: Path) -> None:
    """draco: clones into a relative dest, then ends with WORKDIR $WORK/."""
    target = _make_target_with_main_repo(
        tmp_path,
        "FROM base\n"
        "RUN git clone --depth 1 https://github.com/google/draco draco\n"
        "WORKDIR $WORK/\n",
        "https://github.com/google/draco",
    )
    assert target._resolve_effective_workdir() == "/src/draco"


def test_resolve_tracks_cd_and_options_inside_continued_run(tmp_path: Path) -> None:
    target = _make_target_with_main_repo(
        tmp_path,
        "FROM base\n"
        "RUN mkdir -p deps && cd deps && \\\n"
        "    # a comment line inside the continuation\n"
        "    git clone --branch v1 --filter=blob:none -c core.autocrlf=false \\\n"
        "      git@github.com:Example/Proj.git\n"
        "WORKDIR $OUT\n",
        "https://github.com/example/proj/",
    )
    assert target._resolve_effective_workdir() == "/src/deps/Proj"


def test_resolve_expands_base_image_gopath(tmp_path: Path) -> None:
    """Go projects use $GOPATH, which base-builder-go sets to /root/go."""
    target = _make_target_with_main_repo(
        tmp_path,
        "FROM gcr.io/oss-fuzz-base/base-builder-go\n"
        "RUN git clone --depth 1 https://github.com/google/mtail $GOPATH/src/github.com/google/mtail\n"
        "WORKDIR $GOPATH/src/github.com/google/mtail\n",
        "https://github.com/google/mtail",
    )
    assert target._resolve_effective_workdir() == "/root/go/src/github.com/google/mtail"


def test_resolve_keeps_source_workdir_even_if_main_repo_cloned_elsewhere(
    tmp_path: Path,
) -> None:
    target = _make_target_with_main_repo(
        tmp_path,
        "FROM base\n"
        "RUN git clone https://github.com/example/proj upstream\n"
        "WORKDIR $SRC/proj\n",
        "https://github.com/example/proj",
    )
    assert target._resolve_effective_workdir() == "/src/proj"


def test_resolve_defaults_to_src_with_one_warning_when_no_clone_found(
    tmp_path: Path, monkeypatch
) -> None:
    warnings: list[str] = []
    monkeypatch.setattr("oss_crs.src.target.log_warning", warnings.append)
    target = _make_target_with_main_repo(
        tmp_path,
        "FROM base\nRUN curl -L https://example.com/proj.tgz | tar xz\nWORKDIR $WORK\n",
        "https://github.com/example/proj",
    )

    assert target._resolve_effective_workdir() == "/src"
    assert target._resolve_effective_workdir() == "/src"
    assert len(warnings) == 1
    assert "/work" in warnings[0]


def test_resolve_defaults_to_src_for_unknown_variable(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr("oss_crs.src.target.log_warning", lambda _msg: None)
    target = _make_target_with_main_repo(
        tmp_path, "FROM base\nWORKDIR $UNDEFINED_DIR/proj\n", None
    )
    assert target._resolve_effective_workdir() == "/src"


def test_resolve_ignores_workdir_text_inside_continued_run(tmp_path: Path) -> None:
    target = _make_target_with_dockerfile(
        tmp_path,
        "FROM base\nWORKDIR $SRC/proj\nRUN echo \\\n    WORKDIR /bogus\n",
    )
    assert target._resolve_effective_workdir() == "/src/proj"

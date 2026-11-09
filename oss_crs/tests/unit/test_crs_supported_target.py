# SPDX-License-Identifier: MIT
"""Unit tests for CRS target compatibility validation (supported_target)."""

import textwrap
from dataclasses import dataclass
from pathlib import Path

from oss_crs.src.config.crs_compose import CRSEntry, CRSSource
from oss_crs.src.crs import CRS
from oss_crs.src.ui import TaskResult
from oss_crs.src.workdir import WorkDir


@dataclass
class _MockTarget:
    name: str = "mock-target"
    language: str = "c"
    sanitizer: str = "address"
    architecture: str = "x86_64"
    engine: str = "libfuzzer"


class _CaptureProgress:
    """Mock MultiTaskProgress to capture scheduled tasks without execution."""

    def __init__(self):
        self.tasks: list[tuple[str, object]] = []

    def add_task(self, name, func):
        self.tasks.append((name, func))

    def run_added_tasks(self):
        return TaskResult(success=True)


def _make_crs(
    tmp_path: Path,
    supported_target_yaml: str | None = None,
    crs_name: str = "test-crs",
) -> CRS:
    crs_root = tmp_path / crs_name
    (crs_root / "oss-crs").mkdir(parents=True, exist_ok=True)

    if supported_target_yaml is None:
        supported_target_yaml = textwrap.dedent(
            """\
            supported_target:
              mode: [full]
              language: [c]
              sanitizer: [address]
              architecture: [x86_64]
              fuzzing_engine: [libfuzzer]
            """
        )

    crs_yaml_content = (
        textwrap.dedent(
            f"""\
        name: {crs_name}
        type: [bug-finding]
        version: 1.0.0
        prepare_phase:
          hcl: docker-bake.hcl
        crs_run_phase:
          fuzzer:
            dockerfile: oss-crs/fuzzer.Dockerfile
        """
        )
        + supported_target_yaml
    )

    (crs_root / "oss-crs" / "crs.yaml").write_text(crs_yaml_content)

    resource = CRSEntry(
        cpuset="0-1",
        memory="2G",
        source=CRSSource(local_path=str(crs_root)),
    )
    return CRS(
        name=crs_name,
        crs_path=crs_root,
        work_dir=WorkDir(tmp_path / "work"),
        resource=resource,
        crs_compose_env=None,
    )


def test_is_supported_target_all_matching(tmp_path: Path) -> None:
    crs = _make_crs(tmp_path)
    target = _MockTarget(
        language="c",
        sanitizer="address",
        architecture="x86_64",
        engine="libfuzzer",
    )
    assert crs.is_supported_target(target) is True


def test_is_supported_target_language_mismatch(tmp_path: Path) -> None:
    crs = _make_crs(tmp_path)
    target = _MockTarget(language="jvm")
    assert crs.is_supported_target(target) is False


def test_is_supported_target_sanitizer_mismatch(tmp_path: Path) -> None:
    crs = _make_crs(tmp_path)
    target = _MockTarget(sanitizer="memory")
    assert crs.is_supported_target(target) is False


def test_is_supported_target_architecture_mismatch(tmp_path: Path) -> None:
    crs = _make_crs(tmp_path)
    target = _MockTarget(architecture="aarch64")
    assert crs.is_supported_target(target) is False


def test_is_supported_target_engine_mismatch(tmp_path: Path) -> None:
    crs = _make_crs(tmp_path)
    target = _MockTarget(engine="afl")
    assert crs.is_supported_target(target) is False


def test_is_supported_target_multiple_allowed_values(tmp_path: Path) -> None:
    supported_yaml = textwrap.dedent(
        """\
        supported_target:
          mode: [full, delta]
          language: [c, c++]
          sanitizer: [address, undefined]
          architecture: [x86_64, i386]
          fuzzing_engine: [libfuzzer, afl]
        """
    )
    crs = _make_crs(tmp_path, supported_target_yaml=supported_yaml)

    # All these variations should match
    assert (
        crs.is_supported_target(
            _MockTarget(
                language="c",
                sanitizer="address",
                architecture="x86_64",
                engine="libfuzzer",
            )
        )
        is True
    )
    assert (
        crs.is_supported_target(
            _MockTarget(
                language="c++", sanitizer="undefined", architecture="i386", engine="afl"
            )
        )
        is True
    )

    # Variations with unlisted values should be rejected
    assert (
        crs.is_supported_target(
            _MockTarget(
                language="rust",
                sanitizer="address",
                architecture="x86_64",
                engine="libfuzzer",
            )
        )
        is False
    )
    assert (
        crs.is_supported_target(
            _MockTarget(
                language="c",
                sanitizer="memory",
                architecture="x86_64",
                engine="libfuzzer",
            )
        )
        is False
    )
    assert (
        crs.is_supported_target(
            _MockTarget(
                language="c",
                sanitizer="address",
                architecture="aarch64",
                engine="libfuzzer",
            )
        )
        is False
    )
    assert (
        crs.is_supported_target(
            _MockTarget(
                language="c",
                sanitizer="address",
                architecture="x86_64",
                engine="honggfuzz",
            )
        )
        is False
    )


def test_is_supported_target_uses_schema_defaults(tmp_path: Path) -> None:
    """When sanitizer, architecture, or fuzzing_engine are omitted, default schema values apply."""
    minimal_supported_yaml = textwrap.dedent(
        """\
        supported_target:
          mode: [full]
          language: [c]
        """
    )
    crs = _make_crs(tmp_path, supported_target_yaml=minimal_supported_yaml)

    # Defaults include address sanitizer, x86_64 architecture, and libfuzzer engine
    assert (
        crs.is_supported_target(
            _MockTarget(
                language="c",
                sanitizer="address",
                architecture="x86_64",
                engine="libfuzzer",
            )
        )
        is True
    )

    # Defaults include ubsan (undefined) as well
    assert (
        crs.is_supported_target(
            _MockTarget(
                language="c",
                sanitizer="undefined",
                architecture="x86_64",
                engine="libfuzzer",
            )
        )
        is True
    )

    # Language is still strictly checked
    assert (
        crs.is_supported_target(
            _MockTarget(
                language="python",
                sanitizer="address",
                architecture="x86_64",
                engine="libfuzzer",
            )
        )
        is False
    )


def test_build_target_skips_unsupported_target(tmp_path: Path) -> None:
    crs = _make_crs(tmp_path, crs_name="my-crs")
    incompatible_target = _MockTarget(name="java-project", language="jvm")
    progress = _CaptureProgress()

    result = crs.build_target(
        target=incompatible_target,
        target_base_image="base:latest",
        progress=progress,
        build_id="build-1",
        sanitizer="address",
    )

    assert result.success is True
    assert (
        "Skipping target java-project for CRS my-crs as it is not supported."
        in result.output
    )
    # No build tasks should be scheduled
    assert len(progress.tasks) == 0


def test_is_target_built_skips_unsupported_target(tmp_path: Path) -> None:
    # Set up a CRS with a target_build_phase
    crs_root = tmp_path / "crs-with-build"
    (crs_root / "oss-crs").mkdir(parents=True, exist_ok=True)
    crs_yaml_content = textwrap.dedent(
        """\
        name: crs-with-build
        type: [bug-finding]
        version: 1.0.0
        prepare_phase:
          hcl: docker-bake.hcl
        target_build_phase:
          builds:
            - name: compile
              dockerfile: oss-crs/compile.Dockerfile
              outputs: [binary]
        crs_run_phase:
          fuzzer:
            dockerfile: oss-crs/fuzzer.Dockerfile
        supported_target:
          mode: [full]
          language: [c]
          sanitizer: [address]
          architecture: [x86_64]
          fuzzing_engine: [libfuzzer]
        """
    )
    (crs_root / "oss-crs" / "crs.yaml").write_text(crs_yaml_content)

    resource = CRSEntry(
        cpuset="0-1",
        memory="2G",
        source=CRSSource(local_path=str(crs_root)),
    )
    crs = CRS(
        name="crs-with-build",
        crs_path=crs_root,
        work_dir=WorkDir(tmp_path / "work"),
        resource=resource,
        crs_compose_env=None,
    )

    incompatible_target = _MockTarget(name="rust-project", language="rust")
    progress = _CaptureProgress()

    result = crs.is_target_built(
        target=incompatible_target,
        target_base_image="base:latest",
        progress=progress,
        build_id="build-1",
        sanitizer="address",
    )

    assert result.success is True
    assert (
        "Skipping target rust-project for CRS crs-with-build as it is not supported."
        in result.output
    )
    assert len(progress.tasks) == 0

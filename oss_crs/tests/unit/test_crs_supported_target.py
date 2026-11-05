# SPDX-License-Identifier: MIT
"""Unit tests for CRS.__is_supported_target.

Verifies that the compatibility check correctly allows fully-matching targets
and rejects targets that mismatch on language, sanitizer, architecture, or
fuzzing engine.
"""

import textwrap

from oss_crs.src.config.crs_compose import CRSEntry, CRSSource
from oss_crs.src.crs import CRS
from oss_crs.src.workdir import WorkDir


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_crs_yaml(crs_root, *, languages, sanitizers, architectures, engines):
    """Write a minimal crs.yaml with the given supported_target constraints."""
    lang_list = "[" + ", ".join(languages) + "]"
    san_list = "[" + ", ".join(sanitizers) + "]"
    arch_list = "[" + ", ".join(architectures) + "]"
    eng_list = "[" + ", ".join(engines) + "]"

    crs_yaml = textwrap.dedent(
        f"""\
        name: test-crs
        type: [bug-finding]
        version: 1.0.0
        crs_run_phase:
          runner:
            dockerfile: oss-crs/runner.Dockerfile
        supported_target:
          mode: [full]
          language: {lang_list}
          sanitizer: {san_list}
          architecture: {arch_list}
          fuzzing_engine: {eng_list}
        """
    )
    (crs_root / "oss-crs").mkdir(parents=True, exist_ok=True)
    (crs_root / "oss-crs" / "crs.yaml").write_text(crs_yaml)


def _make_crs(tmp_path, *, languages, sanitizers, architectures, engines) -> CRS:
    """Construct a CRS whose supported_target is set to the given constraints."""
    crs_root = tmp_path / "crs"
    _write_crs_yaml(
        crs_root,
        languages=languages,
        sanitizers=sanitizers,
        architectures=architectures,
        engines=engines,
    )
    resource = CRSEntry(
        cpuset="0-1",
        memory="2G",
        source=CRSSource(local_path=str(crs_root)),
        additional_env={},
    )
    return CRS(
        name="test-crs",
        crs_path=crs_root,
        work_dir=WorkDir(tmp_path / "work"),
        resource=resource,
        crs_compose_env=None,
    )


class _FakeTarget:
    """Minimal Target stand-in that only carries the four checked attributes."""

    def __init__(
        self,
        language: str = "c",
        sanitizer: str = "address",
        architecture: str = "x86_64",
        engine: str = "libfuzzer",
    ):
        self.name = "fake-target"
        self.language = language
        self.sanitizer = sanitizer
        self.architecture = architecture
        self.engine = engine


# ---------------------------------------------------------------------------
# Matching target — should always be supported
# ---------------------------------------------------------------------------


def test_matching_target_is_supported(tmp_path):
    """A target that matches all four dimensions must be accepted."""
    crs = _make_crs(
        tmp_path,
        languages=["c"],
        sanitizers=["address"],
        architectures=["x86_64"],
        engines=["libfuzzer"],
    )
    target = _FakeTarget(
        language="c", sanitizer="address", architecture="x86_64", engine="libfuzzer"
    )
    assert crs._CRS__is_supported_target(target) is True


def test_matching_target_multi_valued_sets(tmp_path):
    """When CRS supports multiple values per dimension, any match is accepted."""
    crs = _make_crs(
        tmp_path,
        languages=["c", "c++"],
        sanitizers=["address", "undefined"],
        architectures=["x86_64", "i386"],
        engines=["libfuzzer", "afl"],
    )
    # C++ / undefined / i386 / AFL — all within the allowed sets
    target = _FakeTarget(
        language="c++", sanitizer="undefined", architecture="i386", engine="afl"
    )
    assert crs._CRS__is_supported_target(target) is True


# ---------------------------------------------------------------------------
# Language mismatch
# ---------------------------------------------------------------------------


def test_language_mismatch_returns_false(tmp_path):
    """A JVM-only CRS must reject a C target."""
    crs = _make_crs(
        tmp_path,
        languages=["jvm"],
        sanitizers=["address"],
        architectures=["x86_64"],
        engines=["libfuzzer"],
    )
    target = _FakeTarget(language="c")
    assert crs._CRS__is_supported_target(target) is False


def test_language_mismatch_rust_crs_vs_go_target(tmp_path):
    """A Rust-only CRS must reject a Go target."""
    crs = _make_crs(
        tmp_path,
        languages=["rust"],
        sanitizers=["address", "undefined"],
        architectures=["x86_64"],
        engines=["libfuzzer"],
    )
    target = _FakeTarget(language="go")
    assert crs._CRS__is_supported_target(target) is False


# ---------------------------------------------------------------------------
# Sanitizer mismatch
# ---------------------------------------------------------------------------


def test_sanitizer_mismatch_returns_false(tmp_path):
    """A CRS limited to 'memory' sanitizer must reject an ASAN target."""
    crs = _make_crs(
        tmp_path,
        languages=["c"],
        sanitizers=["memory"],
        architectures=["x86_64"],
        engines=["libfuzzer"],
    )
    target = _FakeTarget(sanitizer="address")
    assert crs._CRS__is_supported_target(target) is False


def test_sanitizer_mismatch_thread_not_in_defaults(tmp_path):
    """A CRS that only supports address/undefined must reject a thread sanitizer."""
    crs = _make_crs(
        tmp_path,
        languages=["c"],
        sanitizers=["address", "undefined"],
        architectures=["x86_64"],
        engines=["libfuzzer"],
    )
    target = _FakeTarget(sanitizer="thread")
    assert crs._CRS__is_supported_target(target) is False


# ---------------------------------------------------------------------------
# Architecture mismatch
# ---------------------------------------------------------------------------


def test_architecture_mismatch_returns_false(tmp_path):
    """A CRS limited to x86_64 must reject an aarch64 target."""
    crs = _make_crs(
        tmp_path,
        languages=["c"],
        sanitizers=["address"],
        architectures=["x86_64"],
        engines=["libfuzzer"],
    )
    target = _FakeTarget(architecture="aarch64")
    assert crs._CRS__is_supported_target(target) is False


def test_architecture_mismatch_i386_only_vs_x86_64(tmp_path):
    """A CRS limited to i386 must reject an x86_64 target."""
    crs = _make_crs(
        tmp_path,
        languages=["c"],
        sanitizers=["address"],
        architectures=["i386"],
        engines=["libfuzzer"],
    )
    target = _FakeTarget(architecture="x86_64")
    assert crs._CRS__is_supported_target(target) is False


# ---------------------------------------------------------------------------
# Fuzzing engine mismatch
# ---------------------------------------------------------------------------


def test_engine_mismatch_returns_false(tmp_path):
    """A CRS limited to honggfuzz must reject a libfuzzer target."""
    crs = _make_crs(
        tmp_path,
        languages=["c"],
        sanitizers=["address"],
        architectures=["x86_64"],
        engines=["honggfuzz"],
    )
    target = _FakeTarget(engine="libfuzzer")
    assert crs._CRS__is_supported_target(target) is False


def test_engine_mismatch_centipede_not_supported(tmp_path):
    """A libfuzzer/AFL CRS must reject a centipede target."""
    crs = _make_crs(
        tmp_path,
        languages=["c"],
        sanitizers=["address"],
        architectures=["x86_64"],
        engines=["libfuzzer", "afl"],
    )
    target = _FakeTarget(engine="centipede")
    assert crs._CRS__is_supported_target(target) is False


# ---------------------------------------------------------------------------
# Multiple mismatches (first failing dimension short-circuits)
# ---------------------------------------------------------------------------


def test_multiple_mismatches_still_returns_false(tmp_path):
    """When more than one dimension mismatches, the check still returns False."""
    crs = _make_crs(
        tmp_path,
        languages=["c"],
        sanitizers=["address"],
        architectures=["x86_64"],
        engines=["libfuzzer"],
    )
    target = _FakeTarget(
        language="jvm",
        sanitizer="thread",
        architecture="aarch64",
        engine="honggfuzz",
    )
    assert crs._CRS__is_supported_target(target) is False


# ---------------------------------------------------------------------------
# build_target skips gracefully when target is unsupported
# ---------------------------------------------------------------------------


def test_build_target_skips_unsupported_target(tmp_path):
    """build_target must return success=True with a skip message for unsupported targets."""
    crs = _make_crs(
        tmp_path,
        languages=["jvm"],
        sanitizers=["address"],
        architectures=["x86_64"],
        engines=["libfuzzer"],
    )

    class _FakeProgress:
        def add_task(self, name, fn):
            pass

        def run_added_tasks(self):
            from oss_crs.src.ui import TaskResult

            return TaskResult(success=True)

    target = _FakeTarget(language="c")  # C target vs JVM-only CRS

    result = crs.build_target(
        target=target,
        target_base_image="proj:abc123",
        progress=_FakeProgress(),
        build_id="build-001",
        sanitizer="address",
    )

    assert result.success is True
    assert "Skipping" in (result.output or "")

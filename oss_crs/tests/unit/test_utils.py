# SPDX-License-Identifier: MIT
"""Unit tests for oss_crs.src.utils module."""

import pytest
import re
from pathlib import Path

from oss_crs.src.utils import normalize_run_id, user_temporary_dir


class TestUserTemporaryDir:
    """Tests for the reusable per-user temporary directory helper."""

    def test_uses_existing_xdg_runtime_dir(self, tmp_path, monkeypatch):
        runtime_dir = tmp_path / "runtime"
        runtime_dir.mkdir()
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime_dir))
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)

        assert user_temporary_dir() == runtime_dir / "oss-crs"

    def test_uses_existing_xdg_cache_dir_when_runtime_missing(
        self, tmp_path, monkeypatch
    ):
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
        monkeypatch.setenv("XDG_CACHE_HOME", str(cache_dir))

        assert user_temporary_dir() == cache_dir / "oss-crs"

    def test_ignores_missing_xdg_dirs(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "missing-runtime"))
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "missing-cache"))
        monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")

        assert user_temporary_dir() == tmp_path / "home" / ".cache" / "oss-crs"


class TestNormalizeRunId:
    """Tests for normalize_run_id function.

    Focuses on the important behavioral guarantees:
    - Collision prevention via hash suffix
    - Deterministic output
    - Filesystem safety
    - Unicode handling
    """

    def test_hash_prevents_collisions(self):
        """Inputs that normalize to different base strings should have different hashes."""
        # These normalize to genuinely different base strings
        result1 = normalize_run_id("test-run")
        result2 = normalize_run_id("test_run")
        result3 = normalize_run_id("alpha-beta")

        results = {result1, result2, result3}
        assert len(results) == 3, "Hash suffix should prevent collisions"

        # Inputs that normalize to the SAME base string should produce the same output
        # (case, spaces, and special chars are stripped/lowered before hashing)
        assert normalize_run_id("test-run") == normalize_run_id("TEST-RUN")
        assert normalize_run_id("test-run") == normalize_run_id("test run")

    def test_idempotent(self):
        """Calling normalize_run_id on an already-normalized ID returns the same value."""
        inputs = ["my-build-123", "test_run", "1778522723j6"]
        for input_id in inputs:
            once = normalize_run_id(input_id)
            twice = normalize_run_id(once)
            assert once == twice, (
                f"normalize_run_id is not idempotent for '{input_id}': "
                f"'{once}' != '{twice}'"
            )

    def test_deterministic(self):
        """Same input should always produce same output."""
        inputs = [
            "my-test-run-123",
            "Test Run With Spaces",
            "special@chars#here!",
            "test-日本語-run",  # Mixed unicode + ascii
        ]
        for input_id in inputs:
            result1 = normalize_run_id(input_id)
            result2 = normalize_run_id(input_id)
            assert result1 == result2, (
                f"Output should be deterministic for '{input_id}'"
            )

    def test_filesystem_safe(self):
        """Result should be safe for filesystem use across platforms."""
        dangerous_inputs = [
            "test/run",  # Unix path separator
            "test\\run",  # Windows path separator
            "test:run",  # Windows drive separator
            "test*run",  # Glob wildcard
            "test?run",  # Glob wildcard
            'test"run',  # Quote
            "test<run>",  # Angle brackets
            "test|run",  # Pipe
            "CON",  # Windows reserved name
            "test\x00run",  # Null byte
            "test\nrun",  # Newline
        ]
        # Only lowercase alphanumeric, hyphens, and underscores allowed
        safe_pattern = re.compile(r"^[a-z0-9_-]+$")

        for dangerous in dangerous_inputs:
            try:
                result = normalize_run_id(dangerous)
                assert safe_pattern.match(result), (
                    f"'{result}' from '{dangerous}' is not filesystem safe"
                )
            except ValueError:
                # Empty result after normalization is also acceptable
                pass

    def test_unicode_handling(self):
        """Unicode characters should be handled gracefully."""
        unicode_inputs = [
            "test-日本語-run",
            "tëst-rün",
            "тест",
            "🚀rocket",
        ]
        safe_pattern = re.compile(r"^[a-z0-9_-]+$")

        for unicode_input in unicode_inputs:
            try:
                result = normalize_run_id(unicode_input)
                assert safe_pattern.match(result), (
                    f"'{result}' from '{unicode_input}' is not valid"
                )
            except ValueError:
                # If all chars are unicode, result may be empty - that's ok
                pass

    def test_empty_input_raises(self):
        """Empty or non-alphanumeric-only strings should raise."""
        with pytest.raises(ValueError, match="at least one alphanumeric"):
            normalize_run_id("")

        with pytest.raises(ValueError, match="at least one alphanumeric"):
            normalize_run_id("@#$%^&*()")

    def test_path_separator_is_normalized(self):
        """Path separators should be normalized like other delimiters."""
        result = normalize_run_id("../escape")
        assert result.startswith("escape-")
        result2 = normalize_run_id(r"test\\run")
        assert result2.startswith("test-run-")

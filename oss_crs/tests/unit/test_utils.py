"""Unit tests for oss_crs.src.utils module."""

import pytest
import re
from oss_crs.src.utils import normalize_run_id


class TestNormalizeRunId:
    """Tests for normalize_run_id function.

    Focuses on the important behavioral guarantees:
    - Collision prevention via hash suffix
    - Deterministic output
    - Filesystem safety
    - Unicode handling
    """

    def test_hash_prevents_collisions(self):
        """Different inputs that normalize similarly should have different hashes."""
        # These all normalize to similar base strings but should differ
        result1 = normalize_run_id("test-run")
        result2 = normalize_run_id("test_run")
        result3 = normalize_run_id("test run")
        result4 = normalize_run_id("TEST-RUN")

        # All should be unique due to hash suffix
        results = {result1, result2, result3, result4}
        assert len(results) == 4, "Hash suffix should prevent collisions"

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
            assert result1 == result2, f"Output should be deterministic for '{input_id}'"

    def test_filesystem_safe(self):
        """Result should be safe for filesystem use across platforms."""
        dangerous_inputs = [
            "test/run",      # Unix path separator
            "test\\run",     # Windows path separator
            "test:run",      # Windows drive separator
            "test*run",      # Glob wildcard
            "test?run",      # Glob wildcard
            'test"run',      # Quote
            "test<run>",     # Angle brackets
            "test|run",      # Pipe
            "CON",           # Windows reserved name
            "test\x00run",   # Null byte
            "test\nrun",     # Newline
        ]
        # Only lowercase alphanumeric, hyphens, and underscores allowed
        safe_pattern = re.compile(r"^[a-z0-9_-]+$")

        for dangerous in dangerous_inputs:
            try:
                result = normalize_run_id(dangerous)
                assert safe_pattern.match(result), \
                    f"'{result}' from '{dangerous}' is not filesystem safe"
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
                assert safe_pattern.match(result), \
                    f"'{result}' from '{unicode_input}' is not valid"
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

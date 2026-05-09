# SPDX-License-Identifier: MIT
"""Unit tests for oss_crs.src.cpuset module.

Tests the CPU set parsing and mapping algorithm as specified in issue #68.
"""

import pytest
from oss_crs.src.cpuset import (
    parse_cpuset,
    cpuset_to_str,
    map_cpuset,
    create_cpu_mapping,
)


class TestParseCpuset:
    """Tests for parse_cpuset function."""

    def test_parses_all_formats(self):
        """Should parse single CPUs, ranges, and mixed formats."""
        assert parse_cpuset("5") == {5}
        assert parse_cpuset("0-3") == {0, 1, 2, 3}
        assert parse_cpuset("0,2,4") == {0, 2, 4}
        assert parse_cpuset("0-3,5,8-11") == {0, 1, 2, 3, 5, 8, 9, 10, 11}

    def test_rejects_invalid_input(self):
        """Should reject malformed cpuset strings."""
        invalid_inputs = ["", "abc", "0-3;5", "5-3"]  # reversed range
        for invalid in invalid_inputs:
            with pytest.raises(ValueError):
                parse_cpuset(invalid)


class TestCpusetToStr:
    """Tests for cpuset_to_str function."""

    def test_produces_compact_format(self):
        """Should produce compact range notation where possible."""
        assert cpuset_to_str({0, 1, 2, 3}) == "0-3"
        assert cpuset_to_str({1, 3, 5}) == "1,3,5"
        assert cpuset_to_str({0, 1, 2, 3, 5, 8, 9, 10, 11}) == "0-3,5,8-11"

    def test_roundtrip(self):
        """parse -> str -> parse should be idempotent."""
        test_cases = ["0-3", "1,3,5", "0-3,5,8-11"]
        for case in test_cases:
            assert parse_cpuset(cpuset_to_str(parse_cpuset(case))) == parse_cpuset(case)


class TestCpuMapping:
    """Tests for the CPU mapping algorithm (create_cpu_mapping + map_cpuset).

    These test cases come directly from the examples in issue #68.
    """

    def test_separate_cpus_example(self):
        """Example from issue: separate CPU ranges mapped to new pool.

        YAML: infra="0-3", crs-libfuzzer="4-7", multilang="8-11"
        --cpus "20-31"

        Expected:
          infra:          "0-3"  -> "20-23"
          crs-libfuzzer:  "4-7"  -> "24-27"
          multilang:      "8-11" -> "28-31"
        """
        mapping = create_cpu_mapping(["0-3", "4-7", "8-11"], "20-31")

        assert map_cpuset("0-3", mapping) == "20-23"
        assert map_cpuset("4-7", mapping) == "24-27"
        assert map_cpuset("8-11", mapping) == "28-31"

    def test_shared_cpus_example(self):
        """Example from issue: deliberate CPU sharing is preserved.

        YAML: infra="0-3", crs-libfuzzer="0-3", multilang="4-7"
        --cpus "20-27"

        Expected:
          infra:          "0-3" -> "20-23"
          crs-libfuzzer:  "0-3" -> "20-23"  (shared with infra!)
          multilang:      "4-7" -> "24-27"
        """
        mapping = create_cpu_mapping(["0-3", "0-3", "4-7"], "20-27")

        assert map_cpuset("0-3", mapping) == "20-23"
        assert map_cpuset("4-7", mapping) == "24-27"

    def test_non_contiguous_pool(self):
        """Non-contiguous CPU pools should work."""
        mapping = create_cpu_mapping(["0-3", "4-7"], "1-4,10-13")

        assert map_cpuset("0-3", mapping) == "1-4"
        assert map_cpuset("4-7", mapping) == "10-13"

    def test_insufficient_cpus_error(self):
        """Should error with clear message when pool is too small."""
        with pytest.raises(ValueError) as exc_info:
            create_cpu_mapping(["0-11"], "20-23")

        error = str(exc_info.value)
        assert "4 CPUs" in error  # pool size
        assert "12" in error  # required count

    def test_excess_cpus_unused(self):
        """Extra CPUs in pool should be silently unused."""
        mapping = create_cpu_mapping(["0-3"], "0-100")
        assert len(mapping) == 4  # Only maps what's needed

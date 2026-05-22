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
    scale_cpusets,
    default_cpu_allocation,
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


class TestScaleCpusets:
    """Tests for proportional CPU scaling."""

    def test_even_scaling(self):
        """Equal allocations scaled evenly."""
        allocations = {"oss_crs_infra": 4, "crs-libfuzzer": 4}
        result = scale_cpusets(allocations, "0-15")

        assert len(parse_cpuset(result["oss_crs_infra"])) == 8
        assert len(parse_cpuset(result["crs-libfuzzer"])) == 8
        # No overlap
        infra = parse_cpuset(result["oss_crs_infra"])
        crs = parse_cpuset(result["crs-libfuzzer"])
        assert infra.isdisjoint(crs)

    def test_proportional_scaling(self):
        """Unequal allocations maintain ratios."""
        # 2:6 ratio = 1:3 -> pool of 16: 4 and 12
        allocations = {"oss_crs_infra": 2, "crs-libfuzzer": 6}
        result = scale_cpusets(allocations, "0-15")

        infra_count = len(parse_cpuset(result["oss_crs_infra"]))
        crs_count = len(parse_cpuset(result["crs-libfuzzer"]))
        assert infra_count + crs_count == 16
        assert infra_count == 4
        assert crs_count == 12

    def test_remainder_goes_to_crs(self):
        """Remainder CPUs distributed to CRS, not infra."""
        # Equal ratio, pool of 9: floor(4.5)=4 each, 1 remainder -> CRS
        allocations = {"oss_crs_infra": 4, "crs-libfuzzer": 4}
        result = scale_cpusets(allocations, "0-8")

        assert len(parse_cpuset(result["oss_crs_infra"])) == 4
        assert len(parse_cpuset(result["crs-libfuzzer"])) == 5

    def test_remainder_largest_crs_first(self):
        """Remainder goes to largest CRS entries first."""
        allocations = {
            "oss_crs_infra": 4,
            "crs-libfuzzer": 8,
            "crs-codex": 4,
        }
        # Total 16, pool 17: each scaled floor, 1 remainder -> crs-libfuzzer (largest)
        result = scale_cpusets(allocations, "0-16")

        infra = len(parse_cpuset(result["oss_crs_infra"]))
        libfuzzer = len(parse_cpuset(result["crs-libfuzzer"]))
        codex = len(parse_cpuset(result["crs-codex"]))
        assert infra + libfuzzer + codex == 17
        assert libfuzzer > codex  # libfuzzer gets the remainder

    def test_minimum_1_cpu_per_entry(self):
        """Every entry gets at least 1 CPU."""
        allocations = {"oss_crs_infra": 1, "crs-a": 1, "crs-b": 1}
        result = scale_cpusets(allocations, "0-2")

        for name in allocations:
            assert len(parse_cpuset(result[name])) >= 1

    def test_pool_too_small(self):
        """Error when pool has fewer CPUs than entries."""
        allocations = {"oss_crs_infra": 4, "crs-a": 4, "crs-b": 4}
        with pytest.raises(ValueError, match="2 CPUs"):
            scale_cpusets(allocations, "0-1")

    def test_non_contiguous_pool(self):
        """Works with non-contiguous CPU pools."""
        allocations = {"oss_crs_infra": 4, "crs-libfuzzer": 4}
        result = scale_cpusets(allocations, "1-4,10-13")

        infra = parse_cpuset(result["oss_crs_infra"])
        crs = parse_cpuset(result["crs-libfuzzer"])
        assert len(infra) == 4
        assert len(crs) == 4
        assert infra.isdisjoint(crs)
        assert infra | crs == {1, 2, 3, 4, 10, 11, 12, 13}

    def test_scale_down(self):
        """Scale to a smaller pool than original."""
        allocations = {"oss_crs_infra": 8, "crs-libfuzzer": 8}
        result = scale_cpusets(allocations, "0-7")

        assert len(parse_cpuset(result["oss_crs_infra"])) == 4
        assert len(parse_cpuset(result["crs-libfuzzer"])) == 4


class TestDefaultCpuAllocation:
    """Tests for default_cpu_allocation."""

    def test_basic_allocation(self):
        """Infra gets ~25%, rest to CRS."""
        result = default_cpu_allocation(["crs-libfuzzer"], "0-15")

        infra_count = len(parse_cpuset(result["oss_crs_infra"]))
        crs_count = len(parse_cpuset(result["crs-libfuzzer"]))
        assert infra_count == 4  # min(4, 16//4=4)
        assert crs_count == 12

    def test_small_pool(self):
        """Small pool: infra gets 1, rest to CRS."""
        result = default_cpu_allocation(["crs-libfuzzer"], "0-3")

        infra_count = len(parse_cpuset(result["oss_crs_infra"]))
        crs_count = len(parse_cpuset(result["crs-libfuzzer"]))
        assert infra_count == 1  # min(4, max(1, 4//4=1)) = 1
        assert crs_count == 3

    def test_multiple_crs_even_split(self):
        """Multiple CRSes get even split."""
        result = default_cpu_allocation(["crs-a", "crs-b"], "0-11")

        infra = len(parse_cpuset(result["oss_crs_infra"]))
        crs_a = len(parse_cpuset(result["crs-a"]))
        crs_b = len(parse_cpuset(result["crs-b"]))
        assert infra == 3  # min(4, 12//4=3)
        assert crs_a + crs_b == 9
        # Should be roughly equal
        assert abs(crs_a - crs_b) <= 1

    def test_pool_too_small(self):
        """Error when pool can't fit all entries."""
        with pytest.raises(ValueError):
            default_cpu_allocation(["crs-a", "crs-b"], "0")

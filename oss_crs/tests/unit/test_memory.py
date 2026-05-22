"""Unit tests for oss_crs.src.memory module."""

import pytest
from oss_crs.src.memory import (
    parse_memory,
    memory_to_str,
    scale_memory,
    default_memory_allocation,
)


class TestParseMemory:
    def test_parses_common_formats(self):
        assert parse_memory("8G") == 8 * 1024**3
        assert parse_memory("16GB") == 16 * 1024**3
        assert parse_memory("1024M") == 1024 * 1024**2
        assert parse_memory("2048MB") == 2048 * 1024**2
        assert parse_memory("1T") == 1024**4
        assert parse_memory("512K") == 512 * 1024

    def test_case_insensitive(self):
        assert parse_memory("8g") == 8 * 1024**3
        assert parse_memory("8gb") == 8 * 1024**3

    def test_fractional(self):
        assert parse_memory("1.5G") == int(1.5 * 1024**3)

    def test_rejects_invalid(self):
        with pytest.raises(ValueError):
            parse_memory("")
        with pytest.raises(ValueError):
            parse_memory("8")
        with pytest.raises(ValueError):
            parse_memory("abc")
        with pytest.raises(ValueError):
            parse_memory("8X")


class TestMemoryToStr:
    def test_uses_largest_clean_unit(self):
        assert memory_to_str(8 * 1024**3) == "8G"
        assert memory_to_str(1024**4) == "1T"
        assert memory_to_str(512 * 1024**2) == "512M"
        assert memory_to_str(256 * 1024) == "256K"

    def test_falls_back_to_smaller_unit(self):
        # 1.5G -> expressed as 1536M
        assert memory_to_str(int(1.5 * 1024**3)) == "1536M"

    def test_rejects_zero(self):
        with pytest.raises(ValueError):
            memory_to_str(0)


class TestScaleMemory:
    def test_even_scaling(self):
        allocations = {"oss_crs_infra": "8G", "crs-libfuzzer": "8G"}
        result = scale_memory(allocations, "32G")
        assert result["oss_crs_infra"] == "16G"
        assert result["crs-libfuzzer"] == "16G"

    def test_proportional_scaling(self):
        allocations = {"oss_crs_infra": "16G", "crs-libfuzzer": "16G"}
        result = scale_memory(allocations, "64G")
        assert result["oss_crs_infra"] == "32G"
        assert result["crs-libfuzzer"] == "32G"

    def test_unequal_ratios(self):
        # 1:3 ratio, total 64G -> 16G and 48G
        allocations = {"oss_crs_infra": "8G", "crs-libfuzzer": "24G"}
        result = scale_memory(allocations, "64G")
        assert result["oss_crs_infra"] == "16G"
        assert result["crs-libfuzzer"] == "48G"

    def test_remainder_to_crs(self):
        # Equal ratio, total 33G -> can't split evenly in MB
        allocations = {"oss_crs_infra": "8G", "crs-libfuzzer": "8G"}
        result = scale_memory(allocations, "33G")
        infra = parse_memory(result["oss_crs_infra"])
        crs = parse_memory(result["crs-libfuzzer"])
        # CRS should get more than or equal to infra
        assert crs >= infra
        # Total should be close to 33G
        assert infra + crs <= parse_memory("33G")


class TestDefaultMemoryAllocation:
    def test_basic(self):
        result = default_memory_allocation(["crs-libfuzzer"], "64G")
        infra = parse_memory(result["oss_crs_infra"])
        crs = parse_memory(result["crs-libfuzzer"])
        assert infra == 16 * 1024**3  # min(16G, 25% of 64G)
        assert crs == 48 * 1024**3

    def test_small_total(self):
        result = default_memory_allocation(["crs-libfuzzer"], "8G")
        infra = parse_memory(result["oss_crs_infra"])
        crs = parse_memory(result["crs-libfuzzer"])
        assert infra == 2 * 1024**3  # 25% of 8G
        assert crs == 6 * 1024**3

    def test_multiple_crs(self):
        result = default_memory_allocation(["crs-a", "crs-b"], "64G")
        infra = parse_memory(result["oss_crs_infra"])
        crs_a = parse_memory(result["crs-a"])
        crs_b = parse_memory(result["crs-b"])
        assert infra == 16 * 1024**3
        assert crs_a + crs_b == 48 * 1024**3

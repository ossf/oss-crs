"""Unit tests for cgroup module."""

from oss_crs.src.cgroup import (
    parse_cpuset,
    format_cpuset,
    parse_memory_to_bytes,
)


class TestParseCpuset:
    """Tests for parse_cpuset function."""

    def test_single_cpu(self):
        assert parse_cpuset("0") == {0}
        assert parse_cpuset("5") == {5}

    def test_cpu_range(self):
        assert parse_cpuset("0-3") == {0, 1, 2, 3}
        assert parse_cpuset("4-7") == {4, 5, 6, 7}

    def test_comma_separated(self):
        assert parse_cpuset("0,2,4") == {0, 2, 4}

    def test_mixed_format(self):
        assert parse_cpuset("0-2,5,8-9") == {0, 1, 2, 5, 8, 9}

    def test_whitespace_handling(self):
        assert parse_cpuset(" 0-2 ") == {0, 1, 2}


class TestFormatCpuset:
    """Tests for format_cpuset function."""

    def test_single_cpu(self):
        assert format_cpuset({0}) == "0"
        assert format_cpuset({5}) == "5"

    def test_consecutive_range(self):
        assert format_cpuset({0, 1, 2, 3}) == "0-3"

    def test_non_consecutive(self):
        assert format_cpuset({0, 2, 4}) == "0,2,4"

    def test_mixed(self):
        result = format_cpuset({0, 1, 2, 5, 8, 9})
        assert result == "0-2,5,8-9"

    def test_roundtrip(self):
        """parse and format should be inverse operations."""
        original = "0-3,8,12-15"
        cpus = parse_cpuset(original)
        formatted = format_cpuset(cpus)
        assert formatted == original


class TestParseMemoryToBytes:
    """Tests for parse_memory_to_bytes function."""

    def test_gigabytes(self):
        assert parse_memory_to_bytes("8G") == 8 * 1024 * 1024 * 1024
        assert parse_memory_to_bytes("1G") == 1024 * 1024 * 1024

    def test_megabytes(self):
        assert parse_memory_to_bytes("512M") == 512 * 1024 * 1024
        assert parse_memory_to_bytes("256M") == 256 * 1024 * 1024

    def test_kilobytes(self):
        assert parse_memory_to_bytes("1024K") == 1024 * 1024

    def test_case_insensitive(self):
        assert parse_memory_to_bytes("8g") == parse_memory_to_bytes("8G")
        assert parse_memory_to_bytes("512m") == parse_memory_to_bytes("512M")

"""Unit tests for verify_cgroup_limits in oss_crs.src.cgroup."""

from pathlib import Path

from oss_crs.src.cgroup import verify_cgroup_limits


class TestVerifyCgroupLimits:
    def test_missing_cgroup_dir(self, tmp_path: Path):
        non_existent = tmp_path / "does_not_exist"
        valid, errors = verify_cgroup_limits(
            non_existent, expected_cpuset="0-1", expected_memory_bytes=1024
        )
        assert not valid
        assert any("does not exist" in err for err in errors)

    def test_no_limits_specified(self, tmp_path: Path):
        valid, errors = verify_cgroup_limits(tmp_path)
        assert valid
        assert errors == []

    def test_memory_limit_matching(self, tmp_path: Path):
        mem_file = tmp_path / "memory.max"
        mem_file.write_text("1048576\n")

        valid, errors = verify_cgroup_limits(tmp_path, expected_memory_bytes=1048576)
        assert valid
        assert errors == []

    def test_memory_limit_missing_file(self, tmp_path: Path):
        valid, errors = verify_cgroup_limits(tmp_path, expected_memory_bytes=1048576)
        assert not valid
        assert any("Memory limit file missing" in err for err in errors)

    def test_memory_limit_mismatch(self, tmp_path: Path):
        mem_file = tmp_path / "memory.max"
        mem_file.write_text("1048576\n")

        valid, errors = verify_cgroup_limits(tmp_path, expected_memory_bytes=2097152)
        assert not valid
        assert any("Memory limit mismatch" in err for err in errors)

    def test_memory_limit_max_ignored(self, tmp_path: Path):
        mem_file = tmp_path / "memory.max"
        mem_file.write_text("max\n")

        # "max" means unlimited in cgroup v2
        valid, errors = verify_cgroup_limits(tmp_path, expected_memory_bytes=2097152)
        assert valid
        assert errors == []

    def test_memory_limit_corrupt_content(self, tmp_path: Path):
        mem_file = tmp_path / "memory.max"
        mem_file.write_text("invalid_number\n")

        valid, errors = verify_cgroup_limits(tmp_path, expected_memory_bytes=2097152)
        assert not valid
        assert any("Failed to read memory.max" in err for err in errors)

    def test_cpuset_matching_canonical(self, tmp_path: Path):
        cpuset_file = tmp_path / "cpuset.cpus"
        cpuset_file.write_text("0-3\n")

        valid, errors = verify_cgroup_limits(tmp_path, expected_cpuset="0,1,2,3")
        assert valid
        assert errors == []

    def test_cpuset_missing_file(self, tmp_path: Path):
        valid, errors = verify_cgroup_limits(tmp_path, expected_cpuset="0-3")
        assert not valid
        assert any("Cpuset file missing" in err for err in errors)

    def test_cpuset_mismatch(self, tmp_path: Path):
        cpuset_file = tmp_path / "cpuset.cpus"
        cpuset_file.write_text("0-1\n")

        valid, errors = verify_cgroup_limits(tmp_path, expected_cpuset="0-3")
        assert not valid
        assert any("Cpuset mismatch" in err for err in errors)

    def test_both_valid(self, tmp_path: Path):
        (tmp_path / "memory.max").write_text("4294967296\n")
        (tmp_path / "cpuset.cpus").write_text("0-1\n")

        valid, errors = verify_cgroup_limits(
            tmp_path, expected_cpuset="0-1", expected_memory_bytes=4294967296
        )
        assert valid
        assert errors == []

    def test_both_mismatched(self, tmp_path: Path):
        (tmp_path / "memory.max").write_text("1000\n")
        (tmp_path / "cpuset.cpus").write_text("0\n")

        valid, errors = verify_cgroup_limits(
            tmp_path, expected_cpuset="1", expected_memory_bytes=2000
        )
        assert not valid
        assert len(errors) == 2

# SPDX-License-Identifier: MIT
"""Unit tests for worker and CRS cgroup naming in oss_crs.src.cgroup."""

from oss_crs.src.cgroup import (
    _sanitize_cgroup_name_component,
    generate_worker_cgroup_name,
)


class TestSanitizeCgroupNameComponent:
    def test_clean_name(self):
        assert _sanitize_cgroup_name_component("normal-name_1.0") == "normal-name_1.0"

    def test_path_traversal(self):
        assert _sanitize_cgroup_name_component("../../etc/passwd") == "etc-passwd"

    def test_special_characters(self):
        assert (
            _sanitize_cgroup_name_component("bad@char#dir$name") == "bad-char-dir-name"
        )

    def test_empty_or_all_special(self):
        assert _sanitize_cgroup_name_component("///") == "default"
        assert _sanitize_cgroup_name_component("") == "default"


class TestGenerateWorkerCgroupName:
    def test_basic_generation(self):
        name = generate_worker_cgroup_name("run123", "build")
        parts = name.split("-")
        assert len(parts) >= 4
        assert parts[0] == "run123"
        assert parts[1] == "build"

    def test_run_id_sanitization(self):
        # Path traversal characters and non-alphanumeric chars should be sanitized
        name = generate_worker_cgroup_name("../bad/run!id", "run")
        assert "/" not in name
        assert ".." not in name
        assert "!" not in name

    def test_phase_sanitization(self):
        name = generate_worker_cgroup_name("testrun", "../phase")
        assert "/" not in name
        assert ".." not in name

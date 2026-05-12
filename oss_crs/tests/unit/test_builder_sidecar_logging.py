"""Unit tests for builder sidecar API call logging.

NOTE: The _make_log_entry function is duplicated here because the template
file (oss_crs_builder_server.py) imports FastAPI which is not available in
the test environment. If the implementation changes, these tests must be
updated to match. The canonical source is:
  oss_crs/src/templates/oss_crs_builder_server.py
"""

import json
from pathlib import Path


# ---------------------------------------------------------------------------
# Duplicated from oss_crs_builder_server.py (see NOTE above)
# ---------------------------------------------------------------------------

_EXIT_CODE_KEYS = {
    "build": "build_exit_code",
    "run-pov": "pov_exit_code",
    "run-test": "test_exit_code",
}


def _make_log_entry(
    action: str,
    job_id: str,
    result: dict,
    duration_ms: int,
    ts_start: float,
) -> dict:
    """Create a structured log entry for an API call."""
    exit_code = result.get(_EXIT_CODE_KEYS.get(action, ""), -1)
    entry: dict = {
        "ts": ts_start,
        "api": action,
        "job_id": job_id,
        "duration_ms": duration_ms,
        "exit_code": exit_code,
    }
    for key in ("harness", "build_id"):
        if key in result:
            entry[key] = result[key]
    if "error" in result:
        entry["error"] = result["error"]
    if action == "build":
        entry["build_success"] = exit_code == 0
    elif action == "run-pov":
        entry["crash"] = exit_code > 0 and exit_code != 124
        entry["timeout"] = exit_code == 124
    elif action == "run-test":
        entry["test_passed"] = exit_code == 0
        entry["skipped"] = bool(result.get("test_skipped"))
    return entry


# ---------------------------------------------------------------------------
# Tests: _make_log_entry
# ---------------------------------------------------------------------------


class TestMakeLogEntryBuild:
    """Tests for build API log entries."""

    def test_success(self):
        e = _make_log_entry("build", "abc123", {"build_exit_code": 0}, 8920, 1000.0)
        assert e["api"] == "build"
        assert e["exit_code"] == 0
        assert e["build_success"] is True
        assert e["ts"] == 1000.0
        assert e["duration_ms"] == 8920

    def test_failure(self):
        e = _make_log_entry("build", "def456", {"build_exit_code": 1}, 5000, 1000.0)
        assert e["build_success"] is False

    def test_no_build_id_in_entry(self):
        """Build entries use job_id as build_id; no separate build_id field."""
        e = _make_log_entry("build", "abc123", {"build_exit_code": 0}, 100, 1000.0)
        assert "build_id" not in e

    def test_handler_exception(self):
        e = _make_log_entry("build", "j1", {"error": "compile crashed"}, 100, 1000.0)
        assert e["exit_code"] == -1
        assert e["build_success"] is False
        assert e["error"] == "compile crashed"


class TestMakeLogEntryRunPov:
    """Tests for run-pov API log entries."""

    def test_crash(self):
        result = {"pov_exit_code": 1, "harness": "html", "build_id": "base"}
        e = _make_log_entry("run-pov", "p1", result, 2340, 1000.0)
        assert e["crash"] is True
        assert e["timeout"] is False
        assert e["harness"] == "html"
        assert e["build_id"] == "base"

    def test_no_crash(self):
        result = {"pov_exit_code": 0, "harness": "fuzz", "build_id": "abc"}
        e = _make_log_entry("run-pov", "p2", result, 1500, 1000.0)
        assert e["crash"] is False
        assert e["timeout"] is False

    def test_timeout(self):
        result = {"pov_exit_code": 124, "harness": "fuzz", "build_id": "base"}
        e = _make_log_entry("run-pov", "p3", result, 30000, 1000.0)
        assert e["crash"] is False
        assert e["timeout"] is True
        assert e["exit_code"] == 124

    def test_exit_77_is_crash(self):
        """Exit 77 = libFuzzer security finding, should be classified as crash."""
        result = {"pov_exit_code": 77, "harness": "fuzz", "build_id": "base"}
        e = _make_log_entry("run-pov", "p4", result, 100, 1000.0)
        assert e["crash"] is True
        assert e["exit_code"] == 77

    def test_handler_exception_not_crash(self):
        """Handler exception (exit_code=-1) must not be classified as crash."""
        e = _make_log_entry(
            "run-pov", "p5", {"error": "connection refused"}, 100, 1000.0
        )
        assert e["crash"] is False
        assert e["exit_code"] == -1
        assert e["error"] == "connection refused"

    def test_harness_not_found(self):
        """Exit 127 = harness binary missing, still classified as crash."""
        result = {"pov_exit_code": 127, "harness": "missing", "build_id": "base"}
        e = _make_log_entry("run-pov", "p6", result, 50, 1000.0)
        assert e["crash"] is True
        assert e["harness"] == "missing"


class TestMakeLogEntryRunTest:
    """Tests for run-test API log entries."""

    def test_pass(self):
        result = {"test_exit_code": 0, "test_skipped": False, "build_id": "abc"}
        e = _make_log_entry("run-test", "t1", result, 45000, 1000.0)
        assert e["test_passed"] is True
        assert e["skipped"] is False
        assert e["build_id"] == "abc"

    def test_fail(self):
        result = {"test_exit_code": 1, "test_skipped": False, "build_id": "abc"}
        e = _make_log_entry("run-test", "t2", result, 30000, 1000.0)
        assert e["test_passed"] is False
        assert e["skipped"] is False

    def test_skipped(self):
        result = {"test_exit_code": 0, "test_skipped": True, "build_id": "abc"}
        e = _make_log_entry("run-test", "t3", result, 50, 1000.0)
        assert e["test_passed"] is True
        assert e["skipped"] is True

    def test_handler_exception(self):
        e = _make_log_entry("run-test", "t4", {"error": "timeout"}, 100, 1000.0)
        assert e["exit_code"] == -1
        assert e["test_passed"] is False
        assert e["error"] == "timeout"


class TestMakeLogEntryCommon:
    """Tests for common log entry fields."""

    def test_timestamp_is_start_time(self):
        ts = 1774296986.79
        e = _make_log_entry("build", "j1", {"build_exit_code": 0}, 1000, ts)
        assert e["ts"] == ts

    def test_required_fields_present(self):
        for action, result in [
            ("build", {"build_exit_code": 0}),
            ("run-pov", {"pov_exit_code": 1, "harness": "h", "build_id": "b"}),
            ("run-test", {"test_exit_code": 0, "test_skipped": False, "build_id": "b"}),
        ]:
            e = _make_log_entry(action, "j1", result, 100, 1000.0)
            assert all(
                k in e for k in ("ts", "api", "job_id", "duration_ms", "exit_code")
            )

    def test_all_entries_json_serializable(self):
        entries = [
            _make_log_entry("build", "j1", {"build_exit_code": 0}, 100, 1000.0),
            _make_log_entry(
                "run-pov",
                "j2",
                {"pov_exit_code": 1, "harness": "h", "build_id": "b"},
                100,
                1000.0,
            ),
            _make_log_entry(
                "run-test",
                "j3",
                {"test_exit_code": 0, "test_skipped": False, "build_id": "b"},
                100,
                1000.0,
            ),
            _make_log_entry("run-pov", "j4", {"error": "fail"}, 100, 1000.0),
        ]
        for entry in entries:
            line = json.dumps(entry)
            assert json.loads(line) == entry

    def test_unknown_action(self):
        e = _make_log_entry("unknown", "j1", {}, 100, 1000.0)
        assert e["exit_code"] == -1
        assert "crash" not in e
        assert "build_success" not in e
        assert "test_passed" not in e


# ---------------------------------------------------------------------------
# Tests: JSONL file writing
# ---------------------------------------------------------------------------


class TestLogApiCallFile:
    """Tests for JSONL file writing mechanics."""

    def _write_entry(self, log_file: Path, entry: dict) -> None:
        with log_file.open("a") as f:
            f.write(json.dumps(entry, separators=(",", ":")) + "\n")

    def test_writes_jsonl_lines(self, tmp_path):
        log_file = tmp_path / "api-calls.jsonl"
        log_file.touch()
        self._write_entry(log_file, {"api": "build", "exit_code": 0})
        self._write_entry(log_file, {"api": "run-pov", "exit_code": 1})

        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["api"] == "build"
        assert json.loads(lines[1])["api"] == "run-pov"

    def test_empty_file_means_zero_calls(self, tmp_path):
        log_file = tmp_path / "api-calls.jsonl"
        log_file.touch()
        assert log_file.exists()
        assert log_file.read_text() == ""

    def test_compact_json_no_spaces(self, tmp_path):
        log_file = tmp_path / "api-calls.jsonl"
        log_file.touch()
        self._write_entry(log_file, {"api": "build", "exit_code": 0})
        line = log_file.read_text().strip()
        assert " " not in line  # compact separators

    def test_each_line_is_valid_json(self, tmp_path):
        log_file = tmp_path / "api-calls.jsonl"
        log_file.touch()
        for i in range(5):
            self._write_entry(log_file, {"api": "run-pov", "n": i})
        lines = log_file.read_text().strip().split("\n")
        assert len(lines) == 5
        for line in lines:
            json.loads(line)  # should not raise

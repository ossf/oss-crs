# SPDX-License-Identifier: MIT
"""Unit tests for sidecar server-side API-call logging.

Covers the structured JSONL logging added to the builder-sidecar and
runner-sidecar servers: ``_make_log_entry`` (build / run-pov / run-test
classification) and ``_log_api_call`` (per-CRS JSONL file mechanics).

Unlike the upstream PR, the real functions are importable here, so we test the
actual implementation instead of a duplicated copy. Both sidecars expose a
module named ``server``; the builder is imported via sys.path as ``server`` (to
satisfy its local ``docker_ops`` import) and the runner is loaded via importlib
under the collision-safe alias ``runner_server``.
"""

# ruff: noqa: E402

import importlib.util
import json
import sys
from pathlib import Path

_INFRA = Path(__file__).resolve().parent.parent.parent.parent / "oss-crs-infra"
_BUILDER_DIR = _INFRA / "builder-sidecar"
sys.path.insert(0, str(_BUILDER_DIR))

import server as builder_server

# Load runner-sidecar server.py under a collision-safe alias so it does not
# clobber builder-sidecar's cached ``server`` module in the same pytest session.
_RUNNER_SERVER_PATH = _INFRA / "runner-sidecar" / "server.py"
_spec = importlib.util.spec_from_file_location("runner_server", _RUNNER_SERVER_PATH)
runner_server = importlib.util.module_from_spec(_spec)
sys.modules["runner_server"] = runner_server
_spec.loader.exec_module(runner_server)


def _entry(event, job_id, result, duration_ms=100, ts_start=1000.0):
    """Call the builder server's unified _make_log_entry with test defaults."""
    return builder_server._make_log_entry(
        event, job_id, result, duration_ms, ts_start, "mycrs", "builder-sidecar"
    )


# ---------------------------------------------------------------------------
# Tests: _make_log_entry — build (apply-patch-build)
# ---------------------------------------------------------------------------


class TestMakeLogEntryBuild:
    def test_success(self):
        e = _entry("apply-patch-build", "abc123", {"exit_code": 0}, 8920, 1000.0)
        assert e["event"] == "apply-patch-build"
        assert e["exit_code"] == 0
        assert e["build_success"] is True
        assert e["ts"] == 1000.0
        assert e["duration_ms"] == 8920
        assert e["crs"] == "mycrs"
        assert e["service"] == "builder-sidecar"

    def test_failure(self):
        e = _entry("apply-patch-build", "def456", {"exit_code": 1})
        assert e["build_success"] is False

    def test_handler_exception(self):
        e = _entry("apply-patch-build", "j1", {"error": "compile crashed"})
        assert e["exit_code"] == -1
        assert e["build_success"] is False
        assert e["error"] == "compile crashed"

    def test_rebuild_id_passthrough(self):
        e = _entry("apply-patch-build", "j1", {"exit_code": 0, "rebuild_id": 3})
        assert e["rebuild_id"] == 3


# ---------------------------------------------------------------------------
# Tests: _make_log_entry — run-pov
# ---------------------------------------------------------------------------


class TestMakeLogEntryRunPov:
    def test_crash(self):
        result = {"exit_code": 1, "harness": "html", "rebuild_id": "2"}
        e = _entry("run-pov", "p1", result, 2340, 1000.0)
        assert e["crash"] is True
        assert e["timeout"] is False
        assert e["harness"] == "html"
        assert e["rebuild_id"] == "2"

    def test_no_crash(self):
        e = _entry("run-pov", "p2", {"exit_code": 0, "harness": "fuzz"})
        assert e["crash"] is False
        assert e["timeout"] is False

    def test_timeout(self):
        e = _entry("run-pov", "p3", {"exit_code": 124, "harness": "fuzz"}, 30000, 1.0)
        assert e["crash"] is False
        assert e["timeout"] is True
        assert e["exit_code"] == 124

    def test_exit_77_is_crash(self):
        """Exit 77 = libFuzzer security finding -> crash."""
        e = _entry("run-pov", "p4", {"exit_code": 77, "harness": "fuzz"})
        assert e["crash"] is True
        assert e["exit_code"] == 77

    def test_handler_exception_not_crash(self):
        """Handler exception (exit_code=-1) must not be classified as crash."""
        e = _entry("run-pov", "p5", {"error": "connection refused"})
        assert e["crash"] is False
        assert e["exit_code"] == -1
        assert e["error"] == "connection refused"

    def test_harness_not_found_is_crash(self):
        """Exit 127 = harness binary missing, still classified as crash."""
        e = _entry("run-pov", "p6", {"exit_code": 127, "harness": "missing"})
        assert e["crash"] is True
        assert e["harness"] == "missing"

    def test_runner_server_matches(self):
        """The runner-sidecar's own _make_log_entry classifies identically."""
        e = runner_server._make_log_entry(
            "run-pov",
            "p1",
            {"exit_code": 124, "harness": "h"},
            5,
            1.0,
            "mycrs",
            "runner-sidecar",
        )
        assert e["timeout"] is True
        assert e["crash"] is False
        assert e["service"] == "runner-sidecar"


# ---------------------------------------------------------------------------
# Tests: _make_log_entry — run-test (apply-patch-test)
# ---------------------------------------------------------------------------


class TestMakeLogEntryRunTest:
    def test_pass(self):
        e = _entry("apply-patch-test", "t1", {"exit_code": 0, "test_skipped": False})
        assert e["test_passed"] is True
        assert e["skipped"] is False

    def test_fail(self):
        e = _entry("apply-patch-test", "t2", {"exit_code": 1, "test_skipped": False})
        assert e["test_passed"] is False
        assert e["skipped"] is False

    def test_skipped(self):
        e = _entry("apply-patch-test", "t3", {"exit_code": 0, "test_skipped": True})
        assert e["test_passed"] is True
        assert e["skipped"] is True

    def test_handler_exception(self):
        e = _entry("apply-patch-test", "t4", {"error": "timeout"})
        assert e["exit_code"] == -1
        assert e["test_passed"] is False
        assert e["error"] == "timeout"


# ---------------------------------------------------------------------------
# Tests: _make_log_entry — common fields
# ---------------------------------------------------------------------------


class TestMakeLogEntryCommon:
    def test_timestamp_is_start_time(self):
        ts = 1774296986.79
        e = _entry("apply-patch-build", "j1", {"exit_code": 0}, 1000, ts)
        assert e["ts"] == ts

    def test_required_fields_present(self):
        for event, result in [
            ("apply-patch-build", {"exit_code": 0}),
            ("run-pov", {"exit_code": 1, "harness": "h"}),
            ("apply-patch-test", {"exit_code": 0, "test_skipped": False}),
        ]:
            e = _entry(event, "j1", result)
            assert all(
                k in e
                for k in (
                    "ts",
                    "crs",
                    "event",
                    "service",
                    "job_id",
                    "exit_code",
                    "duration_ms",
                )
            )

    def test_all_entries_json_serializable(self):
        entries = [
            _entry("apply-patch-build", "j1", {"exit_code": 0}),
            _entry("run-pov", "j2", {"exit_code": 1, "harness": "h"}),
            _entry("apply-patch-test", "j3", {"exit_code": 0, "test_skipped": False}),
            _entry("run-pov", "j4", {"error": "fail"}),
        ]
        for entry in entries:
            assert json.loads(json.dumps(entry)) == entry

    def test_unknown_event(self):
        e = _entry("unknown", "j1", {})
        assert e["exit_code"] == -1
        assert "crash" not in e
        assert "build_success" not in e
        assert "test_passed" not in e


# ---------------------------------------------------------------------------
# Tests: _log_api_call — per-CRS JSONL file mechanics
# ---------------------------------------------------------------------------


class TestLogApiCallFile:
    def _read_lines(self, path):
        return [ln for ln in path.read_text().splitlines() if ln.strip()]

    def test_writes_jsonl_lines_per_crs(self, tmp_path, monkeypatch):
        monkeypatch.setattr(builder_server, "_API_LOG_DIR", tmp_path)
        builder_server._log_api_call("crsA", {"event": "apply-patch-build", "n": 0})
        builder_server._log_api_call("crsA", {"event": "run-pov", "n": 1})

        log_file = tmp_path / "crsA" / "libcrs-sidecar-metrics.jsonl"
        assert log_file.exists()
        lines = self._read_lines(log_file)
        assert len(lines) == 2
        assert json.loads(lines[0])["event"] == "apply-patch-build"
        assert json.loads(lines[1])["event"] == "run-pov"

    def test_routes_by_crs_name(self, tmp_path, monkeypatch):
        monkeypatch.setattr(builder_server, "_API_LOG_DIR", tmp_path)
        builder_server._log_api_call("crsA", {"event": "run-pov"})
        builder_server._log_api_call("crsB", {"event": "run-pov"})
        assert (tmp_path / "crsA" / "libcrs-sidecar-metrics.jsonl").exists()
        assert (tmp_path / "crsB" / "libcrs-sidecar-metrics.jsonl").exists()

    def test_compact_json_no_spaces(self, tmp_path, monkeypatch):
        monkeypatch.setattr(builder_server, "_API_LOG_DIR", tmp_path)
        builder_server._log_api_call(
            "crsA", {"event": "apply-patch-build", "exit_code": 0}
        )
        line = (tmp_path / "crsA" / "libcrs-sidecar-metrics.jsonl").read_text().strip()
        assert " " not in line  # compact separators

    def test_each_line_is_valid_json(self, tmp_path, monkeypatch):
        monkeypatch.setattr(builder_server, "_API_LOG_DIR", tmp_path)
        for i in range(5):
            builder_server._log_api_call("crsA", {"event": "run-pov", "n": i})
        lines = self._read_lines(tmp_path / "crsA" / "libcrs-sidecar-metrics.jsonl")
        assert len(lines) == 5
        for line in lines:
            json.loads(line)  # should not raise

    def test_best_effort_never_raises(self, tmp_path, monkeypatch):
        # Point the log dir at a path that cannot be created (a file, not a dir).
        broken = tmp_path / "afile"
        broken.write_text("x")
        monkeypatch.setattr(builder_server, "_API_LOG_DIR", broken)
        # Must not raise despite the unwritable target.
        builder_server._log_api_call("crsA", {"event": "run-pov"})

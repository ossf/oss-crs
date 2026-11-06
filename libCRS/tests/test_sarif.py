# SPDX-License-Identifier: MIT
"""Unit tests for libCRS SARIF 2.1.0 parsing and validation utilities."""

import json
from pathlib import Path

import pytest
from libCRS.sarif import (
    SARIF_VERSION,
    BugCandidate,
    BugLocation,
    _parse_result,
    parse_sarif_dir,
    parse_sarif_file,
    validate_sarif,
)


def _create_minimal_valid_sarif(results=None):
    """Helper to build a valid minimal SARIF 2.1.0 dict."""
    return {
        "version": SARIF_VERSION,
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "test-scanner",
                        "version": "1.0.0",
                    }
                },
                "results": results if results is not None else [],
            }
        ],
    }


# =====================================================================
# Dataclass Tests
# =====================================================================


def test_bug_location_defaults():
    loc = BugLocation(file_path="src/main.c", start_line=42)
    assert loc.file_path == "src/main.c"
    assert loc.start_line == 42
    assert loc.end_line is None
    assert loc.function_name is None


def test_bug_location_full():
    loc = BugLocation(
        file_path="src/util.c",
        start_line=10,
        end_line=25,
        function_name="process_buffer",
    )
    assert loc.file_path == "src/util.c"
    assert loc.start_line == 10
    assert loc.end_line == 25
    assert loc.function_name == "process_buffer"


def test_bug_candidate_defaults():
    candidate = BugCandidate(
        rule_id="CWE-120",
        level="error",
        message="Buffer overflow detected",
    )
    assert candidate.rule_id == "CWE-120"
    assert candidate.level == "error"
    assert candidate.message == "Buffer overflow detected"
    assert candidate.locations == []


def test_bug_candidate_with_locations():
    loc = BugLocation(file_path="main.c", start_line=15)
    candidate = BugCandidate(
        rule_id="CWE-476",
        level="warning",
        message="Null pointer dereference",
        locations=[loc],
    )
    assert len(candidate.locations) == 1
    assert candidate.locations[0].file_path == "main.c"


# =====================================================================
# validate_sarif Tests
# =====================================================================


def test_validate_sarif_valid():
    doc = _create_minimal_valid_sarif(
        results=[
            {
                "ruleId": "CWE-119",
                "message": {"text": "Out of bounds memory access"},
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": "src/parse.c"},
                            "region": {"startLine": 12, "endLine": 15},
                        }
                    }
                ],
            }
        ]
    )
    errors = validate_sarif(doc)
    assert errors == []


def test_validate_sarif_version_mismatch():
    doc = _create_minimal_valid_sarif()
    doc["version"] = "2.0.0"
    errors = validate_sarif(doc)
    assert any("Expected SARIF version '2.1.0', got '2.0.0'" in err for err in errors)


def test_validate_sarif_missing_or_empty_runs():
    # runs missing
    errors = validate_sarif({"version": SARIF_VERSION})
    assert "'runs' must be a non-empty array" in errors

    # runs is empty list
    errors = validate_sarif({"version": SARIF_VERSION, "runs": []})
    assert "'runs' must be a non-empty array" in errors

    # runs is not a list
    errors = validate_sarif({"version": SARIF_VERSION, "runs": "not-a-list"})
    assert "'runs' must be a non-empty array" in errors


def test_validate_sarif_invalid_tool_structure():
    # runs[0].tool is not dict
    doc = {"version": SARIF_VERSION, "runs": [{"tool": "not-dict"}]}
    errors = validate_sarif(doc)
    assert "runs[0].tool must be an object" in errors

    # runs[0].tool.driver is not dict
    doc = {"version": SARIF_VERSION, "runs": [{"tool": {"driver": None}}]}
    errors = validate_sarif(doc)
    assert "runs[0].tool.driver must be an object" in errors

    # runs[0].tool.driver.name is missing / empty
    doc = {"version": SARIF_VERSION, "runs": [{"tool": {"driver": {"name": ""}}}]}
    errors = validate_sarif(doc)
    assert "runs[0].tool.driver.name is required" in errors


def test_validate_sarif_invalid_results():
    doc = {
        "version": SARIF_VERSION,
        "runs": [
            {
                "tool": {"driver": {"name": "scanner"}},
                "results": "not-a-list",
            }
        ],
    }
    errors = validate_sarif(doc)
    assert "runs[0].results must be an array" in errors


def test_validate_sarif_result_missing_message():
    doc = _create_minimal_valid_sarif(results=[{}])
    errors = validate_sarif(doc)
    assert "runs[0].results[0].message is required" in errors

    doc = _create_minimal_valid_sarif(results=[{"message": "string-instead-of-dict"}])
    errors = validate_sarif(doc)
    assert "runs[0].results[0].message.text is required" in errors

    doc = _create_minimal_valid_sarif(results=[{"message": {"text": ""}}])
    errors = validate_sarif(doc)
    assert "runs[0].results[0].message.text is required" in errors


def test_validate_sarif_location_checks():
    # physicalLocation without uri in artifactLocation
    doc = _create_minimal_valid_sarif(
        results=[
            {
                "message": {"text": "issue found"},
                "locations": [{"physicalLocation": {"artifactLocation": {"uri": ""}}}],
            }
        ]
    )
    errors = validate_sarif(doc)
    assert (
        "runs[0].results[0].locations[0].physicalLocation.artifactLocation.uri is required"
        in errors
    )

    # region with non-integer startLine
    doc = _create_minimal_valid_sarif(
        results=[
            {
                "message": {"text": "issue found"},
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": "file.c"},
                            "region": {"startLine": "not-an-int"},
                        }
                    }
                ],
            }
        ]
    )
    errors = validate_sarif(doc)
    assert (
        "runs[0].results[0].locations[0].physicalLocation.region.startLine must be an integer"
        in errors
    )

    # physicalLocation is None (should be skipped gracefully)
    doc = _create_minimal_valid_sarif(
        results=[
            {
                "message": {"text": "issue found"},
                "locations": [{"physicalLocation": None}],
            }
        ]
    )
    errors = validate_sarif(doc)
    assert errors == []


# =====================================================================
# _parse_result Tests
# =====================================================================


def test_parse_result_full():
    result = {
        "ruleId": "CWE-787",
        "level": "error",
        "message": {"text": "Out-of-bounds write"},
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": "src/parser.c"},
                    "region": {"startLine": 105, "endLine": 110},
                },
                "logicalLocations": [
                    {"kind": "module", "name": "parser_module"},
                    {"kind": "function", "name": "parse_header"},
                ],
            }
        ],
    }
    candidate = _parse_result(result)
    assert candidate.rule_id == "CWE-787"
    assert candidate.level == "error"
    assert candidate.message == "Out-of-bounds write"
    assert len(candidate.locations) == 1

    loc = candidate.locations[0]
    assert loc.file_path == "src/parser.c"
    assert loc.start_line == 105
    assert loc.end_line == 110
    assert loc.function_name == "parse_header"


def test_parse_result_defaults_and_fallbacks():
    # missing level, ruleId, non-dict message, location missing uri
    result = {
        "message": "Direct message string",
        "locations": [
            {
                # Location with no artifact uri -> should be skipped
                "physicalLocation": {
                    "artifactLocation": {},
                    "region": {"startLine": 10},
                }
            },
            {
                # Valid location without optional endLine or logicalLocations
                "physicalLocation": {
                    "artifactLocation": {"uri": "src/entry.c"},
                    "region": {},
                }
            },
        ],
    }
    candidate = _parse_result(result)
    assert candidate.rule_id == ""
    assert candidate.level == "warning"
    assert candidate.message == "Direct message string"
    assert len(candidate.locations) == 1

    loc = candidate.locations[0]
    assert loc.file_path == "src/entry.c"
    assert loc.start_line == 0
    assert loc.end_line is None
    assert loc.function_name is None


# =====================================================================
# parse_sarif_file Tests
# =====================================================================


def test_parse_sarif_file_success(tmp_path: Path):
    doc = _create_minimal_valid_sarif(
        results=[
            {
                "ruleId": "CWE-190",
                "level": "warning",
                "message": {"text": "Integer overflow"},
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": "calc.c"},
                            "region": {"startLine": 50},
                        }
                    }
                ],
            }
        ]
    )
    file_path = tmp_path / "report.sarif"
    file_path.write_text(json.dumps(doc), encoding="utf-8")

    candidates = parse_sarif_file(file_path)
    assert len(candidates) == 1
    assert candidates[0].rule_id == "CWE-190"
    assert candidates[0].message == "Integer overflow"
    assert candidates[0].locations[0].file_path == "calc.c"
    assert candidates[0].locations[0].start_line == 50


def test_parse_sarif_file_invalid_sarif_raises(tmp_path: Path):
    doc = {"version": "1.0.0"}  # Invalid version and missing runs
    file_path = tmp_path / "invalid.sarif"
    file_path.write_text(json.dumps(doc), encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid SARIF file"):
        parse_sarif_file(file_path)


def test_parse_sarif_file_nonexistent(tmp_path: Path):
    file_path = tmp_path / "does_not_exist.sarif"
    with pytest.raises(FileNotFoundError):
        parse_sarif_file(file_path)


# =====================================================================
# parse_sarif_dir Tests
# =====================================================================


def test_parse_sarif_dir(tmp_path: Path):
    # Create two SARIF files and one ignored file
    doc1 = _create_minimal_valid_sarif(
        results=[{"ruleId": "RULE-1", "message": {"text": "Bug 1"}}]
    )
    doc2 = _create_minimal_valid_sarif(
        results=[{"ruleId": "RULE-2", "message": {"text": "Bug 2"}}]
    )

    (tmp_path / "b_report.sarif").write_text(json.dumps(doc2), encoding="utf-8")
    (tmp_path / "a_report.sarif.json").write_text(json.dumps(doc1), encoding="utf-8")
    (tmp_path / "ignored.txt").write_text("not sarif", encoding="utf-8")
    (tmp_path / "ignored.json").write_text("{}", encoding="utf-8")

    candidates = parse_sarif_dir(tmp_path)
    # Glob sorts per pattern:
    # *.sarif matches b_report.sarif -> RULE-2
    # *.sarif.json matches a_report.sarif.json -> RULE-1
    assert len(candidates) == 2
    rule_ids = [c.rule_id for c in candidates]
    assert "RULE-1" in rule_ids
    assert "RULE-2" in rule_ids


def test_parse_sarif_dir_empty(tmp_path: Path):
    candidates = parse_sarif_dir(tmp_path)
    assert candidates == []

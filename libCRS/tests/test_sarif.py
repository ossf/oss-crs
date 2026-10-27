# SPDX-License-Identifier: MIT
"""Unit tests for libCRS SARIF 2.1.0 parsing and validation utilities."""

import json
from pathlib import Path

import pytest
from libCRS.sarif import (
    SARIF_VERSION,
    BugCandidate,
    BugLocation,
    parse_sarif_dir,
    parse_sarif_file,
    validate_sarif,
)


def _build_valid_sarif(results=None) -> dict:
    if results is None:
        results = [
            {
                "ruleId": "CWE-119",
                "level": "error",
                "message": {"text": "Buffer overflow detected"},
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": "src/parser.c"},
                            "region": {"startLine": 42, "endLine": 45},
                        },
                        "logicalLocations": [
                            {"name": "parse_packet", "kind": "function"}
                        ],
                    }
                ],
            }
        ]
    return {
        "version": SARIF_VERSION,
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "example-scanner",
                        "version": "1.0.0",
                    }
                },
                "results": results,
            }
        ],
    }


def test_validate_sarif_valid():
    doc = _build_valid_sarif()
    errors = validate_sarif(doc)
    assert errors == []


def test_validate_sarif_invalid_version():
    doc = _build_valid_sarif()
    doc["version"] = "2.0.0"
    errors = validate_sarif(doc)
    assert any("Expected SARIF version '2.1.0'" in err for err in errors)


def test_validate_sarif_missing_or_empty_runs():
    errors_none = validate_sarif({"version": SARIF_VERSION})
    assert any("'runs' must be a non-empty array" in err for err in errors_none)

    errors_empty = validate_sarif({"version": SARIF_VERSION, "runs": []})
    assert any("'runs' must be a non-empty array" in err for err in errors_empty)


def test_validate_sarif_invalid_tool_structure():
    doc = {"version": SARIF_VERSION, "runs": [{}]}
    errors = validate_sarif(doc)
    assert any("runs[0].tool must be an object" in err for err in errors)

    doc["runs"] = [{"tool": {}}]
    errors = validate_sarif(doc)
    assert any("runs[0].tool.driver must be an object" in err for err in errors)

    doc["runs"] = [{"tool": {"driver": {}}}]
    errors = validate_sarif(doc)
    assert any("runs[0].tool.driver.name is required" in err for err in errors)


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
    assert any("runs[0].results must be an array" in err for err in errors)


def test_validate_sarif_invalid_result_message():
    doc = _build_valid_sarif(results=[{}])
    errors = validate_sarif(doc)
    assert any("runs[0].results[0].message is required" in err for err in errors)

    doc = _build_valid_sarif(results=[{"message": {"text": ""}}])
    errors = validate_sarif(doc)
    assert any("runs[0].results[0].message.text is required" in err for err in errors)

    doc_str_msg = _build_valid_sarif(results=[{"message": "not-a-dict"}])
    errors = validate_sarif(doc_str_msg)
    assert any("runs[0].results[0].message.text is required" in err for err in errors)


def test_validate_sarif_location_validation():
    bad_loc_doc = _build_valid_sarif(
        results=[
            {
                "message": {"text": "Bug found"},
                "locations": [
                    {
                        "physicalLocation": {
                            "artifactLocation": {"uri": ""},
                            "region": {"startLine": "not-an-int"},
                        }
                    }
                ],
            }
        ]
    )
    errors = validate_sarif(bad_loc_doc)
    assert any("artifactLocation.uri is required" in err for err in errors)
    assert any("region.startLine must be an integer" in err for err in errors)


def test_parse_sarif_file_success(tmp_path: Path):
    sarif_file = tmp_path / "report.sarif"
    doc = _build_valid_sarif()
    sarif_file.write_text(json.dumps(doc), encoding="utf-8")

    candidates = parse_sarif_file(sarif_file)
    assert len(candidates) == 1
    cand = candidates[0]
    assert isinstance(cand, BugCandidate)
    assert cand.rule_id == "CWE-119"
    assert cand.level == "error"
    assert cand.message == "Buffer overflow detected"
    assert len(cand.locations) == 1

    loc = cand.locations[0]
    assert isinstance(loc, BugLocation)
    assert loc.file_path == "src/parser.c"
    assert loc.start_line == 42
    assert loc.end_line == 45
    assert loc.function_name == "parse_packet"


def test_parse_sarif_file_invalid_raises_value_error(tmp_path: Path):
    sarif_file = tmp_path / "invalid.sarif"
    sarif_file.write_text(
        json.dumps({"version": "1.0.0", "runs": []}), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="Invalid SARIF file"):
        parse_sarif_file(sarif_file)


def test_parse_sarif_file_without_locations_and_string_message(tmp_path: Path):
    sarif_file = tmp_path / "minimal.sarif"
    doc = {
        "version": SARIF_VERSION,
        "runs": [
            {
                "tool": {"driver": {"name": "minimal-scanner"}},
                "results": [
                    {
                        "ruleId": "TEST-01",
                        "level": "note",
                        "message": {"text": "Simple finding"},
                    }
                ],
            }
        ],
    }
    sarif_file.write_text(json.dumps(doc), encoding="utf-8")

    candidates = parse_sarif_file(sarif_file)
    assert len(candidates) == 1
    assert candidates[0].rule_id == "TEST-01"
    assert candidates[0].level == "note"
    assert candidates[0].locations == []


def test_parse_sarif_dir_multiple_patterns(tmp_path: Path):
    file1 = tmp_path / "scan1.sarif"
    file2 = tmp_path / "scan2.sarif.json"
    ignored_file = tmp_path / "scan.txt"

    doc1 = _build_valid_sarif(
        results=[{"ruleId": "RULE-1", "message": {"text": "Finding 1"}}]
    )
    doc2 = _build_valid_sarif(
        results=[{"ruleId": "RULE-2", "message": {"text": "Finding 2"}}]
    )

    file1.write_text(json.dumps(doc1), encoding="utf-8")
    file2.write_text(json.dumps(doc2), encoding="utf-8")
    ignored_file.write_text("irrelevant", encoding="utf-8")

    candidates = parse_sarif_dir(tmp_path)
    assert len(candidates) == 2
    rule_ids = {c.rule_id for c in candidates}
    assert rule_ids == {"RULE-1", "RULE-2"}

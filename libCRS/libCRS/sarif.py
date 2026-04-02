"""SARIF 2.1.0 parsing and validation utilities for bug-candidate reports."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


SARIF_VERSION = "2.1.0"


@dataclass
class BugLocation:
    file_path: str
    start_line: int
    end_line: Optional[int] = None
    function_name: Optional[str] = None


@dataclass
class BugCandidate:
    rule_id: str
    level: str
    message: str
    locations: list[BugLocation] = field(default_factory=list)


def validate_sarif(doc: dict) -> list[str]:
    """Validate SARIF 2.1.0 required fields.

    Returns a list of error messages. An empty list means the document is valid.
    """
    errors: list[str] = []

    version = doc.get("version")
    if version != SARIF_VERSION:
        errors.append(f"Expected SARIF version '{SARIF_VERSION}', got '{version}'")

    runs = doc.get("runs")
    if not isinstance(runs, list) or len(runs) == 0:
        errors.append("'runs' must be a non-empty array")
        return errors

    for run_idx, run in enumerate(runs):
        tool = run.get("tool")
        if not isinstance(tool, dict):
            errors.append(f"runs[{run_idx}].tool must be an object")
            continue
        driver = tool.get("driver")
        if not isinstance(driver, dict):
            errors.append(f"runs[{run_idx}].tool.driver must be an object")
            continue
        if not driver.get("name"):
            errors.append(f"runs[{run_idx}].tool.driver.name is required")

        results = run.get("results")
        if not isinstance(results, list):
            errors.append(f"runs[{run_idx}].results must be an array")
            continue

        for res_idx, result in enumerate(results):
            prefix = f"runs[{run_idx}].results[{res_idx}]"
            if not result.get("message"):
                errors.append(f"{prefix}.message is required")
            elif not isinstance(result["message"], dict) or not result["message"].get(
                "text"
            ):
                errors.append(f"{prefix}.message.text is required")

            locations = result.get("locations")
            if isinstance(locations, list):
                for loc_idx, loc in enumerate(locations):
                    loc_prefix = f"{prefix}.locations[{loc_idx}]"
                    phys = loc.get("physicalLocation")
                    if phys is None:
                        continue
                    artifact = phys.get("artifactLocation")
                    if artifact and not artifact.get("uri"):
                        errors.append(
                            f"{loc_prefix}.physicalLocation.artifactLocation.uri is required"
                        )
                    region = phys.get("region")
                    if region and not isinstance(region.get("startLine"), int):
                        errors.append(
                            f"{loc_prefix}.physicalLocation.region.startLine must be an integer"
                        )

    return errors


def _parse_result(result: dict) -> BugCandidate:
    """Parse a single SARIF result into a BugCandidate."""
    locations: list[BugLocation] = []
    for loc in result.get("locations", []):
        phys = loc.get("physicalLocation", {})
        artifact = phys.get("artifactLocation", {})
        region = phys.get("region", {})
        file_path = artifact.get("uri", "")
        if not file_path:
            continue
        start_line = region.get("startLine", 0)
        end_line = region.get("endLine")

        function_name = None
        for logical in loc.get("logicalLocations", []):
            if logical.get("kind") == "function" and logical.get("name"):
                function_name = logical["name"]
                break

        locations.append(
            BugLocation(
                file_path=file_path,
                start_line=start_line,
                end_line=end_line,
                function_name=function_name,
            )
        )

    message = result.get("message", {})
    message_text = (
        message.get("text", "") if isinstance(message, dict) else str(message)
    )

    return BugCandidate(
        rule_id=result.get("ruleId", ""),
        level=result.get("level", "warning"),
        message=message_text,
        locations=locations,
    )


def parse_sarif_file(path: Path) -> list[BugCandidate]:
    """Parse a SARIF 2.1.0 file and return a list of BugCandidates."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    errors = validate_sarif(doc)
    if errors:
        raise ValueError(f"Invalid SARIF file {path}: {'; '.join(errors)}")

    candidates: list[BugCandidate] = []
    for run in doc.get("runs", []):
        for result in run.get("results", []):
            candidates.append(_parse_result(result))
    return candidates


def parse_sarif_dir(dir_path: Path) -> list[BugCandidate]:
    """Parse all SARIF files (*.sarif, *.sarif.json) in a directory."""
    candidates: list[BugCandidate] = []
    for pattern in ("*.sarif", "*.sarif.json"):
        for f in sorted(dir_path.glob(pattern)):
            candidates.extend(parse_sarif_file(f))
    return candidates

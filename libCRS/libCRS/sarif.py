# SPDX-License-Identifier: MIT
"""SARIF 2.1.0 parsing and validation utilities for bug-candidate reports.

Beyond validation, this module surfaces the SARIF-native fields the
bug-candidate interface builds identity and versioning on:

- ``result.correlationGuid`` — the stable identifier for the equivalence class
  of logically identical results (i.e. "the same bug across versions/runs"). The
  bug-candidate ``candidate_id`` *is* this value.
- ``result.guid`` / ``result.partialFingerprints`` — per-instance identity and
  matching hints used for explicit merge/dedup.
- ``run.versionControlProvenance[].revisionId`` (with ``repositoryUri`` /
  ``branch``) — the program version a location is valid for.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Optional


SARIF_VERSION = "2.1.0"


@dataclass
class VersionInfo:
    """Version-control provenance for a SARIF run (or a single location)."""

    revision_id: Optional[str] = None
    repository_uri: Optional[str] = None
    branch: Optional[str] = None


@dataclass
class BugLocation:
    file_path: str
    start_line: int
    end_line: Optional[int] = None
    function_name: Optional[str] = None
    start_column: Optional[int] = None
    end_column: Optional[int] = None
    uri_base_id: Optional[str] = None
    # Program version this location is valid for (SARIF revisionId).
    version: Optional[str] = None
    repository_uri: Optional[str] = None
    branch: Optional[str] = None


@dataclass
class BugCandidate:
    rule_id: str
    level: str
    message: str
    locations: list[BugLocation] = field(default_factory=list)
    # SARIF-native identity.
    correlation_guid: Optional[str] = None
    guid: Optional[str] = None
    partial_fingerprints: dict = field(default_factory=dict)


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


def run_version_info(run: dict) -> VersionInfo:
    """Extract version-control provenance from a SARIF run.

    Uses the first ``versionControlProvenance`` entry; SARIF permits several
    (e.g. one per submodule), but bug-candidate locations map to a single
    program version, so the first entry is the run's version.
    """
    provenance = run.get("versionControlProvenance")
    if isinstance(provenance, list) and provenance:
        vcs = provenance[0]
        if isinstance(vcs, dict):
            return VersionInfo(
                revision_id=vcs.get("revisionId"),
                repository_uri=vcs.get("repositoryUri"),
                branch=vcs.get("branch"),
            )
    return VersionInfo()


def _parse_result(result: dict, version: VersionInfo) -> BugCandidate:
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
                start_column=region.get("startColumn"),
                end_column=region.get("endColumn"),
                uri_base_id=artifact.get("uriBaseId"),
                version=version.revision_id,
                repository_uri=version.repository_uri,
                branch=version.branch,
            )
        )

    message = result.get("message", {})
    message_text = (
        message.get("text", "") if isinstance(message, dict) else str(message)
    )

    fingerprints = result.get("partialFingerprints")
    if not isinstance(fingerprints, dict):
        fingerprints = {}

    return BugCandidate(
        rule_id=result.get("ruleId", ""),
        level=result.get("level", "warning"),
        message=message_text,
        locations=locations,
        correlation_guid=result.get("correlationGuid"),
        guid=result.get("guid"),
        partial_fingerprints=fingerprints,
    )


def iter_results(doc: dict) -> Iterator[tuple[dict, VersionInfo]]:
    """Yield ``(raw_result, version_info)`` pairs across all runs.

    Yields the *original* SARIF result dicts (not the parsed dataclass) so
    callers that embed results in event payloads keep every field intact.
    """
    for run in doc.get("runs", []):
        version = run_version_info(run)
        for result in run.get("results", []):
            yield result, version


def parse_sarif_doc(doc: dict) -> list[BugCandidate]:
    """Parse an already-loaded SARIF document into BugCandidates."""
    return [_parse_result(result, version) for result, version in iter_results(doc)]


def parse_sarif_file(path: Path) -> list[BugCandidate]:
    """Parse a SARIF 2.1.0 file and return a list of BugCandidates."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    errors = validate_sarif(doc)
    if errors:
        raise ValueError(f"Invalid SARIF file {path}: {'; '.join(errors)}")
    return parse_sarif_doc(doc)


def parse_sarif_dir(dir_path: Path) -> list[BugCandidate]:
    """Parse all SARIF files (*.sarif, *.sarif.json) in a directory."""
    candidates: list[BugCandidate] = []
    for pattern in ("*.sarif", "*.sarif.json"):
        for f in sorted(dir_path.glob(pattern)):
            candidates.extend(parse_sarif_file(f))
    return candidates


def wrap_result(result: dict, version: VersionInfo | None = None) -> dict:
    """Build a minimal single-result SARIF 2.1.0 document around ``result``.

    Used to persist one bug-candidate's SARIF result as a standalone artifact.
    """
    run: dict = {
        "tool": {"driver": {"name": "libCRS"}},
        "results": [result],
    }
    if version is not None and (
        version.revision_id or version.repository_uri or version.branch
    ):
        vcs: dict = {}
        if version.repository_uri:
            vcs["repositoryUri"] = version.repository_uri
        if version.revision_id:
            vcs["revisionId"] = version.revision_id
        if version.branch:
            vcs["branch"] = version.branch
        run["versionControlProvenance"] = [vcs]
    return {"version": SARIF_VERSION, "runs": [run]}

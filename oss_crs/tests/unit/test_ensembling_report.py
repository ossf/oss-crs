# SPDX-License-Identifier: MIT
"""Unit tests for ensembling effectiveness calculation and reporting."""

import tempfile
from pathlib import Path
from scripts.ensembling_report import (
    CRSFindings,
    calculate_effectiveness,
    generate_markdown_report,
    collect_from_work_dir,
)


def test_calculate_effectiveness_unique_and_redundant():
    findings = {
        "crs-libfuzzer": CRSFindings(
            name="crs-libfuzzer",
            pov_digests={"hash1", "hash2"},
            patch_digests=set(),
            pov_count=2,
        ),
        "crs-atlantis": CRSFindings(
            name="crs-atlantis",
            pov_digests={"hash2", "hash3"},
            patch_digests={"patch1"},
            pov_count=2,
            patch_count=1,
        ),
        "crs-candidate-new": CRSFindings(
            name="crs-candidate-new",
            pov_digests={"hash3", "hash4"},
            patch_digests={"patch2"},
            pov_count=2,
            patch_count=1,
        ),
    }

    metrics = calculate_effectiveness(findings, candidate_crs="crs-candidate-new")

    assert metrics["total_ensemble_povs"] == 4
    assert metrics["total_ensemble_patches"] == 2
    assert metrics["candidate"]["unique_povs"] == 1  # hash4
    assert metrics["candidate"]["unique_patches"] == 1  # patch2
    assert not metrics["candidate"]["is_purely_redundant"]

    # Redundant candidate scenario
    findings_redundant = {
        "baseline-1": CRSFindings(name="baseline-1", pov_digests={"a", "b"}, pov_count=2),
        "candidate-dup": CRSFindings(name="candidate-dup", pov_digests={"a"}, pov_count=1),
    }
    redundant_metrics = calculate_effectiveness(findings_redundant, candidate_crs="candidate-dup")
    assert redundant_metrics["candidate"]["unique_povs"] == 0
    assert redundant_metrics["candidate"]["is_purely_redundant"]


def test_generate_markdown_report():
    findings = {
        "crs-a": CRSFindings(name="crs-a", pov_digests={"1"}, pov_count=1),
        "crs-b": CRSFindings(name="crs-b", pov_digests={"1", "2"}, pov_count=2),
    }
    metrics = calculate_effectiveness(findings, candidate_crs="crs-b")
    report = generate_markdown_report(findings, metrics, candidate_crs="crs-b", benchmark_name="mock-c-project")

    assert "## 🔬 CRS Ensembling Effectiveness Report" in report
    assert "mock-c-project" in report
    assert "crs-b" in report
    assert "Candidate Component Value Add" in report


def test_collect_from_work_dir():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        submit1 = root / "crs" / "crs-fuzzer" / "submit" / "povs"
        submit1.mkdir(parents=True)
        (submit1 / "pov_1.bin").write_bytes(b"crash-input-1")

        submit2 = root / "crs" / "crs-patcher" / "submit" / "patches"
        submit2.mkdir(parents=True)
        (submit2 / "patch_1.diff").write_bytes(b"diff-content")

        findings = collect_from_work_dir(root)

        assert "crs-fuzzer" in findings
        assert "crs-patcher" in findings
        assert findings["crs-fuzzer"].pov_count == 1
        assert findings["crs-patcher"].patch_count == 1

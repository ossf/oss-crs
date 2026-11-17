#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Ensembling effectiveness evaluation and report generation script for OSS-CRS.

Evaluates performance advantages across Cyber Reasoning Systems (CRSs) in an ensemble,
identifying unique findings (POVs, patches, seeds, bug candidates), overlap/redundancy,
and marginal value added by a candidate CRS vs. baseline ensemble.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


def _compute_file_digest(file_path: Path) -> str:
    """Compute sha256 of file contents for deduplication."""
    hasher = hashlib.sha256()
    try:
        hasher.update(file_path.read_bytes())
        return hasher.hexdigest()[:16]
    except (OSError, PermissionError):
        return f"err-{file_path.name}"


@dataclass
class CRSFindings:
    name: str
    pov_digests: Set[str] = field(default_factory=set)
    patch_digests: Set[str] = field(default_factory=set)
    seed_digests: Set[str] = field(default_factory=set)
    bug_candidate_count: int = 0
    pov_count: int = 0
    patch_count: int = 0
    seed_count: int = 0
    llm_tokens: int = 0
    llm_cost: float = 0.0


def scan_submit_dir(submit_path: Path) -> tuple[Set[str], Set[str], Set[str], int]:
    """Scan a CRS submit directory for artifacts."""
    povs: Set[str] = set()
    patches: Set[str] = set()
    seeds: Set[str] = set()
    bug_candidates_count = 0

    if not submit_path.is_dir():
        return povs, patches, seeds, bug_candidates_count

    pov_dir = submit_path / "povs"
    if pov_dir.is_dir():
        for f in pov_dir.iterdir():
            if f.is_file() and not f.name.startswith("."):
                povs.add(_compute_file_digest(f))

    patch_dir = submit_path / "patches"
    if patch_dir.is_dir():
        for f in patch_dir.iterdir():
            if f.is_file() and not f.name.startswith("."):
                patches.add(_compute_file_digest(f))

    seed_dir = submit_path / "seeds"
    if seed_dir.is_dir():
        for f in seed_dir.iterdir():
            if f.is_file() and not f.name.startswith("."):
                seeds.add(_compute_file_digest(f))

    bc_dir = submit_path / "bug-candidates"
    if bc_dir.is_dir():
        bug_candidates_count = sum(1 for f in bc_dir.iterdir() if f.is_file() and not f.name.startswith("."))

    return povs, patches, seeds, bug_candidates_count


def collect_from_work_dir(work_dir: Path, target_name: Optional[str] = None) -> Dict[str, CRSFindings]:
    """Collect artifacts produced across all CRSs from work_dir."""
    findings: Dict[str, CRSFindings] = {}

    # Look for run meta file first if available
    meta_files = list(work_dir.glob("**/meta.json"))
    meta_info: Dict[str, Any] = {}
    if meta_files:
        try:
            meta_info = json.loads(meta_files[0].read_text())
        except Exception:
            pass

    # Inspect submit directories
    # Standard workdir layout: <work_dir>/crs/<crs_name>/submit or <work_dir>/<target>/.../submit
    submit_dirs = list(work_dir.glob("**/submit")) + list(work_dir.glob("**/submit_dir"))
    if not submit_dirs:
        # Fallback: check subdirectories under work_dir
        for child in work_dir.iterdir():
            if child.is_dir():
                direct_submit = child / "submit"
                if direct_submit.is_dir():
                    submit_dirs.append(direct_submit)

    for s_dir in submit_dirs:
        # Extract CRS name from path
        crs_name = s_dir.parent.name
        if crs_name in ("povs", "patches", "seeds", "exchange"):
            continue

        povs, patches, seeds, bc_count = scan_submit_dir(s_dir)
        if crs_name not in findings:
            findings[crs_name] = CRSFindings(name=crs_name)

        f = findings[crs_name]
        f.pov_digests.update(povs)
        f.patch_digests.update(patches)
        f.seed_digests.update(seeds)
        f.bug_candidate_count += bc_count

    # Populate metadata counts if available
    crs_meta = meta_info.get("crs", {})
    for crs_name, c_stats in crs_meta.items():
        if crs_name not in findings:
            findings[crs_name] = CRSFindings(name=crs_name)
        f = findings[crs_name]
        llm = c_stats.get("llm", {})
        f.llm_tokens = llm.get("prompt_tokens", 0) + llm.get("completion_tokens", 0)
        f.llm_cost = llm.get("credits_used", 0.0)

    for f in findings.values():
        f.pov_count = len(f.pov_digests)
        f.patch_count = len(f.patch_digests)
        f.seed_count = len(f.seed_digests)

    return findings


def calculate_effectiveness(
    findings: Dict[str, CRSFindings],
    candidate_crs: Optional[str] = None,
) -> Dict[str, Any]:
    """Calculate ensemble overlap and marginal advantages."""
    all_povs: Set[str] = set()
    all_patches: Set[str] = set()
    all_seeds: Set[str] = set()

    for f in findings.values():
        all_povs.update(f.pov_digests)
        all_patches.update(f.patch_digests)
        all_seeds.update(f.seed_digests)

    unique_povs: Dict[str, Set[str]] = {}
    unique_patches: Dict[str, Set[str]] = {}
    unique_seeds: Dict[str, Set[str]] = {}

    for name, f in findings.items():
        others_povs = set()
        others_patches = set()
        others_seeds = set()
        for other_name, other_f in findings.items():
            if other_name != name:
                others_povs.update(other_f.pov_digests)
                others_patches.update(other_f.patch_digests)
                others_seeds.update(other_f.seed_digests)

        unique_povs[name] = f.pov_digests - others_povs
        unique_patches[name] = f.patch_digests - others_patches
        unique_seeds[name] = f.seed_digests - others_seeds

    # Marginal analysis for candidate CRS
    candidate_stats: Optional[Dict[str, Any]] = None
    if candidate_crs and candidate_crs in findings:
        cf = findings[candidate_crs]
        baseline_povs = all_povs - cf.pov_digests
        baseline_patches = all_patches - cf.patch_digests

        marginal_pov_pct = (len(unique_povs[candidate_crs]) / len(all_povs) * 100) if all_povs else 0.0
        marginal_patch_pct = (len(unique_patches[candidate_crs]) / len(all_patches) * 100) if all_patches else 0.0

        candidate_stats = {
            "name": candidate_crs,
            "unique_povs": len(unique_povs[candidate_crs]),
            "unique_patches": len(unique_patches[candidate_crs]),
            "unique_seeds": len(unique_seeds[candidate_crs]),
            "marginal_pov_gain_pct": marginal_pov_pct,
            "marginal_patch_gain_pct": marginal_patch_pct,
            "total_ensemble_povs_without": len(baseline_povs),
            "total_ensemble_povs_with": len(all_povs),
            "is_purely_redundant": len(unique_povs[candidate_crs]) == 0 and len(unique_patches[candidate_crs]) == 0,
        }

    return {
        "total_ensemble_povs": len(all_povs),
        "total_ensemble_patches": len(all_patches),
        "total_ensemble_seeds": len(all_seeds),
        "crs_count": len(findings),
        "unique_povs_by_crs": {k: len(v) for k, v in unique_povs.items()},
        "unique_patches_by_crs": {k: len(v) for k, v in unique_patches.items()},
        "candidate": candidate_stats,
    }


def generate_markdown_report(
    findings: Dict[str, CRSFindings],
    metrics: Dict[str, Any],
    candidate_crs: Optional[str] = None,
    benchmark_name: str = "Benchmark Suite",
) -> str:
    """Generate GitHub-friendly markdown report summarizing ensembling effectiveness."""
    lines: List[str] = []
    lines.append("## 🔬 CRS Ensembling Effectiveness Report")
    lines.append("")
    lines.append(f"**Target Evaluation Scope**: `{benchmark_name}`  ")
    lines.append(f"**Evaluated CRSs**: {metrics['crs_count']}  ")
    lines.append(f"**Total Distinct Ensemble POVs**: `{metrics['total_ensemble_povs']}` | **Patches**: `{metrics['total_ensemble_patches']}`")
    lines.append("")

    if candidate_crs and metrics.get("candidate"):
        cand = metrics["candidate"]
        lines.append("### 🎯 Candidate Component Value Add")
        lines.append("")
        if cand["is_purely_redundant"]:
            lines.append(f"> ⚠️ **Notice**: `{candidate_crs}` produced **0 unique POVs** and **0 unique patches** beyond what existing ensemble components already discovered.")
        else:
            lines.append(f"> ✅ **Value Added**: `{candidate_crs}` contributed **+{cand['unique_povs']} unique POV(s)** and **+{cand['unique_patches']} unique patch(es)** to the ensemble.")
        lines.append("")
        lines.append(f"- **Ensemble POV Delta**: {cand['total_ensemble_povs_without']} ➔ {cand['total_ensemble_povs_with']} (+{cand['marginal_pov_gain_pct']:.1f}% marginal gain)")
        lines.append(f"- **Unique Patches Contributed**: {cand['unique_patches']}")
        lines.append(f"- **Unique Seeds Contributed**: {cand['unique_seeds']}")
        lines.append("")

    lines.append("### 📊 Component Performance Breakdown")
    lines.append("")
    lines.append("| CRS Name | Total POVs | Unique POVs | Total Patches | Unique Patches | Bug Candidates | LLM Cost ($) |")
    lines.append("|:---|---:|---:|---:|---:|---:|---:|")

    for name, f in sorted(findings.items(), key=lambda x: x[0]):
        u_pov = metrics["unique_povs_by_crs"].get(name, 0)
        u_patch = metrics["unique_patches_by_crs"].get(name, 0)
        cost_str = f"${f.llm_cost:.2f}" if f.llm_cost > 0 else "-"
        is_candidate_marker = " *(Candidate)*" if name == candidate_crs else ""
        lines.append(f"| `{name}`{is_candidate_marker} | {f.pov_count} | **{u_pov}** | {f.patch_count} | **{u_patch}** | {f.bug_candidate_count} | {cost_str} |")

    lines.append("")
    lines.append("---")
    lines.append("*Generated by `oss-crs` Ensembling Effectiveness Pipeline (`#74`).*")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate Ensembling Effectiveness Report across Cyber Reasoning Systems."
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        required=True,
        help="Path to oss-crs work directory containing run outputs.",
    )
    parser.add_argument(
        "--candidate-crs",
        type=str,
        default=None,
        help="Name of the new or updated CRS under evaluation to calculate marginal advantage.",
    )
    parser.add_argument(
        "--benchmark-name",
        type=str,
        default="Benchmark Suite",
        help="Descriptive name of the benchmark target(s).",
    )
    parser.add_argument(
        "--output-markdown",
        type=Path,
        default=None,
        help="Optional path to output the markdown summary report (e.g. for PR comments).",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=None,
        help="Optional path to output raw structured metric json.",
    )

    args = parser.parse_args()

    if not args.work_dir.exists():
        print(f"Error: work-dir does not exist: {args.work_dir}", file=sys.stderr)
        return 1

    findings = collect_from_work_dir(args.work_dir)
    if not findings:
        print(f"Warning: No CRS outputs or submit directories discovered under {args.work_dir}", file=sys.stderr)
        # Create empty placeholder if candidate is specified
        if args.candidate_crs:
            findings[args.candidate_crs] = CRSFindings(name=args.candidate_crs)

    metrics = calculate_effectiveness(findings, candidate_crs=args.candidate_crs)
    report_md = generate_markdown_report(
        findings=findings,
        metrics=metrics,
        candidate_crs=args.candidate_crs,
        benchmark_name=args.benchmark_name,
    )

    if args.output_markdown:
        args.output_markdown.parent.mkdir(parents=True, exist_ok=True)
        args.output_markdown.write_text(report_md, encoding="utf-8")
        print(f"Wrote markdown report to {args.output_markdown}")

    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        dump_data = {
            "metrics": metrics,
            "components": {k: v.__dict__ for k, v in findings.items()},
        }
        # Serialize sets as lists
        def set_default(obj):
            if isinstance(obj, set):
                return list(obj)
            raise TypeError
        args.output_json.write_text(json.dumps(dump_data, indent=2, default=set_default), encoding="utf-8")
        print(f"Wrote JSON metrics to {args.output_json}")

    print("\n" + report_md)
    return 0


if __name__ == "__main__":
    sys.exit(main())

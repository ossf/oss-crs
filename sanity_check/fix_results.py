#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Fix results.csv by re-checking artifact counts from the workdir.

Rows that say "failed at run (exit code 1)" were timeout exits, not real
failures.  This script re-evaluates them by scanning the workdir for POVs
and seeds, then writes a corrected CSV.

Usage:
    uv run python sanity_check/fix_results.py results.csv results-fixed.csv \\
        --work-dir .oss-crs-workdir
"""

import argparse
import csv
import sys
from pathlib import Path



def count_files(directory: Path) -> int:
    if not directory.is_dir():
        return 0
    return sum(1 for p in directory.iterdir() if p.is_file() and not p.name.startswith("."))


def find_submit_dirs(work_dir: Path, crs_name: str, harness: str) -> list[Path]:
    """Find all SUBMIT_DIR paths for a CRS + harness across all config hashes and runs."""
    results = []
    crs_compose_dir = work_dir / "crs_compose"
    if not crs_compose_dir.exists():
        return results
    for hash_dir in crs_compose_dir.iterdir():
        if not hash_dir.is_dir() or hash_dir.name == "crs_src":
            continue
        for sanitizer_dir in hash_dir.iterdir():
            if not sanitizer_dir.is_dir():
                continue
            runs_dir = sanitizer_dir / "runs"
            if not runs_dir.is_dir():
                continue
            for run_dir in runs_dir.iterdir():
                if not run_dir.is_dir():
                    continue
                # Look for SUBMIT_DIR under crs/{crs_name}/*/SUBMIT_DIR/{harness}/
                crs_dir = run_dir / "crs" / crs_name
                if not crs_dir.is_dir():
                    continue
                for target_key_dir in crs_dir.iterdir():
                    submit = target_key_dir / "SUBMIT_DIR" / harness
                    if submit.is_dir():
                        results.append(submit)
    return results


def recheck_row(row: dict, work_dir: Path) -> dict:
    """Re-evaluate a row that failed at run with exit code 1."""
    row = dict(row)

    if row["failed_stage"] != "run" or row["detail"] not in (
        "failed at run (exit code 1)",
        "failed at run (exit code 124)",
    ):
        return row

    crs_name = row["crs"]
    target = row["target"]
    # target is "project_name/harness"
    parts = target.rsplit("/", 1)
    if len(parts) != 2:
        return row
    harness = parts[1]

    # Find submit dirs and count artifacts
    submit_dirs = find_submit_dirs(work_dir, crs_name, harness)

    pov_total = 0
    seed_total = 0
    for submit in submit_dirs:
        pov_dir = submit / "povs"
        seed_dir = submit / "seeds"
        pov_total += count_files(pov_dir)
        seed_total += count_files(seed_dir)

    # Re-evaluate status
    row["seeds"] = str(seed_total)
    row["povs"] = str(pov_total)
    row["failed_stage"] = ""

    if seed_total == 0 and pov_total == 0:
        row["status"] = "ERROR"
        row["detail"] = "no seeds and no POVs produced"
    elif pov_total == 0:
        row["status"] = "WARN"
        row["detail"] = "no POVs produced"
    else:
        row["status"] = "OK"
        row["detail"] = ""

    return row


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fix results.csv by re-checking artifacts for timeout exits"
    )
    parser.add_argument("input", type=Path, help="Input results.csv")
    parser.add_argument("output", type=Path, help="Output fixed CSV")
    parser.add_argument(
        "--work-dir", type=Path, default=Path(".oss-crs-workdir"),
        help="Work directory to scan for artifacts",
    )
    args = parser.parse_args()

    if not args.input.exists():
        print(f"Error: {args.input} not found", file=sys.stderr)
        return 1

    with open(args.input, newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = reader.fieldnames

    fixed = 0
    for i, row in enumerate(rows):
        new_row = recheck_row(row, args.work_dir.resolve())
        if new_row != rows[i]:
            old_status = rows[i]["status"]
            print(f"  Fixed: {row['crs']} @ {row['target']}: {old_status} -> {new_row['status']} "
                  f"(seeds={new_row['seeds']}, povs={new_row['povs']})")
            rows[i] = new_row
            fixed += 1

    assert fieldnames is not None
    with open(args.output, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\n{fixed} row(s) fixed, written to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

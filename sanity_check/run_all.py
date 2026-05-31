#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Run all bug-finding CRSs from the registry against a target.

For each bug-finding CRS that has an example compose config, this script
runs: prepare -> build-target -> run --timeout <seconds>, then checks for
seeds and POVs.  All CRSs are always attempted; a per-CRS report with
error level is printed at the end.

Targets can be specified via CLI flags (--fuzz-proj-path / --target-harness)
or via a config YAML file (--config).  The config file can also blacklist
CRSs.  See ``example_sanity_check_config.yaml`` for the format.
"""

import argparse
import csv
import json
import subprocess
import sys
import tempfile
import textwrap
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path

import yaml
from rich.console import Console
from rich.table import Table

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_DIR = REPO_ROOT / "registry"
EXAMPLE_DIR = REPO_ROOT / "example"
DEFAULT_TIMEOUT = 600
DEFAULT_WORK_DIR = REPO_ROOT / ".oss-crs-workdir"


# ---------------------------------------------------------------------------
# Config file
# ---------------------------------------------------------------------------

@dataclass
class TargetSpec:
    """A single target (project + harness) to test against."""
    fuzz_proj_path: Path
    target_harness: str
    target_source_path: Path | None = None
    diff: Path | None = None
    language: str | None = None  # e.g. "c", "jvm"

    @property
    def label(self) -> str:
        return f"{self.fuzz_proj_path.name}/{self.target_harness}"


@dataclass
class SanityCheckConfig:
    """Parsed content of a --config YAML file."""
    skip_crs: list[str] = field(default_factory=list)
    targets: list[TargetSpec] = field(default_factory=list)

    @classmethod
    def from_yaml(cls, path: Path) -> "SanityCheckConfig":
        with open(path) as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            raise ValueError(f"Config file must be a YAML mapping, got {type(data).__name__}")

        skip_crs = data.get("skip_crs", [])
        if isinstance(skip_crs, str):
            skip_crs = [skip_crs]

        raw_targets = data.get("targets", [])
        targets = []
        for i, t in enumerate(raw_targets):
            if not isinstance(t, dict):
                raise ValueError(f"targets[{i}]: expected mapping, got {type(t).__name__}")
            if "fuzz_proj_path" not in t or "target_harness" not in t:
                raise ValueError(f"targets[{i}]: must have 'fuzz_proj_path' and 'target_harness'")
            base = path.parent  # resolve relative paths against config dir
            fpp = Path(t["fuzz_proj_path"]).expanduser()
            if not fpp.is_absolute():
                fpp = (base / fpp).resolve()
            else:
                fpp = fpp.resolve()
            tsp = None
            if "target_source_path" in t and t["target_source_path"]:
                tsp = Path(t["target_source_path"]).expanduser()
                if not tsp.is_absolute():
                    tsp = (base / tsp).resolve()
                else:
                    tsp = tsp.resolve()
            diff = None
            if "diff" in t and t["diff"]:
                diff = Path(t["diff"]).expanduser()
                if not diff.is_absolute():
                    diff = (base / diff).resolve()
                else:
                    diff = diff.resolve()
            targets.append(TargetSpec(
                fuzz_proj_path=fpp,
                target_harness=t["target_harness"],
                target_source_path=tsp,
                diff=diff,
                language=t.get("language"),
            ))

        return cls(skip_crs=skip_crs, targets=targets)


# ---------------------------------------------------------------------------
# Result tracking
# ---------------------------------------------------------------------------

class Severity(IntEnum):
    OK = 0
    WARN = 1
    ERROR = 2


@dataclass
class CRSResult:
    name: str
    target_label: str = ""
    severity: Severity = Severity.OK
    failed_stage: str | None = None  # prepare / build-target / run
    error_msg: str | None = None
    pov_count: int = 0
    seed_count: int = 0
    warnings: list[str] = field(default_factory=list)

    def mark_stage_failure(self, stage: str, msg: str) -> None:
        self.severity = Severity.ERROR
        self.failed_stage = stage
        self.error_msg = msg

    def mark_no_artifacts(self, pov_count: int, seed_count: int) -> None:
        self.pov_count = pov_count
        self.seed_count = seed_count
        if seed_count == 0 and pov_count == 0:
            self.severity = Severity.ERROR
            self.error_msg = "no seeds and no POVs produced"
        elif pov_count == 0:
            if self.severity < Severity.WARN:
                self.severity = Severity.WARN
            self.warnings.append("no POVs produced")


# ---------------------------------------------------------------------------
# Registry / compose helpers
# ---------------------------------------------------------------------------

def find_bug_finding_crs_names() -> list[str]:
    """Return sorted list of CRS names that have type 'bug-finding'."""
    names = []
    for yaml_file in sorted(REGISTRY_DIR.glob("*.yaml")):
        with open(yaml_file) as f:
            data = yaml.safe_load(f)
        if data is None:
            continue
        crs_type = data.get("type", [])
        if isinstance(crs_type, str):
            crs_type = [crs_type]
        if "bug-finding" in crs_type:
            name = data.get("name", yaml_file.stem)
            names.append(name)
    return names


def get_crs_supported_languages(crs_name: str, work_dir: Path) -> list[str] | None:
    """Read supported languages from a CRS's crs.yaml (must be cloned already)."""
    crs_yaml = work_dir / "crs_compose" / "crs_src" / crs_name / "oss-crs" / "crs.yaml"
    if not crs_yaml.exists():
        return None
    with open(crs_yaml) as f:
        data = yaml.safe_load(f)
    if not data:
        return None
    st = data.get("supported_target", {})
    return st.get("language")


def crs_supports_language(crs_name: str, target_language: str | None, work_dir: Path) -> bool:
    """Check if a CRS supports the target's language. True if unknown."""
    if not target_language:
        return True
    langs = get_crs_supported_languages(crs_name, work_dir)
    if langs is None:
        return True  # no restriction declared
    # Normalize: "c" targets should match CRSs that support "c" or "c++"
    return target_language in langs


def has_example_compose(crs_name: str) -> bool:
    return (EXAMPLE_DIR / crs_name / "compose.yaml").exists()


def generate_compose(crs_name: str, output_path: Path) -> None:
    example = EXAMPLE_DIR / crs_name / "compose.yaml"
    if example.exists():
        output_path.write_text(example.read_text())
        return
    compose = textwrap.dedent(f"""\
        run_env: local
        docker_registry: local
        oss_crs_infra:
          cpuset: "0-1"
          memory: "8G"
        {crs_name}:
          cpuset: "2-7"
          memory: "16G"
    """)
    output_path.write_text(compose)


# ---------------------------------------------------------------------------
# Command execution
# ---------------------------------------------------------------------------

def run_cmd(
    cmd: list[str], label: str, log_file: Path | None = None
) -> tuple[bool, int]:
    """Run a command. Returns (success, exit_code)."""
    header = f"\n>>> [{label}] {' '.join(cmd)}\n"
    if log_file is not None:
        with open(log_file, "a") as f:
            f.write(header)
            result = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT)
    else:
        print(header, flush=True)
        result = subprocess.run(cmd)

    if result.returncode != 0:
        msg = f"FAILED [{label}]: exit code {result.returncode}"
        if log_file is not None:
            with open(log_file, "a") as f:
                f.write(msg + "\n")
        else:
            print(msg, file=sys.stderr)
    return result.returncode == 0, result.returncode


def count_files(directory: Path) -> int:
    """Count non-hidden files in *directory* (non-recursive)."""
    if not directory.is_dir():
        return 0
    return sum(1 for p in directory.iterdir() if p.is_file() and not p.name.startswith("."))


def collect_artifact_counts(
    compose_path: Path,
    work_dir: Path,
    fuzz_proj_path: Path,
    target_source_path: Path | None,
    target_harness: str,
    run_id: str,
    build_id: str | None,
    log_file: Path | None = None,
) -> tuple[int, int]:
    """Query oss-crs artifacts and count POVs and seeds.

    Returns (pov_count, seed_count).
    """
    cmd = [
        "uv", "run", "oss-crs", "artifacts",
        "--compose-file", str(compose_path),
        "--work-dir", str(work_dir),
        "--fuzz-proj-path", str(fuzz_proj_path),
        "--target-harness", target_harness,
        "--run-id", run_id,
    ]
    if target_source_path:
        cmd += ["--target-source-path", str(target_source_path)]
    if build_id:
        cmd += ["--build-id", build_id]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        detail = f"artifacts query failed (exit {result.returncode}): {result.stderr.strip()}"
        if log_file:
            with open(log_file, "a") as f:
                f.write(detail + "\n")
        else:
            print(detail, file=sys.stderr)
        return 0, 0

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return 0, 0

    pov_total = 0
    seed_total = 0
    for crs_info in data.get("crs", {}).values():
        pov_dir = crs_info.get("pov")
        seed_dir = crs_info.get("seed")
        if pov_dir:
            pov_total += count_files(Path(pov_dir))
        if seed_dir:
            seed_total += count_files(Path(seed_dir))

    # Also check exchange-level dirs
    exchange = data.get("exchange_dir", {})
    if exchange.get("pov"):
        pov_total += count_files(Path(exchange["pov"]))
    if exchange.get("seed"):
        seed_total += count_files(Path(exchange["seed"]))

    return pov_total, seed_total


# ---------------------------------------------------------------------------
# Per-CRS runner
# ---------------------------------------------------------------------------

def prepare_crs(crs_name: str, work_dir: Path) -> bool:
    """Run oss-crs prepare once for a CRS. Returns True on success."""
    with tempfile.TemporaryDirectory(prefix=f"sanity-prep-{crs_name}-") as tmpdir:
        compose_path = Path(tmpdir) / "compose.yaml"
        generate_compose(crs_name, compose_path)
        ok, _ = run_cmd(
            ["uv", "run", "oss-crs", "prepare",
             "--compose-file", str(compose_path),
             "--work-dir", str(work_dir)],
            f"{crs_name}/prepare",
        )
        return ok


def run_crs(
    crs_name: str,
    *,
    target: TargetSpec,
    work_dir: Path,
    timeout: int,
    run_id: str | None,
    build_id: str | None,
    extra_run_args: list[str],
    log_dir: Path | None = None,
) -> CRSResult:
    """Run build-target -> run for a single CRS against a target."""
    result = CRSResult(name=crs_name, target_label=target.label)
    log_file = log_dir / f"{crs_name}.log" if log_dir else None

    if log_file:
        print(f"  Started {crs_name} @ {target.label}  (log: {log_file})", flush=True)
    else:
        print(f"\n{'='*60}")
        print(f"CRS: {crs_name}  target: {target.label}")
        print(f"{'='*60}")

    crs_work_dir = work_dir / crs_name if log_dir else work_dir

    with tempfile.TemporaryDirectory(prefix=f"sanity-{crs_name}-") as tmpdir:
        compose_path = Path(tmpdir) / "compose.yaml"
        generate_compose(crs_name, compose_path)

        common = ["uv", "run", "oss-crs"]
        compose_args = [
            "--compose-file", str(compose_path),
            "--work-dir", str(crs_work_dir),
        ]
        target_args = ["--fuzz-proj-path", str(target.fuzz_proj_path)]
        if target.target_source_path:
            target_args += ["--target-source-path", str(target.target_source_path)]

        # 1. Build target (prepare already done upfront)
        build_cmd = common + ["build-target"] + compose_args + target_args
        if build_id:
            build_cmd += ["--build-id", build_id]
        if target.diff:
            build_cmd += ["--diff", str(target.diff)]
        ok, rc = run_cmd(build_cmd, f"{crs_name}/build-target", log_file)
        if not ok:
            result.mark_stage_failure("build-target", f"exit code {rc}")
            return result

        # 2. Run
        crs_run_id = run_id or crs_name
        run_cmd_args = (
            common + ["run"] + compose_args + target_args
            + ["--target-harness", target.target_harness]
            + ["--timeout", str(timeout)]
            + ["--run-id", crs_run_id]
        )
        if build_id:
            run_cmd_args += ["--build-id", build_id]
        if target.diff:
            run_cmd_args += ["--diff", str(target.diff)]
        run_cmd_args += extra_run_args
        ok, rc = run_cmd(run_cmd_args, f"{crs_name}/run", log_file)
        if not ok and rc != 124:
            # exit code 124 = timeout (normal for bug-finding)
            # any other non-zero exit code is a real failure
            result.mark_stage_failure("run", f"exit code {rc}")
            return result

        # 3. Check artifacts
        pov_count, seed_count = collect_artifact_counts(
            compose_path, crs_work_dir, target.fuzz_proj_path,
            target.target_source_path, target.target_harness,
            crs_run_id, build_id, log_file,
        )
        result.mark_no_artifacts(pov_count, seed_count)

        # 4. Clean build-target and run images (no artifact removal)
        run_cmd(
            common + ["clean", "build-target", "--yes"] + compose_args + target_args,
            f"{crs_name}/clean-build", log_file,
        )
        run_cmd(
            common + ["clean", "run", "--yes"] + compose_args,
            f"{crs_name}/clean-run", log_file,
        )

    return result


# ---------------------------------------------------------------------------
# Summary & CSV export
# ---------------------------------------------------------------------------

SEVERITY_LABEL = {
    Severity.OK: "OK",
    Severity.WARN: "WARN",
    Severity.ERROR: "ERROR",
}

SEVERITY_STYLE = {
    Severity.OK: "green",
    Severity.WARN: "yellow",
    Severity.ERROR: "red bold",
}


def _result_detail(r: CRSResult) -> str:
    """One-line detail string for a CRS result."""
    if r.failed_stage:
        return f"failed at {r.failed_stage} ({r.error_msg})"
    parts = []
    if r.warnings:
        parts.extend(r.warnings)
    if r.error_msg and not r.failed_stage:
        parts.append(r.error_msg)
    return "; ".join(parts)


def print_summary(results: list[CRSResult], total: int, multi_target: bool) -> None:
    console = Console()
    errors = [r for r in results if r.severity == Severity.ERROR]
    warns = [r for r in results if r.severity == Severity.WARN]
    oks = [r for r in results if r.severity == Severity.OK]

    console.print()
    console.rule("[bold]Sanity-Check Summary[/bold]")
    console.print(
        f"  Total: {total}   "
        f"[green]OK: {len(oks)}[/green]   "
        f"[yellow]WARN: {len(warns)}[/yellow]   "
        f"[red]ERROR: {len(errors)}[/red]"
    )
    console.print()

    table = Table(show_lines=False, pad_edge=False, expand=True)
    table.add_column("Status", width=7, justify="center")
    table.add_column("CRS")
    if multi_target:
        table.add_column("Target")
    table.add_column("Seeds", justify="right", width=6)
    table.add_column("POVs", justify="right", width=6)
    table.add_column("Detail")

    for r in results:
        style = SEVERITY_STYLE[r.severity]
        label = SEVERITY_LABEL[r.severity]
        seeds = str(r.seed_count) if not r.failed_stage else "-"
        povs = str(r.pov_count) if not r.failed_stage else "-"
        row: list[str] = [f"[{style}]{label}[/{style}]", r.name]
        if multi_target:
            row.append(r.target_label)
        row += [seeds, povs, _result_detail(r)]
        table.add_row(
            *row,
            style=style if r.severity == Severity.ERROR else None,
        )

    console.print(table)

    if errors:
        console.print()
        stage_counts: dict[str, list[str]] = {}
        for r in errors:
            key = r.failed_stage or "artifacts"
            stage_counts.setdefault(key, []).append(r.name)
        for stage, names in stage_counts.items():
            console.print(f"  [red]Errors in {stage}:[/red] {', '.join(names)}")

    console.print()


CSV_HEADER = ["crs", "target", "status", "failed_stage", "seeds", "povs", "detail"]


def _csv_row(r: CRSResult) -> list[str | int]:
    return [
        r.name,
        r.target_label,
        SEVERITY_LABEL[r.severity],
        r.failed_stage or "",
        r.seed_count if not r.failed_stage else "",
        r.pov_count if not r.failed_stage else "",
        _result_detail(r),
    ]


def load_completed_jobs(csv_path: Path) -> tuple[set[tuple[str, str]], list[CRSResult]]:
    """Load already-completed (crs, target) pairs and results from an existing CSV.

    Returns (completed_keys, results).
    """
    completed: set[tuple[str, str]] = set()
    results: list[CRSResult] = []
    if not csv_path.exists():
        return completed, results
    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            crs = row.get("crs", "")
            target = row.get("target", "")
            if not crs:
                continue
            completed.add((crs, target))
            # Reconstruct CRSResult for the summary
            status = row.get("status", "OK")
            sev = {"OK": Severity.OK, "WARN": Severity.WARN, "ERROR": Severity.ERROR}.get(
                status, Severity.OK
            )
            r = CRSResult(
                name=crs,
                target_label=target,
                severity=sev,
                failed_stage=row.get("failed_stage") or None,
                error_msg=row.get("detail") or None,
                pov_count=int(row["povs"]) if row.get("povs", "").isdigit() else 0,
                seed_count=int(row["seeds"]) if row.get("seeds", "").isdigit() else 0,
            )
            results.append(r)
    return completed, results


class CSVStreamer:
    """Append CSV rows as results arrive so partial data survives a kill."""

    def __init__(self, path: Path, append: bool = False):
        self.path = path
        if append and path.exists():
            self._file = open(path, "a", newline="")
        else:
            self._file = open(path, "w", newline="")
            writer = csv.writer(self._file)
            writer.writerow(CSV_HEADER)
            self._file.flush()
        self._writer = csv.writer(self._file)

    def write(self, r: CRSResult) -> None:
        self._writer.writerow(_csv_row(r))
        self._file.flush()

    def close(self) -> None:
        self._file.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run all bug-finding CRSs against a target",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              # Run all bug-finding CRSs against a single target
              uv run sanity-check --fuzz-proj-path /path/to/project \\
                  --target-harness fuzz_parse_buffer

              # Use a config file with multiple targets and CRS blacklist
              uv run sanity-check --config sanity-check.yaml

              # Run specific CRSs only
              uv run sanity-check --fuzz-proj-path /path/to/project \\
                  --target-harness fuzz_parse_buffer \\
                  --crs crs-libfuzzer --crs fuzzing-brain

              # List available bug-finding CRSs
              uv run sanity-check --list

            Config file format (YAML):
              skip_crs:
                - crs-shellphish-grammar
                - crs-vincent

              targets:
                - fuzz_proj_path: /path/to/project-a
                  target_harness: fuzz_parse_buffer
                - fuzz_proj_path: ../relative/project-b
                  target_harness: fuzz_main
                  target_source_path: ../source-b
        """),
    )
    parser.add_argument(
        "--list", action="store_true",
        help="List all bug-finding CRSs and exit",
    )
    parser.add_argument(
        "--config", type=Path, default=None,
        help="Path to a config YAML file listing targets and CRS blacklist",
    )
    parser.add_argument(
        "--fuzz-proj-path", type=Path,
        help="Path to target project directory",
    )
    parser.add_argument(
        "--target-source-path", type=Path, default=None,
        help="Optional local source override path",
    )
    parser.add_argument(
        "--target-harness", type=str,
        help="Target harness to run against",
    )
    parser.add_argument(
        "--timeout", type=int, default=DEFAULT_TIMEOUT,
        help=f"Timeout in seconds per CRS run (default: {DEFAULT_TIMEOUT})",
    )
    parser.add_argument(
        "--work-dir", type=Path, default=DEFAULT_WORK_DIR,
        help="Working directory for CRS operations",
    )
    parser.add_argument(
        "--crs", action="append", dest="crs_names", default=None,
        help="Run only specific CRS(s). Can be repeated. If omitted, runs all bug-finding CRSs.",
    )
    parser.add_argument(
        "--run-id", type=str, default=None,
        help="Run ID prefix (default: CRS name is used as run-id)",
    )
    parser.add_argument(
        "--build-id", type=str, default=None,
        help="Build ID to use (shared across all CRS runs)",
    )
    parser.add_argument(
        "--early-exit", action="store_true", default=False,
        help="Stop each CRS run when first artifact is discovered",
    )
    parser.add_argument(
        "--parallel", type=int, default=1, metavar="N",
        help="Run N CRSs in parallel (default: 1 = sequential). "
             "Each parallel CRS gets its own work-dir and log file.",
    )
    parser.add_argument(
        "--csv", type=Path, default=None, metavar="FILE",
        help="Export results to a CSV file",
    )

    args = parser.parse_args()

    all_crs = find_bug_finding_crs_names()

    if args.list:
        print(f"Bug-finding CRSs in registry ({len(all_crs)}):\n")
        for name in all_crs:
            marker = " [example]" if has_example_compose(name) else " [generated]"
            print(f"  {name}{marker}")
        return 0

    # --- Resolve targets ---------------------------------------------------
    config: SanityCheckConfig | None = None

    if args.config:
        if args.fuzz_proj_path or args.target_harness:
            parser.error("--config cannot be combined with --fuzz-proj-path / --target-harness")
        config = SanityCheckConfig.from_yaml(args.config.resolve())
        if not config.targets:
            parser.error("Config file has no targets defined")
        targets = config.targets
    elif args.fuzz_proj_path and args.target_harness:
        targets = [TargetSpec(
            fuzz_proj_path=args.fuzz_proj_path.resolve(),
            target_harness=args.target_harness,
            target_source_path=args.target_source_path.resolve() if args.target_source_path else None,
        )]
    else:
        parser.error("Either --config or both --fuzz-proj-path and --target-harness are required")

    # --- Resolve CRS list --------------------------------------------------
    skip_set: set[str] = set()
    if config and config.skip_crs:
        skip_set = set(config.skip_crs)
        unknown_skip = skip_set - set(all_crs)
        if unknown_skip:
            Console().print(
                f"[yellow]Warning:[/yellow] skip_crs contains unknown CRS(s): "
                f"{', '.join(sorted(unknown_skip))}"
            )

    if args.crs_names:
        unknown = set(args.crs_names) - set(all_crs)
        if unknown:
            parser.error(f"Unknown CRS(s): {', '.join(sorted(unknown))}")
        crs_to_run = [n for n in args.crs_names if n not in skip_set]
    else:
        crs_to_run = [n for n in all_crs if n not in skip_set]

    if not crs_to_run:
        parser.error("No CRSs to run (all blacklisted or filtered out)")

    extra_run_args = []
    if args.early_exit:
        extra_run_args.append("--early-exit")

    multi_target = len(targets) > 1
    parallel = max(1, args.parallel)

    # Build (crs, target) job list
    jobs: list[tuple[str, TargetSpec]] = [
        (crs_name, tgt)
        for tgt in targets
        for crs_name in crs_to_run
    ]

    print(f"Running {len(crs_to_run)} CRS(s) x {len(targets)} target(s) = {len(jobs)} job(s)" +
          (f" ({parallel} in parallel)" if parallel > 1 else ""))
    for tgt in targets:
        print(f"  Target: {tgt.label}")
    if skip_set:
        print(f"  Skipping: {', '.join(sorted(skip_set))}")
    print(f"  Timeout: {args.timeout}s per run")

    # --- Load completed jobs (continuation) ---------------------------------
    completed_keys: set[tuple[str, str]] = set()
    results: list[CRSResult] = []
    csv_streamer: CSVStreamer | None = None

    if args.csv:
        csv_path = args.csv.resolve()
        completed_keys, prior_results = load_completed_jobs(csv_path)
        if completed_keys:
            results.extend(prior_results)
            print(f"  Resuming: {len(completed_keys)} job(s) already completed in {csv_path.name}")
            csv_streamer = CSVStreamer(csv_path, append=True)
        else:
            csv_streamer = CSVStreamer(csv_path, append=False)
        Console().print(f"Streaming results to [bold]{csv_path}[/bold]")

    remaining_jobs = [
        (crs_name, tgt) for crs_name, tgt in jobs
        if (crs_name, tgt.label) not in completed_keys
    ]
    if len(remaining_jobs) < len(jobs):
        print(f"  Running {len(remaining_jobs)} remaining job(s) (skipping {len(jobs) - len(remaining_jobs)})")

    # --- Helpers -----------------------------------------------------------
    def _on_result(r: CRSResult) -> None:
        results.append(r)
        if csv_streamer:
            csv_streamer.write(r)
        label = SEVERITY_LABEL[r.severity]
        print(f"  Finished {r.name} @ {r.target_label}: {label}", flush=True)

    def _run_job(crs_name: str, tgt: TargetSpec, log_dir: Path | None = None) -> CRSResult:
        return run_crs(
            crs_name,
            target=tgt,
            work_dir=args.work_dir.resolve(),
            timeout=args.timeout,
            run_id=args.run_id,
            build_id=args.build_id,
            extra_run_args=extra_run_args,
            log_dir=log_dir,
        )

    # --- Prepare CRSs (once per CRS) --------------------------------------
    crs_needing_prepare = sorted({name for name, _ in remaining_jobs})
    if crs_needing_prepare:
        print(f"\nPreparing {len(crs_needing_prepare)} CRS(s)...")
        failed_prepare: list[str] = []
        for crs_name in crs_needing_prepare:
            if not prepare_crs(crs_name, args.work_dir.resolve()):
                failed_prepare.append(crs_name)
        if failed_prepare:
            for crs_name in failed_prepare:
                for job_crs, tgt in list(remaining_jobs):
                    if job_crs == crs_name:
                        r = CRSResult(name=crs_name, target_label=tgt.label)
                        r.mark_stage_failure("prepare", "exit code 1")
                        _on_result(r)
            remaining_jobs = [
                (c, t) for c, t in remaining_jobs if c not in failed_prepare
            ]

    # --- Filter by language compatibility -----------------------------------
    work_dir_resolved = args.work_dir.resolve()
    before_filter = len(remaining_jobs)
    remaining_jobs = [
        (crs_name, tgt) for crs_name, tgt in remaining_jobs
        if crs_supports_language(crs_name, tgt.language, work_dir_resolved)
    ]
    skipped_lang = before_filter - len(remaining_jobs)
    if skipped_lang:
        print(f"  Skipped {skipped_lang} job(s) due to language mismatch")

    # --- Execute -----------------------------------------------------------
    try:
        if parallel > 1:
            par_log_dir = args.work_dir.resolve() / "logs"
            par_log_dir.mkdir(parents=True, exist_ok=True)
            print(f"  Logs: {par_log_dir}/")

            with ThreadPoolExecutor(max_workers=parallel) as pool:
                futures = {
                    pool.submit(_run_job, crs_name, tgt, par_log_dir): (crs_name, tgt)
                    for crs_name, tgt in remaining_jobs
                }
                for future in as_completed(futures):
                    _on_result(future.result())
        else:
            for crs_name, tgt in remaining_jobs:
                r = _run_job(crs_name, tgt)
                _on_result(r)
    finally:
        if csv_streamer:
            csv_streamer.close()

    # Preserve input order for the summary
    job_order = {(crs, tgt.label): i for i, (crs, tgt) in enumerate(jobs)}
    results.sort(key=lambda r: job_order.get((r.name, r.target_label), 0))

    print_summary(results, len(jobs), multi_target)

    has_errors = any(r.severity == Severity.ERROR for r in results)
    return 1 if has_errors else 0


if __name__ == "__main__":
    sys.exit(main())

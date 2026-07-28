# SPDX-License-Identifier: MIT
import os
import sys
import json
import time
import argparse
from pathlib import Path
from ..base import DataType, SourceType, CRSUtils
from ..bug_candidate import DEFAULT_CLAIM_TTL
from ..local import LocalCRSUtils
from ..common import get_run_env_type, EnvType


def init_crs_utils() -> CRSUtils:
    env_type = get_run_env_type()
    if env_type == EnvType.LOCAL:
        return LocalCRSUtils()
    else:
        raise NotImplementedError(
            f"CRSUtils not implemented for run environment: {env_type}"
        )


class DaemonContext:
    def __init__(self, log_path: str | None = None):
        self.log_path = log_path
        self.log_file = None

    def __enter__(self):
        pid = os.fork()
        if pid > 0:
            # Parent exits immediately
            print(f"Started daemon with PID: {pid}")
            os._exit(0)  # Use os._exit to avoid cleanup in parent

        # Child continues as daemon
        os.setsid()

        # Redirect stdout/stderr to log file or /dev/null
        if self.log_path:
            self.log_file = open(self.log_path, "a", buffering=1)
        else:
            self.log_file = open(os.devnull, "w")

        sys.stdout = self.log_file
        sys.stderr = self.log_file
        sys.stdin = open(os.devnull, "r")

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.log_file:
            self.log_file.close()
        return False


def register_submit_dir(crs_utils, args):
    with DaemonContext(log_path=args.log):
        crs_utils.register_submit_dir(args.type, args.path)


def register_fetch_dir(crs_utils, args):
    with DaemonContext(log_path=args.log):
        crs_utils.register_fetch_dir(args.type, args.path)


def get_service_domain(crs_utils, args):
    domain = crs_utils.get_service_domain(args.service_name)
    print(domain)


def _parse_kv(items, parser):
    """Parse repeatable ``KEY=VALUE`` args into a dict (or None)."""
    if not items:
        return None
    out = {}
    for item in items:
        if "=" not in item:
            parser.error(f"--property must be KEY=VALUE, got {item!r}")
        k, v = item.split("=", 1)
        out[k] = v
    return out


def _register_bug_candidate(subparsers, crs_utils):
    """Register the `bug-candidate` command group (shared-DB interface)."""
    bc = subparsers.add_parser(
        "bug-candidate",
        help="Manage bug-candidates (add/list/get/claim/status/merge/watch)",
    )
    bc_sub = bc.add_subparsers(dest="bc_command", metavar="SUBCOMMAND")

    # Shared --agent option (actor identity for events).
    actor = argparse.ArgumentParser(add_help=False)
    actor.add_argument(
        "--agent",
        default=None,
        help="Actor id recorded on events (default: $OSS_CRS_AGENT_ID or CRS name)",
    )

    # add — flag-based (libCRS builds the SARIF); or --raw to import a SARIF file.
    p_add = bc_sub.add_parser(
        "add",
        parents=[actor],
        help="Add a bug-candidate from flags (libCRS builds the SARIF), or --raw a SARIF file",
    )
    p_add.add_argument(
        "--raw", type=Path, default=None, help="Import a SARIF 2.1.0 file verbatim"
    )
    p_add.add_argument(
        "--rule",
        default=None,
        help="Bug class (SARIF ruleId), e.g. heap-buffer-overflow",
    )
    p_add.add_argument(
        "--file", default=None, help="Source file (path relative to the source tree)"
    )
    p_add.add_argument("--line", type=int, default=None, help="Start line")
    p_add.add_argument(
        "--message", default=None, help="Why it's suspicious (SARIF message)"
    )
    p_add.add_argument(
        "--severity", default="warning", help="SARIF level: error/warning/note"
    )
    p_add.add_argument("--function", default=None, help="Enclosing function name")
    p_add.add_argument("--end-line", type=int, default=None, dest="end_line")
    p_add.add_argument("--column", type=int, default=None)
    p_add.add_argument("--end-column", type=int, default=None, dest="end_column")
    p_add.add_argument("--uri-base-id", default=None, dest="uri_base_id")
    p_add.add_argument(
        "--version", default=None, help="Program version (SARIF revisionId)"
    )
    p_add.add_argument(
        "--repo", default=None, dest="repository_uri", help="Repository URI"
    )
    p_add.add_argument("--branch", default=None)
    p_add.add_argument(
        "--id",
        default=None,
        dest="candidate_id",
        help="Correlation id: append a location to this existing candidate, or set a new one",
    )
    p_add.add_argument("--harness", default=None, help="Associated harness name")
    p_add.add_argument("--pov-ref", default=None, dest="pov_ref", help="PoV reference")
    p_add.add_argument(
        "--property",
        action="append",
        default=None,
        dest="property",
        metavar="KEY=VALUE",
        help="Arbitrary indexed metadata (repeatable), e.g. --property subagent=pov-gen",
    )

    def _add(args):
        if args.raw is None and not args.file and not args.candidate_id:
            p_add.error(
                "provide --file (with --rule/--line/...), or --raw <sarif.json>"
            )
        ids = crs_utils.bug_candidate_add(
            raw=args.raw,
            rule=args.rule,
            file=args.file,
            line=args.line,
            message=args.message,
            severity=args.severity,
            function=args.function,
            end_line=args.end_line,
            column=args.column,
            end_column=args.end_column,
            uri_base_id=args.uri_base_id,
            version=args.version,
            repository_uri=args.repository_uri,
            branch=args.branch,
            candidate_id=args.candidate_id,
            harness=args.harness,
            pov_ref=args.pov_ref,
            properties=_parse_kv(args.property, p_add),
            agent=args.agent,
        )
        print("\n".join(ids))

    p_add.set_defaults(func=_add)

    # mark-explored
    p_explored = bc_sub.add_parser(
        "mark-explored",
        parents=[actor],
        help="Record that this CRS tried exploring a candidate (CRS name is automatic)",
    )
    p_explored.add_argument("candidate_id")
    p_explored.set_defaults(
        func=lambda args: crs_utils.bug_candidate_mark_explored(
            args.candidate_id, agent=args.agent
        )
    )

    # mark-pov
    p_pov = bc_sub.add_parser(
        "mark-pov",
        parents=[actor],
        help="Record that a PoV was generated from a candidate",
    )
    p_pov.add_argument("candidate_id")
    p_pov.add_argument(
        "--pov", required=True, dest="pov_ref", help="The PoV's content hash"
    )
    p_pov.set_defaults(
        func=lambda args: crs_utils.bug_candidate_mark_pov(
            args.candidate_id, args.pov_ref, agent=args.agent
        )
    )

    # claim
    p_claim = bc_sub.add_parser("claim", parents=[actor], help="Claim a candidate")
    p_claim.add_argument("candidate_id")
    p_claim.add_argument("--owner", default=None, help="Claim owner (default: --agent)")
    p_claim.add_argument(
        "--ttl", type=float, default=DEFAULT_CLAIM_TTL, help="Lease TTL in seconds"
    )

    def _claim(args):
        owner = args.owner or args.agent
        ok = crs_utils.bug_candidate_claim(args.candidate_id, owner, args.ttl)
        result = {"claimed": ok, "candidate_id": args.candidate_id}
        if ok:
            got = crs_utils.bug_candidate_get(args.candidate_id)
            if got:
                result["claim"] = got["claim"]
        print(json.dumps(result, indent=2))
        sys.exit(0 if ok else 1)

    p_claim.set_defaults(func=_claim)

    # release
    p_rel = bc_sub.add_parser("release", parents=[actor], help="Release a claim")
    p_rel.add_argument("candidate_id")
    p_rel.add_argument("--owner", default=None)
    p_rel.set_defaults(
        func=lambda args: crs_utils.bug_candidate_release(
            args.candidate_id, args.owner or args.agent
        )
    )

    # merge
    p_merge = bc_sub.add_parser(
        "merge", parents=[actor], help="Alias a candidate into another (dedup)"
    )
    p_merge.add_argument("candidate_id")
    p_merge.add_argument("--into", required=True, dest="into_candidate_id")
    p_merge.set_defaults(
        func=lambda args: crs_utils.bug_candidate_merge(
            args.candidate_id, args.into_candidate_id, agent=args.agent
        )
    )

    # note
    p_note = bc_sub.add_parser("note", parents=[actor], help="Append a note")
    p_note.add_argument("candidate_id")
    p_note.add_argument("text")
    p_note.set_defaults(
        func=lambda args: crs_utils.bug_candidate_note(
            args.candidate_id, args.text, agent=args.agent
        )
    )

    # list
    p_list = bc_sub.add_parser("list", help="List candidates (JSON array)")
    p_list.add_argument(
        "--has-pov", action="store_true", dest="has_pov", help="Only with a PoV"
    )
    p_list.add_argument(
        "--no-pov", action="store_true", dest="no_pov", help="Only without a PoV"
    )
    p_list.add_argument(
        "--explored-by",
        default=None,
        dest="explored_by",
        metavar="CRS",
        help='CRS that explored it ("me" = this CRS)',
    )
    p_list.add_argument(
        "--not-explored-by",
        default=None,
        dest="not_explored_by",
        metavar="CRS",
        help='Skip ones this CRS explored ("me" = this CRS) — i.e. what I haven\'t tried',
    )
    p_list.add_argument("--claimed", action="store_true", help="Only actively claimed")
    p_list.add_argument("--unclaimed", action="store_true", help="Only unclaimed")
    p_list.add_argument(
        "--scope",
        choices=["harness", "project", "all"],
        default="harness",
        help="Widen the read: 'harness' (default) = this harness; 'project' = all "
        "harnesses of this target; 'all' = every project. Cross-harness rows are "
        'flagged "foreign": true.',
    )
    p_list.add_argument(
        "--version", default=None, help="Only with a location at this version"
    )
    p_list.add_argument(
        "--property",
        action="append",
        default=None,
        dest="property",
        metavar="KEY=VALUE",
        help="Only candidates with this metadata (repeatable, ANDed), e.g. --property subagent=pov-gen",
    )

    def _list(args):
        claimed = True if args.claimed else (False if args.unclaimed else None)
        has_pov = True if args.has_pov else (False if args.no_pov else None)
        rows = crs_utils.bug_candidate_list(
            has_pov=has_pov,
            explored_by=args.explored_by,
            not_explored_by=args.not_explored_by,
            claimed=claimed,
            version=args.version,
            properties=_parse_kv(args.property, p_list),
            scope=args.scope,
        )
        print(json.dumps(rows, indent=2))

    p_list.set_defaults(func=_list)

    # get
    p_get = bc_sub.add_parser("get", help="Get one candidate (JSON object)")
    p_get.add_argument("candidate_id")
    p_get.add_argument(
        "--scope",
        choices=["harness", "project", "all"],
        default="harness",
        help="Widen the read to another harness ('project') or project ('all')",
    )
    p_get.set_defaults(
        func=lambda args: print(
            json.dumps(
                crs_utils.bug_candidate_get(args.candidate_id, scope=args.scope),
                indent=2,
            )
        )
    )

    # sync
    p_sync = bc_sub.add_parser(
        "sync", help="Fold newly-fetched events into the local view"
    )
    p_sync.add_argument(
        "--daemon", action="store_true", help="Run a background ingest poll loop"
    )
    p_sync.add_argument(
        "--poll-interval", type=float, default=5.0, dest="poll_interval"
    )
    p_sync.add_argument("--log", type=Path, default=None)

    def _sync(args):
        if args.daemon:
            with DaemonContext(log_path=args.log):
                while True:
                    crs_utils.bug_candidate_sync()
                    time.sleep(args.poll_interval)
        else:
            print(crs_utils.bug_candidate_sync())

    p_sync.set_defaults(func=_sync)

    # watch
    p_watch = bc_sub.add_parser(
        "watch", help="Stream candidate update events as JSON Lines"
    )
    p_watch.add_argument("--candidate", default=None, help="Filter to one candidate id")
    p_watch.add_argument("--since", type=int, default=0, help="Resume after this seq")
    p_watch.add_argument(
        "--type",
        action="append",
        default=None,
        help="Filter by event type (repeatable)",
    )
    p_watch.add_argument(
        "--follow", action="store_true", help="Keep streaming (tail -f)"
    )
    p_watch.add_argument(
        "--poll-interval", type=float, default=5.0, dest="poll_interval"
    )
    p_watch.add_argument("--timeout", type=float, default=None)

    def _watch(args):
        for ev in crs_utils.bug_candidate_watch(
            args.since,
            candidate_id=args.candidate,
            types=args.type,
            follow=args.follow,
            poll_interval=args.poll_interval,
            timeout=args.timeout,
        ):
            print(json.dumps(ev), flush=True)

    p_watch.set_defaults(func=_watch)

    def _bc_default(_args):
        bc.print_help()

    bc.set_defaults(func=_bc_default)


def main():
    crs_utils = init_crs_utils()
    parser = argparse.ArgumentParser(
        prog="libCRS", description="libCRS - CRS utilities"
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # =========================================================================
    # Build output commands
    # =========================================================================

    # submit-build-output command
    submit_build_parser = subparsers.add_parser(
        "submit-build-output", help="Submit build output from src_path to dst_path"
    )
    submit_build_parser.add_argument("src_path", help="Source path in docker container")
    submit_build_parser.add_argument(
        "dst_path", help="Destination path on build output file system"
    )
    submit_build_parser.add_argument(
        "--rebuild-id",
        type=int,
        default=None,
        help="Rebuild ID (required during run phase, forbidden during build-target phase)",
    )
    submit_build_parser.set_defaults(
        func=lambda args: crs_utils.submit_build_output(
            args.src_path, Path(args.dst_path), rebuild_id=args.rebuild_id
        )
    )

    # skip-build-output command
    skip_parser = subparsers.add_parser(
        "skip-build-output",
        help="Skip build output for dst_path on build output file system",
    )
    skip_parser.add_argument(
        "dst_path", help="Destination path on build output file system"
    )
    skip_parser.set_defaults(
        func=lambda args: crs_utils.skip_build_output(args.dst_path)
    )

    # download-build-output command
    download_build_parser = subparsers.add_parser(
        "download-build-output",
        help="Download build output to dst_path. Uses rebuild artifacts if --rebuild-id provided, otherwise build-target artifacts.",
    )
    download_build_parser.add_argument(
        "src_path", help="Source path within build output directory"
    )
    download_build_parser.add_argument(
        "dst_path", help="Destination path in docker container"
    )
    download_build_parser.add_argument(
        "--rebuild-id",
        type=int,
        default=None,
        help="Rebuild ID to fetch sidecar artifacts (omit for build-target artifacts)",
    )
    download_build_parser.set_defaults(
        func=lambda args: crs_utils.download_build_output(
            args.src_path, Path(args.dst_path), rebuild_id=args.rebuild_id
        )
    )

    # download-source command
    download_source_parser = subparsers.add_parser(
        "download-source",
        help="Download source tree from mount path to destination",
    )
    download_source_parser.add_argument(
        "type",
        type=SourceType,
        choices=list(SourceType),
        metavar="TYPE",
        help="Source type: fuzz-proj, target-source",
    )
    download_source_parser.add_argument(
        "dst_path",
        type=Path,
        help="Destination path in docker container",
    )
    download_source_parser.set_defaults(
        func=lambda args: crs_utils.download_source(args.type, args.dst_path)
    )

    # =========================================================================
    # Data registration commands (auto-sync directories)
    # =========================================================================

    # Valid types for submit vs fetch commands. `report` is submit-only via the
    # one-shot `submit` command (bundled into a tarball); not valid for
    # `register-submit-dir`. bug-candidate is intentionally absent: it is managed
    # through the `bug-candidate` command group, not raw file drops.
    submit_types = [DataType.POV, DataType.SEED, DataType.REPORT, DataType.PATCH]
    register_submit_types = [t for t in submit_types if t != DataType.REPORT]
    fetch_types = list(DataType)

    # register-submit-dir command (auto-submit data to oss-crs-infra)
    register_submit_dir_parser = subparsers.add_parser(
        "register-submit-dir",
        help="Register a directory for automatic submission to oss-crs-infra",
    )
    register_submit_dir_parser.add_argument(
        "type",
        type=DataType,
        choices=register_submit_types,
        metavar="TYPE",
        help="Type of data: pov, seed, bug-candidate, patch",
    )
    register_submit_dir_parser.add_argument(
        "path", type=Path, help="Directory path to register"
    )
    register_submit_dir_parser.add_argument(
        "--log",
        type=Path,
        default=None,
        help="Log file path for the registered directory",
    )
    register_submit_dir_parser.set_defaults(
        func=lambda args: register_submit_dir(crs_utils, args)
    )

    # register-shared-dir command (share a directory between containers in a CRS)
    register_shared_dir_parser = subparsers.add_parser(
        "register-shared-dir",
        help="Register a shared directory for sharing data between containers in a CRS",
    )
    register_shared_dir_parser.add_argument(
        "local_path", type=Path, help="Local directory path inside the container"
    )
    register_shared_dir_parser.add_argument(
        "shared_path",
        type=str,
        help="Path on the shared filesystem accessible by all containers in the CRS",
    )
    register_shared_dir_parser.set_defaults(
        func=lambda args: crs_utils.register_shared_dir(
            args.local_path, args.shared_path
        )
    )

    # register-log-dir command (symlink a local directory into LOG_DIR)
    register_log_dir_parser = subparsers.add_parser(
        "register-log-dir",
        help="Register a local directory for persisting CRS agent/internal logs",
    )
    register_log_dir_parser.add_argument(
        "local_path",
        type=Path,
        help="Local directory path inside the container to symlink into LOG_DIR",
    )
    register_log_dir_parser.set_defaults(
        func=lambda args: crs_utils.register_log_dir(args.local_path)
    )

    # register-fetch-dir command (auto-fetch shared data from other CRS)
    register_fetch_dir_parser = subparsers.add_parser(
        "register-fetch-dir",
        help="Register a directory to automatically fetch shared data from other CRS",
    )
    register_fetch_dir_parser.add_argument(
        "type",
        type=DataType,
        choices=fetch_types,
        metavar="TYPE",
        help="Type of data: pov, seed, bug-candidate, report, patch, diff",
    )
    register_fetch_dir_parser.add_argument(
        "path", type=Path, help="Directory path to receive shared data"
    )
    register_fetch_dir_parser.add_argument(
        "--log",
        type=Path,
        default=None,
        help="Log file path for the registered directory",
    )
    register_fetch_dir_parser.set_defaults(
        func=lambda args: register_fetch_dir(crs_utils, args)
    )

    # =========================================================================
    # Manual data operations
    # =========================================================================

    # submit command (manually submit a single file)
    submit_parser = subparsers.add_parser(
        "submit",
        help="Submit a single file to oss-crs-infra",
    )
    submit_parser.add_argument(
        "type",
        type=DataType,
        choices=submit_types,
        metavar="TYPE",
        help="Type of data: pov, seed, bug-candidate, report, patch",
    )
    submit_parser.add_argument(
        "path",
        type=Path,
        help="File to submit (for `report`, a file or directory bundled into a tarball)",
    )
    submit_parser.set_defaults(func=lambda args: crs_utils.submit(args.type, args.path))

    # submit-harness command (harness-gen output: fuzz-proj dir + optional target source dir)
    submit_harness_parser = subparsers.add_parser(
        "submit-harness",
        help="Submit a generated harness project (fuzz-proj dir + optional target source dir)",
    )
    submit_harness_parser.add_argument(
        "--fuzz-proj-dir",
        type=Path,
        required=True,
        dest="fuzz_proj_dir",
        metavar="DIR",
        help="OSS-Fuzz project directory (Dockerfile, build.sh, harness source)",
    )
    submit_harness_parser.add_argument(
        "--target-source-dir",
        type=Path,
        default=None,
        dest="target_source_dir",
        metavar="DIR",
        help="Modified upstream target source tree (optional)",
    )
    submit_harness_parser.add_argument(
        "--name",
        type=str,
        default=None,
        help="Subdir name under harnesses (defaults to fuzz-proj dir basename)",
    )
    submit_harness_parser.set_defaults(
        func=lambda args: crs_utils.submit_harness(
            args.fuzz_proj_dir, args.target_source_dir, args.name
        )
    )

    # fetch command (manually fetch shared data)
    fetch_parser = subparsers.add_parser(
        "fetch",
        help="Fetch shared data from other CRS to a directory",
    )
    fetch_parser.add_argument(
        "type",
        type=DataType,
        choices=fetch_types,
        metavar="TYPE",
        help="Type of data: pov, seed, bug-candidate, report, patch, diff",
    )
    fetch_parser.add_argument("path", type=Path, help="Output directory path")
    fetch_parser.set_defaults(
        func=lambda args: print("\n".join(crs_utils.fetch(args.type, args.path)))
    )

    # =========================================================================
    # Patch build commands
    # =========================================================================

    # apply-patch-build command
    apply_patch_build_parser = subparsers.add_parser(
        "apply-patch-build",
        help="Apply a target-source patch to the snapshot image and rebuild",
    )
    apply_patch_build_parser.add_argument(
        "patch_path", type=Path, help="Path to the unified diff file"
    )
    apply_patch_build_parser.add_argument(
        "response_dir", type=Path, help="Directory to receive build results"
    )
    apply_patch_build_parser.add_argument(
        "--builder",
        type=str,
        default=None,
        help="Builder sidecar module name (defaults to BUILDER_MODULE env var)",
    )
    apply_patch_build_parser.add_argument(
        "--builder-name",
        type=str,
        default=None,
        help="Builder config name for image resolution (e.g. 'coverage-build'). Defaults to --builder value.",
    )
    apply_patch_build_parser.add_argument(
        "--rebuild-id",
        type=int,
        default=None,
        help="Rebuild ID (auto-increments if omitted, overwrites artifacts if provided)",
    )

    def _apply_patch_build(args):
        exit_code = crs_utils.apply_patch_build(
            args.patch_path,
            args.response_dir,
            builder=args.builder,
            builder_name=args.builder_name,
            rebuild_id=args.rebuild_id,
        )
        sys.exit(exit_code)

    apply_patch_build_parser.set_defaults(func=_apply_patch_build)

    # build-project command (harness-gen: rebuild from a modified fuzz-proj and/or target source)
    build_project_parser = subparsers.add_parser(
        "build-project",
        help="Rebuild the project image from a modified fuzz-proj and/or target source dir",
    )
    build_project_parser.add_argument(
        "--response-dir",
        type=Path,
        required=True,
        dest="response_dir",
        metavar="DIR",
        help="Directory to receive build results",
    )
    build_project_parser.add_argument(
        "--fuzz-proj-dir",
        type=Path,
        default=None,
        dest="fuzz_proj_dir",
        metavar="DIR",
        help="Modified OSS-Fuzz project directory (diffed against the fuzz-proj base)",
    )
    build_project_parser.add_argument(
        "--target-source-dir",
        type=Path,
        default=None,
        dest="target_source_dir",
        metavar="DIR",
        help="Modified target source directory (diffed against the target-source base)",
    )
    build_project_parser.add_argument(
        "--builder",
        type=str,
        default=None,
        help="Builder sidecar module name (defaults to BUILDER_MODULE env var)",
    )
    build_project_parser.add_argument(
        "--builder-name",
        type=str,
        default=None,
        help="Builder config name for image resolution (e.g. 'coverage-build'). Defaults to --builder value.",
    )
    build_project_parser.add_argument(
        "--rebuild-id",
        type=int,
        default=None,
        help="Rebuild ID (auto-increments if omitted, overwrites artifacts if provided)",
    )

    def _build_project(args):
        if not args.fuzz_proj_dir and not args.target_source_dir:
            build_project_parser.error(
                "At least one of --fuzz-proj-dir or --target-source-dir must be provided"
            )
        exit_code = crs_utils.build_project(
            args.response_dir,
            fuzz_proj_dir=args.fuzz_proj_dir,
            target_source_dir=args.target_source_dir,
            builder=args.builder,
            builder_name=args.builder_name,
            rebuild_id=args.rebuild_id,
        )
        sys.exit(exit_code)

    build_project_parser.set_defaults(func=_build_project)

    # run-pov command
    run_pov_parser = subparsers.add_parser(
        "run-pov",
        help="Run a POV binary against a specific rebuild's output",
    )
    run_pov_parser.add_argument(
        "pov_path", type=Path, help="Path to the POV binary file"
    )
    run_pov_parser.add_argument(
        "response_dir", type=Path, help="Directory to receive POV results"
    )
    run_pov_parser.add_argument(
        "--harness",
        type=str,
        required=True,
        help="Harness binary name in /out/",
    )
    run_pov_parser.add_argument(
        "--rebuild-id",
        type=str,
        default=None,
        help="Rebuild ID from a prior apply-patch-build call. Omit to run against the base build.",
    )
    run_pov_parser.add_argument(
        "--builder",
        type=str,
        default=None,
        help="Runner sidecar module name (defaults to BUILDER_MODULE env var)",
    )

    def _run_pov(args):
        exit_code = crs_utils.run_pov(
            args.pov_path,
            args.harness,
            args.response_dir,
            args.rebuild_id,
            args.builder,
        )
        sys.exit(exit_code)

    run_pov_parser.set_defaults(func=_run_pov)

    # apply-patch-test command
    apply_patch_test_parser = subparsers.add_parser(
        "apply-patch-test",
        help="Apply a target-source patch and run the project's bundled test.sh",
    )
    apply_patch_test_parser.add_argument(
        "patch_path", type=Path, help="Path to the unified diff file"
    )
    apply_patch_test_parser.add_argument(
        "response_dir", type=Path, help="Directory to receive test results"
    )
    apply_patch_test_parser.add_argument(
        "--builder",
        type=str,
        default=None,
        help="Builder sidecar module name (defaults to BUILDER_MODULE env var)",
    )

    def _apply_patch_test(args):
        exit_code = crs_utils.apply_patch_test(
            args.patch_path,
            args.response_dir,
            args.builder,
        )
        sys.exit(exit_code)

    apply_patch_test_parser.set_defaults(func=_apply_patch_test)

    # =========================================================================
    # Service discovery
    # =========================================================================

    get_service_domain_parser = subparsers.add_parser(
        "get-service-domain",
        help="Get the service domain for accessing CRS services",
    )
    get_service_domain_parser.add_argument(
        "service_name", type=str, help="Service name to get the domain for"
    )
    get_service_domain_parser.set_defaults(
        func=lambda args: get_service_domain(crs_utils, args)
    )

    # =========================================================================
    # Bug-candidate interface
    # =========================================================================
    _register_bug_candidate(subparsers, crs_utils)

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        return

    else:
        args.func(args)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""One-off: move the bug-candidate DB out from under the compose hash.

The DB used to live at ``.oss-crs-workdir/crs_compose/<hash>/BUG_CANDIDATE_DIR/``.
That made "global" mean *global to one compose config*: a different CRS -- or any
compose edit outside ``CRSComposeConfig.md5_hash()``'s exclusion list -- forked the
DB, so two CRSs on the same target silently never saw each other's candidates. It
now hangs off the workdir root: ``.oss-crs-workdir/BUG_CANDIDATE_DIR/``.

This migrates existing workdirs. Rows are keyed by (target_key, harness,
correlation_id), so DBs from different composes merge cleanly; identical rows are
deduped with INSERT OR IGNORE (last writer does not clobber).

    uv run python scripts/migrate_bug_candidate_dir.py [--workdir .oss-crs-workdir] [--apply]

Defaults to a dry run. Refuses to touch a DB a live campaign is using.
"""

import argparse
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

# Every table is (re)created by libCRS's schema on open; we only move rows.
# `updates` is excluded: its `seq` is an AUTOINCREMENT pub/sub cursor, so rows are
# re-keyed on insert rather than carried over verbatim.
_TABLES = [
    "candidates",
    "explorations",
    "detections",
    "locations",
    "metadata",
    "ingested_files",
]


def _live_mounts(old_dirs: list[Path]) -> list[str]:
    """Containers currently bind-mounting one of the old dirs."""
    try:
        names = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=30, check=True,
        ).stdout.split()
    except Exception:
        return []
    live = []
    for n in names:
        try:
            src = subprocess.run(
                ["docker", "inspect", n, "--format",
                 '{{range .Mounts}}{{.Source}}{{"\\n"}}{{end}}'],
                capture_output=True, text=True, timeout=30, check=True,
            ).stdout
        except Exception:
            continue
        if any(str(d) in src for d in old_dirs):
            live.append(n)
    return live


def _merge(src: Path, dst: Path) -> dict:
    """Merge every row of src's DB into dst's, skipping duplicates."""
    conn = sqlite3.connect(str(dst))
    conn.execute("ATTACH DATABASE ? AS src", (str(src),))
    moved = {}
    for table in _TABLES:
        try:
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
            src_cols = [r[1] for r in conn.execute(f"PRAGMA src.table_info({table})")]
        except sqlite3.Error:
            continue
        shared = [c for c in cols if c in src_cols]
        if not shared:
            continue
        cl = ", ".join(shared)
        before = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        conn.execute(f"INSERT OR IGNORE INTO {table} ({cl}) SELECT {cl} FROM src.{table}")
        after = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        moved[table] = after - before
    conn.commit()
    conn.execute("DETACH DATABASE src")
    conn.close()
    return moved


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workdir", type=Path, default=Path(".oss-crs-workdir"))
    ap.add_argument("--apply", action="store_true", help="Actually migrate (default: dry run)")
    args = ap.parse_args()

    root: Path = args.workdir
    if not root.is_dir():
        print(f"error: no workdir at {root}", file=sys.stderr)
        return 1

    sources = sorted(
        p for p in root.glob("crs_compose/*/BUG_CANDIDATE_DIR") if (p / "index.sqlite").exists()
    )
    if not sources:
        print("nothing to migrate: no crs_compose/*/BUG_CANDIDATE_DIR/index.sqlite")
        return 0

    live = _live_mounts(sources)
    if live:
        print("REFUSING: these containers are still using the old DB:", file=sys.stderr)
        for n in live:
            print(f"  {n}", file=sys.stderr)
        print("Wait for the campaign to finish, then re-run.", file=sys.stderr)
        return 2

    dst_dir = root / "BUG_CANDIDATE_DIR"
    dst = dst_dir / "index.sqlite"
    print(f"destination: {dst}{'' if dst.exists() else '  (new)'}\n")

    # A run started after the path change will already have created the destination
    # from inside a container -- i.e. owned by root, mode 644 -- so a host-side merge
    # opens fine for reads and then dies with "attempt to write a readonly database"
    # partway through. Fail up front with the fix rather than half-migrating.
    if dst.exists() and not os.access(dst, os.W_OK):
        st = dst.stat()
        print(
            f"error: {dst} is not writable by this user (owner uid={st.st_uid}, "
            f"mode={oct(st.st_mode & 0o777)}).\n"
            f"       A CRS container likely created it as root. Fix with:\n"
            f"         sudo chown -R $(id -u):$(id -g) {dst_dir}\n"
            f"       then re-run.",
            file=sys.stderr,
        )
        return 3

    for s in sources:
        n = sqlite3.connect(str(s / "index.sqlite")).execute(
            "SELECT COUNT(*) FROM candidates"
        ).fetchone()[0]
        print(f"  source {s.parent.name}: {n} candidates")

    if not args.apply:
        print("\ndry run — re-run with --apply")
        return 0

    dst_dir.mkdir(parents=True, exist_ok=True)
    # Simple case: nothing at the destination and a single source -> move it whole
    # (keeps sarif/ and any sidecar files alongside the DB).
    if not dst.exists() and len(sources) == 1:
        for item in sources[0].iterdir():
            shutil.move(str(item), str(dst_dir / item.name))
        print(f"\nmoved {sources[0]} -> {dst_dir}")
    else:
        # libCRS creates the schema on open; make sure dst has one before merging.
        if not dst.exists():
            sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "libCRS"))
            from libCRS.bug_candidate import BugCandidateStore  # noqa: E402

            BugCandidateStore(dst_dir).close()
        for s in sources:
            moved = _merge(s / "index.sqlite", dst)
            print(f"\nmerged {s.parent.name}: " + ", ".join(f"{k}+{v}" for k, v in moved.items()))
            for f in s.glob("sarif/*"):
                (dst_dir / "sarif").mkdir(exist_ok=True)
                shutil.copy2(f, dst_dir / "sarif" / f.name)

    # Leave the old dirs behind, renamed, rather than deleting them.
    for s in sources:
        if s.exists() and any(s.iterdir()):
            s.rename(s.with_name("BUG_CANDIDATE_DIR.migrated"))

    total = sqlite3.connect(str(dst)).execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
    print(f"\ndone: {total} candidates at {dst}")
    print("old dirs kept as BUG_CANDIDATE_DIR.migrated/ — remove once you're happy")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# SPDX-License-Identifier: MIT
"""Bug-candidate interface — one global SQLite DB for the whole workdir.

A single database (``OSS_CRS_BUG_CANDIDATE_DIR/index.sqlite``) is mounted
read-write into every CRS module container of every run. SQLite's own locking
(WAL + ``busy_timeout``) handles concurrent access, so:

- state is immediately consistent across CRSs (no replication), and
- ``claim`` is a genuine atomic check-and-set, so parallel agents never both grab
  the same candidate.

The DB is one portable file under ``.oss-crs-workdir`` (keeping the "all
artifacts are files" model). A candidate's SARIF is written as a plain file into
the producing CRS's ``SUBMIT_DIR/bug-candidates/`` — the same parent as POV files
— so it rides the normal artifacts/exchange machinery (visible to ``oss-crs
artifacts``/``archive``, forwarded to other CRSs) and stays tied to that
campaign's output. Its filename is ``<correlation_id>.sarif.json`` — deterministic
from the candidate id — so the DB stores no path; consumers reconstruct it from
the id (and ``created_run`` locates the producing run's dir). Every mutation
appends to an ``updates`` table used as the pub/sub cursor.

**Scoping.** Because the DB is global, each row carries ``target_key`` and
``harness`` columns; the store is constructed with the current CRS's values
(``OSS_CRS_TARGET_KEY`` / ``OSS_CRS_TARGET_HARNESS``) and *every* query is
automatically filtered by them and every insert stamps them. A CRS therefore only
ever sees its own target+harness candidates, while the file stays shared.

Identity: a candidate's key is its **correlation id** — the SARIF
``result.correlationGuid`` (the equivalence class of logically identical
results), or a minted opaque id when the SARIF has none — unique within a
(target_key, harness) scope. The per-instance ``result.guid`` is recorded as
detection provenance, never identity; ``partialFingerprints`` are a merge hint.
"""

import hashlib
import json
import logging
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Callable, Iterator, Optional

from . import sarif

logger = logging.getLogger(__name__)

DEFAULT_CLAIM_TTL = 3600  # seconds
_POLL_INTERVAL = 5.0
# Matches oss_crs constants.UNHARNESSED — the sentinel for targets with no harness.
DEFAULT_HARNESS = "OSS_CRS_UNHARNESSED"

# Bump when the schema changes; see _migrate().
_SCHEMA_VERSION = 2

# Status is DERIVED, never stored: a candidate either has a PoV or it doesn't.
# There is deliberately no "confirmed"/"false_positive" -- those are *judgments*,
# and judgments are what independently-developed CRSs disagree about. A single
# mutable status column silently last-writer-wins that disagreement away. The
# shared interface carries only facts: who explored it (`explorations`, a
# monotonic set) and whether a PoV came out of it (`pov_ref` + provenance).
STATUS_NEW = "new"
STATUS_POV_GENERATED = "pov_generated"

# Read scopes. WRITES are always scoped to the current target_key+harness -- a CRS
# may only file candidates against what it is actually working on. READS can be
# widened deliberately, to borrow cross-domain knowledge: the same library bug
# often reaches several harnesses, and a lead found via one entry point is
# evidence for the others.
SCOPE_HARNESS = "harness"  # default: this target_key + this harness
SCOPE_PROJECT = "project"  # this target_key, ALL harnesses (all harnesses of a
#                            project build share a target_key -- it has no harness
#                            component)
SCOPE_ALL = "all"  # every project, every harness in the global DB
_VALID_SCOPES = (SCOPE_HARNESS, SCOPE_PROJECT, SCOPE_ALL)


def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha(*parts) -> str:
    return hashlib.sha256(_canonical(parts).encode()).hexdigest()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);

CREATE TABLE IF NOT EXISTS candidates (
    target_key           TEXT NOT NULL,
    harness              TEXT NOT NULL,
    correlation_id       TEXT NOT NULL,
    rule_id              TEXT,
    severity             TEXT,
    summary              TEXT,
    harness_name         TEXT,
    pov_ref              TEXT,
    pov_crs              TEXT,
    pov_agent            TEXT,
    pov_at               REAL,
    partial_fingerprints TEXT,
    alias_of             TEXT,
    claim_owner          TEXT,
    claim_lease_expires  REAL,
    claim_ts             REAL,
    created_crs          TEXT,
    created_agent        TEXT,
    created_run          TEXT,
    created_at           REAL,
    updated_at           REAL,
    PRIMARY KEY (target_key, harness, correlation_id)
);

-- Monotonic set: which CRSs have tried exploring a candidate. Additive and
-- order-independent (set union), so concurrent CRSs never conflict -- unlike a
-- single mutable status, which silently last-writer-wins across disagreeing CRSs.
-- One row per CRS; re-exploring refreshes agent/at.
CREATE TABLE IF NOT EXISTS explorations (
    target_key     TEXT NOT NULL,
    harness        TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    crs            TEXT NOT NULL,
    agent          TEXT,
    run_id         TEXT,
    at             REAL,
    PRIMARY KEY (target_key, harness, correlation_id, crs)
);
CREATE INDEX IF NOT EXISTS idx_explorations_corr
    ON explorations(target_key, harness, correlation_id);

CREATE TABLE IF NOT EXISTS detections (
    detection_key  TEXT PRIMARY KEY,
    target_key     TEXT NOT NULL,
    harness        TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    guid           TEXT,
    crs            TEXT,
    agent          TEXT,
    run_id         TEXT,
    created_at     REAL
);
CREATE INDEX IF NOT EXISTS idx_detections_corr
    ON detections(target_key, harness, correlation_id);

CREATE TABLE IF NOT EXISTS locations (
    loc_key        TEXT PRIMARY KEY,
    target_key     TEXT NOT NULL,
    harness        TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    version        TEXT,
    repository_uri TEXT,
    branch         TEXT,
    file_path      TEXT,
    uri_base_id    TEXT,
    start_line     INTEGER,
    start_column   INTEGER,
    end_line       INTEGER,
    end_column     INTEGER,
    function_name  TEXT
);
CREATE INDEX IF NOT EXISTS idx_locations_corr
    ON locations(target_key, harness, correlation_id);

CREATE TABLE IF NOT EXISTS updates (
    seq            INTEGER PRIMARY KEY AUTOINCREMENT,
    target_key     TEXT NOT NULL,
    harness        TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    type           TEXT NOT NULL,
    payload_json   TEXT,
    crs            TEXT,
    agent          TEXT,
    run_id         TEXT,
    ts             REAL
);
CREATE INDEX IF NOT EXISTS idx_updates_scope ON updates(target_key, harness, seq);

CREATE TABLE IF NOT EXISTS metadata (
    target_key     TEXT NOT NULL,
    harness        TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    key            TEXT NOT NULL,
    value          TEXT,
    PRIMARY KEY (target_key, harness, correlation_id, key)
);
CREATE INDEX IF NOT EXISTS idx_metadata_kv ON metadata(target_key, harness, key, value);

CREATE TABLE IF NOT EXISTS ingested_files (
    target_key TEXT NOT NULL,
    harness    TEXT NOT NULL,
    name       TEXT NOT NULL,
    PRIMARY KEY (target_key, harness, name)
);
"""


class BugCandidateStore:
    """Bug-candidate store over one global DB, auto-scoped by target_key+harness."""

    def __init__(
        self,
        db_dir: Path,
        target_key: str = "unknown",
        harness: str = DEFAULT_HARNESS,
        submit_dir: Optional[Path] = None,
        fetch_dir: Optional[Path] = None,
        crs_name: str = "unknown",
        agent: Optional[str] = None,
        run_id: str = "",
        revision: str = "",
        repository_uri: str = "",
        clock: Callable[[], float] = time.time,
    ):
        self.db_dir = Path(db_dir)
        self.run_id = run_id
        # Default program version for locations (the campaign's resolved source
        # revision, OSS_CRS_TARGET_REVISION) — agents can still override per-add.
        self.revision = revision
        # Default repository for locations (the target's main_repo from
        # project.yaml, OSS_CRS_TARGET_REPOSITORY) — pairs with revision so the
        # opaque target_key is human-identifiable. Agents can override per-add.
        self.repository_uri = repository_uri
        # SARIF blobs are published as files into SUBMIT_DIR/bug-candidates/ (the
        # POV files' parent), so they flow through artifacts/exchange like POVs.
        self.submit_bc_dir = Path(submit_dir) / "bug-candidates" if submit_dir else None
        # Directed-input / bootup raw SARIF arrives read-only via the fetch dir.
        self.fetch_sarif_dir = Path(fetch_dir) / "bug-candidates" if fetch_dir else None
        self.target_key = target_key
        self.harness = harness or DEFAULT_HARNESS
        self.crs_name = crs_name
        self._agent_default = agent or os.environ.get("OSS_CRS_AGENT_ID") or crs_name
        self._clock = clock

        self.db_dir.mkdir(parents=True, exist_ok=True)
        # isolation_level="IMMEDIATE": the sqlite3 module opens a write-locking
        # transaction before the first DML statement, so claim()'s check-and-set
        # is atomic across every CRS sharing this file.
        self.conn = sqlite3.connect(
            str(self.db_dir / "index.sqlite"), timeout=30.0, isolation_level="IMMEDIATE"
        )
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.conn.executescript(_SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Bring an existing DB up to _SCHEMA_VERSION.

        The DB is global and persists across runs, so it long outlives any single
        schema. CREATE TABLE IF NOT EXISTS alone never alters an existing table,
        so column changes need explicit steps here.
        """
        row = self.conn.execute(
            "SELECT v FROM meta WHERE k='schema_version'"
        ).fetchone()
        version = int(row["v"]) if row else 1  # pre-versioning DBs are v1

        if version < 2:
            cols = {
                r["name"] for r in self.conn.execute("PRAGMA table_info(candidates)")
            }
            for name, decl in (
                ("pov_crs", "TEXT"),
                ("pov_agent", "TEXT"),
                ("pov_at", "REAL"),
            ):
                if name not in cols:
                    self.conn.execute(
                        f"ALTER TABLE candidates ADD COLUMN {name} {decl}"
                    )
            if "status" in cols:
                # A legacy status other than 'new' (exploring/confirmed/fixed/
                # false_positive) means one thing that survives the model change:
                # that CRS looked at this candidate. Drop the judgment, keep the
                # fact.
                self.conn.execute(
                    "INSERT OR IGNORE INTO explorations "
                    "(target_key, harness, correlation_id, crs, agent, run_id, at) "
                    "SELECT target_key, harness, correlation_id, "
                    "COALESCE(created_crs, 'unknown'), created_agent, created_run, "
                    "created_at FROM candidates "
                    "WHERE status IS NOT NULL AND status != 'new'"
                )
                self.conn.execute("ALTER TABLE candidates DROP COLUMN status")

        self.conn.execute(
            "INSERT OR REPLACE INTO meta (k, v) VALUES ('schema_version', ?)",
            (str(_SCHEMA_VERSION),),
        )

    # ------------------------------------------------------------------ utils
    def close(self) -> None:
        self.conn.close()

    def _now(self) -> float:
        return self._clock()

    def _resolve_actor(self, actor: Optional[str]) -> str:
        return actor or self._agent_default

    def _scope(self, *extra) -> tuple:
        """Prepend (target_key, harness) to query args for scoped WHERE clauses."""
        return (self.target_key, self.harness, *extra)

    def _require_own(self, candidate_id: str, cid: str) -> None:
        """Assert a candidate lives in THIS store's scope before mutating it.

        Reads can be widened (SCOPE_PROJECT/SCOPE_ALL), so a CRS can hold the id
        of a candidate belonging to another harness/project. Writes must never
        cross that line: mutating one would otherwise silently no-op (an UPDATE
        matching zero rows) or insert a row referencing a candidate absent from
        this scope. To act on a foreign lead, `add` it in your own scope.
        """
        row = self.conn.execute(
            "SELECT 1 FROM candidates "
            "WHERE target_key=? AND harness=? AND correlation_id=?",
            self._scope(cid),
        ).fetchone()
        if row is None:
            raise KeyError(
                f"bug-candidate {candidate_id!r} is not in this CRS's scope "
                f"(target_key={self.target_key!r}, harness={self.harness!r}). "
                f"Reads may be widened with scope=, but writes may not; `add` it "
                f"in your own scope to act on a foreign lead."
            )

    def _scope_where(self, scope: str) -> tuple[str, tuple]:
        """SQL predicate + args for a read scope. See SCOPE_* constants."""
        if scope == SCOPE_HARNESS:
            return "target_key=? AND harness=?", (self.target_key, self.harness)
        if scope == SCOPE_PROJECT:
            return "target_key=?", (self.target_key,)
        if scope == SCOPE_ALL:
            return "1=1", ()
        raise ValueError(f"Invalid scope {scope!r}; expected one of {_VALID_SCOPES}")

    def _record_update(
        self, correlation_id: str, utype: str, payload: dict, actor: Optional[str]
    ) -> None:
        self.conn.execute(
            "INSERT INTO updates (target_key, harness, correlation_id, type, "
            "payload_json, crs, agent, run_id, ts) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                self.target_key,
                self.harness,
                correlation_id,
                utype,
                json.dumps(payload),
                self.crs_name,
                self._resolve_actor(actor),
                self.run_id,
                self._now(),
            ),
        )

    def _resolve_alias(self, correlation_id: str) -> str:
        seen = set()
        cur = correlation_id
        while cur not in seen:
            seen.add(cur)
            row = self.conn.execute(
                "SELECT alias_of FROM candidates "
                "WHERE target_key=? AND harness=? AND correlation_id=?",
                self._scope(cur),
            ).fetchone()
            if row is None or row["alias_of"] is None:
                return cur
            cur = row["alias_of"]
        return cur  # cycle guard

    # --------------------------------------------------------------- mutations
    def _insert_locations(self, correlation_id: str, locs) -> None:
        for loc in locs:
            loc_key = _sha(
                self.target_key,
                self.harness,
                correlation_id,
                loc.version,
                loc.file_path,
                loc.start_line,
                loc.start_column,
                loc.end_line,
                loc.end_column,
                loc.function_name,
            )
            self.conn.execute(
                "INSERT OR IGNORE INTO locations (loc_key, target_key, harness, "
                "correlation_id, version, repository_uri, branch, file_path, "
                "uri_base_id, start_line, start_column, end_line, end_column, "
                "function_name) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    loc_key,
                    self.target_key,
                    self.harness,
                    correlation_id,
                    loc.version,
                    loc.repository_uri,
                    loc.branch,
                    loc.file_path,
                    loc.uri_base_id,
                    loc.start_line,
                    loc.start_column,
                    loc.end_line,
                    loc.end_column,
                    loc.function_name,
                ),
            )

    def _upsert_from_result(
        self,
        result: dict,
        vinfo: sarif.VersionInfo,
        *,
        agent: Optional[str],
        harness_name: Optional[str],
        pov_ref: Optional[str],
        publish: bool = True,
    ) -> str:
        """Create or enrich a candidate from one SARIF result. Idempotent.

        ``publish`` writes the SARIF as a file into SUBMIT_DIR/bug-candidates/
        (for CRS-produced candidates); directed-input candidates (publish=False)
        already have their file in the fetch dir.
        """
        cid = result.get("correlationGuid")
        if not cid:
            cid = uuid.uuid4().hex
            result = {**result, "correlationGuid": cid}
        # Default the location version + repository to the campaign revision /
        # main_repo when the caller / SARIF didn't specify them, so candidates
        # resolve to the source they were found against.
        if (not vinfo.revision_id and self.revision) or (
            not vinfo.repository_uri and self.repository_uri
        ):
            vinfo = sarif.VersionInfo(
                revision_id=vinfo.revision_id or self.revision,
                repository_uri=vinfo.repository_uri or self.repository_uri,
                branch=vinfo.branch,
            )
        bc = sarif._parse_result(result, vinfo)
        now = self._now()
        actor = self._resolve_actor(agent)

        # Publish the SARIF as a file into the submit dir (POV files' parent), so it
        # flows through artifacts/exchange. Its name is <correlation_id>.sarif.json,
        # so the path is reconstructible from the id (+ created_run) — we don't
        # store it in the DB. Directed inputs (publish=False) already have a file.
        if publish and self.submit_bc_dir is not None:
            self.submit_bc_dir.mkdir(parents=True, exist_ok=True)
            p = self.submit_bc_dir / f"{cid}.sarif.json"
            if not p.exists():
                p.write_text(
                    json.dumps(sarif.wrap_result(result, vinfo), indent=2),
                    encoding="utf-8",
                )

        props = result.get("properties") or {}
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO candidates (target_key, harness, correlation_id, "
            "rule_id, severity, summary, harness_name, pov_ref, "
            "partial_fingerprints, created_crs, created_agent, created_run, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                self.target_key,
                self.harness,
                cid,
                bc.rule_id,
                bc.level,
                bc.message,
                harness_name or props.get("harness"),
                pov_ref or props.get("pov_ref"),
                json.dumps(bc.partial_fingerprints)
                if bc.partial_fingerprints
                else None,
                self.crs_name,
                actor,
                self.run_id,
                now,
                now,
            ),
        )
        if cur.rowcount == 1:
            self._record_update(cid, "created", {"rule_id": bc.rule_id}, agent)

        # Per-instance detection (guid) provenance, deduped by content.
        self.conn.execute(
            "INSERT OR IGNORE INTO detections (detection_key, target_key, harness, "
            "correlation_id, guid, crs, agent, run_id, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                _sha(self.target_key, self.harness, cid, _canonical(result)),
                self.target_key,
                self.harness,
                cid,
                bc.guid,
                self.crs_name,
                actor,
                self.run_id,
                now,
            ),
        )
        self._insert_locations(cid, bc.locations)

        # Index arbitrary SARIF `properties` as queryable metadata (last-writer-wins
        # per key). `harness`/`pov_ref` are already typed columns, so they're not
        # duplicated here.
        meta_props = {k: v for k, v in props.items() if k not in ("harness", "pov_ref")}
        for k, v in meta_props.items():
            self.conn.execute(
                "INSERT OR REPLACE INTO metadata (target_key, harness, "
                "correlation_id, key, value) VALUES (?,?,?,?,?)",
                (
                    self.target_key,
                    self.harness,
                    cid,
                    k,
                    v if isinstance(v, str) else json.dumps(v),
                ),
            )
        if meta_props:
            self._record_update(cid, "property", {"keys": sorted(meta_props)}, agent)
        return cid

    def add(
        self,
        sarif_path: Path,
        *,
        agent: Optional[str] = None,
        harness: Optional[str] = None,
        pov_ref: Optional[str] = None,
    ) -> list[str]:
        """Register bug-candidate(s) from a SARIF file. Returns correlation ids.

        A new candidate is always fresh -- there is no initial status to set. Use
        ``mark_explored`` / ``mark_pov`` to record what happened to it afterwards.

        (``harness`` here labels the candidate's associated harness in metadata;
        the store's scoping harness is fixed at construction.)
        """
        doc = json.loads(Path(sarif_path).read_text(encoding="utf-8"))
        errors = sarif.validate_sarif(doc)
        if errors:
            raise ValueError(f"Invalid SARIF {sarif_path}: {'; '.join(errors)}")
        ids: list[str] = []
        for result, vinfo in sarif.iter_results(doc):
            ids.append(
                self._upsert_from_result(
                    result,
                    vinfo,
                    agent=agent,
                    harness_name=harness,
                    pov_ref=pov_ref,
                )
            )
        self.conn.commit()
        return ids

    def add_finding(
        self,
        *,
        file_path: Optional[str] = None,
        start_line: Optional[int] = None,
        rule: Optional[str] = None,
        message: Optional[str] = None,
        severity: str = "warning",
        function_name: Optional[str] = None,
        end_line: Optional[int] = None,
        start_column: Optional[int] = None,
        end_column: Optional[int] = None,
        uri_base_id: Optional[str] = None,
        version: Optional[str] = None,
        repository_uri: Optional[str] = None,
        branch: Optional[str] = None,
        correlation_id: Optional[str] = None,
        harness: Optional[str] = None,
        pov_ref: Optional[str] = None,
        properties: Optional[dict] = None,
        agent: Optional[str] = None,
    ) -> str:
        """Create or enrich a candidate from structured fields — builds the SARIF
        internally. Returns the correlation id.

        Pass an existing ``correlation_id`` to append a location/detection/metadata
        to that candidate (this folds the former ``add_location``); omit it to mint
        a new candidate. ``rule``/``message``/``severity`` describe a new candidate
        and are ignored when enriching an existing one. ``properties`` is arbitrary
        indexed metadata (queryable, last-writer-wins per key) — e.g.
        ``{"subagent": "pov-gen"}``.
        """
        result = sarif.build_result(
            rule_id=rule or "",
            level=severity,
            message=message,
            file_path=file_path,
            start_line=start_line,
            start_column=start_column,
            end_line=end_line,
            end_column=end_column,
            function_name=function_name,
            uri_base_id=uri_base_id,
            correlation_guid=correlation_id,
            properties=properties,
        )
        vinfo = sarif.VersionInfo(
            revision_id=version, repository_uri=repository_uri, branch=branch
        )
        cid = self._upsert_from_result(
            result,
            vinfo,
            agent=agent,
            harness_name=harness,
            pov_ref=pov_ref,
        )
        self.conn.commit()
        return cid

    def add_location(
        self,
        candidate_id: str,
        version: str,
        file_path: str,
        start_line: int,
        *,
        end_line: Optional[int] = None,
        start_column: Optional[int] = None,
        end_column: Optional[int] = None,
        function_name: Optional[str] = None,
        repository_uri: Optional[str] = None,
        branch: Optional[str] = None,
        uri_base_id: Optional[str] = None,
        agent: Optional[str] = None,
    ) -> None:
        cid = self._resolve_alias(candidate_id)
        loc = sarif.BugLocation(
            file_path=file_path,
            start_line=start_line,
            end_line=end_line,
            function_name=function_name,
            start_column=start_column,
            end_column=end_column,
            uri_base_id=uri_base_id,
            version=version or self.revision or None,
            repository_uri=repository_uri or self.repository_uri or None,
            branch=branch,
        )
        self._insert_locations(cid, [loc])
        self.conn.execute(
            "UPDATE candidates SET updated_at=? "
            "WHERE target_key=? AND harness=? AND correlation_id=?",
            (self._now(), self.target_key, self.harness, cid),
        )
        self._record_update(
            cid, "location", {"version": version, "file_path": file_path}, agent
        )
        self.conn.commit()

    def mark_explored(self, candidate_id: str, *, agent: Optional[str] = None) -> None:
        """Record that THIS CRS tried exploring this candidate.

        Additive and idempotent: `explorations` is a set keyed by CRS, so
        concurrent CRSs marking the same candidate can never conflict. The CRS
        name is derived from the store (OSS_CRS_NAME) -- callers never pass it.

        This deliberately records only the fact of the attempt, not a verdict.
        """
        cid = self._resolve_alias(candidate_id)
        self._require_own(candidate_id, cid)
        now = self._now()
        self.conn.execute(
            "INSERT INTO explorations (target_key, harness, correlation_id, crs, "
            "agent, run_id, at) VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(target_key, harness, correlation_id, crs) "
            "DO UPDATE SET agent=excluded.agent, run_id=excluded.run_id, "
            "at=excluded.at",
            (
                self.target_key,
                self.harness,
                cid,
                self.crs_name,
                self._resolve_actor(agent),
                self.run_id,
                now,
            ),
        )
        self._record_update(cid, "explored", {"crs": self.crs_name}, agent)
        self.conn.commit()

    def mark_pov(
        self, candidate_id: str, pov_ref: str, *, agent: Optional[str] = None
    ) -> None:
        """Record that a PoV was generated from this candidate.

        ``pov_ref`` is the PoV's content hash, linking the candidate to the
        artifact it produced. This is the only transition the shared status
        expresses: fresh candidate -> PoV generated.
        """
        if not pov_ref:
            raise ValueError("mark_pov requires a pov_ref (the PoV's content hash)")
        cid = self._resolve_alias(candidate_id)
        self._require_own(candidate_id, cid)
        now = self._now()
        self.conn.execute(
            "UPDATE candidates SET pov_ref=?, pov_crs=?, pov_agent=?, pov_at=?, "
            "updated_at=? WHERE target_key=? AND harness=? AND correlation_id=?",
            (
                pov_ref,
                self.crs_name,
                self._resolve_actor(agent),
                now,
                now,
                self.target_key,
                self.harness,
                cid,
            ),
        )
        self._record_update(
            cid, "pov", {"pov_ref": pov_ref, "crs": self.crs_name}, agent
        )
        self.conn.commit()

    def note(
        self, candidate_id: str, text: str, *, agent: Optional[str] = None
    ) -> None:
        cid = self._resolve_alias(candidate_id)
        # Notes land in the `updates` stream under this store's scope, so a note on
        # a foreign candidate would never reach that candidate's own watchers.
        self._require_own(candidate_id, cid)
        self._record_update(cid, "note", {"text": text}, agent)
        self.conn.commit()

    def merge(
        self, candidate_id: str, into_candidate_id: str, *, agent: Optional[str] = None
    ) -> None:
        cid = self._resolve_alias(candidate_id)
        into = self._resolve_alias(into_candidate_id)
        if cid == into:
            return
        self.conn.execute(
            "UPDATE candidates SET alias_of=?, updated_at=? "
            "WHERE target_key=? AND harness=? AND correlation_id=?",
            (into, self._now(), self.target_key, self.harness, cid),
        )
        self._record_update(cid, "merge", {"into": into}, agent)
        self.conn.commit()

    def claim(
        self,
        candidate_id: str,
        owner: Optional[str] = None,
        ttl_seconds: float = DEFAULT_CLAIM_TTL,
    ) -> bool:
        """Atomically claim a candidate. Returns False if another owner holds an
        unexpired claim. The check-and-set runs in one write transaction."""
        owner = self._resolve_actor(owner)
        now = self._now()
        cid = self._resolve_alias(candidate_id)
        try:
            # UPDATE opens the IMMEDIATE write transaction, excluding other writers.
            self.conn.execute(
                "UPDATE candidates SET updated_at=updated_at "
                "WHERE target_key=? AND harness=? AND correlation_id=?",
                (self.target_key, self.harness, cid),
            )
            row = self.conn.execute(
                "SELECT claim_owner, claim_lease_expires FROM candidates "
                "WHERE target_key=? AND harness=? AND correlation_id=?",
                (self.target_key, self.harness, cid),
            ).fetchone()
            if row is None:
                self.conn.rollback()
                raise KeyError(f"unknown bug-candidate: {candidate_id}")
            active = (
                row["claim_owner"] is not None
                and row["claim_lease_expires"] is not None
                and row["claim_lease_expires"] > now
            )
            if active and row["claim_owner"] != owner:
                self.conn.rollback()
                return False
            self.conn.execute(
                "UPDATE candidates SET claim_owner=?, claim_lease_expires=?, "
                "claim_ts=?, updated_at=? "
                "WHERE target_key=? AND harness=? AND correlation_id=?",
                (
                    owner,
                    now + ttl_seconds,
                    now,
                    now,
                    self.target_key,
                    self.harness,
                    cid,
                ),
            )
            self._record_update(cid, "claim", {"owner": owner}, owner)
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise
        return True

    def release(self, candidate_id: str, owner: Optional[str] = None) -> None:
        owner = self._resolve_actor(owner)
        cid = self._resolve_alias(candidate_id)
        self.conn.execute(
            "UPDATE candidates SET claim_owner=NULL, claim_lease_expires=NULL, "
            "claim_ts=NULL, updated_at=? "
            "WHERE target_key=? AND harness=? AND correlation_id=?",
            (self._now(), self.target_key, self.harness, cid),
        )
        self._record_update(cid, "release", {"owner": owner}, owner)
        self.conn.commit()

    # ---------------------------------------------------------------- queries
    # Detail helpers take the ROW's (target_key, harness) rather than the store's:
    # a widened-scope read returns rows from other harnesses/projects, and using
    # the store's scope here would silently return empty locations/metadata.
    def _metadata_for(self, tk: str, h: str, correlation_id: str) -> dict:
        rows = self.conn.execute(
            "SELECT key, value FROM metadata "
            "WHERE target_key=? AND harness=? AND correlation_id=?",
            (tk, h, correlation_id),
        ).fetchall()
        return {r["key"]: r["value"] for r in rows}

    def _locations_for(self, tk: str, h: str, correlation_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT version, repository_uri, branch, file_path, uri_base_id, "
            "start_line, start_column, end_line, end_column, function_name "
            "FROM locations WHERE target_key=? AND harness=? AND correlation_id=? "
            "ORDER BY version, file_path, start_line",
            (tk, h, correlation_id),
        ).fetchall()
        return [dict(r) for r in rows]

    def _detection_guids(self, tk: str, h: str, correlation_id: str) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT guid FROM detections "
            "WHERE target_key=? AND harness=? AND correlation_id=? AND guid IS NOT NULL",
            (tk, h, correlation_id),
        ).fetchall()
        return [r["guid"] for r in rows]

    def _explored_by(self, tk: str, h: str, correlation_id: str) -> list[str]:
        """CRSs that have declared they tried exploring this candidate."""
        rows = self.conn.execute(
            "SELECT crs FROM explorations "
            "WHERE target_key=? AND harness=? AND correlation_id=? ORDER BY crs",
            (tk, h, correlation_id),
        ).fetchall()
        return [r["crs"] for r in rows]

    def _candidate_dict(self, row: sqlite3.Row, now: float) -> dict:
        active = (
            row["claim_owner"] is not None
            and row["claim_lease_expires"] is not None
            and row["claim_lease_expires"] > now
        )
        fingerprints = (
            json.loads(row["partial_fingerprints"])
            if row["partial_fingerprints"]
            else {}
        )
        # Use the row's own scope, not the store's — under a widened scope the row
        # may belong to another harness/project.
        tk, h = row["target_key"], row["harness"]
        # True when this row came from outside the store's own harness — lets a
        # consumer tell borrowed cross-domain knowledge from its own candidates.
        foreign = tk != self.target_key or h != self.harness
        return {
            "candidate_id": row["correlation_id"],
            "correlation_id": row["correlation_id"],
            "target_key": row["target_key"],
            "harness": row["harness"],
            "rule_id": row["rule_id"],
            "severity": row["severity"],
            "summary": row["summary"],
            "harness_name": row["harness_name"],
            "pov_ref": row["pov_ref"],
            # Derived, never stored: a candidate either produced a PoV or it
            # didn't. Two states that are a pure function of pov_ref must not be
            # a second source of truth that can drift.
            "status": STATUS_POV_GENERATED if row["pov_ref"] else STATUS_NEW,
            "pov": {
                "ref": row["pov_ref"],
                "crs": row["pov_crs"],
                "agent": row["pov_agent"],
                "at": row["pov_at"],
            },
            "explored_by": self._explored_by(tk, h, row["correlation_id"]),
            "foreign": foreign,
            "partial_fingerprints": fingerprints,
            "created_crs": row["created_crs"],
            "created_agent": row["created_agent"],
            "created_run": row["created_run"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "sarif_name": f"{row['correlation_id']}.sarif.json",
            "properties": self._metadata_for(tk, h, row["correlation_id"]),
            "detection_guids": self._detection_guids(tk, h, row["correlation_id"]),
            "claim": {
                "owner": row["claim_owner"] if active else None,
                "lease_expires": row["claim_lease_expires"] if active else None,
                "active": active,
            },
            "locations": self._locations_for(tk, h, row["correlation_id"]),
        }

    def get(self, candidate_id: str, *, scope: str = SCOPE_HARNESS) -> Optional[dict]:
        """Fetch one candidate. Widen ``scope`` to read another harness/project."""
        cid = self._resolve_alias(candidate_id)
        where, args = self._scope_where(scope)
        row = self.conn.execute(
            f"SELECT * FROM candidates WHERE {where} AND correlation_id=?",
            (*args, cid),
        ).fetchone()
        return self._candidate_dict(row, self._now()) if row else None

    def list_candidates(
        self,
        *,
        has_pov: Optional[bool] = None,
        explored_by: Optional[str] = None,
        not_explored_by: Optional[str] = None,
        claimed: Optional[bool] = None,
        version: Optional[str] = None,
        properties: Optional[dict] = None,
        scope: str = SCOPE_HARNESS,
    ) -> list[dict]:
        """Query candidates.

        ``not_explored_by`` accepts the sentinel "me" (the current CRS) -- the
        common case being "candidates my CRS hasn't tried yet".

        ``scope`` widens the read beyond this harness (see SCOPE_*): ``project``
        adds the target's other harnesses, ``all`` adds every project in the
        global DB. Rows from outside this harness are flagged ``foreign: true``.
        """
        now = self._now()
        where, args = self._scope_where(scope)
        rows = self.conn.execute(
            f"SELECT * FROM candidates WHERE {where} AND alias_of IS NULL "
            "ORDER BY created_at, correlation_id",
            args,
        ).fetchall()
        if explored_by == "me":
            explored_by = self.crs_name
        if not_explored_by == "me":
            not_explored_by = self.crs_name
        out: list[dict] = []
        for row in rows:
            d = self._candidate_dict(row, now)
            if has_pov is not None and bool(d["pov_ref"]) != has_pov:
                continue
            if explored_by is not None and explored_by not in d["explored_by"]:
                continue
            if not_explored_by is not None and not_explored_by in d["explored_by"]:
                continue
            if claimed is not None and d["claim"]["active"] != claimed:
                continue
            if version is not None and not any(
                loc["version"] == version for loc in d["locations"]
            ):
                continue
            if properties and any(
                d["properties"].get(k) != v for k, v in properties.items()
            ):
                continue
            out.append(d)
        return out

    # --------------------------------------------------------- directed input
    def sync(self) -> int:
        """Ingest directed-input / bootup raw SARIF files from the fetch dir.

        Returns the number of newly-ingested files.
        """
        if self.fetch_sarif_dir is None or not self.fetch_sarif_dir.is_dir():
            return 0
        count = 0
        for entry in sorted(self.fetch_sarif_dir.iterdir()):
            name = entry.name
            if name.startswith(".") or not entry.is_file():
                continue
            if self.conn.execute(
                "SELECT 1 FROM ingested_files "
                "WHERE target_key=? AND harness=? AND name=?",
                self._scope(name),
            ).fetchone():
                continue
            if self._ingest_raw_sarif(entry):
                count += 1
            self.conn.execute(
                "INSERT OR IGNORE INTO ingested_files (target_key, harness, name) "
                "VALUES (?,?,?)",
                self._scope(name),
            )
        self.conn.commit()
        return count

    def _ingest_raw_sarif(self, path: Path) -> bool:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("skipping unreadable bug-candidate file: %s", path)
            return False
        if not isinstance(doc, dict) or sarif.validate_sarif(doc):
            logger.warning("skipping invalid directed-input SARIF: %s", path)
            return False
        for result, vinfo in sarif.iter_results(doc):
            if not result.get("correlationGuid"):
                # Deterministic (and scope-local) id so re-ingest never dupes.
                result = {
                    **result,
                    "correlationGuid": _sha(
                        "directed-input",
                        self.target_key,
                        self.harness,
                        _canonical(result),
                    )[:32],
                }
            self._upsert_from_result(
                result,
                vinfo,
                agent="directed-input",
                harness_name=None,
                pov_ref=None,
                publish=False,
            )
        return True

    # --------------------------------------------------------------- pub/sub
    def _update_dict(self, row: sqlite3.Row) -> dict:
        return {
            "seq": row["seq"],
            "correlation_id": row["correlation_id"],
            "candidate_id": row["correlation_id"],
            "type": row["type"],
            "payload": json.loads(row["payload_json"]) if row["payload_json"] else {},
            "crs": row["crs"],
            "agent": row["agent"],
            "ts": row["ts"],
        }

    def _updates_since(
        self, since_seq: int, candidate_id: Optional[str], types: Optional[list[str]]
    ) -> list[dict]:
        q = "SELECT * FROM updates WHERE target_key=? AND harness=? AND seq > ?"
        args: list = [self.target_key, self.harness, since_seq]
        if candidate_id is not None:
            q += " AND correlation_id = ?"
            args.append(self._resolve_alias(candidate_id))
        q += " ORDER BY seq"
        rows = self.conn.execute(q, args).fetchall()
        return [self._update_dict(r) for r in rows if not types or r["type"] in types]

    def watch(
        self,
        since_seq: int = 0,
        *,
        candidate_id: Optional[str] = None,
        types: Optional[list[str]] = None,
        follow: bool = False,
        poll_interval: float = _POLL_INTERVAL,
        timeout: Optional[float] = None,
    ) -> Iterator[dict]:
        """Yield update events (for this target+harness) with ``seq > since_seq``.

        Other CRSs write to the same DB, so their updates appear here directly.
        With ``follow=True`` this polls (also ingesting new directed inputs) until
        ``timeout`` elapses or the caller stops iterating.
        """
        self.sync()
        last = since_seq
        for ev in self._updates_since(last, candidate_id, types):
            last = ev["seq"]
            yield ev
        if not follow:
            return
        start = self._now()
        while timeout is None or (self._now() - start) < timeout:
            time.sleep(poll_interval)
            self.sync()
            for ev in self._updates_since(last, candidate_id, types):
                last = ev["seq"]
                yield ev

# SPDX-License-Identifier: MIT
"""Bug-candidate interface — one global SQLite DB for the whole workdir.

A single database (``OSS_CRS_BUG_CANDIDATE_DIR/index.sqlite``) is mounted
read-write into every CRS module container of every run. SQLite's own locking
(WAL + ``busy_timeout``) handles concurrent access, so:

- state is immediately consistent across CRSs (no replication), and
- ``claim`` is a genuine atomic check-and-set, so parallel agents never both grab
  the same candidate.

The DB is one portable file under ``.oss-crs-workdir`` (keeping the "all
artifacts are files" model). SARIF results are written alongside as plain files
under ``sarif/`` and every mutation appends to an ``updates`` table used as the
pub/sub cursor.

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

_VALID_STATUSES = {"new", "exploring", "confirmed", "fixed", "false_positive"}


def normalize_status(status: str) -> str:
    s = str(status).strip().lower().replace("-", "_")
    if s not in _VALID_STATUSES:
        raise ValueError(
            f"Invalid status {status!r}; expected one of {sorted(_VALID_STATUSES)}"
        )
    return s


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
    sarif_path           TEXT,
    partial_fingerprints TEXT,
    status               TEXT NOT NULL DEFAULT 'new',
    alias_of             TEXT,
    claim_owner          TEXT,
    claim_lease_expires  REAL,
    claim_ts             REAL,
    created_crs          TEXT,
    created_agent        TEXT,
    created_at           REAL,
    updated_at           REAL,
    PRIMARY KEY (target_key, harness, correlation_id)
);

CREATE TABLE IF NOT EXISTS detections (
    detection_key  TEXT PRIMARY KEY,
    target_key     TEXT NOT NULL,
    harness        TEXT NOT NULL,
    correlation_id TEXT NOT NULL,
    guid           TEXT,
    crs            TEXT,
    agent          TEXT,
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
    ts             REAL
);
CREATE INDEX IF NOT EXISTS idx_updates_scope ON updates(target_key, harness, seq);

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
        fetch_dir: Optional[Path] = None,
        crs_name: str = "unknown",
        agent: Optional[str] = None,
        clock: Callable[[], float] = time.time,
    ):
        self.db_dir = Path(db_dir)
        self.sarif_dir = self.db_dir / "sarif"
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
        self.conn.commit()

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

    def _record_update(
        self, correlation_id: str, utype: str, payload: dict, actor: Optional[str]
    ) -> None:
        self.conn.execute(
            "INSERT INTO updates (target_key, harness, correlation_id, type, "
            "payload_json, crs, agent, ts) VALUES (?,?,?,?,?,?,?,?)",
            (
                self.target_key,
                self.harness,
                correlation_id,
                utype,
                json.dumps(payload),
                self.crs_name,
                self._resolve_actor(actor),
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
        status: str,
        harness_name: Optional[str],
        pov_ref: Optional[str],
    ) -> str:
        """Create or enrich a candidate from one SARIF result. Idempotent."""
        cid = result.get("correlationGuid")
        if not cid:
            cid = uuid.uuid4().hex
            result = {**result, "correlationGuid": cid}
        bc = sarif._parse_result(result, vinfo)
        now = self._now()
        actor = self._resolve_actor(agent)

        # Persist the SARIF result as a plain file (first writer wins).
        self.sarif_dir.mkdir(parents=True, exist_ok=True)
        sarif_path = self.sarif_dir / f"{cid}.sarif.json"
        if not sarif_path.exists():
            sarif_path.write_text(
                json.dumps(sarif.wrap_result(result, vinfo), indent=2),
                encoding="utf-8",
            )

        props = result.get("properties") or {}
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO candidates (target_key, harness, correlation_id, "
            "rule_id, severity, summary, harness_name, pov_ref, sarif_path, "
            "partial_fingerprints, status, created_crs, created_agent, created_at, "
            "updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                self.target_key,
                self.harness,
                cid,
                bc.rule_id,
                bc.level,
                bc.message,
                harness_name or props.get("harness"),
                pov_ref or props.get("pov_ref"),
                str(sarif_path),
                json.dumps(bc.partial_fingerprints)
                if bc.partial_fingerprints
                else None,
                normalize_status(status),
                self.crs_name,
                actor,
                now,
                now,
            ),
        )
        if cur.rowcount == 1:
            self._record_update(cid, "created", {"rule_id": bc.rule_id}, agent)

        # Per-instance detection (guid) provenance, deduped by content.
        self.conn.execute(
            "INSERT OR IGNORE INTO detections (detection_key, target_key, harness, "
            "correlation_id, guid, crs, agent, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                _sha(self.target_key, self.harness, cid, _canonical(result)),
                self.target_key,
                self.harness,
                cid,
                bc.guid,
                self.crs_name,
                actor,
                now,
            ),
        )
        self._insert_locations(cid, bc.locations)
        return cid

    def add(
        self,
        sarif_path: Path,
        *,
        agent: Optional[str] = None,
        status: str = "new",
        harness: Optional[str] = None,
        pov_ref: Optional[str] = None,
    ) -> list[str]:
        """Register bug-candidate(s) from a SARIF file. Returns correlation ids.

        (``harness`` here labels the candidate's associated harness in metadata;
        the store's scoping harness is fixed at construction.)
        """
        doc = json.loads(Path(sarif_path).read_text(encoding="utf-8"))
        errors = sarif.validate_sarif(doc)
        if errors:
            raise ValueError(f"Invalid SARIF {sarif_path}: {'; '.join(errors)}")
        status = normalize_status(status)
        ids: list[str] = []
        for result, vinfo in sarif.iter_results(doc):
            ids.append(
                self._upsert_from_result(
                    result,
                    vinfo,
                    agent=agent,
                    status=status,
                    harness_name=harness,
                    pov_ref=pov_ref,
                )
            )
        self.conn.commit()
        return ids

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
            version=version,
            repository_uri=repository_uri,
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

    def set_status(
        self, candidate_id: str, status: str, *, agent: Optional[str] = None
    ) -> None:
        status = normalize_status(status)
        cid = self._resolve_alias(candidate_id)
        self.conn.execute(
            "UPDATE candidates SET status=?, updated_at=? "
            "WHERE target_key=? AND harness=? AND correlation_id=?",
            (status, self._now(), self.target_key, self.harness, cid),
        )
        self._record_update(cid, "status", {"status": status}, agent)
        self.conn.commit()

    def note(
        self, candidate_id: str, text: str, *, agent: Optional[str] = None
    ) -> None:
        cid = self._resolve_alias(candidate_id)
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
    def _locations_for(self, correlation_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT version, repository_uri, branch, file_path, uri_base_id, "
            "start_line, start_column, end_line, end_column, function_name "
            "FROM locations WHERE target_key=? AND harness=? AND correlation_id=? "
            "ORDER BY version, file_path, start_line",
            self._scope(correlation_id),
        ).fetchall()
        return [dict(r) for r in rows]

    def _detection_guids(self, correlation_id: str) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT guid FROM detections "
            "WHERE target_key=? AND harness=? AND correlation_id=? AND guid IS NOT NULL",
            self._scope(correlation_id),
        ).fetchall()
        return [r["guid"] for r in rows]

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
            "status": row["status"],
            "sarif_path": row["sarif_path"],
            "partial_fingerprints": fingerprints,
            "created_crs": row["created_crs"],
            "created_agent": row["created_agent"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "detection_guids": self._detection_guids(row["correlation_id"]),
            "claim": {
                "owner": row["claim_owner"] if active else None,
                "lease_expires": row["claim_lease_expires"] if active else None,
                "active": active,
            },
            "locations": self._locations_for(row["correlation_id"]),
        }

    def get(self, candidate_id: str) -> Optional[dict]:
        cid = self._resolve_alias(candidate_id)
        row = self.conn.execute(
            "SELECT * FROM candidates "
            "WHERE target_key=? AND harness=? AND correlation_id=?",
            self._scope(cid),
        ).fetchone()
        return self._candidate_dict(row, self._now()) if row else None

    def list_candidates(
        self,
        *,
        status: Optional[str] = None,
        claimed: Optional[bool] = None,
        version: Optional[str] = None,
    ) -> list[dict]:
        now = self._now()
        rows = self.conn.execute(
            "SELECT * FROM candidates "
            "WHERE target_key=? AND harness=? AND alias_of IS NULL "
            "ORDER BY created_at, correlation_id",
            self._scope(),
        ).fetchall()
        out: list[dict] = []
        for row in rows:
            d = self._candidate_dict(row, now)
            if status is not None and d["status"] != normalize_status(status):
                continue
            if claimed is not None and d["claim"]["active"] != claimed:
                continue
            if version is not None and not any(
                loc["version"] == version for loc in d["locations"]
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
                status="new",
                harness_name=None,
                pov_ref=None,
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

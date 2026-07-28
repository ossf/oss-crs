# SPDX-License-Identifier: MIT
import json
import sqlite3

import pytest

from libCRS import sarif
from libCRS.bug_candidate import BugCandidateStore


def test_build_result_roundtrips_with_parse_result():
    """build_result is the exact inverse of _parse_result (they share the SARIF
    result shape, so they must round-trip)."""
    result = sarif.build_result(
        rule_id="heap-overflow",
        level="error",
        message="len used unchecked",
        file_path="src/p.c",
        start_line=142,
        start_column=5,
        end_line=143,
        end_column=9,
        function_name="parse_header",
        uri_base_id="SRCROOT",
        correlation_guid="CORR-9",
        properties={"subagent": "pov-gen"},
    )
    vinfo = sarif.VersionInfo(revision_id="rev1", repository_uri="r", branch="main")
    bc = sarif._parse_result(result, vinfo)

    assert bc.rule_id == "heap-overflow"
    assert bc.level == "error"
    assert bc.message == "len used unchecked"
    assert bc.correlation_guid == "CORR-9"
    assert result["properties"] == {"subagent": "pov-gen"}
    loc = bc.locations[0]
    assert (loc.file_path, loc.start_line, loc.start_column) == ("src/p.c", 142, 5)
    assert (loc.end_line, loc.end_column) == (143, 9)
    assert loc.function_name == "parse_header"
    assert loc.uri_base_id == "SRCROOT"
    # version-control provenance comes from the VersionInfo, not the result
    assert (loc.version, loc.repository_uri, loc.branch) == ("rev1", "r", "main")


def test_build_result_minimal_defaults():
    """A bare finding still yields a valid, parseable result."""
    bc = sarif._parse_result(sarif.build_result(), sarif.VersionInfo())
    assert bc.rule_id == "" and bc.level == "warning"
    assert bc.message == "bug candidate" and bc.locations == []


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def make_sarif(rule="CWE-787", uri="src/a.c", line=42, corr=None, rev="rev1", col=3):
    result = {
        "ruleId": rule,
        "level": "error",
        "message": {"text": f"bug in {uri}"},
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": uri},
                    "region": {"startLine": line, "startColumn": col},
                }
            }
        ],
    }
    if corr:
        result["correlationGuid"] = corr
    return {
        "version": "2.1.0",
        "runs": [
            {
                "tool": {"driver": {"name": "scanner"}},
                "versionControlProvenance": [
                    {"repositoryUri": "https://x/y", "revisionId": rev}
                ],
                "results": [result],
            }
        ],
    }


def write_sarif(tmp_path, name, **kw):
    p = tmp_path / name
    p.write_text(json.dumps(make_sarif(**kw)))
    return p


@pytest.fixture
def clock():
    return Clock()


def make_store(
    db_dir,
    *,
    submit=None,
    fetch=None,
    clock=None,
    crs="crs-a",
    target_key="proj_v1",
    harness="h1",
):
    """One BugCandidateStore. Pass the SAME db_dir for two stores to model two
    CRS containers sharing the single global bug-candidate DB."""
    return BugCandidateStore(
        db_dir,
        target_key=target_key,
        harness=harness,
        submit_dir=submit,
        fetch_dir=fetch,
        crs_name=crs,
        clock=clock or Clock(),
    )


def test_add_uses_correlation_guid_as_identity(tmp_path, clock):
    store = make_store(tmp_path / "db", clock=clock)
    ids = store.add(write_sarif(tmp_path, "b.sarif.json", corr="CORR-1"), agent="a1")
    assert ids == ["CORR-1"]
    got = store.get("CORR-1")
    assert got["status"] == "new"  # derived: no PoV yet
    assert got["rule_id"] == "CWE-787"
    loc = got["locations"][0]
    assert loc["version"] == "rev1"
    assert loc["start_column"] == 3
    assert loc["repository_uri"] == "https://x/y"


def test_add_publishes_sarif_to_submit_dir_with_minted_guid(tmp_path, clock):
    submit = tmp_path / "submit"
    store = make_store(tmp_path / "db", submit=submit, clock=clock)
    ids = store.add(write_sarif(tmp_path, "b.sarif.json"), agent="a1")  # no corr
    assert len(ids) == 1 and ids[0]
    # Published under SUBMIT_DIR/bug-candidates/ (the POV files' parent).
    published = submit / "bug-candidates" / f"{ids[0]}.sarif.json"
    assert published.exists()
    # No path stored — the filename is reconstructible from the id.
    got = store.get(ids[0])
    assert "sarif_path" not in got
    assert got["sarif_name"] == f"{ids[0]}.sarif.json" == published.name
    stored = json.loads(published.read_text())
    assert stored["runs"][0]["results"][0]["correlationGuid"] == ids[0]


def test_directed_input_references_source_not_republished(tmp_path, clock):
    exchange = tmp_path / "exchange"
    submit = tmp_path / "submit"
    bc = exchange / "bug-candidates"
    bc.mkdir(parents=True)
    src = bc / "seed.sarif"
    src.write_text(json.dumps(make_sarif(rule="uaf", uri="src/z.c")))

    store = make_store(tmp_path / "db", submit=submit, fetch=exchange, clock=clock)
    store.sync()
    assert len(store.list_candidates()) == 1
    # Directed input is not re-published to the submit dir.
    assert not (submit / "bug-candidates").exists()


def test_same_correlation_guid_is_one_candidate(tmp_path, clock):
    store = make_store(tmp_path / "db", clock=clock)
    store.add(write_sarif(tmp_path, "1.sarif.json", corr="C", line=10), agent="a1")
    store.add(write_sarif(tmp_path, "2.sarif.json", corr="C", line=20), agent="a2")
    assert len(store.list_candidates()) == 1


def test_add_finding_flag_based_and_folds_location(tmp_path, clock):
    submit = tmp_path / "submit"
    store = make_store(tmp_path / "db", submit=submit, clock=clock)
    cid = store.add_finding(
        rule="heap-buffer-overflow",
        file_path="src/p.c",
        start_line=142,
        function_name="parse_header",
        message="len used unchecked",
    )
    got = store.get(cid)
    assert got["rule_id"] == "heap-buffer-overflow" and got["status"] == "new"
    loc = got["locations"][0]
    assert loc["file_path"] == "src/p.c" and loc["function_name"] == "parse_header"
    # libCRS built + published the SARIF from flags.
    assert (submit / "bug-candidates" / f"{cid}.sarif.json").exists()
    # passing the same id folds the former add-location: a new versioned location,
    # still one candidate.
    store.add_finding(
        correlation_id=cid, file_path="src/p.c", start_line=155, version="rev2"
    )
    assert sorted((loc["version"] or "") for loc in store.get(cid)["locations"]) == [
        "",
        "rev2",
    ]
    assert len(store.list_candidates()) == 1


def test_properties_indexed_and_queryable(tmp_path, clock):
    store = make_store(tmp_path / "db", clock=clock)
    a = store.add_finding(
        rule="read", file_path="a.c", start_line=1, properties={"subagent": "pov-gen"}
    )
    b = store.add_finding(
        rule="cov", file_path="b.c", start_line=2, properties={"subagent": "seed-gen"}
    )
    # surfaced in get()
    assert store.get(a)["properties"] == {"subagent": "pov-gen"}
    # queryable filter
    assert [
        c["candidate_id"]
        for c in store.list_candidates(properties={"subagent": "pov-gen"})
    ] == [a]
    assert [
        c["candidate_id"]
        for c in store.list_candidates(properties={"subagent": "seed-gen"})
    ] == [b]
    assert store.list_candidates(properties={"subagent": "nobody"}) == []
    # last-writer-wins per key when enriching via --id
    store.add_finding(correlation_id=a, properties={"subagent": "pov-gen-cov"})
    assert store.get(a)["properties"]["subagent"] == "pov-gen-cov"


def test_location_version_defaults_to_revision(tmp_path, clock):
    store = BugCandidateStore(
        tmp_path / "db",
        target_key="proj",
        harness="h1",
        revision="abc123def456",
        clock=clock,
    )
    # no --version given → location.version defaults to the campaign revision
    cid = store.add_finding(rule="read", file_path="a.c", start_line=1)
    assert store.get(cid)["locations"][0]["version"] == "abc123def456"
    # explicit version overrides the default
    cid2 = store.add_finding(rule="read", file_path="b.c", start_line=2, version="rev9")
    assert store.get(cid2)["locations"][0]["version"] == "rev9"


def test_location_repository_defaults_to_target_repo(tmp_path, clock):
    repo = "https://github.com/wolfssl/wolfssl"
    store = BugCandidateStore(
        tmp_path / "db",
        target_key="proj",
        harness="h1",
        revision="abc123def456",
        repository_uri=repo,
        clock=clock,
    )
    # no repository given → location.repository_uri defaults to the target's main_repo
    cid = store.add_finding(rule="read", file_path="a.c", start_line=1)
    loc = store.get(cid)["locations"][0]
    assert loc["repository_uri"] == repo
    assert loc["version"] == "abc123def456"  # revision default still applies
    # explicit repository overrides the default
    cid2 = store.add_finding(
        rule="read", file_path="b.c", start_line=2, repository_uri="https://other/repo"
    )
    assert store.get(cid2)["locations"][0]["repository_uri"] == "https://other/repo"


def test_created_run_is_recorded(tmp_path, clock):
    store = BugCandidateStore(
        tmp_path / "db",
        target_key="proj",
        harness="h1",
        crs_name="crs-a",
        run_id="run-42",
        clock=clock,
    )
    cid = store.add_finding(rule="read", file_path="a.c", start_line=1)
    assert store.get(cid)["created_run"] == "run-42"


def test_versioned_locations_and_version_filter(tmp_path, clock):
    store = make_store(tmp_path / "db", clock=clock)
    store.add(write_sarif(tmp_path, "b.sarif.json", corr="C", rev="v1"), agent="a1")
    store.add_location("C", version="v2", file_path="src/a.c", start_line=99)
    versions = sorted(loc["version"] for loc in store.get("C")["locations"])
    assert versions == ["v1", "v2"]
    assert len(store.list_candidates(version="v2")) == 1
    assert len(store.list_candidates(version="v3")) == 0


def test_claim_is_exclusive_and_lease_expires(tmp_path):
    clk = Clock()
    store = make_store(tmp_path / "db", clock=clk)
    store.add(write_sarif(tmp_path, "b.sarif.json", corr="C"), agent="a1")
    assert store.claim("C", owner="a1", ttl_seconds=100) is True
    assert store.claim("C", owner="a2", ttl_seconds=100) is False
    assert store.get("C")["claim"]["owner"] == "a1"
    assert store.claim("C", owner="a1", ttl_seconds=100) is True  # same owner re-claim
    clk.t = 2000.0  # lease expires
    assert store.get("C")["claim"]["active"] is False
    assert store.claim("C", owner="a2", ttl_seconds=100) is True


def test_cross_crs_shared_db(tmp_path):
    """Two CRS containers share one DB file: state and claims are consistent."""
    clk = Clock()
    db = tmp_path / "shared-db"
    a = make_store(db, clock=clk, crs="crs-a")
    b = make_store(db, clock=clk, crs="crs-b")

    a.add(write_sarif(tmp_path, "b.sarif.json", corr="C"), agent="a1")
    # crs-b sees it immediately — no sync/replication
    assert b.get("C")["status"] == "new"

    # cross-CRS atomic claim: a holds it, b cannot take it
    assert a.claim("C", owner="a1", ttl_seconds=100) is True
    assert b.claim("C", owner="b1", ttl_seconds=100) is False

    # Explorations are a monotonic set: both CRSs record an attempt and NEITHER
    # overwrites the other (the failure mode a single mutable status had).
    a.mark_explored("C")
    b.mark_explored("C")
    assert a.get("C")["explored_by"] == ["crs-a", "crs-b"]
    # ...and it's idempotent — re-marking doesn't duplicate.
    b.mark_explored("C")
    assert b.get("C")["explored_by"] == ["crs-a", "crs-b"]

    # b's PoV is visible to a, with provenance
    b.mark_pov("C", "povhash123")
    got = a.get("C")
    assert got["status"] == "pov_generated"
    assert got["pov"]["ref"] == "povhash123" and got["pov"]["crs"] == "crs-b"


def test_not_explored_by_me_filter(tmp_path, clock):
    """The dispatcher/pov-gen query: candidates my CRS hasn't tried yet."""
    db = tmp_path / "db"
    a = make_store(db, clock=clock, crs="crs-a")
    b = make_store(db, clock=clock, crs="crs-b")
    c1 = a.add_finding(rule="r1", file_path="a.c", start_line=1)
    c2 = a.add_finding(rule="r2", file_path="b.c", start_line=2)
    a.mark_explored(c1)

    # crs-a already tried c1, so only c2 is left for it
    assert [d["candidate_id"] for d in a.list_candidates(not_explored_by="me")] == [c2]
    # crs-b hasn't tried either
    assert len(b.list_candidates(not_explored_by="me")) == 2
    assert [d["candidate_id"] for d in b.list_candidates(explored_by="crs-a")] == [c1]


def test_has_pov_filter_and_derived_status(tmp_path, clock):
    store = make_store(tmp_path / "db", clock=clock)
    c1 = store.add_finding(rule="r1", file_path="a.c", start_line=1)
    c2 = store.add_finding(rule="r2", file_path="b.c", start_line=2)
    store.mark_pov(c2, "hash-abc")

    assert [d["candidate_id"] for d in store.list_candidates(has_pov=True)] == [c2]
    assert [d["candidate_id"] for d in store.list_candidates(has_pov=False)] == [c1]
    assert store.get(c1)["status"] == "new"
    assert store.get(c2)["status"] == "pov_generated"
    with pytest.raises(ValueError):
        store.mark_pov(c1, "")  # a pov mark without provenance is meaningless


def test_migration_v1_status_becomes_exploration(tmp_path, clock):
    """A legacy judgment status means only: that CRS looked at it. Keep the fact."""
    db = tmp_path / "db"
    db.mkdir(parents=True, exist_ok=True)
    # Hand-build a v1-shaped DB: status column, no explorations/pov provenance.
    conn = sqlite3.connect(str(db / "index.sqlite"))
    conn.executescript(
        """
        CREATE TABLE candidates (
            target_key TEXT NOT NULL, harness TEXT NOT NULL,
            correlation_id TEXT NOT NULL, rule_id TEXT, severity TEXT, summary TEXT,
            harness_name TEXT, pov_ref TEXT, partial_fingerprints TEXT,
            status TEXT NOT NULL DEFAULT 'new', alias_of TEXT, claim_owner TEXT,
            claim_lease_expires REAL, claim_ts REAL, created_crs TEXT,
            created_agent TEXT, created_run TEXT, created_at REAL, updated_at REAL,
            PRIMARY KEY (target_key, harness, correlation_id));
        INSERT INTO candidates (target_key, harness, correlation_id, status,
            created_crs, created_at, updated_at)
        VALUES ('proj_v1', 'h1', 'OLD-FP', 'false_positive', 'crs-old', 1.0, 1.0),
               ('proj_v1', 'h1', 'OLD-NEW', 'new', 'crs-old', 1.0, 1.0);
        """
    )
    conn.commit()
    conn.close()

    store = make_store(db, clock=clock)  # opening runs the migration
    # judgment dropped, fact preserved
    assert store.get("OLD-FP")["explored_by"] == ["crs-old"]
    assert store.get("OLD-FP")["status"] == "new"  # no PoV -> new, not false_positive
    # a candidate nobody touched stays untouched
    assert store.get("OLD-NEW")["explored_by"] == []
    # the status column is gone
    cols = {r[1] for r in store.conn.execute("PRAGMA table_info(candidates)")}
    assert "status" not in cols and "pov_crs" in cols


def test_read_scope_widens_across_harnesses_and_projects(tmp_path):
    """Reads can borrow cross-domain knowledge; writes stay in the CRS's own scope."""
    clk = Clock()
    db = tmp_path / "global-db"
    # same project build (target_key), two different harnesses
    h1 = make_store(db, clock=clk, target_key="projA", harness="falcon_fuzz")
    h2 = make_store(db, clock=clk, target_key="projA", harness="pkcs12_parse_fuzz")
    # a different project entirely
    other = make_store(db, clock=clk, target_key="projB", harness="h9")

    c1 = h1.add_finding(rule="r1", file_path="falcon.c", start_line=1)
    c2 = h2.add_finding(rule="r2", file_path="pkcs12.c", start_line=2)
    c3 = other.add_finding(rule="r3", file_path="other.c", start_line=3)

    # default: only my own harness
    assert [d["candidate_id"] for d in h1.list_candidates()] == [c1]
    # project: all harnesses of this target, but not other projects
    proj = h1.list_candidates(scope="project")
    assert sorted(d["candidate_id"] for d in proj) == sorted([c1, c2])
    # all: everything in the global DB
    assert len(h1.list_candidates(scope="all")) == 3
    assert c3 in [d["candidate_id"] for d in h1.list_candidates(scope="all")]

    # borrowed rows are flagged foreign, own rows are not
    by_id = {d["candidate_id"]: d for d in h1.list_candidates(scope="all")}
    assert by_id[c1]["foreign"] is False
    assert by_id[c2]["foreign"] is True and by_id[c3]["foreign"] is True

    # a foreign row still carries its real detail (the helpers must use the ROW's
    # scope, not the store's — else these come back empty)
    assert by_id[c2]["locations"][0]["file_path"] == "pkcs12.c"
    assert by_id[c2]["harness"] == "pkcs12_parse_fuzz"
    assert by_id[c2]["rule_id"] == "r2"

    # get() widens too
    assert h1.get(c2) is None  # not in my harness
    assert h1.get(c2, scope="project")["candidate_id"] == c2
    assert h1.get(c3, scope="all")["candidate_id"] == c3

    with pytest.raises(ValueError):
        h1.list_candidates(scope="bogus")


def test_writes_never_cross_scope(tmp_path, clock):
    """Widened reads hand out foreign ids; mutating one must fail loudly.

    Otherwise mark_pov silently no-ops (UPDATE matching zero rows) and
    mark_explored inserts a row referencing a candidate absent from this scope.
    """
    db = tmp_path / "db"
    h1 = make_store(db, clock=clock, target_key="projA", harness="falcon_fuzz")
    h2 = make_store(db, clock=clock, target_key="projA", harness="pkcs12_parse_fuzz")
    cid = h2.add_finding(rule="r", file_path="pkcs12.c", start_line=1)

    # h1 can SEE it...
    assert h1.get(cid, scope="project")["candidate_id"] == cid
    # ...but cannot mutate it.
    for mutate in (
        lambda: h1.mark_explored(cid),
        lambda: h1.mark_pov(cid, "hash123"),
        lambda: h1.note(cid, "hi"),
        lambda: h1.claim(cid),
    ):
        with pytest.raises(KeyError):
            mutate()

    # the real row is untouched, and no dangling rows were created
    got = h2.get(cid)
    assert got["status"] == "new" and got["explored_by"] == []
    assert h1.conn.execute("SELECT COUNT(*) FROM explorations").fetchone()[0] == 0


def test_global_db_scoped_by_target_and_harness(tmp_path):
    """One global DB file; each CRS only sees its own target_key+harness rows."""
    clk = Clock()
    db = tmp_path / "global-db"
    a = make_store(db, clock=clk, target_key="projA", harness="h1")
    b = make_store(db, clock=clk, target_key="projB", harness="h1")
    c = make_store(db, clock=clk, target_key="projA", harness="h2")

    a.add(write_sarif(tmp_path, "a.sarif.json", corr="X"), agent="a1")
    # Different target (B) and different harness (C) do not see A's candidate.
    assert a.get("X") is not None
    assert b.get("X") is None
    assert c.get("X") is None
    assert a.list_candidates() and not b.list_candidates() and not c.list_candidates()

    # Same correlation id can exist independently under another scope.
    b.add(write_sarif(tmp_path, "b.sarif.json", corr="X", rule="uaf"), agent="b1")
    assert a.get("X")["rule_id"] == "CWE-787"
    assert b.get("X")["rule_id"] == "uaf"
    # A claim in one scope does not affect the same id in another scope.
    assert a.claim("X", owner="a1") is True
    assert b.claim("X", owner="b1") is True


def test_release(tmp_path):
    clk = Clock()
    store = make_store(tmp_path / "db", clock=clk)
    store.add(write_sarif(tmp_path, "b.sarif.json", corr="C"), agent="a1")
    store.claim("C", owner="a1", ttl_seconds=100)
    store.release("C", owner="a1")
    assert store.get("C")["claim"]["active"] is False
    assert store.claim("C", owner="a2", ttl_seconds=100) is True


def test_claimed_filter(tmp_path):
    clk = Clock()
    store = make_store(tmp_path / "db", clock=clk)
    store.add(write_sarif(tmp_path, "1.sarif.json", corr="A"), agent="a1")
    store.add(write_sarif(tmp_path, "2.sarif.json", corr="B"), agent="a1")
    store.claim("A", owner="a1", ttl_seconds=100)
    assert [c["candidate_id"] for c in store.list_candidates(claimed=True)] == ["A"]
    assert [c["candidate_id"] for c in store.list_candidates(claimed=False)] == ["B"]


def test_merge_alias_resolution(tmp_path, clock):
    store = make_store(tmp_path / "db", clock=clock)
    store.add(write_sarif(tmp_path, "1.sarif.json", corr="A"), agent="a1")
    store.add(write_sarif(tmp_path, "2.sarif.json", corr="B"), agent="a1")
    store.merge("B", "A")
    assert store.get("B")["candidate_id"] == "A"  # alias resolves to canonical
    assert [c["candidate_id"] for c in store.list_candidates()] == ["A"]
    store.mark_pov("B", "povhash")  # mutation via alias lands on canonical
    assert store.get("A")["status"] == "pov_generated"


def test_claim_unknown_candidate_raises(tmp_path, clock):
    store = make_store(tmp_path / "db", clock=clock)
    with pytest.raises(KeyError):
        store.claim("nope", owner="a1")


def test_watch_cursor(tmp_path, clock):
    store = make_store(tmp_path / "db", clock=clock)
    store.add(write_sarif(tmp_path, "b.sarif.json", corr="C"), agent="a1")
    store.mark_explored("C")
    store.claim("C", owner="a1")
    evs = list(store.watch(since_seq=0, candidate_id="C"))
    types = [e["type"] for e in evs]
    assert types == ["created", "explored", "claim"]
    last = evs[-1]["seq"]
    assert list(store.watch(since_seq=last, candidate_id="C")) == []
    # type filter
    only = list(store.watch(since_seq=0, candidate_id="C", types=["explored"]))
    assert [e["type"] for e in only] == ["explored"]


def test_detection_guid_provenance(tmp_path, clock):
    store = make_store(tmp_path / "db", clock=clock)
    doc = make_sarif(corr="C")
    doc["runs"][0]["results"][0]["guid"] = "INSTANCE-1"
    p = tmp_path / "g.sarif.json"
    p.write_text(json.dumps(doc))
    store.add(p, agent="a1")
    assert store.get("C")["detection_guids"] == ["INSTANCE-1"]


def test_directed_input_raw_sarif_idempotent(tmp_path, clock):
    exchange = tmp_path / "exchange"
    bc = exchange / "bug-candidates"
    bc.mkdir(parents=True)
    (bc / "seed.sarif").write_text(json.dumps(make_sarif(rule="uaf", uri="src/z.c")))

    store = make_store(tmp_path / "db", fetch=exchange, clock=clock)
    store.sync()
    before = len(store.list_candidates())
    assert before == 1
    # a second identical copy must not create a duplicate candidate
    (bc / "seed.sarif.json").write_text(
        json.dumps(make_sarif(rule="uaf", uri="src/z.c"))
    )
    store.sync()
    assert len(store.list_candidates()) == before


def test_invalid_sarif_rejected(tmp_path, clock):
    store = make_store(tmp_path / "db", clock=clock)
    bad = tmp_path / "bad.sarif.json"
    bad.write_text(json.dumps({"version": "9.9.9", "runs": []}))
    with pytest.raises(ValueError):
        store.add(bad, agent="a1")

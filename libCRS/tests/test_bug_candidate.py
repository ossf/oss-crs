# SPDX-License-Identifier: MIT
import json

import pytest

from libCRS.bug_candidate import BugCandidateStore


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
    db_dir, *, fetch=None, clock=None, crs="crs-a", target_key="proj_v1", harness="h1"
):
    """One BugCandidateStore. Pass the SAME db_dir for two stores to model two
    CRS containers sharing the single global bug-candidate DB."""
    return BugCandidateStore(
        db_dir,
        target_key=target_key,
        harness=harness,
        fetch_dir=fetch,
        crs_name=crs,
        clock=clock or Clock(),
    )


def test_add_uses_correlation_guid_as_identity(tmp_path, clock):
    store = make_store(tmp_path / "db", clock=clock)
    ids = store.add(
        write_sarif(tmp_path, "b.sarif.json", corr="CORR-1"),
        agent="a1",
        status="exploring",
    )
    assert ids == ["CORR-1"]
    got = store.get("CORR-1")
    assert got["status"] == "exploring"
    assert got["rule_id"] == "CWE-787"
    loc = got["locations"][0]
    assert loc["version"] == "rev1"
    assert loc["start_column"] == 3
    assert loc["repository_uri"] == "https://x/y"


def test_add_mints_guid_when_absent_and_written_back(tmp_path, clock):
    store = make_store(tmp_path / "db", clock=clock)
    ids = store.add(write_sarif(tmp_path, "b.sarif.json"), agent="a1")  # no corr
    assert len(ids) == 1 and ids[0]
    stored = json.loads((store.sarif_dir / f"{ids[0]}.sarif.json").read_text())
    assert stored["runs"][0]["results"][0]["correlationGuid"] == ids[0]


def test_same_correlation_guid_is_one_candidate(tmp_path, clock):
    store = make_store(tmp_path / "db", clock=clock)
    store.add(write_sarif(tmp_path, "1.sarif.json", corr="C", line=10), agent="a1")
    store.add(write_sarif(tmp_path, "2.sarif.json", corr="C", line=20), agent="a2")
    assert len(store.list_candidates()) == 1


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

    a.add(
        write_sarif(tmp_path, "b.sarif.json", corr="C"), agent="a1", status="exploring"
    )
    # crs-b sees it immediately — no sync/replication
    assert b.get("C")["status"] == "exploring"

    # cross-CRS atomic claim: a holds it, b cannot take it
    assert a.claim("C", owner="a1", ttl_seconds=100) is True
    assert b.claim("C", owner="b1", ttl_seconds=100) is False
    # b's set-status is visible to a
    b.set_status("C", "confirmed")
    assert a.get("C")["status"] == "confirmed"


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
    store.set_status("B", "confirmed")  # mutation via alias lands on canonical
    assert store.get("A")["status"] == "confirmed"


def test_claim_unknown_candidate_raises(tmp_path, clock):
    store = make_store(tmp_path / "db", clock=clock)
    with pytest.raises(KeyError):
        store.claim("nope", owner="a1")


def test_watch_cursor(tmp_path, clock):
    store = make_store(tmp_path / "db", clock=clock)
    store.add(write_sarif(tmp_path, "b.sarif.json", corr="C"), agent="a1")
    store.set_status("C", "confirmed")
    store.claim("C", owner="a1")
    evs = list(store.watch(since_seq=0, candidate_id="C"))
    types = [e["type"] for e in evs]
    assert types == ["created", "status", "claim"]
    last = evs[-1]["seq"]
    assert list(store.watch(since_seq=last, candidate_id="C")) == []
    # type filter
    only = list(store.watch(since_seq=0, candidate_id="C", types=["status"]))
    assert [e["type"] for e in only] == ["status"]


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

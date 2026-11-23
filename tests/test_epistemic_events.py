"""Append-only hash-chained event log with provenance (R1)."""

import sqlite3

import pytest

from halibut.data.store import Store
from halibut.epistemic.events import GENESIS, EventLog


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "s.sqlite")
    yield s
    s.close()


def test_chain_verifies_and_carries_provenance(store):
    log = EventLog(store)
    h1 = log.append("claim.accepted", "EI", {"claim_type": "forecast"}, run_id="r1", subject_id="c:1",
                    provenance={"model": "m", "snapshot": "abc"}, commit=True)
    h2 = log.append("promotion.approved", "L8", {"component": "x"}, provenance={"by": "founder"}, commit=True)
    ev = log.read()
    assert [e["hash"] for e in ev] == [h1, h2] and ev[0]["prev_hash"] == GENESIS and ev[1]["prev_hash"] == h1
    assert ev[0]["provenance"] == {"model": "m", "snapshot": "abc"} and ev[0]["payload"] == {"claim_type": "forecast"}
    assert log.verify() == {"ok": True, "events": 2, "first_bad_seq": None}
    assert [e["event_type"] for e in log.read(run_id="r1")] == ["claim.accepted"]


def test_append_only_and_tamper_evident(store):
    log = EventLog(store)
    for i in range(3):
        log.append("llm.call", "L1", {"i": i}, commit=True)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.conn.execute("UPDATE events_log SET payload_json='{}' WHERE seq=2")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.conn.execute("DELETE FROM events_log WHERE seq=3")
    # even bypassing the triggers, an edit is detected by the chain
    store.conn.execute("DROP TRIGGER events_no_update")
    store.conn.execute("""UPDATE events_log SET payload_json='{"i":99}' WHERE seq=2""")
    assert log.verify() == {"ok": False, "events": 1, "first_bad_seq": 2}


def test_rejects_unknown_layers_and_undotted_types(store):
    log = EventLog(store)
    with pytest.raises(ValueError):
        log.append("claim.accepted", "L9", {})
    with pytest.raises(ValueError):
        log.append("accepted", "EI", {})


def test_pipeline_emits_events_for_calls_claims_and_promotions(store):
    from halibut.learning.memory import LearningMemory
    mem = LearningMemory(store)
    kid = mem.submit("lesson", "s", "engine:t", {"tags": ["t"]}, [])
    mem.set_status(kid, "inconclusive", "engine:t", effective_at="2025-01-01")
    types = [e["event_type"] for e in EventLog(store).read()]
    assert "knowledge.status_changed" in types and EventLog(store).verify()["ok"]

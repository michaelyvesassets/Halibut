"""Typed claim ledger: type rules, provenance, temporal visibility, tenancy, immutability (R1)."""

import sqlite3

import pytest

from halibut.data.store import Store
from halibut.epistemic.claims import ClaimLedger, ClaimRejected
from halibut.epistemic.events import EventLog

AS_OF = "2024-05-23T23:59:59Z"
FORECAST = {"model": {"id": "intel.prediction_policy", "version": "level/empirical"}, "point": 0.10,
            "interval80": [0.08, 0.12], "resolution": {"quantity": {"kind": "share"}, "resolvable_after": "2024-12-31"}}
HYP = {"origin": "original", "implications": [{"metric": "segment_share:byd", "direction": "above"}],
       "refutation_criteria": "Refuted if BYD's segment share is not above 1.2% over the next 2 quarters."}


@pytest.fixture
def led(tmp_path):
    s = Store(tmp_path / "s.sqlite")
    yield ClaimLedger(s)
    s.close()


def submit(led, t, payload, by="engine:test", evidence=("metric:a",), **kw):
    return led.submit(t, kw.pop("statement", f"a {t}"), by, as_of=kw.pop("as_of", AS_OF), payload=payload,
                      provenance={"test": True}, evidence=list(evidence), **kw)


def test_each_type_has_its_own_rules(led):
    cid, p = submit(led, "forecast", FORECAST)
    assert cid and not p
    assert submit(led, "hypothesis", HYP, by="agent:x")[0]
    assert submit(led, "explanation", {"code": "seasonality"})[0]
    assert submit(led, "simulated_outcome", {"scenario": {"names": ["control"]}, "assumptions": ["synthetic"],
                                             "conditional": True, "calibration": "synthetic_uncalibrated"}, evidence=())[0]
    assert submit(led, "strategic_proposal", {"levers": ["price"], "requires_approval": True,
                                              "compliance": {"status": "pending_review"}}, by="agent:s")[0]
    assert submit(led, "observation", {"evidence_id": "obs:1"}, evidence=())[0]
    # rejections, each with its reason
    assert "point must lie inside" in " ".join(submit(led, "forecast", {**FORECAST, "point": 0.5})[1])
    assert "refutation criteria" in " ".join(submit(led, "hypothesis", {**HYP, "refutation_criteria": ""}, by="agent:x")[1])
    assert "not a forecast" in " ".join(submit(led, "hypothesis", {**HYP, "point": 0.1}, by="agent:x")[1])
    assert "forecast fields" in " ".join(submit(led, "explanation", {"point": 0.1, "interval80": [0, 1]})[1])
    assert "never scored" in " ".join(submit(led, "simulated_outcome", {
        "scenario": {"n": 1}, "assumptions": ["a"], "conditional": True, "calibration": "calibrated",
        "resolution": {"quantity": {}}}, evidence=())[1])
    bad = submit(led, "strategic_proposal", {"levers": ["fake_reviews"], "requires_approval": True,
                                             "compliance": {"status": "pending_review"}}, statement="post fake reviews")[1]
    assert any("lawful levers" in x for x in bad) and any("prohibited" in x for x in bad)
    assert "agents" in " ".join(submit(led, "observation", {"evidence_id": "obs:1"}, by="agent:x", evidence=())[1])


def test_agents_cannot_issue_forecasts_but_may_keep_labelled_estimates(led):
    _, p = submit(led, "forecast", FORECAST, by="agent:llm_hypotheses:v1")
    assert any("may not issue official forecasts" in x for x in p)
    est = {"value": 0.11, "sd": 0.01, "method": "llm judgement"}
    assert "candidate = true" in " ".join(submit(led, "estimate", est, by="agent:x")[1])
    cid, p = submit(led, "estimate", {**est, "candidate": True}, by="agent:x")
    assert cid and not p and led.get(cid)["status"] == "proposed"
    # agents may only create proposed claims
    assert "only create 'proposed'" in " ".join(submit(led, "hypothesis", HYP, by="agent:x", status="survived")[1])


def test_rejections_are_recorded_not_dropped(led):
    _, p = submit(led, "forecast", {**FORECAST, "interval80": None}, run_id="r1")
    row = led.conn.execute("SELECT stage, outcome, failure_class, problems_json FROM validation_records WHERE run_id='r1'").fetchone()
    assert tuple(row)[:3] == ("ledger", "rejected", "ledger.forecast") and "interval80" in row[3]
    assert [e["event_type"] for e in EventLog(led.store).read(run_id="r1")] == ["agent_output.rejected"]
    with pytest.raises(ClaimRejected):
        submit(led, "forecast", {**FORECAST, "interval80": None}, strict=True)


def test_temporal_integrity(led):
    # evidence must exist as of the claim's date; forecasts must resolve after it; claims are invisible before they exist
    exists = lambda e: e == "metric:a"  # noqa: E731
    assert "not available" in " ".join(submit(led, "forecast", FORECAST, evidence=["metric:b"], exists=exists)[1])
    late = {**FORECAST, "resolution": {"quantity": {"kind": "share"}, "resolvable_after": "2024-01-01"}}
    assert "no hindsight" in " ".join(submit(led, "forecast", late)[1])
    assert "available before" in " ".join(submit(led, "forecast", FORECAST, available_at="2024-01-01")[1])
    cid, _ = submit(led, "hypothesis", HYP, by="agent:x", exists=exists)
    assert led.get(cid, as_of="2024-05-22") is None and led.get(cid, as_of="2024-05-24")["claim_id"] == cid
    led.set_status(cid, "refuted", "engine:scorer", effective_at="2025-02-01", reason="implication failed")
    assert led.get(cid, as_of="2024-12-31")["status"] == "proposed"
    assert led.get(cid, as_of="2025-03-01")["status"] == "refuted"
    assert [c["claim_id"] for c in led.as_of("2024-06-01", claim_type="hypothesis")] == [cid]


def test_validation_requires_an_evaluation_and_a_person_or_engine(led):
    cid, _ = submit(led, "hypothesis", HYP, by="agent:x")
    with pytest.raises(ValueError, match="requires an evaluation"):
        led.set_status(cid, "validated", "engine:x", effective_at="2025-01-01", reason="looks right")
    with pytest.raises(ValueError):
        led.set_status(cid, "validated", "agent:x", effective_at="2025-01-01", reason="r", evaluation_id="eval:1")


def test_tenant_isolation(led):
    a, _ = submit(led, "hypothesis", HYP, by="agent:x", tenant="client-a")
    b, _ = submit(led, "hypothesis", {**HYP, "origin": "template_selection"}, by="agent:x", tenant="client-b")
    pub, _ = submit(led, "forecast", FORECAST)
    seen_a = {c["claim_id"] for c in led.as_of("2030-01-01", tenant="client-a")}
    assert a in seen_a and pub in seen_a and b not in seen_a
    assert led.get(b, tenant="client-a") is None and led.get(b, tenant="client-b")["claim_id"] == b
    assert "invalid tenant" in " ".join(submit(led, "forecast", FORECAST, tenant="Client A!")[1])


def test_claims_are_immutable_idempotent_and_duplicates_linked(led):
    cid, _ = submit(led, "forecast", FORECAST, run_id="r")
    assert submit(led, "forecast", FORECAST, run_id="r")[0] == cid  # identical claim: same id, one row
    other, _ = submit(led, "forecast", FORECAST, run_id="r", statement="same forecast, different wording")
    assert other != cid
    assert led.conn.execute("SELECT relation, to_id FROM claim_links WHERE from_id=?", (other,)).fetchone()[:] == ("duplicates", cid)
    submit(led, "forecast", {**FORECAST, "point": 9})  # creates a validation record (rejected claim)
    for sql in ("UPDATE claims SET statement='x'", "DELETE FROM claims", "DELETE FROM claim_status_history",
                "UPDATE validation_records SET outcome='rejected'"):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            led.conn.execute(sql)
    assert led.scorable(led.get(cid)) and not led.scorable(led.get(submit(led, "explanation", {"code": "x"})[0]))

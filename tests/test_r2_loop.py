"""R2 intelligence loop, end to end and failure cases (offline, mock agents)."""

import hashlib
import json
from pathlib import Path

import pytest

from halibut.agents.mock_loop import responder as mock_responder
from halibut.agents.routing import Router
from halibut.control.approvals import AuthorizationError
from halibut.data.providers.base import register
from halibut.data.store import Store
from halibut.epistemic.claims import ClaimLedger
from halibut.epistemic.events import EventLog
from halibut.investigation import artifact as art
from halibut.investigation.loop import run_investigation

from .test_intel import MARKET, routes
from .test_providers import FakeNet

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures"
UK = dict(as_of="2025-05-23", evaluate_as_of="2026-11-23")
MEASUREMENT = """
[measurement]
category_key = "road_vehicles.cars"
universe = "first UK registrations of cars (fixture)"
unit = "vehicles registered (count)"
period = "quarter"
share_basis = "share of all first registrations; segment shares within the fuel segment"
"""


@pytest.fixture(scope="module")
def uk_store(tmp_path_factory):
    from halibut.intel.workflow import investigate
    tmp = tmp_path_factory.mktemp("uk")
    (tmp / "m.toml").write_text(MARKET + MEASUREMENT)
    store = Store(tmp / "s.sqlite")
    register(store, ROOT / "configs" / "data" / "providers.toml")
    investigate(store, tmp / "m.toml", tmp / "out", tmp / "raw", log=lambda *_: None, transport=FakeNet(routes()))
    store.close()
    return tmp


def fresh(tmp_path, src):
    import sqlite3
    a, b = sqlite3.connect(src / "s.sqlite"), sqlite3.connect(tmp_path / "s.sqlite")
    a.backup(b)
    a.close(), b.close()
    return Store(tmp_path / "s.sqlite")


def run(store, market, router=None, **kw):
    return run_investigation(store, market, router=router, log=lambda *_: None, **kw)


def items(doc, stage):
    return doc["stages"][stage]


def test_uk_loop_end_to_end(uk_store, tmp_path):
    store = fresh(tmp_path, uk_store)
    doc = run(store, uk_store / "m.toml", **UK)
    assert doc["status"] == "completed" and art.validate(doc) == []
    hy = items(doc, "hypotheses")
    by_ref = {h["data"]["ref"]: h for h in hy}
    # original hypotheses with every required part; a restatement flagged; a duplicate dropped; an untestable kept
    assert {"H1", "H2", "H3", "H4"} == set(by_ref) and "H5" not in by_ref
    for h in hy:
        d = h["data"]
        assert d["origin"] == "original" and d["mechanism"]["pathway"] and d["alternative_explanations"]
        assert d["refutation_criteria"] and d["assumptions"] and d["uncertainty"]["level"]
        assert [ln for ln in h["links"] if ln["relation"] == "cites"]
    assert by_ref["H4"]["data"]["novelty"] == "restatement"
    assert sum(1 for h in hy if h["data"]["novelty"] == "original") == 3
    assert any(f["failure_class"] == "duplicate.hypothesis" for f in doc["failures"]["items"])
    assert by_ref["H3"]["status"] == "untested"
    assert [t for t in items(doc, "tests") if t["status"] == "not_testable"]
    assert any(it["type"] == "question" for it in items(doc, "lessons"))
    # tests resolve at the evaluation date; statuses follow the rule
    assert items(doc, "test_results") and all(r["status"] in ("passed", "failed", "pending") for r in items(doc, "test_results"))
    assert {h["status"] for h in hy} <= {"survived", "refuted", "inconclusive", "untested", "proposed"}
    # forecasts only from validated registered models; unvalidated outputs are labelled estimates
    validated = {m["id"] for m in items(doc, "model_specs") if m["status"] == "validated"}
    for f in items(doc, "forecasts"):
        assert f["produced_by"].startswith("engine:model:") and f["data"]["model"]["version"] in validated
        assert f["data"]["score"]["status"] in ("scored", "pending")
    for e in items(doc, "estimates"):
        assert "NOT an official forecast" in e["data"]["label"]
    assert any(m["status"] == "rejected" for m in items(doc, "model_specs"))  # e.g. the logistic on a share gap
    # strategy: competing options with every field; simulations per competitor response; a non-executable protocol
    opts = items(doc, "strategic_options")
    assert len(opts) >= 2
    for o in opts:
        d = o["data"]
        assert d["intended_effect"] and d["supporting_evidence"] and d["assumptions"] and d["competitor_responses"]
        assert "known" in d["cost"] and d["risks"] and d["uncertainty"] and d["success_criteria"]
        assert d["requires_approval"] and d["executable"] is False and d["compliance"]["status"] == "pending_human_review"
        sims = [ln["to"] for ln in o["links"] if ln["relation"] == "simulated_by"]
        assert len(sims) == 1 + len(d["competitor_responses"])
    assert all(s["data"]["baseline_status"] in ("validated", "unvalidated") for s in items(doc, "simulations"))
    proto = doc["experiment_protocol"]
    assert proto["status"] == "draft_not_executable" and proto["data"]["pre_registration_hash"]
    assert doc["approvals"][0]["kind"] == "experiment" and doc["approvals"][0]["status"] == "requested"
    # costs and coordination: exactly 3 LLM calls, nothing paid
    assert doc["agents"]["costs"]["calls"] == 3 and doc["agents"]["costs"]["actual_cost_usd"] == 0
    # ledger, audit trail, storage
    led = ClaimLedger(store)
    counts = led.counts(doc["input_id"])
    assert {"explanation", "hypothesis", "critique", "forecast", "estimate", "simulated_outcome", "strategic_proposal",
            "assumption", "lesson"} <= set(counts)
    assert all(not c["produced_by"].startswith("agent:") for c in led.as_of("2030-01-01", claim_type="forecast"))
    assert store.conn.execute("SELECT COUNT(*) FROM validation_records WHERE stage='ledger'").fetchone()[0] == 0
    assert EventLog(store).verify()["ok"]
    stored = json.loads(store.conn.execute("SELECT artifact_json FROM investigations").fetchone()[0])
    assert stored["content_hash"] == doc["content_hash"]
    store.close()


def test_every_stage_links_to_its_predecessors(uk_store, tmp_path):
    store = fresh(tmp_path, uk_store)
    doc = run(store, uk_store / "m.toml", **UK)
    ids = {it["id"] for st in doc["stages"].values() for it in st}
    hyp = {h["id"] for h in items(doc, "hypotheses")}
    assert all(any(ln["to"] in hyp for ln in t["links"]) for t in items(doc, "tests"))
    tests = {t["id"] for t in items(doc, "tests")}
    assert all(any(ln["to"] in tests for ln in r["links"]) for r in items(doc, "test_results"))
    assert all(any(ln["to"] in ids for ln in f["links"] if ln["relation"] == "from_model") for f in items(doc, "forecasts"))
    assert all(any(ln["to"] in hyp for ln in o["links"]) for o in items(doc, "strategic_options"))
    assert any(ln["to"] in {o["id"] for o in items(doc, "strategic_options")} for ln in doc["experiment_protocol"]["links"])
    assert all(any(ln["to"] in hyp for ln in les["links"]) for les in items(doc, "lessons"))
    for st in doc["stages"].values():
        for it in st:
            assert it["id"] and it["type"] in art.ITEM_TYPES and it["produced_by"] and it["as_of"]
    store.close()


def test_temporal_integrity_and_strategy_sees_no_outcomes(uk_store, tmp_path):
    store = fresh(tmp_path, uk_store)
    seen = {}

    def spy(role, p):
        seen[role] = p
        return mock_responder(role, p)
    doc = run(store, uk_store / "m.toml", router=Router("mock", store, "x", responders=spy), **UK)
    last = doc["evidence"]["panel"]["last_period"]
    assert last <= "2024-Q4"  # 2025-Q1 not published by 2025-05-23
    for t in items(doc, "tests"):
        if t["data"]["spec"]:
            assert min(t["data"]["spec"]["target_periods"]) > last
    for r in items(doc, "test_results"):
        if r["data"]["available_at"]:
            assert doc["objective"]["as_of"] < r["data"]["available_at"] <= doc["objective"]["evaluate_as_of"]
    text = json.dumps(seen["strategist"])
    assert '"passed"' not in text and '"failed"' not in text and "realized" not in text
    store.close()


def test_survival_is_not_causal_proof_and_reproducible(tmp_path):
    store = Store(tmp_path / "s.sqlite")
    m = FIX / "market_plant_milk_panel.toml"
    a = run(store, m, as_of="2024-06-30", evaluate_as_of="2025-06-30")
    n_claims = store.conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
    b = run(store, m, as_of="2024-06-30", evaluate_as_of="2025-06-30")
    assert a["content_hash"] == b["content_hash"] and a["investigation_id"] != b["investigation_id"]
    assert store.conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == n_claims  # identical claims are not re-recorded
    assert b["agents"]["costs"]["cache_hits"] == 3
    surv = [h for h in items(a, "hypotheses") if h["status"] == "survived"]
    assert surv and all("not causal proof" in h["data"]["status_detail"]["reason"] for h in surv)
    assert any("not causal proof" in les["label"] for les in items(a, "lessons"))
    store.close()


def test_non_vehicle_market_runs_on_an_industry_neutral_panel(tmp_path):
    store = Store(tmp_path / "s.sqlite")
    m = FIX / "market_plant_milk_panel.toml"
    doc = run(store, m, as_of="2024-06-30", evaluate_as_of="2025-06-30")
    assert doc["status"] == "completed" and art.validate(doc) == []
    p = doc["evidence"]["panel"]
    assert p["source"] == "csv_panel" and p["last_period"] == "2024-Q1"  # Q2 published 60 days after quarter end
    assert p["snapshot"]["sha256"] == hashlib.sha256((FIX / "plant_milk_panel.csv").read_bytes()).hexdigest()
    assert doc["evidence"]["coverage"]["contract"]["valid"] and items(doc, "hypotheses") and items(doc, "strategic_options")
    assert "dft" not in json.dumps(doc["world_model"]).lower()
    store.close()


def test_market_without_transaction_evidence_is_reported_not_forced(tmp_path):
    store = Store(tmp_path / "s.sqlite")
    doc = run(store, FIX / "market_non_vehicle.toml")
    assert doc["status"] == "insufficient_evidence" and "no transaction panel" in doc["stop_reason"]
    assert art.validate(doc) == [] and all(not v for v in doc["stages"].values())
    store.close()


@pytest.mark.parametrize("reply,expect", [
    ("truncated", "unusable"), ({"hypotheses": [], "note": "x"}, "violates its schema"), ({"explanations": []}, "violates")])
def test_unusable_generator_reply_stops_the_investigation(uk_store, tmp_path, reply, expect):
    from halibut.agents.llm import LLMResponse
    store = fresh(tmp_path, uk_store)

    class Bad:
        name, model = "mock", "bad-v1"

        def complete(self, system, user, *, role, max_output_tokens=None, **_):
            if reply == "truncated":
                r = LLMResponse('{"hypo', None, 10, 5000, True, "bad-v1", "mock", 0.0)
                r.stop_reason = "max_tokens"
                return r
            r = LLMResponse(json.dumps(reply), reply, 10, 10, True, "bad-v1", "mock", 0.0)
            r.stop_reason = "end_turn"
            return r
    router = Router("mock", store, "x")
    router._mock = Bad()
    doc = run(store, uk_store / "m.toml", router=router, **UK)
    assert doc["status"] == "stopped" and expect in doc["stop_reason"] and not items(doc, "hypotheses")
    assert doc["agents"]["costs"]["calls"] == 1 and art.validate(doc) == []
    assert store.conn.execute("SELECT COUNT(*) FROM validation_records").fetchone()[0] >= 1
    store.close()


def test_fabricated_evidence_and_invalid_options_are_rejected_not_repaired(uk_store, tmp_path):
    store = fresh(tmp_path, uk_store)

    def bad_strategy(role, p):
        out = mock_responder(role, p)
        if role == "strategist":
            o = out["options"]
            o[0]["cost"] = {"known": False, "low": 5, "high": 9, "currency": "unknown", "basis": "x"}   # numbers but unknown
            o[1]["intended_effect"]["magnitude_low_pp"] = 3.0                                       # low > high
            o[2]["title"] = "Seed fake reviews of the rival"                                         # prohibited tactic
        return out
    doc = run(store, uk_store / "m.toml", router=Router("mock", store, "x", responders=bad_strategy), faults=True, **UK)
    f = doc["failures"]["items"]
    assert any(x["failure_class"] == "fabricated.evidence_id" and x["subject"] == "hypothesis" for x in f)
    opt_fail = [x for x in f if x["subject"] == "strategic_option"]
    reasons = " ".join(p for x in opt_fail for p in x["problems"])
    assert len(opt_fail) == 3 and "unknown but numbers" in reasons and "exceeds" in reasons and "prohibited" in reasons
    assert not items(doc, "strategic_options") and doc["experiment_protocol"] is None
    store.close()


def test_paid_agents_need_a_granted_approval_and_a_cap(uk_store, tmp_path):
    from halibut.control import approvals as ap
    store = fresh(tmp_path, uk_store)
    m = uk_store / "m.toml"
    with pytest.raises(AuthorizationError, match="max-usd"):
        run(store, m, agents="anthropic", **UK)
    with pytest.raises(AuthorizationError, match="required"):
        run(store, m, agents="anthropic", max_usd=0.1, **UK)
    aid = ap.request(store, "paid_run", "investigation:test_market", "founder", max_usd=0.1)
    with pytest.raises(AuthorizationError, match="requested"):
        run(store, m, agents="anthropic", approval_id=aid, max_usd=0.1, **UK)
    ap.decide(store, aid, "granted", "reviewer")
    with pytest.raises(AuthorizationError, match="exceeds"):
        run(store, m, agents="anthropic", approval_id=aid, max_usd=0.2, **UK)
    assert store.conn.execute("SELECT COUNT(*) FROM llm_spend_ledger").fetchone()[0] == 0
    store.close()


def test_result_schema_document_and_read_only_api(uk_store, tmp_path):
    from halibut.command import api
    store = fresh(tmp_path, uk_store)
    doc = run(store, uk_store / "m.toml", **UK)
    s = api.schema()
    assert s["$id"] == art.SCHEMA_ID and set(art.STAGES) == set(s["properties"]["stages"]["properties"])
    published = json.loads((ROOT / "docs" / "architecture" / "schemas" / "investigation_result.v1.schema.json").read_text())
    assert published == s  # the published schema is the code's schema
    rows = api.list_investigations(store)
    assert rows[-1]["investigation_id"] == doc["investigation_id"]
    assert api.get_investigation(store, doc["investigation_id"])["content_hash"] == doc["content_hash"]
    assert api.get_investigation(store, doc["investigation_id"], tenant="client-a") is not None  # public results visible
    assert len(api.get_items(store, doc["investigation_id"], "hypothesis")) == len(items(doc, "hypotheses"))
    assert api.get_items(store, doc["investigation_id"], "experiment_protocol")
    assert api.list_approvals(store)[0]["kind"] == "experiment"
    store.close()

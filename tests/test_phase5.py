import json
import math
from pathlib import Path

import pytest

from halibut.agents.llm import CachedProvider, MockProvider, NotApproved, Pricing, QwenProvider, run_costs, SpendLimitReached
from halibut.agents.mock import responder
from halibut.data.providers.base import register
from halibut.data.store import Store
from halibut.learning.memory import LearningMemory
from halibut.learning.predictions import make_prediction, score_due, score_one
from halibut.validation import check_numbers

from .test_intel import MARKET, routes
from .test_providers import FakeNet

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def world(tmp_path):
    """Store with fixture DfT/World Bank/etc. evidence ingested through the real workflow."""
    from halibut.intel.workflow import investigate
    (tmp_path / "m.toml").write_text(MARKET.replace('as_of = "2026-11-23"', 'as_of = "2026-11-23"'))
    store = Store(tmp_path / "s.sqlite")
    register(store, ROOT / "configs" / "data" / "providers.toml")
    investigate(store, tmp_path / "m.toml", tmp_path / "out", tmp_path / "raw", log=lambda *_: None, transport=FakeNet(routes()))
    yield store, tmp_path / "m.toml"
    store.close()


def test_score_one_proper_scores():
    p = make_prediction({"kind": "share"}, 0.10, 0.01, 0.09, "above", "level", "engine:t", "2025-01-01", "2025-06-01", [])
    lo, hi = p["interval80"]
    assert hi - lo == pytest.approx(2 * 1.2815515655446004 * 0.01)
    inside = score_one(p, 0.105)
    assert inside["in_interval80"] and inside["direction_hit"] and inside["interval_score80"] == pytest.approx(hi - lo)
    assert inside["brier"] == pytest.approx((p["p_direction"] - 1) ** 2)
    outside = score_one(p, 0.13)
    assert not outside["in_interval80"]
    assert outside["interval_score80"] == pytest.approx((hi - lo) + (2 / 0.2) * (0.13 - hi))
    assert score_one(p, 0.08)["direction_hit"] is False


def test_scoring_respects_publication_cutoffs_and_records_lineage(world):
    store, mpath = world
    from halibut.intel.retrieval import StoreEvidenceRetriever
    mem = LearningMemory(store)
    retr = StoreEvidenceRetriever(store, "2024-11-23")
    den = retr.series_ids("dft_veh0160_makes:BodyType=Cars")
    num = [s for s in den if ":Make=BYD:" in s]
    q = {"kind": "share", "numerator": num, "denominator": den, "geo": "GB", "periods": ["2024-Q3", "2024-Q4"]}
    pred = make_prediction(q, 0.2, 0.01, None, None, "level", "engine:test", retr.as_of, "2025-04-30", ["metric:x"],
                           extra={"origin_window": ["2023-Q3", "2023-Q4", "2024-Q1", "2024-Q2"]})
    kid = mem.submit("prediction", "p", "engine:test", {"tags": ["t:1"]}, [], record={"prediction": pred}, available_at=retr.as_of)
    assert score_due(store, mem, "2025-02-01", tags=["t:1"]) == []  # 2024-Q4 published ~2025-04-30: not yet
    res = score_due(store, mem, "2025-06-01", tags=["t:1"])
    assert len(res) == 1 and res[0]["published"] <= "2025-06-01T23:59:59Z"
    row = store.conn.execute("SELECT * FROM prediction_scores WHERE prediction_id=?", (kid,)).fetchone()
    assert row["method"] == "level" and row["origin_as_of"] == retr.as_of and row["outcome_available_at"] <= "2025-06-01T23:59:59Z"
    assert mem.retrieve(tags=["t:1"], kinds=["prediction"])[0]["status"] == "tested"
    assert score_due(store, mem, "2026-01-01", tags=["t:1"]) == []  # never scored twice


def test_failed_hypothesis_prediction_gets_candidate_explanations_not_causes(world):
    store, _ = world
    from halibut.intel.retrieval import StoreEvidenceRetriever
    mem = LearningMemory(store)
    retr = StoreEvidenceRetriever(store, "2024-11-23")
    den = retr.series_ids("dft_veh0160_makes:BodyType=Cars")
    num = [s for s in den if ":Make=BYD:" in s]
    q = {"kind": "share", "numerator": num, "denominator": den, "geo": "GB", "periods": ["2024-Q3", "2024-Q4"]}
    h = mem.submit("hypothesis", "BYD collapses", "agent:x", {"tags": ["t:2"]}, [])
    pred = make_prediction(q, 0.9, 0.001, 0.8, "above", "level", "agent:x", retr.as_of, "2025-04-30", [], hypothesis_id="hyp:1",
                           extra={"origin_window": ["2023-Q3", "2023-Q4", "2024-Q1", "2024-Q2"]})
    pred["hypothesis_knowledge_id"] = h
    mem.submit("prediction", "p2", "agent:x", {"tags": ["t:2"]}, [], record={"prediction": pred}, available_at=retr.as_of)
    res = score_due(store, mem, "2026-11-23", tags=["t:2"])
    assert res[0]["failed"] and "volatility_underestimated" in res[0]["explanations"]
    expl = mem.retrieve(tags=["explanation"], kinds=["hypothesis"])
    assert expl and all(e["status"] == "proposed" for e in expl)
    assert all("not a demonstrated cause" in e["record"]["note"] for e in expl)
    assert mem.retrieve(tags=["t:2"], kinds=["hypothesis"], statuses=["rejected"])[0]["knowledge_id"] == h


def test_lessons_respect_as_of(world):
    store, _ = world
    mem = LearningMemory(store)
    mem.submit("lesson", "learned in 2025", "engine:t", {"tags": ["x"]}, [], available_at="2025-06-01T00:00:00Z")
    assert mem.retrieve(tags=["x"], as_of="2025-01-01") == []
    assert len(mem.retrieve(tags=["x"], as_of="2025-07-01")) == 1


def test_number_check():
    assert check_numbers("BYD rose from 1.24% to 3.19% (+1.95 pp)", {1.24, 3.19, 1.95}) == []
    assert check_numbers("BYD reached 4.19%", {1.24, 3.19}) != []


def test_mock_agents_faults_are_caught_and_costs_accounted(world):
    store, mpath = world
    import dataclasses
    from halibut.intel.market import load_market
    from halibut.intel.replay import make_agents
    from halibut.intel.workflow import analyse
    market = load_market(mpath)
    mem = LearningMemory(store)
    store.conn.execute("INSERT INTO intel_runs VALUES ('r1','m','x','x',NULL,'running','{}')")
    agents = make_agents("mock", store, "r1", faults=True)
    a = analyse(store, market, mem, "r1", None, {}, agents=agents, preview=False)
    problems = [p for h in a["rejected"] for p in h["validation"]["problems"]]
    assert any("does_not_exist" in p for p in problems) and any("does not match any evidence value" in p for p in problems)
    assert all(h["validation"]["passed"] for h in a["accepted"]) and a["accepted"]
    c = a["agent_costs"]
    assert c["calls"] == 3 and c["by_role"] == {"research": 1, "hypothesis": 1, "critic": 1}
    assert c["input_tokens"] > 0 and c["tokens_estimated"] and c["duplicate_tool_calls"] >= 1
    assert any(p["status"] == "pending_approval" for p in c["research"]["pending"])  # network tools never auto-run
    crit = [x for h in a["accepted"] for x in h["critiques"]]
    assert crit and all(x["produced_by"] == "agent:llm_critic:v1" for x in crit)


def test_cache_hits_avoid_repeat_calls(world):
    store, _ = world
    calls = []

    def counting(role, payload):
        calls.append(role)
        return {"hypotheses": []}
    cp = CachedProvider(MockProvider(counting), store)
    r1 = cp.complete("s", 'BUNDLE:{"a": 1}', role="hypothesis")
    r2 = cp.complete("s", 'BUNDLE:{"a": 1}', role="hypothesis")
    assert calls == ["hypothesis"] and not r1.cached and r2.cached and r2.estimated_cost_usd == 0.0
    assert (cp.hits, cp.misses) == (1, 1)


def test_qwen_provider_is_gated(tmp_path):
    """Phase 5 gate (signature updated in the readiness step): approval, prices, key and caps are required."""
    store = Store(tmp_path / "q.sqlite")
    base = {"base_url": "https://example.invalid/v1", "model": "qwen-x", "api_key_env": "HALIBUT_TEST_KEY",
            "input_per_mtok_usd": 1.0, "output_per_mtok_usd": 2.0, "max_spend_usd_total": 0.01, "max_spend_usd_per_run": 0.01}
    with pytest.raises(NotApproved, match="not approved"):
        QwenProvider({**base, "approved": False}, store, "s", environ={"HALIBUT_TEST_KEY": "k"})
    with pytest.raises(NotApproved, match="input_per_mtok_usd"):
        QwenProvider({**base, "approved": True, "input_per_mtok_usd": ""}, store, "s", environ={"HALIBUT_TEST_KEY": "k"})
    with pytest.raises(NotApproved, match="HALIBUT_TEST_KEY"):
        QwenProvider({**base, "approved": True}, store, "s", environ={})
    q = QwenProvider({**base, "approved": True}, store, "s", environ={"HALIBUT_TEST_KEY": "k"},
                     transport=lambda *a: (_ for _ in ()).throw(AssertionError("must not be called")))
    with pytest.raises(SpendLimitReached):
        q.complete("JSON " + "s" * 4000, "u" * 4000, role="hypothesis", max_output_tokens=4000, run_id="s:1")
    assert Pricing(1.0, 2.0).cost(1_000_000, 500_000) == pytest.approx(2.0)
    store.close()


def test_audit_decomposition_is_exact(world):
    store, mpath = world
    from halibut.intel.audit import audit_transactions
    from halibut.intel.market import load_market
    from halibut.intel.retrieval import StoreEvidenceRetriever
    from halibut.intel.signals import transaction_signals
    market = load_market(mpath)
    retr = StoreEvidenceRetriever(store, market.as_of)
    tx = transaction_signals(retr, market, {c.id: f"brand:{c.id}" for c in market.competitors})
    a = audit_transactions(retr, market, tx)
    assert a["all_passed"]
    for d in a["decomposition"].values():
        assert abs(d["residual_pp"]) < 1e-9
        assert d["delta_pp"] == pytest.approx(d["mix_effect_pp"] + d["within_segment_effect_pp"] + d["interaction_pp"])
    assert "imported" in next(c for c in a["checks"] if c["check"] == "universe_definition")["detail"]
    assert "not observed" in a["displacement"]["assessment"]


def test_replay_end_to_end(world):
    store, mpath = world
    from halibut.intel.replay import replay
    origins = ["2023-11-23", "2024-05-23", "2024-11-23", "2025-05-23", "2025-11-23"]
    r = replay(store, mpath, origins, agents="reference", log=lambda *_: None)
    assert r["hypothesis_predictions_scored"] > 0 and r["supported"] + r["refuted"] == r["hypothesis_predictions_scored"]
    assert r["per_origin"][0]["scored_at_origin"] == 0 and any(p["scored_at_origin"] > 0 for p in r["per_origin"][1:])
    assert {e["comparison"].split(" ")[0] for e in r["evaluations"]} >= {"momentum", "last-quarter"}
    for e in r["evaluations"]:
        if "lesson_status" in e:
            assert e["lesson_status"] in ("validated", "rejected", "inconclusive")
            assert e["promotion"] in ("awaiting human approval", "not eligible")
    mem = LearningMemory(store)
    assert mem.active("intel.prediction_policy") is None  # never auto-promoted
    lessons = mem.retrieve(tags=["component:intel.prediction_policy"], kinds=["lesson"])
    assert lessons and all(l["available_at"] for l in lessons)

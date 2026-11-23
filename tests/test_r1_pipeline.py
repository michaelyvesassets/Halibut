"""R1 behaviour of the existing pipeline: claims written, drops and failures recorded, duplicates removed,
structured outputs sent, offline reconstruction, coverage contract (all offline)."""

import json
from pathlib import Path

import pytest

from halibut.data.providers.base import register
from halibut.data.store import Store
from halibut.epistemic.claims import ClaimLedger
from halibut.epistemic.events import EventLog
from halibut.intel.market import load_market
from halibut.world.coverage import check_contract, coverage_report

from .test_intel import MARKET, routes
from .test_providers import FakeNet

ROOT = Path(__file__).resolve().parents[1]
ORIGINS = ["2024-11-23", "2025-05-23", "2025-11-23"]


@pytest.fixture
def world(tmp_path):
    from halibut.intel.workflow import investigate
    (tmp_path / "m.toml").write_text(MARKET)
    store = Store(tmp_path / "s.sqlite")
    register(store, ROOT / "configs" / "data" / "providers.toml")
    investigate(store, tmp_path / "m.toml", tmp_path / "out", tmp_path / "raw", log=lambda *_: None, transport=FakeNet(routes()))
    yield store, tmp_path
    store.close()


def test_replay_writes_typed_claims_and_records_failures(world):
    from halibut.intel.replay import replay
    store, tmp = world
    s = replay(store, tmp / "m.toml", ORIGINS, agents="mock", faults=True, log=lambda *_: None)
    led = ClaimLedger(store)
    counts = led.counts(s["replay_id"])
    assert counts.get("hypothesis", 0) == s["hypotheses_valid"] > 0
    assert counts.get("forecast", 0) > counts["hypothesis"] and counts.get("critique", 0) > 0
    assert counts.get("simulated_outcome", 0) >= 1  # the last origin's synthetic preview, labelled conditional
    for c in led.as_of("2030-01-01", run_id=f"{s['replay_id']}:{ORIGINS[0]}", claim_type="forecast"):
        assert not c["produced_by"].startswith("agent:") and c["payload"]["model"]["id"] == "intel.prediction_policy"
    sim = led.as_of("2030-01-01", claim_type="simulated_outcome")[0]
    assert sim["payload"]["conditional"] is True and not led.scorable(sim)
    hyp = led.as_of("2030-01-01", claim_type="hypothesis")[0]
    assert hyp["payload"]["refutation_criteria"].startswith("Refuted if") and hyp["payload"]["origin"] == "template_selection"
    # no engine-produced claim was rejected by its own ledger
    assert store.conn.execute("SELECT COUNT(*) FROM validation_records WHERE stage='ledger'").fetchone()[0] == 0
    # injected faults: fabricated id rejected by the schema, wrong number by validation, both recorded with classes
    f = s["failures"]["items"]
    assert f["schema:rejected"]["fabricated.evidence_id"] == len(ORIGINS)
    assert f["validation:rejected"]["validation.unsupported_number"] == len(ORIGINS)
    assert s["hypotheses_rejected_by_validation"] == 2 * len(ORIGINS)
    assert s["coverage_contract"]["violations"]  # the fixture market is a legacy spec without [measurement]
    assert EventLog(store).verify()["ok"]
    types = {e["event_type"] for e in EventLog(store).read()}
    assert {"replay.started", "replay.completed", "claim.accepted", "agent_output.rejected", "llm.call"} <= types


def test_duplicate_predictions_count_once():
    from halibut.intel.replay import unique_predictions
    base = {"origin_as_of": "o", "subject": "market", "hypothesis_mode": "continuation", "quantity_kind": "share",
            "method": "level", "interval_method": "naive", "point": 0.1, "realized": 0.12}
    rows = [dict(base), dict(base), {**base, "point": 0.2}]
    assert len(unique_predictions(rows)) == 2


def test_strict_mode_drops_and_rejections_are_explicit(world):
    """A Haiku-v1-style reply (dict evidence, prefixed subjects, appended values) under the v2 contract:
    every bad item is rejected at the schema stage with its class; none is repaired; drops are recorded."""
    from halibut.agents.llm import MockProvider
    from halibut.agents.orchestrator import LLMAgentSet
    from halibut.intel.workflow import analyse
    from halibut.learning.memory import LearningMemory
    store, tmp = world
    market = load_market(tmp / "m.toml")

    def responder(role, b):
        if role == "research":
            return {"actions": []}
        if role == "critic":
            return {"critiques": []}
        m = b["metrics"][0]["id"]
        base = {"kind": "momentum", "subject": "byd", "claim": "BYD moved.", "evidence": [m], "confidence": "low",
                "mechanism_candidates": [], "levers": []}
        return {"hypotheses": [base, {**base, "evidence": {"metrics_cited": [m]}}, {**base, "evidence": [m + " = 1.23"]},
                               {**base, "subject": "brand:byd"}]}
    store.conn.execute("INSERT INTO intel_runs VALUES ('rs','m','x','x',NULL,'running','{}')")
    a = analyse(store, market, LearningMemory(store), "rs", None, {}, agents=LLMAgentSet(MockProvider(responder), store, "rs"),
                preview=False)
    got = sorted(h.get("failure_class") for h in a["rejected"])
    assert got == ["format.evidence_not_id_list", "format.evidence_value_appended", "format.subject_not_canonical"]
    rows = store.conn.execute("SELECT stage, failure_class, item_json FROM validation_records WHERE run_id='rs'").fetchall()
    assert {r[0] for r in rows} == {"schema"} and len(rows) == 3
    assert any('" = 1.23"' in r[2] or "= 1.23" in r[2] for r in rows)  # stored as returned


def test_failed_reply_text_is_stored(world):
    from halibut.agents.llm import LLMResponse, record_call
    store, _ = world
    r = LLMResponse('{"hypotheses": [{"kind": "mom', None, 10, 600, False, "m", "p", 1.0)
    r.stop_reason = "max_tokens"
    record_call(store, "rt", "agent:x", "research", r, "h")
    row = store.conn.execute("SELECT outcome, response_excerpt FROM agent_calls WHERE run_id='rt'").fetchone()
    assert row[0] == "truncated: stop_reason=max_tokens" and row[1].startswith('{"hypotheses"')


def test_reconstruct_reproduces_a_recorded_run_offline(world):
    """Record a run (Anthropic over a mock HTTP transport, v1 prompts), then reconstruct it from the cache and the
    recorded outcomes alone: the same results, failures classified, and no request sent."""
    anthropic = pytest.importorskip("anthropic")  # noqa: F841
    httpx = pytest.importorskip("httpx2")
    from halibut.agents.anthropic_provider import AnthropicProvider
    from halibut.agents.llm import load_llm_config
    from halibut.agents.mock import responder
    from halibut.agents.orchestrator import PROMPTS, LLMAgentSet
    from halibut.intel import benchmark as bm
    from halibut.intel.replay import replay
    store, tmp = world
    roles = {v: k for k, v in PROMPTS["v1"].items()}
    sent = []

    def handler(request):
        body = json.loads(request.content)
        sent.append(body)
        role = roles[body["system"]]
        if role == "research":  # recorded as truncated, like the live benchmark
            return httpx.Response(200, json={"id": "m", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
                                             "content": [{"type": "text", "text": '{"actions": [{"tool"'}], "stop_reason": "max_tokens",
                                             "stop_sequence": None, "usage": {"input_tokens": 400, "output_tokens": 600}})
        data = responder(role, json.loads(body["messages"][0]["content"].split("BUNDLE:", 1)[1]))
        return httpx.Response(200, json={"id": "m", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
                                         "content": [{"type": "text", "text": json.dumps(data)}], "stop_reason": "end_turn",
                                         "stop_sequence": None, "usage": {"input_tokens": 900, "output_tokens": 300}})
    cfg = {**load_llm_config(ROOT / "configs" / "llm" / "anthropic.toml"), "approved": True}
    client = httpx.Client(transport=httpx.MockTransport(handler))

    def factory(st, run_id):
        return LLMAgentSet(AnthropicProvider(cfg, st, run_id.split(":")[0], environ={"ANTHROPIC_API_KEY": "sk-ant-test"},
                                             http_client=client), st, run_id, prompt_version="v1")
    orig = replay(store, tmp / "m.toml", ORIGINS, agents="anthropic", log=lambda *_: None, agent_factory=factory)
    assert all("output_config" not in b for b in sent)  # v1 = the legacy contract (no schema)
    n_sent = len(sent)
    store.conn.commit()
    r = bm.reconstruct(tmp / "s.sqlite", tmp / "m.toml", orig["replay_id"], salt_config=cfg)
    assert len(sent) == n_sent  # reconstruction sent nothing
    for k in ("hypotheses_valid", "hypotheses_rejected_by_validation", "supported", "refuted", "hypothesis_predictions_scored"):
        assert r["reproduced"][k] == orig[k], k
    # research was truncated (recorded outcome served); the critic's successful replies embed run-specific hypothesis
    # ids, so they cannot be found in the cache: reported as not reproducible, never invented
    assert r["failures"]["calls"]["call.truncated"] == len(ORIGINS)
    assert set(r["failures"]["calls"]) <= {"call.truncated", "call.not_reproducible"}
    assert r["cache_hits"] >= len(ORIGINS)


def test_coverage_contract_is_industry_neutral(tmp_path):
    uk = load_market(ROOT / "configs" / "intel" / "markets" / "uk_cars_bev_v1.toml")
    rep = check_contract(uk)
    assert rep.valid and rep.providers["transactions"] == "dft" and uk.competitor("byd").identifiers["dft_make"] == "BYD"
    spec = (ROOT / "tests" / "fixtures" / "market_non_vehicle.toml").read_text()
    (tmp_path / "food.toml").write_text(spec)
    food = load_market(tmp_path / "food.toml")
    rep = check_contract(food)
    assert rep.valid, rep.violations  # a non-vehicle market is a valid declaration ...
    assert rep.providers["transactions"] is None and rep.capabilities["share_forecasting"].startswith("unavailable")
    # ... and a vehicle-only provider is refused for it, with the reason
    bad = tmp_path / "bad.toml"
    bad.write_text(spec.replace("[evidence]", '[evidence]\ntransactions = { provider = "dft" }'))
    v = " ".join(check_contract(load_market(bad)).violations)
    assert "does not cover" in v and "food.plant_based_milk" in v
    store = Store(tmp_path / "s.sqlite")
    cov = coverage_report(store, food, "2026-01-01")
    assert cov["status"] == "uncovered" and set(cov["per_competitor"]) == {"northfield", "riverbend"}
    store.close()
    legacy = tmp_path / "legacy.toml"
    legacy.write_text(MARKET)
    assert any("[measurement] is undeclared" in x for x in check_contract(load_market(legacy)).violations)

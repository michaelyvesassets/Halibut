import json
import sqlite3
from pathlib import Path

import pytest

from halibut.agents.budget import SpendGuard, conservative_input_tokens
from halibut.agents.llm import CachedProvider, NotApproved, Pricing, QwenProvider, SpendLimitReached, load_llm_config
from halibut.config import ConfigError
from halibut.data.store import Store
from halibut.learning.memory import LearningMemory

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "configs" / "llm" / "qwen.toml"
SECRET = "sk-TEST-SECRET-DO-NOT-LEAK-123"
ENV = {"DASHSCOPE_API_KEY": SECRET, "DASHSCOPE_WORKSPACE_ID": "ws-test"}


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "s.sqlite")
    yield s
    s.close()


def approved_cfg(**kw):
    return {**load_llm_config(CFG), "approved": True, **kw}


class FakeAPI:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def __call__(self, url, body, headers, timeout):
        self.calls.append({"url": url, "body": json.loads(body), "auth": headers["Authorization"]})
        status, payload = self.responses.pop(0)
        return status, json.dumps(payload).encode() if isinstance(payload, dict) else payload


def ok(content='{"ok": true}', usage=None):
    return 200, {"model": "qwen3.7-plus", "choices": [{"message": {"content": content}}],
                 "usage": usage if usage is not None else {"prompt_tokens": 30, "completion_tokens": 6}}


def test_committed_config_is_inactive_verified_and_secret_free():
    cfg = load_llm_config(CFG)
    assert cfg["approved"] is False and cfg["model"] == "qwen3.7-plus" and cfg["enable_thinking"] is False
    assert cfg["input_per_mtok_usd"] == 0.40 and cfg["output_per_mtok_usd"] == 1.60
    assert cfg["max_spend_usd_total"] == 5.0 and cfg["max_spend_usd_per_run"] == 1.0
    assert "ap-southeast-1" in cfg["base_url"] and cfg["api_key_env"] == "DASHSCOPE_API_KEY"
    text = CFG.read_text()
    assert "sk-" not in text and "api_key =" not in text


def test_config_rejects_inline_secrets(tmp_path):
    p = tmp_path / "bad.toml"
    p.write_text(CFG.read_text().replace('api_key_env = "DASHSCOPE_API_KEY"', 'api_key_env = "X"\napi_key = "sk-123"'))
    with pytest.raises(ConfigError, match="secrets"):
        load_llm_config(p)


def test_provider_refuses_without_approval_key_or_workspace(store):
    with pytest.raises(NotApproved, match="not approved"):
        QwenProvider(load_llm_config(CFG), store, "s", environ=ENV)
    with pytest.raises(NotApproved, match="DASHSCOPE_API_KEY"):
        QwenProvider(approved_cfg(), store, "s", environ={})
    with pytest.raises(NotApproved, match="DASHSCOPE_WORKSPACE_ID"):
        QwenProvider(approved_cfg(), store, "s", environ={"DASHSCOPE_API_KEY": SECRET})


def test_request_shape_thinking_off_json_mode_and_settlement(store):
    api = FakeAPI([ok()])
    q = QwenProvider(approved_cfg(), store, "smoke:1", transport=api, environ=ENV)
    r = q.complete("Reply with JSON only.", 'Return {"ok": true}', role="smoke", max_output_tokens=20, run_id="smoke:1")
    call = api.calls[0]
    assert call["url"] == "https://ws-test.ap-southeast-1.maas.aliyuncs.com/compatible-mode/v1/chat/completions"
    assert call["body"]["enable_thinking"] is False and call["body"]["response_format"] == {"type": "json_object"}
    assert call["body"]["model"] == "qwen3.7-plus" and call["body"]["max_tokens"] == 20
    assert r.data == {"ok": True} and r.actual_cost_usd == pytest.approx(30 / 1e6 * 0.4 + 6 / 1e6 * 1.6)
    row = store.conn.execute("SELECT * FROM llm_spend_ledger").fetchone()
    assert row["status"] == "settled" and row["actual_usd"] == pytest.approx(r.actual_cost_usd) and row["reserved_usd"] >= row["actual_usd"]
    with pytest.raises(ValueError, match="JSON"):
        q.complete("no keyword", "here", role="smoke", run_id="smoke:1")


def test_secret_never_stored_logged_or_raised(store, tmp_path):
    api = FakeAPI([(401, {"error": {"code": "invalid_api_key"}})])
    q = QwenProvider(approved_cfg(retries=0), store, "s", transport=api, environ=ENV)
    with pytest.raises(RuntimeError) as exc:
        q.complete("JSON please", "x", role="smoke", run_id="s")
    assert SECRET not in str(exc.value) and SECRET not in repr(q.__dict__.get("cfg"))
    dump = "\n".join(store.conn.iterdump())
    assert SECRET not in dump
    assert api.calls[0]["auth"] == f"Bearer {SECRET}"  # only in the outgoing header


def test_retries_are_reserved_and_failed_attempts_charged(store):
    api = FakeAPI([(503, {"error": "busy"}), ok()])
    q = QwenProvider(approved_cfg(retries=1), store, "rp1", transport=api, environ=ENV)
    import halibut.agents.llm as llm
    llm.time.sleep = lambda s: None
    q.complete("JSON", "x", role="smoke", max_output_tokens=20, run_id="rp1:2024")
    rows = store.conn.execute("SELECT status, reserved_usd, actual_usd FROM llm_spend_ledger ORDER BY entry_id").fetchall()
    assert [r["status"] for r in rows] == ["failed_charged", "settled"]
    assert q.guard.committed_scope() == pytest.approx(rows[0]["reserved_usd"] + rows[1]["actual_usd"])


def test_missing_usage_charged_at_worst_case_and_reasoning_flagged(store):
    api = FakeAPI([ok(usage={}), ok(usage={"prompt_tokens": 10, "completion_tokens": 50,
                                           "completion_tokens_details": {"reasoning_tokens": 40}})])
    q = QwenProvider(approved_cfg(), store, "rp2", transport=api, environ=ENV)
    q.complete("JSON", "x", role="smoke", max_output_tokens=20, run_id="rp2:a")
    r2 = q.complete("JSON", "y", role="smoke", max_output_tokens=20, run_id="rp2:b")
    rows = store.conn.execute("SELECT reserved_usd, actual_usd, note FROM llm_spend_ledger ORDER BY entry_id").fetchall()
    assert rows[0]["actual_usd"] == rows[0]["reserved_usd"] and "usage missing" in rows[0]["note"]
    assert r2.reasoning_tokens == 40 and "reasoning_tokens=40" in rows[1]["note"]


def test_guard_caps_including_outstanding_reservations_and_concurrency(tmp_path):
    a, b = Store(tmp_path / "g.sqlite"), Store(tmp_path / "g.sqlite")
    pricing = Pricing(0.4, 1.6)
    ga = SpendGuard(a, "qwen", "m", pricing, total_cap_usd=0.007, scope_cap_usd=0.004, scope="rpA")
    gb = SpendGuard(b, "qwen", "m", pricing, total_cap_usd=0.007, scope_cap_usd=0.004, scope="rpB")
    text = "x" * 3000
    w = ga.worst_case(text, 1500)
    assert w == pytest.approx(pricing.cost(conservative_input_tokens(text), 1500))
    ga.reserve(text, 1500, "rpA:1")  # outstanding (never settled)
    with pytest.raises(SpendLimitReached, match="scope cap"):
        ga.reserve(text, 1500, "rpA:2")  # scope already has a reservation
    gb.reserve(text, 1500, "rpB:1")  # another process / scope
    with pytest.raises(SpendLimitReached, match="total cap"):
        gb.reserve(text, 1500, "rpB:2")  # total includes the other process's outstanding reservation
    with pytest.raises(SpendLimitReached, match="prices"):
        SpendGuard(a, "qwen", "m", Pricing(None, None), 5, 1, "x")
    a.close(), b.close()


def test_cached_responses_cost_nothing(store):
    api = FakeAPI([ok()])
    q = QwenProvider(approved_cfg(), store, "rp3", transport=api, environ=ENV)
    cp = CachedProvider(q, store)
    cp.complete("JSON", "same", role="smoke", run_id="rp3:a")
    r = cp.complete("JSON", "same", role="smoke", run_id="rp3:b")
    assert r.cached and len(api.calls) == 1 and store.conn.execute("SELECT COUNT(*) FROM llm_spend_ledger").fetchone()[0] == 1


def test_status_history_is_as_of(store):
    mem = LearningMemory(store)
    k = mem.submit("lesson", "L", "engine:x", {"tags": ["t"]}, [], available_at="2024-01-01T00:00:00Z")
    e1 = mem.record_experiment("t", "b", "engine:x", {})
    mem.set_status(k, "rejected", "engine:x", effective_at="2025-06-01T00:00:00Z")
    assert mem.retrieve(tags=["t"], as_of="2025-01-01")[0]["status"] == "proposed"
    later = mem.retrieve(tags=["t"], as_of="2025-07-01")[0]
    assert later["status"] == "rejected"
    assert mem.retrieve(tags=["t"], as_of="2025-01-01", statuses=["rejected"]) == []
    assert mem.retrieve(tags=["t"])[0]["status"] == "rejected"  # current view


def test_benchmark_plan_and_mock_run_on_fixtures(tmp_path):
    from halibut.data.providers.base import register
    from halibut.intel import benchmark as bm
    from halibut.intel.workflow import investigate
    from .test_intel import MARKET, routes
    from .test_providers import FakeNet
    (tmp_path / "m.toml").write_text(MARKET)
    store = Store(tmp_path / "s.sqlite")
    register(store, ROOT / "configs" / "data" / "providers.toml")
    investigate(store, tmp_path / "m.toml", tmp_path / "out", tmp_path / "raw", log=lambda *_: None, transport=FakeNet(routes()))
    store.close()
    origins = ["2024-11-23", "2025-05-23", "2025-11-23"]
    p = bm.plan(tmp_path / "s.sqlite", tmp_path / "m.toml", origins)
    assert p["calls"] == 9 and p["fits_per_run_cap"] and p["worst_case_usd"] > 0
    store = Store(tmp_path / "s.sqlite")
    assert store.conn.execute("SELECT COUNT(*) FROM llm_spend_ledger").fetchone()[0] == 0  # plan used a throwaway copy
    r = bm.run(store, tmp_path / "m.toml", "mock", origins)
    assert r["identical_cutoffs"] and set(r["results"]) == {"reference", "mock"}
    assert r["results"]["reference"]["cost_usd"] == 0.0 and "ESTIMATED" in r["publication_dates"]
    store.close()

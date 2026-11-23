"""Offline tests for the Anthropic provider: the real ``anthropic`` SDK over a mock HTTP transport (no network)."""

import json
from pathlib import Path

import pytest

anthropic = pytest.importorskip("anthropic")
httpx = pytest.importorskip("httpx2")

from halibut.agents.anthropic_provider import AnthropicProvider, parse_json_text, pricing_from_config  # noqa: E402
from halibut.agents.budget import SpendGuard  # noqa: E402
from halibut.agents.llm import (CachedProvider, NotApproved, Pricing, SpendLimitReached, load_llm_config,  # noqa: E402
                                record_call)
from halibut.config import ConfigError  # noqa: E402
from halibut.data.store import Store  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "configs" / "llm" / "anthropic.toml"
SECRET = "sk-ant-TEST-SECRET-DO-NOT-LEAK-456"
ENV = {"ANTHROPIC_API_KEY": SECRET}
SYS, USER = "Reply with JSON only.", 'Return the JSON object {"ok": true}.'


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "s.sqlite")
    yield s
    s.close()


def approved_cfg(model=None, **kw):
    return {**load_llm_config(CFG, model=model), "approved": True, **kw}


def message(text='{"ok": true}', usage=None, stop="end_turn", model="claude-haiku-5-5", blocks=None, stop_details=None):
    return {"id": "msg_test", "type": "message", "role": "assistant", "model": model,
            "content": blocks if blocks is not None else [{"type": "text", "text": text}],
            "stop_reason": stop, "stop_sequence": None, "stop_details": stop_details,
            "usage": usage if usage is not None else {"input_tokens": 30, "output_tokens": 6,
                                                       "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}


class FakeHTTP:
    """Scripted Anthropic Messages API: list of (status, json) or callables(request_body) -> (status, json)."""

    def __init__(self, responses):
        self.responses, self.requests = list(responses), []

    def __call__(self, request):
        body = json.loads(request.content)
        self.requests.append({"url": str(request.url), "body": body, "headers": dict(request.headers)})
        nxt = self.responses.pop(0)
        status, payload = nxt(body) if callable(nxt) else nxt
        return httpx.Response(status, json=payload, headers={"request-id": f"req_{len(self.requests)}"})

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self))


def provider(store, http, scope="smoke:1", cfg=None, env=ENV):
    return AnthropicProvider(cfg or approved_cfg(), store, scope, environ=env, http_client=http.client())


def error(status, kind):
    return status, {"type": "error", "error": {"type": kind, "message": "scripted"}}


# ---- configuration -------------------------------------------------------------------------------------------

def test_committed_config_is_inactive_official_prices_and_secret_free():
    cfg = load_llm_config(CFG)
    assert cfg["provider"] == "anthropic" and cfg["approved"] is False
    assert cfg["model"] == "claude-haiku-5-5" and cfg["api_key_env"] == "ANTHROPIC_API_KEY"
    assert cfg["base_url"] == "https://api.anthropic.com" and cfg["thinking"] == "disabled"
    # official pricing page, Claude Haiku 5.5 (prompts <= 100K tokens) and its long-prompt card
    assert (cfg["input_per_mtok_usd"], cfg["cache_write_5m_per_mtok_usd"], cfg["cache_write_1h_per_mtok_usd"],
            cfg["cache_read_per_mtok_usd"], cfg["output_per_mtok_usd"]) == (0.10, 0.125, 0.20, 0.01, 0.50)
    assert cfg["long_context_threshold_tokens"] == 100000 and cfg["long_context"]["input_per_mtok_usd"] == 0.50
    assert cfg["long_context"]["output_per_mtok_usd"] == 2.50
    assert cfg["max_spend_usd_total"] == 5.0 and cfg["max_spend_usd_per_run"] == 1.0 and cfg["max_spend_usd_smoke"] == 0.01
    assert cfg["prompt_cache"] == "off"
    sonnet = load_llm_config(CFG, model="claude-sonnet-5-5")
    assert (sonnet["input_per_mtok_usd"], sonnet["output_per_mtok_usd"], sonnet["cache_read_per_mtok_usd"]) == (2.0, 10.0, 0.10)
    assert sonnet["thinking"] == "between_tools" and sonnet["long_context_threshold_tokens"] is None
    text = CFG.read_text()
    assert "sk-ant" not in text and "api_key =" not in text


def test_config_rejects_inline_secrets_and_unpriced_models(tmp_path):
    p = tmp_path / "bad.toml"
    p.write_text(CFG.read_text().replace('api_key_env = "ANTHROPIC_API_KEY"', 'api_key_env = "X"\napi_key = "sk-ant-1"'))
    with pytest.raises(ConfigError, match="secrets"):
        load_llm_config(p)
    with pytest.raises(ConfigError, match="no priced entry"):
        load_llm_config(CFG, model="claude-opus-5-5")
    p.write_text(CFG.read_text().replace("cache_read_per_mtok_usd = 0.01\n", ""))
    with pytest.raises(ConfigError, match="cache_read_per_mtok_usd"):
        load_llm_config(p)


def test_provider_refuses_without_approval_key_or_valid_thinking(store):
    http = FakeHTTP([])
    with pytest.raises(NotApproved, match="not approved"):
        AnthropicProvider(load_llm_config(CFG), store, "s", environ=ENV, http_client=http.client())
    with pytest.raises(NotApproved, match="ANTHROPIC_API_KEY"):
        provider(store, http, env={})
    with pytest.raises(NotApproved, match="rejected by claude-sonnet-5-5"):
        provider(store, http, cfg=approved_cfg("claude-sonnet-5-5", thinking="disabled"))
    with pytest.raises(NotApproved, match="effort"):
        provider(store, http, cfg=approved_cfg(effort="max"))
    with pytest.raises(SpendLimitReached):
        provider(store, http, cfg=approved_cfg(max_spend_usd_per_run=0))
    assert http.requests == []


# ---- request shape, secrets, settlement ----------------------------------------------------------------------

def test_request_shape_via_real_sdk_and_settlement(store, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://elsewhere.example")  # must not redirect the key
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "other-credential")
    http = FakeHTTP([(200, message())])
    r = provider(store, http).complete(SYS, USER, role="smoke", max_output_tokens=20, run_id="smoke:1")
    req = http.requests[0]
    assert req["url"] == "https://api.anthropic.com/v1/messages"
    body = req["body"]
    assert body["model"] == "claude-haiku-5-5" and body["max_tokens"] == 20 and body["system"] == SYS
    assert body["thinking"] == {"type": "disabled"}
    assert body["messages"] == [{"role": "user", "content": USER}]  # ends with a user turn: no prefill
    assert not {"temperature", "top_p", "top_k", "cache_control", "output_config"} & set(body)
    assert req["headers"]["authorization"] == f"Bearer {SECRET}" and req["headers"]["x-stainless-retry-count"] == "0"
    assert r.data == {"ok": True} and r.input_tokens == 30 and r.output_tokens == 6 and not r.tokens_estimated
    assert r.actual_cost_usd == pytest.approx(30 / 1e6 * 0.10 + 6 / 1e6 * 0.50)
    assert r.stop_reason == "end_turn" and r.request_id == "req_1"
    row = store.conn.execute("SELECT * FROM llm_spend_ledger").fetchone()
    assert row["provider"] == "anthropic" and row["status"] == "settled" and row["run_id"] == "smoke:1"
    assert row["actual_usd"] == pytest.approx(r.actual_cost_usd) and row["reserved_usd"] >= row["actual_usd"]
    assert "request_id=req_1" in row["note"]


def test_effort_and_prompt_cache_options_are_sent_only_when_configured(store):
    http = FakeHTTP([(200, message()), (200, message())])
    provider(store, http, cfg=approved_cfg(effort="low", prompt_cache="5m")).complete(SYS, USER, role="smoke", run_id="smoke:1")
    provider(store, http, cfg=approved_cfg("claude-sonnet-5-5", prompt_cache="1h")).complete(SYS, USER, role="smoke",
                                                                                           run_id="smoke:1")
    a, b = http.requests[0]["body"], http.requests[1]["body"]
    assert a["output_config"] == {"effort": "low"} and a["cache_control"] == {"type": "ephemeral"}
    assert b["thinking"] == {"type": "between_tools"} and b["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert b["model"] == "claude-sonnet-5-5"


def test_cache_token_costs_use_official_rates_and_long_prompt_card(store):
    usage = {"input_tokens": 1000, "output_tokens": 200, "cache_creation_input_tokens": 3000, "cache_read_input_tokens": 5000,
             "cache_creation": {"ephemeral_5m_input_tokens": 2000, "ephemeral_1h_input_tokens": 1000}}
    long_usage = {"input_tokens": 90000, "output_tokens": 100, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 20000}
    http = FakeHTTP([(200, message(usage=usage)), (200, message(usage=long_usage))])
    p = provider(store, http, cfg=approved_cfg(prompt_cache="5m"))
    r = p.complete(SYS, USER, role="smoke", run_id="smoke:1")
    expect = (1000 * 0.10 + 200 * 0.50 + 2000 * 0.125 + 1000 * 0.20 + 5000 * 0.01) / 1e6
    assert r.actual_cost_usd == pytest.approx(expect) and r.input_tokens == 9000
    assert (r.cache_write_tokens, r.cache_read_tokens) == (3000, 5000)
    # 110,000 prompt tokens in total (incl. cache reads) > 100K: the whole request uses the long-prompt card
    r2 = p.complete(SYS, USER + " ", role="smoke", run_id="smoke:1")
    assert r2.actual_cost_usd == pytest.approx((90000 * 0.50 + 100 * 2.50 + 20000 * 0.05) / 1e6)
    pr = pricing_from_config(approved_cfg("claude-sonnet-5-5"))
    assert pr.cost_usage(0, 0, cache_read=1_000_000) == pytest.approx(0.10)  # Sonnet 5.5 reads at 0.05x


def test_worst_case_reservation_is_an_upper_bound():
    pr = pricing_from_config(approved_cfg(prompt_cache="5m"))
    text = "x" * 30000
    worst = pr.worst_case(text, 2000)
    # even if every character were its own token and all input were a cache write, plus the full output cap
    assert worst == pytest.approx(((30000 / 2 + 64) * 0.125 + 2000 * 0.50) / 1e6)
    assert worst >= pr.cost_usage(30000 // 4, 2000)
    big = pr.worst_case("x" * 250000, 100)  # conservative estimate > 100K tokens -> long card
    assert big == pytest.approx(((125000 + 64) * 0.625 + 100 * 2.50) / 1e6)


def test_retries_are_each_reserved_and_errors_are_redacted(store):
    http = FakeHTTP([error(529, "overloaded_error"), (200, message())])
    p = provider(store, http)
    r = p.complete(SYS, USER, role="smoke", run_id="smoke:1")
    assert r.data == {"ok": True} and len(http.requests) == 2
    statuses = [x[0] for x in store.conn.execute("SELECT status FROM llm_spend_ledger ORDER BY entry_id")]
    assert statuses == ["failed_charged", "settled"]
    # a 400 is not retried; it stays charged at its reservation; the message never contains the key
    http2 = FakeHTTP([error(400, "invalid_request_error")])
    with pytest.raises(RuntimeError) as exc:
        provider(store, http2).complete(SYS, USER, role="smoke", run_id="smoke:1")
    assert len(http2.requests) == 1 and "HTTP 400" in str(exc.value) and SECRET not in str(exc.value)
    http3 = FakeHTTP([error(429, "rate_limit_error"), error(500, "api_error")])
    with pytest.raises(RuntimeError, match="HTTP 500"):
        provider(store, http3).complete(SYS, USER, role="smoke", run_id="smoke:1")
    dump = "\n".join(str(tuple(row)) for t in ("llm_spend_ledger", "agent_calls", "llm_cache")
                     for row in store.conn.execute(f"SELECT * FROM {t}"))
    assert SECRET not in dump


def test_missing_usage_is_charged_at_worst_case(store):
    http = FakeHTTP([(200, {**message(), "usage": {"input_tokens": 0, "output_tokens": 0}})])
    r = provider(store, http).complete(SYS, USER, role="smoke", run_id="smoke:1")
    assert r.actual_cost_usd == 0.0  # usage reported (zero) -> settled at zero
    p = provider(store, FakeHTTP([]))

    class NoUsage:
        usage, stop_reason, model, content, _request_id = None, "end_turn", "claude-haiku-5-5", [], None
    entry, worst = p.guard.reserve(SYS + USER, 20, "smoke:1")
    r = p._settle(NoUsage(), entry, worst, 20, 0.1, SYS + USER)
    row = store.conn.execute("SELECT actual_usd, reserved_usd, note FROM llm_spend_ledger WHERE entry_id=?", (entry,)).fetchone()
    assert r.actual_cost_usd is None and r.tokens_estimated and row[0] == row[1] and "usage missing" in row[2]


def test_refusal_truncation_and_thinking_blocks(store):
    refusal = message(text="", stop="refusal", stop_details={"type": "refusal", "category": None, "explanation": "declined"})
    truncated = message(text='{"hypotheses": [{"a": 1', stop="max_tokens")
    with_thinking = message(blocks=[{"type": "thinking", "thinking": "", "signature": "sig"},
                                    {"type": "text", "text": '```json\n{"ok": true}\n```'}])
    http = FakeHTTP([(200, refusal), (200, truncated), (200, with_thinking), (200, refusal)])
    p = provider(store, http)
    cached = CachedProvider(p, store)
    r1 = cached.complete(SYS, USER, role="smoke", run_id="smoke:1")
    assert r1.data is None and r1.stop_reason == "refusal" and r1.actual_cost_usd > 0
    r2 = p.complete(SYS, USER, role="smoke", run_id="smoke:1")
    assert r2.data is None and r2.stop_reason == "max_tokens"
    r3 = p.complete(SYS, USER, role="smoke", run_id="smoke:1")
    assert r3.data == {"ok": True}  # text read by block type, fenced JSON accepted
    record_call(store, "smoke:1", "agent", "smoke", r1, "h")
    assert store.conn.execute("SELECT outcome FROM agent_calls").fetchone()[0] == "unparseable: stop_reason=refusal"
    # the refusal was not cached: the identical prompt is sent again (and charged again)
    cached.complete(SYS, USER, role="smoke", run_id="smoke:1")
    assert len(http.requests) == 4 and cached.hits == 0


def test_parse_json_text():
    assert parse_json_text('{"a": 1}') == {"a": 1}
    assert parse_json_text('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_text('Here it is: {"a": {"b": 2}} done') == {"a": {"b": 2}}
    assert parse_json_text("[1, 2]") is None and parse_json_text("no json") is None


# ---- caching and budget ----------------------------------------------------------------------------------------

def test_prompt_cache_hits_are_free_and_keyed_by_model_and_settings(store):
    http = FakeHTTP([(200, message()), (200, message()), (200, message(model="claude-sonnet-5-5"))])
    a = CachedProvider(provider(store, http), store)
    first = a.complete(SYS, USER, role="smoke", run_id="smoke:1")
    again = a.complete(SYS, USER, role="smoke", run_id="smoke:1")
    assert first.actual_cost_usd > 0 and again.cached and again.actual_cost_usd == 0.0 and len(http.requests) == 1
    CachedProvider(provider(store, http, cfg=approved_cfg(effort="low")), store).complete(SYS, USER, role="smoke", run_id="smoke:1")
    CachedProvider(provider(store, http, cfg=approved_cfg("claude-sonnet-5-5")), store).complete(SYS, USER, role="smoke",
                                                                                               run_id="smoke:1")
    assert len(http.requests) == 3  # different effort or model never reuses a cached answer


def test_caps_are_application_wide_across_providers(store):
    qwen = SpendGuard(store, "qwen", "qwen3.7-plus", Pricing(0.4, 1.6), 5.0, 1.0, "rpQ")
    e, _ = qwen.reserve("x", 10, "rpQ:o1")
    qwen.settle(e, 4.99999)  # the smoke worst case (~$0.00002) no longer fits under $5
    p = provider(store, FakeHTTP([(200, message())]), scope="rpA")
    with pytest.raises(SpendLimitReached, match="total cap"):
        p.complete(SYS, USER, role="smoke", run_id="rpA:o1")
    assert p.guard.summary()["committed_by_provider_usd"] == {"qwen": 4.99999}


def test_per_replay_cap_stops_before_sending(store):
    full = {"input_tokens": 100, "output_tokens": 2000}  # each settled call costs ~$0.00101
    http = FakeHTTP([(200, message(usage=full))] * 10)
    p = provider(store, http, scope="rpB", cfg=approved_cfg(max_spend_usd_per_run=0.0025))
    sent = 0
    with pytest.raises(SpendLimitReached, match="scope cap"):
        for _ in range(10):
            p.complete(SYS, USER + str(sent), role="hypothesis", max_output_tokens=2000, run_id="rpB:o1")
            sent += 1
    assert sent == len(http.requests) == 2  # $0.00202 committed + ~$0.001 worst case > $0.0025


# ---- end to end: benchmark on fixtures with the real SDK over a mock transport ------------------------------------

def test_benchmark_plan_and_anthropic_run_on_fixtures(tmp_path, monkeypatch):
    from halibut.agents import orchestrator as orch
    from halibut.agents.mock import responder
    from halibut.data.providers.base import register
    from halibut.intel import benchmark as bm
    from halibut.intel import replay as rp
    from halibut.intel.workflow import investigate
    from .test_intel import MARKET, routes
    from .test_providers import FakeNet
    (tmp_path / "m.toml").write_text(MARKET)
    store = Store(tmp_path / "s.sqlite")
    register(store, ROOT / "configs" / "data" / "providers.toml")
    investigate(store, tmp_path / "m.toml", tmp_path / "out", tmp_path / "raw", log=lambda *_: None, transport=FakeNet(routes()))
    store.close()
    origins = ["2024-11-23", "2025-05-23", "2025-11-23"]
    p = bm.plan(tmp_path / "s.sqlite", tmp_path / "m.toml", origins, agents="anthropic")
    assert p["provider"] == "anthropic" and p["model"] == "claude-haiku-5-5" and p["calls"] == 9 and p["fits_per_run_cap"]
    roles = {orch.RESEARCH_SYSTEM: "research", orch.HYPOTHESIS_SYSTEM: "hypothesis", orch.CRITIC_SYSTEM: "critic"}

    def answer(body):  # deterministic mock agent behind the real SDK and wire format
        data = responder(roles[body["system"]], json.loads(body["messages"][0]["content"].split("BUNDLE:", 1)[1]))
        return 200, message(text=json.dumps(data), usage={"input_tokens": 900, "output_tokens": 300})

    http = FakeHTTP([answer] * 200)
    original = rp.make_agents

    def make(kind, st, run_id, faults):
        if kind != "anthropic":
            return original(kind, st, run_id, faults)
        prov = AnthropicProvider(approved_cfg(), st, run_id.split(":")[0], environ=ENV, http_client=http.client())
        return orch.LLMAgentSet(prov, st, run_id)
    monkeypatch.setattr(rp, "make_agents", make)
    store = Store(tmp_path / "s.sqlite")
    r = bm.run(store, tmp_path / "m.toml", "anthropic", origins)
    a, ref = r["results"]["anthropic"], r["results"]["reference"]
    assert r["identical_cutoffs"] and ref["cost_usd"] == 0.0 and a["llm_calls"] == len(http.requests) > 0
    assert a["cost_usd"] == pytest.approx(len(http.requests) * (900 * 0.10 + 300 * 0.50) / 1e6)
    assert a["paid_attempts"] == len(http.requests) and a["failed_attempts_charged"] == 0 and a["failed_llm_calls"] == {}
    assert a["latency_mean_s"] is not None and "validation_failure_types" in a
    if a["supported"]:
        assert a["cost_per_useful_validated_result_usd"] == pytest.approx(a["cost_usd"] / a["supported"])
    store.close()

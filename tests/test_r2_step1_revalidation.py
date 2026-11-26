"""R2 Step 1 mixed cached/live engineering revalidation, exercised OFFLINE (real SDK over a mock transport)."""

import hashlib
import json
from pathlib import Path

import pytest

from halibut.agents import loop_agents
from halibut.control import approvals as ap
from halibut.data.store import Store

from .test_r2_live_validation import ATTEST, EVAL, ORIGINS, fake_claude, uk  # noqa: F401  (uk is a fixture)
from .test_r2_loop import fresh

anthropic = pytest.importorskip("anthropic")
httpx = pytest.importorskip("httpx2")
ENV = {"ANTHROPIC_API_KEY": "sk-ant-test"}
LOOP_V1_PROMPTS = {"hypothesis_generator": "f3773bdc8e471027", "loop_critic": "d5442635fac8b051", "strategist": "03f28296bad4c285"}


def test_agent_prompts_are_the_ones_used_in_the_original_run():
    """The critic and strategist see exactly the loop.v1 prompts: nothing from the postmortem reaches any agent."""
    for role, text in loop_agents.SYSTEMS.items():
        assert hashlib.sha256(text.encode()).hexdigest()[:16] == LOOP_V1_PROMPTS[role]
    from halibut.investigation.revalidation import KNOWN_ISSUES
    for k in KNOWN_ISSUES:
        assert k["summary"] not in " ".join(loop_agents.SYSTEMS.values())


def _original_run(uk, tmp_path):
    """An 'original' live run (offline): its generator replies carry 5 explanations, like r2v-b16fc9281536."""
    from halibut.agents.loop_agents import SYSTEMS
    from halibut.agents.mock_loop import responder
    from halibut.investigation.live_validation import run_validation
    store = fresh(tmp_path, uk)
    roles = {v: k for k, v in SYSTEMS.items()}

    def handler(request):
        body = json.loads(request.content)
        if body["system"].startswith("You are a connectivity check"):
            data = {"ok": True}
        else:
            role = roles[body["system"]]
            data = responder(role, json.loads(body["messages"][0]["content"].split("BUNDLE:", 1)[1]))
            if role == "hypothesis_generator":
                data["explanations"] = (data["explanations"] * 3)[:5]
        return httpx.Response(200, json={"id": "m", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
                                         "content": [{"type": "text", "text": json.dumps(data)}], "stop_reason": "end_turn",
                                         "stop_sequence": None, "usage": {"input_tokens": 3000, "output_tokens": 900}})
    aid = ap.request(store, "paid_run", "r2_validation:test_market", "founder", max_usd=0.06)
    ap.decide(store, aid, "granted", "founder", evidence={"attestation": ATTEST})
    rep = run_validation(store, uk / "m.toml", ORIGINS, EVAL, live=True, approval_id=aid, max_usd=0.06, out_dir=tmp_path / "orig",
                         environ=ENV, http_client=httpx.Client(transport=httpx.MockTransport(handler)),
                         cfg_overrides={"approved": True}, log=lambda *_: None)
    return store, Path(rep["output_dir"]).parent


def _approve(store, cap=0.04):
    aid = ap.request(store, "paid_run", "r2_revalidation:test_market", "founder", max_usd=cap)
    ap.decide(store, aid, "granted", "founder", evidence={"attestation": ATTEST})
    return aid


def test_step1_sends_only_smoke_critic_and_strategist(uk, tmp_path):
    from halibut.investigation import artifact as art
    from halibut.investigation.revalidation import KNOWN_ISSUES, LABEL, run_revalidation
    store, orig_dir = _original_run(uk, tmp_path)
    aid = _approve(store)
    sent = []
    r = run_revalidation(store, tmp_path / "s.sqlite", uk / "m.toml", orig_dir, ORIGINS, EVAL, live=True, approval_id=aid,
                         max_usd=0.04, out_dir=tmp_path / "rv", environ=ENV, http_client=fake_claude(sent),
                         cfg_overrides={"approved": True}, log=lambda *_: None)
    assert r["identity_preflight"]["identical"]
    systems = [b["system"] for b in sent]
    assert len(sent) == 1 + 2 * len(ORIGINS) and loop_agents.GENERATOR_SYSTEM not in systems  # generator NEVER sent
    assert systems.count(loop_agents.CRITIC_SYSTEM) == len(ORIGINS) and systems.count(loop_agents.STRATEGIST_SYSTEM) == len(ORIGINS)
    # rejected explanations never reach downstream agents, and no postmortem finding is in any request
    text = json.dumps(sent)
    assert "explanations" not in text
    for k in KNOWN_ISSUES:
        assert k["summary"] not in text
    assert r["label"] == LABEL and "Not a fresh independent intelligence experiment" in LABEL
    assert all(v["state"] == "partial_rejection" for v in r["completion"].values())
    assert set(r["criteria_interpretation"]) == set(r["criteria"]) and r["criteria_unchanged"]
    c = r["costs_step1"]
    assert c["historical_generator_total_usd"] > 0 and 0 < c["incremental_live_total_usd"] <= 0.04
    for v in c["per_origin"].values():
        assert v["total_equivalent_investigation_usd"] == pytest.approx(
            v["incremental_live_usd"] + v["historical_generator_usd (r2v-b16fc9281536, recorded reply reused)"]
            + v["historical_reused_critic_strategist_usd"])
    out = Path(r["output_dir"])
    man = json.loads((out / "manifest.json").read_text())
    assert all(f["result_class"] == "mixed_cached_live" for f in man["investigations"])
    for f in man["investigations"]:
        assert art.validate(json.loads((out / f["file"]).read_text())) == []
    assert json.loads((out / "execution_records.json").read_text())["approval"]["used_by"] == r["experiment_id"]
    assert {x["issue"] for x in r["critic_detection"]} == {k["id"] for k in KNOWN_ISSUES}
    assert r["strategist_assessment"]
    with pytest.raises(ap.AuthorizationError, match="single-use|already"):  # the approval cannot be reused
        run_revalidation(store, tmp_path / "s.sqlite", uk / "m.toml", orig_dir, ORIGINS, EVAL, live=True, approval_id=aid,
                         max_usd=0.04, out_dir=tmp_path / "rv2", environ=ENV, http_client=fake_claude([]),
                         cfg_overrides={"approved": True}, log=lambda *_: None)
    store.close()


def test_identity_mismatch_refuses_before_any_call(uk, tmp_path):
    from halibut.investigation.revalidation import run_revalidation
    store, orig_dir = _original_run(uk, tmp_path)
    store.conn.execute("DELETE FROM llm_cache WHERE model='claude-haiku-5-5'")  # the recorded replies are gone
    store.conn.commit()
    aid = _approve(store)
    sent = []
    with pytest.raises(ap.AuthorizationError, match="identity preflight failed"):
        run_revalidation(store, tmp_path / "s.sqlite", uk / "m.toml", orig_dir, ORIGINS, EVAL, live=True, approval_id=aid,
                         max_usd=0.04, out_dir=tmp_path / "rv", environ=ENV, http_client=fake_claude(sent),
                         cfg_overrides={"approved": True}, log=lambda *_: None)
    assert sent == [] and ap.get(store, aid)["used_by"] is None
    store.close()


def test_generator_role_is_cache_only(tmp_path):
    from halibut.agents.llm import load_llm_config
    from halibut.investigation.revalidation import GeneratorCacheMiss, RevalidationRouter
    store = Store(tmp_path / "s.sqlite")
    cfg = load_llm_config(Path(__file__).resolve().parents[1] / "configs" / "llm" / "anthropic.toml")
    r = RevalidationRouter(store, "x", cfg, mode="live")
    prov, _ = r.provider("hypothesis_generator")
    with pytest.raises(GeneratorCacheMiss):
        prov.complete("JSON please", "BUNDLE:{}", role="hypothesis_generator", run_id="x", schema={"type": "object"})
    store.close()


def test_completion_state_distinguishes_clean_from_partial_rejection():
    from halibut.investigation.loop import _completion
    assert _completion("completed", [])["state"] == "clean"
    part = _completion("completed", [{"stage": "schema", "failure_class": "schema.array_limit", "subject": "explanation",
                                      "problems": ["$.explanations: more than 4 items (5)"]}])
    assert part["state"] == "partial_rejection" and part["rejected_arrays"][0]["subject"] == "explanation"
    assert _completion("stopped", [])["state"] == "not_completed"


def test_reuse_sends_no_request_already_paid_for_and_gives_the_same_result(uk, tmp_path):
    """A second revalidation that reuses the first one's PAID critic/strategist replies sends only the smoke test, and its
    documents have the same content hash: the reuse is exact, not an approximation."""
    from halibut.investigation.revalidation import reusable_calls, run_revalidation
    store, orig_dir = _original_run(uk, tmp_path)
    kw = dict(live=True, max_usd=0.04, environ=ENV, cfg_overrides={"approved": True}, log=lambda *_: None)
    first = run_revalidation(store, tmp_path / "s.sqlite", uk / "m.toml", orig_dir, ORIGINS, EVAL, approval_id=_approve(store),
                             out_dir=tmp_path / "rv1", http_client=fake_claude([]), **kw)
    assert reusable_calls(store, ["r2r-not-an-experiment"]) == {}
    assert len(reusable_calls(store, [first["experiment_id"]])) == 2 * len(ORIGINS)
    sent = []
    second = run_revalidation(store, tmp_path / "s.sqlite", uk / "m.toml", orig_dir, ORIGINS, EVAL,
                              approval_id=_approve(store), out_dir=tmp_path / "rv2", http_client=fake_claude(sent),
                              reuse_from=[first["experiment_id"]], **kw)
    assert len(sent) == 1 and sent[0]["system"].startswith("You are a connectivity check")  # smoke only
    assert second["plan_step1"]["calls"] == 1 and len(second["plan_step1"]["reused_calls"]) == 2 * len(ORIGINS)
    assert all(x["reused"] and x["source"]["experiment"] == first["experiment_id"] for x in second["reuse_log"])
    for o in ORIGINS:
        a = json.loads((Path(first["output_dir"]) / f"investigations/mixed_cached_live_claude_{o}.json").read_text())
        b = json.loads((Path(second["output_dir"]) / f"investigations/mixed_cached_live_claude_{o}.json").read_text())
        assert a["content_hash"] == b["content_hash"]
    store.close()


def test_reuse_never_covers_a_changed_request(uk, tmp_path, monkeypatch):
    """If the request differs (here: the strategist schema), the recorded reply is not reused; the call goes live."""
    from halibut.epistemic import schemas as sch
    from halibut.investigation import loop as loop_mod
    from halibut.investigation.revalidation import run_revalidation
    store, orig_dir = _original_run(uk, tmp_path)
    kw = dict(live=True, max_usd=0.04, environ=ENV, cfg_overrides={"approved": True}, log=lambda *_: None)
    first = run_revalidation(store, tmp_path / "s.sqlite", uk / "m.toml", orig_dir, ORIGINS, EVAL, approval_id=_approve(store),
                             out_dir=tmp_path / "rv1", http_client=fake_claude([]), **kw)
    real = sch.strategist_schema
    monkeypatch.setattr(loop_mod.sch, "strategist_schema", lambda *a: {**real(*a), "description": "changed"})
    sent = []
    second = run_revalidation(store, tmp_path / "s.sqlite", uk / "m.toml", orig_dir, ORIGINS, EVAL,
                              approval_id=_approve(store), out_dir=tmp_path / "rv2", http_client=fake_claude(sent),
                              reuse_from=[first["experiment_id"]], **kw)
    systems = [b["system"] for b in sent]
    assert systems.count(loop_agents.STRATEGIST_SYSTEM) == len(ORIGINS) and loop_agents.CRITIC_SYSTEM not in systems
    assert {x["role"] for x in second["reuse_log"] if not x["reused"]} == {"strategist"}
    store.close()


def test_an_internal_error_is_visible_stops_spending_and_keeps_the_records(uk, tmp_path, monkeypatch):
    from halibut.investigation import loop as loop_mod
    from halibut.investigation.revalidation import run_revalidation
    store, orig_dir = _original_run(uk, tmp_path)

    def boom(*a, **k):
        raise ZeroDivisionError("planted defect")
    monkeypatch.setattr(loop_mod.strategy, "simulate", boom)
    sent = []
    r = run_revalidation(store, tmp_path / "s.sqlite", uk / "m.toml", orig_dir, ORIGINS, EVAL, live=True,
                         approval_id=_approve(store), max_usd=0.04, out_dir=tmp_path / "rv", environ=ENV,
                         http_client=fake_claude(sent), cfg_overrides={"approved": True}, log=lambda *_: None)
    first, *rest = sorted(ORIGINS)
    assert r["internal_errors"][first].startswith("internal_error: ZeroDivisionError")
    assert all(r["internal_errors"][o].startswith("not run") for o in rest)
    assert len(sent) == 3  # smoke + the first origin's critic and strategist; nothing after the defect
    assert r["verdict"]["pipeline_ready"] is False
    out = Path(r["output_dir"])
    d = json.loads((out / f"investigations/mixed_cached_live_claude_{first}.json").read_text())
    assert d["status"] == "stopped" and d["failures"]["completion"]["internal_error"]
    rec = json.loads((out / "execution_records.json").read_text())
    assert rec["internal_errors"] == r["internal_errors"] and (out / "comparison.md").exists()
    assert "INTERNAL ERRORS" in (out / "comparison.md").read_text()
    store.close()


def test_only_live_origins_may_call_live(uk, tmp_path):
    """Without reusable replies for the first origin, a run restricted to the second origin refuses before ANY call; with
    them, only the smoke test and the second origin's critic and strategist are sent."""
    from halibut.investigation.revalidation import run_revalidation
    store, orig_dir = _original_run(uk, tmp_path)
    first, second = sorted(ORIGINS)
    kw = dict(live=True, max_usd=0.04, environ=ENV, cfg_overrides={"approved": True}, log=lambda *_: None)
    sent = []
    aid = _approve(store)
    with pytest.raises(ap.AuthorizationError, match="not covered by reusable paid replies"):
        run_revalidation(store, tmp_path / "s.sqlite", uk / "m.toml", orig_dir, ORIGINS, EVAL, approval_id=aid,
                         out_dir=tmp_path / "rv0", http_client=fake_claude(sent), live_origins=[second], **kw)
    assert sent == [] and ap.get(store, aid)["used_by"] is None
    prior = run_revalidation(store, tmp_path / "s.sqlite", uk / "m.toml", orig_dir, ORIGINS, EVAL, approval_id=aid,
                             out_dir=tmp_path / "rv1", http_client=fake_claude([]), **kw)
    # make the second origin's paid replies unavailable for reuse, as in the real r2r-57fa83dc8f2f (never called)
    store.conn.execute("UPDATE agent_calls SET outcome='x' WHERE run_id=? AND role IN ('loop_critic','strategist')",
                       (next(json.loads(p.read_text())["input_id"] for p in
                             (Path(prior["output_dir"]) / "investigations").glob(f"*{second}.json")),))
    store.conn.commit()
    r = run_revalidation(store, tmp_path / "s.sqlite", uk / "m.toml", orig_dir, ORIGINS, EVAL, approval_id=_approve(store),
                         out_dir=tmp_path / "rv2", http_client=fake_claude(sent), reuse_from=[prior["experiment_id"]],
                         live_origins=[second], **kw)
    systems = [b["system"] for b in sent]
    assert len(sent) == 3 and systems.count(loop_agents.CRITIC_SYSTEM) == 1 and systems.count(loop_agents.STRATEGIST_SYSTEM) == 1
    assert r["plan_step1"]["calls"] == 3 and {x["origin"] for x in r["plan_step1"]["reused_calls"]} == {first}
    c = r["costs_step1"]["per_origin"]
    assert prior["experiment_id"] != r["experiment_id"]  # ids never collide (they once did within one second)
    assert c[first]["historical_reused_critic_strategist_usd"] > 0 and c[first]["incremental_live_usd"] == 0
    assert c[second]["incremental_live_usd"] > 0 and c[second]["historical_reused_critic_strategist_usd"] == 0
    store.close()


def test_experiment_ids_never_collide_within_one_second(uk, tmp_path, monkeypatch):
    """Ids were hash(original, origins, utc_now()) at 1 s resolution: two runs in the same second shared a spend scope
    and mixed their ledger costs. A random nonce is now part of the id."""
    from halibut.investigation import revalidation
    store, orig_dir = _original_run(uk, tmp_path)
    monkeypatch.setattr(revalidation, "utc_now", lambda: "2026-11-26T00:00:00Z")
    ids = {revalidation.run_revalidation(store, tmp_path / "s.sqlite", uk / "m.toml", orig_dir, ORIGINS, EVAL,
                                         out_dir=tmp_path / f"p{i}", log=lambda *_: None)["experiment_id"] for i in range(2)}
    assert len(ids) == 2
    store.close()


def test_a_rejected_key_is_reported_consistently(uk, tmp_path):
    """r2r-dc620061d98c: the smoke test got HTTP 401 'Invalid bearer token'. The report must show the failure and one
    cost figure (the ledger's fail-closed reservation, in both the smoke and the incremental totals), Step 1's own plan,
    and no 'dry run' labels in a live run. Nothing after the smoke test is sent."""
    from halibut.investigation.revalidation import revalidation_markdown, run_revalidation
    store, orig_dir = _original_run(uk, tmp_path)
    sent = []

    def reject(request):
        sent.append(request)
        return httpx.Response(401, json={"type": "error", "error": {"type": "authentication_error",
                                                                     "message": "Invalid bearer token"}})
    r = run_revalidation(store, tmp_path / "s.sqlite", uk / "m.toml", orig_dir, ORIGINS, EVAL, live=True,
                         approval_id=_approve(store), max_usd=0.02, out_dir=tmp_path / "rv", environ=ENV,
                         http_client=httpx.Client(transport=httpx.MockTransport(reject)),
                         cfg_overrides={"approved": True}, log=lambda *_: None)
    assert len(sent) == 1 and not r["smoke"]["ok"] and "401" in r["smoke"]["error"]
    c = r["costs_step1"]
    assert c["smoke_usd"] == pytest.approx(c["incremental_live_total_usd"]) and c["smoke_usd"] > 0
    md = revalidation_markdown(r)
    assert "SMOKE FAILED" in md and "n/a (dry run)" not in md and "no live investigation completed" in md
    assert f"plan worst case ${r['plan_step1']['worst_case_usd']:.4f}" in md
    assert r["verdict"]["pipeline_ready"] is False
    store.close()

"""R2 live validation experiment, exercised OFFLINE: the real SDK over a mock HTTP transport (no network, no spend)."""

import json
from pathlib import Path

import pytest

from halibut.control import approvals as ap
from halibut.data.providers.base import register
from halibut.data.store import Store

from .test_intel import MARKET, routes
from .test_providers import FakeNet
from .test_r2_loop import MEASUREMENT, fresh

anthropic = pytest.importorskip("anthropic")
httpx = pytest.importorskip("httpx2")

ROOT = Path(__file__).resolve().parents[1]
ORIGINS = ["2025-05-23", "2025-11-23"]
EVAL = "2026-11-23"
ATTEST = "I, the founder and sole operator, authorise this capped R2 validation experiment (offline test)."


@pytest.fixture(scope="module")
def uk(tmp_path_factory):
    from halibut.intel.workflow import investigate
    tmp = tmp_path_factory.mktemp("ukv")
    (tmp / "m.toml").write_text(MARKET + MEASUREMENT)
    store = Store(tmp / "s.sqlite")
    register(store, ROOT / "configs" / "data" / "providers.toml")
    investigate(store, tmp / "m.toml", tmp / "out", tmp / "raw", log=lambda *_: None, transport=FakeNet(routes()))
    store.close()
    return tmp


def fake_claude(sent, usage=(2000, 800)):
    from halibut.agents.loop_agents import SYSTEMS
    from halibut.agents.mock_loop import responder
    roles = {v: k for k, v in SYSTEMS.items()}

    def handler(request):
        body = json.loads(request.content)
        sent.append(body)
        if body["system"].startswith("You are a connectivity check"):
            data = {"ok": True}
        else:
            data = responder(roles[body["system"]], json.loads(body["messages"][0]["content"].split("BUNDLE:", 1)[1]))
        return httpx.Response(200, json={"id": "m", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
                                         "content": [{"type": "text", "text": json.dumps(data)}], "stop_reason": "end_turn",
                                         "stop_sequence": None, "usage": {"input_tokens": usage[0], "output_tokens": usage[1]}})
    return httpx.Client(transport=httpx.MockTransport(handler))


def approve(store, max_usd=0.06):
    aid = ap.request(store, "paid_run", "r2_validation:test_market", "founder", max_usd=max_usd)
    ap.decide(store, aid, "granted", "founder", evidence={"attestation": ATTEST})
    return aid


def test_sole_operator_policy_is_narrow_and_audited(tmp_path):
    store = Store(tmp_path / "s.sqlite")
    aid = ap.request(store, "paid_run", "r2_validation:x", "founder", max_usd=0.06)
    with pytest.raises(ap.AuthorizationError, match="attestation"):
        ap.decide(store, aid, "granted", "founder", evidence={"attestation": "ok"})
    a = ap.decide(store, aid, "granted", "founder", evidence={"attestation": ATTEST})
    assert a["status"] == "granted" and a["decision_evidence"]["self_approved_under_policy"] is True
    assert a["decision_evidence"]["policy_version"] == "approvals-policy-v1"
    big = ap.request(store, "paid_run", "r2_validation:x", "founder", max_usd=0.50)
    with pytest.raises(ap.AuthorizationError, match="limited to"):
        ap.decide(store, big, "granted", "founder", evidence={"attestation": ATTEST})
    xp = ap.request(store, "experiment", "xp:1", "founder")
    with pytest.raises(ap.AuthorizationError, match="not allowed for experiment"):
        ap.decide(store, xp, "granted", "founder", evidence={"attestation": ATTEST, "legal_review_ref": "x",
                                                            "measurable_outcomes": "y"})
    other = ap.request(store, "paid_run", "r2_validation:x", "alex", max_usd=0.06)
    with pytest.raises(ap.AuthorizationError, match="sole operator"):
        ap.decide(store, other, "granted", "alex", evidence={"attestation": ATTEST})
    from halibut.epistemic.events import EventLog
    granted = [e for e in EventLog(store).read(event_type="approval.granted")]
    assert granted[0]["payload"]["self_approved_under_policy"] is True
    store.close()


def test_dry_run_plans_without_any_request(uk, tmp_path):
    from halibut.investigation.live_validation import run_validation
    store = fresh(tmp_path, uk)
    sent = []
    r = run_validation(store, uk / "m.toml", ORIGINS, EVAL, live=False, max_usd=0.06, out_dir=tmp_path / "out",
                       http_client=fake_claude(sent), log=lambda *_: None)
    assert sent == [] and r["plan"]["calls"] == 1 + 3 * len(ORIGINS) and r["plan"]["fits_cap_without_retries"]
    assert set(r["evaluations"]) == {"reference", "mock", "anthropic"} and not r["evaluations"]["anthropic"]
    assert all(p["window_resolvable"] for p in r["preflight"])
    assert store.conn.execute("SELECT COUNT(*) FROM llm_spend_ledger").fetchone()[0] == 0
    store.close()


def test_live_path_offline_end_to_end(uk, tmp_path):
    from halibut.investigation import artifact as art
    from halibut.investigation.live_validation import run_validation
    store = fresh(tmp_path, uk)
    aid = approve(store)
    sent = []
    r = run_validation(store, uk / "m.toml", ORIGINS, EVAL, live=True, approval_id=aid, max_usd=0.06,
                       out_dir=tmp_path / "out", environ={"ANTHROPIC_API_KEY": "sk-ant-test"}, http_client=fake_claude(sent),
                       cfg_overrides={"approved": True}, log=lambda *_: None)
    assert len(sent) == 1 + 3 * len(ORIGINS)  # smoke + 3 calls per origin
    assert all(b["output_config"]["format"]["type"] == "json_schema" for b in sent)  # structured outputs everywhere
    assert r["smoke"]["ok"] and r["verdict"]["pipeline_ready"], r["criteria_results"]
    assert set(r["criteria_results"]) == set(r["criteria"])
    out = Path(r["output_dir"])
    files = sorted(p.name for p in (out / "investigations").glob("*.json"))
    assert len(files) == 3 * len(ORIGINS)
    for f in (out / "investigations").glob("*.json"):
        assert art.validate(json.loads(f.read_text())) == []
    assert (out / "comparison.md").read_text().startswith("# R2 live validation")
    spend = r["cost"]["live_spend_usd"]
    assert 0 < spend <= 0.06 and r["cost"]["ledger_attempts"] == len(sent)
    assert all(c["identical_evidence"] for c in r["comparisons"].values())
    store.close()


def test_live_needs_approval_and_the_cap_holds_across_the_experiment(uk, tmp_path):
    from halibut.investigation.live_validation import run_validation
    store = fresh(tmp_path, uk)
    sent = []
    kw = dict(out_dir=tmp_path / "out", environ={"ANTHROPIC_API_KEY": "sk-ant-test"}, http_client=fake_claude(sent),
              cfg_overrides={"approved": True}, log=lambda *_: None)
    with pytest.raises(ap.AuthorizationError):
        run_validation(store, uk / "m.toml", ORIGINS, EVAL, live=True, approval_id=None, max_usd=0.06, **kw)
    aid = approve(store, max_usd=0.004)  # far below the plan: the guard must stop before exceeding it
    r = run_validation(store, uk / "m.toml", ORIGINS, EVAL, live=True, approval_id=aid, max_usd=0.004, **kw)
    assert r["cost"]["live_spend_usd"] <= 0.004 + 1e-12
    assert not r["verdict"]["pipeline_ready"]  # stopped by the budget, reported honestly
    stopped = [e for e in r["evaluations"]["anthropic"].values() if e["status"] == "stopped"]
    assert stopped and any("spend" in (e["stop_reason"] or "") or "cap" in (e["stop_reason"] or "") for e in stopped)
    store.close()

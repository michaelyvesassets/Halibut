"""R2 Step 2 offline: the loop.v2 pipeline, the blinded critic benchmark and the pre-registered runner (fake transport)."""

import json
from pathlib import Path

import pytest

from halibut.agents.loop_agents import SYSTEMS as V1
from halibut.agents.loop_v2_offline import v2_adapter
from halibut.agents.mock_loop import responder as mock
from halibut.control import approvals as ap
from halibut.investigation import artifact as art
from halibut.investigation import critic_benchmark as cb
from halibut.investigation import loop_v2 as v2
from halibut.investigation import step2
from halibut.investigation.evaluation import evaluate_document

from .test_r2_live_validation import ATTEST, uk  # noqa: F401  (uk is a fixture)
from .test_r2_loop import UK, fresh, run, uk_store  # noqa: F401

httpx = pytest.importorskip("httpx2")
ENV = {"ANTHROPIC_API_KEY": "sk-ant-test"}


def test_v2_pipeline_offline(uk_store, tmp_path):
    store = fresh(tmp_path, uk_store)
    doc = run(store, uk_store / "m.toml", contract="loop.v2", measurements="extended", **UK)
    assert doc["status"] == "completed" and art.validate(doc) == []
    assert doc["provenance"]["prompt_version"] == "loop.v2:extended"
    st = doc["stages"]
    assert st["information_gaps"] and all(g["type"] == "information_gap" and g["status"] == "open" for g in st["information_gaps"])
    assert all(g["data"]["autonomous_research"].startswith("disabled") for g in st["information_gaps"])
    assert any(t["data"].get("discriminates_from") for t in st["tests"])
    assert {c["data"]["dimension"] for c in st["critiques"]} <= set(v2.DIMENSIONS)
    assert all("numeric_claims" in o["data"] for o in st["strategic_options"])
    gap_opt = [o for o in st["strategic_options"] if o["data"]["intended_effect"]["template"] == "segment_share_gap"]
    assert gap_opt and gap_opt[0]["data"]["simulation"]["status"] == "simulated"  # pairwise targets are now simulable
    ev = evaluate_document(doc)
    e, c = ev["v2"], ev["common"]
    assert e["tests"]["discriminating"] >= 2 and e["hypotheses_with_rivals"] >= 2  # compliance (declared rivals)
    assert c["competing_pairs"] and c["tests"]["disputed"] >= 2                  # common method, same for every arm
    assert c["tests"]["informative_confirmations"] + c["tests"]["informative_refutations"] >= 1
    assert c["label"] == "predictive support, not causal proof"
    v1 = run(fresh(tmp_path / "v1" if (tmp_path / "v1").mkdir() is None else tmp_path, uk_store), uk_store / "m.toml", **UK)
    assert "information_gaps" not in v1["stages"] and v1["provenance"]["prompt_version"] == "loop.v1"
    e1 = evaluate_document(v1)
    assert e1["v2"] is None and e1["common"]["tests"]["registered"] == evaluate_document(v1)["tests"]["registered"]
    store.close()


def test_v1_and_v2_ids_differ_and_v1_ids_do_not_move(uk_store, tmp_path):
    with pytest.raises(ValueError, match="frozen"):
        run(fresh(tmp_path, uk_store), uk_store / "m.toml", contract="loop.v1", measurements="extended", **UK)
    a = run(fresh(tmp_path / "a" if (tmp_path / "a").mkdir() is None else tmp_path, uk_store), uk_store / "m.toml", **UK)
    b = run(fresh(tmp_path / "b" if (tmp_path / "b").mkdir() is None else tmp_path, uk_store), uk_store / "m.toml",
            contract="loop.v2", **UK)
    assert a["input_id"] != b["input_id"] and a["content_hash"] != b["content_hash"]


def _bench(uk_store, tmp_path):
    import dataclasses
    from halibut.data.store import Store
    from halibut.intel.market import load_market
    from halibut.world.context import build_slice
    from halibut.world.panel import build_panel
    st = fresh(tmp_path, uk_store)
    m = dataclasses.replace(load_market(uk_store / "m.toml"), as_of=UK["as_of"])
    p = build_panel(st, m, UK["as_of"])
    s, met = build_slice(m, p, {"status": "covered", "contract": {}, "gaps": []}, [], extended=True)
    return cb.build(p, met), v2.compact_slice(s, met), met, st


def test_benchmark_is_blinded_and_scored_structurally(uk_store, tmp_path):
    bench, sl, met, st = _bench(uk_store, tmp_path)
    text = json.dumps([cb.payload(s, sl) for s in bench["sets"]])
    for word in ("planted", "defect", "clean", "wrong_number", "contradicted", "misencoded"):
        assert word not in text  # nothing in the payload marks a defect
    assert bench["key_sha256"] and len(bench["key"]) == sum(len(s["hypotheses"]) for s in bench["sets"])

    def critiques(policy):
        out = {}
        for s in bench["sets"]:
            out[s["set_id"]] = []
            for h in s["hypotheses"]:
                k = bench["key"][f"{s['set_id']}:{h['ref']}"]
                out[s["set_id"]] += policy(h, k)
        return out
    oracle = cb.score(critiques(lambda h, k: [] if k["kind"] == "clean" else [
        {"hypothesis_ref": h["ref"], "dimension": k["accept"][0], "severity": "high", "evidence": k["evidence"][:1]}]), bench)
    assert oracle["recall"] == 1.0 and oracle["precision"] == 1.0 and oracle["clean_false_alarm_rate"] == 0.0
    harsh = cb.score(critiques(lambda h, k: [{"hypothesis_ref": h["ref"], "dimension": d, "severity": "high",
                                              "evidence": list(met)[:1]} for d in v2.DIMENSIONS]), bench)
    assert harsh["clean_false_alarm_rate"] == 1.0 and harsh["precision"] < 0.5  # excessive criticism is penalised
    wrong_ev = cb.score(critiques(lambda h, k: [] if k["kind"] == "clean" else [
        {"hypothesis_ref": h["ref"], "dimension": k["accept"][0], "severity": "high", "evidence": ["metric:share:L4:byd"]
         if k["evidence"] else []}]), bench)
    assert wrong_ev["recall_by_type"]["contradicted_assumption"] == 0.0  # evidence-bound types need the right metric
    words = cb.score(critiques(lambda h, k: [{"hypothesis_ref": h["ref"], "dimension": "alternative_explanation",
                                              "severity": "medium", "evidence": [], "statement": "contradicted wrong number"}]),
                     bench)
    assert words["recall"] == 0.0  # no keyword can score a detection
    st.close()


def _fake(sent):
    roles = {v: k for k, v in list(V1.items()) + list(v2.SYSTEMS.items())}
    answer = v2_adapter(mock, rich=True)

    def handler(request):
        body = json.loads(request.content)
        sent.append(body)
        if body["system"].startswith("You are a connectivity check"):
            data = {"ok": True}
        else:
            data = answer(roles[body["system"]], json.loads(body["messages"][0]["content"].split("BUNDLE:", 1)[1]))
        return httpx.Response(200, json={"id": "m", "type": "message", "role": "assistant", "model": "claude-haiku-5-5",
                                         "content": [{"type": "text", "text": json.dumps(data)}], "stop_reason": "end_turn",
                                         "stop_sequence": None, "usage": {"input_tokens": 3000, "output_tokens": 900}})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_step2_runner_end_to_end_with_fake_transport(uk, tmp_path):
    store = fresh(tmp_path, uk)
    m = str(uk / "m.toml")
    runs = {"t": {"investigations": [(m, UK["as_of"], arm, UK["evaluate_as_of"], "test") for arm in step2.ARMS],
                  "benchmark": (m, UK["as_of"])}}
    with pytest.raises(ap.AuthorizationError):  # no approval: nothing is sent
        step2.run_step2(store, tmp_path / "s.sqlite", "t", live=True, approval_id=None, max_usd=0.10, out_dir=tmp_path / "o",
                        environ=ENV, http_client=_fake([]), cfg_overrides={"approved": True}, runs=runs, log=lambda *_: None)
    aid = ap.request(store, "paid_run", "r2_step2:t", "founder", max_usd=0.10)
    ap.decide(store, aid, "granted", "founder", evidence={"attestation": ATTEST})
    sent = []
    r = step2.run_step2(store, tmp_path / "s.sqlite", "t", live=True, approval_id=aid, max_usd=0.10, out_dir=tmp_path / "o",
                        environ=ENV, http_client=_fake(sent), cfg_overrides={"approved": True}, runs=runs, log=lambda *_: None)
    assert len(sent) == 1 + 3 * 3 + 2  # smoke, 3 arms x 3 roles, 2 benchmark sets (no retries: RETRIES = 0)
    assert r["plan"]["retries"] == 0 and r["plan"]["worst_case_with_retries_usd"] == r["plan"]["worst_case_usd"]
    assert "$defs" in json.dumps(sent[0])  # the smoke test checks $defs/$ref support before anything else
    res = r["criteria_results"]
    assert set(res) == set(step2.CRITERIA) and all(res[k] for k in ("A1_smoke_with_defs_ok", "A2_all_investigations_completed",
                                                                  "A4_documents_validate", "A5_matched_evidence",
                                                                  "A6_spend_within_cap", "D1_no_ineligible_protocol_executable"))
    assert res["P1_registered_at_run_time_with_future_window"] is None  # not evaluable without prospective investigations
    sc = r["critic_benchmark"]["score"]
    assert sc["planted"] >= 5 and "precision_by_dimension" in sc and "evidence_reference_accuracy" in sc
    assert r["spend_usd"] <= 0.10
    arms = r["summary"]["arms"]
    assert set(arms) == set(step2.ARMS) and "combined_contract_and_evidence_presentation_B_minus_A" in r["summary"]["contrasts"]
    assert arms["A_v1_base"]["compliance_v2"] is None and arms["C_v2_extended"]["compliance_v2"]  # compliance apart
    assert all(set(a["per_role"]["hypothesis_generator"]) >= {"input_tokens", "output_tokens", "latency_s", "cache_hits"}
               for a in arms.values())
    out = Path(r["output_dir"])
    assert (out / "execution_records.json").exists() and len(json.loads((out / "manifest.json").read_text())["investigations"]) == 3
    assert ap.get(store, aid)["used_by"] == r["experiment_id"]
    with pytest.raises(ap.AuthorizationError):  # single use
        step2.run_step2(store, tmp_path / "s.sqlite", "t", live=True, approval_id=aid, max_usd=0.10, out_dir=tmp_path / "o",
                        environ=ENV, http_client=_fake([]), cfg_overrides={"approved": True}, runs=runs, log=lambda *_: None)
    store.close()


def test_2b_is_separately_authorized_and_prospective_needs_fresh_data(uk, tmp_path):
    store = fresh(tmp_path, uk)
    aid = ap.request(store, "paid_run", "r2_step2:2b", "founder", max_usd=0.10)
    ap.decide(store, aid, "granted", "founder", evidence={"attestation": ATTEST})
    sent = []
    with pytest.raises(ap.AuthorizationError, match="separately authorized"):
        step2.run_step2(store, tmp_path / "s.sqlite", "2b", live=True, approval_id=aid, max_usd=0.10, out_dir=tmp_path / "o",
                        environ=ENV, http_client=_fake(sent), cfg_overrides={"approved": True}, log=lambda *_: None)
    m = str(uk / "m.toml")
    runs = {"p": {"investigations": [(m, None, "C_v2_extended", None, "prospective")], "benchmark": None}}
    assert step2.publication_check(store)["fresh"]  # the fixture just fetched its data
    store.conn.execute("UPDATE snapshots SET retrieved_at='2026-01-01T00:00:00Z' WHERE dataset_id LIKE 'dft_veh0160%'")
    store.conn.commit()
    with pytest.raises(ap.AuthorizationError, match="refresh the data"):  # stale publication data: refused
        step2.run_step2(store, tmp_path / "s.sqlite", "p", live=True, approval_id=aid, max_usd=0.10, out_dir=tmp_path / "o",
                        environ=ENV, http_client=_fake(sent), cfg_overrides={"approved": True}, runs=runs, log=lambda *_: None)
    assert sent == [] and ap.get(store, aid)["used_by"] is None
    store.close()


def test_prospective_registration_is_never_backdated_and_has_a_future_window(uk_store, tmp_path):
    from halibut.data.store import utc_now
    with pytest.raises(ValueError, match="registers on its run date"):
        run(fresh(tmp_path, uk_store), uk_store / "m.toml", contract="loop.v2", prospective=True, as_of="2025-05-23")
    doc = run(fresh(tmp_path / "x" if (tmp_path / "x").mkdir() is None else tmp_path, uk_store), uk_store / "m.toml",
              contract="loop.v2", prospective=True)
    today = utc_now()[:10]
    assert doc["objective"]["as_of"].startswith(today) and doc["objective"]["evaluate_as_of"] is None
    fut = [t for t in doc["stages"]["tests"] if t["data"].get("horizon") == "future_window"]
    nom = [t for t in doc["stages"]["tests"] if t["data"].get("horizon") == "nominal"]
    assert fut and nom and all(t["data"]["registered_at"][:10] == today for t in fut + nom)
    assert all(set(t["data"]["window_classes"].values()) == {"future"} for t in fut)
    assert all(t["status"] == "registered" and not [r for r in doc["stages"]["test_results"]
                                                     if any(ln["to"] == t["id"] for ln in r["links"])] for t in fut)
    assert not [t for t in fut if "already_published" in t["data"]["window_classes"].values()]


def test_assumptions_cannot_disguise_facts():
    opt = {"title": "Lift BYD", "risks": ["BYD's share is 1.5% already"], "assumptions": [], "cost": {"basis": "unknown"},
           "intended_effect": {"template": "share", "entity": "byd", "direction": "increase", "magnitude_low_pp": 0.5,
                               "magnitude_high_pp": 1.5, "basis": "ASSUMED lift of 0.5 to 1.5 pp; BYD holds 1.5 today"},
           "numeric_claims": [{"text": t, "value": float(t), "unit": "pp", "kind": "assumed", "decimals": 1, "operation": "none",
                               "metric_ids": []} for t in ("0.5", "1.5")]}
    p = v2.disguised_assumptions(opt)
    assert any("risks" in x for x in p) and any("not marked as assumed" in x for x in p)
    ok = {**opt, "risks": ["slow effect"], "intended_effect": {**opt["intended_effect"], "basis": "ASSUMED lift of 0.5 to 1.5 pp"}}
    assert v2.disguised_assumptions(ok) == []


def test_preregistered_criteria_are_frozen():
    assert step2.CRITERIA_HASH == FROZEN_STEP2_CRITERIA_HASH
    assert set(step2.THRESHOLDS) <= {k.split("_")[0] for k in step2.CRITERIA}
    assert all(arm in step2.ARMS for r in step2.RUNS.values() for _, _, arm, _, _ in r["investigations"])
    prospective = [x for r in step2.RUNS.values() for x in r["investigations"] if x[4] == "prospective"]
    assert prospective and all(x[1] is None and x[3] is None for x in prospective)  # origin = the real registration date
    assert step2.RETRIES == 0 and set(step2.PROSPECTIVE_PROTOCOL) >= {"registration", "resolution", "no_tuning"}


FROZEN_STEP2_CRITERIA_HASH = "223362694f7d254f225808caecee081eeccb0444be4d582035eb667b43e3d91d"  # pre-registered 2026-11-26; must not change after any Step 2 call


@pytest.mark.parametrize("seed", range(8))
def test_random_v2_schema_valid_output_never_crashes(uk_store, tmp_path, monkeypatch, seed):
    import math
    import random
    from halibut.epistemic import schemas as sch
    from halibut.investigation import loop as loop_mod
    from .test_r2_resilience import sample
    rng, orig = random.Random(500 + seed), loop_mod.call_role

    def fuzz(router, store, role, payload, schema, run_id, system=None):
        res = orig(router, store, role, payload, schema, run_id, system=system)
        local = v2.inline(schema) if "$defs" in schema else schema
        data = sample(local, rng)
        assert sch.validate(data, local) == []
        return {**res, "data": data}
    monkeypatch.setattr(loop_mod, "call_role", fuzz)
    doc = run(fresh(tmp_path, uk_store), uk_store / "m.toml", contract="loop.v2",
              measurements=("base", "extended")[seed % 2], **UK)
    assert doc["status"] in ("completed", "stopped") and art.validate(doc) == []
    assert not doc["failures"]["completion"]["internal_error"]
    for s in doc["stages"]["simulations"]:
        assert all(math.isfinite(v) for v in s["data"]["outcome_range"])
    e = evaluate_document(doc)
    assert e["v2"] is not None

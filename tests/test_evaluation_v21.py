"""Secondary evaluation r2_eval.v2.1, critic benchmark v2 and the sealed holdout (Architecture V2 milestone 2), offline."""

import json

import pytest

from halibut.investigation import critic_benchmark as cb
from halibut.investigation import evaluation_v21 as E
from halibut.investigation import holdout as H
from halibut.investigation.evaluation import evaluate_document

from .test_r2_loop import UK, fresh, run, uk_store  # noqa: F401
from .test_r2_registry_approvals import panel as synthetic_panel


def _h(ref, imps, rivals=(), cause="", direction="increase", effect="share", explains="metric:share:L4:a", ev=None, nov="original"):
    return {"id": f"c:{ref}", "data": {"ref": ref, "rivals": list(rivals), "explains": explains, "novelty": nov,
                                       "mechanism": {"cause": cause, "pathway": "", "effect_template": effect, "direction": direction},
                                       "observable_implications": [{"id": f"I{i + 1}", "template": t, "entity": e, "entity_b": b,
                                                                    "comparator": c} for i, (t, e, b, c) in enumerate(imps)]},
            "links": [{"relation": "cites", "to": x} for x in (ev or [explains])]}


A, B = "dealer network expansion widens availability", "temporary price incentives pull demand forward"
UP = [("share", "a", "", "increase_vs_L4"), ("segment_share", "a", "", "increase_vs_L4")]
DOWN1 = [("share", "a", "", "decrease_vs_L4"), ("segment_share", "a", "", "increase_vs_L4")]
FLIP = [("share", "a", "", "decrease_vs_L4"), ("segment_share", "a", "", "decrease_vs_L4")]


def test_evidence_consistency_is_an_engine_check():
    m = {"metric:share:L4:a": 0.10, "metric:share:P4:a": 0.05}
    assert E.evidence_consistency(_h("H1", UP, direction="increase"), m)["status"] == "consistent"
    assert E.evidence_consistency(_h("H2", UP, direction="decrease"), m)["status"] == "contradicted"
    assert E.evidence_consistency(_h("H3", UP, effect="other"), m)["status"] == "consistent"  # 'explains' supplies the metric
    assert E.evidence_consistency(_h("H4", UP, effect="other", explains=None, ev=[]), m)["status"] == "undetermined"


@pytest.mark.parametrize("a,b,cons_b,cls", [
    (_h("H1", UP, ["H2"], A), _h("H2", DOWN1, [], B), "consistent", "genuine"),
    (_h("H1", UP, ["H2"], A), _h("H2", DOWN1, [], B), "contradicted", "contradicted_at_registration"),
    (_h("H1", UP, ["H2"], A), _h("H2", FLIP, [], B), "consistent", "pure_negation"),
    (_h("H1", UP, ["H2"], A), _h("H2", DOWN1, [], A), "consistent", "near_duplicate"),
    (_h("H1", UP, ["H2"], A), _h("H2", DOWN1, [], B, explains="metric:share:L4:z", ev=["metric:share:L4:z"]), "consistent",
     "not_same_observation"),
    (_h("H1", UP, ["H2"], A), _h("H2", UP, [], B), "consistent", "no_opposition"),
])
def test_competing_pairs_are_classified_and_only_genuine_ones_count(a, b, cons_b, cls):
    cons = {a["id"]: {"status": "consistent"}, b["id"]: {"status": cons_b}}
    p = E.classify_pairs([a, b], cons)
    assert [x["class"] for x in p] == [cls] and p[0]["plausibility"].startswith("not machine-assessable")


def test_baselines_use_origin_evidence_only():
    p = synthetic_panel()
    r = E.reference_class_rate(p, "share")
    assert r is None or 0 <= r <= 1
    # the reference class only ever uses windows inside the panel (the origin evidence): truncating the panel's
    # future cannot change it, because there is none
    spec = {"template": "share", "entity": "a", "entity_b": None, "comparator": "increase_vs_L4",
            "reference_value": p.value("share", "a", p.windows()[0])[0], "reference_source": "L4 window",
            "target_periods": ["2099-Q1", "2099-Q2"]}
    b = E.extra_baselines(spec, p, {})
    assert "holt" in b and set(b) <= {"holt", "reference_class"}


def test_wilson_interval_and_skill_tiers():
    lo, hi = E.wilson(8, 10)
    assert 0.49 < lo < 0.5 and 0.94 < hi < 0.95 and E.wilson(0, 0) is None


def test_secondary_eval_end_to_end_preserves_frozen_scores(uk_store, tmp_path):
    from halibut.epistemic import predictions as P
    store = fresh(tmp_path, uk_store)
    doc = run(store, uk_store / "m.toml", contract="loop.v2", measurements="extended", **UK)
    frozen = json.dumps(evaluate_document(doc), sort_keys=True, default=str)
    P.sync(store, uk_store, include_scripted=True)
    P.resolve(store, uk_store)
    ev = E.evaluate(doc, store, None)
    assert ev["version"] == "r2_eval.v2.1" and ev["causal_validity"].startswith("not assessed")
    assert ev["calibration"].startswith("not assessable")
    assert all(t["status"] in ("resolved", "pending", "ambiguous", "unavailable", "not_in_ledger") for t in ev["tests"])
    for t in ev["tests"]:
        if t["informative"]:
            assert t["committed_baselines"] >= 1 and not any(v.get("predicts") for v in t["baselines"].values())
        if t["skill_tier"] == "beats_all":
            assert t["committed_baselines"] >= E.BEATS_ALL_MIN_BASELINES
    agg = E.aggregate([ev])
    cell = next(iter(agg["by_contract"].values()))["by_temporal_and_basis"]
    for c in cell.values():
        assert c["unique_realised_outcomes"] <= c["resolved"] and (c["hit_rate"] is None) == (c["resolved"] == 0)
    assert json.dumps(evaluate_document(doc), sort_keys=True, default=str) == frozen  # the frozen readout is untouched
    store.close()


def test_pending_predictions_are_never_scored(uk_store, tmp_path):
    from halibut.epistemic import predictions as P
    store = fresh(tmp_path, uk_store)
    doc = run(store, uk_store / "m.toml", contract="loop.v2", prospective=True)
    P.sync(store, uk_store, include_scripted=True)
    P.resolve(store, uk_store)
    ev = E.evaluate(doc, store, None)
    assert ev["tests"] and all(t["status"] == "pending" and t["hit"] is None and t["skill_tier"] is None for t in ev["tests"])
    assert {t["temporal"] for t in ev["tests"]} <= {"future", "nowcast", "unpublished_historical"}
    assert {t["leakage_risk"] for t in ev["tests"] if t["temporal"] == "future"} == {"low"}
    cells = E.aggregate([ev])["by_contract"]["loop.v2:base"]["by_temporal_and_basis"]
    assert all(c["hit_rate"] is None and c["unique_realised_outcomes"] == 0 for c in cells.values())
    store.close()


def _bench(uk_store, tmp_path):
    import dataclasses
    from halibut.intel.market import load_market
    from halibut.world.context import build_slice
    from halibut.world.panel import build_panel
    st = fresh(tmp_path, uk_store)
    m = dataclasses.replace(load_market(uk_store / "m.toml"), as_of=UK["as_of"])
    p = build_panel(st, m, UK["as_of"])
    s, met = build_slice(m, p, {"status": "covered", "contract": {}, "gaps": []}, [], extended=True)
    return cb.build_v2(p, met), met, st


def test_purified_benchmark_has_one_defect_per_item_and_grounded_controls(uk_store, tmp_path):
    b, met, st = _bench(uk_store, tmp_path)
    assert b["audit"]["status"] == "proposed" and b["audit"]["checklist"]
    kinds = [v["kind"] for v in b["key"].values()]
    assert sorted(set(kinds) - {"clean"}) == sorted(cb.EXPECTED_SEVERITY) and kinds.count("clean") == 2
    hyps = {f"{s['set_id']}:{h['ref']}": h for s in b["sets"] for h in s["hypotheses"]}
    for k, v in b["key"].items():
        text = json.dumps(hyps[k]).lower()
        if v["kind"] != "unsupported_detail":
            for word in ("launch", "price", "dealer", "loyal", "fleet"):
                assert word not in text, (k, word)  # no unevidenced mechanism outside the planted unsupported detail
    payload = json.dumps(b["sets"])
    for word in ("planted", "defect", "clean", "wrong_number"):
        assert word not in payload  # blinded
    st.close()


def test_structured_critic_scores_do_not_reward_volume(uk_store, tmp_path):
    b, met, st = _bench(uk_store, tmp_path)

    def crit(policy):
        out = {}
        for s in b["sets"]:
            out[s["set_id"]] = [c for h in s["hypotheses"] for c in policy(h, b["key"][f"{s['set_id']}:{h['ref']}"])]
        return out
    oracle = cb.score_v2(crit(lambda h, k: [] if k["kind"] == "clean" else [
        {"hypothesis_ref": h["ref"], "dimension": k["accept"][0], "severity": k["expected_severity"],
         "evidence": k["evidence"][:1]}]), b, schema_failures=0)
    assert oracle["detection_rate"] == oracle["classification_rate"] == oracle["severity_accuracy"] == 1.0
    assert oracle["evidence_reference_accuracy"] == 1.0 and oracle["false_positives_on_clean"] == 0
    assert oracle["label"].startswith("UNAUDITED")  # not audited until a person signs off
    spam = cb.score_v2(crit(lambda h, k: [{"hypothesis_ref": h["ref"], "dimension": d, "severity": "high", "evidence": []}
                                          for d in cb.DEFECT_CLAIM_DIMS + cb.CAVEAT_DIMS]), b, schema_failures=3)
    assert spam["false_positives_on_clean"] > 0 and spam["misdirected_high_severity"] > 0
    assert spam["evidence_reference_accuracy"] < 1.0 and spam["schema_failures"] == 3
    caveat = cb.score_v2(crit(lambda h, k: [{"hypothesis_ref": h["ref"], "dimension": "alternative_explanation",
                                             "severity": "medium", "evidence": []}]), b)
    assert caveat["false_positives_on_clean"] == 0 and caveat["detection_rate"] == 0.0 and caveat["generic_caveats"] > 0
    st.close()


def test_sealed_holdout_is_refused_for_development(tmp_path, monkeypatch):
    seal = tmp_path / "seal.json"
    body = H.write_seal([("mkt", "2024-05-23")], "salt", seal)
    assert "2024-05-23" not in seal.read_text() and body["status"] == "sealed" and body["eval_token_sha256"] is None
    with pytest.raises(H.HoldoutSealed):
        H.guard("mkt", "2024-05-23", seal)
    H.guard("mkt", "2025-11-23", seal)  # development origins are allowed
    monkeypatch.setenv(H.TOKEN_ENV, "anything")
    with pytest.raises(H.HoldoutSealed):  # no token recorded yet: nothing opens it
        H.guard("mkt", "2024-05-23", seal)


def test_the_committed_seal_blocks_holdout_origins_in_the_loop(uk_store, tmp_path, monkeypatch):
    import hashlib
    seal = tmp_path / "seal.json"
    H.write_seal([("test_market", "2025-05-23")], "s", seal)
    monkeypatch.setattr(H, "SEAL", seal)
    monkeypatch.setattr(H.guard, "__defaults__", (seal,))
    with pytest.raises(H.HoldoutSealed):
        run(fresh(tmp_path, uk_store), uk_store / "m.toml", **UK)
    from pathlib import Path
    committed = json.loads((Path(__file__).resolve().parents[1] / "registry" / "holdout" / "seal.json").read_text())
    assert committed["n"] == len(committed["entries"]) and committed["eval_token_sha256"] is None
    assert hashlib.sha256(json.dumps({k: v for k, v in committed.items() if k != "seal_sha256"},
                                     sort_keys=True).encode()).hexdigest() == committed["seal_sha256"]


def test_manual_overlay_only_adds_labelled_contradictions():
    a = _h("H1", UP, ["H2"], A)
    b = _h("H2", DOWN1, [], B)
    cons = {a["id"]: {"status": "consistent"}, b["id"]: {"status": "consistent"}}
    assert E.classify_pairs([a, b], cons)[0]["class"] == "genuine"
    cons[b["id"]] = {"status": "contradicted", "reason": "manual review: ..."}
    assert E.classify_pairs([a, b], cons)[0]["class"] == "contradicted_at_registration"
    from pathlib import Path
    mr = json.loads((Path(__file__).resolve().parents[1] / "registry" / "evaluations" / "manual_reviews.json").read_text())
    assert all(r["status"] == "contradicted" and r["source"] and r["reason"] for r in mr["reviews"])


# ---------------------------------------------------------------------------------------------- critic benchmark v3
def _bench_v3(uk_store, tmp_path):
    import dataclasses
    from halibut.intel.market import load_market
    from halibut.world.context import build_slice
    from halibut.world.panel import build_panel
    st = fresh(tmp_path, uk_store)
    m = dataclasses.replace(load_market(uk_store / "m.toml"), as_of=UK["as_of"])
    p = build_panel(st, m, UK["as_of"])
    s, met = build_slice(m, p, {"status": "covered", "contract": {}, "gaps": []}, [], extended=True)
    return p, met, st


def test_v3_canonical_items_are_clean_and_each_mutation_is_isolated():
    p = synthetic_panel()
    met = p.metrics(extended=True)
    for e in ("a", "b"):
        assert cb.check_item(cb.canonical(e, met), met) == []
    e = "a"
    c = cb.canonical(e, met)
    for kind in ("wrong_number", "misencoded_test", "unsupported_detail", "causal_overclaim"):
        x, _ = cb.mutate(kind, c, e, met, other="b")
        assert cb.check_item(x, met) == cb.EXPECTED_ISSUES[kind], kind
        diff = [k for k in ("statement", "assumptions", "observable_implications", "mechanism") if x[k] != c[k]]
        assert len(diff) == 1, (kind, diff)  # exactly one changed component


def test_v3_boundary_checks_catch_impossible_and_contradicted_items():
    met = {"metric:segment_share:L4:t": {"value": 0.10}, "metric:segment_share:P4:t": {"value": 0.13},
           "metric:segment_mix:L4:t": {"value": 1.0}, "metric:non_segment_share:L4:t": {"value": 0.0}}
    c = cb.canonical("t", met)
    assert cb.check_item(c, met) == []
    imp, ev = cb.mutate("impossible_prediction", c, "t", met)
    assert cb.check_item(imp, met) == ["impossible_prediction"] and ev == ["metric:segment_mix:L4:t"]
    con, _ = cb.mutate("contradicted_assumption", c, "t", met)
    assert cb.check_item(con, met) == ["contradicted_assumption"]
    bad = {**c, "mechanism": {**c["mechanism"], "direction": "increase"}}  # mechanism and test disagree
    assert "direction_inconsistent" in cb.check_item(bad, met)


def test_v3_build_scoring_and_separation(uk_store, tmp_path):
    p, met, st = _bench_v3(uk_store, tmp_path)
    try:
        b = cb.build_v3(p, met)
    except ValueError:
        pytest.skip("fixture panel lacks two mixed brands (the real UK panel is used for the audit package)")
    payload = json.dumps(b["sets"])
    assert "planted" not in payload and "clean" not in payload  # keys stay apart from critic inputs
    for s in b["sets"]:
        ents = [b["key"][f"{s['set_id']}:{h['ref']}"]["entity"] for h in s["hypotheses"]]
        assert len(ents) == len(set(ents))  # no variant beside its own canonical
    st.close()


def test_v3_scorer_separates_dimensions_and_does_not_penalise_legitimate_extra_criticism():
    key = {"S1:H1": {"kind": "wrong_number", "accept": ["numerical_accuracy", "factual_contradiction"],
                     "evidence": ["metric:x"], "min_severity": "medium", "ideal_severity": "high"},
           "S1:H2": {"kind": "clean", "accept": [], "evidence": [], "min_severity": None, "ideal_severity": None}}
    b = {"key": key, "audit": {"status": "proposed"}}
    good = {"S1": [{"hypothesis_ref": "H1", "dimension": "factual_contradiction", "severity": "high", "evidence": ["metric:x"]},
                   {"hypothesis_ref": "H1", "dimension": "alternative_explanation", "severity": "medium", "evidence": []},
                   {"hypothesis_ref": "H2", "dimension": "test_validity", "severity": "low", "evidence": []}]}
    s = cb.score_v3(good, b)
    assert s["detection_rate"] == s["classification_rate"] == s["severity_accuracy"] == s["evidence_reference_accuracy"] == 1
    assert s["presumed_erroneous_on_clean"] == 0 and s["label"].startswith("UNAUDITED")
    cats = sorted(r["category"] for r in s["critiques_for_review"])
    assert cats == ["additional_on_defective_item_methodological", "methodological_comment_on_clean"]  # recorded, not penalised
    assert "not a comprehensive precision" in s["precision_note"]
    weak = {"S1": [{"hypothesis_ref": "H1", "dimension": "numerical_accuracy", "severity": "low", "evidence": []},
                   {"hypothesis_ref": "H2", "dimension": "numerical_accuracy", "severity": "medium", "evidence": []}]}
    w = cb.score_v3(weak, b)
    assert w["detection_rate"] == 1 and w["classification_rate"] == 1 and w["severity_accuracy"] == 0
    assert w["evidence_reference_accuracy"] == 0 and w["presumed_erroneous_on_clean"] == 1
    high = cb.score_v3({"S1": [{"hypothesis_ref": "H2", "dimension": "causal_identification", "severity": "high", "evidence": []}]}, b)
    assert high["presumed_erroneous_on_clean"] == 0  # high methodological criticism is assessed on merits, not auto-false
    assert high["critiques_for_review"][0]["category"] == "high_methodological_on_clean_assess_on_merits"


def test_v3_committed_package_counts_and_critic_inputs():
    from pathlib import Path
    b = json.loads((Path(__file__).resolve().parents[1] / "registry" / "critic_audit" /
                    "critic_benchmark_v3_uk_bev_2026-02-23.json").read_text())
    assert b["audit"]["status"] != "audited" and b["audit"]["reviews"][0]["independent_human_audit"] is False
    s = cb.score_v3({}, b)
    assert s["planted_defects"] == 7 and s["clean_control_entries"] == 5 and s["unique_clean_hypotheses"] == 3
    assert all(v["counts_as_defects"] == 1 for v in b["key"].values() if v["kind"] != "clean")
    assert cb._h(b["key"]) == b["key_sha256"] and cb._h(b["sets"]) == b["payload_sha256"]
    sl = {"market": {}, "metrics": [], "metric_id_format": "x", "templates": {}, "windows": {"L4": ["2025-Q3"], "P4": ["2024-Q3"]}}
    pl = cb.payload_v3(b["sets"][0], sl)
    assert pl["slice"]["windows"]["L4"] == ["2025-Q3"] and "next TWO quarters after L4" in pl["slice"]["windows"]["test_window"]
    assert "not company sales" in pl["slice"]["measure"] and "key" not in json.dumps(pl)

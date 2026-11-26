"""loop.v2 contract (R2 Step 2): pairwise targets, objective agreement, exact numeric claims, discrimination, gaps and
structured critiques. loop.v1 requests (Step 1 cache reuse) must be unchanged."""

import hashlib
import json
from pathlib import Path

from halibut.agents import loop_agents
from halibut.epistemic import schemas as sch
from halibut.investigation import compiler
from halibut.investigation import loop_v2 as v2

from .test_r2_registry_approvals import panel as synthetic_panel

FIX = json.loads((Path(__file__).parent / "fixtures" / "haiku_r2r_57fa83dc8f2f_critic_strategist_replies.json").read_text())
OPTS = {o["ref"]: o for o in next(r for r in FIX["replies"] if r["role"] == "strategist")["data"]["options"]}
ENTS = ["byd", "volkswagen", "tesla", "kia", "mg", "toyota", "ford", "bmw"]
VALUES = {"metric:segment_share:L4:byd": 0.036014, "metric:segment_share:P4:byd": 0.012041,
          "metric:share:L4:byd": 0.012426, "metric:share:P4:byd": 0.001969, "metric:segment_mix:L4:byd": 0.626335,
          "metric:segment_mix:P4:byd": 1.0}


def test_v1_prompts_and_schemas_are_unchanged():
    pins = {"hypothesis_generator": "f3773bdc8e471027", "loop_critic": "d5442635fac8b051", "strategist": "03f28296bad4c285"}
    for role, text in loop_agents.SYSTEMS.items():
        assert hashlib.sha256(text.encode()).hexdigest()[:16] == pins[role]
    assert v2.systems("loop.v1") is loop_agents.SYSTEMS and v2.systems("loop.v2") is v2.SYSTEMS
    v1 = sch.strategist_schema(["H1"], ENTS, list(VALUES), ["price"], ["enforcement"])
    v2.strategist_schema(["H1"], ENTS, list(VALUES), ["price"], ["enforcement"], True)  # must not mutate v1
    assert v1 == sch.strategist_schema(["H1"], ENTS, list(VALUES), ["price"], ["enforcement"])
    assert "entity_b" not in v1["properties"]["options"]["items"]["properties"]["intended_effect"]["properties"]


def test_defs_are_defined_once_and_inline_for_local_validation():
    s = v2.generator_schema(list(VALUES), ENTS, ["baseline.level"], extended=True)
    txt = json.dumps(s)
    assert txt.count("metric:segment_share:L4:byd") == 2  # metric_id and metric_id_or_empty definitions only
    local = v2.inline(s)
    assert "$ref" not in json.dumps(local) and "$defs" not in local
    bad = {"explanations": [{"statement": "x", "evidence": ["metric:invented"]}], "hypotheses": [], "information_gaps": []}
    assert any("not an allowed value" in p for p in sch.validate(bad, local))
    assert set(v2.critic_schema(["H1"], list(VALUES))["$defs"]) == {"metric_id"}  # unused definitions are dropped


def test_recorded_o3_fails_pairwise_and_agreement_checks():
    o3 = OPTS["O3"]
    assert "needs entity_b" in v2.check_pairwise(o3["intended_effect"], ENTS)[0]
    o3b = {**o3, "intended_effect": {**o3["intended_effect"], "entity_b": "mg"}}
    assert v2.check_pairwise(o3b["intended_effect"], ENTS) == []
    assert "contradicts the intended direction" in v2.check_objective_agreement(o3b)[0]  # decrease vs increase_vs_L4


def test_numbers_must_reproduce_exactly_and_be_declared_in_every_text_field():
    o4 = OPTS["O4"]
    p = v2.check_numeric_claims({**o4, "numeric_claims": []}, VALUES)
    assert any("number 0.12 in the narrative is not declared" in x for x in p)  # unit-less: loop.v1 never checked it
    good = [{"text": "1.24", "value": 1.24, "unit": "percent", "kind": "observed", "decimals": 2, "operation": "value",
             "metric_ids": ["metric:share:L4:byd"]},
            {"text": "0.20", "value": 0.20, "unit": "percent", "kind": "observed", "decimals": 2, "operation": "value",
             "metric_ids": ["metric:share:P4:byd"]}]
    close = {**good[0], "text": "0.12", "value": 0.12}
    assert v2.check_numeric_claims({**o4, "numeric_claims": good + [close]}, VALUES) == [
        "claim '0.12' does not reproduce: observed value is 1.2426 percent"]  # 1.24 and 0.20 reproduce exactly
    near = {**good[0], "text": "1.25", "value": 1.25}  # 0.01 from the truth: close is NOT enough
    only = {**o4, "title": "t", "assumptions": [], "risks": [], "numeric_claims": [near],
            "intended_effect": {**o4["intended_effect"], "basis": "share 1.25%"}}
    assert v2.check_numeric_claims(only, VALUES) == ["claim '1.25' does not reproduce: observed value is 1.2426 percent"]
    hidden = {**only, "intended_effect": {**only["intended_effect"], "basis": "x"}, "numeric_claims": [],
              "cost": {**o4["cost"], "basis": "about 7.27 million"}}  # a number hidden in a field v1 never checked
    assert any("7.27" in x for x in v2.check_numeric_claims(hidden, VALUES))


def test_kinds_separate_observed_calculated_and_assumed():
    base = {"title": "t", "assumptions": [], "risks": [], "success_criteria": [], "cost": {"basis": ""},
            "intended_effect": {"template": "segment_share", "entity": "byd", "direction": "increase",
                                "magnitude_low_pp": 0.5, "magnitude_high_pp": 1.5, "basis": ""}}

    def run(text, claim):
        return v2.check_numeric_claims({**base, "intended_effect": {**base["intended_effect"], "basis": text},
                                        "numeric_claims": [claim]}, VALUES)
    diff = {"text": "2.40", "value": 2.40, "unit": "pp", "kind": "calculated", "decimals": 2, "operation": "difference",
            "metric_ids": ["metric:segment_share:L4:byd", "metric:segment_share:P4:byd"]}
    assert run("up 2.40 pp", diff) == []
    assumed = {"text": "1.5", "value": 1.5, "unit": "pp", "kind": "assumed", "decimals": 1, "operation": "none", "metric_ids": []}
    assert run("ASSUMED 1.5 pp", assumed) == []
    assert run("ASSUMED 3.0 pp", {**assumed, "text": "3.0", "value": 3.0})            # not an assumed magnitude
    assert run("1.5", {**assumed, "metric_ids": ["metric:share:L4:byd"]})              # an assumption cites no metric
    assert run("7.27", {"text": "7.27", "value": 7.27, "unit": "percent", "kind": "observed", "decimals": 2,
                        "operation": "value", "metric_ids": ["metric:segment_share:L4:byd"]})  # the recorded O1 number


def _h(ref, imps, rivals=(), cause="", explains="metric:share:L4:a", ev=("metric:share:L4:a",)):
    return {"ref": ref, "rivals": list(rivals), "explains": explains, "supporting_evidence": list(ev),
            "mechanism": {"cause": cause, "pathway": ""},
            "observable_implications": [{"id": f"I{i + 1}", "template": t, "entity": e, "entity_b": b, "comparator": c}
                                        for i, (t, e, b, c) in enumerate(imps)]}


A_MECH, B_MECH = "dealer network expansion widens availability", "temporary price incentives pull demand forward"


def test_discrimination_needs_a_real_gated_rival():
    h1 = _h("H1", [("share", "a", "", "increase_vs_L4"), ("segment_share", "a", "", "increase_vs_L4")], ["H2"], A_MECH)
    h2 = _h("H2", [("share", "a", "", "decrease_vs_L4"), ("segment_share", "a", "", "increase_vs_L4")], [], B_MECH)
    assert v2.discrimination([h1, h2]) == {("H1", "I1"): ["H2"], ("H2", "I1"): ["H1"]}  # only the disputed quantity
    assert v2.discrimination([{**h1, "rivals": []}, h2]) == {}   # disagreeing without being named rivals: no
    assert v2.discrimination([h1]) == {}                         # a rival that is not an accepted hypothesis: no


def test_opposing_predictions_alone_do_not_count():
    base = [("share", "a", "", "increase_vs_L4"), ("segment_share", "a", "", "increase_vs_L4")]
    flip = [("share", "a", "", "decrease_vs_L4"), ("segment_share", "a", "", "decrease_vs_L4")]
    cases = {
        "not meaningfully distinct": (_h("H1", base[:1] + base[1:], ["H2"], A_MECH), _h("H2", flip[:1] + base[1:], [], A_MECH)),
        "pure negation": (_h("H1", base, ["H2"], A_MECH), _h("H2", flip, [], B_MECH)),
        "same observation": (_h("H1", base, ["H2"], A_MECH),
                             _h("H2", flip[:1] + base[1:], [], B_MECH, "metric:share:L4:b", ("metric:share:L4:b",))),
        "not interpretable": (_h("H1", base + [("share", "z", "", "increase_vs_L4")], ["H2"], A_MECH),
                              _h("H2", base + [("share", "z", "", "decrease_vs_L4")], [], B_MECH)),
    }
    for why, (a, b) in cases.items():
        ass = v2.assess_pair(a, b)
        assert not ass["counted"] and any(why in r for r in ass["reasons"]), (why, ass)
        assert v2.discrimination([a, b]) == {}
    dup = _h("H3", base, [], A_MECH)
    assert v2.duplicate_mechanism(dup, [_h("H1", flip, [], A_MECH)]) == "H1"
    assert v2.duplicate_mechanism(dup, [_h("H1", flip, [], B_MECH)]) is None


def test_information_gaps_and_critiques_are_validated():
    g = {"id": "G1", "missing_measurement": "model-level data", "why_it_matters": "separates H1 from H2",
         "distinguishes": ["H1", "H9"], "potential_sources": ["x"], "expected_research_value": "high"}
    assert "H9" in v2.gap_problems(g, {"H1", "H2"}, set())[0]
    assert v2.gap_problems({**g, "distinguishes": ["H1", "H2"]}, {"H1", "H2"}, set()) == []
    c = {"dimension": "factual_contradiction", "evidence": []}
    assert "must cite the evidence" in v2.critique_problems(c)[0]
    assert v2.critique_problems({**c, "evidence": ["metric:segment_mix:L4:tesla"]}) == []
    assert v2.critique_problems({"dimension": "alternative_explanation", "evidence": []}) == []


def test_pairwise_comparators_and_extended_measurements_compile():
    p = synthetic_panel()
    m = p.metrics()
    spec, why = compiler.compile_implication({"template": "segment_share_gap", "entity": "a", "entity_b": "b",
                                              "comparator": "above_zero", "reference_metric": ""}, p, m)
    assert spec and spec["reference_value"] == 0.0 and compiler.holds(0.01, "above_zero", 0) and not compiler.holds(-0.01, "above_zero", 0)
    spec, why = compiler.compile_implication({"template": "share", "entity": "a", "entity_b": "", "comparator": "above_zero",
                                              "reference_metric": ""}, p, m)
    assert spec is None and "not pairwise" in why
    ext = p.metrics(extended=True)
    assert set(m) < set(ext) and all(ext[k]["value"] == m[k]["value"] for k in m)  # v1 metric set unchanged
    for t in ("non_segment_share", "units_growth"):
        spec, why = compiler.compile_implication({"template": t, "entity": "a", "entity_b": "", "comparator": "increase_vs_L4",
                                                  "reference_metric": ""}, p, ext)
        assert spec is not None, why
    spec, _ = compiler.compile_implication({"template": "units_growth", "entity": "market", "entity_b": "",
                                            "comparator": "increase_vs_L4", "reference_metric": ""}, p, ext)
    assert spec is not None

"""loop.v2-draft (prepared, NOT active): pairwise targets, objective agreement and exact numeric claims, checked on
the recorded r2r-57fa83dc8f2f strategist reply. The active loop.v1 requests must be unchanged (Step 1 cache reuse)."""

import hashlib
import json
from pathlib import Path

from halibut.agents import loop_agents
from halibut.epistemic import schemas as sch
from halibut.investigation import loop as loop_mod
from halibut.investigation import loop_v2_draft as v2

FIX = json.loads((Path(__file__).parent / "fixtures" / "haiku_r2r_57fa83dc8f2f_critic_strategist_replies.json").read_text())
OPTS = {o["ref"]: o for o in next(r for r in FIX["replies"] if r["role"] == "strategist")["data"]["options"]}
ENTS = ["byd", "volkswagen", "tesla", "kia", "mg", "toyota", "ford", "bmw"]
VALUES = {"metric:segment_share:L4:byd": 0.036014, "metric:segment_share:P4:byd": 0.012041,
          "metric:share:L4:byd": 0.012426, "metric:share:P4:byd": 0.001969, "metric:segment_mix:L4:byd": 0.626335,
          "metric:segment_mix:P4:byd": 1.0}


def test_draft_is_not_active_and_v1_requests_are_unchanged():
    assert v2.ACTIVE is False and loop_mod.PROMPT_VERSION == "loop.v1"
    assert "loop_v2_draft" not in Path(loop_mod.__file__).read_text()
    assert hashlib.sha256(loop_agents.SYSTEMS["strategist"].encode()).hexdigest()[:16] == "03f28296bad4c285"
    v1 = sch.strategist_schema(["H1"], ENTS, list(VALUES), ["price"], ["enforcement"])
    v2.strategist_schema_v2(["H1"], ENTS, list(VALUES), ["price"], ["enforcement"])  # must not mutate v1
    assert v1 == sch.strategist_schema(["H1"], ENTS, list(VALUES), ["price"], ["enforcement"])
    assert "entity_b" not in v1["properties"]["options"]["items"]["properties"]["intended_effect"]["properties"]


def test_recorded_o3_fails_pairwise_and_agreement_checks():
    o3 = OPTS["O3"]
    assert "needs entity_b" in v2.check_pairwise(o3["intended_effect"], ENTS)[0]
    o3b = {**o3, "intended_effect": {**o3["intended_effect"], "entity_b": "mg"}}
    assert v2.check_pairwise(o3b["intended_effect"], ENTS) == []
    assert "contradicts the intended direction" in v2.check_objective_agreement(o3b)[0]  # decrease vs increase_vs_L4


def test_numbers_must_reproduce_exactly_and_be_declared():
    o4 = OPTS["O4"]
    p = v2.check_numeric_claims({**o4, "numeric_claims": []}, VALUES)
    assert any("number 0.12 in the narrative is not declared" in x for x in p)  # the unit-less number that loop.v1 never checks
    good = [{"text": "1.24", "value": 1.24, "unit": "percent", "kind": "observed", "decimals": 2, "operation": "value",
             "metric_ids": ["metric:share:L4:byd"]},
            {"text": "0.20", "value": 0.20, "unit": "percent", "kind": "observed", "decimals": 2, "operation": "value",
             "metric_ids": ["metric:share:P4:byd"]}]
    close = {"text": "0.12", "value": 0.12, "unit": "percent", "kind": "observed", "decimals": 2, "operation": "value",
             "metric_ids": ["metric:share:L4:byd"]}
    p = v2.check_numeric_claims({**o4, "numeric_claims": good + [close]}, VALUES)
    assert p == ["claim '0.12' does not reproduce: observed value is 1.2426 percent"]  # 1.24 and 0.20 reproduce exactly
    near = {**good[0], "text": "1.25", "value": 1.25}  # 0.01 from the truth: close is NOT enough
    only = {**o4, "title": "t", "assumptions": [], "risks": [], "numeric_claims": [near],
            "intended_effect": {**o4["intended_effect"], "basis": "share 1.25%"}}
    assert v2.check_numeric_claims(only, VALUES) == ["claim '1.25' does not reproduce: observed value is 1.2426 percent"]


def test_kinds_separate_observed_calculated_and_assumed():
    base = {"title": "t", "assumptions": [], "risks": [], "success_criteria": [],
            "intended_effect": {"template": "segment_share", "entity": "byd", "direction": "increase",
                                "magnitude_low_pp": 0.5, "magnitude_high_pp": 1.5, "basis": ""}}
    def run(text, claim):
        return v2.check_numeric_claims({**base, "intended_effect": {**base["intended_effect"], "basis": text},
                                        "numeric_claims": [claim]}, VALUES)
    diff = {"text": "2.40", "value": 2.40, "unit": "pp", "kind": "calculated", "decimals": 2, "operation": "difference",
            "metric_ids": ["metric:segment_share:L4:byd", "metric:segment_share:P4:byd"]}
    assert run("up 2.40 pp", diff) == []
    assert run("ASSUMED 1.5 pp", {"text": "1.5", "value": 1.5, "unit": "pp", "kind": "assumed", "decimals": 1,
                                  "operation": "none", "metric_ids": []}) == []
    assert run("ASSUMED 3.0 pp", {"text": "3.0", "value": 3.0, "unit": "pp", "kind": "assumed", "decimals": 1,
                                  "operation": "none", "metric_ids": []})  # not one of the assumed magnitudes
    assert run("observed 1.5", {"text": "1.5", "value": 1.5, "unit": "pp", "kind": "assumed", "decimals": 1,
                                "operation": "none", "metric_ids": ["metric:share:L4:byd"]})  # an assumption cites no metric
    assert run("7.27", {"text": "7.27", "value": 7.27, "unit": "percent", "kind": "observed", "decimals": 2,
                        "operation": "value", "metric_ids": ["metric:segment_share:L4:byd"]})  # the recorded O1 number


def test_v2_schema_requires_the_new_fields():
    s = v2.strategist_schema_v2(["H1"], ENTS, list(VALUES), ["price"], ["enforcement"])
    item = s["properties"]["options"]["items"]
    assert "numeric_claims" in item["required"] and "entity_b" in item["properties"]["intended_effect"]["required"]
    assert sch.validate({"options": [OPTS["O1"]]}, s)  # a v1 reply is not a valid v2 reply

"""strategy.v3 (Architecture V2 milestone 3): the model proposes; the engine computes and verifies every quantity.

Fixtures:
- tests/fixtures/strategist_v2_rejected_r2.json: the 22 real Haiku v2 options rejected in Step 2a and 2b, verbatim,
  with their evidence;
- the r2r-57fa83dc8f2f strategist reply: the fabricated "7.27%", the contradictory O3 and the unsourced "0.12".

Offline only: nothing here claims a live improvement.
"""

import copy
import json
from pathlib import Path

import pytest

from halibut.investigation import loop_v2 as v2
from halibut.investigation import strategy_v3 as S

from .test_r2_loop import UK, fresh, run, uk_store  # noqa: F401

FIX = Path(__file__).parent / "fixtures"
REJ = json.loads((FIX / "strategist_v2_rejected_r2.json").read_text())
R2R = {o["ref"]: o for o in next(r for r in json.loads((FIX / "haiku_r2r_57fa83dc8f2f_critic_strategist_replies.json").read_text())
                                 ["replies"] if r["role"] == "strategist")["data"]["options"]}


def _cat(inv):
    return S.catalogue({k: {"id": k, "value": v} for k, v in inv["metrics"].items()}, {}, inv["entities"], inv["focal"],
                       inv["target"])


def test_replay_rejected_v2_options_numbers_are_engine_computable():
    """What the v3 contract changes for the 22 real rejected options:
    - their v2 failure (a numeric_claims text over the invisible 24-character limit) cannot occur, because the field
      no longer exists and the limits are stated;
    - every observed or calculated number they declared is available as a catalogue quantity;
    - assumed numbers go to the magnitude fields."""
    routes, n_opts, full = {}, 0, 0
    for inv in REJ["investigations"]:
        cat = _cat(inv)
        for r in inv["rejected"]:
            n_opts += 1
            assert any("numeric_claims" in p and "longer than 24" in p for p in r["v2_problems"])  # the v2 failure
            t = S.translate_v2_option(r["option"], cat)
            full += t["fully_referencable"]
            for x in t["numbers"]:
                routes[x["route"]] = routes.get(x["route"], 0) + 1
    assert n_opts == 22
    assert routes.get("unreferencable", 0) <= 0.15 * sum(routes.values())  # nearly every number is engine-computable
    assert full >= 0.6 * n_opts
    print(f"\nreplay: {n_opts} options, number routes {routes}, fully referencable {full}/{n_opts}")


def test_v2_format_output_is_not_accepted_as_v3():
    """A v2-style option (numbers written in text) is rejected under v3: v3 does not loosen integrity."""
    inv = REJ["investigations"][0]
    o = copy.deepcopy(inv["rejected"][0]["option"])
    o.update(objective={k: o["intended_effect"][k] for k in ("template", "entity", "entity_b", "direction")},
             actions=[{"lever": o["levers"][0], "description": "x"}], rationale="x", quantity_refs=[], subclaims=[],
             calculation_requests=[], limitations=[])
    p = S.validate(o, _cat(inv), inv["entities"])
    assert any("digit in free text" in x for x in p)


def _v3(o, **over):
    """A recorded r2r option, minimally restructured to v3 (no numbers changed, so failures stay failures)."""
    x = copy.deepcopy(o)
    x["intended_effect"] = {**x["intended_effect"], "entity_b": x["intended_effect"].get("entity_b", ""),
                            "assumption": {"justification": "analogy with past offers", "basis": "analogy",
                                           "uncertainty": "high"}}
    x.update(objective={k: x["intended_effect"][k] for k in ("template", "entity", "entity_b", "direction")},
             actions=[{"lever": x["levers"][0], "description": "launch the offer"}], rationale="see quantities",
             quantity_refs=["q:obs:segment_share:L4:byd"],
             assumptions=[{**a, "justification": "j", "uncertainty": "medium"} for a in x["assumptions"]],
             competitor_responses=[{**r, "justification": "j"} for r in x["competitor_responses"]],
             subclaims=[], calculation_requests=[], limitations=[])
    x.update(over)
    return x


CAT = S.catalogue({"metric:segment_share:L4:byd": {"id": "metric:segment_share:L4:byd", "value": 0.036014},
                   "metric:segment_share:P4:byd": {"id": "metric:segment_share:P4:byd", "value": 0.012041},
                   "metric:share:L4:byd": {"id": "metric:share:L4:byd", "value": 0.012426}}, {}, ["byd", "volkswagen", "mg"],
                  "byd", "volkswagen")
ENTS = ["byd", "volkswagen", "tesla", "kia", "mg", "toyota", "ford", "bmw"]


def test_fabricated_numbers_and_invalid_references_are_rejected():
    o1 = _v3(R2R["O1"])  # its basis still writes 'baseline point 7.27%' (fabricated) and other numbers
    assert any("digit in free text" in p for p in S.validate(o1, CAT, ENTS))
    clean = _v3(R2R["O1"], title="Defend the segment",
                intended_effect={**_v3(R2R["O1"])["intended_effect"], "basis": "share is {q:obs:segment_share:L4:invented}"},
                risks=["margin erosion"])
    clean["assumptions"] = [{"statement": "demand responds", "kind": "behavioural", "justification": "j", "uncertainty": "high"}]
    p = S.validate(clean, CAT, ENTS)
    assert any("not in the quantity catalogue" in x for x in p) and not any("digit" in x for x in p)
    ok = {**clean, "intended_effect": {**clean["intended_effect"], "basis": "share is {q:obs:segment_share:L4:byd}"}}
    assert S.validate(ok, CAT, ENTS) == []
    assert S.render(ok, CAT)["intended_effect.basis"] == "share is 3.60% [observed]"


def test_unsupported_assumptions_and_contradictory_objectives_are_rejected():
    base = _v3(R2R["O1"], title="t", risks=["r"])
    base["intended_effect"] = {**base["intended_effect"], "basis": "b"}
    base["assumptions"] = [{"statement": "s", "kind": "behavioural", "justification": "", "uncertainty": "high"}]
    assert any("assumptions[0] needs a justification" in p for p in S.validate(base, CAT, ENTS))
    nojust = copy.deepcopy(base)
    nojust["intended_effect"]["assumption"]["justification"] = " "
    assert any("intended_effect.assumption needs a justification" in p for p in S.validate(nojust, CAT, ENTS))
    o3 = _v3(R2R["O3"], title="t", risks=["r"])  # decrease the VW-MG gap vs a success criterion that it increases
    o3["intended_effect"] = {**o3["intended_effect"], "entity_b": "mg", "basis": "b"}
    o3["objective"] = {**o3["objective"], "entity_b": "mg"}
    o3["assumptions"] = []
    assert any("contradicts the intended direction" in p for p in S.validate(o3, CAT, ENTS))
    mism = {**base, "objective": {**base["objective"], "direction": "decrease"}}
    assert any("objective and intended effect differ" in p for p in S.validate(mism, CAT, ENTS))
    lever = {**base, "actions": [{"lever": "covert_discounts", "description": "x"}]}
    assert any("not the option's levers" in p for p in S.validate(lever, CAT, ENTS))


def test_calculation_requests_are_computed_or_typed_unavailable():
    o = {"calculation_requests": [
        {"operation": "difference", "inputs": ["q:obs:segment_share:L4:byd", "q:obs:segment_share:P4:byd"], "purpose": "p"},
        {"operation": "ratio", "inputs": ["q:obs:segment_share:L4:byd", "q:obs:missing"], "purpose": "p"}]}
    r = S.run_calculations(o, CAT)
    assert r[0]["status"] == "computed" and r[0]["value"] == pytest.approx(2.3973) and r[0]["kind"] == "calculated"
    assert r[1]["status"] == "calculation_unavailable" and "value" not in r[1]  # never invented


def test_every_enforced_limit_is_stated_to_the_model():
    s = S.schema(["H1"], ENTS, ["metric:segment_share:L4:byd"], ["price"], ["enforcement"], list(CAT), True)
    lim = S.describe_limits(s)
    assert "options[].title: at most 160 characters" in lim and "options[].subclaims: at least 1 and at most 3 items" in lim
    assert lim in S.system_prompt(s) and "Never write a digit" in S.system_prompt(s)
    assert "numeric_claims" not in json.dumps(s)


def test_v3_pipeline_offline_registers_subclaims_and_keeps_safeguards(uk_store, tmp_path):
    from halibut.epistemic import predictions as P
    from halibut.investigation import artifact as art
    store = fresh(tmp_path, uk_store)
    with pytest.raises(ValueError, match="only with contract loop.v2"):
        run(store, uk_store / "m.toml", strategist="v3", **UK)
    doc = run(store, uk_store / "m.toml", contract="loop.v2", measurements="extended", strategist="v3", **UK)
    assert doc["status"] == "completed" and art.validate(doc) == []
    assert doc["provenance"]["prompt_version"] == "loop.v2:extended+strategy.v3"
    opts = doc["stages"]["strategic_options"]
    assert opts
    for o in opts:
        d = o["data"]
        assert d["contract"] == "strategy.v3" and "a well-formed proposal, not evidence" in d["validity_note"]
        assert all(q["kind"] in ("observed", "calculated", "model") for q in d["quantities"].values())
        kinds = {s["kind"] for s in d["scenarios"]}
        assert kinds <= {"model", "simulated"} and "simulated" in kinds
        assert d["subclaims"] and all(sc["test_id"] and sc["spec"] for sc in d["subclaims"])
        assert d["executable"] is False
    proto = doc["experiment_protocol"]
    assert proto is None or proto["data"]["eligibility"]["status"] in ("exploratory_not_eligible", "eligible_pending_human_review")
    P.sync(store, uk_store, include_scripted=True)
    subs = [x for x in P.current(store) if x["source"] == "strategy_subclaim"]
    assert len(subs) == sum(len(o["data"]["subclaims"]) for o in opts)  # strategy subclaims are resolvable predictions
    store.close()


def test_v2_and_v1_strategists_are_unchanged(uk_store, tmp_path):
    a = run(fresh(tmp_path, uk_store), uk_store / "m.toml", contract="loop.v2", **UK)
    assert a["provenance"]["prompt_version"] == "loop.v2:base"
    assert all("numeric_claims" in o["data"] and "quantities" not in o["data"] for o in a["stages"]["strategic_options"])
    assert "strategy.v3" not in json.dumps(v2.SYSTEMS)

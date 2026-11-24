"""Resilience of the boundary between AI output and deterministic execution (R2 Step 1 failure, r2r-57fa83dc8f2f).

The Step 1 revalidation crashed with an IndexError in ``models/registry.execute``. The live strategist proposed O3 with
an intended effect on ``segment_share_gap`` for one entity. That was schema-valid, because the loop.v1 strategist
schema has no second entity for an intended effect. The gap series for (volkswagen, None) is empty, and
``official_baseline`` fell back to an unguarded ``execute``.

These tests pin the fix:
- typed failure states;
- per-item isolation;
- no invented values;
- internal errors stay visible.

They also run randomly sampled, schema-valid agent output through the full pipeline (seeded, so it is reproducible).

Fixture: tests/fixtures/haiku_r2r_57fa83dc8f2f_critic_strategist_replies.json, the two live replies recorded verbatim.
"""

import json
import math
import random
import re
from pathlib import Path

import pytest

from halibut.agents.mock_loop import responder as mock_responder
from halibut.agents.routing import Router
from halibut.epistemic import schemas as sch
from halibut.epistemic.failures import classify_problems
from halibut.investigation import artifact as art
from halibut.investigation import loop as loop_mod
from halibut.investigation import strategy
from halibut.investigation.loop import InvestigationInternalError, run_investigation
from halibut.models import registry as reg

from .test_r2_loop import UK, fresh, uk_store  # noqa: F401  (uk_store is a fixture)
from .test_r2_registry_approvals import panel as synthetic_panel

FIX = json.loads((Path(__file__).parent / "fixtures" / "haiku_r2r_57fa83dc8f2f_critic_strategist_replies.json").read_text())
CRITIC = next(r for r in FIX["replies"] if r["role"] == "loop_critic")
STRAT = next(r for r in FIX["replies"] if r["role"] == "strategist")


def run(store, market, responders=None, **kw):
    return run_investigation(store, market, router=Router("mock", store, "x", responders=responders) if responders else None,
                             log=lambda *_: None, **{**UK, **kw})


# ---------------------------------------------------------------------------------------------------------- the record
def test_fixture_is_the_recorded_live_reply():
    assert FIX["provenance"]["experiment"] == "r2r-57fa83dc8f2f"
    assert [r["request_id"] for r in FIX["replies"]] == ["req_011ChM1EKtZ43v9Xjk9CUzww", "req_011ChM1FALcUmUUeDgpiG1Fn"]
    assert all(r["stop_reason"] == "end_turn" and json.loads(r["text"]) == r["data"] for r in FIX["replies"])
    assert len(CRITIC["data"]["critiques"]) == 12 and len(STRAT["data"]["options"]) == 4
    o3 = STRAT["data"]["options"][2]
    assert o3["ref"] == "O3" and o3["intended_effect"]["template"] == "segment_share_gap"
    assert o3["intended_effect"]["entity"] == "volkswagen" and "entity_b" not in o3["intended_effect"]


def test_the_strategist_reply_was_schema_valid():
    """The strategist did not break its contract: the gap effect with one entity is allowed by the loop.v1 schema."""
    opts = STRAT["data"]["options"]
    ids = sorted({i for o in opts for i in o["supporting_evidence"]} |
                 {c["reference_metric"] for o in opts for c in o["success_criteria"] if c["reference_metric"]})
    ents = ["byd", "volkswagen", "tesla", "kia", "mg", "toyota", "ford", "bmw"]
    refs = sorted({r for o in opts for r in o["linked_hypotheses"]})
    s = sch.strategist_schema(refs, ents, ids, sorted({lv for o in opts for lv in o["levers"]}), sorted({o["mode"] for o in opts}))
    assert sch.validate(STRAT["data"], s) == []
    assert "entity_b" not in s["properties"]["options"]["items"]["properties"]["intended_effect"]["properties"]


# ---------------------------------------------------------------------------------------- the registry never invents
def test_execute_on_an_empty_series_raises_a_typed_failure_not_an_indexerror():
    p = synthetic_panel()
    with pytest.raises(reg.ModelUnavailable) as e:
        reg.execute({"family": "baseline.level", "target": {"template": "segment_share_gap", "entity": "a", "entity_b": None}}, p)
    assert e.value.failure_class == "model.target_unavailable"
    with pytest.raises(reg.ModelUnavailable) as e:
        reg.execute({"family": "simulation.share_shift", "target": {"template": "share", "entity": "a"}}, p)
    assert e.value.failure_class == "model.unknown_family"


def test_execute_rejects_short_and_non_finite_series(monkeypatch):
    p = synthetic_panel()
    t = {"family": "baseline.level", "target": {"template": "share", "entity": "a", "entity_b": None}}
    full = p.series("share", "a")
    monkeypatch.setattr(p, "series", lambda *a, **k: full[:5])
    with pytest.raises(reg.ModelUnavailable, match="insufficient_history"):
        reg.execute(t, p)
    monkeypatch.setattr(p, "series", lambda *a, **k: full[:-1] + [(full[-1][0], float("nan"))])
    with pytest.raises(reg.ModelUnavailable) as e:
        reg.execute(t, p)
    assert e.value.failure_class == "numeric.nonfinite"


def test_simulation_needs_a_finite_baseline_and_finite_inputs():
    base = {"point": 0.1, "interval80": [0.08, 0.12]}
    assert math.isfinite(reg.share_shift(base, (0.5, 1.0), (0.0, 0.25))["outcome_point"])
    for bad in ({"point": None, "interval80": None}, {"point": float("nan"), "interval80": [0.0, 0.1]}):
        with pytest.raises(reg.ModelUnavailable):
            reg.share_shift(bad, (0.5, 1.0), (0.0, 0.0))
    with pytest.raises(reg.ModelUnavailable, match="nonfinite"):
        reg.share_shift(base, (0.5, float("inf")), (0.0, 0.0))
    with pytest.raises(ValueError):
        strategy.min_effect_pp([])


def test_the_recorded_gap_effect_has_no_measurable_target():
    p = synthetic_panel()
    e = {**STRAT["data"]["options"][2]["intended_effect"], "entity": "a"}
    assert "needs a second entity" in strategy.effect_target_problems(e, p)[0]
    assert strategy.effect_target_problems({**e, "template": "segment_share"}, p) == []
    assert "no observations" in strategy.effect_target_problems({**e, "template": "share", "entity": "market"}, p)[0]


def test_non_finite_agent_numbers_are_schema_violations():
    s = {"type": "object", "properties": {"x": {"type": "number"}}, "required": ["x"], "additionalProperties": False}
    for v in (float("nan"), float("inf"), -float("inf")):
        pr = sch.validate({"x": v}, s)
        assert pr and "finite" in pr[0] and classify_problems(pr) == "numeric.nonfinite"
    assert json.loads('{"x": NaN}')["x"] != json.loads('{"x": NaN}')["x"]  # why: json.loads accepts NaN


# ------------------------------------------------------------------------------- full pipeline, recorded live replies
def _transplant(role, p):
    """The recorded live critic/strategist replies, with their references mapped onto this fixture's bundle (entities
    mg/kia/... -> tesla, metric ids -> ids present in the bundle, hypothesis refs -> accepted refs). Structure, numbers,
    templates and text are unchanged."""
    out = mock_responder(role, p)
    if role not in ("loop_critic", "strategist"):
        return out
    refs = [h["ref"] for h in p["hypotheses"]]
    ref = lambda r: r if r in refs else refs[(int(r[1:]) - 1) % len(refs)]  # noqa: E731
    if role == "loop_critic":
        return {"critiques": [{**c, "hypothesis_ref": ref(c["hypothesis_ref"])} for c in CRITIC["data"]["critiques"]]}
    ents = set(p["slice"]["market"]["competitors"])
    ids = [m["id"] for m in p["slice"]["metrics"]]
    ent = lambda e: e if e in ents or e in ("market", "") else "tesla"  # noqa: E731
    mid = lambda i: i if i in ids else next((x for x in ids if x.split(":")[1] == i.split(":")[1]), ids[0])  # noqa: E731
    opts = json.loads(json.dumps(STRAT["data"]["options"]))
    num = re.compile(r"[-+]?\d+(?:\.\d+)?\s*(?:%|pp|percentage points?)?")
    for o in opts:  # narrative numbers refer to the real DfT values, not this fixture's: removed (the validator would
        # otherwise reject every option, correctly, as citing numbers absent from the evidence)
        o["title"] = num.sub("N", o["title"])
        o["intended_effect"]["basis"] = num.sub("N", o["intended_effect"]["basis"])
        o["assumptions"] = [{**a, "statement": num.sub("N", a["statement"])} for a in o["assumptions"]]
        o["risks"] = [num.sub("N", r) for r in o["risks"]]
        o["mode"] = p["slice"]["market"]["mode"] if o["mode"] not in ("annihilation", "continuation") else o["mode"]
        o["linked_hypotheses"] = sorted({ref(r) for r in o["linked_hypotheses"]})
        o["supporting_evidence"] = sorted({mid(i) for i in o["supporting_evidence"]})
        o["intended_effect"]["entity"] = ent(o["intended_effect"]["entity"])
        o["competitor_responses"] = [{**r, "competitor": ent(r["competitor"])} for r in o["competitor_responses"]]
        o["success_criteria"] = [{**c, "entity": ent(c["entity"]), "entity_b": ent(c["entity_b"]),
                                  "reference_metric": mid(c["reference_metric"]) if c["reference_metric"] else ""}
                                 for c in o["success_criteria"]]
    return {"options": opts}


def test_recorded_live_replies_complete_with_the_gap_option_isolated(uk_store, tmp_path):
    store = fresh(tmp_path, uk_store)
    doc = run(store, uk_store / "m.toml", _transplant)
    assert doc["status"] == "completed" and art.validate(doc) == []
    ns = [f for f in doc["failures"]["items"] if f["stage"] == "simulation"]
    assert [f["failure_class"] for f in ns if f["ref"] == "O3"] == ["option.effect_target_unsupported"]
    o3 = [x for x in doc["stages"]["strategic_options"] if x["data"]["ref"] == "O3"]
    assert len(o3) == 1  # kept as a proposal (it passed validation), never simulated, never given an invented number
    assert o3[0]["data"]["simulation"]["status"] == "not_simulated"
    assert not [ln for ln in o3[0]["links"] if ln["relation"] == "simulated_by"]
    assert not [s for s in doc["stages"]["simulations"] if any(ln["to"] == o3[0]["id"] for ln in s["links"])]
    others = [x for x in doc["stages"]["strategic_options"] if x["data"]["ref"] != "O3"]
    assert others and all(x["data"]["simulation"]["status"] == "simulated" for x in others)  # valid work is not lost
    assert doc["experiment_protocol"] is not None and doc["experiment_protocol"]["data"]["option_ref"] != "O3"
    assert doc["failures"]["completion"]["unavailable_items"] >= 1 and not doc["failures"]["completion"]["internal_error"]
    assert len(doc["stages"]["critiques"]) >= 1
    store.close()


def test_an_unexpected_engine_error_is_stored_visibly_and_reraised(uk_store, tmp_path, monkeypatch):
    store = fresh(tmp_path, uk_store)

    def boom(*a, **k):
        raise ZeroDivisionError("planted defect")
    monkeypatch.setattr(loop_mod.strategy, "simulate", boom)
    with pytest.raises(InvestigationInternalError) as e:
        run(store, uk_store / "m.toml")
    doc = e.value.doc
    assert doc["status"] == "stopped" and doc["stop_reason"].startswith("internal_error: ZeroDivisionError")
    assert doc["failures"]["completion"]["state"] == "not_completed" and doc["failures"]["completion"]["internal_error"]
    f = [x for x in doc["failures"]["items"] if x["failure_class"] == "internal.error"]
    assert f and any("in boom" in p for p in f[0]["problems"])
    assert doc["evidence"]["metrics"] and doc["stages"]["hypotheses"]  # partial work is kept, labelled
    row = store.conn.execute("SELECT status FROM investigations WHERE investigation_id=?", (doc["investigation_id"],)).fetchone()
    assert row["status"] == "stopped" and art.validate(doc) == []
    assert store.conn.execute("SELECT COUNT(*) FROM events_log WHERE event_type='investigation.internal_error'").fetchone()[0] == 1
    store.close()


@pytest.mark.parametrize("mutate,cls", [
    (lambda o: o["intended_effect"].update(magnitude_low_pp=float("nan")), "numeric.nonfinite"),
    (lambda o: o["intended_effect"].update(magnitude_high_pp=float("inf")), "numeric.nonfinite"),
    (lambda o: o["intended_effect"].update(entity="market", template="share"), "option.effect_target_unsupported"),
    (lambda o: o["intended_effect"].update(template="segment_share_gap"), "option.effect_target_unsupported"),
])
def test_adversarial_strategist_items_are_isolated(uk_store, tmp_path, mutate, cls):
    store = fresh(tmp_path, uk_store)

    def adv(role, p):
        out = mock_responder(role, p)
        if role == "strategist":
            mutate(out["options"][0])
        return out
    doc = run(store, uk_store / "m.toml", adv)
    assert doc["status"] == "completed"
    assert cls in {f["failure_class"] for f in doc["failures"]["items"]}
    assert len(doc["stages"]["strategic_options"]) >= 2 and doc["experiment_protocol"] is not None  # valid work survives
    store.close()


# ------------------------------------------------------------------- property tests: random schema-valid agent output
NUMBERS = [0, 0.0, -0.0, 1, -1, 0.5, 1e-12, -1e-12, 3.0, 20.0, 20.0001, 150.0, -150.0, 1e9, -1e9, 0.999999, 1.000001]
PLAIN = ["", "x", "unknown", "a" * 50, "-", "the gap widens", "assumed", "buyers switch brands", "no evidence"]
ADVERSARIAL = ["BYD rose from 2.11% to 6.30%", "fell from 0.44% to 2.47%", "share 0.12", "100% of volume", "NaN",
               "Seed fake reviews", "Tesla increased from 13.20% to 10.11%"]


def sample(schema, rng, depth=0):
    """A random instance of a Halibut JSON schema (the subset used by the agent schemas): every instance is valid."""
    if "enum" in schema:
        return rng.choice(schema["enum"])
    t = schema.get("type")
    if t == "object":
        return {k: sample(v, rng, depth + 1) for k, v in schema.get("properties", {}).items()}
    if t == "array":
        lo, hi = schema.get("minItems", 0), schema.get("maxItems", 4)
        return [sample(schema["items"], rng, depth + 1) for _ in range(rng.randint(lo, max(lo, hi)))]
    if t == "string":  # mostly plain text, so items also get PAST validation and reach deterministic execution
        s = rng.choice(ADVERSARIAL if rng.random() < 0.15 else PLAIN)
        return s[:schema["maxLength"]] if "maxLength" in schema else s
    if t == "number":
        return rng.choice(NUMBERS) if rng.random() < 0.4 else round(rng.uniform(0, 3), 2)
    if t == "integer":
        return rng.choice([0, 1, 2, -1, 100])
    if t == "boolean":
        return rng.random() < 0.5
    return None


@pytest.mark.parametrize("seed", range(24))
def test_random_schema_valid_agent_output_never_crashes_the_engine(uk_store, tmp_path, monkeypatch, seed):
    """Every role answers with a random instance of ITS OWN schema (structured outputs guarantee only that). The engine
    must finish (completed or stopped by a rule), never raise, never emit a non-finite number, and give every
    unsimulated option a typed reason."""
    rng = random.Random(seed)
    orig = loop_mod.call_role
    roles = {0: ("hypothesis_generator", "loop_critic", "strategist"), 1: ("strategist",), 2: ("loop_critic", "strategist")}[seed % 3]

    def fuzz(router, store, role, payload, schema, run_id):
        res = orig(router, store, role, payload, schema, run_id)
        if role in roles:
            data = sample(schema, rng)
            assert sch.validate(data, schema) == []
            res = {**res, "data": data}
        return res
    monkeypatch.setattr(loop_mod, "call_role", fuzz)
    store = fresh(tmp_path, uk_store)
    doc = run(store, uk_store / "m.toml")
    assert doc["status"] in ("completed", "stopped") and art.validate(doc) == []
    assert not doc["failures"]["completion"]["internal_error"]
    for st, key in (("forecasts", "point"), ("estimates", "value")):
        assert all(math.isfinite(x["data"][key]) for x in doc["stages"][st])
    for s in doc["stages"]["simulations"]:
        assert all(math.isfinite(v) for v in s["data"]["outcome_range"] + [s["data"]["outcome_point"]])
    for o in doc["stages"]["strategic_options"]:
        sim = o["data"]["simulation"]
        assert sim["status"] == "simulated" or (sim["failure_class"] and sim["reason"])
    store.close()


def _passes_validation(o, p, rng):
    """Repair what option VALIDATION checks (cost, magnitude order and size, measurable criteria, plain narrative) so the
    option reaches deterministic execution; the intended effect (template, entity, direction, magnitudes) stays random."""
    e = o["intended_effect"]
    lo, hi = sorted(abs(float(x)) % 20 for x in (e["magnitude_low_pp"], e["magnitude_high_pp"]))
    e.update(magnitude_low_pp=round(lo, 2), magnitude_high_pp=round(hi, 2), basis="assumed")
    o.update(title="option", risks=["risk"], assumptions=[{"statement": "assumed", "kind": "behavioural"}],
             cost={"known": False, "low": 0, "high": 0, "currency": "unknown", "basis": "no cost evidence"})
    tmpl = "segment_share" if p["slice"]["market"].get("segment") else "share"
    o["success_criteria"] = [{"template": tmpl, "entity": rng.choice(sorted(p["slice"]["market"]["competitors"])), "entity_b": "",
                              "comparator": "increase_vs_L4", "reference_metric": "", "horizon_quarters": 2}]
    return o


@pytest.mark.parametrize("seed", range(16))
def test_validated_options_with_any_intended_effect_never_crash(uk_store, tmp_path, monkeypatch, seed):
    """Property: an option that passes validation, whatever intended effect it names (any template, any entity including
    'market', any direction or magnitude), is simulated against a real baseline or kept with a typed reason. It never
    crashes the investigation and never gets an invented number. (On the pre-fix engine this fails with the production
    IndexError.)"""
    rng = random.Random(1000 + seed)
    orig = loop_mod.call_role

    def fuzz(router, store, role, payload, schema, run_id):
        res = orig(router, store, role, payload, schema, run_id)
        if role == "strategist":
            data = sample(schema, rng)
            data["options"] = [_passes_validation(o, payload, rng) for o in data["options"]] or \
                [_passes_validation(sample(schema["properties"]["options"]["items"], rng), payload, rng)]
            for i, o in enumerate(data["options"]):
                o["ref"] = f"O{i + 1}"
            assert sch.validate(data, schema) == []
            res = {**res, "data": data}
        return res
    monkeypatch.setattr(loop_mod, "call_role", fuzz)
    store = fresh(tmp_path, uk_store)
    doc = run(store, uk_store / "m.toml")
    assert doc["status"] == "completed" and not doc["failures"]["completion"]["internal_error"]
    opts = doc["stages"]["strategic_options"]
    assert opts  # the repaired options pass validation, so the execution boundary is exercised
    for o in opts:
        sim, e = o["data"]["simulation"], o["data"]["intended_effect"]
        sims = [s for s in doc["stages"]["simulations"] if any(ln["to"] == o["id"] for ln in s["links"])]
        if sim["status"] == "simulated":
            assert sims and all(math.isfinite(v) for s in sims for v in s["data"]["outcome_range"])
        else:
            assert not sims and sim["failure_class"] in ("option.effect_target_unsupported", "model.target_unavailable",
                                                         "model.insufficient_history", "numeric.nonfinite")
        if e["template"] == "segment_share_gap" or e["entity"] == "market" and e["template"] != "market_segment_share":
            assert sim["status"] == "not_simulated"
    store.close()

import pytest

from halibut.data.store import Store
from halibut.empirical.spec import load_pilot
from halibut.learning.demo import ReferenceImprovementAgent, run_demo
from halibut.learning.evaluate import decide
from halibut.learning.lineage import diff, lineage
from halibut.learning.memory import LearningMemory, TrustViolation

from .test_empirical import SPEC, make_store


@pytest.fixture
def mem(tmp_path):
    s = Store(tmp_path / "m.sqlite")
    yield LearningMemory(s)
    s.close()


def exp(mem, title="e"):
    return mem.record_experiment("test", title, "engine:test", {"code": {"commit": "x"}})


def test_decision_rules():
    base = {f"o{i}": 5.0 for i in range(6)}
    better = {f"o{i}": 4.0 + 0.01 * i for i in range(6)}
    worse = {f"o{i}": 6.0 + 0.01 * i for i in range(6)}
    hb, hc = {"h1": 5.0, "h2": 5.0}, {"h1": 4.5, "h2": 4.6}
    assert decide(base, better, hb, hc)["decision"] == "eligible_for_promotion"
    assert decide(base, worse, hb, {"h1": 6, "h2": 6})["decision"] == "reject"
    one = decide({"o1": 5.0}, {"o1": 1.0}, hb, hc)  # a single favourable result never promotes
    assert one["result"] == "inconclusive" and "single" in one["reasons"][0]
    held_only = decide(base, {**base, "o0": 4.9}, hb, hc)  # held-out gain without development evidence
    assert held_only["decision"] == "hold"
    no_holdout_gain = decide(base, better, hb, {"h1": 5.5, "h2": 5.5})
    assert no_holdout_gain["decision"] != "eligible_for_promotion"
    loses_to_simple = decide(base, better, hb, hc, simple_holdout={"h1": 1.0, "h2": 1.0})
    assert loses_to_simple["decision"] == "hold"


def test_trust_rules(mem):
    with pytest.raises(TrustViolation):
        mem.submit("lesson", "x", "agent:a", {"tags": []}, [], status="tested")
    with pytest.raises(TrustViolation):
        mem.submit("lesson", "x", "engine:e", {"tags": []}, [], status="validated")
    e1, e2 = exp(mem, "b"), exp(mem, "c")
    k = mem.submit("lesson", "candidate helps", "agent:a", {"tags": ["t"]}, ["evidence:1"], experiment_id=e2)
    hold = mem.record_evaluation(e2, e1, {"v": 1}, "inconclusive", "hold", {}, "engine:eval")
    with pytest.raises(TrustViolation):
        mem.set_status(k, "validated", "engine:eval", evaluation_id=hold)
    ok = mem.record_evaluation(e2, e1, {"v": 1}, "improved", "eligible_for_promotion", {}, "engine:eval")
    other = exp(mem, "d")
    k2 = mem.submit("lesson", "other", "agent:a", {"tags": ["t"]}, [], experiment_id=other)
    with pytest.raises(TrustViolation, match="experiment"):
        mem.set_status(k2, "validated", "engine:eval", evaluation_id=ok)
    mem.set_status(k, "validated", "engine:eval", evaluation_id=ok)
    assert mem.retrieve(tags=["t"], statuses=["validated"])[0]["knowledge_id"] == k
    # knowledge never becomes an observation
    assert mem.store.conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0


def test_disagreement_is_preserved(mem):
    a = mem.submit("hypothesis", "X displaces Y", "agent:a", {"tags": ["m"]}, ["obs:1"])
    b = mem.submit("critique", "Evidence is a media proxy, not sales", "agent:b", {"tags": ["m"]}, ["doc:1"])
    mem.link(b, "contradicts", a, "agent:b")
    items = {k["knowledge_id"]: k for k in mem.retrieve(tags=["m"])}
    assert set(items) == {a, b} and items[a]["status"] == "proposed"
    assert any(ln["relation"] == "contradicts" for ln in items[a]["links"])


def test_promotion_requires_eligible_evaluation_and_person_and_rolls_back(mem):
    e1, e2, e3 = exp(mem, "b"), exp(mem, "c1"), exp(mem, "c2")
    hold = mem.record_evaluation(e2, e1, {}, "inconclusive", "hold", {}, "engine:eval")
    with pytest.raises(TrustViolation):
        mem.promote("comp", hold, "alice")
    ok1 = mem.record_evaluation(e2, e1, {}, "improved", "eligible_for_promotion", {}, "engine:eval")
    with pytest.raises(TrustViolation, match="person"):
        mem.promote("comp", ok1, "engine:eval")
    mem.promote("comp", ok1, "alice")
    ok2 = mem.record_evaluation(e3, e2, {}, "improved", "eligible_for_promotion", {}, "engine:eval")
    mem.promote("comp", ok2, "alice")
    assert mem.active("comp")["experiment_id"] == e3
    assert mem.rollback("comp", "alice")["experiment_id"] == e2


def test_lineage_and_diff(mem):
    lin = lineage(mem.store, config={"a": 1}, parameters={"w": 60}, seeds=[1, 2])
    assert lin["engine_version"] and lin["config_hash"] and lin["seeds"] == [1, 2] and "commit" in lin["code"]
    assert diff({"model": "logistic", "w": None, "x": {"y": 1}}, {"model": "logistic", "w": 60, "x": {"y": 2}}) == \
        {"w": (None, 60), "x.y": (1, 2)}


def test_learning_loop_demo_on_synthetic_store(tmp_path):
    store = make_store(tmp_path)
    spec = load_pilot(SPEC)
    mem = LearningMemory(store)
    r = run_demo(store, spec, mem)
    for step in ("first", "second"):
        x = r[step]
        assert x["verdict"]["decision"] in ("eligible_for_promotion", "reject", "hold")
        assert x["lesson_status"] in ("validated", "rejected", "inconclusive")
        e = mem.experiment(x["candidate_id"])
        assert e["baseline_id"] == x["baseline_id"] and e["change"] and e["rationale"]
        assert e["lineage"]["data_snapshots"] and e["expected_improvement"]["direction"] == "decrease"
    # the second proposal is informed by the first lesson
    assert r["proposal"]["considered"] and r["proposal"]["considered"][0]["id"] == r["first"]["lesson_id"]
    if r["first"]["lesson_status"] == "validated":
        assert r["proposal"]["action"] == "generalisation_test"
    else:
        assert r["proposal"]["action"] == "ablation" and r["proposal"]["window"] != 60
    links = store.conn.execute("SELECT COUNT(*) FROM knowledge_links WHERE relation='considered'").fetchone()[0]
    assert links >= 1
    assert mem.active("forecasting.share_curve") is None  # never promoted automatically
    store.close()

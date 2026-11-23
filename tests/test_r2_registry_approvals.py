"""R2 model registry, approvals and routing (offline)."""

import sqlite3

import numpy as np
import pytest

from halibut.agents.routing import Router, load_routing
from halibut.control import approvals as ap
from halibut.data.store import Store
from halibut.models import registry as reg
from halibut.world.panel import SharePanel


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "s.sqlite")
    yield s
    s.close()


def panel(n=24, as_of="2024-12-31", trend=0.004):
    qs = [f"{2018 + i // 4}-Q{i % 4 + 1}" for i in range(n)]
    M = SharePanel.MARKET
    cells = {(M, "all"): {}, (M, "seg"): {}, ("a", "all"): {}, ("a", "seg"): {}, ("b", "all"): {}, ("b", "seg"): {}}
    rng = np.random.default_rng(1)
    for i, q in enumerate(qs):
        tot, seg = 1000.0, 200.0
        a = seg * (0.2 + trend * i + rng.normal(0, 0.003))
        cells[(M, "all")][q] = (tot, [f"obs:{i}0"], "2020-01-01")
        cells[(M, "seg")][q] = (seg, [f"obs:{i}1"], "2020-01-01")
        cells[("a", "seg")][q] = (a, [f"obs:{i}2"], "2020-01-01")
        cells[("a", "all")][q] = (a + 100, [f"obs:{i}3"], "2020-01-01")
        cells[("b", "seg")][q] = (seg - a, [f"obs:{i}4"], "2020-01-01")
        cells[("b", "all")][q] = (seg - a + 300, [f"obs:{i}5"], "2020-01-01")
    return SharePanel("m", "test", as_of, "u", "seg", ["a", "b"], cells, {"provider": "test"})


T = {"template": "segment_share", "entity": "a", "entity_b": None}


def test_registry_validates_rejects_and_is_reproducible(store):
    p = panel()
    lvl = reg.register(store, {"family": "baseline.level", "target": T}, p, registered_by="engine:t")
    assert lvl["status"] in ("validated", "unvalidated") and lvl["validation"]["reproducible"]
    again = reg.register(store, {"family": "baseline.level", "target": T}, p, registered_by="engine:t")
    assert again["model_version_id"] == lvl["model_version_id"]  # idempotent, one row
    assert store.conn.execute("SELECT COUNT(*) FROM model_versions").fetchone()[0] == 1
    assert reg.execute({"family": "stat.holt", "target": T}, p) == reg.execute({"family": "stat.holt", "target": T}, p)
    did = reg.register(store, {"family": "causal.did", "target": T}, p, registered_by="engine:t")
    assert did["status"] == "rejected" and "unavailable" in did["validation"]["problems"][0]
    sim = reg.check_spec({"family": "simulation.share_shift", "target": T}, p)
    assert "not a forecaster" in sim[0]
    bad = reg.check_spec({"family": "diffusion.logistic", "target": T, "assumptions": [{"name": "saturation_share", "value": 0.1}]}, p)
    assert "below the observed maximum" in bad[0]
    assert "not a parameter" in reg.check_spec({"family": "stat.holt", "target": T,
                                                "assumptions": [{"name": "elasticity", "value": 2}]}, p)[0]
    short = reg.check_spec({"family": "baseline.level", "target": T}, panel(n=8))
    assert "at least 12" in short[0]
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.conn.execute("UPDATE model_versions SET status='validated'")


def test_backtest_uses_only_data_before_each_origin():
    p = panel(trend=0.01)
    bt = reg.backtest({"family": "baseline.momentum", "target": T}, p)
    assert bt["n"] == reg.BACKTEST_ORIGINS and bt["mae"] is not None
    # appending future quarters must not change a forecast made at the original cut-off
    a = reg.execute({"family": "baseline.momentum", "target": T}, panel(n=20))
    b_full = panel(n=24)
    b_cut = SharePanel("m", "test", "x", "u", "seg", ["a", "b"],
                       {k: {q: v for q, v in c.items() if q in panel(n=20).periods} for k, c in b_full.cells.items()}, {})
    assert reg.execute({"family": "baseline.momentum", "target": T}, b_cut)["point"] == a["point"]


def test_share_shift_is_a_labelled_scenario():
    base = {"point": 0.10, "interval80": [0.09, 0.11]}
    s = reg.share_shift(base, (0.5, 1.0), (0.25, 0.75))
    assert s["family"] == "simulation.share_shift" and s["outcome_range"][0] < s["outcome_point"] < s["outcome_range"][1]
    assert s["effect_pp_assumed"] == [0.5, 1.0] and s["competitor_offset_assumed"] == [0.25, 0.75]


def test_approval_rules(store):
    with pytest.raises(ap.AuthorizationError, match="agents may not request"):
        ap.request(store, "paid_run", "investigation:x", "agent:loop_strategist:v1", max_usd=0.1)
    with pytest.raises(ap.AuthorizationError, match="agents may not request"):
        ap.request(store, "external_action", "campaign", "agent:loop_strategist:v1")
    with pytest.raises(ap.AuthorizationError, match="max_usd"):
        ap.request(store, "paid_run", "investigation:x", "founder")
    xp = ap.request(store, "experiment", "xp:1", "engine:investigation", payload={"legal_review_required": True})
    for who in ("agent:x", "engine:x", ""):
        with pytest.raises(ap.AuthorizationError, match="named person"):
            ap.decide(store, xp, "granted", who)
    with pytest.raises(ap.AuthorizationError, match="legal"):
        ap.decide(store, xp, "granted", "founder")
    with pytest.raises(ap.AuthorizationError, match="measurable"):
        ap.decide(store, xp, "granted", "founder", evidence={"legal_review_ref": "LR-1"})
    assert ap.decide(store, xp, "granted", "founder", evidence={"legal_review_ref": "LR-1",
                                                                "measurable_outcomes": "segment share +0.2 pp"})["status"] == "granted"
    pr = ap.request(store, "paid_run", "investigation:x", "founder", max_usd=0.1)
    with pytest.raises(ap.AuthorizationError, match="own request"):
        ap.decide(store, pr, "granted", "founder")
    ap.decide(store, pr, "granted", "reviewer")
    assert ap.require(store, pr, "paid_run", "investigation:x", 0.05)["status"] == "granted"
    for args, msg in (((pr, "paid_run", "investigation:y", 0.05), "not paid_run"), ((pr, "experiment", "investigation:x"), "not"),
                      ((pr, "paid_run", "investigation:x", 0.2), "exceeds"), ((None, "paid_run", "investigation:x"), "none given")):
        with pytest.raises(ap.AuthorizationError, match=msg.split()[0]):
            ap.require(store, *args)
    ap.decide(store, pr, "revoked", "reviewer")
    with pytest.raises(ap.AuthorizationError, match="revoked"):
        ap.require(store, pr, "paid_run", "investigation:x", 0.05)
    for sql in ("UPDATE approvals SET max_usd=9", "DELETE FROM approval_decisions"):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            store.conn.execute(sql)
    from halibut.epistemic.events import EventLog
    types = [e["event_type"] for e in EventLog(store).read()]
    assert "approval.requested" in types and "approval.granted" in types and "approval.revoked" in types


def test_routing_is_configuration(store):
    rt = load_routing()
    assert set(rt["roles"]) == {"hypothesis_generator", "loop_critic", "strategist"}
    assert all(v["model"] == "claude-haiku-5-5" for v in rt["roles"].values())
    r = Router("mock", store, "s")
    p1, mo = r.provider("hypothesis_generator")
    p2, _ = r.provider("strategist")
    assert p1 is p2 and mo == rt["roles"]["hypothesis_generator"]["max_output_tokens"]
    alt = {**rt, "roles": {**rt["roles"], "strategist": {**rt["roles"]["strategist"], "model": "claude-sonnet-5-5"}}}
    assert Router("mock", store, "s", routing=alt).fingerprint != r.fingerprint
    with pytest.raises(ValueError):
        Router("openai", store, "s")


def test_baselines_use_pooled_windows_like_tests():
    """A momentum forecast must move in the direction of the POOLED L4 - P4 change (the statistic tests compare),
    and the level forecast must equal the pooled L4 value."""
    from halibut.investigation.compiler import compile_implication, holds
    for trend in (0.006, -0.004):
        p = panel(trend=trend)
        L4, P4 = p.windows()
        l4, p4 = p.value("segment_share", "a", L4)[0], p.value("segment_share", "a", P4)[0]
        assert reg.execute({"family": "baseline.level", "target": T}, p)["point"] == pytest.approx(l4)
        mom = reg.execute({"family": "baseline.momentum", "target": T}, p)["point"]
        assert (mom - l4) * (l4 - p4) > 0
        spec, _ = compile_implication({"template": "segment_share", "entity": "a", "entity_b": "",
                                       "comparator": "increase_vs_L4" if l4 > p4 else "decrease_vs_L4", "reference_metric": ""},
                                      p, p.metrics())
        assert spec["trend_check"]["trend_consistent"] is True and holds(mom, spec["comparator"], spec["reference_value"])

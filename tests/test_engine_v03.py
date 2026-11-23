"""Engine 0.3 features: purchase opportunity, social signal, drivers, expected shares."""

import copy

import numpy as np
import pytest

from halibut.config import ConfigError, config_hash, parse_config
from halibut.engine import build_world, run_scenario
from halibut.metrics import NO_PURCHASE_CAUSES


def v12(raw, **sections):
    r = copy.deepcopy(raw)
    r["schema_version"] = "1.2"
    for k, v in sections.items():
        if isinstance(v, dict) and isinstance(r.get(k), dict):
            r[k].update(v)
        else:
            r[k] = v
    return parse_config(r)


def run(cfg, scenario="control", seed=0):
    return run_scenario(cfg, build_world(cfg, seed), cfg.scenario(scenario), trace=True)


def test_defaults_are_v1_identical_and_hash_neutral(raw):
    base = parse_config(raw)
    cfg = v12(raw)
    assert cfg.to_dict() == {**base.to_dict(), "schema_version": "1.2"}
    a, b = run(base), run(cfg)
    keys = [k for k in a.metrics[0] if not k.startswith(("expected_share_", "in_market", "np_not_in_market"))]
    assert [{k: m[k] for k in keys} for m in a.metrics] == [{k: m[k] for k in keys} for m in b.metrics]


@pytest.mark.parametrize("section", [{"purchase_opportunity": {"enabled": True, "probability": 0.1}},
                                     {"social_influence": {"signal": "current_product"}},
                                     {"drivers": [{"product": "matcha", "attribute": "quality", "values": [0.5]}]}])
def test_new_features_require_schema_12(raw, section):
    r = copy.deepcopy(raw)
    for k, v in section.items():
        if isinstance(v, dict) and isinstance(r.get(k), dict):
            r[k].update(v)
        else:
            r[k] = v
    with pytest.raises(ConfigError, match="1.2"):
        parse_config(r)


def test_purchase_opportunity(raw):
    cfg = v12(raw, purchase_opportunity={"enabled": True, "probability": 0.1})
    r = run(cfg)
    n = cfg.population.size
    in_market = np.array([m["in_market"] for m in r.metrics])
    assert abs(in_market.mean() / n - 0.1) < 0.02
    for m in r.metrics:
        assert m["purchasers"] <= m["in_market"]
        assert m["np_not_in_market"] == n - m["in_market"]
        assert sum(m[c] for c in NO_PURCHASE_CAUSES) == n - m["purchasers"]
    # probability 1 equals the opportunity model being off (all in market) except for the extra stream
    on = run(v12(raw, purchase_opportunity={"enabled": True, "probability": 1.0}))
    off = run(v12(raw))
    assert (on.choices == off.choices).all()


def test_out_of_market_consumers_keep_current_product(raw):
    cfg = v12(raw, purchase_opportunity={"enabled": True, "probability": 0.05})
    world = build_world(cfg, 3)
    r = run_scenario(cfg, world, cfg.scenario("control"), trace=True)
    # adoption shares move slowly: current products change only for buyers
    first, last = r.metrics[0], r.metrics[-1]
    changed = abs(last["adoption_share_matcha"] - first["adoption_share_matcha"])
    full = run_scenario(v12(raw), build_world(v12(raw), 3), v12(raw).scenario("control"))
    changed_full = abs(full.metrics[-1]["adoption_share_matcha"] - full.metrics[0]["adoption_share_matcha"])
    assert changed < changed_full


def test_current_product_signal_differs_from_last_purchase(raw):
    a = run(v12(raw, purchase_opportunity={"enabled": True, "probability": 0.1}))
    b = run(v12(raw, purchase_opportunity={"enabled": True, "probability": 0.1},
                social_influence={"signal": "current_product"}))
    assert (a.choices != b.choices).any()


def test_drivers_apply_in_every_scenario_and_hold_last_value(raw):
    path = [0.1, 0.2, 0.3]
    cfg = v12(raw, drivers=[{"product": "matcha", "attribute": "quality", "values": path, "start_week": 2}])
    for s in cfg.scenario_names:
        q = [m["quality_matcha"] for m in run(cfg, s).metrics]
        assert q[:2] == [0.7, 0.7] and q[2:5] == path and set(q[5:]) == {0.3}
    assert config_hash(cfg) != config_hash(v12(raw))


def test_expected_share_tracks_realised_share(example_raw):
    r = copy.deepcopy(example_raw)
    r["population"]["size"] = 6000
    r["experiment"]["weeks"] = 6
    r["scenarios"] = [{"name": "control"}]
    cfg = v12(r)
    rows = run(cfg).metrics
    exp = np.mean([m["expected_share_matcha"] for m in rows])
    real = np.mean([m["buyer_share_matcha"] for m in rows])
    assert abs(exp - real) < 0.01
    assert all(abs(m["expected_share_coffee"] + m["expected_share_matcha"] - 1) < 1e-9 for m in rows)

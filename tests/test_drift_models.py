import copy

import numpy as np
import pytest

from halibut.config import ConfigError, PreferenceDriftConfig, parse_config
from halibut.engine import build_world, run_scenario
from halibut.influence import apply_drift, drift_preferences

RNG = np.random.default_rng(0)
N, J = 200, 2


def _iterate(cfg, pref, base, susc, share, steps=3000):
    for _ in range(steps):
        pref = apply_drift(cfg, pref, base, susc, share)
    return pref


@pytest.fixture
def state():
    return dict(pref=RNG.random((N, J)), base=RNG.random((N, J)),
                susc=RNG.random(N), share=RNG.dirichlet([1, 1, 1], N)[:, :2])


def test_none_and_disabled_leave_preferences_unchanged(state):
    for cfg in (PreferenceDriftConfig(model="none"), PreferenceDriftConfig(enabled=False, model="exposure_reinforcement")):
        assert apply_drift(cfg, state["pref"], state["base"], state["susc"], state["share"]) is state["pref"]


def test_conformity_is_v1_rule(state):
    cfg = PreferenceDriftConfig(rate=0.07)
    a = apply_drift(cfg, state["pref"], state["base"], state["susc"], state["share"])
    assert np.array_equal(a, drift_preferences(state["pref"], state["susc"], state["share"], 0.07))


@pytest.mark.parametrize("model, fixed_point", [
    ("conformity", lambda a, k, d, S, b: S + 0 * b),
    ("baseline_reversion", lambda a, k, d, S, b: (a * S + k * b) / (a + k)),
    ("exposure_reinforcement", lambda a, k, d, S, b: (a * S + d * b) / (a * S + d)),
])
def test_analytic_fixed_points(state, model, fixed_point):
    cfg = PreferenceDriftConfig(model=model, rate=0.2, reversion_rate=0.03, decay_rate=0.04)
    a = np.clip(cfg.rate * state["susc"], 0, 1)[:, None]
    end = _iterate(cfg, state["pref"], state["base"], state["susc"], state["share"])
    expected = fixed_point(a, cfg.reversion_rate, cfg.decay_rate, state["share"], state["base"])
    mask = (a[:, 0] > 0.02)  # tiny steps converge slowly; skip near-zero susceptibility
    assert np.allclose(end[mask], expected[mask], atol=1e-6)


@pytest.mark.parametrize("model", ["conformity", "baseline_reversion", "exposure_reinforcement"])
def test_preferences_stay_in_unit_interval_under_extreme_rates(state, model):
    cfg = PreferenceDriftConfig(model=model, rate=1.0, reversion_rate=0.9, decay_rate=0.9)
    big = state["susc"] * 50  # forces clipping of the step
    pref = state["pref"]
    for _ in range(50):
        pref = apply_drift(cfg, pref, state["base"], big, state["share"])
        assert pref.min() >= -1e-12 and pref.max() <= 1 + 1e-12


def test_bias_properties():
    """Conformity drives an unpopular product's preference below baseline;
    exposure reinforcement never does; reversion stays between share and baseline."""
    base = np.array([[0.5, 0.5]])
    share = np.array([[0.9, 0.05]])
    susc = np.array([1.0])
    conf = _iterate(PreferenceDriftConfig(model="conformity", rate=0.1), base, base, susc, share)
    exp_ = _iterate(PreferenceDriftConfig(model="exposure_reinforcement", rate=0.1, decay_rate=0.05), base, base, susc, share)
    rev = _iterate(PreferenceDriftConfig(model="baseline_reversion", rate=0.1, reversion_rate=0.05), base, base, susc, share)
    assert conf[0, 1] < 0.1
    assert exp_[0, 1] >= 0.5 and exp_[0, 0] > exp_[0, 1]
    assert 0.05 < rev[0, 1] < 0.5


def test_drift_model_config(raw):
    raw["preference_drift"]["model"] = "baseline_reversion"
    with pytest.raises(ConfigError, match="1.1"):
        parse_config(raw)
    raw["schema_version"] = "1.1"
    cfg = parse_config(raw)
    assert cfg.preference_drift.effective_model == "baseline_reversion"
    assert cfg.to_dict()["preference_drift"]["model"] == "baseline_reversion"
    raw["preference_drift"]["model"] = "magic"
    with pytest.raises(ConfigError, match="model"):
        parse_config(raw)


@pytest.mark.parametrize("model", ["none", "baseline_reversion", "exposure_reinforcement"])
def test_engine_runs_each_model_and_none_equals_disabled(raw, model):
    raw["schema_version"] = "1.1"
    r = copy.deepcopy(raw)
    r["preference_drift"]["model"] = model
    cfg = parse_config(r)
    run = run_scenario(cfg, build_world(cfg, 0), cfg.scenario("combined"), trace=True)
    assert 0 <= run.final_preference.min() and run.final_preference.max() <= 1
    if model == "none":
        off = copy.deepcopy(raw)
        off["preference_drift"]["enabled"] = False
        cfg_off = parse_config(off)
        run_off = run_scenario(cfg_off, build_world(cfg_off, 0), cfg_off.scenario("combined"), trace=True)
        assert (run.choices == run_off.choices).all()

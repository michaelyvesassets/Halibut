import copy
import json

import numpy as np
import pytest

from halibut.calibration import (
    Moment, MomentSimulator, calibrate, calibration_labels, load_calibration, load_targets,
    make_twin_targets, objective, write_calibration,
)
from halibut.config import ConfigError, parse_config
from halibut.params import Parameter, ParameterSpace

from .conftest import EXAMPLE


def _homogeneous_raw(example_raw, n=20000, weeks=10):
    raw = copy.deepcopy(example_raw)
    raw["schema_version"] = "1.1"
    raw["population"]["size"] = n
    raw["experiment"].update(weeks=weeks, replicates=1, summary_window_weeks=2)
    seg = copy.deepcopy(raw["population"]["segments"][0])
    seg.update(name="all", weight=1.0, initial_product_shares={"none": 1.0})
    seg["attributes"] = dict(budget=10.0, consumption=1.0, price_sensitivity=4.0, brand_loyalty=0.0,
                             social_susceptibility=0.0, switching_resistance=0.0)
    seg["preferences"] = {"coffee": 0.5, "matcha": 0.5}
    raw["population"]["segments"] = [seg]
    raw["products"][0].update(price=2.0, quality=0.5, availability=1.0)
    raw["products"][1].update(price=3.0, quality=1.0, availability=1.0)
    raw["decision"].update(outside_utility=0.0, preference_weight=1.0, quality_weight=1.0, temperature=1.0)
    raw["social_influence"]["enabled"] = False
    raw["preference_drift"]["enabled"] = False
    raw["network"]["k"] = 4
    raw["scenarios"] = [{"name": "control"}]
    return raw


def test_arc_elasticity_matches_logit_analytic(example_raw):
    raw = _homogeneous_raw(example_raw)
    space = ParameterSpace((Parameter("dummy", ("decision.outside_utility",), -1.0, 1.0),))
    sim = MomentSimulator(raw, space, seeds=[1])
    own = Moment("own", "elasticity", "units", "control", 0, 10, None, None, product="matcha", price_of="matcha")
    cross = Moment("cross", "elasticity", "units", "control", 0, 10, None, None, product="coffee", price_of="matcha")
    est_own, est_cross = sim.simulate(np.array([[0.5]]), [own, cross])[0]
    # analytic: V = pref + quality - lambda * q * p / B; outside 0
    v = np.array([0.0, 0.5 + 0.5 - 4 * 2 / 10, 0.5 + 1.0 - 4 * 3 / 10])
    P = np.exp(v) / np.exp(v).sum()
    dv_dlnp = -4 * 3 / 10
    assert est_own == pytest.approx(dv_dlnp * (1 - P[2]), rel=0.05)
    assert est_cross == pytest.approx(-dv_dlnp * P[2], rel=0.08)  # logit cross elasticity
    assert est_own < 0 < est_cross


def test_objective_and_labels():
    ms = [Moment("a", "window_mean", "x", "control", 0, 1, 1.0, 0.5), Moment("b", "window_mean", "y", "control", 0, 1, 0.0, 2.0)]
    assert objective(np.array([2.0, 2.0]), ms) == pytest.approx(4 + 1)
    from halibut.calibration import Targets
    synth = calibration_labels(Targets("t", "synthetic", "", "", ms, {}))
    assert synth["calibration_status"] == "calibrated_to_synthetic"
    assert synth["empirically_validated"] is False and "NOT real-world validation" in synth["validation_note"]
    emp = calibration_labels(Targets("t", "empirical", "", "", ms, {}))
    assert emp["empirically_validated"] is False and "not validation" in emp["validation_note"]


SPEC = """
schema_version = "1.0"
[calibration]
name = "twin_test"
base_config = "{base}"
targets = "targets.toml"
seeds = [11, 12]
time_limit_minutes = 5
[calibration.optimizer]
popsize = 6
maxiter = 8
seed = 3
nelder_mead_maxfev = 20
[calibration.abc]
samples = 40
accept_quantile = 0.1
[calibration.identifiability]
slice_points = 3
[calibration.twin]
seeds = [91, 92, 93]
rel_se_floor = 0.02
truth = {{ outside_utility = 1.3, price_sensitivity_scale = 1.4 }}
[overrides]
"schema_version" = "1.1"
"population.size" = 1500
"experiment.weeks" = 14
"experiment.summary_window_weeks" = 2
"network.k" = 6
"social_influence.enabled" = false
"preference_drift.enabled" = false
[[parameters]]
name = "outside_utility"
path = "decision.outside_utility"
range = [0.0, 2.0]
[[parameters]]
name = "price_sensitivity_scale"
mode = "scale"
scale = "log"
range = [0.5, 2.0]
paths = ["population.segments[0].attributes.price_sensitivity.median",
         "population.segments[1].attributes.price_sensitivity.median",
         "population.segments[2].attributes.price_sensitivity.median"]
"""

TEMPLATE = """
schema_version = "1.0"
[targets]
name = "twin_test_targets"
kind = "synthetic"
source = "template"
[[moments]]
name = "no_purchase"
type = "window_mean"
metric = "no_purchase_rate"
start_week = 4
end_week = 14
[[moments]]
name = "matcha_share"
type = "window_mean"
metric = "purchase_share_matcha"
start_week = 4
end_week = 14
[[moments]]
name = "matcha_own_elasticity"
type = "elasticity"
product = "matcha"
price_of = "matcha"
start_week = 4
end_week = 14
[[moments]]
name = "price_war_effect"
type = "effect"
role = "holdout"
scenario = "price_war"
metric = "purchase_share_matcha"
start_week = 11
end_week = 14
"""


@pytest.fixture
def twin(tmp_path):
    (tmp_path / "spec.toml").write_text(SPEC.format(base=EXAMPLE))
    (tmp_path / "template.toml").write_text(TEMPLATE)
    spec = load_calibration(tmp_path / "spec.toml")
    make_twin_targets(spec, tmp_path / "template.toml", tmp_path / "targets.toml")
    return spec, tmp_path


def test_twin_targets_are_labelled_synthetic(twin):
    spec, tmp = twin
    tg = load_targets(tmp / "targets.toml", spec.base.config)
    assert tg.kind == "synthetic" and tg.truth == {"outside_utility": 1.3, "price_sensitivity_scale": 1.4}
    assert "NOT data" in (tmp / "targets.toml").read_text()
    assert [m.role for m in tg.moments] == ["fit", "fit", "fit", "holdout"]
    assert all(m.se > 0 for m in tg.moments)


def test_twin_recovery(twin):
    """What the method must guarantee in a twin experiment (with target seeds
    distinct from calibration seeds, so the truth is not an exact minimum):

    1. the optimiser finds an objective at least as good as the truth's;
    2. the ABC 90% interval covers the truth for every parameter;
    3. identifiability diagnostics expose the outside-utility / price-
       sensitivity trade-off (both raise the no-purchase rate), rather than
       hiding it behind a point estimate.
    """
    spec, tmp = twin
    res = calibrate(spec, workers=1, log=lambda *_: None)
    tg = load_targets(tmp / "targets.toml", spec.base.config)
    sim = MomentSimulator(spec.base.raw, spec.space, spec.base.seeds)
    truth_u = np.array([p.unit(spec.twin_truth[p.name]) for p in spec.space.parameters])
    L_truth = objective(sim.simulate(truth_u[None], tg.fit), tg.fit)[0]
    assert res["objective"] <= L_truth + 1e-9
    for name, r in res["recovery"].items():
        assert r["truth_in_abc_90pct_interval"], (name, r)
    ident = res["identifiability"]
    assert ident["numerical_rank"] == 2
    corr = ident["gauss_newton_correlation"][0][1]
    assert abs(corr) > 0.5  # strongly confounded by these moments
    assert res["abc"]["accepted"] == 4
    assert res["labels"]["calibration_status"] == "calibrated_to_synthetic"
    path = write_calibration(spec, res, tmp / "out")
    doc = json.loads((path / "calibration.json").read_text())
    assert doc["empirically_validated"] is False and doc["result_type"] == "synthetic_simulation"


def test_calibration_spec_validation(twin):
    spec, tmp = twin
    bad = (tmp / "spec.toml").read_text().replace("seeds = [91, 92, 93]", "seeds = [11, 92]")
    (tmp / "bad.toml").write_text(bad)
    with pytest.raises(ConfigError, match="differ"):
        load_calibration(tmp / "bad.toml")
    (tmp / "t2.toml").write_text(TEMPLATE)
    with pytest.raises(ConfigError, match="se"):
        load_targets(tmp / "t2.toml", spec.base.config)  # template has no values

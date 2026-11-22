import dataclasses

import numpy as np
import pytest

from halibut.batch import (
    BudgetExceeded, Runner, Task, check_budget, estimate_seconds, evaluate_task, seconds_per_scenario_run,
)
from halibut.config import ConfigError, config_hash, parse_config
from halibut.experiment import run_experiment
from halibut.params import Parameter, ParameterSpace, get_path, set_paths


def test_get_and_set_paths(raw):
    assert get_path(raw, "products[matcha].price") == 5.0
    assert get_path(raw, "products[1].price") == 5.0
    assert get_path(raw, "population.segments[mainstream].attributes.price_sensitivity.median") == 2.0
    assert get_path(raw, "scenarios[price_war].interventions[0].value") == 0.75
    new = set_paths(raw, {"products[matcha].price": 4.0, "decision.outside_utility": 0.5})
    assert get_path(new, "products[matcha].price") == 4.0
    assert get_path(raw, "products[matcha].price") == 5.0  # original untouched
    cfg = parse_config(new)
    assert cfg.products[1].price == 4.0 and cfg.decision.outside_utility == 0.5
    assert config_hash(cfg) != config_hash(parse_config(raw))


@pytest.mark.parametrize("path", ["products[tea].price", "nope.x", "products[9].price", "products.price[0]", "a..b"])
def test_bad_paths(raw, path):
    with pytest.raises(ConfigError):
        get_path(raw, path)


def test_parameter_transforms():
    lin = Parameter("x", ("a",), 2.0, 4.0)
    log = Parameter("y", ("b",), 0.5, 2.0, scale="log")
    assert lin.value(0) == 2 and lin.value(1) == 4 and lin.value(0.5) == 3
    assert log.value(0.5) == pytest.approx(1.0)
    assert log.unit(log.value(0.3)) == pytest.approx(0.3)
    with pytest.raises(ValueError):
        lin.value(1.5)


def test_space_apply_scale_mode(raw):
    paths = tuple(f"population.segments[{i}].attributes.price_sensitivity.median" for i in range(3))
    space = ParameterSpace((Parameter("ps", paths, 0.5, 2.0, scale="log", mode="scale"),
                            Parameter("ou", ("decision.outside_utility",), 0.0, 2.0)))
    space.validate_against(raw)
    new = space.apply(raw, space.values([1.0, 0.25]))
    for p in paths:
        assert get_path(new, p) == pytest.approx(2.0 * get_path(raw, p))
    assert get_path(new, "decision.outside_utility") == 0.5
    parse_config(new)


def test_runner_matches_engine_and_is_worker_independent(cfg, tmp_path):
    tasks = [Task(cfg, seed, ("control", "price_war")) for seed in (0, 1, 2)]
    serial = Runner(workers=1).map(tasks)
    parallel = Runner(workers=2).map(tasks)
    strip = lambda rs: [{k: v for k, v in r.items() if k != "seconds"} for r in rs]  # noqa: E731
    assert strip(serial) == strip(parallel)
    # end-window means agree with the experiment pipeline
    res = run_experiment(cfg, ["price_war"], seeds=[0])
    last = [r for r in res.timeseries if r["scenario"] == "price_war" and r["week"] >= cfg.weeks - cfg.summary_window_weeks]
    assert serial[0]["scenarios"]["price_war"]["end"]["purchase_share_matcha"] == \
        pytest.approx(np.mean([r["purchase_share_matcha"] for r in last]), abs=0, rel=1e-15)


def test_runner_cache(cfg, tmp_path):
    tasks = [Task(cfg, seed, ("control",)) for seed in (0, 1)]
    first = Runner(cache_dir=tmp_path).map(tasks)
    again = Runner(cache_dir=tmp_path)
    second = again.map(tasks)
    assert again.cache_hits == 2 and again.evaluated == 0
    assert first == second
    other = Runner(cache_dir=tmp_path)
    other.map([Task(dataclasses.replace(cfg, warmup_weeks=3), 0, ("control",))])
    assert other.evaluated == 1  # different config -> different key


def test_estimates_and_budget(cfg):
    s = seconds_per_scenario_run(cfg)
    assert 0 < s < 5
    assert estimate_seconds(1.0, 100, 2) == pytest.approx(100 / 1.7)
    check_budget(10, 60, confirmed=False)
    with pytest.raises(BudgetExceeded):
        check_budget(100, 60, confirmed=False)
    check_budget(100, 60, confirmed=True)

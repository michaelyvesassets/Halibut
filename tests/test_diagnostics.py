import copy
import dataclasses

import numpy as np
import pytest

from halibut.config import ConfigError, config_hash, parse_config
from halibut.diagnostics import (
    accounting_checks, run_diagnostics, size_convergence, stockout_counterfactual, transient_check,
)
from halibut.engine import build_world, run_scenario
from halibut.experiment import run_experiment
from halibut.metrics import NO_PURCHASE_CAUSES, tick_metrics
from halibut.interventions import first_active_week


def test_no_purchase_causes_hand_cases():
    # consumers: 0 nothing affordable, 1 affordable-but-out-of-stock, 2 current infeasible
    # (current coffee out of stock, matcha feasible), 3 voluntary, 4 voluntary (no current),
    # 5 buys matcha with coffee out of stock -> forced substitution
    available = np.array([[1, 1], [0, 1], [0, 1], [1, 1], [1, 1], [0, 1]], dtype=bool)
    affordable = np.array([[0, 0], [1, 0], [1, 1], [1, 1], [1, 0], [1, 1]], dtype=bool)
    choices = np.array([0, 0, 0, 0, 0, 2])
    current = np.array([1, 2, 1, 1, 0, 1])
    m = tick_metrics(0, ("coffee", "matcha"), np.array([1.0, 2.0]), np.zeros(2), np.ones(2),
                     choices, current, current, np.where(choices > 0, choices, current),
                     np.ones(6), np.full(6, 10.0), np.zeros((6, 2)),
                     available=available, affordable=affordable)
    assert m["np_nothing_affordable"] == 1
    assert m["np_nothing_in_stock"] == 1
    assert m["np_current_infeasible"] == 1
    assert m["np_voluntary"] == 2
    assert sum(m[c] for c in NO_PURCHASE_CAUSES) == 6 - m["purchasers"]
    assert m["forced_substitution"] == 1
    assert m["current_out_of_stock"] == 2  # consumers 2 and 5
    assert m["current_unaffordable"] == 2  # consumers 0 and 1


def test_causes_partition_non_buyers_in_full_runs(cfg):
    world = build_world(cfg, 0)
    for s in cfg.scenario_names:
        for m in run_scenario(cfg, world, cfg.scenario(s)).metrics:
            assert sum(m[c] for c in NO_PURCHASE_CAUSES) == m["consumers"] - m["purchasers"]
            assert min(m[c] for c in NO_PURCHASE_CAUSES) >= 0


def test_diagnostics_pass_on_clean_runs(cfg):
    checks = run_diagnostics(run_experiment(cfg))
    fails = [c for c in checks if c.status == "fail"]
    assert not fails, fails
    names = {c.name for c in checks}
    assert {"flows_continuous_week_to_week", "no_purchase_causes_partition", "switching_churn",
            "stockouts_and_no_purchase_causes", "control_transient", "monte_carlo_precision"} <= names


@pytest.mark.parametrize("field, delta", [
    ("purchase_share_coffee", 0.1), ("units_matcha", 1.0), ("flow_none_to_none", 1),
    ("np_voluntary", 1), ("revenue_coffee", 5.0),
])
def test_accounting_detects_corruption(cfg, field, delta):
    result = run_experiment(cfg, seeds=[0])
    result.timeseries[7][field] += delta
    assert any(c.status == "fail" for c in accounting_checks(result))


def test_transient_check_flags_trending_control(cfg):
    chk = transient_check(run_experiment(cfg, seeds=[0, 1]), share_tol=0.0, units_tol=0.0)
    assert chk.status == "warn"
    chk = transient_check(run_experiment(cfg, seeds=[0, 1]), share_tol=1.0, units_tol=1.0)
    assert chk.status == "pass"


def test_full_availability_counterfactual(cfg):
    world = build_world(cfg, 0)
    run = run_scenario(cfg, world, cfg.scenario("control"), full_availability=True)
    for m in run.metrics:
        assert m["np_nothing_in_stock"] == 0 and m["current_out_of_stock"] == 0
    report = stockout_counterfactual(cfg, seeds=[0, 1], scenarios=["price_war"])
    assert set(report["scenarios"]) == {"control", "price_war"}
    assert "vs_control" in report["scenarios"]["price_war"]["full_availability"]


def test_size_convergence_runs(cfg):
    out = size_convergence(cfg, [200, 400], seeds=[0, 1], scenarios=["control"])
    assert set(out["results"]) == {"200", "400"}
    assert 0 <= out["results"]["400"]["control"]["purchase_share_matcha"]["mean"] <= 1


# --------------------------------------------------------------------------- warm-up


def test_warmup_requires_schema_11(raw):
    raw["experiment"]["warmup_weeks"] = 10
    with pytest.raises(ConfigError, match="1.1"):
        parse_config(raw)
    raw["schema_version"] = "1.1"
    assert parse_config(raw).warmup_weeks == 10


def test_warmup_zero_is_default_and_hash_neutral(raw):
    h10 = config_hash(parse_config(raw))
    raw11 = copy.deepcopy(raw)
    raw11["schema_version"] = "1.1"
    raw11["experiment"]["warmup_weeks"] = 0
    cfg11 = parse_config(raw11)
    assert cfg11.warmup_weeks == 0 and "warmup_weeks" not in cfg11.to_dict()
    assert config_hash(cfg11) != h10  # schema version differs, nothing else
    a = run_scenario(parse_config(raw), build_world(parse_config(raw), 0), parse_config(raw).scenario("control"))
    b = run_scenario(cfg11, build_world(cfg11, 0), cfg11.scenario("control"))
    assert a.metrics == b.metrics


def test_warmup_changes_start_state_but_preserves_crn(cfg):
    warm = dataclasses.replace(cfg, warmup_weeks=15)
    cold_world, warm_world = build_world(cfg, 0), build_world(warm, 0)
    assert np.array_equal(cold_world.population.budget, warm_world.population.budget)
    assert not np.array_equal(cold_world.initial_current, warm_world.initial_current)
    assert not np.array_equal(cold_world.initial_preference, warm_world.initial_preference)
    # base preferences (drift anchor) are untouched by warm-up
    assert np.array_equal(cold_world.population.preference, build_world(cfg, 0).population.preference)
    # deterministic, and interventions still have no effect before their start
    assert np.array_equal(build_world(warm, 0).initial_current, warm_world.initial_current)
    control = run_scenario(warm, warm_world, warm.scenario("control"))
    for s in warm.scenario_names[1:]:
        treated = run_scenario(warm, warm_world, warm.scenario(s))
        start = first_active_week(warm.scenario(s).interventions)
        assert treated.metrics[:start] == control.metrics[:start]


def test_warmup_reduces_control_transient(example_raw):
    # On the example market the cold-start control trends; a year of warm-up
    # should shrink the first-to-last-window matcha share change.
    raw = copy.deepcopy(example_raw)
    raw["population"]["size"] = 1500
    raw["experiment"]["replicates"] = 2
    cfg = parse_config(raw)
    cold = transient_check(run_experiment(cfg, scenarios=["control"]))
    warm = transient_check(run_experiment(dataclasses.replace(cfg, warmup_weeks=52), scenarios=["control"]))
    d_cold = abs(cold.details["purchase_share_change"]["matcha"]["mean"])
    d_warm = abs(warm.details["purchase_share_change"]["matcha"]["mean"])
    assert d_warm < d_cold

import numpy as np
import pytest

from halibut.config import parse_config
from halibut.engine import build_world, run_scenario
from halibut.interventions import (
    ProductChange, Schedule, effective_market, first_active_week, select_targets,
)
from halibut.market import Market


def test_schedule_intensity():
    s = Schedule(start_week=5)
    assert [s.intensity(w) for w in (0, 4, 5, 50)] == [0, 0, 1, 1]
    bounded = Schedule(start_week=2, end_week=4)
    assert [bounded.intensity(w) for w in range(6)] == [0, 0, 1, 1, 0, 0]
    ramp = Schedule(start_week=8, ramp_weeks=4)
    assert [ramp.intensity(w) for w in range(7, 14)] == [0, 0.25, 0.5, 0.75, 1, 1, 1]


def test_effective_market_ops_and_ramp():
    base = Market(("a", "b"), ("x", "x"), np.array([4.0, 5.0]), np.zeros(2), np.array([1.0, 0.7]))
    ivs = [
        ProductChange("b", "price", "multiply", 0.75, Schedule(10)),
        ProductChange("b", "availability", "set", 0.95, Schedule(8, ramp_weeks=12)),
    ]
    m = effective_market(base, ivs, 7)
    assert m.price.tolist() == [4.0, 5.0] and m.availability.tolist() == [1.0, 0.7]
    m = effective_market(base, ivs, 13)  # ramp 6/12 done
    assert m.price.tolist() == [4.0, 3.75]
    assert m.availability[1] == pytest.approx(0.7 + 0.5 * 0.25)
    assert effective_market(base, ivs, 30).availability[1] == pytest.approx(0.95)
    assert base.price.tolist() == [4.0, 5.0]  # base never mutated
    clipped = effective_market(base, [ProductChange("a", "availability", "add", 2.0, Schedule(0))], 0)
    assert clipped.availability[0] == 1.0


def test_top_degree_selection(cfg):
    world = build_world(cfg, 0)
    idx = select_targets("top_degree", 0.02, world.network)
    assert len(idx) == 8  # ceil(0.02 * 400)
    others = np.setdiff1d(np.arange(400), idx)
    assert world.network.degree[idx].min() >= world.network.degree[others].max()


@pytest.mark.parametrize("scenario", ["price_war", "distribution_push", "influencer_campaign", "combined"])
def test_no_effect_before_start_and_effect_after(cfg, scenario):
    world = build_world(cfg, 1)
    control = run_scenario(cfg, world, cfg.scenario("control"), trace=True)
    treated = run_scenario(cfg, world, cfg.scenario(scenario), trace=True)
    start = first_active_week(cfg.scenario(scenario).interventions)
    assert start is not None and start > 0
    for week in range(start):
        assert treated.metrics[week] == control.metrics[week], f"week {week} differs"
    assert (treated.choices[:start] == control.choices[:start]).all()
    assert (treated.choices[start:] != control.choices[start:]).any()


def test_bounded_intervention_reverts_market(raw):
    raw["scenarios"].append({
        "name": "flash_sale",
        "interventions": [{"type": "product_change", "product": "matcha", "attribute": "price",
                           "op": "multiply", "value": 0.5, "start_week": 3, "end_week": 6}],
    })
    cfg = parse_config(raw)
    run = run_scenario(cfg, build_world(cfg, 0), cfg.scenario("flash_sale"))
    prices = [m["price_matcha"] for m in run.metrics]
    assert prices[:3] == [5.0] * 3 and prices[3:6] == [2.5] * 3 and prices[6:] == [5.0] * (cfg.weeks - 6)


def test_seed_adopters_event(raw):
    raw["preference_drift"]["enabled"] = False
    cfg = parse_config(raw)
    world = build_world(cfg, 2)
    camp = run_scenario(cfg, world, cfg.scenario("influencer_campaign"), trace=True)
    ctrl = run_scenario(cfg, world, cfg.scenario("control"), trace=True)
    seeds = select_targets("top_degree", 0.02, world.network)
    week = 6
    # seeds count as last week's matcha buyers in the seeding week's flows
    last_matcha = lambda run: sum(run.metrics[week][f"flow_matcha_to_{b}"] for b in ("none", "coffee", "matcha"))  # noqa: E731
    pre = ctrl.choices[week - 1, seeds]
    assert last_matcha(camp) - last_matcha(ctrl) == int((pre != 2).sum()) > 0
    # their preference is set to the configured value (drift disabled, so it stays)
    assert np.allclose(camp.final_preference[seeds, 1], 0.85)
    others = np.setdiff1d(np.arange(world.population.size), seeds)
    assert np.array_equal(camp.final_preference[others], world.population.preference[others])

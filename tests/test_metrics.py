import numpy as np
import pytest

from halibut.engine import build_world, run_scenario
from halibut.experiment import decompose
from halibut.metrics import tick_metrics


def _hand_metrics():
    # 5 consumers; options: 0 none, 1 coffee, 2 matcha
    return tick_metrics(
        week=0, product_ids=("coffee", "matcha"),
        price=np.array([2.0, 4.0]), quality=np.zeros(2), availability=np.ones(2),
        choices=np.array([1, 1, 2, 0, 2]),
        prev_last_choice=np.array([1, 0, 1, 1, 2]),
        prev_current=np.array([1, 0, 1, 1, 2]),
        new_current=np.array([1, 1, 2, 1, 2]),
        consumption=np.array([1.0, 2.0, 3.0, 4.0, 1.0]),
        budget=np.full(5, 20.0),
        preference=np.full((5, 2), 0.5),
    )


def test_hand_computed_metrics():
    m = _hand_metrics()
    assert m["purchasers"] == 4 and m["no_purchase_rate"] == pytest.approx(0.2)
    assert m["units_coffee"] == 3.0 and m["units_matcha"] == 4.0
    assert m["category_units"] == 7.0
    assert m["revenue_coffee"] == 6.0 and m["revenue_matcha"] == 16.0
    assert m["category_revenue"] == 22.0
    assert m["purchase_share_coffee"] == pytest.approx(3 / 7)
    assert m["buyer_share_matcha"] == pytest.approx(0.5)
    assert m["penetration_matcha"] == pytest.approx(0.4)
    assert m["adoption_share_coffee"] == pytest.approx(0.6)
    assert m["adoption_share_matcha"] == pytest.approx(0.4)
    assert m["switching_rate"] == pytest.approx(1 / 4)  # consumer 2: coffee -> matcha
    assert m["first_purchases"] == 1  # consumer 1
    assert m["flow_coffee_to_matcha"] == 1 and m["flow_coffee_to_none"] == 1
    assert m["flow_none_to_coffee"] == 1 and m["flow_matcha_to_matcha"] == 1
    assert m["max_budget_utilisation"] == pytest.approx(12 / 20)


def test_no_purchases_gives_zero_shares():
    m = tick_metrics(0, ("a",), np.array([1.0]), np.zeros(1), np.ones(1),
                     np.zeros(3, int), np.zeros(3, int), np.zeros(3, int), np.zeros(3, int),
                     np.ones(3), np.ones(3), np.zeros((3, 1)))
    assert m["category_units"] == 0 and m["purchase_share_a"] == 0 and m["no_purchase_rate"] == 1


def test_invariants_over_full_run(cfg):
    products = cfg.product_ids
    for scenario in cfg.scenario_names:
        run = run_scenario(cfg, build_world(cfg, 3), cfg.scenario(scenario))
        for m in run.metrics:
            n = m["consumers"]
            assert sum(m[f"purchase_share_{p}"] for p in products) == pytest.approx(1.0)
            assert sum(m[f"revenue_share_{p}"] for p in products) == pytest.approx(1.0)
            assert sum(m[f"adoption_share_{p}"] for p in products) + m["non_adopter_share"] == pytest.approx(1.0)
            assert m["category_units"] == pytest.approx(sum(m[f"units_{p}"] for p in products))
            for p in products:
                assert m[f"revenue_{p}"] == pytest.approx(m[f"price_{p}"] * m[f"units_{p}"])
            assert m["purchasers"] == sum(m[f"buyers_{p}"] for p in products)
            flows = [v for k, v in m.items() if k.startswith("flow_")]
            assert sum(flows) == n
            # flows into each option equal this week's choice counts
            assert sum(m[f"flow_{a}_to_none"] for a in ("none",) + products) == n - m["purchasers"]
            for p in products:
                assert sum(m[f"flow_{a}_to_{p}"] for a in ("none",) + products) == m[f"buyers_{p}"]
            assert m["max_budget_utilisation"] <= 1.0  # budget constraint never violated


def test_decompose_separates_displacement_from_contraction():
    pure_shift = decompose({"coffee": 100, "matcha": 10}, {"coffee": 80, "matcha": 30})
    assert pure_shift["displaced"] == 20 and pure_shift["contraction"] == 0 and pure_shift["expansion"] == 0
    shrink = decompose({"coffee": 100, "matcha": 10}, {"coffee": 70, "matcha": 20})
    assert shrink["displaced"] == 10 and shrink["contraction"] == 20
    grow = decompose({"coffee": 100, "matcha": 10}, {"coffee": 95, "matcha": 40})
    assert grow["displaced"] == 5 and grow["expansion"] == 25 and grow["net_category_change"] == 25

import dataclasses

import numpy as np

from halibut.config import parse_config
from halibut.decision import LogitChoiceModel
from halibut.engine import build_world, run_scenario
from halibut.influence import drift_preferences

from .conftest import make_context


def _run(raw, scenario="control", seed=0, **sections):
    for section, values in sections.items():
        raw[section].update(values)
    cfg = parse_config(raw)
    return run_scenario(cfg, build_world(cfg, seed), cfg.scenario(scenario), trace=True)


def test_more_neighbour_adoption_raises_choice_probability():
    model = LogitChoiceModel(social_weight=2.0)
    probs = [
        model.probabilities(make_context(
            n=1, social_susceptibility=np.array([0.5]), neighbour_share=np.array([[0.0, s]])))[0, 2]
        for s in (0.0, 0.3, 0.9)
    ]
    assert probs[0] < probs[1] < probs[2]


def test_zero_susceptibility_removes_social_term():
    model = LogitChoiceModel(social_weight=5.0)
    with_net = make_context(n=1, neighbour_share=np.array([[0.9, 0.1]]))
    without = make_context(n=1)
    assert np.allclose(model.utilities(with_net), model.utilities(without))


def test_drift_moves_toward_neighbour_share_and_stays_in_bounds():
    pref = np.array([[0.2, 0.8], [0.5, 0.5]])
    share = np.array([[1.0, 0.0], [0.5, 0.5]])
    out = drift_preferences(pref, np.array([1.0, 1.0]), share, 0.5)
    assert np.allclose(out, [[0.6, 0.4], [0.5, 0.5]])
    assert np.allclose(drift_preferences(pref, np.zeros(2), share, 0.5), pref)
    big = drift_preferences(pref, np.array([10.0, 10.0]), share, 1.0)  # step clipped to 1
    assert np.allclose(big, [[1.0, 0.0], [0.5, 0.5]])


def test_population_with_zero_susceptibility_ignores_network(raw):
    for seg in raw["population"]["segments"]:
        seg["attributes"]["social_susceptibility"] = 0.0
    on = _run(raw)
    off = _run(raw, social_influence={"enabled": False}, preference_drift={"enabled": False})
    assert (on.choices == off.choices).all()


def test_influence_disabled_equals_zero_weight(raw):
    a = _run(dict(raw), social_influence={"enabled": False})
    b = _run(raw, social_influence={"enabled": True, "weight": 0.0})
    assert (a.choices == b.choices).all()


def test_mechanisms_independently_configurable(raw):
    import copy
    runs = {}
    for inf in (True, False):
        for drift in (True, False):
            r = copy.deepcopy(raw)
            runs[inf, drift] = _run(r, social_influence={"enabled": inf},
                                    preference_drift={"enabled": drift})
    cfg = parse_config(raw)
    initial = build_world(cfg, 0).population.preference
    # drift off => preferences never change, regardless of influence
    assert np.array_equal(runs[True, False].final_preference, initial)
    assert np.array_equal(runs[False, False].final_preference, initial)
    # drift on => preferences change even with peer influence off
    assert not np.array_equal(runs[False, True].final_preference, initial)
    # each mechanism alone changes behaviour relative to neither
    base = runs[False, False].choices
    assert (runs[True, False].choices != base).any()
    assert (runs[False, True].choices != base).any()


def test_social_influence_spreads_seeded_adoption_to_neighbours(raw):
    import copy
    from halibut.interventions import select_targets

    def neighbour_matcha_effect(influence_on):
        r = copy.deepcopy(raw)
        r["social_influence"].update(enabled=influence_on, weight=3.0)
        r["preference_drift"]["enabled"] = False
        r["population"]["size"] = 2000  # enough seeded neighbours for a visible effect
        cfg = parse_config(r)
        world = build_world(cfg, 0)
        seeds = select_targets("top_degree", 0.02, world.network)
        nbrs = np.setdiff1d(np.unique(np.concatenate([world.network.neighbours(i) for i in seeds])), seeds)
        camp = run_scenario(cfg, world, cfg.scenario("influencer_campaign"), trace=True)
        ctrl = run_scenario(cfg, world, cfg.scenario("control"), trace=True)
        weeks = slice(6, 12)  # from the seeding week, when neighbours first observe seeds
        return int((camp.choices[weeks][:, nbrs] == 2).sum() - (ctrl.choices[weeks][:, nbrs] == 2).sum())

    assert neighbour_matcha_effect(False) == 0  # no channel from seeds to neighbours
    assert neighbour_matcha_effect(True) > 0

import numpy as np
import pytest

from halibut.decision import LogitChoiceModel, sample_options

from .conftest import make_context


def test_probabilities_sum_to_one_and_match_closed_form():
    ctx = make_context(n=1, preference=np.array([[0.5, 1.0]]))
    model = LogitChoiceModel(outside_utility=0.2, preference_weight=2.0, temperature=1.0)
    p = model.probabilities(ctx)[0]
    v = np.array([0.2, 1.0, 2.0])
    assert np.allclose(p, np.exp(v) / np.exp(v).sum())


def test_temperature_scales_utility():
    ctx = make_context(n=1, preference=np.array([[0.5, 1.0]]))
    p = LogitChoiceModel(temperature=0.5).probabilities(ctx)[0]
    v = np.array([0.0, 0.5, 1.0]) / 0.5
    assert np.allclose(p, np.exp(v) / np.exp(v).sum())


def test_no_purchase_has_defined_utility_and_is_always_feasible():
    # nothing affordable, nothing available: the outside option takes all mass
    ctx = make_context(n=2, budget=np.array([0.5, 100.0]),
                       available=np.array([[True, True], [False, False]]))
    p = LogitChoiceModel(outside_utility=-50.0).probabilities(ctx)
    assert np.allclose(p[:, 0], 1.0)
    v = LogitChoiceModel(outside_utility=-50.0).utilities(ctx)
    assert (v[:, 0] == -50.0).all()


def test_unaffordable_and_unavailable_products_never_chosen():
    n = 20_000
    rng = np.random.default_rng(3)
    budget = rng.uniform(0, 10, n)
    ctx = make_context(
        n=n, price=[2.0, 6.0], consumption=np.ones(n), budget=budget,
        available=np.column_stack([rng.random(n) < 0.5, np.ones(n, bool)]),
        preference=np.full((n, 2), 5.0),  # strongly wanted
    )
    model = LogitChoiceModel(preference_weight=1.0)
    choices = model.choose(ctx, rng.random(n))
    chosen = choices > 0
    spend = np.where(chosen, ctx.price[np.maximum(choices - 1, 0)], 0.0)
    assert (spend <= budget).all()
    assert not ((choices == 1) & ~ctx.available[:, 0]).any()
    assert not ((choices == 2) & (budget < 6.0)).any()
    # affordability boundary is inclusive: cost == budget is allowed
    edge = make_context(n=1, price=[5.0, 99.0], budget=np.array([5.0]))
    assert LogitChoiceModel().probabilities(edge)[0, 1] > 0


def test_affordability_uses_consumption_quantity():
    ctx = make_context(n=1, price=[3.0, 3.0], consumption=np.array([4.0]), budget=np.array([10.0]))
    assert np.allclose(LogitChoiceModel().probabilities(ctx)[0], [1, 0, 0])


def test_deterministic_limit_picks_argmax():
    ctx = make_context(n=3, preference=np.array([[0.1, 0.9], [0.9, 0.1], [-5.0, -5.0]]))
    model = LogitChoiceModel(temperature=0.0)
    assert model.choose(ctx, np.array([0.99, 0.0, 0.5])).tolist() == [2, 1, 0]


def test_higher_price_lowers_choice_probability():
    model = LogitChoiceModel()
    probs = []
    for price in (1.0, 2.0, 4.0):
        ctx = make_context(n=1, price=[1.0, price], price_sensitivity=np.array([2.0]),
                           budget=np.array([10.0]))
        probs.append(model.probabilities(ctx)[0, 2])
    assert probs[0] > probs[1] > probs[2]


def test_loyalty_and_switching_resistance():
    model = LogitChoiceModel()
    base = make_context(n=1, current=np.array([1]))
    loyal = make_context(n=1, current=np.array([1]), brand_loyalty=np.array([1.0]))
    resist = make_context(n=1, current=np.array([1]), switching_resistance=np.array([1.0]))
    v0, vl, vr = (model.utilities(c)[0] for c in (base, loyal, resist))
    assert np.allclose(vl - v0, [0, 1, 0])  # loyalty raises the current product only
    assert np.allclose(vr - v0, [0, 0, -1])  # resistance penalises switching, not exit
    # no current product => no switching cost
    fresh = make_context(n=1, current=np.array([0]), switching_resistance=np.array([1.0]))
    assert np.allclose(model.utilities(fresh)[0], v0)


def test_sampling_matches_probabilities():
    n = 200_000
    p = np.tile([0.2, 0.5, 0.3], (n, 1))
    choices = sample_options(p, np.random.default_rng(7).random(n))
    freq = np.bincount(choices, minlength=3) / n
    assert np.allclose(freq, [0.2, 0.5, 0.3], atol=0.005)


@pytest.mark.parametrize("u", [0.0, 0.5, 1 - 1e-16, np.nextafter(1.0, 0)])
def test_sampling_never_selects_zero_probability_option(u):
    p = np.array([[0.3, 0.7, 0.0], [0.0, 1.0, 0.0], [0.5, 0.0, 0.5]])
    c = sample_options(p, np.full(3, u))
    assert (p[np.arange(3), c] > 0).all()


def test_common_uniforms_couple_choices():
    n = 100_000
    u = np.random.default_rng(1).random(n)
    pa, pb = np.array([0.5, 0.4, 0.1]), np.array([0.45, 0.35, 0.2])
    a = sample_options(np.tile(pa, (n, 1)), u)
    b = sample_options(np.tile(pb, (n, 1)), u)
    assert (sample_options(np.tile(pa, (n, 1)), u) == a).all()
    bound = np.abs(np.cumsum(pa) - np.cumsum(pb)).sum()  # 0.15
    assert abs((a != b).mean() - bound) < 0.005

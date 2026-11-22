import numpy as np

from halibut.distributions import parse_distribution
from halibut.population import CONSUMER_ATTRIBUTES, apportion, generate_population, initial_products
from halibut.rng import stream


def test_apportion_exact():
    assert apportion(10, [1, 1, 1]).tolist() == [4, 3, 3]
    assert apportion(5000, [0.45, 0.40, 0.15]).tolist() == [2250, 2000, 750]
    assert apportion(7, [0.3, 0.7]).sum() == 7


def test_distribution_moments():
    rng = np.random.default_rng(0)
    beta = parse_distribution({"dist": "beta", "a": 2, "b": 6}, "x").sample(rng, 200_000)
    assert abs(beta.mean() - 0.25) < 0.005 and beta.min() >= 0 and beta.max() <= 1
    logn = parse_distribution({"dist": "lognormal", "median": 30, "sigma": 0.4}, "x").sample(rng, 200_000)
    assert abs(np.median(logn) - 30) < 0.5
    clipped = parse_distribution({"dist": "normal", "mean": 0, "sd": 1, "min": 0}, "x").sample(rng, 1000)
    assert clipped.min() == 0
    assert (parse_distribution(2.5, "x").sample(rng, 3) == 2.5).all()


def test_population_segments_and_ranges(cfg):
    pop = generate_population(cfg.population, cfg.product_ids, stream(1, "population"))
    assert pop.size == 400
    counts = np.bincount(pop.segment).tolist()
    assert counts == apportion(400, [s.weight for s in cfg.population.segments]).tolist()
    for a in CONSUMER_ATTRIBUTES:
        assert (getattr(pop, a) >= 0).all()
    assert pop.preference.shape == (400, 2)
    assert (pop.preference >= 0).all() and (pop.preference <= 1).all()
    # segment-specific distributions are respected: loyalists prefer coffee more
    loyal, curious = pop.segment == 0, pop.segment == 2
    assert pop.preference[loyal, 0].mean() > pop.preference[curious, 0].mean()
    assert pop.preference[loyal, 1].mean() < pop.preference[curious, 1].mean()


def test_initial_shares_exact_per_segment(cfg):
    pop = generate_population(cfg.population, cfg.product_ids, stream(1, "population"))
    current = initial_products(pop, cfg.population, cfg.product_ids, stream(1, "initial_state"))
    for s, seg in enumerate(cfg.population.segments):
        idx = pop.segment == s
        expected = apportion(idx.sum(), [seg.initial_product_shares.get(o, 0) for o in ("none", "coffee", "matcha")])
        assert np.bincount(current[idx], minlength=3).tolist() == expected.tolist()

import numpy as np

from halibut.engine import build_world, run_scenario
from halibut.experiment import run_experiment
from halibut.export import write_results


def _same_world(a, b):
    pa, pb = a.population, b.population
    return (
        all(np.array_equal(getattr(pa, f), getattr(pb, f)) for f in
            ("segment", "budget", "consumption", "price_sensitivity", "brand_loyalty",
             "social_susceptibility", "switching_resistance", "preference"))
        and np.array_equal(a.network.src, b.network.src)
        and np.array_equal(a.network.dst, b.network.dst)
        and np.array_equal(a.initial_current, b.initial_current)
    )


def test_same_seed_same_world_and_results(cfg):
    a, b = build_world(cfg, 11), build_world(cfg, 11)
    assert _same_world(a, b)
    for s in cfg.scenario_names:
        ra = run_scenario(cfg, a, cfg.scenario(s), trace=True)
        rb = run_scenario(cfg, b, cfg.scenario(s), trace=True)
        assert ra.metrics == rb.metrics and (ra.choices == rb.choices).all()


def test_different_seeds_differ(cfg):
    a, b = build_world(cfg, 11), build_world(cfg, 12)
    assert not _same_world(a, b)
    ra = run_scenario(cfg, a, cfg.scenario("control"))
    rb = run_scenario(cfg, b, cfg.scenario("control"))
    assert ra.metrics != rb.metrics


def test_running_a_scenario_does_not_mutate_world(cfg):
    world = build_world(cfg, 4)
    snapshot = build_world(cfg, 4)
    for s in cfg.scenario_names:
        run_scenario(cfg, world, cfg.scenario(s))
    assert _same_world(world, snapshot)
    # control after treatments equals control on a fresh world (common random numbers)
    assert run_scenario(cfg, world, cfg.scenario("control")).metrics == \
        run_scenario(cfg, snapshot, cfg.scenario("control")).metrics


def test_scenarios_share_initial_conditions(cfg):
    result = run_experiment(cfg, seeds=[5])
    week0 = {r["scenario"]: r for r in result.timeseries if r["week"] == 0}
    # every intervention in the example starts after week 0 => week 0 identical
    ref = {k: v for k, v in week0["control"].items() if k != "scenario"}
    for s, row in week0.items():
        assert {k: v for k, v in row.items() if k != "scenario"} == ref, s


def test_exported_files_byte_identical(cfg, tmp_path):
    files = ("timeseries.csv", "aggregate.csv", "segments.csv", "summary.json")
    p1 = write_results(run_experiment(cfg), tmp_path / "a")
    p2 = write_results(run_experiment(cfg), tmp_path / "b")
    assert p1.name == p2.name
    for f in files:
        assert (p1 / f).read_bytes() == (p2 / f).read_bytes(), f

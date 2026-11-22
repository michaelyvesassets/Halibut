import numpy as np
import pytest

from halibut.sensitivity import (
    ishigami, ishigami_indices, load_sensitivity, morris_design, morris_elementary_effects,
    morris_indices, noise_share, run_sensitivity, saltelli_design, sobol_g, sobol_g_indices,
    sobol_indices, write_sensitivity,
)

from .conftest import EXAMPLE


# --------------------------------------------------------------------------- Sobol benchmarks


def test_saltelli_design_structure():
    X = saltelli_design(3, 64, seed=1)
    assert X.shape == (64 * 5, 3)
    A, B = X[:64], X[64:128]
    for i in range(3):
        AB = X[128 + 64 * i: 128 + 64 * (i + 1)]
        others = [j for j in range(3) if j != i]
        assert np.array_equal(AB[:, i], B[:, i]) and np.array_equal(AB[:, others], A[:, others])
    with pytest.raises(ValueError):
        saltelli_design(3, 100, seed=1)


def test_sobol_recovers_ishigami():
    n, k = 2 ** 14, 3
    X = saltelli_design(k, n, seed=3)
    res = sobol_indices(ishigami(X), k, n, bootstrap=100)
    true = ishigami_indices()
    assert np.allclose(res["S1"][:, 0], true["S1"], atol=0.02)
    assert np.allclose(res["ST"][:, 0], true["ST"], atol=0.02)
    # bootstrap intervals cover the analytic values
    for name in ("S1", "ST"):
        lo, hi = res[f"{name}_ci95"][:, 0, 0], res[f"{name}_ci95"][:, 0, 1]
        assert np.all((lo - 0.01 <= true[name]) & (true[name] <= hi + 0.01))


def test_sobol_recovers_g_function():
    a = [0, 1, 4.5, 9, 99, 99]
    n, k = 2 ** 13, 6
    X = saltelli_design(k, n, seed=5)
    res = sobol_indices(sobol_g(X, a), k, n, bootstrap=50)
    true = sobol_g_indices(a)
    assert np.allclose(res["S1"][:, 0], true["S1"], atol=0.03)
    assert np.allclose(res["ST"][:, 0], true["ST"], atol=0.03)


def test_sobol_multi_output_and_constant_output():
    n, k = 256, 2
    X = saltelli_design(k, n, seed=0)
    y = np.column_stack([X[:, 0], np.ones(len(X))])
    res = sobol_indices(y, k, n, bootstrap=50)
    assert res["S1"][0, 0] == pytest.approx(1.0, abs=0.02) and abs(res["S1"][1, 0]) < 0.02
    assert np.isnan(res["S1"][:, 1]).all()  # zero variance -> undefined, never fabricated


# --------------------------------------------------------------------------- Morris benchmarks


def test_morris_design_is_one_at_a_time():
    k, r = 5, 10
    X = morris_design(k, r, 4, np.random.default_rng(0))
    assert X.shape == (r * (k + 1), k) and X.min() >= 0 and X.max() <= 1
    for t in range(r):
        steps = np.diff(X[t * (k + 1):(t + 1) * (k + 1)], axis=0)
        assert ((np.abs(steps) > 1e-12).sum(axis=1) == 1).all()  # one factor per step
        assert sorted(np.flatnonzero(np.abs(steps) > 1e-12) % k) == list(range(k))  # each once
        assert np.allclose(np.abs(steps).sum(axis=1), 2 / 3)


def test_morris_linear_function_exact():
    coef = np.array([3.0, -2.0, 0.0, 0.5])
    X = morris_design(4, 15, 4, np.random.default_rng(1))
    idx = morris_indices(X, X @ coef, 4, bootstrap=50)
    assert np.allclose(idx["mu"][:, 0], coef)
    assert np.allclose(idx["mu_star"][:, 0], np.abs(coef))
    assert np.allclose(idx["sigma"][:, 0], 0, atol=1e-12)


def test_morris_detects_interaction_and_ranks_g_function():
    X = morris_design(3, 30, 4, np.random.default_rng(2))
    idx = morris_indices(X, X[:, 0] * X[:, 1], 3, bootstrap=50)
    assert idx["sigma"][0, 0] > 0 and idx["sigma"][1, 0] > 0
    assert idx["mu_star"][2, 0] == 0
    a = [0, 1, 4.5, 9, 99, 99]
    X = morris_design(6, 200, 4, np.random.default_rng(3))
    mu_star = morris_indices(X, sobol_g(X, a), 6, bootstrap=50)["mu_star"][:, 0]
    assert list(np.argsort(-mu_star)[:4]) == [0, 1, 2, 3]  # same ordering as analytic S_T


def test_noise_share():
    rng = np.random.default_rng(0)
    signal = rng.normal(0, 1, 500)
    Y_seed = signal[:, None, None] + rng.normal(0, 1, (500, 4, 1))  # noise var 1, 4 seeds
    assert noise_share(Y_seed)[0] == pytest.approx(0.25 / 1.25, abs=0.05)


# --------------------------------------------------------------------------- simulation GSA


SPEC = """
schema_version = "1.0"
[sensitivity]
name = "tiny"
base_config = "{base}"
replicates = 2
base_seed = 3
method = "morris"
sample_seed = 11
bootstrap = 50
outputs = ["control.purchase_share_matcha", "effect.price_war.purchase_share_matcha",
           "transient.control.category_units"]
time_limit_minutes = 5
[sensitivity.morris]
trajectories = 3
[sensitivity.sobol]
n = 8
parameters = ["outside_utility", "matcha_price"]
[overrides]
"population.size" = 250
"experiment.weeks" = 14
"network.k" = 6
[[parameters]]
name = "outside_utility"
path = "decision.outside_utility"
range = [0.0, 2.0]
[[parameters]]
name = "matcha_price"
path = "products[matcha].price"
range = [4.0, 6.0]
[[parameters]]
name = "price_sensitivity_scale"
paths = ["population.segments[0].attributes.price_sensitivity.median",
         "population.segments[1].attributes.price_sensitivity.median"]
range = [0.5, 2.0]
scale = "log"
mode = "scale"
"""


@pytest.mark.parametrize("method", ["morris", "sobol"])
def test_simulation_gsa_end_to_end(tmp_path, method):
    p = tmp_path / "gsa.toml"
    p.write_text(SPEC.format(base=EXAMPLE))
    spec = load_sensitivity(p, method=method)
    run = run_sensitivity(spec, workers=1, cache_dir=tmp_path / "c")
    res = run["result"]
    n_points = 3 * 4 if method == "morris" else 8 * 4
    assert run["Y"].shape == (n_points, 3) and run["Y_seed"].shape == (n_points, 2, 3)
    for o in spec.outputs:
        assert set(res["per_output"][o]["ranking"]) == set(res["parameters"])
    path = write_sensitivity(spec, run, tmp_path / "out")
    assert (path / "design.csv").exists() and (path / "indices.json").exists()
    again = run_sensitivity(spec, workers=2, cache_dir=tmp_path / "c")
    assert np.array_equal(again["Y"], run["Y"])

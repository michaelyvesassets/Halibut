"""Calibrate the simulation to observed moments and produce forecasts.

Decision temperature is fixed at 1 (scale normalisation). Parameters are
searched in their unit cube with SciPy differential evolution (vectorised:
one generation = one parallel, cached batch) followed by a bounded
Nelder-Mead polish. The objective is
    L = sum_m ((sim_m - obs_m) / se_m)^2
over moments built only from data available at the origin.
"""

from __future__ import annotations

import math
import time

import numpy as np
from scipy import optimize

from ..batch import Runner, Task
from ..engine import build_world, run_scenario
from .builder import build_config
from .measurement import build_moments, month_index, simulated_moment


def evaluate_series_task(task) -> dict:
    cfg = task.cfg
    world = build_world(cfg, task.seed)
    rows = run_scenario(cfg, world, cfg.scenario("control"), collect_segments=False).metrics
    return {"seed": task.seed, "series": {m: [float(r[m]) for r in rows] for m in task.payload}}


class Simulator:
    def __init__(self, spec, covariates, workers=1, cache_dir=None):
        self.spec, self.covariates = spec, covariates
        self.runner = Runner(workers=workers, cache_dir=cache_dir)
        self.metrics = tuple(f"expected_share_{p}" for p in spec.product_ids)
        self.evaluations = 0

    def values(self, u) -> dict[str, float]:
        u = np.clip(np.asarray(u, dtype=float), 0.0, 1.0)
        return {fp.param.name: fp.param.value(float(x)) for fp, x in zip(self.spec.parameters, u)}

    def series(self, U, n_ticks: int, seeds) -> list[list[dict]]:
        """For each point: list (per seed) of {metric: per-tick values}."""
        cfgs = [build_config(self.spec, self.values(u), n_ticks, self.covariates, seeds) for u in U]
        tasks = [Task(c, s, ("control",), evaluator="series", payload=self.metrics) for c in cfgs for s in seeds]
        res = self.runner.map(tasks)
        self.evaluations += len(U)
        k = len(seeds)
        return [[r["series"] for r in res[i * k:(i + 1) * k]] for i in range(len(U))]


def mean_series(per_seed: list[dict]) -> dict[str, list[float]]:
    keys = per_seed[0].keys()
    return {k: list(np.mean([s[k] for s in per_seed], axis=0)) for k in keys}


def objective_values(spec, sims: list[list[dict]], moments) -> np.ndarray:
    out = []
    for per_seed in sims:
        ms = mean_series(per_seed)
        out.append(sum(((simulated_moment(ms, m, spec.start_period) - m.value) / m.se) ** 2 for m in moments))
    return np.array(out)


def calibrate(spec, obs, covariates, workers=1, cache_dir=None, log=lambda *_: None) -> dict:
    moments = build_moments(obs, spec)
    if not moments:
        raise ValueError("no moments available at this origin")
    last = sorted(obs)[-1]
    n_ticks = month_index(last, spec.start_period) + 1
    sim = Simulator(spec, covariates, workers, cache_dir)
    k = len(spec.parameters)
    t0 = time.perf_counter()

    def f_vec(X):
        U = np.clip(np.atleast_2d(X).T, 0, 1)
        return objective_values(spec, sim.series(U, n_ticks, spec.calib_seeds), moments)

    de = optimize.differential_evolution(f_vec, bounds=[(0.0, 1.0)] * k, popsize=spec.popsize,
                                         maxiter=spec.maxiter, seed=spec.optimizer_seed, polish=False,
                                         vectorized=True, updating="deferred", init="latinhypercube")
    best_u, best_L = np.clip(de.x, 0, 1), float(de.fun)
    log(f"    DE: L={best_L:.2f} after {de.nfev} evaluations ({time.perf_counter() - t0:.0f}s)")
    if spec.nm_maxfev:
        nm = optimize.minimize(lambda u: float(f_vec(np.clip(u, 0, 1)[:, None])[0]), best_u, method="Nelder-Mead",
                               bounds=[(0.0, 1.0)] * k, options={"maxfev": spec.nm_maxfev, "xatol": 1e-3, "fatol": 1e-2})
        if nm.fun < best_L:
            best_u, best_L = np.clip(nm.x, 0, 1), float(nm.fun)
        log(f"    Nelder-Mead: L={best_L:.2f} ({time.perf_counter() - t0:.0f}s)")
    at_bounds = [fp.param.name for fp, x in zip(spec.parameters, best_u) if x < 0.01 or x > 0.99]
    return {"unit": best_u.tolist(), "values": sim.values(best_u), "objective": best_L,
            "moments": len(moments), "evaluations": sim.evaluations, "n_ticks": n_ticks,
            "parameters_at_bounds": at_bounds, "seconds": time.perf_counter() - t0,
            "cache_hits": sim.runner.cache_hits}


def forecast(spec, fit: dict, obs, covariates, horizon: int, workers=1, cache_dir=None) -> dict:
    """Point forecast and intervals for the target product, months after the last observation."""
    last = sorted(obs)[-1]
    n_fit = month_index(last, spec.start_period) + 1
    sim = Simulator(spec, covariates, workers, cache_dir)
    per_seed = sim.series([np.array(fit["unit"])], n_fit + horizon, spec.forecast_seeds)[0]
    key = f"expected_share_{spec.target_product}"
    arr = np.array([s[key] for s in per_seed])  # seeds x ticks
    mean = arr.mean(axis=0)
    seed_sd = arr.std(axis=0, ddof=1) if len(arr) > 1 else np.zeros_like(mean)
    # in-sample residual SD over the last 36 observed months (same rule as curve baselines)
    periods = sorted(obs)[-36:]
    resid = [obs[p][spec.target_product] - mean[month_index(p, spec.start_period)] for p in periods]
    sigma = float(np.std(resid, ddof=1)) if len(resid) > 1 else 0.0
    point = mean[n_fit:n_fit + horizon]
    total_sd = np.sqrt(seed_sd[n_fit:n_fit + horizon] ** 2 + sigma ** 2)
    fitted = {p: float(mean[month_index(p, spec.start_period)]) for p in sorted(obs)}
    return {"point": point.tolist(), "sd": total_sd.tolist(), "seed_sd": seed_sd[n_fit:n_fit + horizon].tolist(),
            "residual_sd": sigma, "fitted": fitted, "in_sample_mae_36m": float(np.mean(np.abs(resid))) if resid else math.nan}

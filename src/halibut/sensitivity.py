"""Global sensitivity analysis: Morris screening and Sobol variance decomposition.

All designs live in the unit hypercube ``[0, 1]^k``; ``ParameterSpace`` maps
unit coordinates to parameter values (linear or log scale).

Morris elementary effects (Morris 1991; mu* from Campolongo et al. 2007)
    ``r`` random one-at-a-time trajectories on a ``p``-level grid with step
    ``Delta = p / (2 (p - 1))``; each trajectory has k+1 points and yields
    one elementary effect per factor, ``EE_i = (f(x + s e_i) - f(x)) / s``
    with ``s = +-Delta``. Reported: ``mu`` (mean EE), ``mu_star`` (mean |EE|),
    ``sigma`` (sd of EE; large => nonlinearity or interactions), bootstrap
    CI on ``mu_star``. EEs are in output units per unit of the *normalised*
    parameter range, so they are comparable across parameters.

Sobol indices (Saltelli et al. 2010, "Variance based sensitivity analysis of
model output", Table 2, estimators (b) and (f))
    Matrices A, B (N x k) are the two halves of a scrambled Sobol'
    low-discrepancy sample of dimension 2k; ``A_B^(i)`` is A with column i
    taken from B. Total cost ``N (k + 2)`` evaluations.
        V     = Var([f(A), f(B)])
        S_i   = mean(f(B) * (f(A_B^(i)) - f(A))) / V          first order
        S_Ti  = mean((f(A) - f(A_B^(i)))^2) / (2 V)           total (Jansen)
    Both estimators are defined for exactly this A/B/A_B design, so sampling
    scheme and estimators are compatible. CIs by bootstrap over the N rows.

Stochastic simulators: each design point is averaged over the same seeds
(common random numbers). ``noise_share`` estimates the fraction of output
variance across the design that is Monte Carlo noise rather than parameter
effect: ``mean_point_seed_variance / n_seeds / Var(design outputs)``.
"""

from __future__ import annotations

import hashlib
import json
import math
import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.stats import qmc

# --------------------------------------------------------------------------- Morris


def morris_design(k: int, trajectories: int, levels: int, rng: np.random.Generator) -> np.ndarray:
    """Return an array of shape (trajectories * (k + 1), k) of unit-cube points."""
    if levels < 2 or levels % 2:
        raise ValueError("Morris levels must be an even integer >= 2")
    delta = levels / (2 * (levels - 1))
    grid = np.arange(levels) / (levels - 1)
    points = []
    for _ in range(trajectories):
        x = rng.choice(grid, size=k)
        traj = [x.copy()]
        for i in rng.permutation(k):
            x[i] += delta if x[i] + delta <= 1 + 1e-12 else -delta
            x[i] = min(max(x[i], 0.0), 1.0)
            traj.append(x.copy())
        points.extend(traj)
    return np.array(points)


def morris_elementary_effects(X: np.ndarray, y: np.ndarray, k: int) -> np.ndarray:
    """EE array of shape (trajectories, k, n_outputs); ``y`` is (points,) or (points, m)."""
    y = y[:, None] if y.ndim == 1 else y
    r = len(X) // (k + 1)
    ee = np.empty((r, k, y.shape[1]))
    for t in range(r):
        base = t * (k + 1)
        for j in range(k):
            dx = X[base + j + 1] - X[base + j]
            i = int(np.flatnonzero(np.abs(dx) > 1e-12)[0])
            ee[t, i] = (y[base + j + 1] - y[base + j]) / dx[i]
    return ee


def morris_indices(X, y, k, bootstrap: int = 500, rng=None) -> dict:
    ee = morris_elementary_effects(X, np.asarray(y, dtype=float), k)
    rng = rng or np.random.default_rng(0)
    r = ee.shape[0]
    mu_star = np.abs(ee).mean(axis=0)
    boot = np.empty((bootstrap,) + mu_star.shape)
    for b in range(bootstrap):
        boot[b] = np.abs(ee[rng.integers(0, r, r)]).mean(axis=0)
    return {
        "mu": ee.mean(axis=0),
        "mu_star": mu_star,
        "sigma": ee.std(axis=0, ddof=1) if r > 1 else np.zeros_like(mu_star),
        "mu_star_ci95": np.stack([np.percentile(boot, 2.5, axis=0), np.percentile(boot, 97.5, axis=0)], -1),
        "trajectories": r,
    }


# --------------------------------------------------------------------------- Sobol


def saltelli_design(k: int, n: int, seed: int) -> np.ndarray:
    """Stacked design [A; B; A_B^(0); ...; A_B^(k-1)] of shape (n (k + 2), k)."""
    m = int(round(math.log2(n)))
    if 2 ** m != n:
        raise ValueError("Sobol base sample size n must be a power of 2")
    base = qmc.Sobol(d=2 * k, scramble=True, seed=seed).random_base2(m)
    A, B = base[:, :k], base[:, k:]
    blocks = [A, B]
    for i in range(k):
        AB = A.copy()
        AB[:, i] = B[:, i]
        blocks.append(AB)
    return np.vstack(blocks)


def _split(y: np.ndarray, k: int, n: int):
    return y[:n], y[n:2 * n], y[2 * n:].reshape(k, n, *y.shape[1:])


def _sobol_point(fA, fB, fAB):
    V = np.var(np.concatenate([fA, fB]), axis=0)
    with np.errstate(divide="ignore", invalid="ignore"):
        S1 = np.mean(fB[None] * (fAB - fA[None]), axis=1) / V
        ST = 0.5 * np.mean((fA[None] - fAB) ** 2, axis=1) / V
    return S1, ST, V


def sobol_indices(y, k: int, n: int, bootstrap: int = 500, rng=None) -> dict:
    y = np.asarray(y, dtype=float)
    y = y[:, None] if y.ndim == 1 else y
    fA, fB, fAB = _split(y, k, n)
    S1, ST, V = _sobol_point(fA, fB, fAB)
    rng = rng or np.random.default_rng(0)
    bs1 = np.empty((bootstrap,) + S1.shape)
    bst = np.empty_like(bs1)
    for b in range(bootstrap):
        idx = rng.integers(0, n, n)
        bs1[b], bst[b], _ = _sobol_point(fA[idx], fB[idx], fAB[:, idx])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN columns for zero-variance outputs
        ci = lambda a: np.stack([np.nanpercentile(a, 2.5, axis=0), np.nanpercentile(a, 97.5, axis=0)], -1)  # noqa: E731
        s1_ci, st_ci = ci(bs1), ci(bst)
    return {"S1": S1, "ST": ST, "S1_ci95": s1_ci, "ST_ci95": st_ci, "variance": V, "n": n}


# --------------------------------------------------------------------------- benchmarks


def ishigami(X, a: float = 7.0, b: float = 0.1):
    """Ishigami function on [0,1]^3 mapped to [-pi, pi]^3."""
    Z = -np.pi + 2 * np.pi * np.asarray(X)
    return np.sin(Z[:, 0]) + a * np.sin(Z[:, 1]) ** 2 + b * Z[:, 2] ** 4 * np.sin(Z[:, 0])


def ishigami_indices(a: float = 7.0, b: float = 0.1) -> dict:
    V1 = 0.5 * (1 + b * np.pi ** 4 / 5) ** 2
    V2 = a ** 2 / 8
    V13 = b ** 2 * np.pi ** 8 * (1 / 18 - 1 / 50)
    V = V1 + V2 + V13
    return {"S1": np.array([V1, V2, 0.0]) / V, "ST": np.array([V1 + V13, V2, V13]) / V}


def sobol_g(X, a):
    X, a = np.asarray(X), np.asarray(a, dtype=float)
    return np.prod((np.abs(4 * X - 2) + a) / (1 + a), axis=1)


def sobol_g_indices(a) -> dict:
    a = np.asarray(a, dtype=float)
    Vi = 1 / (3 * (1 + a) ** 2)
    V = np.prod(1 + Vi) - 1
    ST = np.array([Vi[i] * np.prod(np.delete(1 + Vi, i)) for i in range(len(a))]) / V
    return {"S1": Vi / V, "ST": ST}


# --------------------------------------------------------------------------- simulation GSA


@dataclass(frozen=True)
class SensitivitySpec:
    name: str
    description: str
    base: object  # specs.BaseSpec
    space: object  # params.ParameterSpace (full)
    method: str
    outputs: list[str]
    sample_seed: int
    morris_trajectories: int
    morris_levels: int
    sobol_n: int
    sobol_parameters: list[str]
    bootstrap: int
    extra_overrides: dict

    def active_space(self):
        from .params import ParameterSpace  # noqa: PLC0415
        if self.method == "sobol" and self.sobol_parameters:
            return ParameterSpace(tuple(p for p in self.space.parameters if p.name in self.sobol_parameters))
        return self.space

    def spec_hash(self) -> str:
        ident = {"name": self.name, "method": self.method, "outputs": self.outputs,
                 "seed": self.sample_seed, "seeds": self.base.seeds,
                 "base": self.base.raw, "params": [p.__dict__ for p in self.active_space().parameters],
                 "morris": [self.morris_trajectories, self.morris_levels],
                 "sobol": [self.sobol_n, self.sobol_parameters]}
        return hashlib.sha256(json.dumps(ident, sort_keys=True, default=list).encode()).hexdigest()


def load_sensitivity(path, method: str | None = None, extra_overrides: dict | None = None) -> SensitivitySpec:
    from .config import ConfigError  # noqa: PLC0415
    from .params import parse_parameters, set_paths  # noqa: PLC0415
    from .specs import BaseSpec, load_base, open_spec  # noqa: PLC0415
    from .config import parse_config  # noqa: PLC0415

    root = open_spec(path)
    t = root.table("sensitivity")
    name = t.string("name")
    description = t.raw("description", "") or ""
    base = load_base(root, t, path)
    if extra_overrides:
        raw = set_paths(base.raw, extra_overrides)
        base = BaseSpec(base.spec_path, base.base_config_path, raw, parse_config(raw), base.seeds,
                        base.time_limit_seconds, {**base.overrides, **extra_overrides})
    if len(base.seeds) < 2:
        raise ConfigError("sensitivity: at least 2 seeds per design point are required (noise estimate)")
    spec_method = t.string("method", "morris", choices=("morris", "sobol"))
    method = method or spec_method
    if method not in ("morris", "sobol"):
        raise ConfigError(f"unknown method {method!r}")
    outputs = t.raw("outputs")
    sample_seed = t.number("sample_seed", 0, integer=True, lo=0)
    bootstrap = t.number("bootstrap", 500, integer=True, lo=50)
    mt = t.table("morris", {})
    trajectories = mt.number("trajectories", 20, integer=True, lo=2)
    levels = mt.number("levels", 4, integer=True, lo=2)
    mt.done()
    st = t.table("sobol", {})
    sobol_n = st.number("n", 256, integer=True, lo=8)
    sobol_params = st.raw("parameters", []) or []
    sobol_reps = st.optional_number("replicates", integer=True, lo=2)
    st.done()
    t.done()
    space = parse_parameters(root.tables("parameters"))
    root.done()
    if method == "sobol" and sobol_reps is not None:
        if sobol_reps > len(base.seeds):
            raise ConfigError("sensitivity.sobol.replicates exceeds the configured seeds")
        base = BaseSpec(base.spec_path, base.base_config_path, base.raw, base.config,
                        base.seeds[:sobol_reps], base.time_limit_seconds, base.overrides)
    space.validate_against(base.raw)
    unknown = set(sobol_params) - set(space.names)
    if unknown:
        raise ConfigError(f"sensitivity.sobol.parameters: unknown {sorted(unknown)}")
    for o in outputs:
        parse_output(o, base.config)
    return SensitivitySpec(name, description, base, space, method, list(outputs), sample_seed,
                           trajectories, levels, sobol_n, list(sobol_params), bootstrap,
                           dict(extra_overrides or {}))


def parse_output(name: str, cfg) -> tuple:
    """Output grammar: ``control.<metric>``, ``effect.<scenario>.<metric>``,
    ``transient.control.<metric>`` (end window minus start window)."""
    from .config import CONTROL, ConfigError  # noqa: PLC0415
    parts = name.split(".")
    if parts[0] == "control" and len(parts) == 2:
        return ("level", CONTROL, parts[1])
    if parts[0] == "effect" and len(parts) == 3:
        cfg.scenario(parts[1])
        if parts[1] == CONTROL:
            raise ConfigError("effect.control is always 0")
        return ("effect", parts[1], parts[2])
    if parts[0] == "transient" and len(parts) == 3:
        cfg.scenario(parts[1])
        return ("transient", parts[1], parts[2])
    raise ConfigError(f"invalid output {name!r}")


def output_value(task_result: dict, spec_tuple: tuple) -> float:
    kind, scenario, metric = spec_tuple
    sc = task_result["scenarios"]
    if kind == "level":
        return sc[scenario]["end"][metric]
    if kind == "effect":
        return sc[scenario]["end"][metric] - sc["control"]["end"][metric]
    return sc[scenario]["end"][metric] - sc[scenario]["start"][metric]


def evaluate_design(spec: SensitivitySpec, space, U: np.ndarray, workers: int, cache_dir, progress=None):
    """Return (Y_mean (points, outputs), Y_seed (points, seeds, outputs))."""
    from .batch import Runner, Task  # noqa: PLC0415
    from .config import CONTROL, parse_config  # noqa: PLC0415

    out_specs = [parse_output(o, spec.base.config) for o in spec.outputs]
    scenarios = tuple(dict.fromkeys([CONTROL] + [s for _, s, _ in out_specs]))
    cfgs = [parse_config(space.apply(spec.base.raw, space.values(u))) for u in U]
    tasks = [Task(c, seed, scenarios) for c in cfgs for seed in spec.base.seeds]
    results = Runner(workers=workers, cache_dir=cache_dir).map(tasks, progress=progress)
    S = len(spec.base.seeds)
    Y_seed = np.array([[output_value(res, o) for o in out_specs] for res in results]).reshape(len(U), S, len(out_specs))
    return Y_seed.mean(axis=1), Y_seed


def noise_share(Y_seed: np.ndarray) -> np.ndarray:
    """Fraction of design-output variance attributable to seed noise, per output."""
    S = Y_seed.shape[1]
    noise_var = Y_seed.var(axis=1, ddof=1).mean(axis=0) / S
    total = Y_seed.mean(axis=1).var(axis=0, ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(total > 0, noise_var / total, np.nan)


def design_points(spec: SensitivitySpec) -> np.ndarray:
    space = spec.active_space()
    if spec.method == "morris":
        return morris_design(space.dimension, spec.morris_trajectories, spec.morris_levels,
                             np.random.default_rng(spec.sample_seed))
    return saltelli_design(space.dimension, spec.sobol_n, spec.sample_seed)


def estimate_sensitivity(spec: SensitivitySpec, workers: int) -> dict:
    from .batch import estimate_seconds, format_duration, seconds_per_scenario_run  # noqa: PLC0415
    n_points = len(design_points(spec))
    out_specs = [parse_output(o, spec.base.config) for o in spec.outputs]
    n_scen = len({"control", *[s for _, s, _ in out_specs]})
    runs = n_points * len(spec.base.seeds) * n_scen
    secs = estimate_seconds(seconds_per_scenario_run(spec.base.config), runs, workers)
    return {"design_points": n_points, "scenario_runs": runs, "seconds": secs,
            "human": format_duration(secs), "workers": workers}


def run_sensitivity(spec: SensitivitySpec, workers=1, cache_dir=None, confirmed=False, progress=None) -> dict:
    from .batch import check_budget  # noqa: PLC0415
    est = estimate_sensitivity(spec, workers)
    check_budget(est["seconds"], spec.base.time_limit_seconds, confirmed)
    space = spec.active_space()
    U = design_points(spec)
    Y, Y_seed = evaluate_design(spec, space, U, workers, cache_dir, progress)
    k = space.dimension
    rng = np.random.default_rng(spec.sample_seed + 1)
    ns = noise_share(Y_seed)
    result = {"method": spec.method, "parameters": space.names, "outputs": spec.outputs,
              "estimate": est, "noise_share": dict(zip(spec.outputs, map(_f, ns)))}
    per_output = {}
    if spec.method == "morris":
        idx = morris_indices(U, Y, k, spec.bootstrap, rng)
        for o_i, o in enumerate(spec.outputs):
            rows = {p: {"mu": _f(idx["mu"][p_i, o_i]), "mu_star": _f(idx["mu_star"][p_i, o_i]),
                        "sigma": _f(idx["sigma"][p_i, o_i]),
                        "mu_star_ci95": [_f(x) for x in idx["mu_star_ci95"][p_i, o_i]]}
                    for p_i, p in enumerate(space.names)}
            ranking = sorted(rows, key=lambda p: -rows[p]["mu_star"])
            per_output[o] = {"indices": rows, "ranking": ranking}
        result["trajectories"] = idx["trajectories"]
    else:
        idx = sobol_indices(Y, k, spec.sobol_n, spec.bootstrap, rng)
        for o_i, o in enumerate(spec.outputs):
            rows = {p: {"S1": _f(idx["S1"][p_i, o_i]), "ST": _f(idx["ST"][p_i, o_i]),
                        "S1_ci95": [_f(x) for x in idx["S1_ci95"][p_i, o_i]],
                        "ST_ci95": [_f(x) for x in idx["ST_ci95"][p_i, o_i]]}
                    for p_i, p in enumerate(space.names)}
            per_output[o] = {"indices": rows, "ranking": sorted(rows, key=lambda p: -(rows[p]["ST"] or 0)),
                             "sum_S1": _f(np.nansum(idx["S1"][:, o_i])),
                             "output_variance": _f(idx["variance"][o_i])}
        result["n"] = spec.sobol_n
    result["per_output"] = per_output
    return {"result": result, "U": U, "Y": Y, "Y_seed": Y_seed}


def _f(x):
    x = float(x)
    return None if math.isnan(x) or math.isinf(x) else x


def write_sensitivity(spec: SensitivitySpec, run: dict, out_dir) -> Path:
    from .export import synthetic_label, write_csv, write_json  # noqa: PLC0415
    space = spec.active_space()
    path = Path(out_dir) / f"{spec.name}_{spec.method}_{spec.spec_hash()[:12]}"
    path.mkdir(parents=True, exist_ok=True)
    rows = []
    for u, y in zip(run["U"], run["Y"]):
        vals = space.values(u)
        rows.append({**{f"u_{n}": float(x) for n, x in zip(space.names, u)},
                     **{n: float(v) for n, v in vals.items()},
                     **{o: float(v) for o, v in zip(spec.outputs, y)}})
    write_csv(path / "design.csv", rows)
    write_json(path / "indices.json", {**synthetic_label(), "analysis": spec.name, **run["result"]})
    write_json(path / "manifest.json", {
        **synthetic_label(), "analysis": spec.name, "description": spec.description,
        "spec_hash": spec.spec_hash(), "spec_file": str(spec.base.spec_path),
        "base_config": str(spec.base.base_config_path), "overrides": spec.base.overrides,
        "seeds_per_point": spec.base.seeds, "sample_seed": spec.sample_seed, "method": spec.method,
        "parameters": [{**p.__dict__, "paths": list(p.paths)} for p in space.parameters],
        "note": ("Indices are conditional on the stated parameter ranges and on the synthetic, "
                 "uncalibrated base model; they rank model sensitivities, not real-world drivers."),
    })
    return path

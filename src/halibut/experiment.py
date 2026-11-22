"""Run every scenario across every seed and summarise the results.

Comparisons against the control are *paired by seed*: for a given seed the
treatment and control share the same world and random numbers, so the per-seed
difference isolates the intervention effect.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .config import CONTROL, ExperimentConfig, config_hash
from .engine import build_world, run_scenario

# Two-sided 95% Student-t critical values by degrees of freedom.
_T975 = [12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228,
         2.201, 2.179, 2.160, 2.145, 2.131, 2.120, 2.110, 2.101, 2.093, 2.086,
         2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042]


@dataclass
class ExperimentResult:
    config: ExperimentConfig
    config_hash: str
    seeds: list[int]
    scenarios: list[str]
    timeseries: list[dict]  # one row per (scenario, seed, week)
    segment_timeseries: list[dict]
    runtime: dict = field(default_factory=dict)


def run_experiment(
    cfg: ExperimentConfig,
    scenarios: list[str] | None = None,
    seeds: list[int] | None = None,
    progress: Callable[[str], None] | None = None,
    collect_segments: bool = True,
) -> ExperimentResult:
    names = list(scenarios) if scenarios else cfg.scenario_names
    for name in names:
        cfg.scenario(name)  # raises on unknown names
    if CONTROL not in names:
        names.insert(0, CONTROL)  # comparisons always need the control
    names.sort(key=lambda s: s != CONTROL)
    seeds = list(seeds) if seeds is not None else list(cfg.seeds)

    rows, seg_rows = [], []
    world_seconds, scenario_seconds = 0.0, {s: 0.0 for s in names}
    started = time.perf_counter()
    for seed in seeds:
        t0 = time.perf_counter()
        world = build_world(cfg, seed)
        world_seconds += time.perf_counter() - t0
        for name in names:
            run = run_scenario(cfg, world, cfg.scenario(name), collect_segments=collect_segments)
            scenario_seconds[name] += run.runtime_seconds
            rows.extend({"scenario": name, "seed": seed, **m} for m in run.metrics)
            seg_rows.extend({"scenario": name, "seed": seed, **m} for m in run.segment_metrics)
        if progress:
            progress(f"seed {seed} done")
    total = time.perf_counter() - started

    agent_weeks = cfg.population.size * cfg.weeks * len(seeds) * len(names)
    runtime = {
        "total_seconds": total,
        "world_build_seconds": world_seconds,
        "scenario_seconds": scenario_seconds,
        "runs": len(seeds) * len(names),
        "agent_weeks": agent_weeks,
        "agent_weeks_per_second": agent_weeks / total if total > 0 else None,
    }
    return ExperimentResult(cfg, config_hash(cfg), seeds, names, rows, seg_rows, runtime)


# --------------------------------------------------------------------------- statistics


def mean_ci(values) -> dict:
    """Mean and 95% confidence-interval half-width across seeds (None if n < 2)."""
    x = np.asarray(values, dtype=float)
    n = len(x)
    mean = float(x.mean())
    if n < 2:
        return {"mean": mean, "ci95": None, "n": n}
    t = _T975[n - 2] if n - 1 <= len(_T975) else 1.96
    return {"mean": mean, "ci95": float(t * x.std(ddof=1) / math.sqrt(n)), "n": n}


def numeric_metric_names(rows: list[dict]) -> list[str]:
    skip = {"scenario", "seed", "week", "segment"}
    return [k for k, v in rows[0].items() if k not in skip and isinstance(v, (int, float))]


def aggregate_timeseries(rows: list[dict]) -> list[dict]:
    """Mean and CI across seeds for every (scenario, week)."""
    if not rows:
        return []
    names = numeric_metric_names(rows)
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        groups.setdefault((r["scenario"], r["week"]), []).append(r)
    out = []
    for (scenario, week), group in groups.items():
        row = {"scenario": scenario, "week": week, "n_seeds": len(group)}
        for m in names:
            stats = mean_ci([g[m] for g in group])
            row[f"{m}_mean"] = stats["mean"]
            row[f"{m}_ci95"] = stats["ci95"]
        out.append(row)
    return out


def summary_metrics(product_ids) -> list[str]:
    names = ["category_units", "category_revenue", "no_purchase_rate", "switching_rate",
             "non_adopter_share"]
    for p in product_ids:
        names += [f"units_{p}", f"revenue_{p}", f"purchase_share_{p}",
                  f"adoption_share_{p}", f"penetration_{p}"]
    return names


def decompose(units_before: dict[str, float], units_after: dict[str, float]) -> dict:
    """Split a change in product demand into displacement vs category size change.

    ``gain`` = total unit increase over products that grew, ``loss`` = total
    decrease over products that shrank. Demand that moved between products is
    ``displaced = min(gain, loss)``; any surplus loss is ``contraction`` (demand
    that left the category) and any surplus gain is ``expansion``.
    """
    deltas = {p: units_after[p] - units_before[p] for p in units_before}
    gain = sum(d for d in deltas.values() if d > 0)
    loss = -sum(d for d in deltas.values() if d < 0)
    return {
        "unit_deltas": deltas,
        "gain": gain,
        "loss": loss,
        "displaced": min(gain, loss),
        "contraction": max(loss - gain, 0.0),
        "expansion": max(gain - loss, 0.0),
        "net_category_change": gain - loss,
    }


def summarize(result: ExperimentResult) -> dict:
    """Per-scenario summary over the final ``summary_window_weeks`` weeks.

    ``vs_control``: paired per-seed differences against the control.
    ``vs_start``: change within the scenario from the first window to the last.
    """
    cfg = result.config
    w = cfg.summary_window_weeks
    products = cfg.product_ids
    metric_names = summary_metrics(products)
    first_weeks = set(range(w))
    last_weeks = set(range(cfg.weeks - w, cfg.weeks))

    # window averages per (scenario, seed)
    window: dict[tuple[str, int, str], dict[str, float]] = {}
    acc: dict[tuple[str, int, str], list[dict]] = {}
    for r in result.timeseries:
        if r["week"] in last_weeks:
            acc.setdefault((r["scenario"], r["seed"], "end"), []).append(r)
        if r["week"] in first_weeks:
            acc.setdefault((r["scenario"], r["seed"], "start"), []).append(r)
    for key, rows in acc.items():
        window[key] = {m: float(np.mean([x[m] for x in rows])) for m in metric_names}

    flow_names = [k for k in result.timeseries[0] if k.startswith("flow_")]
    cumulative_flows: dict[tuple[str, int], dict[str, float]] = {}
    for r in result.timeseries:
        f = cumulative_flows.setdefault((r["scenario"], r["seed"]), dict.fromkeys(flow_names, 0.0))
        for k in flow_names:
            f[k] += r[k]

    def across_seeds(fn) -> dict:
        values = [fn(seed) for seed in result.seeds]
        return {k: mean_ci([v[k] for v in values]) for k in values[0]}

    def decomposition(before_fn, after_fn) -> dict:
        per_seed = []
        for seed in result.seeds:
            before = {p: before_fn(seed)[f"units_{p}"] for p in products}
            after = {p: after_fn(seed)[f"units_{p}"] for p in products}
            d = decompose(before, after)
            flat = {k: v for k, v in d.items() if k != "unit_deltas"}
            flat.update({f"delta_units_{p}": v for p, v in d["unit_deltas"].items()})
            per_seed.append(flat)
        return {k: mean_ci([s[k] for s in per_seed]) for k in per_seed[0]}

    scenarios = {}
    for name in result.scenarios:
        end = lambda seed, n=name: window[(n, seed, "end")]  # noqa: E731
        start = lambda seed, n=name: window[(n, seed, "start")]  # noqa: E731
        control_end = lambda seed: window[(CONTROL, seed, "end")]  # noqa: E731
        entry = {
            "description": cfg.scenario(name).description,
            "final_window": across_seeds(end),
            "cumulative_flows": across_seeds(lambda seed, n=name: cumulative_flows[(n, seed)]),
            "vs_start": decomposition(start, end),
        }
        if name != CONTROL:
            entry["vs_control"] = {
                "metric_deltas": across_seeds(
                    lambda seed: {m: end(seed)[m] - control_end(seed)[m] for m in metric_names}
                ),
                "decomposition": decomposition(control_end, end),
            }
        scenarios[name] = entry

    return {
        "experiment": cfg.name,
        "config_hash": result.config_hash,
        "seeds": result.seeds,
        "weeks": cfg.weeks,
        "summary_window_weeks": w,
        "products": list(products),
        "scenarios": scenarios,
    }

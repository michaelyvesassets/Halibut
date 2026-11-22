"""Parallel, cached evaluation of (config, seed) tasks, and runtime estimates.

A task builds one world for a seed and runs a list of scenarios on it
(common random numbers). It returns compact window summaries rather than
full time series, so large designs stay small in memory and on disk.

Results are returned in task order whatever the number of workers; each
task is deterministic, so output is independent of parallelism (tested).
The on-disk cache is keyed by (config hash, seed, scenarios, options,
engine version, evaluator version), so interrupted analyses resume and
nothing is simulated twice.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable

import numpy as np

from .config import ExperimentConfig, config_hash
from .engine import build_world, run_scenario
from .version import ENGINE_VERSION

EVALUATOR_VERSION = "window-v1"


@dataclass(frozen=True)
class Task:
    cfg: ExperimentConfig
    seed: int
    scenarios: tuple[str, ...]
    full_availability: bool = False

    def key(self) -> str:
        ident = {
            "config_hash": config_hash(self.cfg), "seed": self.seed, "scenarios": list(self.scenarios),
            "full_availability": self.full_availability, "engine": ENGINE_VERSION,
            "evaluator": EVALUATOR_VERSION,
        }
        return hashlib.sha256(json.dumps(ident, sort_keys=True).encode()).hexdigest()


def _window_means(rows: list[dict], weeks: range) -> dict[str, float]:
    sel = [rows[w] for w in weeks]
    return {k: float(np.mean([r[k] for r in sel])) for k, v in rows[0].items()
            if k != "week" and isinstance(v, (int, float))}


def evaluate_task(task: Task) -> dict:
    """Run a task; return per-scenario start/end window means and cumulative flows."""
    cfg = task.cfg
    t0 = time.perf_counter()
    world = build_world(cfg, task.seed)
    w = cfg.summary_window_weeks
    out = {}
    for name in task.scenarios:
        rows = run_scenario(cfg, world, cfg.scenario(name), collect_segments=False,
                            full_availability=task.full_availability).metrics
        flows = {k: float(sum(r[k] for r in rows)) for k in rows[0] if k.startswith("flow_")}
        out[name] = {
            "start": _window_means(rows, range(0, w)),
            "end": _window_means(rows, range(cfg.weeks - w, cfg.weeks)),
            "cumulative_flows": flows,
        }
    return {"seed": task.seed, "scenarios": out, "seconds": time.perf_counter() - t0}


class Runner:
    def __init__(self, workers: int = 1, cache_dir: str | Path | None = None):
        self.workers = max(1, int(workers))
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.cache_hits = 0
        self.evaluated = 0

    def _cache_file(self, key: str) -> Path:
        return self.cache_dir / key[:2] / f"{key}.json"

    def map(self, tasks: list[Task], progress: Callable[[int, int], None] | None = None) -> list[dict]:
        results: list[dict | None] = [None] * len(tasks)
        todo = []
        for i, task in enumerate(tasks):
            if self.cache_dir:
                f = self._cache_file(task.key())
                if f.exists():
                    results[i] = json.loads(f.read_text())
                    self.cache_hits += 1
                    continue
            todo.append(i)

        def store(i, res):
            results[i] = res
            self.evaluated += 1
            if self.cache_dir:
                f = self._cache_file(tasks[i].key())
                f.parent.mkdir(parents=True, exist_ok=True)
                tmp = f.with_suffix(".tmp")
                tmp.write_text(json.dumps(res))
                os.replace(tmp, f)
            if progress:
                progress(self.evaluated, len(todo))

        if self.workers == 1 or len(todo) <= 1:
            for i in todo:
                store(i, evaluate_task(tasks[i]))
        else:
            chunk = max(1, len(todo) // (self.workers * 8))
            with ProcessPoolExecutor(max_workers=self.workers) as pool:
                for i, res in zip(todo, pool.map(evaluate_task, [tasks[i] for i in todo], chunksize=chunk)):
                    store(i, res)
        return results  # type: ignore[return-value]


# --------------------------------------------------------------------------- budgets


class BudgetExceeded(RuntimeError):
    pass


def seconds_per_scenario_run(cfg: ExperimentConfig, sample_weeks: int = 12) -> float:
    """Measure (not guess) the cost of one scenario run at this config's size.

    Runs the control for ``sample_weeks`` weeks and scales linearly in weeks
    (including warm-up weeks), plus the measured world-build time.
    """
    weeks = min(sample_weeks, cfg.weeks)
    probe = replace(cfg, weeks=weeks, warmup_weeks=0, summary_window_weeks=min(cfg.summary_window_weeks, weeks))
    t0 = time.perf_counter()
    world = build_world(probe, probe.seeds[0])
    t1 = time.perf_counter()
    run_scenario(probe, world, probe.scenario(probe.scenario_names[0]), collect_segments=False)
    t2 = time.perf_counter()
    per_week = (t2 - t1) / weeks
    return (t1 - t0) + per_week * (cfg.weeks + cfg.warmup_weeks)


def estimate_seconds(seconds_per_run: float, scenario_runs: int, workers: int) -> float:
    # Parallel efficiency on small machines is imperfect; assume 85%.
    eff = 1.0 if workers == 1 else 0.85
    return seconds_per_run * scenario_runs / (workers * eff)


def format_duration(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f}s"
    if seconds < 5400:
        return f"{seconds / 60:.1f} min"
    return f"{seconds / 3600:.1f} h"


def check_budget(estimate: float, limit_seconds: float, confirmed: bool) -> None:
    if estimate > limit_seconds and not confirmed:
        raise BudgetExceeded(
            f"estimated runtime {format_duration(estimate)} exceeds the "
            f"{format_duration(limit_seconds)} limit; rerun with --yes to proceed"
        )


def default_workers() -> int:
    return max(1, min(os.cpu_count() or 1, 8))

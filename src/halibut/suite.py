"""Experiment suites: separate intervention effects from model-assumption effects.

A suite runs a full factorial design, variants (assumption sets) x scenarios
(interventions, including control) x seeds, with common random numbers
within each (variant, seed). For an end-window outcome ``Y`` it reports:

- intervention effect under variant a:  tau_a = E_r[Y(a,s,r) - Y(a,control,r)]
- assumption effect on the baseline:    E_r[Y(a,control,r) - Y(ref,control,r)]
- interaction (assumption-dependence):  E_r[tau_(a,r) - tau_(ref,r)]
- a balanced ANOVA decomposition of Y into variant, scenario, variant x
  scenario, seed (block) and residual sums of squares
- robustness of each conclusion across variants:
    robust                same-sign effect whose 95% CI excludes 0 in every variant
    reversed              significantly positive in some variant, negative in another
    assumption_dependent  anything else (significant in some variants only, etc.)
- treatment-ranking agreement with the reference variant (Kendall's tau)

All comparisons are paired by seed. Results are synthetic model outputs.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy import stats

from .batch import Runner, Task, check_budget, estimate_seconds, format_duration, seconds_per_scenario_run
from .config import CONTROL, ConfigError, ExperimentConfig, config_hash, parse_config
from .experiment import mean_ci
from .params import parse_overrides, set_paths
from .specs import BaseSpec, load_base, open_spec


@dataclass(frozen=True)
class Variant:
    name: str
    description: str
    overrides: dict
    config: ExperimentConfig


@dataclass(frozen=True)
class SuiteSpec:
    name: str
    description: str
    base: BaseSpec
    variants: list[Variant]
    reference: str
    scenarios: list[str]
    metrics: list[str]

    @property
    def seeds(self) -> list[int]:
        return self.base.seeds

    def spec_hash(self) -> str:
        ident = {"name": self.name, "seeds": self.seeds, "reference": self.reference,
                 "scenarios": self.scenarios, "metrics": self.metrics,
                 "variants": {v.name: config_hash(v.config) for v in self.variants}}
        return hashlib.sha256(json.dumps(ident, sort_keys=True).encode()).hexdigest()


def load_suite(path: str | Path) -> SuiteSpec:
    root = open_spec(path)
    t = root.table("suite")
    name = t.string("name")
    description = t.raw("description", "") or ""
    base = load_base(root, t, path)
    scenarios = t.raw("scenarios", None) or base.config.scenario_names
    for s in scenarios:
        base.config.scenario(s)
    if CONTROL not in scenarios:
        scenarios = [CONTROL] + list(scenarios)
    metrics = t.raw("metrics", None) or [f"purchase_share_{p}" for p in base.config.product_ids] + [
        "category_units", "no_purchase_rate"]
    variants = []
    for vt in root.tables("variants"):
        vname = vt.string("name")
        overrides = parse_overrides(vt.table("overrides") if "overrides" in vt.data else None)
        vt.used.add("overrides")
        vdesc = vt.raw("description", "") or ""
        vt.done()
        try:
            cfg = parse_config(set_paths(base.raw, overrides))
        except ConfigError as exc:
            raise ConfigError(f"variant {vname!r}: {exc}") from exc
        variants.append(Variant(vname, vdesc, overrides, cfg))
    if not variants:
        raise ConfigError("variants: at least one variant is required")
    names = [v.name for v in variants]
    if len(set(names)) != len(names):
        raise ConfigError("variants: names must be unique")
    reference = t.string("reference_variant", names[0], choices=names)
    for m in metrics:
        if m not in _known_metrics(variants[0].config):
            raise ConfigError(f"suite.metrics: unknown metric {m!r}")
    t.done()
    root.done()
    return SuiteSpec(name, description, base, variants, reference, list(scenarios), list(metrics))


def _known_metrics(cfg: ExperimentConfig) -> set[str]:
    from .engine import build_world, run_scenario  # noqa: PLC0415
    import dataclasses  # noqa: PLC0415
    tiny = dataclasses.replace(cfg, weeks=1, warmup_weeks=0, summary_window_weeks=1,
                               population=dataclasses.replace(cfg.population, size=max(cfg.network.k + 1, 20)))
    row = run_scenario(tiny, build_world(tiny, 0), tiny.scenario(CONTROL), collect_segments=False).metrics[0]
    return {k for k, v in row.items() if isinstance(v, (int, float)) and k != "week"}


def estimate_suite(spec: SuiteSpec, workers: int) -> dict:
    per_variant = {v.name: seconds_per_scenario_run(v.config) for v in spec.variants}
    runs_per_variant = len(spec.seeds) * len(spec.scenarios)
    total = sum(estimate_seconds(s, runs_per_variant, workers) for s in per_variant.values())
    return {"scenario_runs": runs_per_variant * len(spec.variants), "workers": workers,
            "seconds": total, "human": format_duration(total), "seconds_per_run": per_variant}


def run_suite(spec: SuiteSpec, workers: int = 1, cache_dir=None, confirmed: bool = False,
              progress=None) -> dict:
    est = estimate_suite(spec, workers)
    check_budget(est["seconds"], spec.base.time_limit_seconds, confirmed)
    tasks = [Task(v.config, seed, tuple(spec.scenarios)) for v in spec.variants for seed in spec.seeds]
    runner = Runner(workers=workers, cache_dir=cache_dir)
    results = runner.map(tasks, progress=progress)
    # Y[variant][scenario][metric] -> array over seeds (seed order = spec.seeds)
    Y: dict = {}
    for task, res in zip(tasks, results):
        vname = next(v.name for v in spec.variants if v.config is task.cfg)
        for s in spec.scenarios:
            for m in spec.metrics:
                Y.setdefault(vname, {}).setdefault(s, {}).setdefault(m, []).append(res["scenarios"][s]["end"][m])
    Y = {v: {s: {m: np.array(x) for m, x in ms.items()} for s, ms in ss.items()} for v, ss in Y.items()}
    analysis = analyse(Y, spec)
    analysis["runtime"] = {"estimate": est, "cache_hits": runner.cache_hits, "evaluated": runner.evaluated,
                           "simulated_seconds": float(sum(r["seconds"] for r in results))}
    return {"Y": Y, "analysis": analysis}


# --------------------------------------------------------------------------- analysis


def _sig(ci: dict) -> int:
    """+1 / -1 if the 95% CI excludes zero on that side, else 0."""
    if ci["ci95"] is None:
        return 0
    if ci["mean"] - ci["ci95"] > 0:
        return 1
    if ci["mean"] + ci["ci95"] < 0:
        return -1
    return 0


def classify(effects: list[dict]) -> str:
    signs = [_sig(e) for e in effects]
    if all(s == 1 for s in signs) or all(s == -1 for s in signs):
        return "robust"
    if 1 in signs and -1 in signs:
        return "reversed"
    return "assumption_dependent"


def anova_decomposition(cube: np.ndarray) -> dict:
    """Balanced two-way layout with blocking: cube[a, s, r] (variant, scenario, seed).

    Returns sums of squares and their shares of the total. The residual pools
    seed x variant and seed x scenario interactions (Monte Carlo noise).
    """
    A, S, R = cube.shape
    g = cube.mean()
    ya = cube.mean(axis=(1, 2))
    ys = cube.mean(axis=(0, 2))
    yr = cube.mean(axis=(0, 1))
    yas = cube.mean(axis=2)
    ss_total = float(((cube - g) ** 2).sum())
    ss = {
        "variant": float(S * R * ((ya - g) ** 2).sum()),
        "scenario": float(A * R * ((ys - g) ** 2).sum()),
        "variant_x_scenario": float(R * ((yas - ya[:, None] - ys[None, :] + g) ** 2).sum()),
        "seed": float(A * S * ((yr - g) ** 2).sum()),
    }
    ss["residual"] = max(ss_total - sum(ss.values()), 0.0)
    return {"sum_of_squares": ss, "total": ss_total,
            "share": {k: (v / ss_total if ss_total > 0 else 0.0) for k, v in ss.items()}}


def analyse(Y: dict, spec: SuiteSpec) -> dict:
    ref = spec.reference
    variants = [v.name for v in spec.variants]
    treatments = [s for s in spec.scenarios if s != CONTROL]
    out: dict = {"reference_variant": ref, "variants": {}, "conclusions": {}, "anova": {}, "rankings": {}}

    for v in variants:
        entry = {"levels": {}, "effects": {}, "assumption_effect_on_control": {}, "interaction_vs_reference": {}}
        for s in spec.scenarios:
            entry["levels"][s] = {m: mean_ci(Y[v][s][m]) for m in spec.metrics}
        for m in spec.metrics:
            entry["assumption_effect_on_control"][m] = mean_ci(Y[v][CONTROL][m] - Y[ref][CONTROL][m])
        for s in treatments:
            entry["effects"][s] = {m: mean_ci(Y[v][s][m] - Y[v][CONTROL][m]) for m in spec.metrics}
            entry["interaction_vs_reference"][s] = {
                m: mean_ci((Y[v][s][m] - Y[v][CONTROL][m]) - (Y[ref][s][m] - Y[ref][CONTROL][m]))
                for m in spec.metrics}
        out["variants"][v] = entry

    for s in treatments:
        out["conclusions"][s] = {}
        for m in spec.metrics:
            effects = [out["variants"][v]["effects"][s][m] for v in variants]
            means = [e["mean"] for e in effects]
            out["conclusions"][s][m] = {
                "classification": classify(effects),
                "effect_by_variant": {v: e for v, e in zip(variants, effects)},
                "min_effect": float(min(means)), "max_effect": float(max(means)),
            }

    for m in spec.metrics:
        cube = np.array([[Y[v][s][m] for s in spec.scenarios] for v in variants])
        out["anova"][m] = anova_decomposition(cube)
        ref_means = [out["variants"][ref]["effects"][s][m]["mean"] for s in treatments]
        ranks = {}
        for v in variants:
            means = [out["variants"][v]["effects"][s][m]["mean"] for s in treatments]
            order = [treatments[i] for i in np.argsort(means)[::-1]]
            tau = float(stats.kendalltau(ref_means, means).statistic) if len(treatments) > 1 else 1.0
            ranks[v] = {"order": order, "kendall_tau_vs_reference": tau}
        out["rankings"][m] = {"by_variant": ranks,
                              "identical_in_all_variants": len({tuple(r["order"]) for r in ranks.values()}) == 1}
    return out


def write_suite(spec: SuiteSpec, result: dict, out_dir: str | Path) -> Path:
    from .export import synthetic_label, write_csv, write_json  # noqa: PLC0415
    path = Path(out_dir) / f"{spec.name}_{spec.spec_hash()[:12]}"
    path.mkdir(parents=True, exist_ok=True)
    rows = []
    for v, ss in result["Y"].items():
        for s, ms in ss.items():
            for i, seed in enumerate(spec.seeds):
                rows.append({"variant": v, "scenario": s, "seed": seed, **{m: float(ms[m][i]) for m in spec.metrics}})
    write_csv(path / "end_window.csv", rows)
    write_json(path / "comparison.json", {**synthetic_label(), "suite": spec.name, **result["analysis"]})
    write_json(path / "manifest.json", {
        **synthetic_label(),
        "suite": spec.name, "description": spec.description, "spec_hash": spec.spec_hash(),
        "spec_file": str(spec.base.spec_path), "base_config": str(spec.base.base_config_path),
        "base_overrides": spec.base.overrides, "seeds": spec.seeds, "scenarios": spec.scenarios,
        "metrics": spec.metrics, "reference_variant": spec.reference,
        "variants": {v.name: {"description": v.description, "overrides": v.overrides,
                              "config_hash": config_hash(v.config), "config": v.config.to_dict()}
                     for v in spec.variants},
    })
    return path

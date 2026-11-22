"""Run diagnostics: accounting invariants and behavioural checks.

Each check returns a ``Check`` with status:

- ``fail``: an accounting invariant is violated (a bug, never expected).
- ``warn``: the run is valid, but a property may undermine interpretation
  (e.g. the control is still trending, so effects include a transient).
- ``info``: descriptive diagnostics (switching churn, stock-outs, causes).
- ``pass``: invariant holds.

Expensive counterfactual diagnostics (stock-out counterfactual, population
size convergence) are separate functions that run extra simulations.
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import asdict, dataclass, field

import numpy as np

from .config import CONTROL, ExperimentConfig
from .experiment import ExperimentResult, mean_ci, run_experiment, summarize
from .interventions import SeedAdopters
from .metrics import NO_PURCHASE_CAUSES

# Defaults for warn thresholds; documented in docs/model.md.
TRANSIENT_SHARE_TOLERANCE = 0.01  # absolute change in purchase share, first vs last window
TRANSIENT_UNITS_TOLERANCE = 0.02  # relative change in category units
TARGET_RELATIVE_PRECISION = 0.10  # CI half-width as a fraction of |effect|


@dataclass
class Check:
    name: str
    status: str
    message: str
    details: dict = field(default_factory=dict)


def _close(a, b, rel=1e-9, abs_=1e-9) -> bool:
    return math.isclose(a, b, rel_tol=rel, abs_tol=abs_)


# --------------------------------------------------------------------------- accounting


def accounting_checks(result: ExperimentResult) -> list[Check]:
    cfg = result.config
    products = cfg.product_ids
    options = ("none",) + products
    seeding_weeks = {
        s: {iv.week for iv in cfg.scenario(s).interventions if isinstance(iv, SeedAdopters)}
        for s in result.scenarios
    }
    violations: dict[str, list] = {k: [] for k in (
        "purchase_shares_sum_to_one", "revenue_shares_sum_to_one", "adoption_shares_sum_to_one",
        "units_add_up", "revenue_equals_price_times_units", "purchasers_add_up",
        "flows_conserve_population", "flows_match_choices", "flows_continuous_week_to_week",
        "no_purchase_causes_partition", "budget_constraint",
    )}
    rows = sorted(result.timeseries, key=lambda r: (r["scenario"], r["seed"], r["week"]))
    prev = None
    for r in rows:
        key = (r["scenario"], r["seed"], r["week"])
        n = r["consumers"]
        if r["category_units"] > 0:
            if not _close(sum(r[f"purchase_share_{p}"] for p in products), 1.0):
                violations["purchase_shares_sum_to_one"].append(key)
        if r["category_revenue"] > 0:
            if not _close(sum(r[f"revenue_share_{p}"] for p in products), 1.0):
                violations["revenue_shares_sum_to_one"].append(key)
        if not _close(sum(r[f"adoption_share_{p}"] for p in products) + r["non_adopter_share"], 1.0):
            violations["adoption_shares_sum_to_one"].append(key)
        if not _close(sum(r[f"units_{p}"] for p in products), r["category_units"]):
            violations["units_add_up"].append(key)
        if not all(_close(r[f"revenue_{p}"], r[f"price_{p}"] * r[f"units_{p}"]) for p in products):
            violations["revenue_equals_price_times_units"].append(key)
        if sum(r[f"buyers_{p}"] for p in products) != r["purchasers"]:
            violations["purchasers_add_up"].append(key)
        flows = {(a, b): r[f"flow_{a}_to_{b}"] for a in options for b in options}
        if sum(flows.values()) != n:
            violations["flows_conserve_population"].append(key)
        into = {b: sum(flows[a, b] for a in options) for b in options}
        if into["none"] != n - r["purchasers"] or any(into[p] != r[f"buyers_{p}"] for p in products):
            violations["flows_match_choices"].append(key)
        same_run = prev is not None and prev["scenario"] == r["scenario"] and prev["seed"] == r["seed"]
        if same_run and r["week"] not in seeding_weeks[r["scenario"]]:
            out_now = {a: sum(flows[a, b] for b in options) for a in options}
            into_prev = {b: sum(prev[f"flow_{a}_to_{b}"] for a in options) for b in options}
            if out_now != into_prev:
                violations["flows_continuous_week_to_week"].append(key)
        if "np_voluntary" in r:
            if sum(r[c] for c in NO_PURCHASE_CAUSES) != n - r["purchasers"]:
                violations["no_purchase_causes_partition"].append(key)
        if r["max_budget_utilisation"] > 1.0 + 1e-12:
            violations["budget_constraint"].append(key)
        prev = r

    checks = []
    for name, bad in violations.items():
        if bad:
            checks.append(Check(name, "fail", f"{len(bad)} rows violate {name}",
                                {"examples": [list(b) for b in bad[:5]]}))
        else:
            checks.append(Check(name, "pass", f"{len(rows)} rows checked"))
    return checks


# --------------------------------------------------------------------------- behaviour


def _by_run(rows):
    runs: dict[tuple, list[dict]] = {}
    for r in rows:
        runs.setdefault((r["scenario"], r["seed"]), []).append(r)
    return runs


def switching_checks(result: ExperimentResult) -> list[Check]:
    """Gross vs net switching between each pair of products.

    ``churn_ratio = (a->b + b->a) / |a->b - b->a|``. A large ratio means most
    switching cancels out: consumers move back and forth rather than migrate.
    """
    products = result.config.product_ids
    runs = _by_run(result.timeseries)
    details = {}
    for scenario in result.scenarios:
        per_pair = {}
        for i, a in enumerate(products):
            for b in products[i + 1:]:
                gross, net = [], []
                for seed in result.seeds:
                    rows = runs[(scenario, seed)]
                    ab = sum(r[f"flow_{a}_to_{b}"] for r in rows)
                    ba = sum(r[f"flow_{b}_to_{a}"] for r in rows)
                    gross.append(ab + ba)
                    net.append(ab - ba)
                g, nt = float(np.mean(gross)), float(np.mean(net))
                per_pair[f"{a}<->{b}"] = {
                    "gross_switches": g, f"net_{a}_to_{b}": nt,
                    "churn_ratio": g / abs(nt) if nt else None,
                }
        mean_rate = float(np.mean([r["switching_rate"] for r in result.timeseries if r["scenario"] == scenario]))
        details[scenario] = {"mean_weekly_switching_rate": mean_rate, "pairs": per_pair}
    return [Check("switching_churn", "info", "gross vs net product switching per scenario", details)]


def stockout_checks(result: ExperimentResult) -> list[Check]:
    if "np_voluntary" not in result.timeseries[0]:
        return [Check("stockouts", "info", "feasibility metrics unavailable (engine < 0.2 results)")]
    details = {}
    for scenario in result.scenarios:
        rows = [r for r in result.timeseries if r["scenario"] == scenario]
        n = rows[0]["consumers"]
        non_buyers = sum(r["consumers"] - r["purchasers"] for r in rows)
        causes = {c: sum(r[c] for r in rows) for c in NO_PURCHASE_CAUSES}
        details[scenario] = {
            "mean_weekly_current_out_of_stock_rate": float(np.mean([r["current_out_of_stock"] for r in rows])) / n,
            "mean_weekly_forced_substitution_rate": float(np.mean([r["forced_substitution"] for r in rows])) / n,
            "no_purchase_cause_shares": {c: (v / non_buyers if non_buyers else 0.0) for c, v in causes.items()},
            "mean_weekly_no_purchase_rate": non_buyers / (n * len(rows)),
        }
    return [Check("stockouts_and_no_purchase_causes", "info",
                  "stock-out exposure and mutually exclusive no-purchase causes", details)]


def transient_check(result: ExperimentResult,
                    share_tol: float = TRANSIENT_SHARE_TOLERANCE,
                    units_tol: float = TRANSIENT_UNITS_TOLERANCE) -> Check:
    """Is the control still changing between its first and last window?

    If so, scenario-vs-start comparisons mix intervention effects with the
    model relaxing from its initial conditions; paired comparisons against the
    control remain valid, but warm-up should be considered.
    """
    cfg = result.config
    s = summarize(result)["scenarios"][CONTROL]["vs_start"]
    w = cfg.summary_window_weeks
    first = {}
    for r in result.timeseries:
        if r["scenario"] == CONTROL and r["week"] < w:
            first.setdefault(r["seed"], []).append(r)
    last = {}
    for r in result.timeseries:
        if r["scenario"] == CONTROL and r["week"] >= cfg.weeks - w:
            last.setdefault(r["seed"], []).append(r)
    share_deltas, unit_rel = {}, []
    for p in cfg.product_ids:
        d = [np.mean([x[f"purchase_share_{p}"] for x in last[sd]]) - np.mean([x[f"purchase_share_{p}"] for x in first[sd]])
             for sd in result.seeds]
        share_deltas[p] = mean_ci(d)
    for sd in result.seeds:
        a = np.mean([x["category_units"] for x in first[sd]])
        b = np.mean([x["category_units"] for x in last[sd]])
        unit_rel.append((b - a) / a if a else 0.0)
    units = mean_ci(unit_rel)
    trending = any(abs(v["mean"]) > share_tol for v in share_deltas.values()) or abs(units["mean"]) > units_tol
    msg = ("control is still trending: changes vs week 0 include a model transient; "
           "consider warm-up" if trending else "control is approximately stationary")
    return Check("control_transient", "warn" if trending else "pass", msg, {
        "warmup_weeks": cfg.warmup_weeks,
        "purchase_share_change": share_deltas,
        "relative_category_units_change": units,
        "thresholds": {"share": share_tol, "relative_units": units_tol},
        "decomposition_vs_start": s,
    })


def precision_check(result: ExperimentResult, target: float = TARGET_RELATIVE_PRECISION) -> Check:
    """Monte Carlo adequacy of paired treatment effects (final window).

    Recommended seeds n_req = ceil(n * (ci95 / (target * |effect|))^2), i.e.
    the seeds needed for the 95% CI half-width to fall to ``target`` x |effect|.
    """
    s = summarize(result)
    products = result.config.product_ids
    metrics = [f"purchase_share_{p}" for p in products] + ["category_units", "no_purchase_rate"]
    n = len(result.seeds)
    details, worst = {}, 0
    for name, sc in s["scenarios"].items():
        if name == CONTROL:
            continue
        entry = {}
        for m in metrics:
            d = sc["vs_control"]["metric_deltas"][m]
            if d["ci95"] is None or d["mean"] == 0:
                entry[m] = {**d, "recommended_seeds": None}
                continue
            req = math.ceil(n * (d["ci95"] / (target * abs(d["mean"]))) ** 2)
            entry[m] = {**d, "significant": abs(d["mean"]) > d["ci95"], "recommended_seeds": req}
            worst = max(worst, req)
        details[name] = entry
    if n < 2:
        return Check("monte_carlo_precision", "warn", "fewer than 2 seeds: no confidence intervals", details)
    return Check("monte_carlo_precision", "info",
                 f"{n} seeds; up to {worst} needed for {target:.0%} relative precision on every effect",
                 details)


def run_diagnostics(result: ExperimentResult) -> list[Check]:
    checks = accounting_checks(result)
    checks += switching_checks(result)
    checks += stockout_checks(result)
    checks.append(transient_check(result))
    checks.append(precision_check(result))
    return checks


def diagnostics_report(checks: list[Check]) -> dict:
    counts = {s: sum(c.status == s for c in checks) for s in ("fail", "warn", "info", "pass")}
    return {"status_counts": counts, "checks": [asdict(c) for c in checks]}


# --------------------------------------------------------------------------- counterfactuals


def stockout_counterfactual(cfg: ExperimentConfig, seeds=None, scenarios=None) -> dict:
    """Compare each scenario with a paired copy in which nothing is ever out of stock.

    Reports how much of the category contraction (vs control) and of the
    no-purchase rate is attributable to stock-outs. Availability interventions
    become no-ops in the counterfactual, which the report notes.
    """
    from .engine import build_world, run_scenario

    seeds = list(seeds if seeds is not None else cfg.seeds)
    names = list(scenarios or cfg.scenario_names)
    if CONTROL not in names:
        names.insert(0, CONTROL)
    w = cfg.summary_window_weeks
    window = lambda rows, m: float(np.mean([r[m] for r in rows[-w:]]))  # noqa: E731
    products = cfg.product_ids
    from .experiment import decompose

    per = {mode: {s: [] for s in names} for mode in ("actual", "full_availability")}
    for seed in seeds:
        world = build_world(cfg, seed)
        for mode in per:
            for s in names:
                rows = run_scenario(cfg, world, cfg.scenario(s), collect_segments=False,
                                    full_availability=(mode == "full_availability")).metrics
                per[mode][s].append({m: window(rows, m) for m in
                                     ["category_units", "no_purchase_rate"] + [f"units_{p}" for p in products]})
    out = {}
    for s in names:
        entry = {}
        for mode in per:
            rows = per[mode][s]
            entry[mode] = {m: mean_ci([r[m] for r in rows]) for m in rows[0]}
            if s != CONTROL:
                decomps = [decompose({p: c[f"units_{p}"] for p in products},
                                     {p: t[f"units_{p}"] for p in products})
                           for c, t in zip(per[mode][CONTROL], rows)]
                entry[mode]["vs_control"] = {k: mean_ci([d[k] for d in decomps])
                                             for k in ("displaced", "contraction", "expansion", "net_category_change")}
        has_avail = any(getattr(iv, "attribute", None) == "availability" for iv in cfg.scenario(s).interventions)
        entry["note"] = "availability interventions are no-ops under full availability" if has_avail else ""
        out[s] = entry
    return {"seeds": seeds, "summary_window_weeks": w, "scenarios": out}


def size_convergence(cfg: ExperimentConfig, sizes, seeds=None, scenarios=None) -> dict:
    """Final-window per-consumer metrics and paired effects at several population sizes.

    Shares and rates should agree across sizes (within Monte Carlo error) if
    smaller analysis populations are representative.
    """
    seeds = list(seeds if seeds is not None else cfg.seeds)
    names = list(scenarios or cfg.scenario_names)
    out = {}
    for size in sizes:
        sized = dataclasses.replace(cfg, population=dataclasses.replace(cfg.population, size=int(size)))
        res = run_experiment(sized, names, seeds)
        s = summarize(res)["scenarios"]
        entry = {}
        for name, sc in s.items():
            fw = sc["final_window"]
            entry[name] = {
                "units_per_consumer": {k: v / size if v is not None else None
                                       for k, v in fw["category_units"].items() if k in ("mean", "ci95")},
                **{f"purchase_share_{p}": fw[f"purchase_share_{p}"] for p in cfg.product_ids},
                "no_purchase_rate": fw["no_purchase_rate"],
            }
            if name != CONTROL:
                entry[name]["effect_purchase_share"] = {
                    p: sc["vs_control"]["metric_deltas"][f"purchase_share_{p}"] for p in cfg.product_ids}
        out[str(size)] = entry
    return {"seeds": seeds, "sizes": list(sizes), "results": out}

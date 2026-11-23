"""Reproducible learning-loop demonstration (no network, seconds of compute).

Component under study: ``forecasting.share_curve`` (the logistic substitution
baseline used in M20 backtests). Data: an empirical pilot's observed target
share, read through ``store.as_of(origin)`` at every origin (no leakage).

Loop:
1. baseline experiment     logistic fitted on all months since the pilot's ``annual_from``
2. controlled change       one parameter: fit window (months), proposed with rationale
3. evaluation              paired MAE per origin; development vs held-out origins
4. record                  outcome + lesson with a status set by the rule, never by preference
5. next experiment         a proposer retrieves prior lessons for this change and picks the
                           next candidate accordingly (ablation if not supported,
                           generalisation test if supported)
"""

from __future__ import annotations

import numpy as np

from ..empirical import baselines as bl
from ..empirical.measurement import month_at, month_index, observed_shares
from .evaluate import decide
from .lineage import diff, lineage
from .memory import LearningMemory

COMPONENT = "forecasting.share_curve"
TAGS = ["domain:share_forecasting", "component:forecasting.share_curve"]


def forecast_mae(store, spec, origins, horizon: int, window: int | None, product: str) -> dict[str, float]:
    """Per-origin MAE (percentage points) of the logistic curve fitted at each origin."""
    actual = {p: v[product] for p, v in observed_shares(store.latest(), spec).items()}
    out = {}
    for o in origins:
        obs = observed_shares(store.as_of(o), spec)
        periods = sorted(obs)
        last = periods[-1]
        y = np.array([obs[p][product] for p in periods])
        start = next(i for i, p in enumerate(periods) if int(p[:4]) >= spec.annual_from)
        if window:
            start = max(start, len(y) - window)
        t = np.arange(len(y), dtype=float) - start
        fc = bl.logistic(y[start:], horizon, t[start:])["point"]
        targets = [month_at(month_index(last, spec.start_period) + h, spec.start_period) for h in range(1, horizon + 1)]
        errs = [abs(f - actual[m]) for f, m in zip(fc, targets) if m in actual]
        out[o] = float(np.mean(errs) * 100)
    return out


def naive_mae(store, spec, origins, horizon: int, product: str) -> dict[str, float]:
    actual = {p: v[product] for p, v in observed_shares(store.latest(), spec).items()}
    out = {}
    for o in origins:
        obs = observed_shares(store.as_of(o), spec)
        last = sorted(obs)[-1]
        targets = [month_at(month_index(last, spec.start_period) + h, spec.start_period) for h in range(1, horizon + 1)]
        out[o] = float(np.mean([abs(obs[last][product] - actual[m]) for m in targets if m in actual]) * 100)
    return out


def run_change(memory: LearningMemory, store, spec, *, baseline_window, candidate_window, product, dev, holdout,
               horizon, proposed_by, rationale, baseline_id=None) -> dict:
    base_params = {"model": "logistic", "fit_window_months": baseline_window, "target": product, "horizon": horizon}
    cand_params = {**base_params, "fit_window_months": candidate_window}
    common = dict(datasets=["ssb_14020_private_cars"], extra={"pilot": spec.name, "dev_origins": dev, "holdout_origins": holdout})
    if baseline_id is None:
        baseline_id = memory.record_experiment("forecast_method", f"logistic baseline ({product})", "engine:learning_demo",
                                               lineage(store, config=base_params, parameters=base_params, **common))
        bdev = forecast_mae(store, spec, dev, horizon, baseline_window, product)
        bhold = forecast_mae(store, spec, holdout, horizon, baseline_window, product)
        memory.finish_experiment(baseline_id, {"mae_pp_dev": bdev, "mae_pp_holdout": bhold})
    base = memory.experiment(baseline_id)
    bdev, bhold = base["metrics"]["mae_pp_dev"], base["metrics"]["mae_pp_holdout"]
    change = diff(base_params, cand_params)
    cid = memory.record_experiment(
        "forecast_method", f"logistic fit window {candidate_window} months ({product})", proposed_by,
        lineage(store, config=cand_params, parameters=cand_params, **common), baseline_id=baseline_id,
        rationale=rationale, expected_improvement={"metric": "mae_pp", "direction": "decrease", "units": "dev and held-out origins"},
        change={k: list(v) for k, v in change.items()})
    try:
        cdev = forecast_mae(store, spec, dev, horizon, candidate_window, product)
        chold = forecast_mae(store, spec, holdout, horizon, candidate_window, product)
    except Exception as exc:  # failed experiments are kept as learning records
        memory.finish_experiment(cid, {"error": str(exc)}, status="failed")
        raise
    memory.finish_experiment(cid, {"mae_pp_dev": cdev, "mae_pp_holdout": chold})
    simple = naive_mae(store, spec, holdout, horizon, product)
    verdict = decide(bdev, cdev, bhold, chold, simple_holdout=simple)
    evid = memory.record_evaluation(cid, baseline_id, verdict["rule"], verdict["result"], verdict["decision"],
                                    {**verdict["details"], "reasons": verdict["reasons"]}, "engine:learning_evaluator")
    status = {"eligible_for_promotion": "validated", "reject": "rejected", "hold": "inconclusive"}[verdict["decision"]]
    statement = (f"Restricting the logistic fit window from {baseline_window or 'full history'} to {candidate_window} months "
                 f"for {product} share forecasts was {verdict['result']} (dev mean MAE change "
                 f"{-verdict['details']['dev_mean_improvement']:+.2f} pp; held-out {-verdict['details']['holdout_mean_improvement']:+.2f} pp).")
    outcome = memory.submit("outcome", statement, "engine:learning_evaluator",
                            {"tags": TAGS + [f"change:fit_window={candidate_window}", f"target:{product}"], "pilot": spec.name},
                            [f"experiment:{baseline_id}", f"experiment:{cid}", f"evaluation:{evid}"], experiment_id=cid,
                            record={"verdict": verdict}, status="tested")
    lesson = memory.submit("lesson", statement + " Reasons: " + "; ".join(verdict["reasons"]), "engine:learning_evaluator",
                           {"tags": TAGS + [f"change:fit_window={candidate_window}", f"target:{product}"], "pilot": spec.name,
                            "change_key": "logistic.fit_window_months"},
                           [f"evaluation:{evid}"], experiment_id=cid,
                           confidence="low" if status == "inconclusive" else "medium", record={"verdict": verdict})
    memory.link(lesson, "derived_from", outcome, "engine:learning_evaluator")
    memory.set_status(lesson, status, "engine:learning_evaluator", evaluation_id=evid if status == "validated" else None)
    return {"baseline_id": baseline_id, "candidate_id": cid, "evaluation_id": evid, "verdict": verdict,
            "lesson_id": lesson, "lesson_status": status, "baseline_dev": bdev, "candidate_dev": cdev,
            "baseline_holdout": bhold, "candidate_holdout": chold, "naive_holdout": simple}


class ReferenceImprovementAgent:
    """Deterministic proposer: consults prior lessons before proposing a change."""

    id = "agent:reference_improver:v1"

    def propose(self, memory: LearningMemory, tried_windows: list[int]) -> dict:
        lessons = memory.retrieve(tags=["component:forecasting.share_curve"], kinds=["lesson"])
        considered = [{"id": l["knowledge_id"], "status": l["status"], "statement": l["statement"]} for l in lessons]
        validated = [l for l in lessons if l["status"] == "validated"]
        if validated:
            w = int([t for t in validated[0]["scope"]["tags"] if t.startswith("change:fit_window=")][0].split("=")[1])
            return {"action": "generalisation_test", "window": w, "product": "hybrid", "considered": considered,
                    "rationale": f"Lesson {validated[0]['knowledge_id']} was validated for bev; test whether it generalises to hybrid share."}
        nxt = next(w for w in (96, 36, 120) if w not in tried_windows)
        return {"action": "ablation", "window": nxt, "product": "bev", "considered": considered,
                "rationale": (f"Prior lesson(s) did not validate window {tried_windows}; ablate the window length "
                              f"({nxt} months) rather than repeat the same change.")}


def run_demo(store, spec, memory: LearningMemory | None = None) -> dict:
    memory = memory or LearningMemory(store)
    dev = [f"{y}-01-15" for y in range(2015, 2021)]
    holdout = ["2022-01-15", "2023-01-15", "2024-01-15"]
    first = run_change(memory, store, spec, baseline_window=None, candidate_window=60, product="bev", dev=dev,
                       holdout=holdout, horizon=12, proposed_by="agent:reference_improver:v1",
                       rationale="M21: logistic forecasts were biased low at later origins; a recent-window fit may track curvature better.")
    agent = ReferenceImprovementAgent()
    proposal = agent.propose(memory, tried_windows=[60])
    for c in proposal["considered"]:
        memory.link(agent.id, "considered", c["id"], agent.id, "retrieved before proposing the next experiment")
    second = run_change(memory, store, spec, baseline_window=None, candidate_window=proposal["window"],
                        product=proposal["product"], dev=dev, holdout=holdout, horizon=12, proposed_by=agent.id,
                        rationale=proposal["rationale"],
                        baseline_id=first["baseline_id"] if proposal["product"] == "bev" else None)
    return {"first": first, "proposal": proposal, "second": second}

"""Historical replay: the full learning cycle on past data with publication-date cutoffs.

For each origin date d (chronological):
  1. score every earlier prediction whose outcome was PUBLISHED by d (score_due at d);
  2. analyse the market as of d (evidence -> research -> hypotheses -> critiques ->
     validation -> predictions; policy shadow predictions for method evaluation);
     lessons and track records are filtered to what was available at d.
Finally: score what has resolved by the final date, evaluate prediction
policies under the standing rule (paired development/held-out origins), and
record lessons. Lessons may be validated; ADOPTION of a policy still needs a
human-approved promotion (halibut learn promote).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

from ..data.store import utc_now
from ..learning.evaluate import decide
from ..learning.lineage import lineage
from ..learning.memory import LearningMemory
from ..learning.predictions import score_due
from .market import load_market
from .workflow import analyse

DEFAULT_ORIGINS = ["2021-11-23", "2022-05-23", "2022-11-23", "2023-05-23", "2023-11-23",
                   "2024-05-23", "2024-11-23", "2025-05-23", "2025-11-23", "2026-05-23"]


def make_agents(kind: str, store, run_id: str, faults: bool):
    from ..agents.orchestrator import LLMAgentSet, ReferenceAgentSet  # noqa: PLC0415
    if kind == "reference":
        return ReferenceAgentSet()
    if kind == "mock":
        from ..agents.llm import MockProvider  # noqa: PLC0415
        from ..agents.mock import responder  # noqa: PLC0415
        return LLMAgentSet(MockProvider(responder), store, run_id, faults=faults)
    if kind == "qwen":
        from ..agents.llm import QwenProvider, load_llm_config  # noqa: PLC0415
        cfg = load_llm_config("configs/llm/qwen.toml")
        spent = lambda: store.conn.execute("SELECT COALESCE(SUM(estimated_cost_usd),0) FROM agent_calls WHERE run_id LIKE ?",  # noqa: E731
                                           (run_id.split(":")[0] + "%",)).fetchone()[0]
        return LLMAgentSet(QwenProvider(cfg, spent), store, run_id)
    raise ValueError(f"unknown agent set {kind!r}")


def replay(store, market_path, origins=None, agents: str = "reference", faults: bool = False, final_as_of: str | None = None,
           dev_fraction: float = 0.6, log=print) -> dict:
    base = load_market(market_path)
    origins = sorted(origins or DEFAULT_ORIGINS)
    memory = LearningMemory(store)
    replay_id = "rp" + hashlib.sha256((Path(market_path).read_text() + agents + utc_now()).encode()).hexdigest()[:10]
    tag = [f"replay:{replay_id}"]
    exp_id = memory.record_experiment("intel_replay", f"replay {base.id} ({agents})", "engine:intel_replay",
                                      lineage(store, config={"market_spec": Path(market_path).read_text()},
                                              parameters={"agents": agents, "faults": faults, "origins": origins},
                                              datasets=["dft_veh0160_makes"], extra={"replay_id": replay_id}))
    t0, per = time.monotonic(), []
    for o in origins:
        s0 = time.monotonic()
        scored = score_due(store, memory, o, tags=tag)
        market = dataclasses.replace(base, as_of=o)
        run_id = f"{replay_id}:{o}"
        store.conn.execute("INSERT INTO intel_runs VALUES (?,?,?,?,?,?,?)",
                           (run_id, base.id, o, utc_now(), None, "running", json.dumps({"experiment": exp_id, "replay": replay_id})))
        a = analyse(store, market, memory, run_id, exp_id, {}, agents=make_agents(agents, store, run_id, faults),
                    replay_id=replay_id, preview=(o == origins[-1]), policy_eval=True)
        store.conn.execute("UPDATE intel_runs SET finished_at=?, status='completed' WHERE run_id=?", (utc_now(), run_id))
        per.append({"origin": o, "scored_at_origin": len(scored), "hypotheses_valid": len(a["accepted"]),
                    "hypotheses_rejected_by_validation": len(a["rejected"]),
                    "rejection_reasons": [h["validation"]["problems"][:2] for h in a["rejected"]],
                    "lessons_available": len(a["lessons"]), "track_record_entries": len(a["track_record"]),
                    "costs": a["agent_costs"], "seconds": round(time.monotonic() - s0, 2),
                    "audit_passed": a["audit"].get("all_passed"),
                    "drafts": len(a["drafts"])})
        log(f"  {o}: scored {len(scored):>3} due predictions; {len(a['accepted'])} valid / {len(a['rejected'])} rejected "
            f"hypotheses; {len(a['lessons'])} lessons available; {per[-1]['seconds']}s")
    final = score_due(store, memory, final_as_of or base.as_of, tags=tag)
    log(f"  final scoring ({final_as_of or base.as_of}): {len(final)} more predictions resolved")
    evaluations = evaluate_policies(store, memory, replay_id, origins, dev_fraction, base.id)
    summary = summarize(store, replay_id, per, agents, time.monotonic() - t0)
    summary["evaluations"] = evaluations
    memory.finish_experiment(exp_id, {"summary": {k: v for k, v in summary.items() if k != "per_origin"}})
    summary["experiment_id"] = exp_id
    return summary


def _scores(store, replay_id):
    out = []
    for r in store.conn.execute("SELECT * FROM prediction_scores"):
        rec = json.loads(r["record_json"])
        if rec.get("replay_id") == replay_id:
            out.append({**dict(r), **rec})
    return out


def evaluate_policies(store, memory, replay_id, origins, dev_fraction, market_id) -> list[dict]:
    rows = [r for r in _scores(store, replay_id) if r.get("hypothesis_id") is None and r["quantity_kind"] == "share"]
    by = defaultdict(lambda: defaultdict(list))
    for r in rows:
        by[(r["method"], r["interval_method"])][r["origin_as_of"]].append(r)
    n_dev = int(round(dev_fraction * len(origins)))
    from ..data.store import to_timestamp  # noqa: PLC0415
    dev_o = {to_timestamp(o) for o in origins[:n_dev]}

    def per_origin(key, metric):
        return {o: float(np.mean([x[metric] for x in v])) for o, v in by.get(key, {}).items()}

    comparisons = [
        ("momentum point forecast vs level persistence", ("level", "naive"), ("momentum", "naive"), "abs_error",
         {"method": "level"}, {"method": "momentum"}),
        ("last-quarter point forecast vs level persistence", ("level", "naive"), ("last_quarter", "naive"), "abs_error",
         {"method": "level"}, {"method": "last_quarter"}),
        ("empirically calibrated 80% intervals vs naive volatility intervals (level)", ("level", "naive"),
         ("level", "empirical"), "interval_score80", {"method": "level", "interval": "naive"},
         {"method": "level", "interval": "empirical"}),
    ]
    out = []
    for title, bkey, ckey, metric, bparams, cparams in comparisons:
        b, c = per_origin(bkey, metric), per_origin(ckey, metric)
        common = sorted(set(b) & set(c))
        if not common:
            out.append({"comparison": title, "status": "no data"})
            continue
        dev = [o for o in common if o in dev_o] or common[: max(1, int(round(dev_fraction * len(common))))]
        hold = [o for o in common if o not in dev]
        verdict = decide({o: b[o] for o in dev}, {o: c[o] for o in dev}, {o: b[o] for o in hold}, {o: c[o] for o in hold})
        bexp = memory.record_experiment("prediction_policy", f"{title}: baseline", "engine:intel_replay",
                                        lineage(store, parameters=bparams, extra={"replay_id": replay_id}), status="completed")
        cexp = memory.record_experiment("prediction_policy", f"{title}: candidate", "engine:intel_replay",
                                        lineage(store, parameters=cparams, extra={"replay_id": replay_id}), baseline_id=bexp,
                                        rationale=f"Evaluate {cparams} against {bparams} on replayed origins",
                                        expected_improvement={"metric": metric, "direction": "decrease"},
                                        change={k: [bparams.get(k), v] for k, v in cparams.items() if bparams.get(k) != v},
                                        status="completed")
        memory.finish_experiment(bexp, {metric: b})
        memory.finish_experiment(cexp, {metric: c})
        evid = memory.record_evaluation(cexp, bexp, verdict["rule"], verdict["result"], verdict["decision"],
                                        {**verdict["details"], "reasons": verdict["reasons"], "metric": metric},
                                        "engine:learning_evaluator")
        status = {"eligible_for_promotion": "validated", "reject": "rejected", "hold": "inconclusive"}[verdict["decision"]]
        avail = max(r["outcome_available_at"] for k in (bkey, ckey) for o in common for r in by[k][o])
        bm, cm = float(np.mean([b[o] for o in common])), float(np.mean([c[o] for o in common]))
        statement = (f"{title} ({market_id}, {len(common)} origins): mean {metric} {100 * bm:.2f} -> {100 * cm:.2f} pp; "
                     f"{verdict['result']} under rule {verdict['rule']['version']} ({'; '.join(verdict['reasons'])}).")
        kid = memory.submit("lesson", statement, "engine:learning_evaluator",
                            {"tags": ["domain:share_forecasting", f"market:{market_id}", "component:intel.prediction_policy",
                                      f"replay:{replay_id}"]},
                            [f"evaluation:{evid}"], experiment_id=cexp, confidence="medium" if status == "validated" else "low",
                            record={"verdict": verdict, "candidate": cparams, "baseline": bparams}, available_at=avail)
        if status == "validated":
            memory.set_status(kid, "validated", "engine:learning_evaluator", evaluation_id=evid)
        else:
            memory.set_status(kid, status, "engine:learning_evaluator")
        out.append({"comparison": title, "metric": metric, "origins": len(common), "dev": len(dev), "holdout": len(hold),
                    "baseline_mean_pp": 100 * bm, "candidate_mean_pp": 100 * cm, "result": verdict["result"],
                    "decision": verdict["decision"], "reasons": verdict["reasons"], "evaluation_id": evid,
                    "lesson_id": kid, "lesson_status": status, "lesson_available_at": avail,
                    "promotion": "awaiting human approval" if status == "validated" else "not eligible"})
    return out


def summarize(store, replay_id, per, agents, seconds) -> dict:
    sc = _scores(store, replay_id)
    hyp = [r for r in sc if r.get("hypothesis_id")]
    failed = [r for r in hyp if (r["direction_hit"] == 0) or not r["in_interval80"]]
    supported = [r for r in hyp if r not in failed]
    dir_ = [r["direction_hit"] for r in hyp if r["direction_hit"] is not None]
    cost = sum((p["costs"].get("estimated_cost_usd") or 0) for p in per)
    calls = sum(p["costs"].get("calls", 0) for p in per)
    tin = sum(p["costs"].get("input_tokens", 0) for p in per)
    tout = sum(p["costs"].get("output_tokens", 0) for p in per)
    hits = [p["costs"].get("cache_hit_rate") for p in per if p["costs"].get("cache_hit_rate") is not None]
    dups = sum(p["costs"].get("duplicate_tool_calls", 0) for p in per)
    lat = sum(p["costs"].get("latency_seconds", 0) for p in per)
    valid = sum(p["hypotheses_valid"] for p in per)
    return {"replay_id": replay_id, "agents": agents, "origins": [p["origin"] for p in per], "seconds": round(seconds, 1),
            "hypotheses_valid": valid, "hypotheses_rejected_by_validation": sum(p["hypotheses_rejected_by_validation"] for p in per),
            "hypothesis_predictions_scored": len(hyp), "supported": len(supported), "refuted": len(failed),
            "direction_hit_rate": (sum(dir_) / len(dir_)) if dir_ else None,
            "hypothesis_mae_pp": 100 * float(np.mean([r["abs_error"] for r in hyp])) if hyp else None,
            "hypothesis_coverage80": float(np.mean([r["in_interval80"] for r in hyp])) if hyp else None,
            "llm": {"calls": calls, "input_tokens": tin, "output_tokens": tout, "estimated_cost_usd": cost,
                    "cache_hit_rate": float(np.mean(hits)) if hits else None, "duplicate_tool_calls": dups,
                    "llm_latency_seconds": round(lat, 3)},
            "useful_validated_results": len(supported),
            "cost_per_useful_result_usd": (cost / len(supported)) if supported else None,
            "explanations_proposed": store.conn.execute(
                "SELECT COUNT(*) FROM knowledge WHERE kind='hypothesis' AND scope_json LIKE ? AND scope_json LIKE ?",
                (f"%replay:{replay_id}%", "%explanation%")).fetchone()[0],
            "per_origin": per}

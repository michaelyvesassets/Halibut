"""Benchmark an LLM agent set against the fixed-rule reference on identical cutoffs and evidence.

    plan  -> replay on a THROWAWAY COPY of the store with a planning provider that records the
             exact prompts (and returns empty answers), then price every call at its worst case
             (conservative input tokens + full max_output_tokens). No network, no spend.
    run   -> reference replay and LLM replay with the same origins, on the same data snapshots
             (hashes recorded before and after and required to match), then a side-by-side report.
             Identical predictions are counted once; predictions the reference also made are counted
             (they add no marginal value); failures use the R1 taxonomy (epistemic.failures).
    reconstruct -> OFFLINE re-run of a past LLM replay on a throwaway copy of the store: cached replies
             are served from the prompt cache, and calls whose replies were never cached (truncated,
             refused, invalid) return the outcome recorded for them at the time. No network, no spend.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path

from ..agents.budget import conservative_input_tokens
from ..agents.llm import LLMResponse, Pricing, pricing_from_config
from ..data.store import Store
from .replay import DEFAULT_ORIGINS, llm_config_for, replay  # noqa: F401  (reconstruct uses replay hooks)


class PlanningProvider:
    name, model = "planning", "planning"

    def __init__(self, max_out: dict, pricing: Pricing):
        self.calls, self.max_out, self.pricing = [], max_out, pricing

    def complete(self, system, user, *, role, max_output_tokens=None, **_):
        mo = int(self.max_out.get(role, 1200))
        cost = self.pricing.worst_case(system + user, mo)
        self.calls.append({"role": role, "chars": len(system + user), "worst_case_usd": cost})
        empty = {"research": {"actions": []}, "hypothesis": {"hypotheses": []}, "critic": {"critiques": []}}[role]
        return LLMResponse(json.dumps(empty), empty, conservative_input_tokens(system + user), 0, True, "planning",
                           "planning", 0.0)


def snapshot_hashes(store) -> list[str]:
    return sorted(f"{d}:{s}" for d, s in store.conn.execute("SELECT dataset_id, sha256 FROM snapshots"))


def plan(db_path, market_path, origins=None, agents: str = "qwen") -> dict:
    """Worst-case cost of one benchmark replay for agent kind ``agents`` ('qwen', 'anthropic[:model]'; 'mock' is
    priced with the Qwen config). Prices, caps and per-role output limits come from that provider's config."""
    cfg = llm_config_for(agents)
    pricing = pricing_from_config(cfg)
    tmp = Path(tempfile.mkdtemp(prefix="halibut_plan_")) / "plan.sqlite"
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(tmp)
    src.backup(dst)
    src.close(), dst.close()
    store = Store(tmp)
    from ..agents.orchestrator import LLMAgentSet  # noqa: PLC0415
    pp = PlanningProvider(cfg["max_output_tokens"], pricing)
    try:
        replay(store, market_path, origins, agents="planning", log=lambda *_: None,
               agent_factory=lambda st, run_id: LLMAgentSet(pp, st, run_id))
    finally:
        store.close()
        shutil.rmtree(tmp.parent, ignore_errors=True)
    # critic prompts are not issued when the planner returns no hypotheses: price them as
    # hypothesis prompts plus room for the hypotheses themselves (one per origin)
    hyp = [c for c in pp.calls if c["role"] == "hypothesis"]
    critic = [{"role": "critic", "chars": c["chars"] + 6000,
               "worst_case_usd": pricing.worst_case("x" * (c["chars"] + 6000), int(cfg["max_output_tokens"]["critic"]))}
              for c in hyp]
    calls = pp.calls + critic
    worst = sum(c["worst_case_usd"] for c in calls)
    with_retry = worst * (1 + int(cfg.get("retries", 1)))
    return {"provider": cfg["provider"], "model": cfg["model"], "origins": origins or DEFAULT_ORIGINS, "calls": len(calls),
            "by_role": {r: sum(1 for c in calls if c["role"] == r) for r in ("research", "hypothesis", "critic")},
            "prompt_chars_total": sum(c["chars"] for c in calls), "worst_case_usd": worst,
            "worst_case_with_retries_usd": with_retry, "per_run_cap_usd": cfg["max_spend_usd_per_run"],
            "total_cap_usd": cfg["max_spend_usd_total"],
            "fits_per_run_cap": with_retry <= float(cfg["max_spend_usd_per_run"]),
            "smoke_cap_usd": cfg.get("max_spend_usd_smoke"),
            "pricing": {"input_per_mtok": pricing.input_per_mtok, "output_per_mtok": pricing.output_per_mtok,
                        "cache_write_5m_per_mtok": cfg.get("cache_write_5m_per_mtok_usd"),
                        "cache_read_per_mtok": cfg.get("cache_read_per_mtok_usd"),
                        "worst_case_rule": "conservative input tokens at the highest applicable input rate + full max_output_tokens",
                        "source": cfg.get("pricing_source")}}


def _failure_types(summary) -> dict:
    """Rejections and drops by R1 failure class (format errors are no longer reported as fabrication)."""
    out: dict = {}
    for p in summary["per_origin"]:
        for c in p.get("rejection_classes", []) + p.get("dropped_classes", []):
            out[c or "unclassified"] = out.get(c or "unclassified", 0) + 1
    return out


def overlap(store, llm_replay_id: str, ref_replay_id: str) -> dict:
    """How many of the LLM's unique scored predictions the reference also made (zero marginal value, F1)."""
    from .replay import _scores, prediction_identity, unique_predictions  # noqa: PLC0415
    ref = {prediction_identity(r) for r in unique_predictions([r for r in _scores(store, ref_replay_id) if r.get("hypothesis_id")])}
    llm = unique_predictions([r for r in _scores(store, llm_replay_id) if r.get("hypothesis_id")])
    same = [r for r in llm if prediction_identity(r) in ref]
    return {"unique_llm_predictions": len(llm), "identical_to_reference": len(same),
            "not_made_by_reference": len(llm) - len(same),
            "supported_not_made_by_reference": sum(1 for r in llm if prediction_identity(r) not in ref
                                                   and r["direction_hit"] != 0 and r["in_interval80"])}


def run(store, market_path, agents: str, origins=None) -> dict:
    before = snapshot_hashes(store)
    t0 = time.monotonic()
    ref = replay(store, market_path, origins, agents="reference", log=lambda *_: None)
    llm = replay(store, market_path, origins, agents=agents, log=print)
    after = snapshot_hashes(store)
    if before != after:
        raise RuntimeError("evidence snapshots changed during the benchmark: comparison invalid")
    lat = store.conn.execute("SELECT COUNT(*), COALESCE(SUM(latency_seconds),0), COALESCE(MAX(latency_seconds),0) FROM agent_calls "
                             "WHERE run_id LIKE ? AND cache_hit=0", (llm["replay_id"] + "%",)).fetchone()
    spend = store.conn.execute("""SELECT COALESCE(SUM(CASE WHEN status='settled' THEN actual_usd ELSE reserved_usd END),0)
        FROM llm_spend_ledger WHERE run_id LIKE ?""", (llm["replay_id"] + "%",)).fetchone()[0]
    attempts = store.conn.execute("SELECT COUNT(*), SUM(status='failed_charged') FROM llm_spend_ledger WHERE run_id LIKE ?",
                                  (llm["replay_id"] + "%",)).fetchone()
    from ..epistemic.failures import classify_outcome  # noqa: PLC0415
    failed = {}
    for (o,) in store.conn.execute("SELECT outcome FROM agent_calls WHERE run_id LIKE ? AND outcome != 'ok'", (llm["replay_id"] + "%",)):
        c = classify_outcome(o)
        failed[c] = failed.get(c, 0) + 1
    rows = {}
    for name, s in (("reference", ref), (agents, llm)):
        cost = spend if name == agents else 0.0
        rows[name] = {"replay_id": s["replay_id"], "hypotheses_valid": s["hypotheses_valid"],
                      "rejected_by_validation": s["hypotheses_rejected_by_validation"],
                      "validation_failure_types": _failure_types(s), "scored": s["hypothesis_predictions_scored"],
                      "dropped_before_validation": s.get("hypotheses_dropped", 0),
                      "scored_raw": s.get("hypothesis_predictions_scored_raw"),
                      "duplicate_predictions_removed": s.get("duplicate_predictions_removed"),
                      "supported": s["supported"], "refuted": s["refuted"], "direction_hit_rate": s["direction_hit_rate"],
                      "mae_pp": s["hypothesis_mae_pp"], "coverage80": s["hypothesis_coverage80"], "runtime_s": s["seconds"],
                      "llm_calls": s["llm"]["calls"], "tokens_in": s["llm"]["input_tokens"], "tokens_out": s["llm"]["output_tokens"],
                      "cost_usd": cost, "cost_per_useful_validated_result_usd": (cost / s["supported"]) if s["supported"] else None}
    rows[agents]["latency_total_s"], rows[agents]["latency_max_s"] = round(lat[1], 2), round(lat[2], 2)
    rows[agents]["latency_mean_s"] = round(lat[1] / lat[0], 3) if lat[0] else None
    rows[agents]["failed_llm_calls"] = failed
    rows[agents]["paid_attempts"], rows[agents]["failed_attempts_charged"] = attempts[0], attempts[1] or 0
    rows[agents]["overlap_with_reference"] = overlap(store, llm["replay_id"], ref["replay_id"])
    rows[agents]["critic_calls"] = {"planned_if_every_origin": len(ref["origins"]),
                                    "made": sum(1 for p in llm["per_origin"] if (p["costs"].get("by_role") or {}).get("critic")),
                                    "rule": "the critic runs only for origins with at least one valid hypothesis"}
    return {"identical_cutoffs": True, "origins": ref["origins"], "evidence_snapshots": len(before),
            "publication_dates": "history before the first retrieval uses ESTIMATED publication dates (quarter end + 120 days)",
            "results": rows, "seconds": round(time.monotonic() - t0, 1),
            "verdict_rule": "the LLM set must beat the reference on supported results, hit rate, coverage and MAE on the same "
                            "held-out outcomes, per dollar, before any promotion (human approval still required)"}


class RecordedOutcomeProvider:
    """Offline stand-in for a past provider. Wrapped in CachedProvider, it is called only on cache
    misses; it then returns the outcome RECORDED for that (origin, role) in the original replay
    (e.g. a truncated reply), so the reconstruction reproduces what happened without any request."""

    def __init__(self, store, original_replay_id: str, name: str, model: str, cache_salt: str | None):
        self.name, self.model, self.cache_salt, self.accepts_run_id = name, model, cache_salt, True
        self.recorded: dict = {}
        for r in store.conn.execute("SELECT run_id, role, outcome, input_tokens, output_tokens FROM agent_calls WHERE run_id LIKE ? "
                                    "AND cache_hit=0 ORDER BY rowid", (original_replay_id + ":%",)):
            self.recorded.setdefault((r["run_id"].split(":", 1)[1], r["role"]), []).append(dict(r))
        self.misses = []

    def complete(self, system, user, *, role, max_output_tokens=None, run_id="unscoped", **_):
        origin = run_id.split(":", 1)[1] if ":" in run_id else run_id
        rows = self.recorded.get((origin, role)) or [{"outcome": "missing", "input_tokens": 0, "output_tokens": 0}]
        rec = rows.pop(0) if len(rows) > 1 else rows[0]
        self.misses.append({"origin": origin, "role": role, "recorded_outcome": rec["outcome"]})
        r = LLMResponse("", None, rec["input_tokens"], rec["output_tokens"], False, self.model, self.name, 0.0,
                        estimated_cost_usd=0.0, actual_cost_usd=0.0)
        o = rec["outcome"] or ""
        r.stop_reason = ("max_tokens" if "max_tokens" in o else "refusal" if "refusal" in o
                         else "not_reproducible" if o == "ok" else "recorded_failure")
        return r


def reconstruct(db_path, market_path, original_replay_id: str, origins=None, salt_config=None) -> dict:
    """Re-run a past LLM replay offline (throwaway copy) with the ORIGINAL prompts (v1) and classify every
    failure with the R1 taxonomy. Also reports how the R1 output contract (v2 schema) would have judged
    each proposed item (diagnostic; nothing is repaired)."""
    from ..agents.anthropic_provider import cache_salt  # noqa: PLC0415
    from ..agents.orchestrator import LLMAgentSet  # noqa: PLC0415
    from ..epistemic.failures import summary as failure_summary  # noqa: PLC0415
    tmp = Path(tempfile.mkdtemp(prefix="halibut_reconstruct_")) / "r.sqlite"
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(tmp)
    src.backup(dst)
    src.close(), dst.close()
    store = Store(tmp)
    try:
        row = store.conn.execute("SELECT provider, model FROM agent_calls WHERE run_id LIKE ? LIMIT 1",
                                 (original_replay_id + ":%",)).fetchone()
        if row is None:
            raise ValueError(f"no recorded agent calls for replay {original_replay_id}")
        exp = store.conn.execute("SELECT lineage_json FROM experiments WHERE lineage_json LIKE ?",
                                 (f'%"replay_id": "{original_replay_id}"%',)).fetchone()
        orig_origins = (json.loads(exp[0]).get("parameters", {}).get("origins") if exp else None) or origins or DEFAULT_ORIGINS
        cfg = salt_config or llm_config_for(f"{row['provider']}:{row['model']}" if row["provider"] == "anthropic" else row["provider"])
        prov = RecordedOutcomeProvider(store, original_replay_id, row["provider"], row["model"],
                                       cache_salt(cfg) if row["provider"] == "anthropic" else None)
        sets = []

        def factory(st, run_id):
            a = LLMAgentSet(prov, st, run_id, prompt_version="v1")
            sets.append(a)
            return a
        rec = replay(store, market_path, orig_origins, agents=f"reconstruct:{original_replay_id}", log=lambda *_: None,
                     agent_factory=factory)
        contract = [c for p in rec["per_origin"] for c in p.get("contract_diagnostic", [])]
        cache_hits = sum(a.provider.hits for a in sets)
        from .replay import _scores, summarize  # noqa: PLC0415
        orig_rows = [r for r in _scores(store, original_replay_id) if r.get("hypothesis_id")]
        fails = failure_summary(store, rec["replay_id"])
        r1 = {"items": len(contract), "would_pass": sum(1 for c in contract if not c["problems"]),
              "would_be_rejected": sum(1 for c in contract if c["problems"])}
        from ..epistemic.failures import classify_problems  # noqa: PLC0415
        from .market import load_market  # noqa: PLC0415
        subjects = [c.id for c in load_market(market_path).competitors]
        r1["rejection_classes"] = {}
        for c in contract:
            if c["problems"]:
                k = classify_problems(c["problems"], c["item"], subjects=subjects)
                r1["rejection_classes"][k] = r1["rejection_classes"].get(k, 0) + 1
        return {"original_replay_id": original_replay_id, "reconstruction_replay_id": rec["replay_id"],
                "provider": row["provider"], "model": row["model"], "origins": orig_origins, "prompt_version": "v1",
                "cache_hits": cache_hits, "recorded_outcomes_served": prov.misses,
                "reproduced": {k: rec[k] for k in ("hypotheses_valid", "hypotheses_rejected_by_validation", "hypotheses_dropped",
                                                   "hypothesis_predictions_scored_raw", "hypothesis_predictions_scored",
                                                   "duplicate_predictions_removed", "supported", "refuted",
                                                   "direction_hit_rate", "hypothesis_mae_pp", "hypothesis_coverage80")},
                "original_scored_raw": len(orig_rows),
                "failures": fails, "r1_contract_on_same_items": r1,
                "note": "offline reconstruction on a throwaway copy; no requests sent; summary deduplicates identical predictions"}
    finally:
        store.close()
        shutil.rmtree(tmp.parent, ignore_errors=True)

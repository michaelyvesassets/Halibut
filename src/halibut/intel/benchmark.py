"""Benchmark an LLM agent set against the fixed-rule reference on identical cutoffs and evidence.

    plan  -> replay on a THROWAWAY COPY of the store with a planning provider that records the
             exact prompts (and returns empty answers), then price every call at its worst case
             (conservative input tokens + full max_output_tokens). No network, no spend.
    run   -> reference replay and LLM replay with the same origins, on the same data snapshots
             (hashes recorded before and after and required to match), then a side-by-side report.
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
from .replay import DEFAULT_ORIGINS, llm_config_for, replay


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
    from . import replay as rp  # noqa: PLC0415
    pp = PlanningProvider(cfg["max_output_tokens"], pricing)
    original = rp.make_agents
    rp.make_agents = lambda kind, st, run_id, faults: LLMAgentSet(pp, st, run_id)  # noqa: E731
    try:
        replay(store, market_path, origins, agents="planning", log=lambda *_: None)
    finally:
        rp.make_agents = original
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
    out = {"fabricated_or_missing_metric": 0, "unsupported_number": 0, "prohibited_or_lever": 0, "other": 0}
    for p in summary["per_origin"]:
        for reasons in p["rejection_reasons"]:
            r = " ".join(reasons)
            if "metric not found" in r or "does not exist" in r:
                out["fabricated_or_missing_metric"] += 1
            elif "does not match any evidence value" in r:
                out["unsupported_number"] += 1
            elif "prohibited" in r or "lever" in r:
                out["prohibited_or_lever"] += 1
            else:
                out["other"] += 1
    return out


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
    failed = dict(store.conn.execute("""SELECT CASE WHEN outcome LIKE 'unparseable%' THEN 'unparseable_or_refused'
        WHEN outcome LIKE 'error%' THEN 'error' ELSE 'other' END, COUNT(*) FROM agent_calls
        WHERE run_id LIKE ? AND outcome != 'ok' GROUP BY 1""", (llm["replay_id"] + "%",)).fetchall())
    attempts = store.conn.execute("SELECT COUNT(*), SUM(status='failed_charged') FROM llm_spend_ledger WHERE run_id LIKE ?",
                                  (llm["replay_id"] + "%",)).fetchone()
    rows = {}
    for name, s in (("reference", ref), (agents, llm)):
        cost = spend if name == agents else 0.0
        rows[name] = {"replay_id": s["replay_id"], "hypotheses_valid": s["hypotheses_valid"],
                      "rejected_by_validation": s["hypotheses_rejected_by_validation"],
                      "validation_failure_types": _failure_types(s), "scored": s["hypothesis_predictions_scored"],
                      "supported": s["supported"], "refuted": s["refuted"], "direction_hit_rate": s["direction_hit_rate"],
                      "mae_pp": s["hypothesis_mae_pp"], "coverage80": s["hypothesis_coverage80"], "runtime_s": s["seconds"],
                      "llm_calls": s["llm"]["calls"], "tokens_in": s["llm"]["input_tokens"], "tokens_out": s["llm"]["output_tokens"],
                      "cost_usd": cost, "cost_per_useful_validated_result_usd": (cost / s["supported"]) if s["supported"] else None}
    rows[agents]["latency_total_s"], rows[agents]["latency_max_s"] = round(lat[1], 2), round(lat[2], 2)
    rows[agents]["latency_mean_s"] = round(lat[1] / lat[0], 3) if lat[0] else None
    rows[agents]["failed_llm_calls"] = failed
    rows[agents]["paid_attempts"], rows[agents]["failed_attempts_charged"] = attempts[0], attempts[1] or 0
    return {"identical_cutoffs": True, "origins": ref["origins"], "evidence_snapshots": len(before),
            "publication_dates": "history before the first retrieval uses ESTIMATED publication dates (quarter end + 120 days)",
            "results": rows, "seconds": round(time.monotonic() - t0, 1),
            "verdict_rule": "the LLM set must beat the reference on supported results, hit rate, coverage and MAE on the same "
                            "held-out outcomes, per dollar, before any promotion (human approval still required)"}

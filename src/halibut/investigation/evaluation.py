"""Metrics for comparing agent workforces on identical evidence (R2 live validation).

Everything is computed from published investigation result documents (``halibut.investigation_result.v1``),
so the same numbers can be recomputed by anyone from the exported files.

Definitions (v1):
  proposed                hypothesis items returned by the generator (accepted + rejected + duplicates)
  evidence_fidelity       accepted / (accepted + rejected at schema or validation stage); duplicates excluded
  original                accepted hypotheses whose testable implications are not all in the reference library
  falsifiable             has refutation criteria AND >= 1 registered test AND every alternative explanation
                          names an implication that compiled into a registered test
  testable                has >= 1 registered test
  informative test        a registered test that trend extrapolation did NOT already predict (trend_consistent false)
  useful validated result an ORIGINAL hypothesis that SURVIVED all its tests including >= 1 informative test
                          (status survived, strength 'moderate'); survival is not causal proof
  cost per useful result  paid cost / useful validated results (None when there are none)
"""

from __future__ import annotations

from collections import Counter

METRICS_VERSION = "r2_validation_metrics.v1"


def _sig(imp: dict) -> tuple:
    return (imp.get("template"), imp.get("entity"), imp.get("entity_b") or "", imp.get("comparator"))


def evaluate_document(doc: dict) -> dict:
    st = doc["stages"]
    hyps, tests, results = st["hypotheses"], st["tests"], st["test_results"]
    by_h = {h["id"]: [t for t in tests if any(ln["to"] == h["id"] for ln in t["links"])] for h in hyps}
    res_by_t = {ln["to"]: r for r in results for ln in r["links"] if ln["relation"] == "result_of"}
    f_items = doc["failures"]["items"]
    rej_h = [f for f in f_items if f["subject"] == "hypothesis" and f["stage"] in ("schema", "validation")]
    dup_h = [f for f in f_items if f["failure_class"] == "duplicate.hypothesis"]
    rej_e = [f for f in f_items if f["subject"] == "explanation"]
    n_acc = len(hyps)

    def falsifiable(h):
        reg = {t["data"]["implication"]["id"] for t in by_h[h["id"]] if t["status"] == "registered"}
        alts = h["data"].get("alternative_explanations", [])
        return bool(h["data"].get("refutation_criteria")) and bool(reg) and all(a["discriminating_implication"] in reg
                                                                                 for a in alts)
    registered = [t for t in tests if t["status"] == "registered"]
    informative = [t for t in registered if (t["data"]["spec"].get("trend_check") or {}).get("trend_consistent") is False]
    status = Counter(h["status"] for h in hyps)
    useful = [h for h in hyps if h["data"].get("novelty") == "original" and h["status"] == "survived"
              and str((h["data"].get("status_detail") or {}).get("strength", "")).startswith("moderate")]
    calls = doc["agents"]["calls"]
    cost = float(doc["agents"]["costs"]["actual_cost_usd"] or 0)
    fc = [f for f in st["forecasts"] if (f["data"].get("score") or {}).get("status") == "scored"]
    gen = next((c for c in calls if c["role"] == "hypothesis_generator"), {})
    return {
        "metrics_version": METRICS_VERSION, "investigation_id": doc["investigation_id"], "agents": doc["provenance"]["agents"],
        "as_of": doc["objective"]["as_of"], "evaluate_as_of": doc["objective"]["evaluate_as_of"], "status": doc["status"],
        "stop_reason": doc["stop_reason"], "panel_fingerprint": doc["evidence"]["panel"].get("fingerprint"),
        "hypotheses": {"proposed": n_acc + len(rej_h) + len(dup_h), "accepted": n_acc, "rejected": len(rej_h),
                       "duplicates": len(dup_h), "original": sum(1 for h in hyps if h["data"].get("novelty") == "original"),
                       "restatements": sum(1 for h in hyps if h["data"].get("novelty") == "restatement"),
                       "falsifiable": sum(1 for h in hyps if falsifiable(h)),
                       "testable": sum(1 for h in hyps if any(t["status"] == "registered" for t in by_h[h["id"]])),
                       "status": dict(status), "useful_validated": len(useful)},
        "rates": {"evidence_fidelity": (n_acc / (n_acc + len(rej_h))) if (n_acc + len(rej_h)) else None,
                  "originality": (sum(1 for h in hyps if h["data"].get("novelty") == "original") / n_acc) if n_acc else None,
                  "falsifiability": (sum(1 for h in hyps if falsifiable(h)) / n_acc) if n_acc else None,
                  "testability_hypotheses": (sum(1 for h in hyps if any(t["status"] == "registered" for t in by_h[h["id"]]))
                                             / n_acc) if n_acc else None,
                  "testability_implications": (len(registered) / len(tests)) if tests else None,
                  "informativeness": (len(informative) / len(registered)) if registered else None},
        "explanations": {"accepted": len(st["explanations"]), "rejected": len(rej_e)},
        "tests": {"total": len(tests), "registered": len(registered), "not_testable": len(tests) - len(registered),
                  "informative": len(informative), "results": dict(Counter(r["status"] for r in results)),
                  "informative_results": dict(Counter(res_by_t[t["id"]]["status"] for t in informative if t["id"] in res_by_t))},
        "forecasts": {"official": len(st["forecasts"]), "estimates": len(st["estimates"]), "scored": len(fc),
                      "mae_pp": (100 * sum(f["data"]["score"]["abs_error"] for f in fc) / len(fc)) if fc else None,
                      "coverage80": (sum(1 for f in fc if f["data"]["score"]["in_interval80"]) / len(fc)) if fc else None},
        "strategy": {"options": len(st["strategic_options"]),
                     "rejected": sum(1 for f in f_items if f["subject"] == "strategic_option"),
                     "simulations": len(st["simulations"]), "protocol": doc["experiment_protocol"] is not None},
        "failures": dict(doc["failures"]["counts"]),
        "calls": {"n": len(calls), "failed": [c["failure_class"] for c in calls if c["outcome"] != "ok"],
                  "truncated": sum(1 for c in calls if c["failure_class"] == "call.truncated"),
                  "schema_top_level": sum(1 for f in f_items if f["failure_class"] == "call.schema_top_level"),
                  "refusals": sum(1 for c in calls if c["failure_class"] == "call.refusal"),
                  "input_tokens": sum(c["input_tokens"] or 0 for c in calls),
                  "output_tokens": sum(c["output_tokens"] or 0 for c in calls),
                  "generator_output_tokens": gen.get("output_tokens"), "generator_max_output_tokens": gen.get("max_output_tokens"),
                  "latency_s": round(sum(c["latency_seconds"] or 0 for c in calls), 2), "cache_hits": sum(1 for c in calls if c["cached"])},
        "cost_usd": cost, "cost_per_useful_result_usd": (cost / len(useful)) if useful else None,
        "signatures": sorted({_sig(i) for h in hyps for i in h["data"].get("observable_implications", [])
                              if i.get("template") != "other"})}


def overlap(a: dict, b: dict) -> dict:
    sa, sb = {tuple(x) for x in a["signatures"]}, {tuple(x) for x in b["signatures"]}
    return {"shared": len(sa & sb), "only_first": len(sa - sb), "jaccard": (len(sa & sb) / len(sa | sb)) if sa | sb else None}

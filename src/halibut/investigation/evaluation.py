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
  informative refutation  an ORIGINAL accepted hypothesis REFUTED by a pre-registered test. Reported SEPARATELY from
                          useful validated results: it is useful learning, not successful prediction. Kinds:
                          'risky_claim_refuted' (the failed test went against the trend) and
                          'trend_break_detected' (trend extrapolation predicted the test, and the outcome broke it)
  per role                tokens, cost, latency and useful outputs of each agent role (generator, critic, strategist);
                          critic skill = share of refuted hypotheses that had a high-severity critique

COMMON block (identical method for every arm, v1 or v2; only fields both contracts produce):
  original (common)       testable implications not all in the reference library (v1's definition)
  competing pair          two accepted hypotheses, DECLARED OR NOT, passing the mechanism gates of loop_v2.assess_pair
                          (same observation, distinct mechanisms, not a pure negation, interpretable disputed quantity).
                          Opposing predictions alone never count
  informative test        trend extrapolation did not predict it, OR it is on a disputed quantity of a competing pair
  discriminating resolution
                          a competing pair's disputed tests resolved with one passing and the other failing
  informative confirmation / refutation
                          an informative test that passed / failed (REPORTED SEPARATELY)
  validated useful result an ORIGINAL (common) hypothesis with no failed test and >= 1 passed informative test;
                          predictive support only, NOT causal proof
  cost per validated useful result, tokens, latency and cache hits per role

COMPLIANCE block (loop.v2 only): declared rivals, information gaps, critique dimensions/evidence, numeric-claim
  rejections. This is CONTRACT COMPLIANCE, reported apart from intelligence and never used to score an arm against v1.
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
    refutations = []
    for h in hyps:
        if h["data"].get("novelty") == "original" and h["status"] == "refuted":
            kinds = sorted({"risky_claim_refuted" if (t["data"]["spec"].get("trend_check") or {}).get("trend_consistent") is False
                            else "trend_break_detected" for t in by_h[h["id"]]
                            if t["id"] in res_by_t and res_by_t[t["id"]]["status"] == "failed"})
            refutations.append({"ref": h["data"]["ref"], "statement": h["label"][:200], "kinds": kinds})
    crit_by_h = {}
    for c in st["critiques"]:
        for ln in c["links"]:
            crit_by_h.setdefault(ln["to"], []).append(c["data"].get("severity"))
    refuted = [h for h in hyps if h["status"] == "refuted"]
    roles = {}
    for c in calls:
        r = roles.setdefault(c["role"], {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "latency_s": 0.0,
                                         "cache_hits": 0, "prompt_chars": 0, "schema_chars": 0, "stop_reasons": []})
        r["calls"] += 1
        r["input_tokens"] += c["input_tokens"] or 0
        r["output_tokens"] += c["output_tokens"] or 0
        r["cost_usd"] += c["actual_cost_usd"] or 0
        r["latency_s"] = round(r["latency_s"] + (c["latency_seconds"] or 0), 2)
        r["cache_hits"] += int(bool(c["cached"]))
        r["prompt_chars"] += c.get("prompt_chars") or 0
        r["schema_chars"] += c.get("schema_chars") or 0
        r["stop_reasons"].append(c.get("stop_reason"))
    roles.setdefault("hypothesis_generator", {})["useful_outputs"] = {
        "explanations": len(st["explanations"]), "hypotheses_accepted": n_acc,
        "original": sum(1 for h in hyps if h["data"].get("novelty") == "original"), "useful_validated": len(useful),
        "informative_refutations": len(refutations)}
    roles.setdefault("loop_critic", {})["useful_outputs"] = {
        "critiques": len(st["critiques"]),
        "critic_skill": (sum(1 for h in refuted if "high" in crit_by_h.get(h["id"], [])) / len(refuted)) if refuted else None,
        "high_severity_on_survivors": sum(1 for h in hyps if h["status"] == "survived" and "high" in crit_by_h.get(h["id"], []))}
    roles.setdefault("strategist", {})["useful_outputs"] = {
        "options_accepted": len(st["strategic_options"]),
        "options_rejected": sum(1 for f in f_items if f["subject"] == "strategic_option"), "simulations": len(st["simulations"])}
    calls = doc["agents"]["calls"]
    cost = float(doc["agents"]["costs"]["actual_cost_usd"] or 0)
    fc = [f for f in st["forecasts"] if (f["data"].get("score") or {}).get("status") == "scored"]
    gen = next((c for c in calls if c["role"] == "hypothesis_generator"), {})
    common = _common(doc, hyps, registered, by_h, res_by_t, cost)
    v2block = _v2(doc, hyps, registered, by_h, res_by_t, f_items, cost) if "information_gaps" in st else None
    return {
        "common": common, "v2": v2block,
        "metrics_version": METRICS_VERSION, "investigation_id": doc["investigation_id"], "agents": doc["provenance"]["agents"],
        "as_of": doc["objective"]["as_of"], "evaluate_as_of": doc["objective"]["evaluate_as_of"], "status": doc["status"],
        "stop_reason": doc["stop_reason"], "panel_fingerprint": doc["evidence"]["panel"].get("fingerprint"),
        "completion": (doc["failures"].get("completion") or {}).get("state"),
        "hypotheses": {"proposed": n_acc + len(rej_h) + len(dup_h), "accepted": n_acc, "rejected": len(rej_h),
                       "duplicates": len(dup_h), "original": sum(1 for h in hyps if h["data"].get("novelty") == "original"),
                       "restatements": sum(1 for h in hyps if h["data"].get("novelty") == "restatement"),
                       "falsifiable": sum(1 for h in hyps if falsifiable(h)),
                       "testable": sum(1 for h in hyps if any(t["status"] == "registered" for t in by_h[h["id"]])),
                       "status": dict(status), "useful_validated": len(useful), "informative_refutations": len(refutations)},
        "informative_refutations": refutations, "roles": roles,
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


def _pseudo(h: dict) -> dict:
    d = h["data"]
    return {"ref": d.get("ref"), "mechanism": d.get("mechanism") or {}, "explains": d.get("explains"),
            "supporting_evidence": [ln["to"] for ln in h["links"] if ln["relation"] == "cites"],
            "observable_implications": d.get("observable_implications", [])}


def _common(doc, hyps, registered, by_h, res_by_t, cost) -> dict:
    from .loop_v2 import assess_pair, quantity  # noqa: PLC0415
    ps = {h["id"]: _pseudo(h) for h in hyps}
    reg_ids = {t["id"] for t in registered}
    test_of = {(h["id"], t["data"]["implication"]["id"]): t for h in hyps for t in by_h[h["id"]]}
    pairs, disputed_tests, resolutions = [], set(), {"discriminating": 0, "both_passed": 0, "both_failed": 0, "pending": 0}
    for i, a in enumerate(hyps):
        for b in hyps[i + 1:]:
            ass = assess_pair(ps[a["id"]], ps[b["id"]])
            if not ass["counted"]:
                continue
            pairs.append([a["data"].get("ref"), b["data"].get("ref")])
            for q in map(tuple, ass["disputed_quantities"]):
                ta = next((test_of[(a["id"], m["id"])] for m in ps[a["id"]]["observable_implications"]
                           if m["template"] != "other" and quantity(m) == q and (a["id"], m["id"]) in test_of), None)
                tb = next((test_of[(b["id"], m["id"])] for m in ps[b["id"]]["observable_implications"]
                           if m["template"] != "other" and quantity(m) == q and (b["id"], m["id"]) in test_of), None)
                for t in (ta, tb):
                    if t and t["id"] in reg_ids:
                        disputed_tests.add(t["id"])
                if ta and tb and ta["id"] in reg_ids and tb["id"] in reg_ids:
                    ra, rb = (res_by_t.get(x["id"], {}).get("status") for x in (ta, tb))
                    key = "pending" if None in (ra, rb) or "pending" in (ra, rb) else \
                        "discriminating" if {ra, rb} == {"passed", "failed"} else \
                        "both_passed" if ra == rb == "passed" else "both_failed"
                    resolutions[key] += 1
    trend_inf = {t["id"] for t in registered if (t["data"]["spec"].get("trend_check") or {}).get("trend_consistent") is False}
    inf = trend_inf | disputed_tests
    out = Counter(res_by_t[t]["status"] for t in inf if t in res_by_t)
    orig = lambda h: (h["data"].get("novelty_common") or h["data"].get("novelty")) == "original"  # noqa: E731
    useful = []
    for h in hyps:
        res = [res_by_t[t["id"]]["status"] for t in by_h[h["id"]] if t["id"] in res_by_t]
        if orig(h) and "failed" not in res and any(t["id"] in inf and res_by_t.get(t["id"], {}).get("status") == "passed"
                                                   for t in by_h[h["id"]]):
            useful.append(h["data"].get("ref"))
    calls = doc["agents"]["calls"]
    return {"hypotheses_original": sum(1 for h in hyps if orig(h)), "competing_pairs": pairs,
            "tests": {"registered": len(registered), "trend_informative": len(trend_inf), "disputed": len(disputed_tests),
                      "informative": len(inf), "informative_confirmations": out.get("passed", 0),
                      "informative_refutations": out.get("failed", 0),
                      "informative_pending": sum(1 for t in inf if res_by_t.get(t, {}).get("status") in (None, "pending"))},
            "resolutions": resolutions,
            "rates": {"informativeness": (len(inf) / len(registered)) if registered else None},
            "validated_useful_results": useful,
            "cost_per_validated_useful_result_usd": (cost / len(useful)) if useful else None,
            "label": "predictive support, not causal proof",
            "efficiency": {"input_tokens": sum(c["input_tokens"] or 0 for c in calls),
                           "output_tokens": sum(c["output_tokens"] or 0 for c in calls),
                           "latency_s": round(sum(c["latency_seconds"] or 0 for c in calls), 2),
                           "cache_hits": sum(1 for c in calls if c["cached"]), "cost_usd": cost}}


def _v2(doc, hyps, registered, by_h, res_by_t, f_items, cost) -> dict:
    """loop.v2 CONTRACT COMPLIANCE (and v2-declared rival metrics). Not an intelligence score across arms: see _common."""
    st = doc["stages"]
    disc = [t for t in registered if t["data"].get("discriminates_from")]
    inf = [t for t in registered if t["data"].get("discriminates_from")
           or (t["data"]["spec"].get("trend_check") or {}).get("trend_consistent") is False]
    out = Counter(res_by_t[t["id"]]["status"] for t in inf if t["id"] in res_by_t)
    useful = []
    for h in hyps:
        ts = by_h[h["id"]]
        res = [res_by_t[t["id"]]["status"] for t in ts if t["id"] in res_by_t]
        if h["data"].get("novelty") == "original" and "failed" not in res and any(
                t in inf and t["id"] in res_by_t and res_by_t[t["id"]]["status"] == "passed" for t in ts):
            useful.append(h["data"]["ref"])
    crit = st["critiques"]
    fact = [c for c in crit if c["data"].get("dimension") in ("factual_contradiction", "numerical_accuracy")]
    numeric_rej = [f for f in f_items if f["subject"] == "strategic_option" and any(
        ("numeric_claims" in p or "does not reproduce" in p or "numeric claim" in p) for p in f["problems"])]
    proto = doc["experiment_protocol"]
    return {
        "hypotheses_with_rivals": sum(1 for h in hyps if h["data"].get("rivals")),
        "tests": {"registered": len(registered), "discriminating": len(disc), "informative": len(inf),
                  "informative_confirmations": out.get("passed", 0), "informative_refutations": out.get("failed", 0),
                  "informative_pending": out.get("pending", 0) + sum(1 for t in inf if t["id"] not in res_by_t)},
        "rates": {"informativeness_v2": (len(inf) / len(registered)) if registered else None,
                  "discrimination": (len(disc) / len(registered)) if registered else None},
        "validated_useful_discoveries": useful,
        "cost_per_validated_useful_discovery_usd": (cost / len(useful)) if useful else None,
        "information_gaps": {"n": len(st.get("information_gaps", [])),
                             "by_value": dict(Counter(g["data"]["expected_research_value"] for g in st.get("information_gaps", [])))},
        "critiques": {"n": len(crit), "by_dimension": dict(Counter(c["data"].get("dimension") for c in crit)),
                      "factual_or_numerical_citing_evidence": (sum(1 for c in fact if c["data"].get("evidence")) / len(fact))
                      if fact else None,
                      "rejected_without_evidence": sum(1 for f in f_items if f["failure_class"] == "validation.critique_needs_evidence")},
        "strategy": {"options_rejected_numeric_integrity": len(numeric_rej),
                     "protocol_eligibility": (proto or {}).get("data", {}).get("eligibility", {}).get("status")}}

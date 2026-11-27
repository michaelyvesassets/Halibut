"""Secondary, separately versioned evaluation readout ``r2_eval.v2.1`` (Architecture V2, milestone 2).

It is computed offline from the stored investigation documents and the prediction ledger
(``halibut.epistemic.predictions``), and is published NEXT TO the frozen R2 scores, never replacing them
(``evaluation.evaluate_document`` and the Step 2 criteria are unchanged). Thresholds are fixed here, in advance; there
is no tuning against results.

The dimensions are reported separately, with no composite score and no automatic causal credit:

| Dimension | Definition |
|---|---|
| originality | testable implications not all in the reference library (the frozen, library-based definition) |
| informativeness | a registered test is informative only if NO committed baseline predicts it. The baselines are persistence, momentum, Holt (share-like quantities) and a reference-class base rate, all computed from the ORIGIN evidence only. Fewer than 2 committed baselines makes the test "weakly assessed" |
| hypothesis quality | evidence consistency: the mechanism's claimed direction for the movement it explains, checked against the observed L4−P4 change (engine check). The critic's factual flags are reported as unverified and never used as a gate |
| predictive accuracy | resolved first-publication outcomes only. Hit rates over effective independent outcome groups, with Wilson 95% intervals. Skill tiers: consensus (every committed baseline also right), beats_some, beats_all (at least 2 committed baselines, all wrong) |
| calibration | assessable only with stated probabilities or valid Gaussian distributions; otherwise "not assessable" |
| mechanism discrimination | competing pairs classified as genuine, pure_negation, near_duplicate, not_same_observation, not_interpretable, no_opposition, or contradicted_at_registration. Only GENUINE pairs whose disputed tests resolved one way earn discrimination credit; plausibility is not machine-assessable and stays flagged as such |
| causal validity | "not assessed": no identification design. Predictive support is never causal evidence |
| decision relevance | options accepted and simulated, eligible protocols, and options linked to surviving useful results |
| computational efficiency | cost and tokens per investigation and per useful result (v2.1) |

**Temporal and verification separation:**
- retrospective, unpublished-historical, nowcast and future scores are kept apart;
- verified first-publication outcomes are kept apart from estimated (backfilled) ones;
- pending predictions are never scored;
- leakage risk is labelled per test against the model's training cutoff.
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from pathlib import Path

VERSION = "r2_eval.v2.1"
BEATS_ALL_MIN_BASELINES = 2
WILSON_Z = 1.959963984540054
TRAINING_CUTOFF = {"claude-haiku-5-5": "2026-06"}  # from configs/llm/anthropic.toml (official model docs)


def wilson(successes: float, n: int) -> list | None:
    if n == 0:
        return None
    p, z = successes / n, WILSON_Z
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [max(0.0, c - h), min(1.0, c + h)]


# ------------------------------------------------------------------------------------------------- baselines
def reference_class_rate(panel, template: str) -> float | None:
    """Share of historical 2-quarter windows (every entity, every origin with 8 quarters of history, ENTIRELY before
    the panel's last period) in which the pooled next-2 value exceeded the pooled last-4 value."""
    q = panel.periods
    ups = n = 0
    ents = ["market"] if template == "market_segment_share" else panel.entities
    for k in range(8, len(q) - 1):
        l4, nxt = q[k - 4:k], q[k:k + 2]
        for e in ents:
            a = panel.value(template, e, l4)[0]
            b = panel.value(template, e, nxt)[0]
            if a is None or b is None or abs(a - b) < 1e-15:
                continue
            n += 1
            ups += b > a
    return (ups / n) if n >= 20 else None


def _own_l4(spec, panel) -> bool:
    """The test compares the quantity with its OWN latest-window value (the form a reference class can price)."""
    src = spec.get("reference_source") or ""
    own = panel.metric_id(spec["template"], "L4", spec["entity"]) if spec["template"] != "segment_share_gap" else None
    return src == "L4 window" or (own is not None and src == own)


def extra_baselines(spec: dict, panel, rc_cache: dict) -> dict:
    """Holt and reference-class baselines for a binary test, from the origin panel only."""
    from ..models import registry as reg  # noqa: PLC0415
    from ..world.panel import SHARE_LIKE  # noqa: PLC0415
    from .compiler import holds  # noqa: PLC0415
    out, ref, comp = {}, spec["reference_value"], spec["comparator"]
    tgt = {"template": spec["template"], "entity": spec["entity"], "entity_b": spec.get("entity_b")}
    if spec["template"] in SHARE_LIKE:
        try:
            pt = reg.execute({"family": "stat.holt", "target": tgt}, panel)["point"]
            out["holt"] = {"point": pt, "predicts": None if abs(pt - ref) <= 1e-12 else bool(holds(pt, comp, ref))}
        except Exception as exc:  # noqa: BLE001  (typed unavailability; never invented)
            out["holt"] = {"point": None, "predicts": None, "note": str(exc)[:120]}
    if comp in ("increase_vs_L4", "decrease_vs_L4", "above_reference", "below_reference") and _own_l4(spec, panel):
        key = (id(panel), spec["template"])
        if key not in rc_cache:
            rc_cache[key] = reference_class_rate(panel, spec["template"])
        r = rc_cache[key]
        up = comp in ("increase_vs_L4", "above_reference")
        out["reference_class"] = {"rate_up": r, "predicts": None if r is None or abs(r - 0.5) < 1e-12 else
                                  ((r > 0.5) if up else (r < 0.5))}
    return out


# ------------------------------------------------------------------------------------------------- hypotheses
def evidence_consistency(h: dict, metrics: dict) -> dict:
    """Does the mechanism's claimed direction for the movement it explains agree with the observed L4-P4 change?"""
    d = h["data"]
    m = d.get("mechanism") or {}
    t, direction = m.get("effect_template"), m.get("direction")
    ent = None
    if d.get("explains"):
        parts = d["explains"].split(":")
        t2 = parts[1]
        ent = parts[3] if len(parts) > 3 else "market"
        t = t if t and t != "other" else t2
    if ent is None:
        imps = [i for i in d.get("observable_implications", []) if i.get("template") == t]
        ent = imps[0]["entity"] if imps else None
    if not t or t == "other" or not ent or direction not in ("increase", "decrease"):
        return {"status": "undetermined", "reason": "no comparable effect metric"}
    wid = "market_segment_share" if t == "market_segment_share" else t
    l4 = metrics.get(f"metric:{wid}:L4" + ("" if t == "market_segment_share" else f":{ent}"))
    p4 = metrics.get(f"metric:{wid}:P4" + ("" if t == "market_segment_share" else f":{ent}"))
    if l4 is None or p4 is None or abs(l4 - p4) < 1e-12:
        return {"status": "undetermined", "reason": "the explained metric did not move or is missing"}
    observed = "increase" if l4 > p4 else "decrease"
    if observed != direction:
        return {"status": "contradicted", "reason": f"claims {t} {ent} {direction}; the evidence shows {observed} "
                                                     f"({100 * p4:.2f}% to {100 * l4:.2f}%)"}
    return {"status": "consistent", "reason": f"{t} {ent} {observed} as claimed"}


def classify_pairs(hyps: list[dict], consistency: dict) -> list[dict]:
    from .evaluation import _pseudo  # noqa: PLC0415
    from .loop_v2 import DISTINCT_MAX_SIMILARITY, assess_pair, mechanism_similarity, quantity  # noqa: PLC0415
    out = []
    for i, a in enumerate(hyps):
        for b in hyps[i + 1:]:
            pa, pb = _pseudo(a), _pseudo(b)
            declared = pb["ref"] in (a["data"].get("rivals") or []) or pa["ref"] in (b["data"].get("rivals") or [])
            ass = assess_pair(pa, pb)
            qa = {quantity(x) for x in pa["observable_implications"] if x["template"] != "other"}
            qb = {quantity(x) for x in pb["observable_implications"] if x["template"] != "other"}
            if not declared and not (qa & qb):
                continue  # unrelated hypotheses: not a candidate pair
            reasons = " ".join(ass["reasons"])
            if ass["counted"]:
                bad = [x["data"]["ref"] for x in (a, b) if consistency[x["id"]]["status"] == "contradicted"]
                cls = "contradicted_at_registration" if bad else "genuine"
            elif "pure negation" in reasons:
                cls = "pure_negation"
            elif mechanism_similarity(pa, pb) > DISTINCT_MAX_SIMILARITY:
                cls = "near_duplicate"
            elif "same observation" in reasons:
                cls = "not_same_observation"
            elif "not interpretable" in reasons:
                cls = "not_interpretable"
            else:
                cls = "no_opposition"
            out.append({"pair": [a["data"]["ref"], b["data"]["ref"]], "ids": [a["id"], b["id"]], "declared_rivals": declared,
                        "class": cls, "disputed_quantities": ass["disputed_quantities"],
                        "contradicted": [x["data"]["ref"] for x in (a, b) if consistency[x["id"]]["status"] == "contradicted"],
                        "plausibility": "not machine-assessable (needs audited judgement)"})
    return out


# ------------------------------------------------------------------------------------------------- one document
def _ledger(store, test_ids):
    from ..epistemic.predictions import current  # noqa: PLC0415
    by = {x["source_id"]: x for x in current(store) if x["source_id"] in test_ids}
    return by


def evaluate(doc: dict, store, panel=None) -> dict:
    """The v2.1 readout of ONE investigation document (frozen scores untouched)."""
    from ..epistemic.predictions import outcome_group  # noqa: PLC0415
    st = doc["stages"]
    metrics = {m["id"]: m["data"]["value"] for m in doc["evidence"]["metrics"]}
    hyps, tests = st["hypotheses"], st["tests"]
    reg_tests = [t for t in tests if t["status"] == "registered"]
    led = _ledger(store, {t["id"] for t in reg_tests})
    rc_cache = {}
    model = (doc["agents"]["routing"].get("roles") or {}).get("hypothesis_generator", {}).get("model")
    cutoff = TRAINING_CUTOFF.get(model)
    cons = {h["id"]: evidence_consistency(h, metrics) for h in hyps}
    rows = {}
    for t in reg_tests:
        spec, x = t["data"]["spec"], led.get(t["id"])
        base = {}
        if x:
            pred = json.loads(store.conn.execute("SELECT baselines_json FROM predictions WHERE prediction_id=?",
                                                 (x["prediction_id"],)).fetchone()[0])
            base = {k: v for k, v in pred.items() if isinstance(v, dict)}
        if panel is not None:
            base.update(extra_baselines(spec, panel, rc_cache))
        committed = {k: v["predicts"] for k, v in base.items() if v.get("predicts") is not None}
        informative = bool(committed) and not any(committed.values())
        status = x["status"] if x else "not_in_ledger"
        hit = (x["outcome"] == "passed") if x and status == "resolved" else None
        tier = None
        if hit:
            wrong = [k for k, v in committed.items() if v != hit]
            tier = "beats_all" if len(committed) >= BEATS_ALL_MIN_BASELINES and len(wrong) == len(committed) else \
                ("beats_some" if wrong else "consensus")
        classes = t["data"].get("window_classes") or {}
        horizon = t["data"].get("horizon") or "standard"
        temporal = "future" if horizon == "future_window" else (
            "nowcast" if "partially_elapsed" in classes.values() else (
                "unpublished_historical" if "unpublished_historical" in classes.values() else (
                    x["temporal_class"] if x else "unknown")))
        end = max(spec["target_periods"])
        leak = "low" if temporal in ("future",) else ("medium" if temporal in ("nowcast", "unpublished_historical") else (
            "high" if cutoff and end[:4] + "-" + f"{int(end[-1]) * 3:02d}" <= cutoff else "medium"))
        hyp = next((ln["to"] for ln in t["links"] if ln["relation"] == "tests"), None)
        rows[t["id"]] = {"test": t["id"], "hypothesis": hyp, "label": t["label"][:120], "baselines": base,
                         "committed_baselines": len(committed), "informative": informative,
                         "weakly_assessed": len(committed) < BEATS_ALL_MIN_BASELINES, "status": status, "hit": hit,
                         "skill_tier": tier, "temporal": temporal, "basis": x["availability_basis"] if x else None,
                         "leakage_risk": leak, "outcome_group": outcome_group(doc["objective"]["market_id"], {
                             "template": spec["template"], "entity": spec["entity"], "entity_b": spec.get("entity_b"),
                             "target_periods": spec["target_periods"]}),
                         "quantity": [spec["template"], spec["entity"], spec.get("entity_b") or ""]}
    pairs = classify_pairs(hyps, cons)
    disc = []
    for p in pairs:
        if p["class"] != "genuine":
            continue
        for q in p["disputed_quantities"]:
            sides = [r for r in rows.values() if r["hypothesis"] in p["ids"] and r["quantity"] == list(q) and r["status"] == "resolved"]
            hits = {r["hypothesis"]: r["hit"] for r in sides}
            if len(hits) == 2 and set(hits.values()) == {True, False}:
                win = next(k for k, v in hits.items() if v)
                disc.append({"pair": p["pair"], "quantity": q, "winner": p["pair"][p["ids"].index(win)], "winner_id": win})
    disc_winners = {d["winner_id"] for d in disc}
    useful = []
    for h in hyps:
        mine = [r for r in rows.values() if r["hypothesis"] == h["id"]]
        orig = (h["data"].get("novelty_common") or h["data"].get("novelty")) == "original"
        if not orig or cons[h["id"]]["status"] == "contradicted" or any(r["hit"] is False for r in mine):
            continue
        if any(r["hit"] and r["skill_tier"] == "beats_all" for r in mine) or h["id"] in disc_winners:
            useful.append(h["data"]["ref"])
    crit_flags = defaultdict(list)
    for c in st["critiques"]:
        if c["data"].get("dimension") == "factual_contradiction" and c["data"].get("severity") == "high":
            for ln in c["links"]:
                if ln["relation"] == "critiques":
                    crit_flags[ln["to"]].append(c["label"][:160])
    proto = doc["experiment_protocol"]
    cost = float(doc["agents"]["costs"].get("actual_cost_usd") or 0)
    return {"version": VERSION, "investigation_id": doc["investigation_id"], "contract": doc["provenance"]["prompt_version"],
            "as_of": doc["objective"]["as_of"], "market_id": doc["objective"]["market_id"],
            "originality": {"original": sum(1 for h in hyps if (h["data"].get("novelty_common") or h["data"].get("novelty"))
                                            == "original"), "accepted": len(hyps)},
            "hypothesis_quality": {h["data"]["ref"]: {**cons[h["id"]], "critic_flags_unverified": crit_flags.get(h["id"], [])}
                                   for h in hyps},
            "tests": list(rows.values()), "pairs": pairs, "discriminating_resolutions": disc,
            "useful_v21": useful,
            "causal_validity": "not assessed: no identification design (predictive support is not causal evidence)",
            "calibration": "not assessable: categorical claims (no stated probabilities)",
            "decision_relevance": {"options_accepted": len(st["strategic_options"]),
                                   "options_simulated": sum(1 for o in st["strategic_options"]
                                                            if (o["data"].get("simulation") or {}).get("status", "simulated") == "simulated"),
                                   "eligible_protocols": int(bool(proto and proto["data"].get("eligibility", {}).get("eligible_for_execution")))},
            "efficiency": {"cost_usd": cost, "input_tokens": doc["agents"]["costs"].get("input_tokens"),
                           "output_tokens": doc["agents"]["costs"].get("output_tokens")}}


# ------------------------------------------------------------------------------------------------- aggregation
def aggregate(evals: list[dict], by_experiment: bool = False) -> dict:
    """Pooled readout by contract (or by experiment and contract) with effective sample sizes (outcome groups) and
    Wilson intervals, separately per temporal class and verification basis."""
    out = {}
    key = (lambda e: f"{e.get('file', '').split('/')[0]}|{e['contract']}") if by_experiment else (lambda e: e["contract"])  # noqa: E731
    for contract in sorted({key(e) for e in evals}):
        E = [e for e in evals if key(e) == contract]
        T = [t for e in E for t in e["tests"]]
        cell = {}
        for (temporal, basis), ts in _group(T, lambda t: (t["temporal"], t["basis"] or "pending")).items():
            res = [t for t in ts if t["status"] == "resolved"]
            og = defaultdict(list)
            for t in res:
                og[t["outcome_group"]].append(1.0 if t["hit"] else 0.0)
            n_eff, succ = len(og), sum(sum(v) / len(v) for v in og.values())
            cell[f"{temporal}|{basis}"] = {
                "registered": len(ts), "resolved": len(res), "pending": sum(1 for t in ts if t["status"] == "pending"),
                "effective_independent_outcomes": n_eff,
                "hit_rate": (succ / n_eff) if n_eff else None, "hit_rate_wilson95": wilson(succ, n_eff),
                "informative": sum(1 for t in ts if t["informative"]),
                "informative_and_weakly_assessed": sum(1 for t in ts if t["informative"] and t["weakly_assessed"]),
                "skill_tiers": dict(Counter(t["skill_tier"] for t in res if t["skill_tier"])),
                "baseline_hit_rates": _baseline_rates(res), "leakage_risk": dict(Counter(t["leakage_risk"] for t in ts))}
        cost = sum(e["efficiency"]["cost_usd"] for e in E)
        useful = sum(len(e["useful_v21"]) for e in E)
        out[contract] = {"investigations": len(E), "originality": {
            "original": sum(e["originality"]["original"] for e in E), "accepted": sum(e["originality"]["accepted"] for e in E)},
            "evidence_contradicted_hypotheses": sum(1 for e in E for v in e["hypothesis_quality"].values()
                                                    if v["status"] == "contradicted"),
            "pairs": dict(Counter(p["class"] for e in E for p in e["pairs"])),
            "discriminating_resolutions": sum(len(e["discriminating_resolutions"]) for e in E),
            "useful_v21": useful, "cost_usd": cost, "cost_per_useful_v21_usd": (cost / useful) if useful else None,
            "by_temporal_and_basis": cell}
    return {"version": VERSION, "by_contract": out,
            "note": "secondary readout; the frozen R2 scores are unchanged; pending tests are never scored; small samples"}


def _group(items, key):
    g = defaultdict(list)
    for x in items:
        g[key(x)].append(x)
    return g


def _baseline_rates(res):
    out = {}
    for t in res:
        for k, v in t["baselines"].items():
            if v.get("predicts") is None:
                continue
            o = out.setdefault(k, [0, 0])
            o[0] += v["predicts"] == t["hit"]
            o[1] += 1
    return {k: {"hit_rate": a / n, "n": n} for k, (a, n) in out.items()}


def evaluate_dirs(store, experiment_dirs, markets_dir="configs/intel/markets") -> dict:
    """The v2.1 readout of every investigation in the given experiment output directories."""
    import dataclasses  # noqa: PLC0415
    from ..epistemic.predictions import _market, market_paths  # noqa: PLC0415
    from ..intel.market import load_market  # noqa: PLC0415
    from ..world.panel import build_panel  # noqa: PLC0415
    markets, panels, evals = market_paths(markets_dir), {}, []
    for d in experiment_dirs:
        for f in sorted(Path(d).glob("investigations/*.json")):
            doc = json.loads(f.read_text())
            mp = _market(markets, doc["objective"]["market_id"], doc["provenance"]["market_spec_sha256"])
            k = (str(mp), doc["objective"]["as_of"])
            if mp and k not in panels:
                m = dataclasses.replace(load_market(mp), as_of=doc["objective"]["as_of"][:10])
                panels[k] = build_panel(store, m, doc["objective"]["as_of"])
            evals.append({**evaluate(doc, store, panels.get(k)), "file": f"{Path(d).name}/{f.name}"})
    return {"evaluations": evals, "aggregate": aggregate(evals), "by_experiment": aggregate(evals, by_experiment=True)}

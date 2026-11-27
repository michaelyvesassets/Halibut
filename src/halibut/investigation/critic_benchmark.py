"""Blinded critic benchmark: planted defects and clean controls, scored structurally (R2 Step 2).

The engine (never an agent) builds hypothesis sets from the market's REAL evidence. In each set, some hypotheses
carry ONE planted defect of a known type and the rest are clean controls: correct numbers, valid tests, and
assumptions consistent with the evidence.

Blinding:
- the critic receives the hypotheses exactly as in an investigation (critic payload of loop.v2);
- references are shuffled with a seed, and nothing marks a defect;
- the answer key is kept apart from the payload, and its sha256 is pre-registered.

Planted defect types and what counts as detection (a critique on the planted hypothesis whose dimension is in the
accepted set; for evidence-bound types it must also cite one of the expected metrics):

| Type | Planted defect | Detected by |
|---|---|---|
| contradicted_assumption | an assumption contradicted by an evidence metric (e.g. "substantial non-segment sales" for a 100%-segment brand) | factual_contradiction, citing that brand's segment_mix or non_segment_share |
| wrong_number | a quoted value about 1.5 times the true one | numerical_accuracy (or factual_contradiction), citing the metric |
| misencoded_test | a share gap compared with a single brand's level | test_validity |
| unsupported_detail | a mechanism resting on facts absent from the evidence (launches, price cuts) | unsupported_detail |
| trend_only_test | a causal mechanism whose only test is trend continuation | causal_identification or test_validity |

Scores (no keywords; excessive criticism is penalised):
- recall: share of planted defects detected, overall and by type;
- precision: share of medium or high critiques that detect a planted defect;
- clean false-alarm rate: share of clean controls with at least one HIGH-severity critique;
- excess: medium or high critiques on clean controls.
"""

from __future__ import annotations

import hashlib
import json
import random

VERSION = "critic_benchmark.v1"
ACCEPT = {"contradicted_assumption": ("factual_contradiction",), "wrong_number": ("numerical_accuracy", "factual_contradiction"),
          "misencoded_test": ("test_validity",), "unsupported_detail": ("unsupported_detail",),
          "trend_only_test": ("causal_identification", "test_validity")}


def _h(x) -> str:
    return hashlib.sha256(json.dumps(x, sort_keys=True, default=str).encode()).hexdigest()


def _imp(i, t, e, c, eb="", rm=""):
    return {"id": i, "template": t, "entity": e, "entity_b": eb, "comparator": c, "reference_metric": rm,
            "description": f"{t} {e}{'-' + eb if eb else ''} {c}", "would_refute_if": "the opposite movement"}


def _hyp(statement, cause, pathway, effect_t, direction, assumptions, evidence, imps):
    return {"statement": statement, "explains": evidence[0], "rivals": [],
            "mechanism": {"cause": cause, "pathway": pathway, "effect_template": effect_t, "direction": direction},
            "assumptions": [{"statement": a, "kind": "market_structure"} for a in assumptions],
            "supporting_evidence": evidence, "observable_implications": imps, "novelty": "original"}


def build(panel, metrics: dict, seed: int = 0) -> dict:
    """Two blinded sets from the panel's evidence: each has 3 planted defects and 2 clean controls."""
    pct = lambda mid: 100 * metrics[mid]["value"]  # noqa: E731
    mid = panel.metric_id
    ents = [e for e in panel.entities if mid("segment_share", "L4", e) in metrics and mid("segment_mix", "L4", e) in metrics]
    pure = [e for e in ents if metrics[mid("segment_mix", "L4", e)]["value"] >= 0.99]
    if len(ents) < 3 or not pure:
        raise ValueError("the panel does not support the benchmark (needs a single-segment brand and two other brands)")
    rng = random.Random(seed)
    p = pure[0]
    a, b = [e for e in ents if e != p][:2]
    up = lambda e: "increase_vs_L4" if metrics[mid("segment_share", "L4", e)]["value"] >= \
        metrics[mid("segment_share", "P4", e)]["value"] else "decrease_vs_L4"  # noqa: E731
    defects = [
        ("contradicted_assumption", {mid("segment_mix", "L4", p), mid("segment_mix", "P4", p),
                                     f"metric:non_segment_share:L4:{p}", f"metric:non_segment_share:P4:{p}"},
         _hyp(f"{p}'s total share is supported by substantial sales outside the segment, which cushion its segment losses.",
              "a broad non-segment range", "non-segment volume offsets segment share changes", "share", "increase",
              [f"{p} sells substantial volumes outside the segment"], [mid("share", "L4", p), mid("segment_share", "L4", p)],
              [_imp("I1", "share", p, "increase_vs_L4")])),
        ("wrong_number", {mid("segment_share", "L4", a)},
         _hyp(f"{a}'s segment share reached {1.5 * pct(mid('segment_share', 'L4', a)):.2f}% in the latest window because its "
              "dealer network expanded.", "dealer expansion", "more outlets raise availability", "segment_share", "increase",
              ["dealer coverage drives segment share"], [mid("segment_share", "L4", a)],
              [_imp("I1", "segment_share", a, up(a))])),
        ("misencoded_test", set(),
         _hyp(f"{a} will keep a lead over {b} within the segment.", "portfolio fit", "a better-fitting range keeps buyers",
              "segment_share", "increase", ["buyers compare the two brands directly"],
              [mid("segment_share", "L4", a), mid("segment_share", "L4", b)],
              [_imp("I1", "segment_share_gap", a, "above_reference", b, mid("segment_share", "L4", b))])),
        ("unsupported_detail", set(),
         _hyp(f"A wave of recent model launches and deep price cuts by {b} explains its segment movement.",
              "recent launches and price cuts", "new models and lower prices attract buyers", "segment_share", "increase",
              [f"{b} launched several new models and cut prices this year"], [mid("segment_share", "L4", b)],
              [_imp("I1", "segment_share", b, up(b))])),
        ("trend_only_test", set(),
         _hyp(f"Brand loyalty causes {b}'s segment trend, so it will continue.", "brand loyalty",
              "loyal buyers repeat their choice", "segment_share", "increase", ["loyalty is the cause of the trend"],
              [mid("segment_share", "L4", b), mid("segment_share", "P4", b)], [_imp("I1", "segment_share", b, up(b))])),
    ]
    clean = [
        _hyp(f"{a}'s segment share moved from {pct(mid('segment_share', 'P4', a)):.2f}% to "
             f"{pct(mid('segment_share', 'L4', a)):.2f}%, with a segment mix of {pct(mid('segment_mix', 'L4', a)):.2f}%; "
             "its range fit to the segment explains the movement.",
             "range concentration in the segment", "segment growth lifts brands concentrated in it", "segment_share",
             "increase", [f"{a}'s range keeps its segment concentration"],
             [mid("segment_share", "L4", a), mid("segment_share", "P4", a), mid("segment_mix", "L4", a)],
             [_imp("I1", "segment_mix", a, "increase_vs_L4"), _imp("I2", "segment_share_gap", a, "increase_vs_L4", b)]),
        _hyp(f"{b}'s segment position and its total share move together, so its segment changes are not a mix artefact.",
             "segment and total demand are linked", "segment demand drives total registrations", "share", "increase",
             [f"{b}'s segment mix is {pct(mid('segment_mix', 'L4', b)):.2f}%"],
             [mid("share", "L4", b), mid("segment_mix", "L4", b)],
             [_imp("I1", "share", b, "increase_vs_L4"), _imp("I2", "segment_share", b, "increase_vs_L4")]),
        _hyp(f"{p} sells only in the segment ({pct(mid('segment_mix', 'L4', p)):.2f}% segment mix), so its total share "
             "follows its segment share.", "single-segment range", "no non-segment sales", "share", "increase",
             [f"{p}'s segment mix stays at {pct(mid('segment_mix', 'L4', p)):.2f}%"], [mid("segment_mix", "L4", p)],
             [_imp("I1", "share", p, "increase_vs_L4"), _imp("I2", "segment_share", p, "increase_vs_L4")]),
        _hyp(f"The segment's share of the market keeps rising, from {pct('metric:market_segment_share:P4'):.2f}% to "
             f"{pct('metric:market_segment_share:L4'):.2f}%, lifting {a} more than {b}.", "segment adoption",
             "adoption favours brands concentrated in the segment", "segment_share", "increase",
             ["adoption continues"], ["metric:market_segment_share:L4", "metric:market_segment_share:P4"],
             [_imp("I1", "market_segment_share", "market", "increase_vs_L4"),
              _imp("I2", "segment_share_gap", a, "increase_vs_L4", b)]),
    ]
    sets, key = [], {}
    order = [(d[0], d[1], d[2]) for d in defects] + [("clean", set(), c) for c in clean]
    groups = [order[0:3] + order[5:7], order[3:5] + order[7:9]]
    for si, grp in enumerate(groups):
        rng.shuffle(grp)
        hyps = []
        for j, (kind, ev, h) in enumerate(grp):
            ref = f"H{j + 1}"
            hyps.append({"ref": ref, **h})
            key[f"S{si + 1}:{ref}"] = {"kind": kind, "accept": list(ACCEPT.get(kind, ())), "evidence": sorted(ev)}
        sets.append({"set_id": f"S{si + 1}", "hypotheses": hyps})
    return {"version": VERSION, "seed": seed, "sets": sets, "key": key, "key_sha256": _h(key),
            "payload_sha256": _h(sets)}


def payload(bench_set: dict, agent_slice: dict) -> dict:
    """The critic bundle for one set: identical in form to a loop.v2 investigation's critic bundle (blinded)."""
    return {"contract": "loop.v2", "slice": {k: agent_slice[k] for k in ("market", "metrics", "metric_id_format", "templates")},
            "hypotheses": bench_set["hypotheses"]}


def score(critiques_by_set: dict, bench: dict) -> dict:
    """``critiques_by_set``: set id -> list of accepted critiques ({hypothesis_ref, dimension, severity, evidence})."""
    found, by_type = {}, {}
    tp_crit, n_med_high, excess, clean_alarm = 0, 0, 0, set()
    for k, v in bench["key"].items():
        if v["kind"] != "clean":
            found[k] = False
    for sid, crits in critiques_by_set.items():
        for c in crits:
            k = f"{sid}:{c['hypothesis_ref']}"
            v = bench["key"].get(k)
            if v is None:
                continue
            serious = c["severity"] in ("medium", "high")
            n_med_high += serious
            if v["kind"] == "clean":
                excess += serious
                if c["severity"] == "high":
                    clean_alarm.add(k)
                continue
            ok = c["dimension"] in v["accept"] and (not v["evidence"] or bool(set(c.get("evidence", [])) & set(v["evidence"])))
            if ok:
                found[k] = True
                tp_crit += serious
    for k, hit in found.items():
        t = bench["key"][k]["kind"]
        d = by_type.setdefault(t, [0, 0])
        d[0] += hit
        d[1] += 1
    n_clean = sum(1 for v in bench["key"].values() if v["kind"] == "clean")
    by_dim, ev_bound, ev_right, clean_any = {}, 0, 0, set()
    for sid, crits in critiques_by_set.items():
        for c in crits:
            v = bench["key"].get(f"{sid}:{c['hypothesis_ref']}")
            if v is None or c["severity"] not in ("medium", "high"):
                continue
            d = by_dim.setdefault(c["dimension"], [0, 0])
            d[1] += 1
            d[0] += v["kind"] != "clean" and c["dimension"] in v["accept"] and (
                not v["evidence"] or bool(set(c.get("evidence", [])) & set(v["evidence"])))
            if v["kind"] == "clean":
                clean_any.add(f"{sid}:{c['hypothesis_ref']}")
            if v["evidence"] and c.get("evidence"):
                ev_bound += 1
                ev_right += bool(set(c["evidence"]) & set(v["evidence"]))
    return {"version": VERSION, "planted": len(found), "detected": sum(found.values()),
            "precision_by_dimension": {k: x[0] / x[1] for k, x in sorted(by_dim.items())},
            "evidence_reference_accuracy": (ev_right / ev_bound) if ev_bound else None,
            "clean_controls_with_any_medium_or_high_critique": len(clean_any),
            "recall": (sum(found.values()) / len(found)) if found else None,
            "recall_by_type": {t: d[0] / d[1] for t, d in sorted(by_type.items())},
            "precision": (tp_crit / n_med_high) if n_med_high else None,
            "clean_false_alarm_rate": len(clean_alarm) / n_clean if n_clean else None,
            "excess_critiques_on_clean": excess}


# =============================================================================================== v2 (milestone 2)
VERSION_V2 = "critic_benchmark.v2"
DEFECT_CLAIM_DIMS = ("factual_contradiction", "numerical_accuracy", "test_validity", "unsupported_detail",
                     "causal_identification")
CAVEAT_DIMS = ("alternative_explanation",)  # generic caveats: neither credited nor counted as false positives
EXPECTED_SEVERITY = {"contradicted_assumption": "high", "wrong_number": "high", "misencoded_test": "high",
                     "unsupported_detail": "medium", "trend_only_test": "medium"}
SEV = {"low": 0, "medium": 1, "high": 2}
AUDIT_CHECKLIST = ["the item carries exactly the stated defect and no other", "every number in a clean item equals the evidence",
                   "every mechanism refers only to quantities in the evidence (no external facts)",
                   "the expected dimension and evidence ids are the only correct ones"]


def _grounded(statement, cause, pathway, effect_t, direction, assumptions, evidence, imps):
    """A hypothesis whose mechanism refers only to evidence quantities (no launches, prices, dealers, loyalty)."""
    return _hyp(statement, cause, pathway, effect_t, direction, assumptions, evidence, imps)


def build_v2(panel, metrics: dict, seed: int = 0) -> dict:
    """PURIFIED blinded sets: one planted defect per item and clean controls whose every number equals the evidence and
    whose mechanisms are relations between evidence quantities only. Items start as 'proposed' and must be audited by
    a person (blind to critic outputs) before any score is called audited."""
    pct = lambda mid: 100 * metrics[mid]["value"]  # noqa: E731
    mid = panel.metric_id
    ents = [e for e in panel.entities if mid("segment_share", "L4", e) in metrics and mid("segment_mix", "L4", e) in metrics]
    pure = [e for e in ents if metrics[mid("segment_mix", "L4", e)]["value"] >= 0.99]
    if len(ents) < 3 or not pure:
        raise ValueError("the panel does not support the benchmark (needs a single-segment brand and two other brands)")
    rng, p = random.Random(seed), pure[0]
    a, b = [e for e in ents if e != p][:2]
    up = lambda e: "increase_vs_L4" if metrics[mid("segment_share", "L4", e)]["value"] >= \
        metrics[mid("segment_share", "P4", e)]["value"] else "decrease_vs_L4"  # noqa: E731
    seg = ("growth of the segment's share of the market", "a larger segment raises the brand's segment units")
    ms_l4, ms_p4 = "metric:market_segment_share:L4", "metric:market_segment_share:P4"
    defects = [
        ("contradicted_assumption", {mid("segment_mix", "L4", p), mid("segment_mix", "P4", p),
                                     f"metric:non_segment_share:L4:{p}", f"metric:non_segment_share:P4:{p}"},
         _grounded(f"{p}'s total share is supported by substantial sales outside the segment.", *seg, "share", "increase",
                   [f"{p} sells substantial volumes outside the segment"], [mid("share", "L4", p)],
                   [_imp("I1", "share", p, up(p))])),
        ("wrong_number", {mid("segment_share", "L4", a)},
         _grounded(f"{a}'s segment share reached {1.5 * pct(mid('segment_share', 'L4', a)):.2f}% in the latest window as the "
                   "segment grew.", *seg, "segment_share", "increase", ["segment growth continues"],
                   [mid("segment_share", "L4", a), ms_l4], [_imp("I1", "segment_share", a, up(a))])),
        ("misencoded_test", set(),
         _grounded(f"{a}'s segment share will stay above {b}'s as the segment grows.", *seg, "segment_share", "increase",
                   ["segment growth continues"], [mid("segment_share", "L4", a), mid("segment_share", "L4", b)],
                   [_imp("I1", "segment_share_gap", a, "above_reference", b, mid("segment_share", "L4", b))])),
        ("unsupported_detail", set(),
         _hyp(f"A wave of recent model launches by {b} explains its segment movement.", "recent model launches",
              "new models attract buyers", "segment_share", "increase", [f"{b} launched several new models this year"],
              [mid("segment_share", "L4", b)], [_imp("I1", "segment_share", b, up(b))])),
        ("trend_only_test", set(),
         _grounded(f"Segment growth causes {b}'s segment movement, so it will continue.", *seg, "segment_share", "increase",
                   ["segment growth is the cause"], [mid("segment_share", "L4", b), ms_l4],
                   [_imp("I1", "segment_share", b, up(b))])),
    ]
    clean = [
        _grounded(f"{a}'s segment share moved from {pct(mid('segment_share', 'P4', a)):.2f}% to "
                  f"{pct(mid('segment_share', 'L4', a)):.2f}% while the segment's market share moved from {pct(ms_p4):.2f}% "
                  f"to {pct(ms_l4):.2f}%.", *seg, "segment_share", "increase", ["segment growth continues"],
                  [mid("segment_share", "L4", a), mid("segment_share", "P4", a), ms_l4, ms_p4],
                  [_imp("I1", "market_segment_share", "market", "increase_vs_L4" if metrics[ms_l4]["value"] >= metrics[ms_p4]["value"]
                        else "decrease_vs_L4"), _imp("I2", "segment_share", a, up(a))]),
        _grounded(f"{p} sells only in the segment ({pct(mid('segment_mix', 'L4', p)):.2f}% segment mix), so its total share "
                  "moves with the segment.", *seg, "share", "increase", [f"{p}'s segment mix stays at "
                                                                         f"{pct(mid('segment_mix', 'L4', p)):.2f}%"],
                  [mid("segment_mix", "L4", p), ms_l4], [_imp("I1", "segment_mix", p, "increase_vs_L4")]),
    ]
    sets, key = [], {}
    order = [(d[0], d[1], d[2]) for d in defects] + [("clean", set(), c) for c in clean]
    groups = [order[0:3] + order[5:6], order[3:5] + order[6:7]]
    for si, grp in enumerate(groups):
        rng.shuffle(grp)
        hyps = []
        for j, (kind, ev, h) in enumerate(grp):
            ref = f"H{j + 1}"
            hyps.append({"ref": ref, **h})
            key[f"S{si + 1}:{ref}"] = {"kind": kind, "accept": list(ACCEPT.get(kind, ())), "evidence": sorted(ev),
                                       "expected_severity": EXPECTED_SEVERITY.get(kind)}
        sets.append({"set_id": f"S{si + 1}", "hypotheses": hyps})
    return {"version": VERSION_V2, "seed": seed, "sets": sets, "key": key, "key_sha256": _h(key), "payload_sha256": _h(sets),
            "audit": {"status": "proposed", "auditor": None, "checklist": AUDIT_CHECKLIST,
                      "rule": "an independent person, blind to critic outputs, confirms every item before scores are 'audited'"}}


def score_v2(critiques_by_set: dict, bench: dict, schema_failures: int = 0) -> dict:
    """Structured scores. Each planted item is judged by its BEST critique; extra critiques never add credit:
    - detection: any defect-claim critique on the item;
    - classification: its dimension is an accepted one;
    - severity: at least the expected severity;
    - evidence: an evidence-bound item cites an expected metric;
    - false positives: medium/high defect claims on CLEAN items (generic caveats are reported apart);
    - misdirected: high-severity defect claims of a wrong dimension on planted items;
    - schema failures: critiques rejected by the schema (passed in)."""
    items = {k: v for k, v in bench["key"].items()}
    per = {k: [] for k in items}
    for sid, crits in critiques_by_set.items():
        for c in crits:
            k = f"{sid}:{c['hypothesis_ref']}"
            if k in per:
                per[k].append(c)
    det = cls_ok = sev_ok = ev_ok = ev_n = fp = caveats = misdirected = 0
    by_type = {}
    planted = [k for k, v in items.items() if v["kind"] != "clean"]
    for k, v in items.items():
        cs = per[k]
        claims = [c for c in cs if c["dimension"] in DEFECT_CLAIM_DIMS]
        caveats += sum(1 for c in cs if c["dimension"] in CAVEAT_DIMS)
        if v["kind"] == "clean":
            fp += sum(1 for c in claims if SEV[c["severity"]] >= 1)
            continue
        t = by_type.setdefault(v["kind"], {"n": 0, "detected": 0, "classified": 0, "severity_ok": 0})
        t["n"] += 1
        right = [c for c in claims if c["dimension"] in v["accept"]]
        misdirected += sum(1 for c in claims if c["dimension"] not in v["accept"] and c["severity"] == "high")
        if claims:
            det += 1
            t["detected"] += 1
        if right:
            best = max(right, key=lambda c: (bool(set(c.get("evidence", [])) & set(v["evidence"])), SEV[c["severity"]]))
            cls_ok += 1
            t["classified"] += 1
            if SEV[best["severity"]] >= SEV[v["expected_severity"] or "medium"]:
                sev_ok += 1
                t["severity_ok"] += 1
            if v["evidence"]:
                ev_n += 1
                ev_ok += bool(set(best.get("evidence", [])) & set(v["evidence"]))
    n, clean = len(planted), sum(1 for v in items.values() if v["kind"] == "clean")
    return {"version": VERSION_V2, "audited": (bench.get("audit") or {}).get("status") == "audited",
            "label": "AUDITED" if (bench.get("audit") or {}).get("status") == "audited" else "UNAUDITED (diagnostic only)",
            "planted": n, "clean_controls": clean,
            "detection_rate": det / n if n else None, "classification_rate": cls_ok / n if n else None,
            "severity_accuracy": sev_ok / cls_ok if cls_ok else None,
            "evidence_reference_accuracy": ev_ok / ev_n if ev_n else None,
            "false_positives_on_clean": fp, "false_positive_rate_per_clean": fp / clean if clean else None,
            "misdirected_high_severity": misdirected, "generic_caveats": caveats, "schema_failures": schema_failures,
            "by_type": by_type}

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

import copy
import hashlib
import json
import random
import re

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


# =============================================================================================== v3 (canonical first)
VERSION_V3 = "critic_benchmark.v3"
SEVERITY_RUBRIC = {
    "high": "the claim is contradicted by the evidence, mathematically impossible, rests on a wrong central number, or its "
            "test cannot measure what it claims",
    "medium": "the claim relies on facts or causal inferences the evidence does not establish, or confuses two quantities "
              "in a way that does not by itself contradict the evidence",
    "low": "a weakness worth noting that does not undermine the claim (e.g. limited power, a plausible alternative)"}
# kind -> accepted dimensions (any is a correct classification), minimum acceptable severity, ideal severity
KINDS_V3 = {
    "wrong_number": (("numerical_accuracy", "factual_contradiction"), "medium", "high"),
    "quantity_confusion": (("numerical_accuracy", "factual_contradiction"), "medium", "high"),
    "contradicted_assumption": (("factual_contradiction",), "medium", "high"),
    "impossible_prediction": (("test_validity", "factual_contradiction", "numerical_accuracy"), "medium", "high"),
    "misencoded_test": (("test_validity",), "medium", "high"),
    "unsupported_detail": (("unsupported_detail", "causal_identification"), "medium", "medium"),
    "causal_overclaim": (("causal_identification", "test_validity", "unsupported_detail"), "medium", "medium"),
}
CHECKABLE_FACTS = ("factual_contradiction", "numerical_accuracy")  # claims that are false by construction on a clean item
EXTERNAL_FACT_WORDS = ("launch", "price", "dealer", "fleet", "incentive", "advert", "loyal")
CAUSAL_WORDS = ("cause", "causes", "caused", "driven by", "because of", "demonstrate", "proves", "due to")


def _num(s):
    return [float(x) for x in re.findall(r"(?<![\w.])(\d+(?:\.\d+)?)%", s)]


def check_item(h: dict, metrics: dict) -> list[str]:
    """Deterministic boundary and consistency checks of ONE hypothesis against the evidence. A clean item returns []."""
    issues = []
    text = " ".join([h["statement"], h["mechanism"]["cause"], h["mechanism"]["pathway"]] + [a["statement"] for a in h["assumptions"]])
    low = text.lower()
    tmpl_words = {"segment share": "segment_share", "segment mix": "segment_mix", "total share": "share"}
    for sent in re.split(r"(?<=\.)\s+", h["statement"].lower()):  # every % in a sentence naming a quantity is that quantity
        word = next((w for w in tmpl_words if w in sent), None)
        if word is None:
            continue
        tmpl = tmpl_words[word]
        mine = [100 * x["value"] for k, x in metrics.items() if k.split(":")[1] == tmpl and k.split(":")[-1] in sent]
        for v in _num(sent):
            if not any(abs(v - y) < 0.006 for y in mine):
                others = [k for k, x in metrics.items() if abs(100 * x["value"] - v) < 0.006 and k.split(":")[1] != tmpl
                          and k.split(":")[-1] in sent]
                issues.append("quantity_confusion" if others else "number_mismatch")
    m = h["mechanism"]
    for i in h["observable_implications"]:
        d = DIRECTION_OF.get(i["comparator"])
        if i["template"] == m["effect_template"] and i["entity"] == h["observable_implications"][0]["entity"] and d and d != m["direction"]:
            issues.append("direction_inconsistent")
        if i["template"] in ("segment_mix", "segment_share", "share") and i["comparator"] in ("increase_vs_L4", "decrease_vs_L4"):
            v = metrics.get(f"metric:{i['template']}:L4:{i['entity']}")
            if v and ((v["value"] >= 0.9999 and d == "increase") or (v["value"] <= 1e-6 and d == "decrease")):
                issues.append("impossible_prediction")
        if i["template"] == "segment_share_gap" and i["comparator"] in ("above_reference", "below_reference"):
            issues.append("misencoded_test")
    for a in h["assumptions"]:
        if "outside the segment" in a["statement"].lower():
            e = h["observable_implications"][0]["entity"]
            mix = metrics.get(f"metric:segment_mix:L4:{e}")
            if mix and mix["value"] >= 0.99:
                issues.append("contradicted_assumption")
    if any(w in low for w in EXTERNAL_FACT_WORDS):
        issues.append("external_fact")
    if any(w in low for w in CAUSAL_WORDS):
        issues.append("causal_claim")
    return sorted(set(issues))


DIRECTION_OF = {"increase_vs_L4": "increase", "above_reference": "increase", "decrease_vs_L4": "decrease",
                "below_reference": "decrease"}
EXPECTED_ISSUES = {"wrong_number": ["number_mismatch"], "quantity_confusion": ["quantity_confusion"],
                   "contradicted_assumption": ["contradicted_assumption"], "impossible_prediction": ["impossible_prediction"],
                   "misencoded_test": ["misencoded_test"], "unsupported_detail": ["causal_claim", "external_fact"],
                   "causal_overclaim": ["causal_claim"]}


def canonical(e: str, metrics: dict) -> dict:
    """A clean continuation claim about ONE quantity (segment share): correct numbers, no causal claim, direction
    consistent everywhere, no boundary issue."""
    l4, p4 = metrics[f"metric:segment_share:L4:{e}"]["value"], metrics[f"metric:segment_share:P4:{e}"]["value"]
    d = "increase" if l4 > p4 else "decrease"
    return _hyp(f"{e}'s segment share (its share of the segment's registrations) moved from {100 * p4:.2f}% to "
                f"{100 * l4:.2f}%. This hypothesis expects the same direction over the next two quarters; it makes no "
                "claim about why.",
                "short-run persistence of the recent within-segment movement",
                "a brand's within-segment share tends to keep its recent direction for a short time", "segment_share", d,
                ["the recent direction persists for two quarters"],
                [f"metric:segment_share:L4:{e}", f"metric:segment_share:P4:{e}"],
                [_imp("I1", "segment_share", e, "increase_vs_L4" if d == "increase" else "decrease_vs_L4")])


def mutate(kind: str, h: dict, e: str, metrics: dict, other: str | None = None) -> tuple[dict, list]:
    """ONE isolated change of a canonical item; returns the item and the accepted evidence ids."""
    x = copy.deepcopy(h)
    l4 = 100 * metrics[f"metric:segment_share:L4:{e}"]["value"]
    if kind == "wrong_number":
        x["statement"] = x["statement"].replace(f"to {l4:.2f}%", f"to {1.5 * l4:.2f}%")
        return x, [f"metric:segment_share:L4:{e}"]
    if kind == "quantity_confusion":
        mix = 100 * metrics[f"metric:segment_mix:L4:{e}"]["value"]
        x["statement"] = x["statement"].replace(f"to {l4:.2f}%", f"to {mix:.2f}%")
        return x, [f"metric:segment_share:L4:{e}", f"metric:segment_mix:L4:{e}"]
    if kind == "contradicted_assumption":
        x["assumptions"].append({"statement": f"{e} also sells substantial volumes outside the segment", "kind": "market_structure"})
        return x, [k for k in (f"metric:segment_mix:L4:{e}", f"metric:segment_mix:P4:{e}", f"metric:non_segment_share:L4:{e}",
                               f"metric:non_segment_share:P4:{e}") if k in metrics]
    if kind == "impossible_prediction":
        x["observable_implications"].append(_imp("I2", "segment_mix", e, "increase_vs_L4"))
        return x, [f"metric:segment_mix:L4:{e}"]
    if kind == "misencoded_test":
        x["observable_implications"] = [_imp("I1", "segment_share_gap", e, "above_reference", other,
                                             f"metric:segment_share:L4:{other}")]
        return x, []
    if kind == "unsupported_detail":
        x["statement"] = x["statement"].replace("it makes no claim about why.", "the movement is driven by recent model launches.")
        return x, []
    if kind == "causal_overclaim":
        x["statement"] = x["statement"].replace("it makes no claim about why.",
                                                "a continuation would demonstrate that the brand's appeal to segment buyers "
                                                "is the cause.")
        return x, []
    raise ValueError(kind)


def build_v3(panel, metrics: dict, seed: int = 0) -> dict:
    """Canonical items first, then isolated mutations; every item checked deterministically (canonical: no issue;
    mutation: exactly its intended issue). Four sets; within a set at most one item per brand, so no variant can be
    diffed against its own canonical."""
    mid = panel.metric_id
    ok = [e for e in panel.entities if all(k in metrics for k in (mid("segment_share", "L4", e), mid("segment_share", "P4", e),
                                                                mid("segment_mix", "L4", e)))
          and 0.001 < metrics[mid("segment_share", "L4", e)]["value"] < 0.999
          and abs(metrics[mid("segment_share", "L4", e)]["value"] - metrics[mid("segment_share", "P4", e)]["value"]) > 0.001]
    pure = [e for e in ok if metrics[mid("segment_mix", "L4", e)]["value"] >= 0.9999]
    mixed = [e for e in ok if metrics[mid("segment_mix", "L4", e)]["value"] < 0.99
             and abs(metrics[mid("segment_mix", "L4", e)]["value"] - metrics[mid("segment_share", "L4", e)]["value"]) > 0.005]
    if not pure or len(mixed) < 2:
        raise ValueError("benchmark v3 needs a single-segment brand and two mixed brands with moving segment shares")
    p, a, b = pure[0], mixed[0], mixed[1]
    C = {e: canonical(e, metrics) for e in (p, a, b)}
    for e, h in C.items():
        assert check_item(h, metrics) == [], (e, check_item(h, metrics))
    plan = [[("clean", p), ("wrong_number", a), ("unsupported_detail", b)],
            [("clean", a), ("quantity_confusion", b), ("contradicted_assumption", p)],
            [("clean", b), ("misencoded_test", a), ("impossible_prediction", p)],
            [("clean", p), ("causal_overclaim", b), ("clean", a)]]
    rng, sets, key = random.Random(seed), [], {}
    for si, row in enumerate(plan):
        items = []
        for kind, e in row:
            if kind == "clean":
                h, ev = C[e], []
            else:
                h, ev = mutate(kind, C[e], e, metrics, other=a if e != a else b)
                assert check_item(h, metrics) == EXPECTED_ISSUES[kind], (kind, check_item(h, metrics))
            items.append((kind, ev, h, e))
        rng.shuffle(items)
        hyps = []
        for j, (kind, ev, h, e) in enumerate(items):
            ref = f"H{j + 1}"
            hyps.append({"ref": ref, **h})
            acc, mn, ideal = KINDS_V3.get(kind, ((), None, None))
            key[f"S{si + 1}:{ref}"] = {"kind": kind, "entity": e, "accept": list(acc),
                                       "evidence": sorted(ev), "min_severity": mn, "ideal_severity": ideal,
                                       "deterministic_issues": check_item(h, metrics)}
        sets.append({"set_id": f"S{si + 1}", "hypotheses": hyps})
    return {"version": VERSION_V3, "seed": seed, "sets": sets, "key": key, "key_sha256": _h(key), "payload_sha256": _h(sets),
            "severity_rubric": SEVERITY_RUBRIC,
            "audit": {"status": "proposed", "auditor": None, "checklist": AUDIT_CHECKLIST + [
                "each defective item differs from its canonical by exactly one change",
                "absolute volume, segment share, total share and segment mix are not confused (except the planted confusion)"],
                "rule": "an independent person, blind to critic outputs, confirms every item before scores are 'audited'"}}


def score_v3(critiques_by_set: dict, bench: dict, schema_failures: int = 0) -> dict:
    """Detection, classification, severity and evidence are scored SEPARATELY on planted defects (one mutation = one
    defect, even when it shows several issues or allows several classifications). Any accepted dimension or evidence id
    counts.

    Every OTHER critique is recorded rather than scored as false:
    - on planted items, additional critiques are kept with a category for review;
    - on clean controls, factual/numerical claims at medium or higher are 'presumed_erroneous' (the item passed every
      deterministic check) but still listed for review, because a correct factual comment is not an error;
    - high-severity methodological critiques are listed for review on their merits, never auto-classified as false.

    The planted-only rates are NOT comprehensive precision: additional critiques are excluded until reviewed."""
    per = {k: [] for k in bench["key"]}
    for sid, crits in critiques_by_set.items():
        for c in crits:
            k = f"{sid}:{c['hypothesis_ref']}"
            if k in per:
                per[k].append(c)
    det = cls = sev = ev = ev_n = presumed = 0
    by_kind, review = {}, []
    planted = [k for k, v in bench["key"].items() if v["kind"] != "clean"]
    clean_keys = [k for k, v in bench["key"].items() if v["kind"] == "clean"]
    for k, v in bench["key"].items():
        cs = per[k]
        if v["kind"] == "clean":
            for c in cs:
                if c["dimension"] in CHECKABLE_FACTS and SEV[c["severity"]] >= 1:
                    presumed += 1
                    review.append({"item": k, "category": "presumed_erroneous_factual_on_clean", **c})
                elif c["severity"] == "high":
                    review.append({"item": k, "category": "high_methodological_on_clean_assess_on_merits", **c})
                else:
                    review.append({"item": k, "category": "methodological_comment_on_clean", **c})
            continue
        t = by_kind.setdefault(v["kind"], {"detected": 0, "classified": 0, "severity_ok": 0, "evidence_ok": None})
        if any(c["dimension"] in DEFECT_CLAIM_DIMS for c in cs):
            det += 1
            t["detected"] += 1
        right = [c for c in cs if c["dimension"] in v["accept"]]
        for c in cs:
            if c not in right:
                review.append({"item": k, "category": "additional_on_defective_item"
                               + ("_factual_needs_verification" if c["dimension"] in CHECKABLE_FACTS else "_methodological"), **c})
        if right:
            cls += 1
            t["classified"] += 1
            if max(SEV[c["severity"]] for c in right) >= SEV[v["min_severity"]]:
                sev += 1
                t["severity_ok"] += 1
            if v["evidence"]:
                ev_n += 1
                hit = any(set(c.get("evidence", [])) & set(v["evidence"]) for c in right)
                ev += hit
                t["evidence_ok"] = hit
    n = len(planted)
    uniq_clean = len({_h(next(h for s in bench["sets"] for h in s["hypotheses"] if f"{s['set_id']}:{h['ref']}" == k
                          ) | {"ref": ""}) for k in clean_keys}) if bench.get("sets") else None
    audited = (bench.get("audit") or {}).get("status") == "audited"
    return {"version": VERSION_V3, "audited": audited, "label": "AUDITED" if audited else "UNAUDITED (diagnostic only)",
            "planted_defects": n, "clean_control_entries": len(clean_keys), "unique_clean_hypotheses": uniq_clean,
            "note": "clean-control entries repeat hypotheses across sets: they are NOT independent samples; one mutation "
                    "is one defect even when it shows several issues",
            "detection_rate": det / n if n else None, "classification_rate": cls / n if n else None,
            "severity_accuracy": sev / cls if cls else None, "evidence_reference_accuracy": ev / ev_n if ev_n else None,
            "presumed_erroneous_on_clean": presumed, "critiques_for_review": review,
            "precision_note": "planted-only rates exclude additional critiques: not a comprehensive precision",
            "schema_failures": schema_failures, "by_kind": by_kind}


def audit_markdown(b: dict, metrics: dict, title: str) -> str:
    L = [f"# {title}", "", f"Benchmark `{b['version']}`; key sha256 `{b['key_sha256'][:16]}…`; payload sha256 "
         f"`{b['payload_sha256'][:16]}…`. **Audit status: {b['audit']['status'].upper()}.**", "",
         "**For the auditor only; never shown to the critic.** The critic receives the blinded sets, without this key. Audit "
         "each item blind to any critic output. Scores are labelled AUDITED only after every item is confirmed.", "",
         "## Construction", "", "- canonical clean items first, one per brand: a continuation claim about segment share only, "
         "with correct numbers, no causal claim, and the same direction in the mechanism and the test;",
         "- each defective item is ONE isolated mutation of a canonical item;",
         "- a deterministic checker (`check_item`) must find no issue in a canonical item and exactly the intended issue in "
         "each mutation;",
         "- within a set, at most one item per brand, so no variant can be compared with its own canonical;",
         "- 5 clean-control ENTRIES repeat 3 unique clean hypotheses across sets: they are not independent samples;",
         "- the critic input (`payload_v3`) states the L4 and P4 quarters, that every implication refers to the next two "
         "quarters after L4, and that the data are first registrations in the dataset, not company sales.", "",
         "## Severity rubric", ""] + [f"- **{k}**: {v}" for k, v in b["severity_rubric"].items()] + [
         "", "## Audit checklist (every item)", ""] + [f"- [ ] {c}" for c in b["audit"]["checklist"]]
    cited = sorted({e for s in b["sets"] for h in s["hypotheses"] for e in h["supporting_evidence"]} |
                   {e for v in b["key"].values() for e in v["evidence"]} |
                   {i["reference_metric"] for s in b["sets"] for h in s["hypotheses"] for i in h["observable_implications"]
                    if i["reference_metric"]})
    L += ["", "## Evidence used by the items", "", "| Metric | Value (%) |", "|---|---|"] + \
         [f"| `{c}` | {100 * metrics[c]['value']:.4f} |" for c in cited if c in metrics]
    for s in b["sets"]:
        L += ["", f"## Set {s['set_id']}"]
        for h in s["hypotheses"]:
            k = b["key"][f"{s['set_id']}:{h['ref']}"]
            L += ["", f"### {s['set_id']}:{h['ref']}: " + ("CLEAN CONTROL" if k["kind"] == "clean" else f"PLANTED DEFECT: {k['kind']}"),
                  "", f"- **Statement:** {h['statement']}",
                  f"- **Mechanism:** {h['mechanism']['cause']} → {h['mechanism']['pathway']} ({h['mechanism']['effect_template']}, "
                  f"{h['mechanism']['direction']})",
                  f"- **Assumptions:** {'; '.join(a['statement'] for a in h['assumptions'])}",
                  f"- **Supporting evidence:** {', '.join('`' + e + '`' for e in h['supporting_evidence'])}",
                  "- **Implications:** " + "; ".join(
                      f"{i['template']} {i['entity']}{('-' + i['entity_b']) if i['entity_b'] else ''} {i['comparator']}"
                      + (f" vs `{i['reference_metric']}`" if i["reference_metric"] else "") for i in h["observable_implications"]),
                  f"- **Deterministic check:** {k['deterministic_issues'] or 'no issue'}"]
            if k["kind"] == "clean":
                L += ["- **Expected:** no ERRONEOUS factual or numerical criticism (correct factual comments are allowed). "
                      "Methodological comments, including high-severity ones, are assessed on their merits, not counted "
                      "as false automatically."]
            else:
                L += ([f"- **One defect:** this single mutation shows {len(k['deterministic_issues'])} deterministic issues; "
                       "any accepted classification counts, and it is scored once"] if len(k["deterministic_issues"]) > 1 else [])
                L += [f"- **Accepted classifications:** {' or '.join(k['accept'])}; minimum severity **{k['min_severity']}** "
                      f"(ideal {k['ideal_severity']})" + (f"; accepted evidence: {', '.join('`' + e + '`' for e in k['evidence'])}"
                                                          if k["evidence"] else "")]
            L += ["- **Auditor verdict:** [ ] confirmed  [ ] not confirmed (notes: ____ )"]
    L += ["", "## Review record", ""] + [f"- {r['date']}: {r['type']}: {r['result']} (independent human audit: "
                                           f"{'yes' if r.get('independent_human_audit') else 'no'})" for r in (b["audit"].get("reviews") or [])]
    L += ["", "## Founder sign-off", "", "- Signed off by: ____  Date: ____",
          "- Only after this sign-off is recorded is `audit.status` set to `audited`. The record above is an AI-assisted review, "
          "not an independently conducted human audit.", ""]
    return "\n".join(L)


def payload_v3(bench_set: dict, agent_slice: dict) -> dict:
    """The v3 critic bundle (blinded; no key). Unlike the frozen v1/v2 bundle, it states the windows and the horizon:
    L4 and P4 are the actual quarters, the test window is the next two quarters after L4, and the data are first
    registrations in the defined dataset, not company sales."""
    w = agent_slice.get("windows") or {}
    return {"contract": "critic_benchmark.v3",
            "slice": {**{k: agent_slice[k] for k in ("market", "metrics", "metric_id_format", "templates")},
                      "windows": {"L4": w.get("L4"), "P4": w.get("P4"),
                                  "definition": "L4 = the latest four quarters with data; P4 = the four quarters before them; "
                                                "values are pooled over each window",
                                  "test_window": "every implication refers to the next TWO quarters after L4 (pooled), "
                                                 "compared with the L4 value unless another reference is named"},
                      "measure": "first registrations in the market's dataset (see market.universe), not company sales or "
                                 "deliveries; shares are shares of those registrations"},
            "hypotheses": bench_set["hypotheses"]}

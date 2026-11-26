"""Context assembly v0 (L3): the world slice an investigation's agents see, and the reference library.

A slice is token-budgeted and as-of: market definition, coverage, window metrics (ids + values),
a deterministic anomaly scan, the allowed id enums for the output schemas, and prior validated
lessons. Agents never receive raw tables or the whole store.

The REFERENCE LIBRARY is the set of implication signatures that would NOT count as original: the
deterministic reference workforce's templates (momentum of each competitor's share, focal-vs-target
segment share gap, segment share of market) plus every implication of hypotheses already in the
ledger for this market. A hypothesis whose implications are all in the library is a restatement.
"""

from __future__ import annotations

import hashlib
import json

from ..data.store import to_timestamp

MAX_ANOMALIES = 8


def _pp(x):
    return None if x is None else round(100 * x, 2)


def anomalies(panel, metrics: dict) -> list[dict]:
    """Largest L4-vs-P4 movements and within-segment vs mix divergences (deterministic; no interpretation)."""
    out = []
    for e in panel.entities:
        for t in ("share", "segment_share", "segment_mix"):
            a, b = metrics.get(panel.metric_id(t, "L4", e)), metrics.get(panel.metric_id(t, "P4", e))
            if a and b:
                out.append({"kind": "change", "template": t, "entity": e, "L4_pct": _pp(a["value"]), "P4_pct": _pp(b["value"]),
                            "change_pp": _pp(a["value"] - b["value"]), "evidence": [a["id"], b["id"]]})
        s_l, s_p = metrics.get(panel.metric_id("share", "L4", e)), metrics.get(panel.metric_id("share", "P4", e))
        g_l, g_p = metrics.get(panel.metric_id("segment_share", "L4", e)), metrics.get(panel.metric_id("segment_share", "P4", e))
        if s_l and s_p and g_l and g_p and (s_l["value"] - s_p["value"]) * (g_l["value"] - g_p["value"]) < 0:
            out.append({"kind": "divergence", "entity": e, "note": "total share and within-segment share moved in opposite directions",
                        "share_change_pp": _pp(s_l["value"] - s_p["value"]), "segment_share_change_pp": _pp(g_l["value"] - g_p["value"]),
                        "evidence": [s_l["id"], s_p["id"], g_l["id"], g_p["id"]]})
    ch = sorted([x for x in out if x["kind"] == "change"], key=lambda x: -abs(x["change_pp"]))
    return (ch[:MAX_ANOMALIES - 2] + [x for x in out if x["kind"] == "divergence"][:2])[:MAX_ANOMALIES]


def signature(imp: dict) -> tuple:
    """Canonical implication signature: comparing a metric with its OWN latest-window value via above/below_reference is
    the same claim as increase/decrease_vs_L4."""
    t, e, c = imp.get("template"), imp.get("entity"), imp.get("comparator")
    own = f"metric:{t}:L4" + ("" if t == "market_segment_share" else f":{e}")
    if c in ("above_reference", "below_reference") and imp.get("reference_metric") == own:
        c = "increase_vs_L4" if c == "above_reference" else "decrease_vs_L4"
    return (t, e, imp.get("entity_b") or "", c)


def reference_library(ledger, market, panel, metrics: dict, exclude_run: str | None = None) -> set:
    """Implication signatures that do not count as original (see module docstring). ``exclude_run``: the
    current investigation's input id, so an identical rerun does not judge itself against its own earlier claims."""
    lib = set()
    for e in panel.entities:  # trend continuation of each competitor's share, segment share and segment mix
        for t in ("share", "segment_share", "segment_mix"):
            a, b = metrics.get(panel.metric_id(t, "L4", e)), metrics.get(panel.metric_id(t, "P4", e))
            if a and b:
                lib.add((t, e, "", "increase_vs_L4" if a["value"] > b["value"] else "decrease_vs_L4"))
    if market.focal and market.target:
        for c in ("increase_vs_L4", "decrease_vs_L4"):
            lib.add(("segment_share_gap", market.focal, market.target, c))
            lib.add(("segment_share_gap", market.target, market.focal, c))
    lib.add(("market_segment_share", "market", "", "increase_vs_L4"))
    for c in ledger.as_of(panel.as_of, claim_type="hypothesis"):
        # prior KNOWLEDGE only: earlier dates, not sibling runs at the same date, and never mock/reference output
        if ((c["provenance"].get("market_id") or "") == market.id and c["run_id"] != exclude_run
                and c["as_of"] < panel.as_of and not str(c["provenance"].get("model", "")).endswith("-loop-v1")):
            for imp in c["payload"].get("observable_implications", []):
                lib.add(signature(imp))
    return lib


def build_slice(market, panel, coverage: dict, lessons: list[dict], token_budget_chars: int = 24000,
                extended: bool = False) -> dict:
    metrics = panel.metrics(extended=extended)
    L4, P4 = panel.windows()
    rows = [{"id": m["id"], "template": m["template"], "entity": m["entity"], "window": m["window"],
             "value_pct": round(100 * m["value"], 4)} for m in metrics.values()]
    s = {"market": {"id": market.id, "name": market.name, "category": market.category, "geography": market.geography,
                    "as_of": panel.as_of, "mode": market.mode, "focal": market.focal, "target": market.target,
                    "segment": panel.segment, "universe": panel.universe,
                    "measurement": market.measurement or {}, "competitors": {c.id: c.name for c in market.competitors}},
         "windows": {"L4": L4, "P4": P4, "next_two_quarters_are_the_test_window": True},
         "metrics": rows, "anomalies": anomalies(panel, metrics),
         "coverage": {"status": coverage.get("status"), "capabilities": coverage.get("contract", {}).get("capabilities", {}),
                      "gaps": coverage.get("gaps", [])[:6]},
         "lessons": [{"id": k["knowledge_id"], "status": k["status"], "statement": k["statement"][:240]} for k in lessons][:6],
         "measurement_grammar": {"templates": ["share", "segment_share", "segment_mix", "market_segment_share",
                                               "segment_share_gap", "other"],
                                 "comparators": ["increase_vs_L4", "decrease_vs_L4", "above_reference", "below_reference"],
                                 "note": "use template 'other' (with a description) for implications that need data not in "
                                         "this evidence; they are recorded as untested"}}
    text = json.dumps(s, sort_keys=True, default=str)
    budget = token_budget_chars * (2 if extended else 1)  # the extended measurement set is about twice as many rows
    while len(text) > budget and s["metrics"]:
        s["metrics"] = s["metrics"][:-4]  # trim lowest-priority rows (stable order) to respect the budget
        text = json.dumps(s, sort_keys=True, default=str)
    s["slice_id"] = "slice:" + hashlib.sha256(text.encode()).hexdigest()[:16]
    s["as_of_ts"] = to_timestamp(panel.as_of)
    return s, metrics

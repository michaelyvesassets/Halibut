"""Offline (scripted) workforces under the ``loop.v2`` contract. SCRIPTED OUTPUT IS NOT INTELLIGENCE.

``v2_adapter(responder)`` wraps a v1 mock or reference responder:
- for a v1 bundle it is transparent, so v1 behaviour is byte-identical;
- for a v2 bundle (``contract: loop.v2``) it downgrades the compact slice to v1 rows, calls the responder, and
  upgrades the reply to the v2 schemas:
  - ``explains``, ``rivals`` and ``information_gaps`` for the generator;
  - ``dimension`` and ``evidence`` for the critic;
  - ``entity_b`` and ``numeric_claims`` for the strategist. Narrative numbers are declared deterministically from
    the metric they reproduce; a number that reproduces nothing is left undeclared, so v2 validation rejects it.

With ``rich=True`` (the mock) it also exercises every v2 path:
- rival hypotheses with an opposite prediction on a shared quantity (a discriminating test);
- a pairwise "A exceeds B" implication (``above_zero``);
- an extended-measurement implication;
- information gaps;
- a factual critique citing evidence;
- a pairwise strategic option with declared numeric claims.
"""

from __future__ import annotations

import copy
import re

NUM = re.compile(r"(?<![\w.])[+-]?\d+(?:\.\d+)?")


def _rows(compact: list) -> list[dict]:
    out = []
    for mid, v in compact:
        parts = mid.split(":")
        t, w = parts[1], parts[2]
        out.append({"id": mid, "template": t, "entity": parts[3] if len(parts) > 3 else "market", "window": w,
                    "value_pct": v})
    return out


def _downgrade(p: dict) -> dict:
    q = copy.deepcopy(p)
    q.pop("contract", None)
    if isinstance(q.get("slice", {}).get("metrics", [None])[0], list):
        q["slice"]["metrics"] = _rows(q["slice"]["metrics"])
    return q


def _declare(text: str, metrics: list[dict], e: dict) -> list[dict]:
    claims = []
    for tok in sorted(set(NUM.findall(text))):
        d = len(tok.split(".")[1]) if "." in tok else 0
        v = float(tok)
        if v in (e["magnitude_low_pp"], e["magnitude_high_pp"]):
            claims.append({"text": tok, "value": v, "unit": "pp", "kind": "assumed", "decimals": d, "operation": "none",
                           "metric_ids": []})
            continue
        m = next((r for r in metrics if round(r["value_pct"], d) == round(v, d)), None)
        if m:
            claims.append({"text": tok, "value": v, "unit": "percent", "kind": "observed", "decimals": d,
                           "operation": "value", "metric_ids": [m["id"]]})
    return claims


def _option_text(o: dict) -> str:
    return " ".join([o["title"], o["intended_effect"]["basis"], o["cost"]["basis"]]
                    + [a["statement"] for a in o["assumptions"]] + o["risks"])


def upgrade(role: str, out: dict, p: dict, rich: bool) -> dict:
    s = p["slice"]
    metrics = _rows(s["metrics"]) if s.get("metrics") and isinstance(s["metrics"][0], list) else s.get("metrics", [])
    ids = {m["id"] for m in metrics}
    if role == "hypothesis_generator":
        hyps = out["hypotheses"]
        for h in hyps:
            h.setdefault("explains", h["supporting_evidence"][0])
            h.setdefault("rivals", [])
        gaps = []
        if rich and len(hyps) >= 2:
            h1, h2 = hyps[0], hyps[1]
            h1["rivals"], h2["rivals"] = [h2["ref"]], [h1["ref"]]
            gap = next((i for i in h1["observable_implications"] if i["template"] == "segment_share_gap"), None)
            if gap:  # the rival predicts the gap NARROWS: a test that discriminates H1 from H2
                h2["observable_implications"] = h2["observable_implications"][:2] + [
                    {**gap, "id": "I3", "comparator": "decrease_vs_L4", "description": "the gap narrows (rival mechanism)",
                     "would_refute_if": "the gap widens"}]
                h1["observable_implications"] = h1["observable_implications"][:3] + [
                    {**gap, "id": "I4", "comparator": "above_zero", "description": "the leader stays ahead",
                     "would_refute_if": "the gap turns negative"}]
            down = next((i["entity"] for i in h2["observable_implications"] if i["template"] != "other"), None)
            if down and f"metric:non_segment_share:L4:{down}" in ids and len(h2["observable_implications"]) < 4:
                h2["observable_implications"].append(  # extended measurement: growth outside the segment
                    {"id": "I4", "template": "non_segment_share", "entity": down, "entity_b": "",
                     "comparator": "increase_vs_L4", "reference_metric": "", "description": "non-segment sales hold up",
                     "would_refute_if": "non-segment share falls"})
            gaps = [{"id": "G1", "missing_measurement": "model-level registrations within the segment",
                     "why_it_matters": "separates portfolio concentration from temporary incentives",
                     "distinguishes": [h1["ref"], h2["ref"]], "potential_sources": ["DfT VEH0160 model table"],
                     "expected_research_value": "high"}]
            if any(h["ref"] == "H3" for h in hyps):
                gaps.append({"id": "G2", "missing_measurement": "regional registrations and infrastructure density",
                             "why_it_matters": "the spatial mechanism cannot be tested on national aggregates",
                             "distinguishes": ["H3"], "potential_sources": ["regional registration statistics"],
                             "expected_research_value": "medium"})
        return {**out, "information_gaps": gaps}
    if role == "loop_critic":
        crit = []
        hyps = {h["ref"]: h for h in p["hypotheses"]}
        for c in out["critiques"]:
            dim = "test_validity" if c["severity"] == "high" else "causal_identification"
            crit.append({**c, "dimension": dim, "evidence": []})
        if rich and hyps:
            h = next(iter(hyps.values()))
            ev = [x for x in h.get("supporting_evidence", []) if x in ids][:1]
            if ev:
                crit.append({"hypothesis_ref": h["ref"], "dimension": "factual_contradiction", "severity": "medium",
                             "statement": "The cited evidence does not show the stated mechanism; it shows the movement only.",
                             "evidence": ev, "challenged_assumption": h["assumptions"][0]["statement"][:240]
                             if h.get("assumptions") else ""})
        return {"critiques": crit}
    if role == "strategist":
        opts = []
        for o in out["options"]:
            o = {**o, "intended_effect": {**o["intended_effect"], "entity_b": o["intended_effect"].get("entity_b", "")}}
            o["numeric_claims"] = _declare(_option_text(o), metrics, o["intended_effect"])
            opts.append(o)
        m = s["market"]
        if rich and opts and m.get("focal") and m.get("target") and m.get("segment") and len(opts) < 4:
            fl4 = next((r for r in metrics if r["id"] == f"metric:segment_share:L4:{m['focal']}"), None)
            if fl4:
                v = round(fl4["value_pct"], 2)
                o = copy.deepcopy(opts[0])
                o.update({"ref": f"O{len(opts) + 1}", "title": "Close the segment gap to the incumbent",
                          "intended_effect": {"template": "segment_share_gap", "entity": m["focal"], "entity_b": m["target"],
                                              "direction": "increase", "magnitude_low_pp": 0.1, "magnitude_high_pp": 0.4,
                                              "basis": f"assumed 0.1 to 0.4 pp; the focal segment share is {v:.2f}%"},
                          "success_criteria": [{"template": "segment_share_gap", "entity": m["focal"],
                                                "entity_b": m["target"], "comparator": "increase_vs_L4",
                                                "reference_metric": "", "horizon_quarters": 2}]})
                o["numeric_claims"] = _declare(_option_text(o), metrics, o["intended_effect"])
                opts.append(o)
        return {"options": opts}
    return out


def v2_adapter(responder, rich: bool = False):
    def wrapped(role: str, p: dict) -> dict:
        if p.get("contract") != "loop.v2":
            return responder(role, p)
        return upgrade(role, responder(role, _downgrade(p)), p, rich)
    return wrapped

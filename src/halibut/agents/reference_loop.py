"""Deterministic REFERENCE workforce for the R2 loop: the baseline an LLM workforce must beat.

It proposes only what fixed rules can: for each competitor (largest movers first), "the latest trend
continues" with one implication per competitor in the direction of its own L4-vs-P4 change. These
implications ARE the reference library, so every reference hypothesis is a restatement by construction;
they are still compiled into tests and scored, which gives the baseline hit rate and informativeness.
Critique and strategy are fixed templates. No randomness; no model.
"""

from __future__ import annotations

MAX_HYPOTHESES = 4


def responder(role: str, p: dict) -> dict:
    if role == "hypothesis_generator":
        return _generator(p["slice"])
    if role == "loop_critic":
        return {"critiques": [{"hypothesis_ref": h["ref"], "severity": "medium", "statement":
                               "Trend continuation is assumed, not explained; mean reversion and segment-mix shifts are "
                               "equally consistent with two windows of data.", "challenged_assumption": "the trend persists"}
                              for h in p["hypotheses"]]}
    if role == "strategist":
        return _strategist(p)
    raise ValueError(role)


def _generator(s: dict) -> dict:
    tmpl = "segment_share" if s["market"].get("segment") else "share"
    from .mock_loop import _movers  # noqa: PLC0415  (L4-vs-P4 changes from the slice metrics)
    ch = sorted(_movers(s, tmpl), key=lambda a: -abs(a["change_pp"]))[:MAX_HYPOTHESES]
    hyps = []
    for i, a in enumerate(ch, start=1):
        e, up = a["entity"], a["change_pp"] >= 0
        hyps.append({
            "ref": f"H{i}", "statement": f"{e}'s {tmpl.replace('_', ' ')} keeps moving in the direction of its latest change.",
            "mechanism": {"cause": "persistence of the recent trend", "pathway": "no mechanism: a fixed persistence rule",
                          "effect_template": tmpl, "direction": "increase" if up else "decrease"},
            "conditions": ["no structural break"], "assumptions": [{"statement": "trends persist over two quarters",
                                                                     "kind": "other"}],
            "supporting_evidence": a["evidence"],
            "alternative_explanations": [{"statement": "mean reversion", "discriminating_implication": "I1"}],
            "observable_implications": [{"id": "I1", "template": tmpl, "entity": e, "entity_b": "",
                                         "comparator": "increase_vs_L4" if up else "decrease_vs_L4", "reference_metric": "",
                                         "description": "trend continuation", "would_refute_if": "the direction reverses"}],
            "uncertainty": {"level": "high", "sources": ["two windows only"]},
            "refutation_criteria": "Refuted if the direction of change reverses over the next two quarters.",
            "novelty_claim": "none: deterministic reference rule",
            "model_request": {"family": "none", "rationale": "", "numerical_assumptions": []}})
    return {"explanations": [], "hypotheses": hyps}


def _strategist(p: dict) -> dict:
    s, hyps = p["slice"], p["hypotheses"]
    focal = s["market"].get("focal")
    if not hyps or not focal:
        return {"options": []}
    tmpl = "segment_share" if s["market"].get("segment") else "share"
    mid = f"metric:{tmpl}:L4:{focal}"
    ev = [mid] if any(r["id"] == mid for r in s["metrics"]) else [s["metrics"][0]["id"]]
    unknown = {"known": False, "low": 0, "high": 0, "currency": "unknown", "basis": "fixed template: no cost evidence"}
    crit = [{"template": tmpl, "entity": focal, "entity_b": "", "comparator": "increase_vs_L4", "reference_metric": "",
             "horizon_quarters": 2}]
    mk = lambda ref, title, levers, lo, hi: {  # noqa: E731
        "ref": ref, "title": title, "mode": s["market"]["mode"], "levers": levers,
        "intended_effect": {"template": tmpl, "entity": focal, "direction": "increase", "magnitude_low_pp": lo,
                            "magnitude_high_pp": hi, "basis": "fixed template assumption (reference workforce)"},
        "linked_hypotheses": [hyps[0]["ref"]], "supporting_evidence": ev,
        "assumptions": [{"statement": "the lever affects the focal brand's share", "kind": "behavioural"}],
        "competitor_responses": [], "cost": unknown, "risks": ["effect smaller than assumed"], "uncertainty": "high",
        "success_criteria": crit}
    return {"options": [mk("O1", "Reference: price lever", ["price"], 0.1, 0.5),
                        mk("O2", "Reference: distribution lever", ["distribution"], 0.1, 0.5)]}

"""Deterministic mock responders for the R2 loop roles (offline testing only).

MOCK OUTPUT IS NOT INTELLIGENCE. The mock reads the slice and emits hypotheses whose ENTITIES come from
the data (largest movers), so the same mock works for any industry. Its fixed repertoire exercises
every path of the loop:
  H1 original mechanism, testable (against-trend and trend-consistent implications, a model request
     with a numerical assumption that may be rejected by the registry's bounds);
  H2 original rival mechanism, testable;
  H3 original but untestable here (needs data not in the evidence: template 'other');
  H4 a restatement of trend continuation (in the reference library);
  H5 an exact duplicate of H1's implications (must be dropped as a duplicate);
  with ``mock_faults`` in the slice: H6 citing a fabricated metric id (schema rejection).
"""

from __future__ import annotations


def _m(slice_, template, window, entity):
    mid = f"metric:{template}:{window}" + ("" if template == "market_segment_share" else f":{entity}")
    row = next((r for r in slice_["metrics"] if r["id"] == mid), None)
    return mid, (row or {}).get("value_pct")


def _movers(s, template):
    ch = [a for a in s["anomalies"] if a.get("kind") == "change" and a.get("template") == template]
    return sorted(ch, key=lambda a: -a["change_pp"])


def responder(role: str, p: dict) -> dict:
    if role == "hypothesis_generator":
        return _generator(p)
    if role == "loop_critic":
        return {"critiques": [{"hypothesis_ref": h["ref"], "severity": "high" if h.get("novelty") == "restatement" else "medium",
                               "statement": "Registrations are not sales to final buyers and brand switching is unobserved; "
                                            "the mechanism is one of several that fit these aggregates.",
                               "challenged_assumption": (h.get("assumptions") or [{"statement": ""}])[0]["statement"][:240]}
                              for h in p["hypotheses"]]}
    if role == "strategist":
        return _strategist(p)
    raise ValueError(role)


def _generator(p: dict) -> dict:
    s = p["slice"]
    seg_up = _movers(s, "segment_share") or _movers(s, "share")
    tmpl = "segment_share" if _movers(s, "segment_share") else "share"
    if len(seg_up) < 2:
        return {"explanations": [], "hypotheses": []}
    up, down = seg_up[0]["entity"], seg_up[-1]["entity"]
    up_l4, _ = _m(s, tmpl, "L4", up)
    up_p4, _ = _m(s, tmpl, "P4", up)
    dn_l4, _ = _m(s, tmpl, "L4", down)
    dn_p4, _ = _m(s, tmpl, "P4", down)
    sh_dn_p4, _ = _m(s, "share", "P4", down)
    sh_up_l4, _ = _m(s, "share", "L4", up)
    sh_up_p4, _ = _m(s, "share", "P4", up)
    has_seg = tmpl == "segment_share"
    ev_up = [x for x in (up_l4, up_p4) if x]
    a_up, a_dn = seg_up[0], seg_up[-1]
    expl = [{"statement": f"{up} gained the most: from {a_up['P4_pct']:.2f}% to {a_up['L4_pct']:.2f}% "
                          f"({tmpl.replace('_', ' ')}).", "evidence": a_up["evidence"]},
            {"statement": f"{down} lost the most: from {a_dn['P4_pct']:.2f}% to {a_dn['L4_pct']:.2f}% "
                          f"({tmpl.replace('_', ' ')}).", "evidence": a_dn["evidence"]}]
    asm = [{"statement": "First registrations (or sales units) track demand with a stable lag.", "kind": "measurement"}]
    gap = {"id": "I2", "template": "segment_share_gap" if has_seg else "share", "entity": up,
           "entity_b": down if has_seg else "", "comparator": "increase_vs_L4", "reference_metric": "",
           "description": f"the gap between {up} and {down} keeps widening", "would_refute_if": "the gap narrows"}
    h1 = {"ref": "H1", "statement": f"Specialist momentum: {up}'s gains come from a range concentrated in the growing segment, "
                                    f"so its segment position keeps improving while {down} cannot follow quickly.",
          "mechanism": {"cause": f"{up}'s portfolio concentration in the growing segment", "pathway":
                        "segment growth lifts specialist brands more than broad-portfolio brands; range breadth adjusts slowly",
                        "effect_template": tmpl, "direction": "increase"},
          "conditions": ["the segment keeps growing", "no supply shock for the specialist"], "assumptions": asm,
          "supporting_evidence": ev_up + [dn_l4],
          "alternative_explanations": [{"statement": "Temporary price incentives, not portfolio fit, explain the gains; they "
                                                     "reverse when incentives end.", "discriminating_implication": "I1"}],
          "observable_implications": [
              {"id": "I1", "template": tmpl, "entity": up, "entity_b": "", "comparator": "above_reference",
               "reference_metric": up_l4, "description": f"{up} stays above its latest-window level",
               "would_refute_if": f"{up} falls back below its latest-window level"},
              gap],
          "uncertainty": {"level": "medium", "sources": ["only two windows observed", "switching is unobserved"]},
          "refutation_criteria": f"Refuted if {up} falls below its latest-window level or the gap to {down} narrows over the "
                                 "next two quarters.",
          "novelty_claim": "Ties gains to portfolio concentration and predicts a widening gap, not just continuation.",
          "model_request": {"family": "diffusion.logistic", "rationale": "adoption-like growth with a ceiling",
                            "numerical_assumptions": [{"name": "saturation_share", "value": 0.5, "unit": "share",
                                                       "basis": "assumed ceiling for one brand's position (mock)"}]}}
    h2 = {"ref": "H2", "statement": f"Incumbent mix cushion: {down} loses position inside the segment, but its total share holds "
                                    "because its non-segment sales are stable.",
          "mechanism": {"cause": f"{down}'s broad portfolio", "pathway": "losses in the growing segment are offset by stable "
                        "demand elsewhere", "effect_template": tmpl, "direction": "decrease"},
          "conditions": ["non-segment demand stays stable"], "assumptions": asm, "supporting_evidence": [dn_l4, dn_p4],
          "alternative_explanations": [{"statement": f"{down} is losing overall, not only within the segment.",
                                        "discriminating_implication": "I2"}],
          "observable_implications": [
              {"id": "I1", "template": tmpl, "entity": down, "entity_b": "", "comparator": "decrease_vs_L4",
               "reference_metric": "", "description": f"{down} keeps falling", "would_refute_if": f"{down} recovers"},
              {"id": "I2", "template": "share", "entity": down, "entity_b": "", "comparator": "above_reference",
               "reference_metric": sh_dn_p4, "description": f"{down}'s total share stays above its earlier window",
               "would_refute_if": f"{down}'s total share drops below its earlier window"}],
          "uncertainty": {"level": "high", "sources": ["mix effects are inferred, not observed"]},
          "refutation_criteria": f"Refuted if {down} recovers in the segment or its total share drops below the earlier window.",
          "novelty_claim": "Separates within-segment loss from total-share resilience.",
          "model_request": {"family": "stat.holt", "rationale": "trend with damping", "numerical_assumptions": []}}
    h3 = {"ref": "H3", "statement": "Local enabling infrastructure, not price, drives adoption differences between regions.",
          "mechanism": {"cause": "regional infrastructure density", "pathway": "convenience lowers adoption barriers",
                        "effect_template": "other", "direction": "increase"},
          "conditions": ["regional variation exists"], "assumptions": asm, "supporting_evidence": ev_up[:1],
          "alternative_explanations": [{"statement": "Regional income differences explain adoption.",
                                        "discriminating_implication": "I1"}],
          "observable_implications": [{"id": "I1", "template": "other", "entity": "market", "entity_b": "",
                                       "comparator": "increase_vs_L4", "reference_metric": "",
                                       "description": "regional infrastructure density vs regional adoption (regional data needed)",
                                       "would_refute_if": "no association across regions"}],
          "uncertainty": {"level": "high", "sources": ["no regional data in evidence"]},
          "refutation_criteria": "Refuted if regional adoption shows no association with infrastructure density.",
          "novelty_claim": "A spatial mechanism the national aggregates cannot show.",
          "model_request": {"family": "causal.did", "rationale": "regional rollout comparison", "numerical_assumptions": []}}
    v_l4, v_p4 = _m(s, "share", "L4", up)[1], _m(s, "share", "P4", up)[1]
    trend = "increase_vs_L4" if (v_l4 or 0) >= (v_p4 or 0) else "decrease_vs_L4"  # the brand's actual total-share trend
    h4 = {**h1, "ref": "H4", "statement": f"{up}'s total share keeps moving the way it moved over the last year.",
          "observable_implications": [{"id": "I1", "template": "share", "entity": up, "entity_b": "", "comparator": trend,
                                       "reference_metric": "", "description": "continuation", "would_refute_if": "it falls"}],
          "alternative_explanations": [{"statement": "Noise.", "discriminating_implication": "I1"}],
          "novelty_claim": "none", "model_request": {"family": "none", "rationale": "", "numerical_assumptions": []}}
    h5 = {**h1, "ref": "H5", "statement": "Same as H1, worded differently: specialist brands win in a growing segment."}
    hyps = [h1, h2, h3, h4, h5]
    if s.get("mock_faults"):
        hyps.append({**h2, "ref": "H6", "supporting_evidence": ["metric:share:L4:invented_brand"]})
    return {"explanations": expl, "hypotheses": hyps}


def _strategist(p: dict) -> dict:
    s, hyps = p["slice"], p["hypotheses"]
    if not hyps:
        return {"options": []}
    focal = s["market"].get("focal") or hyps[0]["entity"]
    target = s["market"].get("target") or ""
    tmpl = "segment_share" if s["market"].get("segment") else "share"
    l4, _ = _m(s, tmpl, "L4", focal)
    refs = [h["ref"] for h in hyps]
    ev = [l4] if l4 else [s["metrics"][0]["id"]]
    others = [c for c in s["market"]["competitors"] if c not in (focal,)][:2]
    crit = [{"template": tmpl, "entity": focal, "entity_b": "", "comparator": "increase_vs_L4", "reference_metric": "",
             "horizon_quarters": 2}]
    mode = s["market"]["mode"]
    unknown = {"known": False, "low": 0, "high": 0, "currency": "unknown", "basis": "no cost evidence in the slice"}
    return {"options": [
        {"ref": "O1", "title": "Widen availability where the segment grows", "mode": mode,
         "levers": ["distribution", "availability"],
         "intended_effect": {"template": tmpl, "entity": focal, "direction": "increase", "magnitude_low_pp": 0.2,
                             "magnitude_high_pp": 0.8, "basis": "assumed (mock): a distribution expansion of this kind"},
         "linked_hypotheses": refs[:1], "supporting_evidence": ev,
         "assumptions": [{"statement": "Added outlets reach buyers who would otherwise choose a rival.", "kind": "behavioural"}],
         "competitor_responses": [{"competitor": target or others[0], "response": "increase_distribution", "likelihood": "medium",
                                   "effect_on_option": "offsets_partly"}],
         "cost": unknown, "risks": ["slower than expected rollout", "rival matches the expansion"], "uncertainty": "high",
         "success_criteria": crit},
        {"ref": "O2", "title": "Price and financing offer on the core range", "mode": mode, "levers": ["price", "financing_terms"],
         "intended_effect": {"template": tmpl, "entity": focal, "direction": "increase", "magnitude_low_pp": 0.1,
                             "magnitude_high_pp": 0.5, "basis": "assumed (mock): typical response to a price offer"},
         "linked_hypotheses": refs[:2], "supporting_evidence": ev,
         "assumptions": [{"statement": "Buyers in the segment are price sensitive.", "kind": "behavioural"}],
         "competitor_responses": [{"competitor": target or others[0], "response": "match_price", "likelihood": "high",
                                   "effect_on_option": "offsets_fully"}],
         "cost": {"known": True, "low": 1000000, "high": 5000000, "currency": "GBP",
                  "basis": "mock placeholder range for pipeline testing; NOT a sourced estimate"},
         "risks": ["margin erosion", "price war"], "uncertainty": "medium", "success_criteria": crit},
        {"ref": "O3", "title": "Service and product quality programme", "mode": "continuation",
         "levers": ["service", "product_quality"],
         "intended_effect": {"template": tmpl, "entity": focal, "direction": "increase", "magnitude_low_pp": 0.0,
                             "magnitude_high_pp": 0.3, "basis": "assumed (mock): slow-acting retention effect"},
         "linked_hypotheses": refs[-1:], "supporting_evidence": ev,
         "assumptions": [{"statement": "Retention improves with service quality.", "kind": "behavioural"}],
         "competitor_responses": [{"competitor": others[-1], "response": "unknown", "likelihood": "low",
                                   "effect_on_option": "unknown"}],
         "cost": unknown, "risks": ["effect too slow to measure in two quarters"], "uncertainty": "high",
         "success_criteria": crit}]}

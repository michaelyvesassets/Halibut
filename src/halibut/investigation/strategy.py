"""Strategic options, compliance checklist, scenario simulation and draft experiment protocol (L7, R2).

Options come from the Strategist agent; everything else here is deterministic:
- validation: linked hypotheses exist, magnitudes are ordered, unknown costs carry no numbers, success
  criteria compile into measurable criteria, narrative numbers match evidence or the option's own
  assumed magnitudes;
- compliance checklist: generic legal domains for the market's jurisdiction plus mode-specific reviews;
  Halibut never marks an option as legally cleared (status is always 'pending_human_review');
- simulation: ``simulation.share_shift`` applies the option's ASSUMED effect to the official baseline
  forecast (or, without a validated model, to the persistence baseline, labelled unvalidated), once with
  no competitor response and once per expected response. Simulated outcomes are conditional and
  uncalibrated, and never scored as forecasts;
- experiment protocol: a DRAFT for the most promising option, with minimum detectable effect from the
  metric's own quarterly volatility. It is not executable: executing needs an approval granted by a
  person with a legal review reference and measurable outcomes (``halibut.control.approvals``).
"""

from __future__ import annotations

import hashlib
import json

import numpy as np

from ..validation import ALLOWED_LEVERS, PROHIBITED, check_directions, check_numbers

OFFSET = {"none": (0.0, 0.0), "offsets_partly": (0.25, 0.75), "offsets_fully": (0.75, 1.0), "unknown": (0.0, 1.0)}
DOMINANCE_SHARE = 0.40
PROTOCOL_VERSION = "experiment_protocol.v1"


def _h(x) -> str:
    return hashlib.sha256(json.dumps(x, sort_keys=True, default=str).encode()).hexdigest()


def validate_option(o: dict, hyp_refs: set, allowed: set, compile_criterion) -> tuple[list[str], list[dict]]:
    p, crit = [], []
    e = o["intended_effect"]
    if e["magnitude_low_pp"] > e["magnitude_high_pp"]:
        p.append("intended_effect: magnitude_low_pp exceeds magnitude_high_pp")
    if abs(e["magnitude_high_pp"]) > 20:
        p.append("intended_effect: an assumed effect above 20 pp needs evidence this slice does not contain")
    missing = [r for r in o["linked_hypotheses"] if r not in hyp_refs]
    if missing:
        p.append(f"linked_hypotheses {missing} are not accepted hypotheses of this investigation")
    c = o["cost"]
    if not c["known"] and (c["low"] or c["high"]):
        p.append("cost marked unknown but numbers given")
    if c["known"] and (c["low"] <= 0 or c["high"] < c["low"] or c["currency"] == "unknown" or not c["basis"].strip()):
        p.append("a known cost needs 0 < low <= high, a currency and a basis")
    bad = [lv for lv in o["levers"] if lv not in ALLOWED_LEVERS]
    if bad:
        p.append(f"levers {bad} are not registered lawful levers")
    text = " ".join([o["title"], e["basis"]] + [a["statement"] for a in o["assumptions"]] + o["risks"])
    if PROHIBITED.search(text):
        p.append("prohibited tactic mentioned")
    p += check_numbers(text, allowed, {e["magnitude_low_pp"], e["magnitude_high_pp"]}) + check_directions(text)
    for i, sc in enumerate(o["success_criteria"]):
        spec, why = compile_criterion(sc)
        if spec is None:
            p.append(f"success_criteria[{i}] is not measurable with this evidence: {why}")
        else:
            crit.append(spec)
    return p, crit


def compliance_checklist(market, option: dict, metrics: dict) -> dict:
    items = [
        {"domain": "competition law", "requirement": "no agreements or information exchange with competitors; no "
         "exclusionary or predatory conduct; review pricing below cost", "status": "pending_human_review"},
        {"domain": "consumer protection and advertising", "requirement": "claims truthful, substantiated and clearly "
         "disclosed as advertising; comparative claims verifiable", "status": "pending_human_review"},
        {"domain": "data protection", "requirement": "lawful basis and minimisation for any customer data used in an "
         "experiment", "status": "pending_human_review"},
        {"domain": "sector regulation", "requirement": f"rules specific to {market.category} in {market.geography}",
         "status": "pending_human_review"}]
    flags = []
    if option["mode"] == "annihilation":
        flags.append("displacement objective: exclusionary-conduct review required before any action")
    focal = market.focal
    sh = metrics.get(f"metric:share:L4:{focal}") if focal else None
    if sh and sh["value"] >= DOMINANCE_SHARE:
        flags.append(f"focal share >= {int(100 * DOMINANCE_SHARE)}%: possible dominance, heightened competition-law review")
    if "price" in option["levers"]:
        flags.append("price lever: confirm pricing stays above cost and is not targeted to exclude a rival")
    return {"jurisdiction": market.geography, "status": "pending_human_review", "items": items, "flags": flags,
            "note": "Halibut does not provide legal clearance; a qualified reviewer must sign off before any action"}


def effect_target_problems(effect: dict, panel) -> list[str]:
    """Why an intended effect has no measurable target (empty when it has one). A gap is a difference between two
    entities, but the loop.v1 strategist schema's intended_effect carries only one entity, so a gap effect is
    under-specified: it is reported, never completed by guessing the second entity (e.g. from the success criteria)."""
    if effect["template"] == "segment_share_gap":
        return [f"intended effect on segment_share_gap names only {effect['entity']!r}: a gap needs a second entity, which "
                "the loop.v1 strategist schema does not carry; the effect has no measurable target and is not simulated"]
    if not panel.series(effect["template"], effect["entity"], None):
        return [f"intended effect {effect['template']} {effect['entity']!r} has no observations in this measurement set"]
    return []


def simulate(option: dict, baseline: dict, baseline_status: str) -> list[dict]:
    from ..models.registry import share_shift  # noqa: PLC0415
    e = option["intended_effect"]
    sign = 1 if e["direction"] == "increase" else -1
    eff = (sign * e["magnitude_low_pp"], sign * e["magnitude_high_pp"])
    scen = [("no_competitor_response", None, OFFSET["none"])]
    for r in option["competitor_responses"]:
        scen.append((f"{r['competitor']}:{r['response']}", r, OFFSET[r["effect_on_option"]]))
    out = []
    for name, resp, off in scen:
        sim = share_shift(baseline, eff, off)
        out.append({"scenario": name, "competitor_response": resp, **sim, "baseline_status": baseline_status,
                    "assumptions": [f"lever effect {eff[0]:+.2f} to {eff[1]:+.2f} pp (agent assumption: {e['basis']})",
                                    f"competitor offset {off[0]:.0%}-{off[1]:.0%} of the effect"
                                    + (f" ({resp['response']}, likelihood {resp['likelihood']})" if resp else ""),
                                    f"baseline: {baseline.get('model', 'persistence')} ({baseline_status})"]})
    return out


def min_effect_pp(simulations: list[dict]) -> float:
    """Smallest ASSUMED lever effect after competitor offsets across scenarios (pp; excludes forecast uncertainty)."""
    if not simulations:
        raise ValueError("min_effect_pp needs at least one simulation")
    return min(s["effect_pp_assumed"][0] * (1 - s["competitor_offset_assumed"][1]) for s in simulations)


def experiment_protocol(market, option: dict, criterion: dict, panel, simulations: list[dict]) -> dict:
    ser = panel.series(criterion["template"], criterion["entity"], criterion.get("entity_b"))
    diffs = np.diff([v for _, v in ser][-13:])
    sigma = float(np.std(diffs, ddof=1)) if len(diffs) > 2 else None
    k_pre, k_post = 4, criterion.get("horizon_quarters", 2) or 2
    mde = None if sigma is None else (1.96 + 0.84) * sigma * (1 / k_pre + 1 / k_post) ** 0.5
    low = min_effect_pp(simulations)
    body = {
        "spec_version": PROTOCOL_VERSION, "status": "draft_not_executable", "market_id": market.id, "option_ref": option["ref"],
        "option_title": option["title"], "hypotheses_tested": option["linked_hypotheses"],
        "design": {"type": "staggered regional rollout, analysed by difference-in-differences",
                   "fallback": "national before/after comparison against the official baseline forecast (weak design)",
                   "units": "regions or outlets assigned to treatment in random order",
                   "randomisation": "seeded random order of rollout waves, recorded before launch"},
        "primary_metric": criterion,
        "power": {"quarterly_sd_of_changes": sigma, "pre_quarters": k_pre, "post_quarters": k_post,
                  "national_mde_pp": None if mde is None else 100 * mde,
                  "assumed_effect_low_pp_after_responses": low,
                  "adequately_powered_nationally": None if mde is None else bool(low >= 100 * mde),
                  "note": "national aggregates have low power; regional outcome data are needed for the rollout design"},
        "guardrails": [{"metric": "gross margin", "stop_if": "falls below the plan's floor"},
                       {"metric": "customer complaints", "stop_if": "rise materially versus the pre-period"}] +
                      [{"metric": "risk", "stop_if": r} for r in option["risks"][:2]],
        "stop_rules": ["any guardrail breach", "legal or compliance concern raised", "end of the pre-registered horizon"],
        "measurement": {"required": "regional or outlet-level outcome data and treatment assignment records",
                        "available_in_evidence": False, "current_evidence": panel.universe},
        "operator": "client or partner team; Halibut designs and evaluates, never executes",
        "compliance": "legal/compliance review reference required before approval",
        "approval": {"kind": "experiment", "granted_by": "a named person only, with legal review and measurable outcomes"}}
    body["pre_registration_hash"] = _h(body)
    body["protocol_id"] = "xp:" + body["pre_registration_hash"][:16]
    return body

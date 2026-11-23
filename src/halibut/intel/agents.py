"""Deterministic reference agents (stand-ins for future Qwen-powered agents).

They implement the interfaces in ``halibut.interfaces`` with explicit rules,
so the whole intelligence workflow is testable and reproducible without an
LLM. Rules use only derived metrics and evidence ids from the context; every
threshold is stated in the hypothesis record.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

from ..data.providers.base import stable_id

SHARE_DELTA_PP = 0.5  # minimum |change| in percentage points to call momentum


def _pp(x):
    return None if x is None else 100 * x


def _resolve_date(as_of: str, quarters: int = 2) -> str:
    # data for the next `quarters` quarters is published ~120 days after each quarter end
    return (date.fromisoformat(as_of[:10]) + timedelta(days=91 * quarters + 120)).isoformat()


class ReferenceHypothesisAgent:
    id = "agent:reference_hypotheses:v1"

    def _h(self, ctx, mode, claim, subjects, evidence, prediction, confidence, rationale, levers=(), mechanisms=(),
           uncertainties=()):
        hid = "hyp:" + stable_id(ctx["run_id"], claim)
        return {"id": hid, "mode": mode, "claim": claim, "subjects": list(subjects), "evidence": list(evidence),
                "prediction": prediction, "confidence": confidence, "rationale": rationale,
                "levers": list(levers), "mechanism_candidates": list(mechanisms), "uncertainties": list(uncertainties),
                "status": "proposed", "epistemic_status": "hypothesis", "produced_by": self.id,
                "rule_thresholds": {"share_delta_pp": SHARE_DELTA_PP},
                "forecast_policy": ctx.get("forecast_policy"),
                "lessons_considered": [k["knowledge_id"] for k in ctx.get("lessons", [])]}

    @staticmethod
    def forecast_policy(lessons: list[dict]) -> dict:
        """Choose how predictions are framed, from retrieved knowledge (cross-agent learning)."""
        sim_deficit = [k for k in lessons if k["kind"] == "outcome" and "component:simulation.agent_model" in k["scope"].get("tags", [])]
        if sim_deficit:
            return {"method": "persistence_baseline", "simulation_use": "exploratory_only",
                    "because": [k["knowledge_id"] for k in sim_deficit],
                    "note": "Prior outcome: simple diffusion baselines beat the agent model in out-of-sample share forecasts; "
                            "predictions therefore use persistence, and scenario drafts are exploratory, not forecasts."}
        return {"method": "persistence_baseline", "simulation_use": "unvalidated", "because": [], "note": "No prior outcome retrieved."}

    def propose(self, ctx: dict) -> list[dict]:
        ctx.setdefault("forecast_policy", self.forecast_policy(ctx.get("lessons", [])))
        m, tx = ctx["market"], ctx["signals"]["transactions"]
        if not tx.get("available"):
            return []
        comps, out = tx["competitors"], []
        seg = tx["segment"]
        resolve = _resolve_date(m.as_of)
        names = {c.id: c.name for c in m.competitors}
        common_unc = ["First UK registrations include imported used vehicles (DfT definition); not new-car sales alone.",
                      "No prices, discounts, fleet/private split or model launches are observed."]

        # momentum (enforcement / continuation framing, all competitors)
        for cid, e in comps.items():
            if not e.get("available") or e["share_L4"] is None or e["share_P4"] is None:
                continue
            d = _pp(e["share_L4"] - e["share_P4"])
            if abs(d) < SHARE_DELTA_PP:
                continue
            gaining = d > 0
            out.append(self._h(
                ctx, "enforcement" if gaining else "continuation",
                f"{names[cid]}'s share of {m.category} first registrations {'rose' if gaining else 'fell'} by "
                f"{abs(d):.2f} pp (prior four quarters {_pp(e['share_P4']):.2f}% -> latest four {_pp(e['share_L4']):.2f}%); "
                f"the {'gain' if gaining else 'decline'} is expected to persist over the next two published quarters.",
                [e["brand"]], [f"metric:share:L4:{cid}", f"metric:share:P4:{cid}"],
                {"metric": f"registration_share:{cid}", "direction": "above" if gaining else "below",
                 "threshold": e["share_P4"], "periods": "next 2 quarters", "resolvable_after": resolve},
                "medium" if abs(d) >= 2 * SHARE_DELTA_PP else "low",
                "Share momentum over adjacent four-quarter windows; persistence is a baseline expectation, not a causal claim.",
                uncertainties=common_unc))

        # competitor-targeted displacement within the segment (annihilation framing)
        if m.mode == "annihilation" and seg:
            f, t = comps.get(m.focal, {}), comps.get(m.target, {})
            if f.get("available") and t.get("available") and None not in (f.get("segment_share_L4"), f.get("segment_share_P4"),
                                                                            t.get("segment_share_L4"), t.get("segment_share_P4")):
                df_, dt_ = _pp(f["segment_share_L4"] - f["segment_share_P4"]), _pp(t["segment_share_L4"] - t["segment_share_P4"])
                pattern = df_ > 0 and dt_ < 0
                ev = [f"metric:segment_share:L4:{m.focal}", f"metric:segment_share:P4:{m.focal}",
                      f"metric:segment_share:L4:{m.target}", f"metric:segment_share:P4:{m.target}"]
                fp = ctx["signals"].get("footprint", {}).get("competitors", {})
                fp_ev = [i for c in (m.focal, m.target) for i in fp.get(c, {}).get("inputs", [])]
                claim = (f"Within {seg} first registrations, {names[m.focal]} "
                         f"{'gained' if df_ > 0 else 'lost'} {abs(df_):.2f} pp while {names[m.target]} "
                         f"{'lost' if dt_ < 0 else 'gained'} {abs(dt_):.2f} pp between the prior and latest four quarters"
                         + (": a pattern consistent with displacement of the target by the focal brand in this segment."
                            if pattern else ": the data do NOT currently show displacement of the target by the focal brand."))
                out.append(self._h(
                    ctx, "annihilation", claim, [f["brand"], t["brand"]], ev + fp_ev,
                    {"metric": f"segment_share_gap:{m.focal}-{m.target}", "direction": "narrowing" if pattern else "unchanged",
                     "periods": "next 2 quarters", "resolvable_after": resolve},
                    "low", "Opposite-signed segment share changes are necessary but not sufficient for displacement; "
                           "other brands may account for the flows.",
                    levers=["price", "product_range", "distribution"],
                    mechanisms=["relative price/financing", "model range breadth", "dealer/distribution footprint",
                                "brand attention"],
                    uncertainties=common_unc + ["Brand-to-brand switching is not observed (no household data)."]))

        # target exposure to segment shift (continuation / defence framing)
        if seg and m.target and comps.get(m.target, {}).get("available") and tx.get("segment_share_L4") is not None:
            t = comps[m.target]
            mix, mkt = t.get("segment_mix_L4"), tx["segment_share_L4"]
            if mix is not None:
                under = mix < mkt
                out.append(self._h(
                    ctx, "continuation",
                    f"{names[m.target]}'s {seg} mix ({_pp(mix):.1f}% of its registrations) is "
                    f"{'below' if under else 'at or above'} the market's {seg} share ({_pp(mkt):.1f}%); "
                    f"{'continued segment growth would pressure its overall share unless its mix rises' if under else 'segment growth is not a structural headwind for it'}.",
                    [t["brand"]], [f"metric:segment_mix:L4:{m.target}", "metric:market_segment_share:L4",
                                   "metric:market_segment_share:P4"],
                    {"metric": "segment_share_of_market", "direction": "above", "threshold": tx.get("segment_share_P4"),
                     "periods": "next 2 quarters", "resolvable_after": resolve},
                    "low", "Mix-vs-market comparison; exposure, not prediction of the target's decline.",
                    levers=["product_range"], uncertainties=common_unc))

        # attention vs registrations (media proxy; low confidence by construction)
        md = ctx["signals"].get("media", {})
        if md.get("available"):
            tot_reg = sum(e["share_L4"] for e in comps.values() if e.get("available") and e.get("share_L4"))
            for cid, att in md["attention_share"].items():
                e = comps.get(cid, {})
                if not e.get("available") or att is None or not tot_reg:
                    continue
                reg_rel = e["share_L4"] / tot_reg
                if md["counts"][cid] >= 3 and att - reg_rel > 0.10:
                    out.append(self._h(
                        ctx, "diagnostic",
                        f"{names[cid]} receives {100*att:.0f}% of competitor-set news attention in the GDELT sample "
                        f"but {100*reg_rel:.0f}% of the set's registrations: attention exceeds sales position.",
                        [e["brand"]], md["inputs"][cid][:20] + [f"metric:share:L4:{cid}"],
                        {"metric": f"registration_share:{cid}", "direction": "above", "threshold": e["share_L4"],
                         "periods": "next 2 quarters", "resolvable_after": resolve},
                        "low", "Media attention is a proxy (sampled 15-minute files); it is not demand.",
                        uncertainties=[f"Sample of {md['sample'].get('n_files')} GDELT files; "
                                       f"{md['counts'][cid]} matched records."]))
        return out


class ReferenceCritic:
    id = "agent:reference_critic:v1"

    def critique(self, h: dict, ctx: dict) -> list[dict]:
        out = []
        if h["mode"] == "annihilation":
            out.append({"target": h["id"], "relation": "critiques",
                        "statement": "Segment share movements of two brands do not establish that one displaced the other; "
                                     "flows to/from third brands and segment growth can produce the same pattern."})
        if any(e.startswith("doc:") for e in h["evidence"]):
            out.append({"target": h["id"], "relation": "critiques",
                        "statement": "Relies partly on media-proxy evidence (GDELT sample), which measures coverage, not purchases."})
        fp_ids = {i for v in ctx["signals"].get("footprint", {}).get("competitors", {}).values() for i in v.get("inputs", [])}
        if fp_ids & set(h["evidence"]):
            out.append({"target": h["id"], "relation": "critiques",
                        "statement": "OSM footprint reflects mapping completeness as of the snapshot, not the full dealer network."})
        unresolved_subjects = [u for u in ctx.get("unresolved", []) if f"brand:{u}" in h["subjects"]]
        if unresolved_subjects:
            out.append({"target": h["id"], "relation": "critiques",
                        "statement": f"Entity resolution left {', '.join(unresolved_subjects)} unresolved; company/parent reasoning about it is incomplete."})
        if h["mode"] == "diagnostic" and any(e.startswith("doc:") for e in h["evidence"]):
            n = sum(e.startswith("doc:") for e in h["evidence"])
            if n < 20:
                out.append({"target": h["id"], "relation": "critiques",
                            "statement": f"Only {n} matched media records support this; the sample is too small to distinguish signal from noise."})
        return out


class ReferenceScenarioDrafter:
    """Draft a 3-product displacement scenario (focal, target, others) for simulation.

    Initial utilities are set so the logit reproduces observed segment shares
    (q_j = ln(s_j / s_others)); levers are illustrative, parameters uncalibrated.
    """

    id = "agent:reference_scenarios:v1"

    def draft(self, h: dict, ctx: dict) -> dict | None:
        if h["mode"] != "annihilation":
            return None
        m, tx = ctx["market"], ctx["signals"]["transactions"]
        f, t = tx["competitors"][m.focal], tx["competitors"][m.target]
        sf, st = f["segment_share_L4"], t["segment_share_L4"]
        so = 1 - sf - st
        if min(sf, st, so) <= 0:
            return None
        q = {"focal": math.log(sf / so), "target": math.log(st / so), "others": 0.0}
        products = [{"id": k, "category": "segment", "price": 1.0, "quality": round(v, 6), "availability": 1.0}
                    for k, v in q.items()]
        attrs = {"budget": 1.0, "consumption": 1.0, "price_sensitivity": 2.0, "brand_loyalty": 0.0,
                 "social_susceptibility": {"dist": "beta", "a": 2, "b": 2}, "switching_resistance": 0.0}
        return {
            "schema_version": "1.2",
            "experiment": {"name": f"draft_{m.id}_{m.focal}_vs_{m.target}",
                           "description": (f"SYNTHETIC DRAFT from {h['id']}: uncalibrated; levers illustrative; "
                                           f"use: {ctx.get('forecast_policy', {}).get('simulation_use', 'unvalidated')} "
                                           f"(lessons {', '.join(ctx.get('forecast_policy', {}).get('because', [])) or 'none'})."),
                           "weeks": 26, "seeds": [1, 2, 3], "summary_window_weeks": 4},
            "products": products,
            "population": {"size": 2000, "segments": [{"name": "segment_buyers", "weight": 1.0,
                                                       "initial_product_shares": {"focal": round(sf, 6), "target": round(st, 6),
                                                                                  "others": round(1 - round(sf, 6) - round(st, 6), 6)},
                                                       "attributes": attrs,
                                                       "preferences": {"focal": 0.0, "target": 0.0, "others": 0.0}}]},
            "decision": {"temperature": 1.0, "outside_utility": -30.0, "preference_weight": 1.0, "quality_weight": 1.0},
            "network": {"k": 10, "rewire_prob": 0.1},
            # UNIDENTIFIED from aggregate data: off, so the control reproduces observed segment shares
            "social_influence": {"enabled": False, "weight": 0.0, "signal": "current_product"},
            "preference_drift": {"enabled": False},
            "scenarios": [
                {"name": "control"},
                {"name": "focal_price_cut_10pct", "description": "Lever 'price': focal price -10% from tick 4.",
                 "interventions": [{"type": "product_change", "product": "focal", "attribute": "price", "op": "multiply",
                                    "value": 0.9, "start_week": 4}]},
                {"name": "focal_range_expansion", "description": "Lever 'product_range': focal attractiveness +0.3 from tick 4.",
                 "interventions": [{"type": "product_change", "product": "focal", "attribute": "quality", "op": "add",
                                    "value": 0.3, "start_week": 4, "ramp_weeks": 8}]},
                {"name": "target_defensive_price_cut", "description": "Target responds: price -10% from tick 8.",
                 "interventions": [{"type": "product_change", "product": "target", "attribute": "price", "op": "multiply",
                                    "value": 0.9, "start_week": 8}]},
            ],
        }

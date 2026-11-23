"""Deterministic mock responses for the three agent roles (offline testing only).

The mock reads the evidence bundle and answers like a reasonable, imperfect LLM.
With ``mock_faults`` in the bundle it also emits one hypothesis citing a
non-existent metric and one with a wrong number, so validation can be shown
to catch them. MOCK OUTPUT IS NOT INTELLIGENCE: it exercises the pipeline,
accounting and validators, nothing more.
"""

from __future__ import annotations


def responder(role: str, b: dict) -> dict:
    if role == "research":
        acts = []
        for g in b["gaps"]:
            code = g["gap"]
            if code == "no_track_record":
                acts.append({"tool": "store.track_record", "args": {}, "reason": "check prior prediction accuracy"})
            elif code == "segment_mix_vs_within_unclear":
                acts.append({"tool": "store.decomposition", "args": {}, "reason": "separate mix from within-segment effects"})
                acts.append({"tool": "store.decomposition", "args": {}, "reason": "(repeated request)"})
            elif code == "media_evidence_thin":
                acts.append({"tool": "provider.gdelt_sample_expand", "args": {"n_files": 48}, "reason": "media sample too small"})
            elif code == "unresolved_entities":
                acts.append({"tool": "analyst.wikidata_override", "args": {"competitors": g["detail"]}, "reason": "resolve entity"})
            elif code == "footprint_unavailable_as_of":
                acts.append({"tool": "provider.overpass_refresh", "args": {}, "reason": "no footprint snapshot at this date"})
        return {"actions": acts}
    if role == "hypothesis":
        m, comps, names = b["market"], b["competitors"], b["market"]["names"]
        out = []
        movers = sorted(comps.items(), key=lambda kv: -abs(kv[1]["share_L4"] - kv[1]["share_P4"]))[:3]
        for cid, e in movers:
            d = e["share_L4"] - e["share_P4"]
            if abs(d) < 0.3:
                continue
            out.append({"kind": "momentum", "subject": cid, "confidence": "low",
                        "claim": f"{names[cid]} moved from {e['share_P4']:.2f}% to {e['share_L4']:.2f}% of first registrations "
                                 f"({d:+.2f} pp); the direction is expected to persist.",
                        "evidence": [f"metric:share:L4:{cid}", f"metric:share:P4:{cid}"],
                        "mechanism_candidates": ["model range", "pricing"], "levers": ["price", "product_range"]})
        f, t = m.get("focal"), m.get("target")
        if f in comps and t in comps and comps[f].get("segment_share_L4") is not None:
            df_ = comps[f]["segment_share_L4"] - comps[f]["segment_share_P4"]
            dt_ = comps[t]["segment_share_L4"] - comps[t]["segment_share_P4"]
            dec = b["audit"]["decomposition"].get(t, {})
            out.append({"kind": "displacement", "subject": f, "confidence": "low",
                        "claim": f"In {m['segment']}, {names[f]} changed {df_:+.2f} pp and {names[t]} {dt_:+.2f} pp; "
                                 f"{names[t]}'s total-share change splits into {dec.get('mix_effect_pp', 0):+.2f} pp segment-mix "
                                 f"and {dec.get('within_segment_effect_pp', 0):+.2f} pp within-segment effects.",
                        "evidence": [f"metric:segment_share:L4:{f}", f"metric:segment_share:P4:{f}",
                                     f"metric:segment_share:L4:{t}", f"metric:segment_share:P4:{t}"],
                        "mechanism_candidates": ["relative price", "range breadth"], "levers": ["price", "distribution"]})
            out.append({"kind": "exposure", "subject": t, "confidence": "low",
                        "claim": f"{names[t]}'s {m['segment']} mix is {comps[t]['segment_mix_L4']:.2f}% against a market "
                                 f"{m['segment']} share of {m['market_segment_share_pct']['L4']:.2f}%.",
                        "evidence": [f"metric:segment_mix:L4:{t}", "metric:market_segment_share:L4"],
                        "mechanism_candidates": ["product range"], "levers": ["product_range"]})
        if b.get("mock_faults") and movers:
            cid, e = movers[0]
            out.append({"kind": "momentum", "subject": cid, "confidence": "medium",
                        "claim": f"{names[cid]} is surging.", "evidence": ["metric:share:L4:does_not_exist"]})
            out.append({"kind": "momentum", "subject": cid, "confidence": "medium",
                        "claim": f"{names[cid]} reached {e['share_L4'] + 1.0:.2f}% of first registrations.",
                        "evidence": [f"metric:share:L4:{cid}", f"metric:share:P4:{cid}"]})
        return {"hypotheses": out}
    if role == "critic":
        dec, disp = b["audit"]["decomposition"], b["audit"].get("displacement", {})
        out = []
        for h in b["hypotheses"]:
            if h["mode"] == "annihilation":
                others = {c: v for c, v in disp.get("within_segment_share_changes_pp", {}).items()
                          if c not in (b["market"]["focal"], b["market"]["target"]) and abs(v) >= 0.5}
                out.append({"hypothesis_id": h["id"], "severity": "high",
                            "statement": "Switching is unobserved; other brands also moved within the segment "
                                         f"({', '.join(f'{c} {v:+.2f} pp' for c, v in others.items()) or 'none above 0.5 pp'}), "
                                         "so the target's loss need not be the focal brand's gain."})
            for subj in h["subjects"]:
                cid = subj.split(":", 1)[1]
                d = dec.get(cid)
                if not d:
                    continue
                if abs(d["mix_effect_pp"]) > abs(d["within_segment_effect_pp"]):
                    out.append({"hypothesis_id": h["id"], "severity": "medium",
                                "statement": f"For {cid}, segment-mix effects ({d['mix_effect_pp']:+.2f} pp) exceed "
                                             f"within-segment effects ({d['within_segment_effect_pp']:+.2f} pp): the change is "
                                             "mostly about which segments grew, not brand performance within them."})
                if d["delta_pp"] < 0 and d["units_L4"] > d["units_P4"]:
                    out.append({"hypothesis_id": h["id"], "severity": "medium",
                                "statement": f"{cid}'s share fell while its registrations rose ({d['units_P4']:,.0f} -> "
                                             f"{d['units_L4']:,.0f}): a share loss is not a sales loss."})
        return {"critiques": out}
    raise ValueError(f"unknown role {role!r}")

"""Evidence-quality audit for transaction-based market signals.

Checks:
- comparison periods: L4 and P4 are each 4 consecutive quarters, adjacent and
  non-overlapping; calendar years only partly covered are flagged;
- denominators: every share in a window uses the identical denominator series
  set and total;
- universe definition: the dataset's own definition (e.g. imports included);
- completeness: competitor quarters with no observation;
- decomposition: exact shift-share split of each competitor's total-share change
      d(share) = sum_s [w'_s b'_s - w_s b_s]
               = sum_s (w'_s - w_s) b_s        (segment-mix effect)
               + sum_s w_s (b'_s - b_s)        (within-segment effect)
               + sum_s (w'_s - w_s)(b'_s - b_s) (interaction)
  where w_s is segment s's share of the market and b_s the brand's share of segment s;
- share vs volume: a falling share can coexist with rising units;
- displacement: brand-to-brand switching is NOT observed; co-movement is
  reported with the other brands that moved, as alternative explanations.
"""

from __future__ import annotations

from collections import defaultdict

from .signals import TX_UNIVERSE, _fuel_of, _make_of


def _qindex(p: str) -> int:
    return int(p[:4]) * 4 + int(p[-1])


def _sums(retr, series, geo, periods) -> dict:
    """make -> fuel -> units over periods; plus per-quarter presence."""
    out = defaultdict(lambda: defaultdict(float))
    present = defaultdict(set)
    ps = set(periods)
    for s in series:
        mk, fu = _make_of(s), _fuel_of(s)
        for o in retr.observations(s, geo):
            if o["period"] in ps and o["value"] is not None:
                out[mk][fu] += o["value"]
                present[mk].add(o["period"])
    return out, present


def audit_transactions(retr, market, tx: dict) -> dict:
    geo = market.geography
    universe = TX_UNIVERSE.format(body=market.transactions.get("body_type", "Cars"))
    series = retr.series_ids(universe)
    L4, P4 = tx["L4"], tx["P4"]
    checks = []
    qi = [_qindex(p) for p in P4 + L4]
    contiguous = len(qi) == 8 and all(b - a == 1 for a, b in zip(qi, qi[1:]))
    checks.append({"check": "comparison_periods", "pass": contiguous and len(L4) == 4 and len(P4) == 4,
                   "detail": f"P4 = {P4[0]}..{P4[-1]}, L4 = {L4[0]}..{L4[-1]} (adjacent 4-quarter windows; not calendar years)"})
    years = defaultdict(set)
    for p in P4 + L4:
        years[p[:4]].add(p)
    partial = {y: sorted(q) for y, q in years.items() if len(q) < 4}
    checks.append({"check": "partial_calendar_years", "pass": True,
                   "detail": ("windows cut across calendar years " + ", ".join(f"{y} ({len(q)} of 4 quarters)" for y, q in partial.items())
                              + "; no calendar-year totals are computed from these windows") if partial else "none"})
    dens = defaultdict(set)
    for m in tx["metrics"]:
        if m["name"] == "registration_share":
            dens[tuple(m["definition"]["periods"])].add((tuple(sorted(m["definition"]["denominator"])), m["denominator_value"]))
    checks.append({"check": "consistent_denominators", "pass": all(len(v) == 1 for v in dens.values()),
                   "detail": {", ".join(k[:1] + ("…",) + k[-1:]): len(v) for k, v in dens.items()}})
    row = retr.store.conn.execute("SELECT definition FROM datasets WHERE dataset_id=?", ("dft_veh0160_makes",)).fetchone()
    definition = row[0] if row else "unknown"
    checks.append({"check": "universe_definition", "pass": True, "detail": definition,
                   "note": "imports included" if "imported" in definition else "check definition"})
    sL, presL = _sums(retr, series, geo, L4)
    sP, presP = _sums(retr, series, geo, P4)
    missing = {}
    for c in market.competitors:
        mk = c.dft_make
        gaps = sorted((set(L4) - presL.get(mk, set())) | (set(P4) - presP.get(mk, set())))
        if gaps:
            missing[c.id] = gaps
    checks.append({"check": "competitor_quarter_completeness", "pass": not missing, "detail": missing or "all quarters present"})

    totL = {f: sum(m[f] for m in sL.values()) for f in {f for m in sL.values() for f in m}}
    totP = {f: sum(m[f] for m in sP.values()) for f in {f for m in sP.values() for f in m}}
    allL, allP = sum(totL.values()), sum(totP.values())
    fuels = sorted(set(totL) | set(totP))
    decomp = {}
    for c in market.competitors:
        mk = c.dft_make
        if mk not in sL and mk not in sP:
            continue
        mix = within = inter = 0.0
        seg_units = {}
        for f in fuels:
            wP, wL = totP.get(f, 0) / allP, totL.get(f, 0) / allL
            bP = sP.get(mk, {}).get(f, 0) / totP[f] if totP.get(f) else 0.0
            bL = sL.get(mk, {}).get(f, 0) / totL[f] if totL.get(f) else 0.0
            mix += (wL - wP) * bP
            within += wP * (bL - bP)
            inter += (wL - wP) * (bL - bP)
            seg_units[f] = {"P4": sP.get(mk, {}).get(f, 0.0), "L4": sL.get(mk, {}).get(f, 0.0)}
        shareP = sum(sP.get(mk, {}).values()) / allP
        shareL = sum(sL.get(mk, {}).values()) / allL
        decomp[c.id] = {"share_P4": shareP, "share_L4": shareL, "delta_pp": 100 * (shareL - shareP),
                        "mix_effect_pp": 100 * mix, "within_segment_effect_pp": 100 * within,
                        "interaction_pp": 100 * inter, "residual_pp": 100 * ((shareL - shareP) - (mix + within + inter)),
                        "units_P4": sum(sP.get(mk, {}).values()), "units_L4": sum(sL.get(mk, {}).values()),
                        "segment_units": {f: v for f, v in seg_units.items() if v["P4"] or v["L4"]}}
    seg = tx.get("segment")
    disp = {}
    if market.focal and market.target and seg:
        movers = []
        for cid, d in decomp.items():
            u = d["segment_units"].get(seg, {"P4": 0, "L4": 0})
            bP = u["P4"] / totP.get(seg, 1)
            bL = u["L4"] / totL.get(seg, 1)
            movers.append((cid, 100 * (bL - bP), u))
        movers.sort(key=lambda x: x[1])
        t_units = decomp.get(market.target, {}).get("segment_units", {}).get(seg, {"P4": 0, "L4": 0})
        disp = {"segment": seg, "segment_units_P4": totP.get(seg, 0), "segment_units_L4": totL.get(seg, 0),
                "target_units": t_units,
                "target_units_change_pct": (100 * (t_units["L4"] / t_units["P4"] - 1)) if t_units["P4"] else None,
                "within_segment_share_changes_pp": {c: round(v, 3) for c, v, _ in movers},
                "assessment": ("Brand-to-brand switching is not observed in registration counts. Co-movement of the focal "
                               "brand's gain and the target's loss within the segment is consistent with, but does not "
                               "establish, displacement; other competitors' within-segment changes are alternative "
                               "sources and sinks of the flows.")}
    return {"available": True, "universe": universe, "checks": checks, "decomposition": decomp, "displacement": disp,
            "all_passed": all(c["pass"] for c in checks)}

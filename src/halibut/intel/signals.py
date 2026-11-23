"""Derived competitive signals (epistemic status: derived).

Every metric carries an exact, recomputable ``definition``, the observation
ids it rests on (``inputs``), its evidence class, and its value. Validators
recompute share metrics from the definition and reject any mismatch, so a
metric cannot be fabricated or silently altered.

Windows: L4 = the four latest quarters available at ``as_of``; P4 = the four
quarters before that. Shares are computed within ONE universe.
"""

from __future__ import annotations

from collections import defaultdict

TX_UNIVERSE = "dft_veh0160_makes:BodyType={body}"
MODEL_UNIVERSE = "dft_veh0160_models:BodyType={body}"


def _make_of(series_id: str) -> str:
    return series_id.split(":Make=")[1].split(":")[0]


def _fuel_of(series_id: str) -> str:
    return series_id.split(":Fuel=")[1]


def _sum(retriever, series_ids, geo, periods) -> tuple[float, list[str]]:
    total, ids = 0.0, []
    for s in series_ids:
        for o in retriever.observations(s, geo):
            if o["period"] in periods and o["value"] is not None:
                total += o["value"]
                ids.append(o["id"])
    return total, ids


def compute_share(retriever, definition: dict) -> float | None:
    """Recompute a share metric from its definition (used by validators)."""
    periods = set(definition["periods"])
    num, _ = _sum(retriever, definition["numerator"], definition["geo"], periods)
    den, _ = _sum(retriever, definition["denominator"], definition["geo"], periods)
    return num / den if den > 0 else None


def windows(retriever, universe: str, geo: str) -> tuple[list[str], list[str]]:
    periods = set()
    for s in retriever.series_ids(universe)[:50]:
        periods.update(o["period"] for o in retriever.observations(s, geo))
    q = sorted(periods)
    return q[-4:], q[-8:-4]


def transaction_signals(retriever, market, competitor_brands: dict[str, str]) -> dict:
    """``competitor_brands``: competitor id -> brand entity id."""
    body = market.transactions.get("body_type", "Cars")
    segment = market.transactions.get("segment")
    geo = market.geography
    universe = TX_UNIVERSE.format(body=body)
    all_series = retriever.series_ids(universe)
    if not all_series:
        return {"available": False, "reason": f"no series in universe {universe} at {retriever.as_of}"}
    L4, P4 = windows(retriever, universe, geo)
    by_make = defaultdict(list)
    for s in all_series:
        by_make[_make_of(s)].append(s)
    seg_series = [s for s in all_series if segment and _fuel_of(s) == segment]
    metrics = []

    def share_metric(mid, name, subject, num, den, periods, scope):
        n, ids = _sum(retriever, num, geo, set(periods))
        d, _ = _sum(retriever, den, geo, set(periods))
        value = n / d if d > 0 else None
        metrics.append({"id": mid, "name": name, "subject": subject, "scope": scope, "value": value,
                        "numerator_value": n, "denominator_value": d, "evidence_class": "transaction",
                        "epistemic_status": "derived", "inputs": ids,
                        "definition": {"kind": "share", "numerator": list(num), "denominator": list(den),
                                       "periods": list(periods), "geo": geo, "universe": universe}})
        return value

    seg_total_L4 = seg_total_P4 = None
    if segment:
        seg_total_L4 = share_metric("metric:market_segment_share:L4", "segment_share_of_market", "market", seg_series,
                                    all_series, L4, f"{segment} share of all {body} first registrations")
        seg_total_P4 = share_metric("metric:market_segment_share:P4", "segment_share_of_market", "market", seg_series,
                                    all_series, P4, f"{segment} share of all {body} first registrations")
    per = {}
    for cid, brand in competitor_brands.items():
        make = market.competitor(cid).dft_make
        if not make or make not in by_make:
            per[cid] = {"available": False, "reason": f"make {make!r} not in {universe}"}
            continue
        own = by_make[make]
        own_seg = [s for s in own if segment and _fuel_of(s) == segment]
        e = {"brand": brand, "available": True}
        e["share_L4"] = share_metric(f"metric:share:L4:{cid}", "registration_share", brand, own, all_series, L4, "all fuels")
        e["share_P4"] = share_metric(f"metric:share:P4:{cid}", "registration_share", brand, own, all_series, P4, "all fuels")
        if segment:
            e["segment_share_L4"] = share_metric(f"metric:segment_share:L4:{cid}", "segment_share", brand, own_seg,
                                                 seg_series, L4, f"share of {segment}")
            e["segment_share_P4"] = share_metric(f"metric:segment_share:P4:{cid}", "segment_share", brand, own_seg,
                                                 seg_series, P4, f"share of {segment}")
            e["segment_mix_L4"] = share_metric(f"metric:segment_mix:L4:{cid}", "segment_mix", brand, own_seg, own, L4,
                                               f"{segment} as share of the brand's own registrations")
        e["registrations_L4"], _ = _sum(retriever, own, geo, set(L4))
        per[cid] = e
    return {"available": True, "universe": universe, "L4": L4, "P4": P4, "segment": segment,
            "segment_share_L4": seg_total_L4, "segment_share_P4": seg_total_P4, "competitors": per, "metrics": metrics}


def product_signals(retriever, market, cids: list[str], top: int = 3) -> dict:
    body = market.transactions.get("body_type", "Cars")
    universe = MODEL_UNIVERSE.format(body=body)
    series = retriever.series_ids(universe)
    L4, _ = windows(retriever, universe, market.geography)
    out = {}
    for cid in cids:
        make = market.competitor(cid).dft_make
        rows = []
        for s in series:
            if f":Make={make}:" not in s:
                continue
            v, ids = _sum(retriever, [s], market.geography, set(L4))
            if v > 0:
                rows.append({"product_line": s.split(":GenModel=")[1], "registrations_L4": v, "inputs": ids,
                             "entity": f"product:dft:{make}:{s.split(':GenModel=')[1]}"})
        out[cid] = sorted(rows, key=lambda r: -r["registrations_L4"])[:top]
    return {"universe": universe, "L4": L4, "top_products": out}


def media_signals(retriever, market, competitor_brands: dict[str, str]) -> dict:
    m = market.media
    if not m:
        return {"available": False}
    counts, tone, ids = {}, {}, {}
    for cid, brand in competitor_brands.items():
        docs = retriever.documents(brand, m["start"], m["end"] + "T23:59:59")
        counts[cid] = len(docs)
        tones = [d["tone"] for d in docs if d["tone"] is not None]
        tone[cid] = sum(tones) / len(tones) if tones else None
        ids[cid] = [d["id"] for d in docs]
    total = sum(counts.values())
    return {"available": total > 0, "evidence_class": "proxy_media", "total_matched": total, "counts": counts,
            "attention_share": {c: (n / total if total else None) for c, n in counts.items()},
            "mean_tone": tone, "inputs": ids, "sample": m}


def footprint_signals(retriever, market, competitor_brands: dict[str, str]) -> dict:
    f = market.footprint
    if not f:
        return {"available": False}
    tag_key = ",".join(f"{k}={v}" for k, v in sorted(f["tags"].items()))
    out = {}
    for cid, brand in competitor_brands.items():
        obs = retriever.observations(f"osm_footprint:{tag_key}:{brand}", market.geography)
        out[cid] = {"mapped_locations": obs[-1]["value"] if obs else None, "inputs": [obs[-1]["id"]] if obs else []}
    return {"available": any(v["mapped_locations"] is not None for v in out.values()), "evidence_class": "presence",
            "tags": tag_key, "competitors": out}


def macro_signals(retriever, market) -> dict:
    out = {}
    for code in market.worldbank_indicators:
        obs = [o for o in retriever.observations(f"worldbank:{code}", market.geography) if o["value"] is not None]
        if obs:
            out[code] = {"latest_period": obs[-1]["period"], "latest": obs[-1]["value"],
                         "previous": obs[-2]["value"] if len(obs) > 1 else None, "inputs": [o["id"] for o in obs[-2:]]}
    return {"available": bool(out), "evidence_class": "macro", "indicators": out}


def group_signals(retriever, tx: dict, competitor_brands: dict[str, str]) -> dict:
    """Aggregate competitor brand shares by ultimate parent (via operated_by / parent_organization)."""
    groups, paths = defaultdict(list), {}
    for cid, brand in competitor_brands.items():
        ops = retriever.relationships(subject=brand, predicate="operated_by")
        if not ops:
            continue
        company = ops[0]["object"]
        chain, rel_ids, node = [company], [ops[0]["id"]], company
        for _ in range(4):
            par = retriever.relationships(subject=node, predicate="parent_organization")
            if not par:
                break
            node = par[0]["object"]
            chain.append(node)
            rel_ids.append(par[0]["id"])
        groups[node].append(cid)
        paths[cid] = {"chain": chain, "relationships": rel_ids}
    out = {}
    comp = tx.get("competitors", {})
    for top, members in groups.items():
        s = [comp[c]["share_L4"] for c in members if comp.get(c, {}).get("available")]
        out[top] = {"members": members, "share_L4_of_members": sum(s) if s else None}
    return {"groups": out, "paths": paths}

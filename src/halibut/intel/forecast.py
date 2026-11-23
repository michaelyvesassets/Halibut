"""Prediction policies for share quantities, and v2 predictions for hypotheses.

Policies (point forecast of a pooled share over the next 2 quarters):
  level         the L4 pooled share (persistence of level)
  momentum      L4 + 0.75 (L4 - P4): the 4-quarter slope extrapolated to the
                target window centre (3 quarters after the L4 centre)
  last_quarter  the share in the latest available quarter
Intervals:
  naive      point +- z80 * sd, sd = SD of the last 8 quarterly shares (floor 0.2 pp)
  empirical  point +- the 80% quantile of absolute errors of earlier SCORED
             predictions of the same method, using only outcomes published on or
             before the forecast's as-of date (needs >= 8 such scores)
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import numpy as np

from ..learning.predictions import make_prediction

PUBLICATION_LAG_DAYS = 120
SD_FLOOR = 0.002


def next_quarters(last: str, n: int = 2) -> list[str]:
    y, q = int(last[:4]), int(last[-1])
    out = []
    for _ in range(n):
        q += 1
        if q == 5:
            y, q = y + 1, 1
        out.append(f"{y}-Q{q}")
    return out


def quarter_end(p: str) -> date:
    y, q = int(p[:4]), int(p[-1])
    return date(y, 3 * q, {1: 31, 2: 30, 3: 30, 4: 31}[q])


def resolvable_after(periods: list[str]) -> str:
    return (max(quarter_end(p) for p in periods) + timedelta(days=PUBLICATION_LAG_DAYS)).isoformat()


def share_q(num, den, geo, periods) -> dict:
    return {"kind": "share", "numerator": list(num), "denominator": list(den), "geo": geo, "periods": list(periods)}


def _pooled(retr, num, den, geo, periods) -> float | None:
    from ..learning.predictions import _share  # noqa: PLC0415
    return _share(retr, share_q(num, den, geo, periods))[0]


def quarterly_sd(retr, num, den, geo, quarters) -> float:
    vals = [v for v in (_pooled(retr, num, den, geo, [q]) for q in quarters) if v is not None]
    return max(float(np.std(vals, ddof=1)) if len(vals) > 2 else SD_FLOOR, SD_FLOOR)


def policy_point(retr, num, den, geo, L4, P4, method) -> tuple[float | None, float | None]:
    l4 = _pooled(retr, num, den, geo, L4)
    if l4 is None:
        return None, None
    if method == "level":
        return l4, None
    if method == "last_quarter":
        return _pooled(retr, num, den, geo, L4[-1:]), None
    p4 = _pooled(retr, num, den, geo, P4)
    if p4 is None:
        return None, None
    mom = l4 - p4
    return float(min(max(l4 + 0.75 * mom, 0.0), 1.0)), mom


def empirical_halfwidth(store, method: str, as_of: str, replay_id: str | None, min_n: int = 8) -> float | None:
    rows = store.conn.execute("""SELECT abs_error, record_json, outcome_available_at, origin_as_of FROM prediction_scores
        WHERE method=? AND outcome_available_at<=? AND origin_as_of<?""", (method, as_of, as_of)).fetchall()
    errs = [r["abs_error"] for r in rows
            if json.loads(r["record_json"]).get("quantity_kind") == "share"
            and (replay_id is None or json.loads(r["record_json"]).get("replay_id") == replay_id)
            and json.loads(r["record_json"]).get("interval_method") == "naive"]
    if len(errs) < min_n:
        return None
    return float(np.quantile(errs, 0.8))


def policy_predictions(store, retr, market, tx: dict, produced_by: str, replay_id: str | None) -> list[dict]:
    """Shadow predictions for every competitor under every policy (for method evaluation)."""
    from .signals import TX_UNIVERSE  # noqa: PLC0415
    universe = TX_UNIVERSE.format(body=market.transactions.get("body_type", "Cars"))
    den = retr.series_ids(universe)
    L4, P4 = tx["L4"], tx["P4"]
    targets = next_quarters(L4[-1])
    quarters8 = sorted(set(P4 + L4))
    out = []
    for c in market.competitors:
        num = [s for s in den if f":Make={c.dft_make}:" in s] if c.dft_make else []
        if not num:
            continue
        sd = quarterly_sd(retr, num, den, market.geography, quarters8)
        for method in ("level", "momentum", "last_quarter"):
            point, mom = policy_point(retr, num, den, market.geography, L4, P4, method)
            if point is None:
                continue
            common = dict(quantity=share_q(num, den, market.geography, targets), point=point, sd=sd, threshold=None,
                          direction=None, method=method, produced_by=produced_by, origin_as_of=retr.as_of,
                          resolvable_after=resolvable_after(targets), evidence=[f"metric:share:L4:{c.id}"])
            extra = {"subject": c.id, "momentum": mom, "origin_window": L4, "replay_id": replay_id, "interval_method": "naive"}
            out.append(make_prediction(**common, extra=extra))
            if method == "level":
                hw = empirical_halfwidth(store, method, retr.as_of, replay_id)
                if hw is not None:
                    out.append(make_prediction(**common, interval80=(point - hw, point + hw),
                                               extra={**extra, "interval_method": "empirical", "empirical_halfwidth": hw}))
    return out


def hypothesis_prediction(h: dict, retr, market, tx: dict, method: str, replay_id: str | None) -> dict | None:
    """Build a v2 prediction from a hypothesis's prediction intent (agent-independent)."""
    from .signals import TX_UNIVERSE, _fuel_of  # noqa: PLC0415
    universe = TX_UNIVERSE.format(body=market.transactions.get("body_type", "Cars"))
    den = retr.series_ids(universe)
    seg = tx.get("segment")
    seg_den = [s for s in den if seg and _fuel_of(s) == seg]
    L4, P4 = tx["L4"], tx["P4"]
    targets = next_quarters(L4[-1])
    metric = h["prediction"]["metric"]
    geo = market.geography

    def brand_num(cid, segment=False):
        make = market.competitor(cid).dft_make
        return [s for s in (seg_den if segment else den) if f":Make={make}:" in s]

    common = dict(produced_by=h["produced_by"], origin_as_of=retr.as_of, resolvable_after=resolvable_after(targets),
                  evidence=h["evidence"], hypothesis_id=h["id"])
    extra = {"origin_window": L4, "replay_id": replay_id, "interval_method": "naive", "hypothesis_mode": h["mode"]}
    sd8 = sorted(set(P4 + L4))
    if metric.startswith("registration_share:"):
        cid = metric.split(":")[1]
        num = brand_num(cid)
        point, mom = policy_point(retr, num, den, geo, L4, P4, method)
        if point is None:
            return None
        return make_prediction(share_q(num, den, geo, targets), point, quarterly_sd(retr, num, den, geo, sd8),
                               h["prediction"].get("threshold"), h["prediction"]["direction"], method,
                               extra={**extra, "subject": cid, "momentum": mom}, **common)
    if metric == "segment_share_of_market":
        point, mom = policy_point(retr, seg_den, den, geo, L4, P4, method)
        return make_prediction(share_q(seg_den, den, geo, targets), point, quarterly_sd(retr, seg_den, den, geo, sd8),
                               h["prediction"].get("threshold"), "above", method,
                               extra={**extra, "subject": "market", "momentum": mom}, **common)
    if metric.startswith("segment_share_gap:"):
        f, t = metric.split(":")[1].split("-")
        a, b = share_q(brand_num(t, True), seg_den, geo, targets), share_q(brand_num(f, True), seg_den, geo, targets)
        pa, ma = policy_point(retr, a["numerator"], seg_den, geo, L4, P4, method)
        pb, mb = policy_point(retr, b["numerator"], seg_den, geo, L4, P4, method)
        ga = _pooled(retr, a["numerator"], seg_den, geo, L4)
        gb = _pooled(retr, b["numerator"], seg_den, geo, L4)
        if None in (pa, pb, ga, gb):
            return None
        sd = (quarterly_sd(retr, a["numerator"], seg_den, geo, sd8) ** 2 + quarterly_sd(retr, b["numerator"], seg_den, geo, sd8) ** 2) ** 0.5
        narrowing = h["prediction"]["direction"] == "narrowing"
        return make_prediction({"kind": "share_difference", "a": a, "b": b}, pa - pb, sd, ga - gb,
                               "below" if narrowing else "above", method,
                               extra={**extra, "subject": f"{t}-{f}", "momentum": (ma - mb) if ma is not None else None}, **common)
    return None

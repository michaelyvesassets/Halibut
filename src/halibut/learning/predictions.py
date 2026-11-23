"""Structured predictions and automatic outcome scoring (prediction spec v2).

A prediction is machine-resolvable:
  quantity  {"kind": "share", numerator, denominator, geo, periods}
            or {"kind": "share_difference", "a": <share>, "b": <share>}  (a - b)
  point, interval80 [lo, hi], sd, threshold, direction ('above'|'below'),
  p_direction (probability the direction holds), method, model version,
  origin_as_of, resolvable_after, evidence ids, hypothesis id (optional).

Scoring (``score_due``) at an evaluation date T uses ONLY observations
available at T (publication-date cutoff), so historical replay is honest:
  error = point - realized; abs_error; in_interval80;
  interval score (Gneiting & Raftery 2007, alpha = 0.2):
      (hi - lo) + (2/alpha)(lo - y)1[y<lo] + (2/alpha)(y - hi)1[y>hi]
  direction_hit; Brier score of p_direction.
Each score is stored in prediction_scores and as an 'outcome' knowledge item
whose available_at is the publication time of the realised data. Failures get
CANDIDATE explanations (status 'proposed', tag 'explanation'), never causes.
"""

from __future__ import annotations

import json
import math

from ..data.store import to_timestamp
from ..intel.retrieval import StoreEvidenceRetriever
from .memory import LearningMemory

SPEC_VERSION = "prediction/v2"
Z80 = 1.2815515655446004
ALPHA = 0.2


def _phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def make_prediction(quantity: dict, point: float, sd: float, threshold: float | None, direction: str | None,
                    method: str, produced_by: str, origin_as_of: str, resolvable_after: str, evidence: list[str],
                    hypothesis_id: str | None = None, interval80: tuple[float, float] | None = None,
                    model_version: str | None = None, extra: dict | None = None) -> dict:
    sd = max(float(sd), 1e-6)
    lo, hi = interval80 if interval80 else (point - Z80 * sd, point + Z80 * sd)
    p = None
    if threshold is not None and direction in ("above", "below"):
        p_above = 1 - _phi((threshold - point) / sd)
        p = p_above if direction == "above" else 1 - p_above
    from ..version import ENGINE_VERSION  # noqa: PLC0415
    return {"spec": SPEC_VERSION, "quantity": quantity, "point": float(point), "sd": sd, "interval80": [float(lo), float(hi)],
            "threshold": threshold, "direction": direction, "p_direction": p, "method": method,
            "produced_by": produced_by, "model_version": model_version or f"{produced_by}@engine-{ENGINE_VERSION}",
            "origin_as_of": origin_as_of, "resolvable_after": resolvable_after, "evidence": list(evidence),
            "hypothesis_id": hypothesis_id, **(extra or {})}


def _share(retr, q: dict) -> tuple[float | None, str | None]:
    """Pooled share over q['periods']; None unless every period is available for some numerator AND denominator series."""
    periods = set(q["periods"])
    num = den = 0.0
    seen, latest = set(), None
    for key, series in (("num", q["numerator"]), ("den", q["denominator"])):
        for s in series:
            for o in retr.observations(s, q["geo"]):
                if o["period"] in periods and o["value"] is not None:
                    if key == "num":
                        num += o["value"]
                    else:
                        den += o["value"]
                        seen.add(o["period"])
                    latest = max(latest or "", o["available_at"])
    if seen != periods or den <= 0:
        return None, None
    return num / den, latest


def realized(retr, q: dict) -> tuple[float | None, str | None]:
    if q["kind"] == "share":
        return _share(retr, q)
    a, ta = _share(retr, q["a"])
    b, tb = _share(retr, q["b"])
    if a is None or b is None:
        return None, None
    return a - b, max(ta, tb)


def score_one(pred: dict, y: float) -> dict:
    lo, hi = pred["interval80"]
    iscore = (hi - lo) + (2 / ALPHA) * max(lo - y, 0) + (2 / ALPHA) * max(y - hi, 0)
    hit = brier = None
    if pred.get("threshold") is not None and pred.get("direction") in ("above", "below"):
        holds = y > pred["threshold"] if pred["direction"] == "above" else y < pred["threshold"]
        hit = bool(holds)
        if pred.get("p_direction") is not None:
            brier = (pred["p_direction"] - (1.0 if holds else 0.0)) ** 2
    return {"realized": y, "point": pred["point"], "error": pred["point"] - y, "abs_error": abs(pred["point"] - y),
            "in_interval80": lo <= y <= hi, "interval_score80": iscore, "direction_hit": hit, "brier": brier}


def candidate_explanations(pred: dict, score: dict, retr_eval, retr_origin) -> list[dict]:
    """Deterministic candidate explanations for a failed prediction. Not causes."""
    out = []
    q = pred["quantity"] if pred["quantity"]["kind"] == "share" else pred["quantity"]["a"]
    if abs(score["error"]) > 2 * pred["sd"]:
        out.append({"code": "volatility_underestimated",
                    "statement": f"Error {100 * score['error']:+.2f} pp exceeded twice the assumed sd ({100 * pred['sd']:.2f} pp): "
                                 "the uncertainty model may understate volatility."})
    # denominator (market size) shift between the origin window and the target window
    origin_den = sum(o["value"] for s in q["denominator"] for o in retr_origin.observations(s, q["geo"])
                     if o["period"] in set(pred.get("origin_window", [])) and o["value"] is not None)
    target_den = sum(o["value"] for s in q["denominator"] for o in retr_eval.observations(s, q["geo"])
                     if o["period"] in set(q["periods"]) and o["value"] is not None)
    if origin_den and pred.get("origin_window"):
        per_q_origin = origin_den / len(pred["origin_window"])
        per_q_target = target_den / len(q["periods"])
        ch = per_q_target / per_q_origin - 1
        if abs(ch) > 0.05:
            out.append({"code": "denominator_shift",
                        "statement": f"Average quarterly market size changed {100 * ch:+.1f}% between the origin window and the "
                                     "target quarters; share changes may reflect market-size or mix changes, not brand performance."})
    # seasonality: same quarters one year earlier vs the origin window average
    prev = [f"{int(p[:4]) - 1}{p[4:]}" for p in q["periods"]]
    seasonal, _ = _share(retr_origin, {**q, "periods": prev})
    if seasonal is not None and abs(seasonal - score["realized"]) < abs(score["point"] - score["realized"]):
        out.append({"code": "seasonality_not_modelled",
                    "statement": f"The same quarters a year earlier ({100 * seasonal:.2f}%) were closer to the outcome than the "
                                 f"prediction ({100 * score['point']:.2f}%): quarter-of-year effects may matter."})
    if pred["method"] == "momentum" and pred.get("momentum") and score["realized"] is not None:
        if (score["realized"] - pred["point"]) * pred["momentum"] < 0:
            out.append({"code": "trend_reversal", "statement": "The outcome moved against the extrapolated momentum."})
    if not out:
        out.append({"code": "unexplained", "statement": "No candidate explanation from the available evidence."})
    return out


def _estimated(store, q: dict) -> bool:
    """True if the realised data's availability dates are estimates (history found in a first retrieval)."""
    qs = [q] if q["kind"] == "share" else [q["a"], q["b"]]
    for x in qs:
        s = x["denominator"][0]
        r = store.conn.execute("""SELECT COUNT(*) FROM observations WHERE series_id=? AND period IN (%s)
            AND availability_basis='estimated_lag'""" % ",".join("?" * len(x["periods"])), [s, *x["periods"]]).fetchone()[0]
        if r:
            return True
    return False


def score_due(store, memory: LearningMemory, eval_as_of: str, tags: list[str] | None = None,
              produced_by: str = "engine:prediction_scorer") -> list[dict]:
    """Score every open v2 prediction whose quantity is fully observable at ``eval_as_of``."""
    retr_eval = StoreEvidenceRetriever(store, eval_as_of)
    results = []
    preds = memory.retrieve(tags=tags, kinds=["prediction"], statuses=["proposed"], limit=100000)
    for k in preds:
        pred = k["record"].get("prediction")
        if not pred or pred.get("spec") != SPEC_VERSION:
            continue
        y, published = realized(retr_eval, pred["quantity"])
        if y is None:
            continue
        sc = score_one(pred, y)
        failed = (sc["direction_hit"] is False) or (not sc["in_interval80"])
        retr_origin = StoreEvidenceRetriever(store, pred["origin_as_of"])
        # candidate explanations only for hypothesis predictions (shadow policy forecasts are scored, not explained)
        expl = candidate_explanations(pred, sc, retr_eval, retr_origin) if failed and pred.get("hypothesis_id") else []
        basis = "approximate (estimated publication lag)" if _estimated(store, pred["quantity"]) else "published"
        statement = (f"Prediction {k['knowledge_id']} ({pred['method']}): point {100 * sc['point']:.2f}% vs realized "
                     f"{100 * y:.2f}% (error {100 * sc['error']:+.2f} pp); 80% interval "
                     f"{'covered' if sc['in_interval80'] else 'missed'}"
                     + ("" if sc["direction_hit"] is None else f"; direction {'held' if sc['direction_hit'] else 'failed'}") + ".")
        oid = memory.submit("outcome", statement, produced_by, {"tags": list(k["scope"].get("tags", [])) + ["outcome:prediction"]},
                            [k["knowledge_id"]], experiment_id=k["experiment_id"], status="tested",
                            record={"score": sc, "prediction": pred, "explanations": expl,
                                    "outcome_publication_date_basis": basis}, available_at=published)
        memory.link(oid, "tests", k["knowledge_id"], produced_by)
        memory.set_status(k["knowledge_id"], "tested", produced_by, effective_at=published)
        store.conn.execute("INSERT OR REPLACE INTO prediction_scores VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (k["knowledge_id"], to_timestamp(eval_as_of), y, sc["point"], sc["error"], sc["abs_error"],
                            int(sc["in_interval80"]), sc["interval_score80"],
                            None if sc["direction_hit"] is None else int(sc["direction_hit"]), sc["brier"],
                            pred["method"], pred["produced_by"], pred["origin_as_of"], published, oid,
                            json.dumps({"quantity_kind": pred["quantity"]["kind"], "hypothesis_id": pred.get("hypothesis_id"),
                                        "subject": pred.get("subject"), "interval_method": pred.get("interval_method"),
                                        "replay_id": pred.get("replay_id"), "hypothesis_mode": pred.get("hypothesis_mode"),
                                        "produced_by": pred.get("produced_by"),
                                        "outcome_publication_date_basis": basis})))
        for e in expl:
            eid = memory.submit("hypothesis", f"Candidate explanation for {k['knowledge_id']}: {e['statement']}", produced_by,
                                {"tags": list(k["scope"].get("tags", [])) + ["explanation", f"explanation:{e['code']}"]},
                                [oid], experiment_id=k["experiment_id"], confidence="low",
                                record={"code": e["code"], "note": "candidate explanation, not a demonstrated cause"},
                                available_at=published)
            memory.link(eid, "derived_from", oid, produced_by)
            # typed claim: an explanation of an observed error (not a hypothesis, not a forecast)
            from ..epistemic.claims import ClaimLedger  # noqa: PLC0415
            ClaimLedger(store).submit("explanation", e["statement"], produced_by, as_of=published,
                                      evidence=[oid, k["knowledge_id"]], exists=StoreEvidenceRetriever(store, published).exists,
                                      payload={"code": e["code"], "explains": k["knowledge_id"], "knowledge_id": eid,
                                               "note": "candidate explanation, not a demonstrated cause"},
                                      provenance={"scorer": produced_by, "replay_id": pred.get("replay_id")},
                                      run_id=pred.get("replay_id"), commit=False)
        hyp = pred.get("hypothesis_knowledge_id")
        if hyp:
            memory.set_status(hyp, "rejected" if failed else "tested", produced_by, effective_at=published)
            memory.link(oid, "contradicts" if failed else "supports", hyp, produced_by)
        store.conn.commit()
        results.append({"prediction": k["knowledge_id"], "outcome": oid, "failed": failed, **sc,
                        "explanations": [e["code"] for e in expl], "published": published})
    return results

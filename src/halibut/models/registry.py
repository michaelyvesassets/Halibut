"""Model registry (R2): families, specification validation, reproducible execution, validation.

A FAMILY is code (deterministic, versioned). A MODEL VERSION is a family + target + parameters +
data cut-off, registered in ``model_versions`` with a spec hash. Only VALIDATED model versions
produce official forecasts; unvalidated ones may be run, but their outputs are labelled estimates.

Validation rule ``model-validation-v1`` (all on data available at the model's as-of date):
  1. the spec is valid: the family exists and is available for forecasting, the target series has
     >= 12 quarters, numerical assumptions are inside the family's bounds;
  2. execution is reproducible: two runs give bit-identical outputs;
  3. a rolling-origin backtest over the last 6 origins (2-quarter-ahead pooled target, as for tests)
     has 80% interval coverage >= 0.5 AND MAE <= 1.25 x the persistence baseline's MAE.
     (The persistence baseline is validated on criteria 1, 2 and coverage only.)

Families:
  forecast families   baseline.level, baseline.momentum, stat.holt, diffusion.logistic, diffusion.bass
  simulation family   simulation.share_shift (scenario model for strategy options; never a forecast)
  registered, unavailable for forecasting here
                      causal.did (needs treated/control units with outcome data; not in the evidence),
                      simulation.abm_synthetic (the Phase 1-3 agent-based engine: synthetic, not calibrated to
                      this market's universe)
"""

from __future__ import annotations

import hashlib
import json

import numpy as np

from ..data.store import utc_now

RULE = "model-validation-v1"
Z80 = 1.2815515655446004
MIN_QUARTERS = 12
BACKTEST_ORIGINS = 6
HORIZON = 2
SD_FLOOR = 0.002

FAMILIES = {
    "baseline.level": {"version": "1", "kind": "statistical", "available": True, "bounded01": False,
                       "description": "persistence: the pooled share of the latest 4 quarters", "assumptions": {}},
    "baseline.momentum": {"version": "1", "kind": "statistical", "available": True, "bounded01": False,
                          "description": "L4 + 0.75 (L4 - P4): the 4-quarter change extrapolated to the target window",
                          "assumptions": {}},
    "stat.holt": {"version": "1", "kind": "statistical", "available": True, "bounded01": True,
                  "description": "Holt linear exponential smoothing on the logit of the quarterly share", "assumptions": {}},
    "diffusion.logistic": {"version": "1", "kind": "diffusion", "available": True, "bounded01": True,
                           "description": "3-parameter logistic substitution curve; optional fixed saturation",
                           "assumptions": {"saturation_share": (0.0, 1.0)}},
    "diffusion.bass": {"version": "1", "kind": "diffusion", "available": True, "bounded01": True,
                       "description": "Bass diffusion m F(t); optional fixed market potential",
                       "assumptions": {"market_potential_share": (0.0, 1.0)}},
    "simulation.share_shift": {"version": "1", "kind": "simulation", "available": True, "forecast": False,
                               "description": "scenario model: baseline forecast + assumed lever effect x (1 - competitor offset)",
                               "assumptions": {}},
    "causal.did": {"version": "0", "kind": "causal", "available": False,
                   "unavailable_reason": "difference-in-differences needs treated and control units with outcome data "
                                         "(e.g. regional or store-level); the evidence has national totals only",
                   "assumptions": {}},
    "simulation.abm_synthetic": {"version": "0.3", "kind": "simulation", "available": False,
                                 "unavailable_reason": "the agent-based engine is synthetic and not calibrated to this "
                                                       "market's universe; it cannot issue forecasts here",
                                 "assumptions": {}},
}
FORECAST_FAMILIES = [f for f, d in FAMILIES.items() if d["available"] and d.get("forecast", True)]


def catalogue() -> list[dict]:
    return [{"family": f, **{k: v for k, v in d.items() if k != "assumptions"},
             "assumption_bounds": {k: list(v) for k, v in d["assumptions"].items()}} for f, d in FAMILIES.items()]


def _h(x) -> str:
    return hashlib.sha256(json.dumps(x, sort_keys=True, default=float).encode()).hexdigest()


def check_spec(spec: dict, panel) -> list[str]:
    p = []
    fam = FAMILIES.get(spec.get("family"))
    if fam is None:
        return [f"unknown model family {spec.get('family')!r}"]
    if not fam["available"]:
        return [f"family {spec['family']} is unavailable: {fam['unavailable_reason']}"]
    if fam.get("forecast", True) is False:
        return [f"family {spec['family']} is a simulation family, not a forecaster"]
    t = spec.get("target") or {}
    ser = panel.series(t.get("template"), t.get("entity"), t.get("entity_b"))
    if len(ser) < MIN_QUARTERS:
        p.append(f"target series has {len(ser)} quarters as of {panel.as_of}; at least {MIN_QUARTERS} are required")
    if fam["bounded01"] and t.get("template") == "segment_share_gap":
        p.append(f"{spec['family']} models a share in (0, 1); a share gap can be negative")
    for a in spec.get("assumptions", []):
        lo_hi = fam["assumptions"].get(a.get("name"))
        if lo_hi is None:
            p.append(f"assumption {a.get('name')!r} is not a parameter of {spec['family']}")
        elif not (lo_hi[0] < float(a.get("value", -1)) <= lo_hi[1]):
            p.append(f"assumption {a['name']}={a.get('value')} outside ({lo_hi[0]}, {lo_hi[1]}]")
        elif a["name"] in ("saturation_share", "market_potential_share") and ser and float(a["value"]) < max(v for _, v in ser):
            p.append(f"assumption {a['name']}={a['value']} is below the observed maximum {max(v for _, v in ser):.4f}")
    return p


def _pooled(panel, target, periods):
    return panel.value(target["template"], target["entity"], periods, target.get("entity_b"))[0]


def _point_sd(family: str, y: np.ndarray, assumptions: dict, pooled: tuple | None = None) -> tuple[float, float]:
    """2-quarter-ahead target and its SD from the quarterly series y (ending at the cut-off). ``pooled`` = the
    POOLED L4 and P4 values (sum of numerators / sum of denominators), as used by tests and evidence metrics;
    the baselines use them so a forecast and a test compare like with like."""
    from ..empirical import baselines as bl  # noqa: PLC0415
    sd_q = max(float(np.std(y[-8:], ddof=1)) if len(y) > 2 else SD_FLOOR, SD_FLOOR)
    l4, p4 = pooled if pooled and None not in pooled else (float(np.mean(y[-4:])), float(np.mean(y[-8:-4])))
    if family == "baseline.level":
        return l4, sd_q
    if family == "baseline.momentum":
        return l4 + 0.75 * (l4 - p4), sd_q
    t = np.arange(len(y), dtype=float)
    if family == "stat.holt":
        r = bl.holt(np.clip(y, 1e-4, 1 - 1e-4), HORIZON)
    elif family == "diffusion.logistic":
        if "saturation_share" in assumptions:
            K = assumptions["saturation_share"]
            fit = bl._curve_fit(bl.logistic_curve, t, y, [(K, 0.05, t[-1]), (K, 0.2, t[-1] - 8)],
                                ([K * 0.9999, 1e-4, -1e3], [K * 1.0001 if K * 1.0001 <= 1 else 1.0, 1.0, 1e4]))
            tf = t[-1] + np.arange(1, HORIZON + 1)
            resid = bl.logistic_curve(t[-36:], *fit.x) - y[-36:]
            r = {"point": bl.logistic_curve(tf, *fit.x), "sd": np.full(HORIZON, float(np.std(resid, ddof=1)))}
        else:
            r = bl.logistic(y, HORIZON, t)
    elif family == "diffusion.bass":
        if "market_potential_share" in assumptions:
            m = assumptions["market_potential_share"]
            fit = bl._curve_fit(bl.bass_curve, t, y, [(1e-3, 0.05, m), (1e-2, 0.2, m)],
                                ([1e-7, 1e-4, m * 0.9999], [0.2, 1.0, min(1.0, m * 1.0001)]))
            tf = t[-1] + np.arange(1, HORIZON + 1)
            resid = bl.bass_curve(t[-36:], *fit.x) - y[-36:]
            r = {"point": bl.bass_curve(tf, *fit.x), "sd": np.full(HORIZON, float(np.std(resid, ddof=1)))}
        else:
            r = bl.bass(y, HORIZON, t)
    else:
        raise ValueError(f"family {family} cannot forecast")
    point = float(np.mean(r["point"]))
    sd = max(float(np.sqrt(np.mean(np.asarray(r["sd"]) ** 2))), sd_q * 0.5, SD_FLOOR)
    return point, sd


def execute(spec: dict, panel) -> dict:
    """Deterministic forecast for the spec's target over the next 2 quarters after the panel's last quarter."""
    t = spec["target"]
    ser = panel.series(t["template"], t["entity"], t.get("entity_b"))
    y = np.array([v for _, v in ser], dtype=float)
    a = {x["name"]: float(x["value"]) for x in spec.get("assumptions", [])}
    per = [q for q, _ in ser]
    point, sd = _point_sd(spec["family"], y, a, (_pooled(panel, t, per[-4:]), _pooled(panel, t, per[-8:-4])))
    lo, hi = point - Z80 * sd, point + Z80 * sd
    if t["template"] != "segment_share_gap":  # shares live in [0, 1]
        point, lo, hi = (float(min(max(x, 0.0), 1.0)) for x in (point, lo, hi))
    out = {"point": point, "sd": sd, "interval80": [lo, hi], "last_period": ser[-1][0],
           "n_quarters": len(y), "inputs_hash": _h([list(map(float, y)), a, spec["family"]])}
    out["output_hash"] = _h([out["point"], out["sd"]])
    return out


def backtest(spec: dict, panel) -> dict:
    """Rolling-origin evaluation INSIDE the data available at the panel's as-of date (no future data)."""
    t = spec["target"]
    ser = panel.series(t["template"], t["entity"], t.get("entity_b"))
    y = np.array([v for _, v in ser], dtype=float)
    a = {x["name"]: float(x["value"]) for x in spec.get("assumptions", [])}
    per = [q for q, _ in ser]
    errs, base, cov = [], [], []
    for k in range(BACKTEST_ORIGINS, 0, -1):
        cut = len(y) - HORIZON - (k - 1)
        if cut < 8:
            continue
        actual = _pooled(panel, t, per[cut:cut + HORIZON])  # pooled, exactly as a test would resolve
        pooled = (_pooled(panel, t, per[cut - 4:cut]), _pooled(panel, t, per[cut - 8:cut - 4]))
        try:
            pt, sd = _point_sd(spec["family"], y[:cut], a, pooled)
        except Exception:  # noqa: BLE001  (a failed fit counts as a failed backtest step)
            pt, sd = float("nan"), float("nan")
        lpt, _ = _point_sd("baseline.level", y[:cut], {}, pooled)
        errs.append(abs(pt - actual))
        base.append(abs(lpt - actual))
        cov.append(bool(abs(pt - actual) <= Z80 * sd))
    if not errs or any(np.isnan(errs)):
        return {"rule": RULE, "n": len(errs), "mae": None, "baseline_mae": None, "coverage80": None, "passed": False,
                "reason": "backtest could not be computed (too little history or a failed fit)"}
    mae, bmae, c = float(np.mean(errs)), float(np.mean(base)), float(np.mean(cov))
    persistence = spec["family"] == "baseline.level"
    passed = c >= 0.5 and (persistence or mae <= 1.25 * bmae + 1e-12)
    return {"rule": RULE, "n": len(errs), "mae": mae, "baseline_mae": bmae, "coverage80": c, "passed": passed,
            "reason": "passed" if passed else ("coverage below 0.5" if c < 0.5 else "MAE above 1.25 x persistence")}


def register(store, spec: dict, panel, *, registered_by: str, run_id: str | None = None) -> dict:
    """Validate and register a model version for ``spec`` (append-only). Returns the registry record."""
    problems = check_spec(spec, panel)
    fam = FAMILIES.get(spec.get("family"), {})
    body = {"family": spec.get("family"), "target": spec.get("target"), "assumptions": spec.get("assumptions", []),
            "as_of": panel.as_of, "panel": panel.snapshot, "rule": RULE}
    mvid = "mv:" + _h(body)[:20]
    row = store.conn.execute("SELECT * FROM model_versions WHERE model_version_id=?", (mvid,)).fetchone()
    if row:
        return {"model_version_id": mvid, "family": row["family"], "status": row["status"],
                "validation": json.loads(row["validation_json"]), "spec": json.loads(row["spec_json"])}
    if problems:
        status, validation = "rejected", {"rule": RULE, "problems": problems}
    else:
        r1, r2 = execute(spec, panel), execute(spec, panel)
        bt = backtest(spec, panel)
        reproducible = r1["output_hash"] == r2["output_hash"]
        status = "validated" if (reproducible and bt["passed"]) else "unvalidated"
        validation = {"rule": RULE, "reproducible": reproducible, "backtest": bt}
    store.conn.execute("INSERT INTO model_versions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (mvid, spec.get("family", "?"), fam.get("version", "?"), fam.get("kind", "?"),
                        json.dumps(spec.get("target"), sort_keys=True), json.dumps(body, sort_keys=True, default=str),
                        _h(body), panel.as_of, status, json.dumps(validation, sort_keys=True, default=float), registered_by,
                        run_id, utc_now()))
    from ..epistemic.events import EventLog  # noqa: PLC0415
    EventLog(store).append("model.registered", "L5", {"family": spec.get("family"), "status": status}, run_id=run_id,
                           subject_id=mvid, provenance={"registered_by": registered_by, "rule": RULE})
    return {"model_version_id": mvid, "family": spec.get("family"), "status": status, "validation": validation, "spec": body}


def share_shift(baseline: dict, effect_pp: tuple[float, float], offset: tuple[float, float]) -> dict:
    """Scenario model (simulation.share_shift v1): outcome = baseline + effect x (1 - offset), as a range.
    ``effect_pp`` and ``offset`` are ASSUMPTIONS (labelled by the caller); nothing here is calibrated."""
    lo_e, hi_e = sorted(effect_pp)
    lo_o, hi_o = sorted(offset)
    lo = baseline["interval80"][0] + lo_e / 100 * (1 - hi_o)
    hi = baseline["interval80"][1] + hi_e / 100 * (1 - lo_o)
    mid = baseline["point"] + (lo_e + hi_e) / 200 * (1 - (lo_o + hi_o) / 2)
    return {"family": "simulation.share_shift", "version": FAMILIES["simulation.share_shift"]["version"],
            "outcome_point": mid, "outcome_range": [lo, hi], "baseline_point": baseline["point"],
            "effect_pp_assumed": [lo_e, hi_e], "competitor_offset_assumed": [lo_o, hi_o]}

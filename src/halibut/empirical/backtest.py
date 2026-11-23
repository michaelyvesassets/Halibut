"""Rolling-origin backtests with strict information-availability controls.

For each origin date d:
1. ``view = store.as_of(d)``: only observations (and their revisions)
   published on or before d. Everything the forecasters see comes from this
   view; the audit records the newest availability read and fails if it is
   after d or if any observed month is not strictly before d's month.
2. Halibut is calibrated on moments from that view, and every baseline is fit
   on the same observed series.
3. Forecasts cover months last_observed+1 .. +H.

Only afterwards are forecasts scored against the *latest* data
(``store.latest()``), on common target months and with common metrics for
every model. Each origin's result is written atomically and skipped on
rerun, so an interrupted backtest resumes.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path

import numpy as np

from ..data.store import Store, to_timestamp
from ..version import ENGINE_VERSION
from . import baselines as bl
from .fit import calibrate, forecast
from .measurement import month_at, month_index, observed_shares

Z80, Z95 = 1.2816, 1.96


class LeakageAuditError(RuntimeError):
    pass


def origin_inputs(store: Store, spec, origin: str) -> dict:
    view = store.as_of(origin)
    obs = observed_shares(view, spec)
    covariates = {c.name: dict(view.series(c.series, spec.geo)) for c in spec.covariates}
    origin_month = origin[:7]
    if not obs:
        raise ValueError(f"no observations available at origin {origin}")
    last = max(obs)
    if last >= origin_month or any(p >= origin_month for c in covariates.values() for p in c):
        raise LeakageAuditError(f"origin {origin}: data for {origin_month} or later is visible")
    if view.max_available_at_used and view.max_available_at_used > to_timestamp(origin):
        raise LeakageAuditError(f"origin {origin}: read data available at {view.max_available_at_used}")
    return {"view_t": view.t, "obs": obs, "covariates": covariates, "last_observed": last,
            "max_available_at_used": view.max_available_at_used,
            "events_known": [e["id"] for e in view.events(spec.geo)]}


def spec_hash(spec, store: Store) -> str:
    snaps = [r[0] for r in store.conn.execute("SELECT sha256 FROM snapshots ORDER BY sha256")]
    ident = {"engine": ENGINE_VERSION, "spec": spec.path.read_text(), "template": spec.template.read_text(),
             "snapshots": snaps}
    return hashlib.sha256(json.dumps(ident, sort_keys=True).encode()).hexdigest()


def run_origin(spec, store: Store, origin: str, workers=1, cache_dir=None, log=print) -> dict:
    t0 = time.perf_counter()
    inp = origin_inputs(store, spec, origin)
    obs, last = inp["obs"], inp["last_observed"]
    H = spec.horizon_months
    log(f"  origin {origin}: data through {last}; calibrating {len(spec.parameters)} parameters")
    fit = calibrate(spec, obs, inp["covariates"], workers, cache_dir, log=lambda m: log(m))
    fc = forecast(spec, fit, obs, inp["covariates"], H, workers, cache_dir)
    periods = sorted(obs)
    y = np.array([obs[p][spec.target_product] for p in periods])
    fit_start = next(i for i, p in enumerate(periods) if int(p[:4]) >= spec.annual_from)
    models = {"halibut": {"point": fc["point"], "sd": fc["sd"]}}
    for name, res in bl.all_baselines(y, H, fit_start).items():
        models[name] = {"point": [float(x) for x in res["point"]], "sd": [float(x) for x in res["sd"]],
                        **({"params": res["params"]} if "params" in res else {})}
    targets = [month_at(month_index(last, spec.start_period) + h, spec.start_period) for h in range(1, H + 1)]
    naive_scale = float(np.mean(np.abs(np.diff(y[-37:]))))  # in-sample one-step naive MAE (MASE scale)
    hist = {p: fc["fitted"][p] for p in periods}
    in_sample = {"mae_all_from_annual_from": float(np.mean([abs(obs[p][spec.target_product] - hist[p])
                                                             for p in periods[fit_start:]])),
                 "mae_last_36m": fc["in_sample_mae_36m"]}
    return {"origin": origin, "last_observed": last, "targets": targets, "models": models,
            "fit": fit, "in_sample": in_sample, "naive_scale": naive_scale,
            "residual_sd": fc["residual_sd"], "audit": {k: inp[k] for k in ("view_t", "max_available_at_used", "events_known")},
            "seconds": time.perf_counter() - t0}


def evaluate(spec, store: Store, origin_results: list[dict]) -> dict:
    """Score every model on common target months against the latest data."""
    actual = {p: v[spec.target_product] for p, v in observed_shares(store.latest(), spec).items()}
    rows = []
    for r in origin_results:
        for name, m in r["models"].items():
            for h, (tm, f, sd) in enumerate(zip(r["targets"], m["point"], m["sd"]), start=1):
                if tm not in actual:
                    continue
                a = actual[tm]
                rows.append({"origin": r["origin"], "model": name, "h": h, "target": tm, "forecast": f,
                             "actual": a, "error": f - a, "sd": sd,
                             "in80": abs(f - a) <= Z80 * sd, "in95": abs(f - a) <= Z95 * sd,
                             "scaled": abs(f - a) / r["naive_scale"] if r["naive_scale"] > 0 else math.nan})
    models = sorted({x["model"] for x in rows}, key=lambda m: (m != "halibut", m))
    buckets = {"h1-6": (1, 6), "h7-12": (7, 12), "h13-24": (13, 24), "all": (1, 10 ** 6)}
    summary = {}
    for m in models:
        mr = [x for x in rows if x["model"] == m]
        entry = {}
        for b, (lo, hi) in buckets.items():
            sel = [x for x in mr if lo <= x["h"] <= hi]
            if not sel:
                continue
            e = np.array([x["error"] for x in sel])
            entry[b] = {"n": len(sel), "mae_pp": float(np.mean(np.abs(e)) * 100), "rmse_pp": float(np.sqrt(np.mean(e ** 2)) * 100),
                        "bias_pp": float(np.mean(e) * 100), "mase": float(np.nanmean([x["scaled"] for x in sel])),
                        "coverage80": float(np.mean([x["in80"] for x in sel])),
                        "coverage95": float(np.mean([x["in95"] for x in sel]))}
        windows = {}
        for lo, hi in spec.change_windows:
            sel = [x for x in mr if lo <= x["target"] <= hi]
            if sel:
                windows[f"{lo}..{hi}"] = {"n": len(sel), "mae_pp": float(np.mean([abs(x["error"]) for x in sel]) * 100)}
        entry["change_windows"] = windows
        per_origin = {}
        for o in sorted({x["origin"] for x in mr}):
            sel = [x for x in mr if x["origin"] == o]
            per_origin[o] = float(np.mean([abs(x["error"]) for x in sel]) * 100)
        entry["mae_pp_by_origin"] = per_origin
        summary[m] = entry
    ranking = sorted(models, key=lambda m: summary[m]["all"]["mae_pp"])
    hal = summary.get("halibut", {}).get("all", {}).get("mae_pp")
    better = [m for m in models if m != "halibut" and hal is not None and summary[m]["all"]["mae_pp"] < hal]
    beaten_by_origin = {}
    for o in summary.get("halibut", {}).get("mae_pp_by_origin", {}):
        h_mae = summary["halibut"]["mae_pp_by_origin"][o]
        beaten_by_origin[o] = [m for m in models if m != "halibut" and summary[m]["mae_pp_by_origin"].get(o, math.inf) < h_mae]
    return {"rows": rows, "summary": summary, "ranking_by_mae": ranking,
            "baselines_better_than_halibut": better, "baselines_better_by_origin": beaten_by_origin}


def estimate_backtest(spec, store: Store, origins=None) -> dict:
    from ..batch import format_duration, seconds_per_scenario_run  # noqa: PLC0415
    from .builder import build_config  # noqa: PLC0415
    origins = list(origins or spec.origins)
    mid = {fp.param.name: fp.param.value(0.5) for fp in spec.parameters}
    total = 0.0
    per = {}
    k = len(spec.parameters)
    evals = spec.popsize * k * (spec.maxiter + 1) + spec.nm_maxfev
    for o in origins:
        last = origin_inputs(store, spec, o)["last_observed"]
        n = month_index(last, spec.start_period) + 1
        cfg = build_config(spec, mid, n, {}, spec.calib_seeds)
        s = seconds_per_scenario_run(cfg)
        secs = 1.4 * s * (evals * len(spec.calib_seeds) + len(spec.forecast_seeds) * (1 + spec.horizon_months / n))
        per[o] = secs
        total += secs
    return {"origins": origins, "evaluations_per_origin": evals, "seconds_per_origin": per,
            "seconds_serial": total, "human_serial": format_duration(total)}


def run_backtest(spec, store: Store, out_dir, origins=None, workers=1, cache_dir=None, log=print) -> dict:
    origins = list(origins or spec.origins)
    h = spec_hash(spec, store)
    path = Path(out_dir) / f"{spec.name}_{h[:12]}"
    path.mkdir(parents=True, exist_ok=True)
    results = []
    for o in origins:
        f = path / f"origin_{o}.json"
        if f.exists():
            log(f"  origin {o}: already computed, loading {f.name}")
            results.append(json.loads(f.read_text()))
            continue
        r = run_origin(spec, store, o, workers, cache_dir, log)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(r, indent=1))
        tmp.replace(f)
        log(f"  origin {o}: done in {r['seconds']:.0f}s (L={r['fit']['objective']:.2f}, "
            f"at bounds: {r['fit']['parameters_at_bounds'] or 'none'})")
        results.append(r)
    ev = evaluate(spec, store, results)
    return {"path": path, "spec_hash": h, "origins": results, "evaluation": ev}

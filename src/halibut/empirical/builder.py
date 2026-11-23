"""Build a validated experiment config from fitted parameters and as-of data."""

from __future__ import annotations

import copy
import math

from ..config import ConfigError, load_raw, parse_config
from ..params import set_paths
from .measurement import month_at, month_index


def covariate_path(values: dict[str, float], cov, start: str, n_ticks: int) -> list[float]:
    """Transformed covariate for ticks 0..n_ticks-1 from AVAILABLE values only.

    Ticks after the last available month hold the last transformed value
    (persistence); ticks before the first available month are 0.
    """
    periods = sorted(values)
    raw_by_tick: dict[int, float] = {}
    for p in periods:
        if values[p] is not None and values[p] > 0:
            raw_by_tick[month_index(p, start)] = values[p]
    out, last = [], 0.0
    hist: list[float] = []
    all_periods = sorted(p for p in periods if values[p] is not None and values[p] > 0)
    # include pre-start history for trailing means
    pre = [values[p] for p in all_periods if month_index(p, start) < 0]
    hist.extend(math.log(v) for v in pre)
    for t in range(n_ticks):
        if t in raw_by_tick:
            x = math.log(raw_by_tick[t])
            if cov.transform == "level":
                last = raw_by_tick[t]
            else:
                window = hist[-cov.window:]
                last = x - (sum(window) / len(window)) if window else 0.0
            hist.append(x)
        out.append(last)
    return out


def build_config(spec, values: dict[str, float], n_ticks: int, covariates: dict[str, dict[str, float]],
                 seeds) -> object:
    """Experiment config for parameter ``values`` (name -> value) over ``n_ticks`` months.

    ``covariates`` maps covariate name -> {period: value}, already restricted
    to what was available at the forecast origin.
    """
    raw = copy.deepcopy(load_raw(spec.template))
    if raw.get("decision", {}).get("temperature", 1.0) != 1.0:
        raise ConfigError("empirical calibration requires decision.temperature = 1")
    raw["experiment"].update(weeks=int(n_ticks), seeds=list(seeds), summary_window_weeks=1)
    raw["experiment"].pop("replicates", None)
    raw["experiment"].pop("base_seed", None)
    years = [t / 12.0 for t in range(n_ticks)]
    coef = {fp.ref: values[fp.param.name] for fp in spec.parameters if fp.kind == "coefficient"}
    intercept = {fp.ref: values[fp.param.name] for fp in spec.parameters if fp.kind == "intercept"}
    trend = {fp.ref: values[fp.param.name] for fp in spec.parameters if fp.kind == "trend"}
    paths = {p: [intercept.get(p, 0.0) + trend.get(p, 0.0) * y for y in years] for p in spec.product_ids}
    for cov in spec.covariates:
        series = covariate_path(covariates.get(cov.name, {}), cov, spec.start_period, n_ticks)
        b = coef.get(cov.coefficient, 0.0)
        for p, loading in cov.loadings.items():
            paths[p] = [q + loading * b * x for q, x in zip(paths[p], series)]
    raw["drivers"] = [{"type": "product_path", "product": p, "attribute": "quality", "op": "set",
                       "values": [round(v, 10) for v in path]} for p, path in paths.items()]
    overrides = {fp.ref: values[fp.param.name] for fp in spec.parameters if fp.kind == "config"}
    raw = set_paths(raw, overrides)
    cfg = parse_config(raw)
    if cfg.decision.temperature != 1.0:
        raise ConfigError("empirical calibration requires decision.temperature = 1")
    return cfg


def tick_periods(spec, n_ticks: int) -> list[str]:
    return [month_at(t, spec.start_period) for t in range(n_ticks)]

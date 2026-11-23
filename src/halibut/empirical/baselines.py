"""Simple forecasting and diffusion baselines for a share series in (0, 1).

All baselines receive exactly the same input as Halibut: the observed monthly
series available at the origin. Each returns point forecasts for horizons
1..H and an interval SD per horizon using one documented rule per class:

- random-walk class (naive, drift, Holt): sd_h = sigma_1 * sqrt(h), where
  sigma_1 is the in-sample one-step residual SD (logit scale, mapped back by
  the delta method);
- curve class (linear trend, logistic, Bass): constant sd = SD of in-sample
  residuals over the last 36 months (the rule Halibut uses).

Models working on the logit scale clip shares to [1e-4, 1 - 1e-4].
"""

from __future__ import annotations

import math

import numpy as np
from scipy import optimize

EPS = 1e-4


def logit(x):
    x = np.clip(np.asarray(x, dtype=float), EPS, 1 - EPS)
    return np.log(x / (1 - x))


def expit(z):
    return 1.0 / (1.0 + np.exp(-np.asarray(z, dtype=float)))


def _delta_sd(level_logit, sd_logit):
    p = expit(level_logit)
    return p * (1 - p) * sd_logit


def naive(y: np.ndarray, H: int) -> dict:
    z = logit(y)
    s1 = float(np.std(np.diff(z[-36:]), ddof=1)) if len(z) > 3 else 0.0
    point = np.full(H, y[-1])
    return {"point": point, "sd": np.array([_delta_sd(z[-1], s1 * math.sqrt(h)) for h in range(1, H + 1)])}


def drift(y: np.ndarray, H: int, window: int = 24) -> dict:
    z = logit(y)
    d = np.diff(z[-(window + 1):])
    slope = float(d.mean())
    s1 = float(np.std(d - slope, ddof=1)) if len(d) > 2 else 0.0
    zf = z[-1] + slope * np.arange(1, H + 1)
    return {"point": expit(zf), "sd": np.array([_delta_sd(zf[h - 1], s1 * math.sqrt(h)) for h in range(1, H + 1)])}


def linear_trend(y: np.ndarray, H: int, window: int = 36) -> dict:
    z = logit(y[-window:])
    t = np.arange(len(z))
    b, a = np.polyfit(t, z, 1)
    zf = a + b * (len(z) - 1 + np.arange(1, H + 1))
    resid = expit(a + b * t) - y[-window:]
    return {"point": expit(zf), "sd": np.full(H, float(np.std(resid, ddof=1)))}


def holt(y: np.ndarray, H: int, fit_from: int = 0) -> dict:
    """Holt's linear exponential smoothing on the logit share; alpha, beta by one-step SSE."""
    z = logit(y[fit_from:])

    def run(params):
        a, b = params
        level, trend = z[0], z[1] - z[0]
        errs = []
        for x in z[1:]:
            f = level + trend
            errs.append(x - f)
            new_level = a * x + (1 - a) * f
            trend = b * (new_level - level) + (1 - b) * trend
            level = new_level
        return level, trend, np.array(errs)

    res = optimize.minimize(lambda p: float(np.sum(run(p)[2] ** 2)), x0=[0.5, 0.1],
                            bounds=[(0.01, 0.99), (0.001, 0.99)], method="L-BFGS-B")
    level, trend, errs = run(res.x)
    zf = level + trend * np.arange(1, H + 1)
    s1 = float(np.std(errs[-36:], ddof=1))
    return {"point": expit(zf), "sd": np.array([_delta_sd(zf[h - 1], s1 * math.sqrt(h)) for h in range(1, H + 1)]),
            "params": {"alpha": float(res.x[0]), "beta": float(res.x[1])}}


def _curve_fit(fun, t, y, starts, bounds):
    best = None
    for x0 in starts:
        try:
            r = optimize.least_squares(lambda p: fun(t, *p) - y, x0, bounds=bounds)
        except ValueError:
            continue
        if best is None or r.cost < best.cost:
            best = r
    return best


def logistic_curve(t, K, r, t0):
    return K / (1 + np.exp(-r * (t - t0)))


def logistic(y: np.ndarray, H: int, t: np.ndarray) -> dict:
    """3-parameter logistic (Fisher-Pry substitution curve) fit by least squares."""
    starts = [(0.9, 0.05, t[-1]), (1.0, 0.03, t[-1] + 60), (0.6, 0.1, t[-1] - 24), (1.0, 0.08, t[-1] + 120)]
    r = _curve_fit(logistic_curve, t, y, starts, ([0.01, 1e-4, -1e3], [1.0, 1.0, 1e4]))
    tf = t[-1] + np.arange(1, H + 1)
    resid = logistic_curve(t[-36:], *r.x) - y[-36:]
    return {"point": logistic_curve(tf, *r.x), "sd": np.full(H, float(np.std(resid, ddof=1))),
            "params": dict(zip(("K", "r", "t0"), map(float, r.x)))}


def bass_curve(t, p, q, m):
    e = np.exp(-(p + q) * np.maximum(t, 0))
    return m * (1 - e) / (1 + (q / p) * e)


def bass(y: np.ndarray, H: int, t: np.ndarray) -> dict:
    """Bass diffusion curve m * F(t) applied to the share series (t in months from the fit start)."""
    starts = [(1e-3, 0.05, 0.95), (1e-4, 0.1, 1.0), (1e-2, 0.02, 0.8), (1e-5, 0.08, 1.0)]
    r = _curve_fit(bass_curve, t, y, starts, ([1e-7, 1e-4, 0.01], [0.2, 1.0, 1.0]))
    tf = t[-1] + np.arange(1, H + 1)
    resid = bass_curve(t[-36:], *r.x) - y[-36:]
    return {"point": bass_curve(tf, *r.x), "sd": np.full(H, float(np.std(resid, ddof=1))),
            "params": dict(zip(("p", "q", "m"), map(float, r.x)))}


def all_baselines(y: np.ndarray, H: int, fit_start_index: int) -> dict:
    """Run every baseline. ``y`` is the monthly share series ending at the last
    observed month; curve models use months from ``fit_start_index``."""
    t = np.arange(len(y), dtype=float) - fit_start_index
    yc, tc = y[fit_start_index:], t[fit_start_index:]
    return {
        "naive": naive(y, H),
        "drift": drift(y, H),
        "linear_trend": linear_trend(y, H),
        "holt": holt(y, H, fit_from=fit_start_index),
        "logistic": logistic(yc, H, tc),
        "bass": bass(yc, H, tc),
    }

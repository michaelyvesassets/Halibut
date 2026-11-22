"""Parametric distributions used to generate synthetic consumer attributes.

A distribution is specified in configuration as a table, e.g.
``{ dist = "beta", a = 2, b = 5 }``, or as a bare number (a constant).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


class DistributionError(ValueError):
    """Raised for an invalid distribution specification."""


# kind -> (required params, optional params)
_SPECS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "constant": (("value",), ()),
    "uniform": (("low", "high"), ()),
    "normal": (("mean", "sd"), ("min", "max")),
    "lognormal": (("median", "sigma"), ("min", "max")),
    "beta": (("a", "b"), ("low", "high")),
}


@dataclass(frozen=True)
class Distribution:
    kind: str
    params: tuple[tuple[str, float], ...]

    def get(self, name: str, default: float | None = None) -> float | None:
        return dict(self.params).get(name, default)

    def sample(self, rng: np.random.Generator, n: int) -> np.ndarray:
        p = dict(self.params)
        if self.kind == "constant":
            x = np.full(n, p["value"], dtype=float)
        elif self.kind == "uniform":
            x = rng.uniform(p["low"], p["high"], n)
        elif self.kind == "normal":
            x = rng.normal(p["mean"], p["sd"], n)
        elif self.kind == "lognormal":
            x = rng.lognormal(math.log(p["median"]), p["sigma"], n)
        elif self.kind == "beta":
            low, high = p.get("low", 0.0), p.get("high", 1.0)
            x = low + (high - low) * rng.beta(p["a"], p["b"], n)
        else:  # pragma: no cover - guarded by parse_distribution
            raise DistributionError(f"unknown distribution {self.kind!r}")
        if "min" in p or "max" in p:
            x = np.clip(x, p.get("min", -np.inf), p.get("max", np.inf))
        return x

    def to_dict(self) -> dict:
        return {"dist": self.kind, **dict(self.params)}


def parse_distribution(spec: object, where: str) -> Distribution:
    """Parse and validate a distribution specification."""
    if isinstance(spec, bool):
        raise DistributionError(f"{where}: expected a number or distribution table")
    if isinstance(spec, (int, float)):
        return Distribution("constant", (("value", float(spec)),))
    if not isinstance(spec, dict):
        raise DistributionError(f"{where}: expected a number or distribution table")

    spec = dict(spec)
    kind = spec.pop("dist", None)
    if kind not in _SPECS:
        raise DistributionError(f"{where}: 'dist' must be one of {sorted(_SPECS)}, got {kind!r}")
    required, optional = _SPECS[kind]
    missing = [k for k in required if k not in spec]
    unknown = [k for k in spec if k not in required + optional]
    if missing:
        raise DistributionError(f"{where}: {kind} distribution missing {missing}")
    if unknown:
        raise DistributionError(f"{where}: {kind} distribution has unknown parameters {unknown}")
    for k, v in spec.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise DistributionError(f"{where}.{k}: expected a finite number")
    p = {k: float(v) for k, v in spec.items()}

    def fail(msg: str) -> None:
        raise DistributionError(f"{where}: {msg}")

    if kind == "uniform" and p["low"] > p["high"]:
        fail("uniform requires low <= high")
    if kind == "normal" and p["sd"] < 0:
        fail("normal requires sd >= 0")
    if kind == "lognormal" and (p["median"] <= 0 or p["sigma"] < 0):
        fail("lognormal requires median > 0 and sigma >= 0")
    if kind == "beta":
        if p["a"] <= 0 or p["b"] <= 0:
            fail("beta requires a > 0 and b > 0")
        if p.get("low", 0.0) >= p.get("high", 1.0):
            fail("beta requires low < high")
    if "min" in p and "max" in p and p["min"] > p["max"]:
        fail("min must be <= max")

    return Distribution(kind, tuple(sorted(p.items())))

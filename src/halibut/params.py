"""Config overrides by path, and parameter spaces for analyses.

Path grammar (applied to the raw TOML dictionary, before validation)::

    decision.outside_utility
    products[matcha].price
    population.segments[mainstream].attributes.price_sensitivity.median
    scenarios[price_war].interventions[0].value

``[selector]`` on a list picks an item by integer index, or by its ``name``
or ``id`` field. Every overridden config is re-validated by
``halibut.config.parse_config`` and gets its own config hash.
"""

from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .config import ConfigError, _Table

_SEGMENT = re.compile(r"^([A-Za-z_][\w]*)(?:\[([^\]]+)\])?$")


def _parse_path(path: str) -> list[tuple[str, str | None]]:
    parts = []
    for seg in path.split("."):
        m = _SEGMENT.match(seg)
        if not m:
            raise ConfigError(f"invalid path segment {seg!r} in {path!r}")
        parts.append((m.group(1), m.group(2)))
    return parts


def _select(items: Any, selector: str, path: str):
    if not isinstance(items, list):
        raise ConfigError(f"{path}: [{selector}] applied to a non-list")
    if selector.lstrip("-").isdigit():
        i = int(selector)
        if not -len(items) <= i < len(items):
            raise ConfigError(f"{path}: index {i} out of range")
        return items, i
    for i, item in enumerate(items):
        if isinstance(item, dict) and selector in (item.get("name"), item.get("id")):
            return items, i
    raise ConfigError(f"{path}: no item named {selector!r}")


def _locate(raw: dict, path: str):
    """Return (container, key) such that container[key] is the value at ``path``."""
    parts = _parse_path(path)
    node: Any = raw
    for depth, (key, selector) in enumerate(parts):
        last = depth == len(parts) - 1
        if not isinstance(node, dict):
            raise ConfigError(f"{path}: {key!r} is not inside a table")
        if selector is None:
            if last:
                return node, key
            if key not in node:
                raise ConfigError(f"{path}: missing key {key!r}")
            node = node[key]
        else:
            if key not in node:
                raise ConfigError(f"{path}: missing key {key!r}")
            container, i = _select(node[key], selector, path)
            if last:
                return container, i
            node = container[i]
    raise ConfigError(f"{path}: empty path")  # pragma: no cover


def get_path(raw: dict, path: str) -> Any:
    container, key = _locate(raw, path)
    try:
        return container[key]
    except (KeyError, IndexError):
        raise ConfigError(f"{path}: not present") from None


def set_paths(raw: dict, values: dict[str, Any]) -> dict:
    """Return a deep copy of ``raw`` with each path set (tables are created as needed
    only for the final key; intermediate keys must exist)."""
    out = copy.deepcopy(raw)
    for path, value in values.items():
        container, key = _locate(out, path)
        container[key] = value
    return out


# --------------------------------------------------------------------------- parameter spaces


@dataclass(frozen=True)
class Parameter:
    """An uncertain input. ``mode = "set"`` writes the value at every path;
    ``mode = "scale"`` multiplies each path's base value by it (to vary e.g. a
    segment-level attribute coherently across segments)."""

    name: str
    paths: tuple[str, ...]
    low: float
    high: float
    scale: str = "linear"
    mode: str = "set"
    integer: bool = False

    def value(self, u: float) -> float:
        if not 0.0 <= u <= 1.0:
            raise ValueError(f"{self.name}: unit coordinate {u} outside [0, 1]")
        if self.scale == "log":
            x = math.exp(math.log(self.low) + u * (math.log(self.high) - math.log(self.low)))
        else:
            x = self.low + u * (self.high - self.low)
        return float(round(x)) if self.integer else x

    def unit(self, x: float) -> float:
        if self.scale == "log":
            return (math.log(x) - math.log(self.low)) / (math.log(self.high) - math.log(self.low))
        return (x - self.low) / (self.high - self.low)


@dataclass(frozen=True)
class ParameterSpace:
    parameters: tuple[Parameter, ...]

    @property
    def names(self) -> list[str]:
        return [p.name for p in self.parameters]

    @property
    def dimension(self) -> int:
        return len(self.parameters)

    def values(self, unit_point) -> dict[str, float]:
        unit_point = np.asarray(unit_point, dtype=float)
        if unit_point.shape != (self.dimension,):
            raise ValueError(f"expected a point of dimension {self.dimension}")
        return {p.name: p.value(float(u)) for p, u in zip(self.parameters, unit_point)}

    def apply(self, raw: dict, values: dict[str, float]) -> dict:
        overrides = {}
        for p in self.parameters:
            v = values[p.name]
            for path in p.paths:
                if p.mode == "scale":
                    base = get_path(raw, path)
                    if isinstance(base, bool) or not isinstance(base, (int, float)):
                        raise ConfigError(f"{p.name}: {path} is not numeric")
                    overrides[path] = base * v
                else:
                    overrides[path] = int(v) if p.integer else v
        return set_paths(raw, overrides)

    def validate_against(self, raw: dict) -> None:
        for p in self.parameters:
            for path in p.paths:
                v = get_path(raw, path)
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    raise ConfigError(f"parameter {p.name}: {path} is not numeric in the base config")


def parse_parameters(tables: list[_Table]) -> ParameterSpace:
    params = []
    for t in tables:
        name = t.string("name")
        path = t.raw("path", None)
        paths = t.raw("paths", None)
        if (path is None) == (paths is None):
            raise ConfigError(f"{t.path}: give exactly one of 'path' or 'paths'")
        paths = (path,) if path is not None else tuple(paths)
        if not paths or not all(isinstance(x, str) for x in paths):
            raise ConfigError(f"{t.path}: paths must be strings")
        rng = t.raw("range")
        if (not isinstance(rng, list) or len(rng) != 2
                or not all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in rng)):
            raise ConfigError(f"{t.at('range')}: expected [low, high]")
        low, high = float(rng[0]), float(rng[1])
        if not low < high:
            raise ConfigError(f"{t.at('range')}: low must be < high")
        scale = t.string("scale", "linear", choices=("linear", "log"))
        if scale == "log" and low <= 0:
            raise ConfigError(f"{t.at('range')}: log scale requires low > 0")
        mode = t.string("mode", "set", choices=("set", "scale"))
        integer = t.boolean("integer", False)
        t.done()
        params.append(Parameter(name, paths, low, high, scale, mode, integer))
    names = [p.name for p in params]
    if len(set(names)) != len(names):
        raise ConfigError("parameters: names must be unique")
    if not params:
        raise ConfigError("parameters: at least one parameter is required")
    return ParameterSpace(tuple(params))


def parse_overrides(t: _Table | None) -> dict[str, Any]:
    """``[overrides]`` table: quoted path keys -> values, e.g. ``"population.size" = 2000``."""
    if t is None:
        return {}
    out = dict(t.data)
    t.used.update(out)
    return out


def resolve_relative(spec_path: str | Path, target: str) -> Path:
    p = Path(target)
    return p if p.is_absolute() else (Path(spec_path).resolve().parent / p)

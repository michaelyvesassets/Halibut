"""Versioned, validated experiment configuration loaded from TOML.

Every parameter of a run lives here; nothing in the engine hardcodes a market
assumption. ``config_hash`` fingerprints the fully resolved configuration
(defaults included) so results can be traced to the exact inputs.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .distributions import Distribution, DistributionError, parse_distribution
from .interventions import (
    OPERATIONS,
    POPULATION_ATTRIBUTES,
    PRODUCT_ATTRIBUTES,
    TARGETS,
    Intervention,
    PopulationChange,
    ProductChange,
    Schedule,
    SeedAdopters,
)
from .population import CONSUMER_ATTRIBUTES
from .version import CONFIG_SCHEMA_VERSION

NO_PRODUCT = "none"
CONTROL = "control"


class ConfigError(ValueError):
    """Raised for an invalid configuration, with the offending key path."""


@dataclass(frozen=True)
class ProductConfig:
    id: str
    category: str
    price: float
    quality: float
    availability: float


@dataclass(frozen=True)
class SegmentConfig:
    name: str
    weight: float
    initial_product_shares: dict[str, float]
    attributes: dict[str, Distribution]
    preferences: dict[str, Distribution]


@dataclass(frozen=True)
class PopulationConfig:
    size: int
    segments: list[SegmentConfig]


@dataclass(frozen=True)
class DecisionConfig:
    model: str = "logit"
    temperature: float = 1.0
    outside_utility: float = 0.0
    preference_weight: float = 1.0
    quality_weight: float = 1.0


@dataclass(frozen=True)
class NetworkConfig:
    type: str = "watts_strogatz"
    k: int = 10
    rewire_prob: float = 0.1


@dataclass(frozen=True)
class SocialInfluenceConfig:
    enabled: bool = True
    weight: float = 1.0


@dataclass(frozen=True)
class PreferenceDriftConfig:
    enabled: bool = True
    rate: float = 0.02


@dataclass(frozen=True)
class ScenarioConfig:
    name: str
    description: str = ""
    interventions: list[Intervention] = field(default_factory=list)


@dataclass(frozen=True)
class ExperimentConfig:
    schema_version: str
    name: str
    description: str
    weeks: int
    seeds: list[int]
    summary_window_weeks: int
    products: list[ProductConfig]
    population: PopulationConfig
    decision: DecisionConfig
    network: NetworkConfig
    social_influence: SocialInfluenceConfig
    preference_drift: PreferenceDriftConfig
    scenarios: list[ScenarioConfig]

    @property
    def product_ids(self) -> tuple[str, ...]:
        return tuple(p.id for p in self.products)

    @property
    def scenario_names(self) -> list[str]:
        return [s.name for s in self.scenarios]

    def scenario(self, name: str) -> ScenarioConfig:
        for s in self.scenarios:
            if s.name == name:
                return s
        raise KeyError(f"unknown scenario {name!r}; available: {self.scenario_names}")

    def to_dict(self) -> dict:
        return _serialise(self)


def _serialise(obj: Any) -> Any:
    if isinstance(obj, Distribution):
        return obj.to_dict()
    if dataclasses.is_dataclass(obj):
        out = {"type": obj.TYPE} if hasattr(obj, "TYPE") else {}
        for f in dataclasses.fields(obj):
            out[f.name] = _serialise(getattr(obj, f.name))
        return out
    if isinstance(obj, dict):
        return {k: _serialise(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serialise(v) for v in obj]
    return obj


def config_hash(cfg: ExperimentConfig) -> str:
    canonical = json.dumps(cfg.to_dict(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


# --------------------------------------------------------------------------- parsing

_MISSING = object()


class _Table:
    """Reads keys from a TOML table and rejects keys that were never read."""

    def __init__(self, data: Any, path: str):
        if not isinstance(data, dict):
            raise ConfigError(f"{path}: expected a table")
        self.data, self.path, self.used = data, path, set()

    def at(self, key: str) -> str:
        return f"{self.path}.{key}" if self.path else key

    def raw(self, key: str, default: Any = _MISSING) -> Any:
        self.used.add(key)
        if key in self.data:
            return self.data[key]
        if default is _MISSING:
            raise ConfigError(f"{self.at(key)}: required")
        return default

    def number(self, key, default=_MISSING, *, lo=None, hi=None, integer=False):
        v = self.raw(key, default)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
            raise ConfigError(f"{self.at(key)}: expected a finite number")
        if integer and not isinstance(v, int):
            raise ConfigError(f"{self.at(key)}: expected an integer")
        if lo is not None and v < lo:
            raise ConfigError(f"{self.at(key)}: must be >= {lo}")
        if hi is not None and v > hi:
            raise ConfigError(f"{self.at(key)}: must be <= {hi}")
        return int(v) if integer else float(v)

    def optional_number(self, key, **kw):
        return None if self.raw(key, None) is None else self.number(key, **kw)

    def string(self, key, default=_MISSING, choices=None) -> str:
        v = self.raw(key, default)
        if not isinstance(v, str) or not v:
            raise ConfigError(f"{self.at(key)}: expected a non-empty string")
        if choices is not None and v not in choices:
            raise ConfigError(f"{self.at(key)}: must be one of {list(choices)}, got {v!r}")
        return v

    def boolean(self, key, default=_MISSING) -> bool:
        v = self.raw(key, default)
        if not isinstance(v, bool):
            raise ConfigError(f"{self.at(key)}: expected true or false")
        return v

    def table(self, key, default=_MISSING) -> "_Table":
        return _Table(self.raw(key, default), self.at(key))

    def tables(self, key, default=_MISSING) -> list["_Table"]:
        v = self.raw(key, default)
        if not isinstance(v, list):
            raise ConfigError(f"{self.at(key)}: expected an array of tables")
        return [_Table(item, f"{self.at(key)}[{i}]") for i, item in enumerate(v)]

    def done(self) -> None:
        unknown = sorted(set(self.data) - self.used)
        if unknown:
            raise ConfigError(f"{self.path or '<root>'}: unknown keys {unknown}")


def load_config(path: str | Path) -> ExperimentConfig:
    with open(path, "rb") as fh:
        try:
            data = tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{path}: invalid TOML: {exc}") from exc
    return parse_config(data)


def parse_config(data: dict) -> ExperimentConfig:
    root = _Table(data, "")
    version = root.string("schema_version")
    if version != CONFIG_SCHEMA_VERSION:
        raise ConfigError(
            f"schema_version: this engine supports {CONFIG_SCHEMA_VERSION!r}, got {version!r}"
        )

    exp = root.table("experiment")
    name = exp.string("name")
    description = exp.string("description", "") if exp.raw("description", "") else ""
    weeks = exp.number("weeks", integer=True, lo=1)
    seeds = _parse_seeds(exp)
    window = exp.number("summary_window_weeks", 4, integer=True, lo=1, hi=weeks)
    exp.done()

    products = _parse_products(root.tables("products"))
    product_ids = tuple(p.id for p in products)
    population = _parse_population(root.table("population"), product_ids)

    d = root.table("decision", {})
    decision = DecisionConfig(
        model=d.string("model", "logit", choices=("logit",)),
        temperature=d.number("temperature", 1.0, lo=0.0),
        outside_utility=d.number("outside_utility", 0.0),
        preference_weight=d.number("preference_weight", 1.0),
        quality_weight=d.number("quality_weight", 1.0),
    )
    d.done()

    nt = root.table("network", {})
    network = NetworkConfig(
        type=nt.string("type", "watts_strogatz", choices=("watts_strogatz",)),
        k=nt.number("k", 10, integer=True, lo=2),
        rewire_prob=nt.number("rewire_prob", 0.1, lo=0.0, hi=1.0),
    )
    nt.done()
    if network.k % 2 or network.k >= population.size:
        raise ConfigError("network.k: must be even and smaller than population.size")

    si = root.table("social_influence", {})
    influence = SocialInfluenceConfig(
        enabled=si.boolean("enabled", True), weight=si.number("weight", 1.0, lo=0.0)
    )
    si.done()

    pd = root.table("preference_drift", {})
    drift = PreferenceDriftConfig(
        enabled=pd.boolean("enabled", True), rate=pd.number("rate", 0.02, lo=0.0, hi=1.0)
    )
    pd.done()

    scenarios = _parse_scenarios(root.tables("scenarios", []), product_ids, weeks)
    root.done()

    return ExperimentConfig(
        schema_version=version,
        name=name,
        description=description,
        weeks=weeks,
        seeds=seeds,
        summary_window_weeks=window,
        products=products,
        population=population,
        decision=decision,
        network=network,
        social_influence=influence,
        preference_drift=drift,
        scenarios=scenarios,
    )


def _parse_seeds(exp: _Table) -> list[int]:
    explicit = exp.raw("seeds", None)
    if explicit is not None:
        if exp.raw("replicates", None) is not None or exp.raw("base_seed", None) is not None:
            raise ConfigError("experiment: give either 'seeds' or 'replicates'/'base_seed', not both")
        if (
            not isinstance(explicit, list)
            or not explicit
            or not all(isinstance(s, int) and not isinstance(s, bool) and s >= 0 for s in explicit)
        ):
            raise ConfigError("experiment.seeds: expected a non-empty list of non-negative integers")
        if len(set(explicit)) != len(explicit):
            raise ConfigError("experiment.seeds: seeds must be unique")
        return list(explicit)
    replicates = exp.number("replicates", 1, integer=True, lo=1)
    base = exp.number("base_seed", 0, integer=True, lo=0)
    return [base + i for i in range(replicates)]


def _parse_products(tables: list[_Table]) -> list[ProductConfig]:
    if not tables:
        raise ConfigError("products: at least one product is required")
    products = []
    for t in tables:
        p = ProductConfig(
            id=t.string("id"),
            category=t.string("category", "default"),
            price=t.number("price", lo=0.0),
            quality=t.number("quality"),
            availability=t.number("availability", 1.0, lo=0.0, hi=1.0),
        )
        t.done()
        if p.id == NO_PRODUCT:
            raise ConfigError(f"{t.path}.id: {NO_PRODUCT!r} is reserved for the no-purchase option")
        products.append(p)
    ids = [p.id for p in products]
    if len(set(ids)) != len(ids):
        raise ConfigError("products: product ids must be unique")
    return products


def _parse_population(t: _Table, product_ids) -> PopulationConfig:
    size = t.number("size", integer=True, lo=1)
    segments = []
    for st in t.tables("segments"):
        name = st.string("name")
        weight = st.number("weight", lo=0.0)
        if weight <= 0:
            raise ConfigError(f"{st.at('weight')}: must be > 0")

        shares_t = st.table("initial_product_shares")
        options = (NO_PRODUCT,) + tuple(product_ids)
        shares = {}
        for key in list(shares_t.data):
            if key not in options:
                raise ConfigError(f"{shares_t.at(key)}: unknown product (expected one of {list(options)})")
            shares[key] = shares_t.number(key, lo=0.0, hi=1.0)
        shares_t.done()
        if abs(sum(shares.values()) - 1.0) > 1e-6:
            raise ConfigError(f"{shares_t.path}: shares must sum to 1 (got {sum(shares.values()):.6f})")

        attrs = _parse_distributions(st.table("attributes"), CONSUMER_ATTRIBUTES)
        prefs = _parse_distributions(st.table("preferences"), product_ids)
        st.done()
        segments.append(SegmentConfig(name, weight, shares, attrs, prefs))
    t.done()
    if not segments:
        raise ConfigError("population.segments: at least one segment is required")
    names = [s.name for s in segments]
    if len(set(names)) != len(names):
        raise ConfigError("population.segments: segment names must be unique")
    return PopulationConfig(size=size, segments=segments)


def _parse_distributions(t: _Table, keys) -> dict[str, Distribution]:
    out = {}
    for k in keys:
        try:
            out[k] = parse_distribution(t.raw(k), t.at(k))
        except DistributionError as exc:
            raise ConfigError(str(exc)) from exc
    t.done()
    return out


def _parse_schedule(t: _Table, weeks: int) -> Schedule:
    start = t.number("start_week", integer=True, lo=0, hi=weeks - 1)
    end = t.optional_number("end_week", integer=True, lo=start + 1)
    ramp = t.number("ramp_weeks", 0, integer=True, lo=0)
    return Schedule(start, end, ramp)


def _parse_target(t: _Table) -> tuple[str, float]:
    target = t.string("target", "all", choices=TARGETS)
    if target == "all":
        if t.raw("fraction", None) not in (None, 1, 1.0):
            raise ConfigError(f"{t.at('fraction')}: only valid with target = 'top_degree'")
        return target, 1.0
    fraction = t.number("fraction", lo=0.0, hi=1.0)
    if fraction <= 0:
        raise ConfigError(f"{t.at('fraction')}: must be > 0")
    return target, fraction


def _parse_intervention(t: _Table, product_ids, weeks: int) -> Intervention:
    kind = t.string("type", choices=(ProductChange.TYPE, PopulationChange.TYPE, SeedAdopters.TYPE))
    if kind == ProductChange.TYPE:
        iv = ProductChange(
            product=t.string("product", choices=product_ids),
            attribute=t.string("attribute", choices=PRODUCT_ATTRIBUTES),
            op=t.string("op", choices=OPERATIONS),
            value=t.number("value"),
            schedule=_parse_schedule(t, weeks),
        )
    elif kind == PopulationChange.TYPE:
        attribute = t.string("attribute", choices=POPULATION_ATTRIBUTES)
        op = t.string("op", choices=OPERATIONS)
        value = t.number("value")
        schedule = _parse_schedule(t, weeks)
        target, fraction = _parse_target(t)
        iv = PopulationChange(attribute, op, value, schedule, target, fraction)
    else:
        product = t.string("product", choices=product_ids)
        week = t.number("week", integer=True, lo=0, hi=weeks - 1)
        target = t.string("target", "top_degree", choices=TARGETS)
        fraction = t.number("fraction", lo=0.0, hi=1.0) if target == "top_degree" else 1.0
        preference = t.optional_number("preference", lo=0.0, hi=1.0)
        iv = SeedAdopters(product, week, fraction, target, preference)
    t.done()
    return iv


def _parse_scenarios(tables: list[_Table], product_ids, weeks: int) -> list[ScenarioConfig]:
    scenarios = []
    for t in tables:
        s = ScenarioConfig(
            name=t.string("name"),
            description=t.raw("description", "") or "",
            interventions=[
                _parse_intervention(it, product_ids, weeks) for it in t.tables("interventions", [])
            ],
        )
        t.done()
        scenarios.append(s)
    names = [s.name for s in scenarios]
    if len(set(names)) != len(names):
        raise ConfigError("scenarios: scenario names must be unique")
    if CONTROL in names:
        if scenarios[names.index(CONTROL)].interventions:
            raise ConfigError("scenarios: the 'control' scenario must have no interventions")
    else:
        scenarios.insert(0, ScenarioConfig(CONTROL, "Zero-intervention control (added automatically)."))
    # control first, so reports read control -> treatments
    scenarios.sort(key=lambda s: s.name != CONTROL)
    return scenarios

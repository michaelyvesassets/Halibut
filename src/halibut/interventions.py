"""Scheduled experimental interventions.

Interventions are pure functions of the simulation week: the effective market
and population attributes for week ``t`` are recomputed from the immutable base
values plus every intervention active at ``t``. Interventions never draw random
numbers, so a scenario is bit-for-bit identical to the control before its first
intervention starts (common random numbers).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import ClassVar, Union

import numpy as np

from .market import Market
from .network import SocialNetwork
from .population import Population

PRODUCT_ATTRIBUTES = ("price", "quality", "availability")
POPULATION_ATTRIBUTES = (
    "budget",
    "consumption",
    "price_sensitivity",
    "brand_loyalty",
    "social_susceptibility",
    "switching_resistance",
)
OPERATIONS = ("set", "multiply", "add")
TARGETS = ("all", "top_degree")


@dataclass(frozen=True)
class Schedule:
    """Active on weeks ``start_week <= t < end_week`` (``end_week=None``: forever).

    With ``ramp_weeks > 0`` the effect phases in linearly, reaching full
    strength on week ``start_week + ramp_weeks - 1``.
    """

    start_week: int
    end_week: int | None = None
    ramp_weeks: int = 0

    def intensity(self, week: int) -> float:
        if week < self.start_week or (self.end_week is not None and week >= self.end_week):
            return 0.0
        if self.ramp_weeks <= 0:
            return 1.0
        return min(1.0, (week - self.start_week + 1) / self.ramp_weeks)


@dataclass(frozen=True)
class ProductChange:
    TYPE: ClassVar[str] = "product_change"
    product: str
    attribute: str
    op: str
    value: float
    schedule: Schedule


@dataclass(frozen=True)
class PopulationChange:
    TYPE: ClassVar[str] = "population_change"
    attribute: str
    op: str
    value: float
    schedule: Schedule
    target: str = "all"
    fraction: float = 1.0


@dataclass(frozen=True)
class SeedAdopters:
    """One-off event: at ``week`` the targeted consumers become adopters of
    ``product`` (it becomes their current product and last-week purchase), and
    optionally their preference for it is set to ``preference``."""

    TYPE: ClassVar[str] = "seed_adopters"
    product: str
    week: int
    fraction: float
    target: str = "top_degree"
    preference: float | None = None


Intervention = Union[ProductChange, PopulationChange, SeedAdopters]


def _apply(base, op: str, value: float, intensity: float):
    if op == "set":
        target = value
    elif op == "multiply":
        target = base * value
    else:
        target = base + value
    return base + intensity * (target - base)


def select_targets(target: str, fraction: float, network: SocialNetwork) -> np.ndarray:
    """Indices of targeted consumers. ``top_degree`` ties break by index."""
    n = network.n
    if target == "all":
        return np.arange(n)
    count = int(np.ceil(fraction * n))
    order = np.argsort(-network.degree, kind="stable")
    return np.sort(order[:count])


def resolve_targets(interventions, network: SocialNetwork) -> dict[tuple[str, float], np.ndarray]:
    keys = {
        (iv.target, iv.fraction)
        for iv in interventions
        if isinstance(iv, (PopulationChange, SeedAdopters))
    }
    return {k: select_targets(*k, network) for k in keys}


def effective_market(base: Market, interventions, week: int) -> Market:
    changes = [iv for iv in interventions if isinstance(iv, ProductChange)]
    if not changes:
        return base
    values = {a: getattr(base, a).copy() for a in PRODUCT_ATTRIBUTES}
    for iv in changes:
        k = iv.schedule.intensity(week)
        if k == 0.0:
            continue
        j = base.index(iv.product)
        values[iv.attribute][j] = _apply(values[iv.attribute][j], iv.op, iv.value, k)
    values["price"] = np.maximum(values["price"], 0.0)
    values["availability"] = np.clip(values["availability"], 0.0, 1.0)
    return replace(base, **values)


def effective_population(
    population: Population, interventions, week: int, targets: dict
) -> dict[str, np.ndarray]:
    """Return attribute arrays for ``week``; unmodified arrays are shared, not copied."""
    values = {a: getattr(population, a) for a in POPULATION_ATTRIBUTES}
    for iv in interventions:
        if not isinstance(iv, PopulationChange):
            continue
        k = iv.schedule.intensity(week)
        if k == 0.0:
            continue
        idx = targets[(iv.target, iv.fraction)]
        arr = values[iv.attribute].copy()
        arr[idx] = np.maximum(_apply(arr[idx], iv.op, iv.value, k), 0.0)
        values[iv.attribute] = arr
    return values


def seeding_events(interventions, week: int) -> list[SeedAdopters]:
    return [iv for iv in interventions if isinstance(iv, SeedAdopters) and iv.week == week]


def first_active_week(interventions) -> int | None:
    weeks = [
        iv.week if isinstance(iv, SeedAdopters) else iv.schedule.start_week for iv in interventions
    ]
    return min(weeks) if weeks else None

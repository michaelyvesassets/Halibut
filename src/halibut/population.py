"""Synthetic consumer population.

Consumers are stored column-wise (one numpy array per attribute) so that every
weekly decision for the whole population is a handful of vectorised operations.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

CONSUMER_ATTRIBUTES = (
    "budget",  # weekly category budget (currency units)
    "consumption",  # servings bought per purchase week
    "price_sensitivity",  # lambda: disutility per unit of budget share spent
    "brand_loyalty",  # utility bonus for the consumer's current product
    "social_susceptibility",  # scales peer influence and preference drift
    "switching_resistance",  # utility cost of buying a product other than the current one
)

NO_PURCHASE = 0  # option index of the outside option; products are 1..J


@dataclass(frozen=True)
class Population:
    segment: np.ndarray  # int, (N,)
    segment_names: tuple[str, ...]
    budget: np.ndarray
    consumption: np.ndarray
    price_sensitivity: np.ndarray
    brand_loyalty: np.ndarray
    social_susceptibility: np.ndarray
    switching_resistance: np.ndarray
    preference: np.ndarray  # (N, J), each in [0, 1]

    @property
    def size(self) -> int:
        return len(self.segment)


def apportion(n: int, weights) -> np.ndarray:
    """Split ``n`` into integer counts proportional to ``weights`` (largest remainder)."""
    w = np.asarray(weights, dtype=float)
    raw = w / w.sum() * n
    counts = np.floor(raw).astype(np.int64)
    remainder = n - counts.sum()
    order = np.argsort(-(raw - counts), kind="stable")
    counts[order[:remainder]] += 1
    return counts


def generate_population(cfg, product_ids, rng: np.random.Generator) -> Population:
    n = cfg.size
    segments = cfg.segments
    labels = np.repeat(np.arange(len(segments)), apportion(n, [s.weight for s in segments]))
    labels = rng.permutation(labels)

    attrs = {a: np.empty(n) for a in CONSUMER_ATTRIBUTES}
    preference = np.empty((n, len(product_ids)))
    for s, seg in enumerate(segments):
        idx = np.flatnonzero(labels == s)
        for a in CONSUMER_ATTRIBUTES:
            attrs[a][idx] = seg.attributes[a].sample(rng, len(idx))
        for j, p in enumerate(product_ids):
            preference[idx, j] = seg.preferences[p].sample(rng, len(idx))

    for a in CONSUMER_ATTRIBUTES:
        np.maximum(attrs[a], 0.0, out=attrs[a])
    np.clip(preference, 0.0, 1.0, out=preference)
    return Population(
        segment=labels,
        segment_names=tuple(s.name for s in segments),
        preference=preference,
        **attrs,
    )


def initial_products(population: Population, cfg, product_ids, rng: np.random.Generator) -> np.ndarray:
    """Initial current product per consumer (option index; 0 = none).

    Counts within each segment match the configured shares exactly.
    """
    options = ("none",) + tuple(product_ids)
    current = np.zeros(population.size, dtype=np.int64)
    for s, seg in enumerate(cfg.segments):
        idx = np.flatnonzero(population.segment == s)
        shares = [seg.initial_product_shares.get(o, 0.0) for o in options]
        values = np.repeat(np.arange(len(options)), apportion(len(idx), shares))
        current[idx] = rng.permutation(values)
    return current

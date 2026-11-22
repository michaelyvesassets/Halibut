"""Marketplace: the set of competing products and their observable attributes."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Market:
    """Product attributes as aligned arrays of length J (one entry per product).

    ``price`` is per unit (serving); ``quality`` is perceived quality on an
    arbitrary utility scale; ``availability`` is the probability the product is
    in stock for a given consumer in a given week.
    """

    product_ids: tuple[str, ...]
    categories: tuple[str, ...]
    price: np.ndarray
    quality: np.ndarray
    availability: np.ndarray

    @property
    def size(self) -> int:
        return len(self.product_ids)

    def index(self, product_id: str) -> int:
        return self.product_ids.index(product_id)

    @classmethod
    def from_config(cls, products) -> "Market":
        return cls(
            product_ids=tuple(p.id for p in products),
            categories=tuple(p.category for p in products),
            price=np.array([p.price for p in products], dtype=float),
            quality=np.array([p.quality for p in products], dtype=float),
            availability=np.array([p.availability for p in products], dtype=float),
        )

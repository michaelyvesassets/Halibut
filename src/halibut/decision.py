"""Consumer choice: multinomial logit over products plus a no-purchase option.

Random-utility model. For consumer i and option o the utility is
``U_io = V_io + e_io`` with ``e_io`` i.i.d. Gumbel of scale ``temperature``.
Integrating out the Gumbel term gives the closed-form choice probabilities

    P_io = exp(V_io / T) / sum_{o' feasible} exp(V_io' / T)

and the engine samples one option from ``P`` with a single uniform draw per
consumer (inverse CDF). The Gumbel noise is therefore represented *only* by the
softmax; it is never added to ``V`` as well. ``temperature = 0`` is the
deterministic limit (argmax of V, ties to the lowest option index).

Option 0 is the outside (no-purchase) option with ``V_i0 = outside_utility``;
it is always feasible. A product is feasible for a consumer in a given week
only if it is in stock for them and ``price * consumption <= budget``;
infeasible options have probability exactly zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np


@dataclass
class DecisionContext:
    """Everything a choice model may use for one week. Arrays are (N,) or (N, J)."""

    price: np.ndarray  # (J,)
    quality: np.ndarray  # (J,)
    available: np.ndarray  # (N, J) bool, in stock for this consumer this week
    budget: np.ndarray
    consumption: np.ndarray
    price_sensitivity: np.ndarray
    brand_loyalty: np.ndarray
    switching_resistance: np.ndarray
    social_susceptibility: np.ndarray
    preference: np.ndarray  # (N, J)
    current: np.ndarray  # (N,) option index of current product, 0 = none yet
    neighbour_share: np.ndarray | None = None  # (N, J), None disables the social term

    def cost(self) -> np.ndarray:
        return self.consumption[:, None] * self.price[None, :]

    def feasible(self) -> np.ndarray:
        """(N, J+1) feasibility including the always-feasible outside option."""
        products = self.available & (self.cost() <= self.budget[:, None])
        return np.hstack([np.ones((len(self.budget), 1), dtype=bool), products])


class ChoiceModel(Protocol):
    def probabilities(self, ctx: DecisionContext) -> np.ndarray: ...

    def choose(self, ctx: DecisionContext, uniforms: np.ndarray) -> np.ndarray: ...


class LogitChoiceModel:
    def __init__(
        self,
        temperature: float = 1.0,
        outside_utility: float = 0.0,
        preference_weight: float = 1.0,
        quality_weight: float = 1.0,
        social_weight: float = 0.0,
    ):
        self.temperature = temperature
        self.outside_utility = outside_utility
        self.preference_weight = preference_weight
        self.quality_weight = quality_weight
        self.social_weight = social_weight

    @classmethod
    def from_config(cls, decision, social_influence) -> "LogitChoiceModel":
        return cls(
            temperature=decision.temperature,
            outside_utility=decision.outside_utility,
            preference_weight=decision.preference_weight,
            quality_weight=decision.quality_weight,
            social_weight=social_influence.weight if social_influence.enabled else 0.0,
        )

    def utilities(self, ctx: DecisionContext) -> np.ndarray:
        """Systematic utility V, shape (N, J+1), column 0 = no purchase."""
        n, j = ctx.preference.shape
        budget = np.where(ctx.budget > 0, ctx.budget, 1.0)  # zero budget => nothing affordable anyway
        budget_share = ctx.cost() / budget[:, None]
        is_current = ctx.current[:, None] == np.arange(1, j + 1)[None, :]
        has_current = (ctx.current > 0)[:, None]

        v = (
            self.preference_weight * ctx.preference
            + self.quality_weight * ctx.quality[None, :]
            - ctx.price_sensitivity[:, None] * budget_share
            + ctx.brand_loyalty[:, None] * is_current
            - ctx.switching_resistance[:, None] * (has_current & ~is_current)
        )
        if self.social_weight and ctx.neighbour_share is not None:
            v = v + self.social_weight * ctx.social_susceptibility[:, None] * ctx.neighbour_share
        return np.hstack([np.full((n, 1), self.outside_utility), v])

    def probabilities(self, ctx: DecisionContext) -> np.ndarray:
        v = self.utilities(ctx)
        feasible = ctx.feasible()
        if self.temperature == 0:
            masked = np.where(feasible, v, -np.inf)
            p = np.zeros_like(v)
            p[np.arange(len(v)), np.argmax(masked, axis=1)] = 1.0
            return p
        z = np.where(feasible, v / self.temperature, -np.inf)
        z -= z.max(axis=1, keepdims=True)  # outside option is always finite
        e = np.exp(z)
        return e / e.sum(axis=1, keepdims=True)

    def choose(self, ctx: DecisionContext, uniforms: np.ndarray) -> np.ndarray:
        return sample_options(self.probabilities(ctx), uniforms)


def sample_options(p: np.ndarray, uniforms: np.ndarray) -> np.ndarray:
    """Inverse-CDF sample one option per row from probabilities ``p`` using
    ``uniforms`` in [0, 1). Zero-probability options are never selected.

    Feeding the same uniforms to two scenarios couples their choices: a
    consumer's choice differs only where the two CDFs straddle their uniform,
    so the fraction of changed choices is at most sum_k |F_a(k) - F_b(k)|.
    This sharply reduces the variance of paired scenario comparisons.
    """
    cum = np.cumsum(p, axis=1)
    target = uniforms * cum[:, -1]
    choice = (cum <= target[:, None]).sum(axis=1)
    # Guard against floating-point edge cases at the top of the CDF.
    bad = (choice >= p.shape[1]) | (p[np.arange(len(p)), np.minimum(choice, p.shape[1] - 1)] == 0)
    if bad.any():
        choice[bad] = np.argmax(p[bad], axis=1)
    return choice.astype(np.int64)

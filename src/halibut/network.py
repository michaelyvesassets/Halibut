"""Social network over consumers.

Stored as a symmetric directed edge list (each undirected edge appears in both
directions), which supports vectorised neighbour aggregation with
``np.bincount`` and scales to millions of edges without a graph library.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class SocialNetwork:
    n: int
    src: np.ndarray  # int64, length 2E
    dst: np.ndarray  # int64, length 2E
    degree: np.ndarray  # int64, length n

    @property
    def num_edges(self) -> int:
        return len(self.src) // 2

    def neighbours(self, i: int) -> np.ndarray:
        return np.sort(self.dst[self.src == i])

    def neighbour_mean(self, values: np.ndarray) -> np.ndarray:
        """Mean of ``values`` over each node's neighbours (0 for isolated nodes).

        ``values`` has shape (n,) or (n, K).
        """
        values = np.asarray(values, dtype=float)
        flat = values.ndim == 1
        v = values[:, None] if flat else values
        out = np.empty((self.n, v.shape[1]))
        for c in range(v.shape[1]):
            out[:, c] = np.bincount(self.src, weights=v[self.dst, c], minlength=self.n)
        deg = self.degree[:, None].astype(float)
        np.divide(out, deg, out=out, where=deg > 0)
        out[self.degree == 0] = 0.0
        return out[:, 0] if flat else out

    def neighbour_product_share(self, choices: np.ndarray, num_products: int) -> np.ndarray:
        """(n, J) fraction of each node's neighbours whose option index was j+1.

        Option 0 (no purchase) counts in the denominator, so rows sum to <= 1.
        """
        onehot = choices[:, None] == np.arange(1, num_products + 1)[None, :]
        return self.neighbour_mean(onehot)


def _from_undirected(n: int, u: np.ndarray, v: np.ndarray) -> SocialNetwork:
    src = np.concatenate([u, v]).astype(np.int64)
    dst = np.concatenate([v, u]).astype(np.int64)
    order = np.lexsort((dst, src))
    src, dst = src[order], dst[order]
    return SocialNetwork(n=n, src=src, dst=dst, degree=np.bincount(src, minlength=n))


def watts_strogatz(n: int, k: int, rewire_prob: float, rng: np.random.Generator) -> SocialNetwork:
    """Watts-Strogatz small-world graph.

    Start from a ring lattice where each node links to its ``k/2`` nearest
    neighbours on each side, then rewire each edge's far endpoint with
    probability ``rewire_prob`` to a uniformly random node, avoiding self-loops
    and duplicate edges. The number of edges (n*k/2) is preserved exactly.
    """
    if k % 2 or k < 2 or k >= n:
        raise ValueError("watts_strogatz requires an even k with 2 <= k < n")
    half = k // 2
    u = np.repeat(np.arange(n, dtype=np.int64), half)
    v = (u + np.tile(np.arange(1, half + 1, dtype=np.int64), n)) % n

    rewire = np.flatnonzero(rng.random(len(u)) < rewire_prob)
    if len(rewire):
        key = lambda a, b: (a * n + b) if a < b else (b * n + a)  # noqa: E731
        edges = {key(int(a), int(b)) for a, b in zip(u, v)}
        degree = np.bincount(np.concatenate([u, v]), minlength=n)
        for idx in rewire:
            a, b = int(u[idx]), int(v[idx])
            if degree[a] >= n - 1:
                continue
            while True:
                w = int(rng.integers(n))
                if w != a and key(a, w) not in edges:
                    break
            edges.remove(key(a, b))
            edges.add(key(a, w))
            degree[b] -= 1
            degree[w] += 1
            v[idx] = w
    return _from_undirected(n, u, v)


def build_network(cfg, n: int, rng: np.random.Generator) -> SocialNetwork:
    if cfg.type == "watts_strogatz":
        return watts_strogatz(n, cfg.k, cfg.rewire_prob, rng)
    raise ValueError(f"unsupported network type {cfg.type!r}")

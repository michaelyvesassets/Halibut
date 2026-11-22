"""Named, independent random streams derived from a single experiment seed.

Each subsystem draws from its own stream, so changing how much randomness one
subsystem consumes never shifts the numbers another subsystem sees. Every
scenario run for a given seed re-creates the ``choice`` and ``availability``
streams from scratch: scenarios share common random numbers.
"""

from __future__ import annotations

import numpy as np

STREAMS = {
    "population": 0,
    "network": 1,
    "initial_state": 2,
    "choice": 3,
    "availability": 4,
}


def stream(seed: int, name: str) -> np.random.Generator:
    ss = np.random.SeedSequence(entropy=seed, spawn_key=(STREAMS[name],))
    return np.random.Generator(np.random.PCG64(ss))

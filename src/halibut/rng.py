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
    # engine 0.2: warm-up draws come from their own streams, so enabling
    # warm-up never shifts the numbers seen from week 0 onward
    "warmup_choice": 5,
    "warmup_availability": 6,
}


def stream(seed: int, name: str) -> np.random.Generator:
    ss = np.random.SeedSequence(entropy=seed, spawn_key=(STREAMS[name],))
    return np.random.Generator(np.random.PCG64(ss))

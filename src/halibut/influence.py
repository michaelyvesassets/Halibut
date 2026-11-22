"""Social influence mechanisms. Each can be enabled or disabled on its own.

1. Peer influence (short-term, in the utility function; see ``decision.py``):
   ``+ social_weight * susceptibility_i * share_ij``, where ``share_ij`` is the
   fraction of i's neighbours who bought product j last week.

2. Preference drift (long-term, changes state): after each week,
   ``pref_ij <- pref_ij + rate * susceptibility_i * (share_ij - pref_ij)``,
   i.e. tastes move toward what one's neighbours consume (conformity). The
   effective step ``rate * susceptibility`` is clipped to [0, 1] so preferences
   stay in [0, 1].
"""

from __future__ import annotations

import numpy as np


def drift_preferences(
    preference: np.ndarray,
    susceptibility: np.ndarray,
    neighbour_share: np.ndarray,
    rate: float,
) -> np.ndarray:
    step = np.clip(rate * susceptibility, 0.0, 1.0)[:, None]
    return preference + step * (neighbour_share - preference)

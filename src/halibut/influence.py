"""Social influence mechanisms. Each can be enabled or disabled on its own.

1. Peer influence (short-term, in the utility function; see ``decision.py``):
   ``+ social_weight * susceptibility_i * S_ij``, where ``S_ij`` is the
   fraction of i's neighbours who bought product j last week.

2. Preference drift (long-term, changes state), applied after each week.
   With ``a_i = rate * susceptibility_i`` (clipped so every update is a
   convex combination and preferences stay in [0, 1]) and ``pi0`` the
   consumer's original (generated) preference:

   - ``none``:                   pi unchanged
   - ``conformity`` (v1):        pi += a (S - pi)                  fixed point pi* = S
   - ``baseline_reversion``:     pi += a (S - pi) + k (pi0 - pi)   pi* = (a S + k pi0)/(a + k)
   - ``exposure_reinforcement``: pi += a S (1 - pi) - d (pi - pi0) pi* = (a S + d pi0)/(a S + d)

   Conformity pulls preference for any minority product toward its (low)
   neighbour share, which structurally favours incumbents. Baseline reversion
   anchors tastes to their origin. Exposure reinforcement only ever raises
   preference above baseline in proportion to exposure; absent exposure,
   preference decays back to baseline rather than below it.
"""

from __future__ import annotations

import numpy as np


def drift_preferences(
    preference: np.ndarray,
    susceptibility: np.ndarray,
    neighbour_share: np.ndarray,
    rate: float,
) -> np.ndarray:
    """Conformity drift (engine 0.1.0 behaviour, kept bit-identical)."""
    step = np.clip(rate * susceptibility, 0.0, 1.0)[:, None]
    return preference + step * (neighbour_share - preference)


def baseline_reversion(preference, base, susceptibility, share, rate, reversion):
    a = np.clip(rate * susceptibility, 0.0, 1.0 - reversion)[:, None]
    return preference + a * (share - preference) + reversion * (base - preference)


def exposure_reinforcement(preference, base, susceptibility, share, rate, decay):
    a = np.clip(rate * susceptibility, 0.0, 1.0 - decay)[:, None]
    return preference + a * share * (1.0 - preference) - decay * (preference - base)


def apply_drift(cfg, preference, base_preference, susceptibility, share):
    """Apply the configured drift model; returns the (possibly same) array."""
    model = cfg.effective_model
    if model == "none":
        return preference
    if model == "conformity":
        return drift_preferences(preference, susceptibility, share, cfg.rate)
    if model == "baseline_reversion":
        return baseline_reversion(preference, base_preference, susceptibility, share,
                                  cfg.rate, cfg.reversion_rate)
    if model == "exposure_reinforcement":
        return exposure_reinforcement(preference, base_preference, susceptibility, share,
                                      cfg.rate, cfg.decay_rate)
    raise ValueError(f"unknown drift model {model!r}")

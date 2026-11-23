"""Learning / Evaluation Engine: explicit, conservative decision rules.

A candidate change is compared with its baseline on PAIRED units (forecast
origins, seeds, markets) split in advance into a DEVELOPMENT set and a
HELD-OUT set (never used to design the change). Lower metric = better.

Decision rule (documented, versioned as RULE_VERSION):
  1. Development: the candidate must win on >= min_win_fraction of paired
     units AND the paired mean improvement's 95% CI must exclude zero.
  2. Held-out: the candidate must improve the held-out mean as well, and must
     not lose to the simple baseline (if given) on held-out data.
  3. Fewer than ``min_units`` development units, or a single favourable
     result, can never yield promotion: the result is 'inconclusive'.
  -> 'improved' + decision 'eligible_for_promotion' only if 1 and 2 hold.
  -> 'degraded' + 'reject' if the development mean is significantly worse
     or the held-out mean is worse.
  -> otherwise 'inconclusive' + 'hold'.
'eligible_for_promotion' is NOT promotion: activation needs explicit approval.
"""

from __future__ import annotations

import math

import numpy as np

RULE_VERSION = "paired-dev-holdout-v1"
_T975 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306, 9: 2.262, 10: 2.228}


def _ci(diffs: np.ndarray) -> tuple[float, float]:
    n = len(diffs)
    if n < 2:
        return float(diffs.mean()) if n else math.nan, math.inf
    t = _T975.get(n - 1, 1.96 if n > 30 else 2.1)
    return float(diffs.mean()), float(t * diffs.std(ddof=1) / math.sqrt(n))


def decide(baseline_dev: dict, candidate_dev: dict, baseline_holdout: dict, candidate_holdout: dict,
           simple_holdout: dict | None = None, min_units: int = 3, min_win_fraction: float = 0.6) -> dict:
    """Inputs: unit -> metric (lower is better). Returns result, decision and full details."""
    units = sorted(set(baseline_dev) & set(candidate_dev))
    hold = sorted(set(baseline_holdout) & set(candidate_holdout))
    rule = {"version": RULE_VERSION, "min_units": min_units, "min_win_fraction": min_win_fraction,
            "direction": "lower_is_better"}
    d = np.array([baseline_dev[u] - candidate_dev[u] for u in units])  # > 0 means candidate better
    wins = int((d > 0).sum())
    mean, half = _ci(d) if len(d) else (math.nan, math.inf)
    h = np.array([baseline_holdout[u] - candidate_holdout[u] for u in hold])
    h_mean = float(h.mean()) if len(h) else math.nan
    beats_simple = None
    if simple_holdout:
        s_units = [u for u in hold if u in simple_holdout]
        if s_units:
            beats_simple = float(np.mean([candidate_holdout[u] for u in s_units])) <= float(np.mean([simple_holdout[u] for u in s_units]))
    details = {"dev_units": units, "dev_improvements": d.tolist(), "dev_wins": wins, "dev_mean_improvement": mean,
               "dev_ci95_half_width": half, "holdout_units": hold, "holdout_improvements": h.tolist(),
               "holdout_mean_improvement": h_mean, "beats_simple_baseline_on_holdout": beats_simple}
    reasons = []
    if len(units) < min_units:
        reasons.append(f"only {len(units)} development units (< {min_units}): a single or few favourable results cannot promote")
        return {"rule": rule, "result": "inconclusive", "decision": "hold", "reasons": reasons, "details": details}
    sig_better = mean - half > 0
    sig_worse = mean + half < 0
    win_ok = wins / len(units) >= min_win_fraction
    if sig_worse or (len(h) and h_mean < 0 and not sig_better):
        reasons.append("candidate significantly worse on development units" if sig_worse
                       else "candidate worse on held-out units")
        return {"rule": rule, "result": "degraded", "decision": "reject", "reasons": reasons, "details": details}
    if sig_better and win_ok and len(h) and h_mean > 0 and beats_simple is not False:
        reasons.append("significant paired development improvement, majority of units won, held-out improvement"
                       + (", not worse than simple baseline" if beats_simple else ""))
        return {"rule": rule, "result": "improved", "decision": "eligible_for_promotion", "reasons": reasons, "details": details}
    if not sig_better:
        reasons.append("development improvement not significant (95% CI includes 0)")
    if not win_ok:
        reasons.append(f"won only {wins}/{len(units)} development units")
    if len(h) and h_mean <= 0:
        reasons.append("no held-out improvement")
    if beats_simple is False:
        reasons.append("loses to the simple baseline on held-out units")
    if not len(h):
        reasons.append("no held-out units")
    return {"rule": rule, "result": "inconclusive", "decision": "hold", "reasons": reasons, "details": details}

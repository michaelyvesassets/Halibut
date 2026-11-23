"""Measurement model: observed data <-> simulated quantities.

Observed share of product p in month t:
    s_obs(p, t) = sum_{series of p} x(t) / sum_{series of all products} x(t)
All series must belong to ONE universe (``AsOfView.shares`` enforces this).

Simulated counterpart: ``expected_share_<p>`` at tick t (engine 0.3), the
population-average choice probability among in-market agents, i.e. the
expected purchase share. Ticks map to months: tick k = start_period + k.

Moments (targets for calibration): annual means of monthly shares for full
years, plus the most recent months individually, each with an ASSUMED
standard error (see the pilot spec). Months without data are never filled.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..data.store import AsOfView


def month_index(period: str, start: str) -> int:
    return (int(period[:4]) - int(start[:4])) * 12 + (int(period[5:7]) - int(start[5:7]))


def month_at(index: int, start: str) -> str:
    y, m = divmod(int(start[:4]) * 12 + int(start[5:7]) - 1 + index, 12)
    return f"{y:04d}-{m + 1:02d}"


def observed_shares(view: AsOfView, spec) -> dict[str, dict[str, float]]:
    """period -> {product: share} from data available in ``view``."""
    all_series = [s for p in spec.products for s in p.series]
    raw = view.shares(all_series, spec.geo)  # validates a single universe
    out = {}
    for period, by_series in raw.items():
        out[period] = {p.id: sum(by_series[s] for s in p.series) for p in spec.products}
    return out


@dataclass(frozen=True)
class Moment:
    name: str
    product: str
    months: tuple[str, ...]  # periods averaged
    value: float
    se: float


def build_moments(obs: dict[str, dict[str, float]], spec) -> list[Moment]:
    periods = sorted(obs)
    if not periods:
        return []
    last = periods[-1]
    moments = []
    for year in range(spec.annual_from, int(last[:4]) + 1):
        months = tuple(f"{year:04d}-{m:02d}" for m in range(1, 13))
        if all(m in obs for m in months):
            for p in spec.moment_products:
                moments.append(Moment(f"annual_{year}_{p}", p, months,
                                      sum(obs[m][p] for m in months) / 12, spec.se_annual))
    for m in periods[-spec.recent_months:] if spec.recent_months else []:
        for p in spec.moment_products:
            moments.append(Moment(f"month_{m}_{p}", p, (m,), obs[m][p], spec.se_month))
    return moments


def simulated_moment(series: dict[str, list[float]], moment: Moment, start: str) -> float:
    vals = series[f"expected_share_{moment.product}"]
    return sum(vals[month_index(m, start)] for m in moment.months) / len(moment.months)

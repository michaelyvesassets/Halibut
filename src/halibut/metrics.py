"""Per-week market metrics.

Definitions (N consumers, option 0 = no purchase):

- ``purchase_share_<p>``: p's share of category *units* bought this week
  (sums to 1 over products whenever any units were bought).
- ``buyer_share_<p>``: p's share of this week's *purchasers*.
- ``penetration_<p>``: fraction of the whole population that bought p this week.
- ``adoption_share_<p>``: fraction of the population whose current product
  (most recent purchase, which persists through no-purchase weeks) is p.
  ``non_adopter_share`` is the fraction that has never bought; adoption shares
  plus it sum to 1.
- ``category_units`` / ``category_revenue``: total category demand. Falling
  category units with stable purchase shares is *market contraction*; stable
  category units with shifting shares is *competitive displacement*.
- ``no_purchase_rate``: fraction of the population choosing the outside option.
- ``switching_rate``: fraction of purchasers who bought a product different
  from their current product.
- ``flow_<a>_to_<b>``: number of consumers whose last-week choice was a and
  this-week choice is b (a, b include ``none``).

No-purchase causes (engine >= 0.2). Every consumer who buys nothing this week
is assigned exactly one cause, tested in this order (so the causes are
mutually exclusive and sum to ``consumers - purchasers``). "Current product"
means the consumer's current product at the start of the week.

1. ``np_nothing_affordable``: no product is affordable (price * consumption
   > budget for every product), regardless of stock.
2. ``np_nothing_in_stock``: at least one product is affordable, but every
   affordable product is out of stock for this consumer this week.
3. ``np_current_infeasible``: some product is feasible, but the consumer has
   a current product and it is infeasible (out of stock or unaffordable).
4. ``np_voluntary``: some product is feasible and the current product (if
   any) is feasible too; the consumer chose not to buy.

Stock-out metrics: ``current_out_of_stock`` (consumers whose current product
is out of stock for them), ``current_unaffordable``, and
``forced_substitution`` (purchasers whose current product was infeasible, so
they necessarily bought a different product).
"""

from __future__ import annotations

import numpy as np


NO_PURCHASE_CAUSES = (
    "np_nothing_affordable",
    "np_nothing_in_stock",
    "np_current_infeasible",
    "np_voluntary",
)


def _f(x) -> float:
    return float(x)


def tick_metrics(
    week: int,
    product_ids: tuple[str, ...],
    price: np.ndarray,
    quality: np.ndarray,
    availability: np.ndarray,
    choices: np.ndarray,
    prev_last_choice: np.ndarray,
    prev_current: np.ndarray,
    new_current: np.ndarray,
    consumption: np.ndarray,
    budget: np.ndarray,
    preference: np.ndarray,
    available: np.ndarray | None = None,
    affordable: np.ndarray | None = None,
) -> dict:
    n = len(choices)
    j_count = len(product_ids)
    options = ("none",) + tuple(product_ids)
    bought = choices > 0
    purchasers = int(bought.sum())
    price_by_option = np.concatenate([[0.0], price])
    spend = np.where(bought, consumption * price_by_option[choices], 0.0)

    units = np.bincount(choices, weights=consumption, minlength=j_count + 1)[1:]
    buyers = np.bincount(choices, minlength=j_count + 1)[1:]
    adopters = np.bincount(new_current, minlength=j_count + 1)
    revenue = units * price
    category_units = units.sum()
    category_revenue = revenue.sum()

    m: dict = {
        "week": week,
        "consumers": n,
        "purchasers": purchasers,
        "no_purchase_rate": _f(1 - purchasers / n),
        "category_units": _f(category_units),
        "category_revenue": _f(category_revenue),
        "non_adopter_share": _f(adopters[0] / n),
    }
    for j, p in enumerate(product_ids):
        m[f"price_{p}"] = _f(price[j])
        m[f"quality_{p}"] = _f(quality[j])
        m[f"availability_{p}"] = _f(availability[j])
        m[f"buyers_{p}"] = int(buyers[j])
        m[f"units_{p}"] = _f(units[j])
        m[f"revenue_{p}"] = _f(revenue[j])
        m[f"purchase_share_{p}"] = _f(units[j] / category_units) if category_units > 0 else 0.0
        m[f"buyer_share_{p}"] = _f(buyers[j] / purchasers) if purchasers else 0.0
        m[f"revenue_share_{p}"] = _f(revenue[j] / category_revenue) if category_revenue > 0 else 0.0
        m[f"penetration_{p}"] = _f(buyers[j] / n)
        m[f"adoption_share_{p}"] = _f(adopters[j + 1] / n)
        m[f"mean_preference_{p}"] = _f(preference[:, j].mean())

    switchers = bought & (prev_current > 0) & (choices != prev_current)
    m["switching_rate"] = _f(switchers.sum() / purchasers) if purchasers else 0.0
    m["first_purchases"] = int((bought & (prev_current == 0)).sum())

    with_budget = bought & (budget > 0)
    utilisation = spend[with_budget] / budget[with_budget]
    m["mean_budget_utilisation"] = _f(utilisation.mean()) if len(utilisation) else 0.0
    m["max_budget_utilisation"] = _f(utilisation.max()) if len(utilisation) else 0.0

    k = j_count + 1
    flows = np.bincount(prev_last_choice * k + choices, minlength=k * k).reshape(k, k)
    for a, name_a in enumerate(options):
        for b, name_b in enumerate(options):
            m[f"flow_{name_a}_to_{name_b}"] = int(flows[a, b])

    if available is not None and affordable is not None:
        m.update(_feasibility_metrics(choices, prev_current, available, affordable))
    return m


def _feasibility_metrics(choices, prev_current, available, affordable) -> dict:
    rows = np.arange(len(choices))
    feasible = available & affordable
    any_affordable = affordable.any(axis=1)
    any_feasible = feasible.any(axis=1)
    has_current = prev_current > 0
    cur = np.maximum(prev_current - 1, 0)
    current_available = has_current & available[rows, cur]
    current_affordable = has_current & affordable[rows, cur]
    current_feasible = current_available & current_affordable
    none = choices == 0
    causes = {
        "np_nothing_affordable": none & ~any_affordable,
        "np_nothing_in_stock": none & any_affordable & ~any_feasible,
        "np_current_infeasible": none & any_feasible & has_current & ~current_feasible,
        "np_voluntary": none & any_feasible & (~has_current | current_feasible),
    }
    out = {k: int(v.sum()) for k, v in causes.items()}
    out["current_out_of_stock"] = int((has_current & ~current_available).sum())
    out["current_unaffordable"] = int((has_current & ~current_affordable).sum())
    out["forced_substitution"] = int((~none & has_current & ~current_feasible).sum())
    return out


def segment_metrics(
    week: int,
    product_ids: tuple[str, ...],
    segment: np.ndarray,
    segment_names: tuple[str, ...],
    choices: np.ndarray,
    new_current: np.ndarray,
    consumption: np.ndarray,
) -> list[dict]:
    rows = []
    j_count = len(product_ids)
    for s, name in enumerate(segment_names):
        mask = segment == s
        n = int(mask.sum())
        c, cur = choices[mask], new_current[mask]
        units = np.bincount(c, weights=consumption[mask], minlength=j_count + 1)[1:]
        adopters = np.bincount(cur, minlength=j_count + 1)
        row = {
            "week": week,
            "segment": name,
            "consumers": n,
            "no_purchase_rate": _f((c == 0).mean()) if n else 0.0,
            "category_units": _f(units.sum()),
        }
        for j, p in enumerate(product_ids):
            row[f"units_{p}"] = _f(units[j])
            row[f"purchase_share_{p}"] = _f(units[j] / units.sum()) if units.sum() > 0 else 0.0
            row[f"adoption_share_{p}"] = _f(adopters[j + 1] / n) if n else 0.0
        rows.append(row)
    return rows

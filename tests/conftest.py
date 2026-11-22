import copy
import tomllib
from pathlib import Path

import numpy as np
import pytest

from halibut.config import parse_config
from halibut.decision import DecisionContext

EXAMPLE = Path(__file__).resolve().parents[1] / "configs" / "experiments" / "coffee_vs_matcha_v1.toml"


@pytest.fixture(scope="session")
def example_raw() -> dict:
    with open(EXAMPLE, "rb") as fh:
        return tomllib.load(fh)


@pytest.fixture
def raw(example_raw) -> dict:
    """A mutable small copy of the example config (fast to simulate)."""
    d = copy.deepcopy(example_raw)
    d["population"]["size"] = 400
    d["experiment"]["weeks"] = 20
    d["experiment"]["replicates"] = 3
    d["experiment"]["summary_window_weeks"] = 4
    d["network"]["k"] = 6
    # keep intervention weeks inside the shorter horizon
    for s in d["scenarios"]:
        for iv in s.get("interventions", []):
            if iv.get("end_week") is not None:
                iv["end_week"] = min(iv["end_week"], 19)
    return d


@pytest.fixture
def cfg(raw):
    return parse_config(raw)


def make_context(n=1, j=2, **kw) -> DecisionContext:
    """Single-segment decision context with neutral defaults."""
    defaults = dict(
        price=np.full(j, 1.0),
        quality=np.zeros(j),
        available=np.ones((n, j), dtype=bool),
        budget=np.full(n, 100.0),
        consumption=np.ones(n),
        price_sensitivity=np.zeros(n),
        brand_loyalty=np.zeros(n),
        switching_resistance=np.zeros(n),
        social_susceptibility=np.zeros(n),
        preference=np.zeros((n, j)),
        current=np.zeros(n, dtype=np.int64),
        neighbour_share=None,
    )
    defaults.update(kw)
    for k in ("price", "quality"):
        defaults[k] = np.asarray(defaults[k], dtype=float)
    return DecisionContext(**defaults)

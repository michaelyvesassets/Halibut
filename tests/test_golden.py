"""Backward compatibility: Phase 1 (engine 0.1.0) results must reproduce exactly.

Fingerprints are exact float hashes; they hold on the same platform/numpy
build. A legitimate behaviour change requires a new engine version and new
golden files, never silently editing these.
"""

import json
import tomllib
from pathlib import Path

import pytest

from halibut.config import config_hash, parse_config
from halibut.engine import build_world, run_scenario

from .conftest import EXAMPLE, small_raw
from .golden.fingerprint import fingerprint

GOLDEN = json.loads((Path(__file__).parent / "golden" / "phase1_v0.1.0.json").read_text())


@pytest.fixture(scope="module")
def configs():
    example_raw = tomllib.load(open(EXAMPLE, "rb"))
    return parse_config(small_raw(example_raw)), parse_config(example_raw)


def test_phase1_config_hashes_unchanged(configs):
    small, full = configs
    assert config_hash(full) == GOLDEN["example_config_hash"]
    assert config_hash(small) == GOLDEN["small_config_hash"]


def test_phase1_small_results_reproduce(configs):
    small, _ = configs
    keys = GOLDEN["v1_metric_keys"]
    for seed in (0, 1):
        world = build_world(small, seed)
        for s in small.scenario_names:
            rows = run_scenario(small, world, small.scenario(s)).metrics
            assert fingerprint(rows, keys) == GOLDEN["small"][f"{s}/{seed}"], f"{s}/{seed}"


def test_phase1_full_results_reproduce(configs):
    _, full = configs
    keys = GOLDEN["v1_metric_keys"]
    seed = full.seeds[0]
    world = build_world(full, seed)
    for s in ("control", "combined"):
        rows = run_scenario(full, world, full.scenario(s)).metrics
        assert fingerprint(rows, keys) == GOLDEN["full"][f"{s}/{seed}"], s

"""Regenerate Phase 1 golden fingerprints. Run ONLY with engine v0.1.0 behaviour.

    python -m tests.golden.make_golden
"""

import json
import tomllib
from pathlib import Path

from halibut.config import config_hash, parse_config
from halibut.engine import build_world, run_scenario
from tests.conftest import EXAMPLE, small_raw
from tests.golden.fingerprint import fingerprint

OUT = Path(__file__).with_name("phase1_v0.1.0.json")


def main():
    example_raw = tomllib.load(open(EXAMPLE, "rb"))
    small = parse_config(small_raw(example_raw))
    full = parse_config(example_raw)
    data = {"engine_version": "0.1.0", "example_config_hash": config_hash(full),
            "small_config_hash": config_hash(small), "small": {}, "full": {}}
    keys = None
    for seed in (0, 1):
        world = build_world(small, seed)
        for s in small.scenario_names:
            rows = run_scenario(small, world, small.scenario(s)).metrics
            keys = keys or list(rows[0])
            data["small"][f"{s}/{seed}"] = fingerprint(rows, keys)
    world = build_world(full, full.seeds[0])
    for s in ("control", "combined"):
        rows = run_scenario(full, world, full.scenario(s)).metrics
        data["full"][f"{s}/{full.seeds[0]}"] = fingerprint(rows, keys)
        data["full"][f"{s}/{full.seeds[0]}/final_purchase_share_matcha"] = rows[-1]["purchase_share_matcha"]
    data["v1_metric_keys"] = keys
    OUT.write_text(json.dumps(data, indent=1) + "\n")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()

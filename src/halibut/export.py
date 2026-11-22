"""Write experiment results to JSON and CSV.

Output directory layout (``<out>/<experiment>_<config-hash[:12]>/``):

- ``manifest.json``     provenance: versions, config hash, resolved config, seeds, runtime
- ``summary.json``      per-scenario summary, paired comparisons vs control
- ``timeseries.csv``    one row per (scenario, seed, week)
- ``aggregate.csv``     mean and 95% CI across seeds per (scenario, week)
- ``segments.csv``      per-segment metrics per (scenario, seed, week)

Every JSON file carries ``result_type = "synthetic_simulation"`` and
``empirically_validated = false``; the manifest documents the CSV files the
same way.
"""

from __future__ import annotations

import csv
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .diagnostics import diagnostics_report, run_diagnostics
from .experiment import ExperimentResult, aggregate_timeseries, summarize
from .version import CONFIG_SCHEMA_VERSION, ENGINE_VERSION, RESULT_TYPE, SYNTHETIC_DISCLAIMER

FILES = {
    "manifest.json": "Run provenance and resolved configuration.",
    "summary.json": "Per-scenario summary statistics and paired comparisons against control.",
    "timeseries.csv": "Weekly metrics for every scenario and seed.",
    "aggregate.csv": "Weekly metrics averaged across seeds with 95% CI half-widths.",
    "segments.csv": "Weekly metrics per consumer segment for every scenario and seed.",
    "diagnostics.json": "Accounting invariants and behavioural diagnostics.",
}


def synthetic_label() -> dict:
    return {
        "result_type": RESULT_TYPE,
        "empirically_validated": False,
        "disclaimer": SYNTHETIC_DISCLAIMER,
    }


def run_directory(out_dir: str | Path, result: ExperimentResult) -> Path:
    return Path(out_dir) / f"{result.config.name}_{result.config_hash[:12]}"


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("")
        return
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        for r in rows:
            writer.writerow({k: "" if v is None else v for k, v in r.items()})


def write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")


def manifest(result: ExperimentResult) -> dict:
    return {
        **synthetic_label(),
        "experiment": result.config.name,
        "engine_version": ENGINE_VERSION,
        "config_schema_version": CONFIG_SCHEMA_VERSION,
        "config_hash": result.config_hash,
        "seeds": result.seeds,
        "scenarios": result.scenarios,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "environment": {
            "python": sys.version.split()[0],
            "numpy": np.__version__,
            "platform": platform.platform(),
        },
        "runtime": result.runtime,
        "files": FILES,
        "config": result.config.to_dict(),
    }


def write_results(result: ExperimentResult, out_dir: str | Path) -> Path:
    path = run_directory(out_dir, result)
    path.mkdir(parents=True, exist_ok=True)
    summary = summarize(result)
    write_json(path / "manifest.json", manifest(result))
    write_json(path / "summary.json", {**synthetic_label(), **summary})
    write_csv(path / "timeseries.csv", result.timeseries)
    write_csv(path / "aggregate.csv", aggregate_timeseries(result.timeseries))
    write_csv(path / "segments.csv", result.segment_timeseries)
    write_json(path / "diagnostics.json", {**synthetic_label(), **diagnostics_report(run_diagnostics(result))})
    return path

"""Write backtest outputs (JSON, CSV) with explicit labelling."""

from __future__ import annotations

import json
from pathlib import Path

from ..export import write_csv, write_json
from ..version import ENGINE_VERSION

LABEL = {
    "result_type": "empirical_backtest",
    "calibration_status": "calibrated_empirical",
    "empirically_validated": False,
    "disclaimer": (
        "Rolling-origin backtest on observed Norwegian new-car registration shares (Statistics Norway, CC BY 4.0). "
        "Each forecast used only data available at its origin, but history reflects today's (possibly revised) "
        "values. Results describe forecast accuracy for this one industry-level case; they do not establish causal "
        "effects of policies, nor validate competitor-targeted market engineering."),
}


def write_backtest(spec, res) -> Path:
    path = Path(res["path"])
    ev = res["evaluation"]
    write_csv(path / "forecast_errors.csv", ev["rows"])
    write_json(path / "summary.json", {
        **LABEL, "pilot": spec.name, "engine_version": ENGINE_VERSION, "spec_hash": res["spec_hash"],
        "target": f"{spec.target_product} share of new private cars ({spec.geo})",
        "origins": [{k: o[k] for k in ("origin", "last_observed", "in_sample", "residual_sd", "seconds")}
                    | {"fit": {k: o["fit"][k] for k in ("values", "objective", "moments", "parameters_at_bounds", "evaluations")},
                       "audit": o["audit"]} for o in res["origins"]],
        "summary": ev["summary"], "ranking_by_mae": ev["ranking_by_mae"],
        "baselines_better_than_halibut": ev["baselines_better_than_halibut"],
        "baselines_better_by_origin": ev["baselines_better_by_origin"],
    })
    return path

import csv
import json

from halibut.cli import main
from halibut.experiment import run_experiment, summarize
from halibut.export import write_results

from .conftest import EXAMPLE


def test_export_round_trip(cfg, tmp_path):
    result = run_experiment(cfg)
    path = write_results(result, tmp_path)
    manifest = json.loads((path / "manifest.json").read_text())
    summary = json.loads((path / "summary.json").read_text())
    for doc in (manifest, summary):
        assert doc["result_type"] == "synthetic_simulation"
        assert doc["empirically_validated"] is False
        assert "not" in doc["disclaimer"]
    assert manifest["config_hash"] == result.config_hash
    assert manifest["config"] == cfg.to_dict()
    assert set(manifest["files"]) >= {"timeseries.csv", "aggregate.csv", "segments.csv"}

    rows = list(csv.DictReader(open(path / "timeseries.csv")))
    assert len(rows) == len(cfg.scenario_names) * len(cfg.seeds) * cfg.weeks
    first = result.timeseries[0]
    assert float(rows[0]["category_units"]) == first["category_units"]
    assert float(rows[0]["purchase_share_matcha"]) == first["purchase_share_matcha"]

    agg = list(csv.DictReader(open(path / "aggregate.csv")))
    assert len(agg) == len(cfg.scenario_names) * cfg.weeks
    assert summary["scenarios"].keys() == summarize(result)["scenarios"].keys()
    assert "vs_control" not in summary["scenarios"]["control"]
    assert "decomposition" in summary["scenarios"]["combined"]["vs_control"]


def test_cli_validate(capsys):
    assert main(["validate", str(EXAMPLE)]) == 0
    assert "coffee_vs_matcha_v1" in capsys.readouterr().out


def test_cli_run_subset(tmp_path):
    cfg_path = tmp_path / "small.toml"
    cfg_path.write_text(_to_toml_small())
    code = main(["run", str(cfg_path), "--scenario", "price_war", "--seed", "1", "--seed", "2",
                 "--out", str(tmp_path / "out"), "--quiet"])
    assert code == 0
    out_dirs = list((tmp_path / "out").iterdir())
    assert len(out_dirs) == 1
    manifest = json.loads((out_dirs[0] / "manifest.json").read_text())
    assert manifest["scenarios"] == ["control", "price_war"]  # control always included
    assert manifest["seeds"] == [1, 2]


def test_cli_errors(tmp_path, capsys):
    bad = tmp_path / "bad.toml"
    bad.write_text('schema_version = "1.0"\n')
    assert main(["validate", str(bad)]) == 2
    assert main(["run", str(EXAMPLE), "--scenario", "nope", "--replicates", "1",
                 "--out", str(tmp_path)]) == 2


def _to_toml_small() -> str:
    text = EXAMPLE.read_text()
    return text.replace("size = 5000", "size = 300").replace("weeks = 52", "weeks = 20")

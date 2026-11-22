import numpy as np
import pytest

from halibut.cli import main
from halibut.config import ConfigError
from halibut.suite import anova_decomposition, classify, load_suite, run_suite, write_suite

from .conftest import EXAMPLE

SPEC = """
schema_version = "1.0"
[suite]
name = "t"
base_config = "{base}"
replicates = 3
base_seed = 7
scenarios = ["price_war", "combined"]
metrics = ["purchase_share_matcha", "category_units"]
time_limit_minutes = 5
[overrides]
"schema_version" = "1.1"
"population.size" = 300
"experiment.weeks" = 16
"network.k" = 6
[[variants]]
name = "ref"
[[variants]]
name = "nodrift"
[variants.overrides]
"preference_drift.model" = "none"
[[variants]]
name = "warm"
[variants.overrides]
"experiment.warmup_weeks" = 8
"""


@pytest.fixture
def spec_path(tmp_path):
    p = tmp_path / "suite.toml"
    p.write_text(SPEC.format(base=EXAMPLE))
    return p


def test_classify():
    sig_pos = {"mean": 1.0, "ci95": 0.1}
    sig_neg = {"mean": -1.0, "ci95": 0.1}
    null = {"mean": 0.05, "ci95": 0.1}
    assert classify([sig_pos, sig_pos]) == "robust"
    assert classify([sig_neg, sig_neg]) == "robust"
    assert classify([sig_pos, sig_neg]) == "reversed"
    assert classify([sig_pos, null]) == "assumption_dependent"


def test_anova_recovers_planted_effects():
    rng = np.random.default_rng(0)
    A, S, R = 3, 4, 50
    a = np.array([0.0, 1.0, 2.0])[:, None, None]
    s = np.array([0.0, 0.0, 3.0, 3.0])[None, :, None]
    cube = np.broadcast_to(a + s, (A, S, R)) + rng.normal(0, 0.01, (A, S, R))
    d = anova_decomposition(cube)
    assert d["share"]["variant"] + d["share"]["scenario"] > 0.999
    assert d["share"]["variant_x_scenario"] < 1e-3
    inter = np.zeros((A, S, R))
    inter[2, 3] = 5.0
    d2 = anova_decomposition(inter)
    assert d2["share"]["variant_x_scenario"] > 0.3
    assert sum(d2["sum_of_squares"].values()) == pytest.approx(d2["total"])


def test_suite_end_to_end(spec_path, tmp_path):
    spec = load_suite(spec_path)
    assert [v.name for v in spec.variants] == ["ref", "nodrift", "warm"]
    assert spec.scenarios == ["control", "price_war", "combined"]
    assert spec.seeds == [7, 8, 9]
    res = run_suite(spec, workers=1, cache_dir=tmp_path / "cache")
    a = res["analysis"]
    # the reference has zero interaction with itself and zero assumption effect
    ref = a["variants"]["ref"]
    assert ref["assumption_effect_on_control"]["category_units"]["mean"] == 0
    assert ref["interaction_vs_reference"]["price_war"]["purchase_share_matcha"]["mean"] == 0
    for s in ("price_war", "combined"):
        for m in spec.metrics:
            assert a["conclusions"][s][m]["classification"] in ("robust", "reversed", "assumption_dependent")
    # effects computed per variant are paired differences of levels
    lv = a["variants"]["nodrift"]["levels"]
    eff = a["variants"]["nodrift"]["effects"]["combined"]["purchase_share_matcha"]["mean"]
    assert eff == pytest.approx(lv["combined"]["purchase_share_matcha"]["mean"] - lv["control"]["purchase_share_matcha"]["mean"])
    path = write_suite(spec, res, tmp_path / "out")
    assert (path / "comparison.json").exists() and (path / "end_window.csv").exists()
    # rerun is served from cache and gives identical analysis
    again = run_suite(spec, workers=2, cache_dir=tmp_path / "cache")
    assert again["analysis"]["runtime"]["cache_hits"] == 9
    assert again["analysis"]["conclusions"] == a["conclusions"]


def test_suite_rejects_bad_variant(spec_path):
    spec_path.write_text(spec_path.read_text() + '\n[[variants]]\nname = "bad"\n[variants.overrides]\n"preference_drift.model" = "magic"\n')
    with pytest.raises(ConfigError, match="bad"):
        load_suite(spec_path)


def test_suite_cli_dry_run_and_budget(spec_path, tmp_path, capsys):
    assert main(["suite", str(spec_path), "--dry-run", "--workers", "1"]) == 0
    assert "estimated" in capsys.readouterr().out
    spec_path.write_text(spec_path.read_text().replace("time_limit_minutes = 5", "time_limit_minutes = 0"))
    assert main(["suite", str(spec_path), "--workers", "1", "--cache", "", "--out", str(tmp_path)]) == 3

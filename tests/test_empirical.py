import copy
import json
import math
from pathlib import Path

import numpy as np
import pytest

from halibut.config import ConfigError, load_raw
from halibut.data.catalogue import load_catalogue
from halibut.data.periods import parse_period
from halibut.data.store import Observation, SeriesRecord, Store
from halibut.empirical import baselines as bl
from halibut.empirical.backtest import LeakageAuditError, evaluate, origin_inputs, run_backtest
from halibut.empirical.builder import build_config, covariate_path
from halibut.empirical.measurement import build_moments, month_at, month_index, observed_shares
from halibut.empirical.spec import load_pilot

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "configs" / "empirical" / "norway_bev_v1.toml"
CAT = ROOT / "configs" / "data" / "catalogue.toml"
DS = "ssb_14020_private_cars"
FUEL = "ssb_09654_fuel_prices:PetroleumProd=031"


def sid(code):
    return f"{DS}:TypeRegistrering=N:DrivstoffType={code}"


def true_share(t):  # months since 1995-01
    return 0.97 / (1 + math.exp(-0.06 * (t - 290)))


def make_store(tmp_path, last="2026-10", planted_future=False):
    store = Store(tmp_path / "s.sqlite")
    store.register_catalogue(load_catalogue(CAT))
    store.upsert_geographies([("NO", "Norway", "country", "ISO3166-1", "NO")])
    recs = [SeriesRecord(sid(c), DS, f"{DS}:TypeRegistrering=N", {"TypeRegistrering": "N", "DrivstoffType": c}, {})
            for c in ("19", "20", "21", "6")]
    recs.append(SeriesRecord(FUEL, "ssb_09654_fuel_prices", "ssb_09654_fuel_prices", {"PetroleumProd": "031"}, {}))
    store.upsert_series(recs)
    sn, _ = store.add_snapshot(DS, b"synthetic-1", {}, tmp_path / "raw", retrieved_at="2026-11-23T00:00:00Z",
                               publisher_updated="2026-11-03T00:00:00Z")
    obs, fuel = [], []
    n = month_index(last, "1995-01") + 1
    for t in range(n):
        p = month_at(t, "1995-01")
        period, a, b = parse_period(p, "M")
        bev = true_share(t)
        hyb = 0.1 * math.exp(-((t - 280) / 60) ** 2)
        tot = 10000.0
        for code, share in (("19", bev), ("21", hyb), ("20", 1 - bev - hyb), ("6", 0.0)):
            obs.append(Observation(sid(code), "NO", period, a, b, round(tot * max(share, 0))))
        fuel.append(Observation(FUEL, "NO", period, a, b, 10 + 2 * math.sin(t / 20)))
    store.load_observations(sn, obs, lag_days=10)
    s2, _ = store.add_snapshot("ssb_09654_fuel_prices", b"synthetic-f", {}, tmp_path / "raw",
                               retrieved_at="2026-11-23T00:00:00Z", publisher_updated="2026-11-19T00:00:00Z")
    store.load_observations(s2, fuel, lag_days=35)
    if planted_future:
        s3, _ = store.add_snapshot(DS, b"future", {}, tmp_path / "raw", retrieved_at="2031-01-01T00:00:00Z",
                                   publisher_updated="2031-01-01T00:00:00Z")
        p, a, b = parse_period("2015M06", "M")
        store.load_observations(s3, [Observation(sid("19"), "NO", p, a, b, 9999.0)], lag_days=10)
    return store


def tiny_spec(tmp_path):
    tmpl = load_raw(ROOT / "configs" / "empirical" / "norway_bev_template.toml")
    text = (ROOT / "configs" / "empirical" / "norway_bev_template.toml").read_text()
    text = text.replace("size = 4000", "size = 300").replace("k = 10", "k = 6").replace("probability = 0.005", "probability = 0.05")
    (tmp_path / "tmpl.toml").write_text(text)
    s = SPEC.read_text().replace('template = "norway_bev_template.toml"', f'template = "{tmp_path / "tmpl.toml"}"')
    s = s.replace("popsize = 6", "popsize = 2").replace("maxiter = 6", "maxiter = 0").replace("nelder_mead_maxfev = 30", "nelder_mead_maxfev = 0")
    s = s.replace("seeds = [101, 102]", "seeds = [101]").replace("forecast_seeds = [201, 202, 203, 204]", "forecast_seeds = [201, 202]")
    s = s.replace('origins = ["2016-01-15", "2019-01-15", "2022-01-15", "2024-01-15"]', 'origins = ["2019-01-15", "2022-01-15"]')
    s = s.replace("horizon_months = 24", "horizon_months = 6")
    (tmp_path / "spec.toml").write_text(s)
    return load_pilot(tmp_path / "spec.toml")


def test_pilot_spec_loads_and_forbids_free_temperature(tmp_path):
    spec = load_pilot(SPEC)
    assert spec.target_product == "bev" and spec.product_ids == ("bev", "hybrid", "fossil")
    assert "fuel_beta" in spec.parameter_names
    bad = SPEC.read_text().replace('target = "config:social_influence.weight"', 'target = "config:decision.temperature"')
    (tmp_path / "bad.toml").write_text(bad.replace('template = "norway_bev_template.toml"',
                                                   f'template = "{ROOT / "configs/empirical/norway_bev_template.toml"}"'))
    with pytest.raises(ConfigError, match="temperature"):
        load_pilot(tmp_path / "bad.toml")


def test_month_index_roundtrip():
    assert month_index("1995-01", "1995-01") == 0 and month_index("2026-10", "1995-01") == 381
    assert month_at(381, "1995-01") == "2026-10" and month_at(11, "1995-01") == "1995-12"


def test_measurement_and_moments(tmp_path):
    store = make_store(tmp_path)
    spec = load_pilot(SPEC)
    obs = observed_shares(store.as_of("2019-01-15"), spec)
    assert max(obs) == "2018-12"
    assert abs(sum(obs["2018-06"].values()) - 1) < 1e-12
    assert obs["2018-06"]["bev"] == pytest.approx(round(10000 * true_share(month_index("2018-06", "1995-01"))) / 10000, abs=1e-4)
    ms = build_moments(obs, spec)
    annual = [m for m in ms if m.name.startswith("annual")]
    assert {m.name for m in annual} >= {"annual_2005_bev", "annual_2018_hybrid"}
    assert not any(m.name.startswith("annual_2019") for m in ms)
    recent = [m for m in ms if m.name.startswith("month")]
    assert [m.months[0] for m in recent if m.product == "bev"] == [f"2018-{i:02d}" for i in range(7, 13)]
    store.close()


def test_builder_paths_and_temperature_guard(tmp_path):
    spec = tiny_spec(tmp_path)
    vals = {"bev_intercept": -6.0, "bev_trend": 0.3, "hybrid_intercept": -5.0, "hybrid_trend": 0.1,
            "social_weight": 2.0, "fuel_beta": 1.0}
    fuel = {"1995-01": 10.0, "1995-02": 20.0}
    cfg = build_config(spec, vals, 24, {"petrol_price_shock": fuel}, [1])
    assert cfg.decision.temperature == 1.0 and cfg.social_influence.weight == 2.0 and cfg.weeks == 24
    paths = {d.product: d.values for d in cfg.drivers}
    assert paths["bev"][12] == pytest.approx(-6.0 + 0.3 * 1.0)
    shock = covariate_path(fuel, spec.covariates[0], "1995-01", 24)
    assert shock[0] == 0.0 and shock[1] == pytest.approx(math.log(20) - math.log(10)) and shock[23] == shock[1]
    assert paths["fossil"][5] == pytest.approx(-1.0 * 1.0 * shock[5])
    assert paths["hybrid"][5] == pytest.approx(-5.0 + 0.1 * 5 / 12 - 0.5 * shock[5])
    text = (tmp_path / "tmpl.toml").read_text().replace("temperature = 1.0", "temperature = 0.8")
    (tmp_path / "tmpl.toml").write_text(text)
    with pytest.raises(ConfigError, match="temperature"):
        build_config(spec, vals, 24, {}, [1])


def test_origin_inputs_exclude_future_and_planted_values(tmp_path):
    (tmp_path / "a").mkdir()
    clean = make_store(tmp_path / "a")
    spec = load_pilot(SPEC)
    a = origin_inputs(clean, spec, "2016-01-15")
    assert a["last_observed"] == "2015-12" and max(a["covariates"]["petrol_price_shock"]) <= "2015-12"
    assert a["max_available_at_used"] <= "2016-01-15T23:59:59Z"
    (tmp_path / "b").mkdir()
    planted = make_store(tmp_path / "b", planted_future=True)
    b = origin_inputs(planted, spec, "2016-01-15")
    assert a["obs"] == b["obs"] and a["covariates"] == b["covariates"]
    y = np.array([a["obs"][p]["bev"] for p in sorted(a["obs"])])
    yb = np.array([b["obs"][p]["bev"] for p in sorted(b["obs"])])
    assert np.array_equal(bl.logistic(y[120:], 6, np.arange(len(y) - 120.0))["point"],
                          bl.logistic(yb[120:], 6, np.arange(len(yb) - 120.0))["point"])
    later = origin_inputs(planted, spec, "2031-06-01")
    assert later["obs"]["2015-06"]["bev"] != a["obs"]["2015-06"]["bev"]  # the revision becomes visible later
    clean.close(), planted.close()


def test_events_known_respect_origin(tmp_path):
    from halibut.data.events import load_events
    store = make_store(tmp_path)
    store.upsert_events(load_events(ROOT / "configs" / "data" / "events_norway_ev.toml"))
    spec = load_pilot(SPEC)
    early = origin_inputs(store, spec, "2022-01-15")["events_known"]
    assert "no_ev_vat_threshold_500k_2023" not in early and "no_ev_vat_zero_rate_2001" in early
    store.close()


def test_baselines_recover_known_curves():
    t = np.arange(200, dtype=float)
    y = bl.logistic_curve(t, 0.9, 0.05, 150)
    r = bl.logistic(y, 12, t)
    assert r["params"]["K"] == pytest.approx(0.9, abs=1e-3) and r["params"]["r"] == pytest.approx(0.05, abs=1e-3)
    assert np.allclose(r["point"], bl.logistic_curve(t[-1] + np.arange(1, 13), 0.9, 0.05, 150), atol=1e-4)
    yb = bl.bass_curve(t, 0.002, 0.04, 0.95)
    rb = bl.bass(yb, 12, t)
    assert rb["params"]["q"] == pytest.approx(0.04, rel=0.02) and rb["params"]["m"] == pytest.approx(0.95, abs=0.01)
    z = -3 + 0.02 * t
    lin = bl.expit(z)
    for fn in (bl.drift, bl.linear_trend):
        assert np.allclose(fn(lin, 6)["point"], bl.expit(z[-1] + 0.02 * np.arange(1, 7)), atol=1e-6)
    assert np.allclose(bl.holt(lin, 6)["point"], bl.expit(z[-1] + 0.02 * np.arange(1, 7)), atol=1e-3)
    assert np.all(bl.naive(lin, 6)["point"] == lin[-1])
    assert np.all(np.diff(bl.naive(lin + 0.01 * np.sin(t), 6)["sd"]) > 0)  # random-walk sd grows with h


def test_evaluate_metrics_by_hand(tmp_path):
    store = make_store(tmp_path)
    spec = load_pilot(SPEC)
    a1 = observed_shares(store.latest(), spec)["2019-01"]["bev"]
    a2 = observed_shares(store.latest(), spec)["2019-02"]["bev"]
    res = [{"origin": "2019-01-15", "targets": ["2019-01", "2019-02", "2099-01"], "naive_scale": 0.01,
            "models": {"halibut": {"point": [a1 + 0.02, a2 - 0.04, 0.5], "sd": [0.01, 0.01, 0.01]},
                       "naive": {"point": [a1, a2, 0.5], "sd": [0.05, 0.05, 0.05]}}}]
    ev = evaluate(spec, store, res)
    h = ev["summary"]["halibut"]["all"]
    assert h["n"] == 2 and h["mae_pp"] == pytest.approx(3.0) and h["bias_pp"] == pytest.approx(-1.0)
    assert h["mase"] == pytest.approx(3.0) and h["coverage95"] == 0.0
    assert ev["baselines_better_than_halibut"] == ["naive"] and ev["ranking_by_mae"][0] == "naive"
    store.close()


def test_tiny_backtest_end_to_end_and_resume(tmp_path):
    store = make_store(tmp_path)
    spec = tiny_spec(tmp_path)
    logs = []
    res = run_backtest(spec, store, tmp_path / "out", workers=1, cache_dir=tmp_path / "cache", log=logs.append)
    assert len(res["origins"]) == 2
    o = res["origins"][0]
    assert o["last_observed"] == "2018-12" and o["targets"][0] == "2019-01"
    assert set(o["models"]) == {"halibut", "naive", "drift", "linear_trend", "holt", "logistic", "bass"}
    assert all(len(m["point"]) == 6 for m in o["models"].values())
    assert "halibut" in res["evaluation"]["summary"]
    again = run_backtest(spec, store, tmp_path / "out", workers=1, cache_dir=tmp_path / "cache", log=logs.append)
    assert sum("already computed" in m for m in logs) == 2
    assert again["evaluation"]["summary"] == res["evaluation"]["summary"]
    store.close()

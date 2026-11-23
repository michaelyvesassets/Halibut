import json
from pathlib import Path

import pytest

from halibut.config import ConfigError
from halibut.data.catalogue import load_catalogue
from halibut.data.periods import parse_period
from halibut.data.store import LeakageError, Observation, SeriesRecord, Store, to_timestamp

CATALOGUE = Path(__file__).resolve().parents[1] / "configs" / "data" / "catalogue.toml"


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "t.sqlite")
    s.register_catalogue(load_catalogue(CATALOGUE))
    s.upsert_geographies([("NO", "Norway", "country", "ISO3166-1", "NO")])
    ds = "ssb_14020_private_cars"
    s.upsert_series([
        SeriesRecord(f"{ds}:TypeRegistrering=N:DrivstoffType=19", ds, f"{ds}:TypeRegistrering=N", {"TypeRegistrering": "N", "DrivstoffType": "19"}, {}),
        SeriesRecord(f"{ds}:TypeRegistrering=N:DrivstoffType=20", ds, f"{ds}:TypeRegistrering=N", {"TypeRegistrering": "N", "DrivstoffType": "20"}, {}),
        SeriesRecord(f"{ds}:TypeRegistrering=B:DrivstoffType=19", ds, f"{ds}:TypeRegistrering=B", {"TypeRegistrering": "B", "DrivstoffType": "19"}, {}),
    ])
    yield s
    s.close()


EV = "ssb_14020_private_cars:TypeRegistrering=N:DrivstoffType=19"
ICE = "ssb_14020_private_cars:TypeRegistrering=N:DrivstoffType=20"
USED_EV = "ssb_14020_private_cars:TypeRegistrering=B:DrivstoffType=19"


def obs(series, period, value):
    p, a, b = parse_period(period, "M")
    return Observation(series, "NO", p, a, b, value)


def snap(store, tmp_path, payload, retrieved, updated=None):
    sid, new = store.add_snapshot("ssb_14020_private_cars", json.dumps(payload).encode(), {"q": 1},
                                  tmp_path / "raw", retrieved_at=retrieved, publisher_updated=updated)
    return sid


def test_catalogue_loads_and_validates(tmp_path):
    cat = load_catalogue(CATALOGUE)
    d = cat.dataset("ssb_14020_private_cars")
    assert d.evidence_class == "transaction" and d.universe_dims == ("TypeRegistrering",)
    assert cat.sources["ssb"].licence == "CC-BY-4.0" and cat.sources["ssb"].redistribution_allowed
    assert d.series_id({"TypeRegistrering": "N", "DrivstoffType": "19"}) == EV
    bad = CATALOGUE.read_text().replace('universe_dims = ["TypeRegistrering"]\ntime_dim', 'universe_dims = ["Nope"]\ntime_dim', 1)
    (tmp_path / "bad.toml").write_text(bad)
    with pytest.raises(ConfigError):
        load_catalogue(tmp_path / "bad.toml")


def test_migrations_idempotent(tmp_path):
    s = Store(tmp_path / "m.sqlite")
    assert s.migrate() == []
    s.close()
    s2 = Store(tmp_path / "m.sqlite")
    assert [r[0] for r in s2.conn.execute("SELECT version FROM schema_migrations")] == [1, 2, 3]


def test_v1_store_upgrades_in_place(tmp_path):
    import sqlite3
    from halibut.data import store as st
    path = tmp_path / "old.sqlite"
    saved = st.MIGRATIONS
    st.MIGRATIONS = saved[:1]
    try:
        old = Store(path)
        old.register_catalogue(load_catalogue(CATALOGUE))
        n_sources = old.conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
        old.close()
    finally:
        st.MIGRATIONS = saved
    new = Store(path)
    assert [r[0] for r in new.conn.execute("SELECT version FROM schema_migrations")] == [1, 2, 3]
    assert new.conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0] == n_sources
    assert new.conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0] == 0
    new.close()


def test_periods():
    assert parse_period("1995M01", "M") == ("1995-01", "1995-01-01", "1995-01-31")
    assert parse_period("2024M02", "M")[2] == "2024-02-29"
    assert parse_period("2024K3", "Q") == ("2024-Q3", "2024-07-01", "2024-09-30")
    assert parse_period("2025", "A") == ("2025", "2025-01-01", "2025-12-31")
    with pytest.raises(ValueError):
        parse_period("2025M13", "M")


def test_first_snapshot_uses_conservative_lag(store, tmp_path):
    sid = snap(store, tmp_path, {"a": 1}, "2026-11-23T10:00:00Z", "2026-11-03T07:00:00Z")
    store.load_observations(sid, [obs(EV, "2020M01", 100.0), obs(EV, "2026M10", 9.0)], lag_days=10)
    rows = {r["period"]: r for r in store.conn.execute("SELECT * FROM observations")}
    assert rows["2020-01"]["available_at"] == "2020-02-10T23:59:59Z"
    assert rows["2020-01"]["availability_basis"] == "estimated_lag"
    # latest month: the estimate would be after the release, so the release time is used
    assert rows["2026-10"]["available_at"] == "2026-11-03T07:00:00Z"
    assert rows["2026-10"]["availability_basis"] == "published"


def test_revisions_and_as_of(store, tmp_path):
    s1 = snap(store, tmp_path, {"v": 1}, "2026-01-05T00:00:00Z", "2026-01-04T07:00:00Z")
    store.load_observations(s1, [obs(EV, "2025M11", 100.0), obs(EV, "2025M12", 200.0)], lag_days=10)
    s2 = snap(store, tmp_path, {"v": 2}, "2026-02-05T00:00:00Z", "2026-02-03T07:00:00Z")
    counts = store.load_observations(s2, [obs(EV, "2025M11", 100.0), obs(EV, "2025M12", 210.0),
                                          obs(EV, "2026M01", 50.0)], lag_days=10)
    assert counts == {"new": 1, "revised": 1, "unchanged": 1}
    before = store.as_of("2026-01-31")
    assert before.series(EV, "NO") == [("2025-11", 100.0), ("2025-12", 200.0)]
    after = store.as_of("2026-02-04")
    assert after.series(EV, "NO") == [("2025-11", 100.0), ("2025-12", 210.0), ("2026-01", 50.0)]
    assert after.max_available_at_used == "2026-02-03T07:00:00Z"
    # identical raw bytes are deduplicated
    assert snap(store, tmp_path, {"v": 2}, "2026-03-01T00:00:00Z") == s2


def test_planted_future_values_are_invisible(store, tmp_path):
    s1 = snap(store, tmp_path, {"v": 1}, "2026-01-05T00:00:00Z", "2026-01-04T07:00:00Z")
    store.load_observations(s1, [obs(EV, "2025M10", 1.0)], lag_days=10)
    view = store.as_of("2025-12-31")
    baseline = view.series(EV, "NO")
    s2 = snap(store, tmp_path, {"v": "future"}, "2030-01-05T00:00:00Z", "2030-01-04T00:00:00Z")
    store.load_observations(s2, [obs(EV, "2025M10", 999.0), obs(EV, "2029M12", 5.0)], lag_days=10)
    assert store.as_of("2025-12-31").series(EV, "NO") == baseline
    assert store.as_of("2030-02-01").series(EV, "NO")[-1] == ("2029-12", 5.0)


def test_shares_refuse_mixed_universes_and_missing_values(store, tmp_path):
    s1 = snap(store, tmp_path, {"v": 1}, "2026-01-05T00:00:00Z", "2026-01-04T07:00:00Z")
    store.load_observations(s1, [obs(EV, "2025M11", 90.0), obs(ICE, "2025M11", 10.0),
                                 obs(EV, "2025M12", 50.0), obs(ICE, "2025M12", None),
                                 obs(USED_EV, "2025M11", 7.0)], lag_days=10)
    v = store.latest()
    assert v.shares([EV, ICE], "NO") == {"2025-11": {EV: 0.9, ICE: 0.1}}  # 2025-12 omitted, not zero
    with pytest.raises(ConfigError, match="universes"):
        v.shares([EV, USED_EV], "NO")


def test_series_definition_cannot_change(store):
    ds = "ssb_14020_private_cars"
    with pytest.raises(ConfigError, match="definition changed"):
        store.upsert_series([SeriesRecord(EV, ds, f"{ds}:TypeRegistrering=B", {"TypeRegistrering": "N", "DrivstoffType": "19"}, {})])


def test_timestamps():
    assert to_timestamp("2026-01-04") == "2026-01-04T23:59:59Z"
    assert to_timestamp("2026-01-04T07:00:00+01:00") == "2026-01-04T06:00:00Z"

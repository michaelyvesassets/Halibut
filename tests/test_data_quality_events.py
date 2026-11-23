import json
from pathlib import Path

import pytest

from halibut.config import ConfigError
from halibut.data import ssb
from halibut.data.catalogue import load_catalogue
from halibut.data.events import load_events
from halibut.data.quality import _count_gaps, run_quality_checks
from halibut.data.store import Store

ROOT = Path(__file__).resolve().parents[1]
CATALOGUE = ROOT / "configs" / "data" / "catalogue.toml"
EVENTS = ROOT / "configs" / "data" / "events_norway_ev.toml"
FIX = ROOT / "tests" / "fixtures" / "ssb"
DS = "ssb_14020_private_cars"


def test_norway_events_file_is_valid_and_sourced():
    events = load_events(EVENTS)
    assert len(events) >= 10
    for e in events:
        assert e["sources"] and e["observed"] and e["assumed_effect"]
        assert e["verification"] in ("primary", "secondary", "conflicting")
    by_id = {e["id"]: e for e in events}
    vat23 = by_id["no_ev_vat_threshold_500k_2023"]
    assert vat23["verification"] == "primary" and vat23["available_at"] == "2023-01-01T23:59:59Z"
    assert by_id["no_ev_vat_phaseout_2027_2028_proposed"]["status"] == "proposed"
    assert by_id["no_target_zero_emission_new_cars_2025"]["available_at_basis"] == "explicit"


def test_events_require_sources_and_valid_dates(tmp_path):
    p = tmp_path / "e.toml"
    p.write_text('schema_version = "1.0"\ngeo = "NO"\n[[events]]\nid="x"\ncategory="vat"\ntitle="t"\nstatus="in_force"\n'
                 'verification="primary"\nobserved="o"\nassumed_effect="a"\neffective_date="2023-01-01"\nsources=[]\n')
    with pytest.raises(ConfigError, match="source"):
        load_events(p)
    p.write_text(p.read_text().replace("sources=[]", 'sources=["https://example.org"]').replace("2023-01-01", "2023-13-01"))
    with pytest.raises(ConfigError, match="YYYY-MM-DD"):
        load_events(p)


def test_events_respect_as_of(tmp_path):
    store = Store(tmp_path / "s.sqlite")
    store.upsert_events(load_events(EVENTS))
    early = {e["id"] for e in store.as_of("2022-12-31").events("NO")}
    late = {e["id"] for e in store.as_of("2026-11-30").events("NO")}
    assert "no_ev_vat_threshold_500k_2023" not in early and "no_ev_vat_threshold_500k_2023" in late
    assert "no_ev_vat_zero_rate_2001" in early
    store.close()


def test_rebuild_reproduces_ingestion(tmp_path):
    cat = load_catalogue(CATALOGUE)
    raw = tmp_path / "raw"
    a = Store(tmp_path / "a.sqlite")
    a.register_catalogue(cat)
    ssb.ingest(a, cat, DS, raw, raw=(FIX / "14020_new_private_cars_2026-11.json").read_bytes(),
               retrieved_at="2026-11-23T01:00:00Z")
    ssb.rebuild(cat, raw, tmp_path / "b.sqlite")
    b = Store(tmp_path / "b.sqlite")
    q = "SELECT series_id, geo_id, period, value, revision, available_at, availability_basis FROM observations ORDER BY 1,2,3,5"
    assert a.conn.execute(q).fetchall() == b.conn.execute(q).fetchall()
    a.close(), b.close()


def test_quality_flags_too_short_lag_and_gaps(tmp_path):
    text = CATALOGUE.read_text().replace("publication_lag_days = 10   # observed 3 days", "publication_lag_days = 1   # test")
    (tmp_path / "cat.toml").write_text(text)
    cat = load_catalogue(tmp_path / "cat.toml")
    store = Store(tmp_path / "s.sqlite")
    store.register_catalogue(cat)
    doc = json.loads((FIX / "jsonstat_small.json").read_bytes())
    doc["updated"] = "2026-02-05T07:00:00Z"  # 5 days after the last period ends
    ssb.ingest(store, cat, DS, tmp_path / "raw", raw=json.dumps(doc).encode(), retrieved_at="2026-02-06T00:00:00Z")
    report = run_quality_checks(store, cat, checks_file=None)
    lag = next(c for c in report["checks"] if c["name"] == f"{DS}:publication_lag")
    assert lag["status"] == "fail" and "TOO SHORT" in lag["message"]
    comp = next(c for c in report["checks"] if c["name"] == f"{DS}:completeness")
    assert comp["details"]["missing"] == 1 and comp["status"] == "warn"
    store.close()
    assert _count_gaps(["2020-01", "2020-02", "2020-05"], "M") == 2
    assert _count_gaps(["2019", "2021"], "A") == 1

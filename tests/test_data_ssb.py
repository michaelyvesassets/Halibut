import io
import json
import urllib.error
from pathlib import Path

import pytest

from halibut.cli import main
from halibut.data import ssb
from halibut.data.catalogue import load_catalogue
from halibut.data.quality import status_rows
from halibut.data.store import Store

ROOT = Path(__file__).resolve().parents[1]
CATALOGUE = ROOT / "configs" / "data" / "catalogue.toml"
FIX = ROOT / "tests" / "fixtures" / "ssb"
DS = "ssb_14020_private_cars"


@pytest.fixture
def env(tmp_path):
    cat = load_catalogue(CATALOGUE)
    store = Store(tmp_path / "s.sqlite")
    store.register_catalogue(cat)
    yield cat, store, tmp_path / "raw"
    store.close()


def test_build_query_matches_catalogue(env):
    cat, _, _ = env
    q = ssb.build_query(cat.dataset(DS))
    assert q["response"]["format"] == "json-stat2"
    sel = {x["code"]: x["selection"] for x in q["query"]}
    assert sel["TypeRegistrering"] == {"filter": "item", "values": ["N", "B"]}
    assert sel["DrivstoffType"] == {"filter": "all", "values": ["*"]}


def test_parse_small_fixture(env):
    cat, _, _ = env
    p = ssb.parse_jsonstat((FIX / "jsonstat_small.json").read_bytes(), cat.dataset(DS))
    ids = {s.series_id: s for s in p["series"]}
    assert set(ids) == {f"{DS}:TypeRegistrering={t}:DrivstoffType={f}" for t in "NB" for f in ("19", "20")}
    assert ids[f"{DS}:TypeRegistrering=B:DrivstoffType=19"].universe_key == f"{DS}:TypeRegistrering=B"
    assert ids[f"{DS}:TypeRegistrering=N:DrivstoffType=19"].labels["TypeRegistrering"] == "New vehicles"
    missing = [o for o in p["observations"] if o.value is None]
    assert len(missing) == 1 and missing[0].status == ".." and missing[0].period == "2026-01"
    assert len(p["observations"]) == 12 and p["updated"] == "2026-02-03T07:00:00Z"


def test_ingest_is_idempotent_and_captures_revisions(env):
    cat, store, raw = env
    small = (FIX / "jsonstat_small.json").read_bytes()
    r1 = ssb.ingest(store, cat, DS, raw, raw=small, retrieved_at="2026-02-04T00:00:00Z")
    assert r1["new"] == 12 and r1["new_snapshot"]
    r2 = ssb.ingest(store, cat, DS, raw, raw=small, retrieved_at="2026-02-05T00:00:00Z")
    assert (r2["new"], r2["revised"], r2["unchanged"], r2["new_snapshot"]) == (0, 0, 12, False)
    doc = json.loads(small)
    doc["value"][2] = 980.0  # SSB fills in the missing month ...
    doc["value"][1] = 951.0  # ... and revises the previous one
    doc["updated"] = "2026-03-03T07:00:00Z"
    r3 = ssb.ingest(store, cat, DS, raw, raw=json.dumps(doc).encode(), retrieved_at="2026-03-04T00:00:00Z")
    assert (r3["new"], r3["revised"]) == (0, 2)
    ev = f"{DS}:TypeRegistrering=N:DrivstoffType=19"
    assert store.as_of("2026-02-28").series(ev, "NO") == [("2025-11", 900.0), ("2025-12", 950.0), ("2026-01", None)]
    assert store.as_of("2026-03-03T07:00:00Z").series(ev, "NO")[1:] == [("2025-12", 951.0), ("2026-01", 980.0)]
    runs = store.conn.execute("SELECT status FROM ingest_runs").fetchall()
    assert [r[0] for r in runs] == ["ok", "ok", "ok"]
    assert len(list((raw / DS).iterdir())) == 2  # raw snapshots are content-addressed


def test_real_ssb_fixture(env):
    cat, store, raw = env
    r = ssb.ingest(store, cat, DS, raw, raw=(FIX / "14020_new_private_cars_2026-11.json").read_bytes(),
                   retrieved_at="2026-11-23T01:00:00Z")
    assert r["series"] == 4 and r["cells"] == 382 * 4
    v = store.latest()
    ids = [f"{DS}:TypeRegistrering=N:DrivstoffType={f}" for f in ("19", "20", "21", "6")]
    shares = v.shares(ids, "NO")
    assert shares["2025-12"][ids[0]] > 0.9 and shares["1995-01"][ids[0]] == 0.0
    row = next(x for x in status_rows(store) if x["dataset"] == DS)
    assert row["periods"] == "1995-01..2026-10" and row["missing"] == 0


def test_failed_ingest_is_recorded(env):
    cat, store, raw = env
    with pytest.raises(Exception):
        ssb.ingest(store, cat, DS, raw, raw=b"not json")
    assert store.conn.execute("SELECT status FROM ingest_runs").fetchone()[0] == "failed"
    assert store.conn.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0


def test_fetcher_retries_then_succeeds(monkeypatch):
    calls, sleeps = [], []

    class Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout):
        calls.append(req.get_header("User-agent"))
        if len(calls) < 2:
            raise urllib.error.URLError("temporary")
        return Resp(b"{}")

    monkeypatch.setattr(ssb.urllib.request, "urlopen", fake_urlopen)
    f = ssb.Fetcher(min_interval=0.0, sleep=sleeps.append)
    assert f.post_json("https://example.invalid/x", {}) == b"{}"
    assert len(calls) == 2 and calls[0].startswith("halibut-research-client/") and sleeps


def test_fetcher_does_not_retry_bad_queries(monkeypatch):
    def fake_urlopen(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 400, "bad", {}, io.BytesIO(b"bad query"))

    monkeypatch.setattr(ssb.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(ssb.FetchError, match="HTTP 400"):
        ssb.Fetcher(min_interval=0.0, sleep=lambda s: None).post_json("https://example.invalid/x", {})


def test_data_cli_init_and_status(tmp_path, capsys):
    db = str(tmp_path / "c.sqlite")
    assert main(["data", "--db", db, "--catalogue", str(CATALOGUE), "init"]) == 0
    assert main(["data", "--db", db, "--catalogue", str(CATALOGUE), "status"]) == 0
    assert "ssb_14020_private_cars" in capsys.readouterr().out

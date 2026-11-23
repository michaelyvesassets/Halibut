import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from halibut.data.http import USER_AGENT, Budget, BudgetExhausted, PoliteClient, ProviderError, Response
from halibut.data.providers import base, dft, gdelt, overpass, wikidata, worldbank
from halibut.data.store import Store

FIX = Path(__file__).resolve().parent / "fixtures" / "intel"
ROOT = Path(__file__).resolve().parents[1]


class FakeNet:
    """Offline transport: maps URL substrings to fixture bytes; records calls."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, method, url, data, headers, timeout):
        self.calls.append((method, url, headers.get("User-Agent")))
        for key, val in self.routes.items():
            if key in url:
                status, body = val if isinstance(val, tuple) else (200, val)
                return Response(url, status, body, {})
        return Response(url, 404, b"not found", {})


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "s.sqlite")
    base.register(s, ROOT / "configs" / "data" / "providers.toml")
    yield s
    s.close()


def client(store, net, **kw):
    return PoliteClient("test", Budget(**kw), store, min_interval=0, transport=net, sleep=lambda s: None)


def test_registry_has_licences_and_evidence_classes(store):
    sources, datasets = base.load_registry(ROOT / "configs" / "data" / "providers.toml")
    assert sources["osm"].licence == "ODbL-1.0" and "share" in sources["osm"].attribution.lower()
    assert sources["dft"].licence == "OGL-UK-3.0" and sources["wikidata"].licence == "CC0-1.0"
    assert datasets["gdelt_gkg_sample"].evidence_class == "proxy_media"
    assert datasets["dft_veh0160_makes"].evidence_class == "transaction"
    assert datasets["osm_footprint"].evidence_class == "presence"
    assert "imported" in datasets["dft_veh0160_makes"].definition


def test_client_budget_retries_and_logging(store):
    net = FakeNet({"/ok": b"x" * 10, "/busy": (429, b"slow down"), "/bad": (400, b"no")})
    c = client(store, net, max_requests=5, max_bytes=100)
    assert c.get("https://h/ok").body == b"x" * 10
    with pytest.raises(ProviderError, match="429"):
        c.get("https://h/busy")  # 1 try + 2 retries
    with pytest.raises(ProviderError, match="400"):
        c.get("https://h/bad")  # not retried
    with pytest.raises(BudgetExhausted, match="request"):
        c.get("https://h/ok")
    assert c.budget.requests == 5
    log = store.conn.execute("SELECT outcome FROM fetch_log ORDER BY fetch_id").fetchall()
    assert [r[0] for r in log] == ["ok", "http 429", "http 429", "http 429", "http 400"]
    assert all(ua == USER_AGENT for _, _, ua in net.calls) and "github.com" in USER_AGENT and "@" not in USER_AGENT
    with pytest.raises(BudgetExhausted, match="byte"):
        client(store, net, max_bytes=5).get("https://h/ok", expected_bytes=10)


def test_worldbank(store, tmp_path):
    net = FakeNet({"api.worldbank.org": (FIX / "worldbank_nor_gdp.json").read_bytes()})
    out = worldbank.fetch_indicators(store, client(store, net), tmp_path / "raw", "NO", ["NY.GDP.PCAP.CD"])
    assert out["indicators"]["NY.GDP.PCAP.CD"]["values"] == 5
    vals = dict(store.latest().series("worldbank:NY.GDP.PCAP.CD", "NO"))
    assert vals["2024"] == pytest.approx(89888.9535204758)


def test_wikidata_resolution_separates_brand_company_parent(store, tmp_path):
    net = FakeNet({"wbsearchentities": (FIX / "wikidata_search_byd.json").read_bytes(),
                   "query.wikidata.org": (FIX / "wikidata_claims_byd.json").read_bytes()})
    comp = SimpleNamespace(id="byd", name="BYD", aliases=("byd",), dft_make="BYD", wikidata=None)
    r = wikidata.resolve_competitor(client(store, net), store, tmp_path / "raw", comp, ["Q786820"], ["automobile"],
                                    "2026-11-23")
    assert r["method"] == "type_match" and r["qid"] == "Q27423"  # BYD Auto, not BYD Company or Bydgoszcz
    kinds = dict(store.conn.execute("SELECT entity_id, kind FROM entities").fetchall())
    assert kinds["brand:byd"] == "brand" and kinds["wd:Q27423"] == "company" and kinds["wd:Q2608329"] == "company"
    rels = {(a, p, b) for a, p, b in store.conn.execute("SELECT subject_id, predicate, object_id FROM relationships")}
    assert ("brand:byd", "operated_by", "wd:Q27423") in rels
    assert ("wd:Q27423", "parent_organization", "wd:Q2608329") in rels
    assert ("wd:Q27423", "country", "wd:Q148") in rels
    status = dict(store.conn.execute("SELECT predicate, epistemic_status FROM relationships").fetchall())
    assert status["operated_by"] == "derived" and status["parent_organization"] == "observed"


def test_wikidata_unresolved_is_never_guessed(store, tmp_path):
    net = FakeNet({"wbsearchentities": (FIX / "wikidata_search_byd.json").read_bytes(),
                   "query.wikidata.org": (FIX / "wikidata_claims_byd.json").read_bytes()})
    comp = SimpleNamespace(id="byd", name="BYD", aliases=("byd",), dft_make="BYD", wikidata=None)
    r = wikidata.resolve_competitor(client(store, net), store, tmp_path / "raw", comp, ["Q999"], ["spaceship"], "2026-11-23")
    assert r["method"] == "unresolved" and "company" not in r
    assert store.conn.execute("SELECT COUNT(*) FROM relationships").fetchone()[0] == 0


def test_overpass_footprint_locations_and_licence(store, tmp_path):
    net = FakeNet({"overpass-api.de": (FIX / "overpass_dealers.json").read_bytes()})
    q = overpass.build_query("GB", {"shop": "car"}, ["Tesla", "BYD"])
    assert 'area["ISO3166-1"="GB"]' in q and '["brand"~"^(BYD|Tesla)$",i]' in q.replace("Tesla|BYD", "BYD|Tesla")
    for b in ("tesla", "byd"):
        base.upsert_entity(store, f"brand:{b}", "brand", b, "asserted", "test")
    out = overpass.fetch_footprint(store, client(store, net), tmp_path / "raw", "GB", {"shop": "car"},
                                   {"brand:tesla": ["Tesla"], "brand:byd": ["BYD"]}, "2026")
    assert out["counts"] == {"brand:tesla": 2, "brand:byd": 1} and out["osm_timestamp"] == "2026-11-23T12:38:21Z"
    locs = store.conn.execute("SELECT COUNT(*) FROM entities WHERE kind='location'").fetchone()[0]
    assert locs == 3
    rel = store.conn.execute("SELECT subject_id, object_id FROM relationships WHERE predicate='located_at'").fetchall()
    assert {r[0] for r in rel} == {"brand:tesla", "brand:byd"}
    lic = store.conn.execute("""SELECT so.licence FROM snapshots sn JOIN datasets d ON d.dataset_id=sn.dataset_id
        JOIN sources so ON so.source_id=d.source_id WHERE sn.dataset_id='osm_footprint'""").fetchone()[0]
    assert lic == "ODbL-1.0"


def test_gdelt_plan_matching_and_budget(store, tmp_path):
    plan = gdelt.sample_plan("2026-01-01", "2026-06-30", 6)
    assert plan[0] == "20260101120000" and plan[-1] == "20260630120000" and len(plan) == 6
    assert plan == gdelt.sample_plan("2026-01-01", "2026-06-30", 6)  # reproducible
    zipped = (FIX / "gdelt_gkg_synthetic.zip").read_bytes()
    text = zipfile.ZipFile(io.BytesIO(zipped)).read("20260601120000.gkg.csv").decode()
    aliases = {"tesla": "brand:tesla", "byd": "brand:byd", "ford": "brand:ford", "ford motor company": "brand:ford"}
    recs = gdelt.parse_gkg(text, aliases, require_fips=("UK",))
    assert [r["url"] for r in recs] == ["https://example.org/a", "https://example.org/c", "https://example.org/d"]
    assert recs[2]["matched"] == {"brand:ford": "ford motor company"}  # whole names only: 'stanford' never matches
    for b in ("tesla", "byd", "ford"):
        base.upsert_entity(store, f"brand:{b}", "brand", b, "asserted", "test")
    net = FakeNet({"gdeltv2/": zipped})
    out = gdelt.fetch_sample(store, client(store, net), tmp_path / "raw", ["20260601120000"], aliases, ("UK",))
    assert out["matched_records"] == 3
    assert store.conn.execute("SELECT COUNT(*) FROM documents WHERE evidence_class='proxy_media'").fetchone()[0] == 3
    with pytest.raises(ValueError, match="max_files"):
        gdelt.fetch_sample(store, client(store, net), tmp_path / "raw", plan, aliases, max_files=3)


def test_dft_brand_and_model_series(store, tmp_path):
    net = FakeNet({"api/content": (FIX / "dft_content.json").read_bytes(),
                   "df_VEH0160_UK.csv": (FIX / "dft_veh0160_uk_slice.csv").read_bytes()})
    for b in ("tesla", "byd"):
        base.upsert_entity(store, f"brand:{b}", "brand", b, "asserted", "test")
    out = dft.ingest(store, client(store, net), tmp_path / "raw", {"TESLA": "brand:tesla", "BYD": "brand:byd"})
    assert out["first_quarter"] == "2014Q3" and out["last_quarter"] == "2026Q2"
    ids = [r.series_id for r in store.list_series("dft_veh0160_makes")]
    assert any("Make=TESLA:Fuel=Battery electric" in i for i in ids)
    assert not any("Motorcycles" in i for i in ids)  # body type filter
    v = store.latest()
    tesla = dict(v.series("dft_veh0160_makes:BodyType=Cars:Make=TESLA:Fuel=Battery electric", "GB"))
    raw = [r for r in __import__("csv").reader(open(FIX / "dft_veh0160_uk_slice.csv", encoding="utf-8-sig"))]
    hdr = raw[0]
    expected = sum(float(r[hdr.index("2025 Q4")]) for r in raw[1:] if r[0] == "Cars" and r[1] == "TESLA" and r[4] == "Battery electric")
    assert tesla["2025-Q4"] == expected
    prods = store.conn.execute("SELECT COUNT(*) FROM entities WHERE kind='product_line'").fetchone()[0]
    assert prods > 0
    assert store.conn.execute("SELECT COUNT(*) FROM relationships WHERE predicate='product_of'").fetchone()[0] == prods
    # makes universe and models universe never mix
    from halibut.config import ConfigError
    m = [r.series_id for r in store.list_series("dft_veh0160_models")][0]
    with pytest.raises(ConfigError, match="universes"):
        v.shares([ids[0], m], "GB")

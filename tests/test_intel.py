import json
from pathlib import Path

import pytest

from halibut.data.providers.base import register
from halibut.data.store import Store
from halibut.intel.market import load_market
from halibut.intel.workflow import investigate, plan
from halibut.learning.memory import LearningMemory
from halibut.validation import ALLOWED_LEVERS, check_action, validate_hypothesis

from .test_providers import FIX, FakeNet

ROOT = Path(__file__).resolve().parents[1]

MARKET = """
schema_version = "1.0"
[market]
id = "test_market"
name = "Test cars"
category = "cars"
geography = "GB"
gdelt_fips = ["UK"]
as_of = "2026-11-23"
mode = "annihilation"
focal = "byd"
target = "volkswagen"
[resolution]
wikidata_type_hints = ["Q786820"]
keywords = ["automobile"]
[[competitors]]
id = "byd"
name = "BYD"
aliases = ["byd"]
dft_make = "BYD"
osm_brand = ["BYD"]
wikidata = "Q27423"
[[competitors]]
id = "volkswagen"
name = "Volkswagen"
aliases = ["volkswagen"]
dft_make = "VOLKSWAGEN"
osm_brand = ["Volkswagen"]
wikidata = "Q27423"
[[competitors]]
id = "tesla"
name = "Tesla"
aliases = ["tesla"]
dft_make = "TESLA"
osm_brand = ["Tesla"]
wikidata = "Q27423"
[evidence]
worldbank_indicators = ["NY.GDP.PCAP.CD"]
transactions = { provider = "dft", body_type = "Cars", segment = "Battery electric" }
footprint = { provider = "osm", tags = { shop = "car" } }
media = { provider = "gdelt", start = "2026-06-01", end = "2026-06-01", n_files = 1 }
[limits]
max_requests = 40
max_megabytes = 30
max_seconds = 120
"""


def routes():
    return {"api/content": (FIX / "dft_content.json").read_bytes(),
            "df_VEH0160_UK.csv": (FIX / "dft_veh0160_uk_slice.csv").read_bytes(),
            "api.worldbank.org": (FIX / "worldbank_nor_gdp.json").read_bytes(),
            "query.wikidata.org": (FIX / "wikidata_claims_byd.json").read_bytes(),
            "overpass-api.de": (FIX / "overpass_dealers.json").read_bytes(),
            "gdeltv2/": (FIX / "gdelt_gkg_synthetic.zip").read_bytes()}


@pytest.fixture
def run(tmp_path):
    (tmp_path / "m.toml").write_text(MARKET)
    store = Store(tmp_path / "s.sqlite")
    register(store, ROOT / "configs" / "data" / "providers.toml")
    net = FakeNet(routes())
    r = investigate(store, tmp_path / "m.toml", tmp_path / "out", tmp_path / "raw", log=lambda *_: None, transport=net)
    yield r, store, net
    store.close()


def test_plan_and_market_spec():
    m = load_market(ROOT / "configs" / "intel" / "markets" / "uk_cars_bev_v1.toml")
    p = plan(m)
    assert m.mode == "annihilation" and m.focal == "byd" and m.target == "volkswagen"
    assert len(p["gdelt_files"]) == 12 and p["total"]["bytes"] < m.limits["max_megabytes"] * 1e6
    assert p["total"]["requests"] <= m.limits["max_requests"]


def test_end_to_end_offline_fixtures(run):
    r, store, net = run
    assert all(v["status"] == "ok" for v in r["steps"].values()), r["steps"]
    assert all(ua.startswith("halibut-research-client/") for _, _, ua in net.calls)
    tx = r["signals"]["transactions"]
    assert tx["available"] and tx["L4"][-1] == "2026-Q2"
    shares = [e["share_L4"] for e in tx["competitors"].values()]
    assert abs(sum(shares) - 1.0) < 1e-9  # the fixture's market consists of these three makes
    assert r["hypotheses"], "expected at least one validated hypothesis"
    for h in r["hypotheses"]:
        assert h["validation"]["passed"] and h["status"] == "proposed" and h["evidence"]
        assert set(h["levers"]) <= set(ALLOWED_LEVERS)
    modes = {h["mode"] for h in r["hypotheses"]}
    assert "annihilation" in modes
    ann = next(h for h in r["hypotheses"] if h["mode"] == "annihilation")
    assert any("displaced" in c["statement"] for c in ann["critiques"])
    assert r["scenario_drafts"] and r["scenario_drafts"][0]["validated"]
    assert set(r["scenario_drafts"][0]["preview_final_expected_shares"]) >= {"control", "focal_price_cut_10pct"}
    # learning memory: experiment + proposed hypotheses/predictions/critiques; no validated agent claims
    mem = LearningMemory(store)
    ks = mem.retrieve(tags=["market:test_market"])
    kinds = {k["kind"] for k in ks}
    assert {"hypothesis", "prediction", "critique"} <= kinds
    assert all(k["status"] == "proposed" for k in ks)
    assert mem.experiment(r["experiment_id"])["status"] == "completed"
    md = (Path(r["path"]) / "report.md").read_text()
    for section in ("Competitor graph", "Observed market signals", "Hypotheses", "Draft simulation scenarios", "Sources and licences"):
        assert section in md
    assert "ODbL" in md and "Open Government Licence" in md and "imported used vehicles" in md
    data = json.loads((Path(r["path"]) / "report.json").read_text())
    assert data["label"].startswith("Observed facts")


def test_validation_rejects_fabrication_and_prohibited_tactics(run):
    r, store, _ = run
    from halibut.intel.retrieval import StoreEvidenceRetriever
    retr = StoreEvidenceRetriever(store, "2026-11-23")
    metrics = {m["id"]: m for m in r["signals"]["transactions"]["metrics"]}
    for m in metrics.values():  # report copies are stripped; reload the definitions-with-values
        m.setdefault("inputs", [])
    good = dict(r["hypotheses"][0])
    assert validate_hypothesis(good, retr, metrics, retr.as_of) == []
    fake_metric = {**good, "evidence": ["metric:share:L4:byd"]}
    tampered = {k: dict(v) for k, v in metrics.items()}
    tampered["metric:share:L4:byd"]["value"] = 0.99
    assert any("does not match" in p for p in validate_hypothesis(fake_metric, retr, tampered, retr.as_of))
    assert any("does not exist" in p for p in validate_hypothesis({**good, "evidence": ["obs:999999999"]}, retr, metrics, retr.as_of))
    assert any("prohibited" in p for p in validate_hypothesis({**good, "claim": good["claim"] + " Use fake reviews."}, retr, metrics, retr.as_of))
    assert any("lever" in p for p in validate_hypothesis({**good, "levers": ["bot network"]}, retr, metrics, retr.as_of))
    assert any("hindsight" in p for p in validate_hypothesis({**good, "prediction": {**good["prediction"], "resolvable_after": "2020-01-01"}},
                                                               retr, metrics, retr.as_of))
    with pytest.raises(PermissionError):
        check_action("post_to_social_media")


def test_failed_provider_is_reported_not_substituted(tmp_path):
    (tmp_path / "m.toml").write_text(MARKET)
    store = Store(tmp_path / "s.sqlite")
    register(store, ROOT / "configs" / "data" / "providers.toml")
    rt = routes()
    rt["overpass-api.de"] = (502, b"busy")
    r = investigate(store, tmp_path / "m.toml", tmp_path / "out", tmp_path / "raw", log=lambda *_: None, transport=FakeNet(rt))
    assert r["steps"]["footprint"]["status"] == "failed" and not r["signals"]["footprint"]["available"]
    assert r["steps"]["transactions"]["status"] == "ok"
    store.close()

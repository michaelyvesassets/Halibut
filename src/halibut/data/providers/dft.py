"""UK DfT vehicle licensing statistics: brand- and model-level first registrations.

df_VEH0160_UK (OGL v3): quarterly vehicles registered for the first time by
body type, make, generic model, model and fuel. DfT: first registrations
"include both new vehicles and vehicles imported into the UK at the point of
first UK registration": the universe is FIRST UK REGISTRATIONS, not new-car
sales, and the two cannot be separated in this table.

Halibut aggregates body type 'Cars' to make x fuel (all makes, one universe)
and to make x generic model for competitor makes (a second universe).
"""

from __future__ import annotations

import csv
import io
import json
import re

from ..periods import parse_period
from ..store import Observation, SeriesRecord
from .base import add_identifier, add_relationship, snapshot, upsert_entity

CONTENT_API = "https://www.gov.uk/api/content/government/statistical-data-sets/vehicle-licensing-statistics-data-files"
FILE_PATTERN = re.compile(r"df_VEH0160_UK\.csv$")
MAKES, MODELS = "dft_veh0160_makes", "dft_veh0160_models"
PRODUCED_BY = "provider:dft"


def locate(client) -> tuple[str, str]:
    resp = client.get(CONTENT_API)
    d = json.loads(resp.body)
    for a in d.get("details", {}).get("attachments", []):
        if FILE_PATTERN.search(a.get("url", "")):
            return a["url"], d.get("public_updated_at")
    raise RuntimeError("df_VEH0160_UK.csv not listed on the DfT data files page")


def _quarter(col: str) -> str | None:
    m = re.match(r"^(\d{4}) Q([1-4])$", col.strip())
    return f"{m.group(1)}Q{m.group(2)}" if m else None


def parse(raw: bytes, body_type: str = "Cars", model_makes: set[str] | None = None) -> dict:
    rows = csv.reader(io.StringIO(raw.decode("utf-8-sig")))
    header = next(rows)
    qcols = [(i, _quarter(c)) for i, c in enumerate(header) if _quarter(c)]
    idx = {k: header.index(k) for k in ("BodyType", "Make", "GenModel", "Model", "Fuel")}
    makes: dict[tuple, dict] = {}
    models: dict[tuple, dict] = {}
    for r in rows:
        if r[idx["BodyType"]] != body_type:
            continue
        make, fuel, gen = r[idx["Make"]], r[idx["Fuel"]], r[idx["GenModel"]]
        for i, q in qcols:
            v = r[i].strip()
            x = float(v) if v not in ("", "[c]", "[x]", "[z]") else None
            if x is None:
                continue
            makes.setdefault((make, fuel), {}).setdefault(q, 0.0)
            makes[(make, fuel)][q] += x
            if model_makes is None or make in model_makes:
                models.setdefault((make, gen), {}).setdefault(q, 0.0)
                models[(make, gen)][q] += x
    return {"makes": makes, "models": models, "quarters": [q for _, q in qcols]}


def ingest(store, client, raw_dir, competitor_makes: dict[str, str], body_type: str = "Cars") -> dict:
    """``competitor_makes``: DfT make -> brand entity id (for model-level series and links)."""
    url, updated = locate(client)
    resp = client.get(url, expected_bytes=12_000_000)
    sid = snapshot(store, MAKES, resp.body, {"url": url, "body_type": body_type}, raw_dir, publisher_updated=updated)
    parsed = parse(resp.body, body_type, set(competitor_makes))
    store.upsert_geographies([("GB", "United Kingdom", "country", "ISO3166-1", "GB")])
    lag = 120
    series, obs = [], []
    for (make, fuel), byq in parsed["makes"].items():
        s = f"{MAKES}:BodyType={body_type}:Make={make}:Fuel={fuel}"
        series.append(SeriesRecord(s, MAKES, f"{MAKES}:BodyType={body_type}", {"BodyType": body_type, "Make": make, "Fuel": fuel},
                                   {"make": make, "fuel": fuel}))
        for q, v in byq.items():
            p, a, b = parse_period(q, "Q")
            obs.append(Observation(s, "GB", p, a, b, v))
    store.upsert_series(series)
    counts = store.load_observations(sid, obs, lag)
    sid2 = snapshot(store, MODELS, resp.body, {"url": url, "body_type": body_type, "level": "models"},
                    raw_dir, publisher_updated=updated)
    mseries, mobs = [], []
    for (make, gen), byq in parsed["models"].items():
        s = f"{MODELS}:BodyType={body_type}:Make={make}:GenModel={gen}"
        mseries.append(SeriesRecord(s, MODELS, f"{MODELS}:BodyType={body_type}", {"BodyType": body_type, "Make": make, "GenModel": gen},
                                    {"make": make, "generic_model": gen}))
        for q, v in byq.items():
            p, a, b = parse_period(q, "Q")
            mobs.append(Observation(s, "GB", p, a, b, v))
    store.upsert_series(mseries)
    store.load_observations(sid2, mobs, lag)
    # entity links: brand -> make-level series; product lines -> brand
    for make, brand in competitor_makes.items():
        for (mk, fuel) in parsed["makes"]:
            if mk == make:
                store.conn.execute("INSERT OR IGNORE INTO entity_observations VALUES (?,?,?)",
                                   (f"{MAKES}:BodyType={body_type}:Make={make}:Fuel={fuel}", brand, "subject"))
        for (mk, gen) in parsed["models"]:
            if mk != make:
                continue
            prod = f"product:dft:{make}:{gen}"
            upsert_entity(store, prod, "product_line", gen, "observed", PRODUCED_BY, {"make": make, "source_field": "GenModel"})
            add_identifier(store, prod, "dft_genmodel", f"{make}|{gen}", PRODUCED_BY)
            add_relationship(store, prod, "product_of", brand, "observed", PRODUCED_BY, updated or "1970-01-01", sid2)
            store.conn.execute("INSERT OR IGNORE INTO entity_observations VALUES (?,?,?)",
                               (f"{MODELS}:BodyType={body_type}:Make={make}:GenModel={gen}", prod, "subject"))
    store.conn.commit()
    return {"url": url, "publisher_updated": updated, "bytes": len(resp.body), "quarters": len(parsed["quarters"]),
            "first_quarter": min(parsed["quarters"]), "last_quarter": max(parsed["quarters"]),
            "make_fuel_series": len(series), "model_series": len(mseries), **counts}

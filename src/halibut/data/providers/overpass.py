"""OpenStreetMap footprint via Overpass: mapped locations per brand (evidence class: presence).

One query per run fetches all matching features in an ISO country area
(``out center tags``). Each feature becomes a ``location`` entity linked to
the brand (``located_at``); counts become derived observations. Data is
ODbL (share-alike) and reflects what is mapped at the OSM timestamp, not
the complete real-world network.
"""

from __future__ import annotations

import json
import re
import urllib.parse

from ..store import Observation, SeriesRecord
from .base import add_identifier, add_relationship, snapshot, upsert_entity

ENDPOINTS = ("https://overpass-api.de/api/interpreter",)
DATASET = "osm_footprint"
PRODUCED_BY = "provider:osm"


def build_query(country_iso2: str, tag_filter: dict, brand_names: list[str], timeout: int = 120) -> str:
    tags = "".join(f'["{k}"="{v}"]' for k, v in tag_filter.items())
    names = "|".join(re.escape(n) for n in brand_names)
    return (f'[out:json][timeout:{timeout}];area["ISO3166-1"="{country_iso2}"][admin_level=2]->.a;'
            f'nwr{tags}["brand"~"^({names})$",i](area.a);out center tags;')


def fetch_footprint(store, client, raw_dir, country_iso2: str, tag_filter: dict, brands: dict[str, list[str]],
                    period: str) -> dict:
    """``brands``: brand entity id -> OSM brand names. Returns counts per brand."""
    all_names = sorted({n for names in brands.values() for n in names})
    query = build_query(country_iso2, tag_filter, all_names)
    resp = client.post(ENDPOINTS[0], data=("data=" + urllib.parse.quote(query)).encode(),
                       headers={"Content-Type": "application/x-www-form-urlencoded"})
    data = json.loads(resp.body)
    osm_ts = data.get("osm3s", {}).get("timestamp_osm_base")
    sid = snapshot(store, DATASET, resp.body, {"query": query}, raw_dir, publisher_updated=osm_ts)
    name_to_brand = {n.lower(): b for b, names in brands.items() for n in names}
    counts = {b: 0 for b in brands}
    for el in data.get("elements", []):
        tags = el.get("tags", {})
        b = name_to_brand.get(tags.get("brand", "").lower())
        if b is None:
            continue
        counts[b] += 1
        loc = f"osm:{el['type']}/{el['id']}"
        lat = el.get("lat", el.get("center", {}).get("lat"))
        lon = el.get("lon", el.get("center", {}).get("lon"))
        upsert_entity(store, loc, "location", tags.get("name") or tags.get("brand", loc), "observed", PRODUCED_BY,
                      {"lat": lat, "lon": lon, "tags": {k: v for k, v in tags.items()
                                                         if k in ("brand", "brand:wikidata", "shop", "name", "operator", "addr:city")}})
        add_identifier(store, loc, "osm", f"{el['type']}/{el['id']}", PRODUCED_BY)
        add_relationship(store, b, "located_at", loc, "observed", PRODUCED_BY, osm_ts or "1970-01-01", sid,
                         {"matched_brand_tag": tags.get("brand")})
    tag_key = ",".join(f"{k}={v}" for k, v in sorted(tag_filter.items()))
    store.upsert_geographies([(country_iso2, country_iso2, "country", "ISO3166-1", country_iso2)])
    obs = []
    for b, n in counts.items():
        series_id = f"osm_footprint:{tag_key}:{b}"
        store.upsert_series([SeriesRecord(series_id, DATASET, f"osm_footprint:{tag_key}", {"tags": tag_key, "brand": b},
                                          {"brand": b, "tags": tag_key})])
        store.conn.execute("INSERT OR IGNORE INTO entity_observations VALUES (?,?,?)", (series_id, b, "subject"))
        obs.append(Observation(series_id, country_iso2, period, f"{period}-01-01", f"{period}-12-31", float(n)))
    store.load_observations(sid, obs, 0)
    return {"counts": counts, "osm_timestamp": osm_ts, "elements": len(data.get("elements", [])), "snapshot_id": sid}
